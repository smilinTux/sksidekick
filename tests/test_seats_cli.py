import hashlib
import json
import multiprocessing
import re

import pytest

from sksidekick.seats.cli import main
from sksidekick.seats.record import Invite

URL = "https://gw.example.com/v1"


@pytest.fixture(autouse=True)
def _no_host_estate(monkeypatch):
    monkeypatch.delenv("SKWORLD_ESTATE", raising=False)


def base(tmp_path, estate="a"):
    return ["--seats-dir", str(tmp_path / "seats"), "--estate", estate]


def add(tmp_path, owner="Test Person", estate="a", extra=()):
    return main(base(tmp_path, estate) + ["add", "--owner", owner, "--place", "container",
                                          "--gateway-url", URL, "--registry", str(tmp_path / "clients.json"), *extra])


def invite_of(tmp_path):
    (f,) = (tmp_path / "seats" / "invites").glob("*.json")
    return f, Invite.from_json(f.read_text())


def test_add_writes_hash_not_token(tmp_path):
    assert add(tmp_path) == 0
    f, inv = invite_of(tmp_path)
    doc = json.loads((tmp_path / "clients.json").read_text())
    (c,) = doc["credentials"]
    assert c["token_sha256"] == hashlib.sha256(inv.token.encode()).hexdigest()
    assert c["agent_id"] == "sidekick" and c["revoked"] is False
    assert re.match(r"^sk-[0-9a-f]{12}$", c["client_id"])
    from datetime import datetime, timedelta, timezone
    exp = datetime.strptime(c["expires_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    assert abs(exp - (datetime.now(timezone.utc) + timedelta(days=365))) < timedelta(minutes=5)
    for p in tmp_path.rglob("*"):
        if p.is_file() and p != f:
            assert inv.token not in p.read_text(), p


def test_seats_record_maps_client_to_person(tmp_path):
    add(tmp_path, extra=["--contact", "person@example.com"])
    f, inv = invite_of(tmp_path)
    rec = json.loads((tmp_path / "seats" / "seats.json").read_text())
    (s,) = rec["seats"]
    assert s["client_id"] == inv.client_id and s["owner"] == "Test Person"
    assert s["contact"] == "person@example.com" and s["place"] == "container"
    assert s["estate"] == "a" and s["state"] == "invited"
    assert s["credential_revision"] == inv.credential_revision and s["created_at"]
    assert ((tmp_path / "seats" / "seats.json").stat().st_mode & 0o777) == 0o600
    assert ((tmp_path / "seats").stat().st_mode & 0o777) == 0o700


def test_add_prints_no_token(tmp_path, capsys):
    add(tmp_path)
    out = capsys.readouterr()
    f, inv = invite_of(tmp_path)
    assert str(f) in out.out and inv.client_id in out.out
    assert inv.token not in out.out + out.err
    assert "SIGHUP" in out.out


def test_invite_file_mode_and_http_rules(tmp_path, capsys):
    add(tmp_path)
    f, _ = invite_of(tmp_path)
    assert (f.stat().st_mode & 0o777) == 0o600
    for url, code in (("http://127.0.0.1:18790/v1", 0), ("http://10.0.0.5/v1", 0), ("http://example.com/v1", 2)):
        rc = main(base(tmp_path) + ["add", "--owner", "X", "--place", "linux", "--gateway-url", url,
                                    "--registry", str(tmp_path / "clients.json")])
        assert rc == code
    assert "https" in capsys.readouterr().err


def test_estate_mismatch_refused(tmp_path, capsys, monkeypatch):
    assert add(tmp_path, estate="a") == 0
    assert add(tmp_path, estate="b") == 2
    assert "this seats directory belongs to estate a" in capsys.readouterr().err
    monkeypatch.setenv("SKWORLD_ESTATE", "b")
    assert add(tmp_path, estate="a") == 2
    assert "this seats directory belongs to estate a" in capsys.readouterr().err
    assert len(json.loads((tmp_path / "clients.json").read_text())["credentials"]) == 1


def _worker(argv):
    main(argv)


def test_concurrent_adds_keep_both(tmp_path):
    main(base(tmp_path) + ["init"])
    argv = lambda n: base(tmp_path) + ["add", "--owner", n, "--place", "linux", "--gateway-url", URL,
                                       "--registry", str(tmp_path / "clients.json")]
    ps = [multiprocessing.Process(target=_worker, args=(argv(n),)) for n in ("One", "Two", "Three")]
    [p.start() for p in ps]
    [p.join() for p in ps]
    assert len(json.loads((tmp_path / "clients.json").read_text())["credentials"]) == 3
    assert len(json.loads((tmp_path / "seats" / "seats.json").read_text())["seats"]) == 3


def test_rotate_revokes_old_revision(tmp_path):
    add(tmp_path)
    _, old = invite_of(tmp_path)
    reg = ["--registry", str(tmp_path / "clients.json")]
    assert main(base(tmp_path) + ["rotate", old.client_id, "--gateway-url", URL, *reg]) == 0
    creds = json.loads((tmp_path / "clients.json").read_text())["credentials"]
    by_rev = {c["credential_revision"]: c for c in creds}
    assert by_rev[old.credential_revision]["revoked"] is True
    (new,) = [c for c in creds if not c["revoked"]]
    assert new["credential_revision"] != old.credential_revision
    invites = [Invite.from_json(f.read_text()) for f in (tmp_path / "seats" / "invites").glob("*.json")]
    assert len(invites) == 2 and any(i.credential_revision == new["credential_revision"] for i in invites)
    rec = json.loads((tmp_path / "seats" / "seats.json").read_text())["seats"][0]
    assert rec["credential_revision"] == new["credential_revision"]


def test_remove_revokes_and_prints_user_steps(tmp_path, capsys):
    add(tmp_path)
    _, inv = invite_of(tmp_path)
    capsys.readouterr()
    assert main(base(tmp_path) + ["remove", inv.client_id, "--registry", str(tmp_path / "clients.json")]) == 0
    out = capsys.readouterr().out
    assert "sksidekick export" in out and "sksidekick delete" in out
    assert all(c["revoked"] for c in json.loads((tmp_path / "clients.json").read_text())["credentials"])
    assert json.loads((tmp_path / "seats" / "seats.json").read_text())["seats"][0]["state"] == "removed"


def test_unknown_seat_exits_2(tmp_path, capsys):
    add(tmp_path)
    capsys.readouterr()
    assert main(base(tmp_path) + ["remove", "sk-000000000000", "--registry", str(tmp_path / "clients.json")]) == 2
    assert "no seat" in capsys.readouterr().err


def test_list_and_usage_map_owner(tmp_path, capsys):
    add(tmp_path)
    _, inv = invite_of(tmp_path)
    capsys.readouterr()
    main(base(tmp_path) + ["list"])
    out = capsys.readouterr().out
    assert inv.client_id in out and "Test Person" in out and inv.token not in out
    usage = {"today": {"clients": []}, "spend": {"rows": [
        {"day": "2030-01-01", "client": inv.client_id, "provider": "p", "calls": 3,
         "input_tokens": 10, "output_tokens": 5, "dollars": 0.25},
        {"day": "2030-01-02", "client": inv.client_id, "provider": "p", "calls": 1,
         "input_tokens": 1, "output_tokens": 1, "dollars": 0.05},
        {"day": "2030-01-01", "client": "someone-else", "provider": "p", "calls": 2,
         "input_tokens": 1, "output_tokens": 1, "dollars": 0.01}]}}
    u = tmp_path / "usage.json"
    u.write_text(json.dumps(usage))
    assert main(base(tmp_path) + ["usage", "--json", str(u)]) == 0
    cap = capsys.readouterr()
    out = cap.out
    assert "Attribution is only trustworthy on a sidekick gateway that meters by the verified client id (client auth on)." in cap.err
    assert f"{inv.client_id}  Test Person  calls=4 tokens=17 dollars=0.3000" in out
    assert "someone-else  (not a seat)" in out


def test_reload_unit_runs_systemctl(tmp_path, monkeypatch):
    calls = []
    import subprocess
    monkeypatch.setattr(subprocess, "run", lambda cmd, **k: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", ""))
    add(tmp_path, extra=["--reload-unit", "gw.service"])
    assert calls == [["systemctl", "--user", "kill", "-s", "HUP", "gw.service"]]
