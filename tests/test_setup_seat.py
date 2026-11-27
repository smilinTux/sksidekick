from __future__ import annotations

import json
import os
import stat
import subprocess
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from sksidekick import archive
from sksidekick.home import Home
from sksidekick.seats.record import Invite
from sksidekick.setup import hermes_config, seat, tier
from sksidekick.setup.state import FieldError
from test_setup_server import TOKEN, call

SECRET = "tok-secret-0123456789abcdefghijklmnopqrstuvwxyz"


def _expiry(days: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_invite(url="http://127.0.0.1:1/v1", days=30, token=SECRET) -> Invite:
    return Invite(url, "sidekick", "sk-0123456789ab", "r1", token, _expiry(days))


class Stub:
    def __init__(self, status=200, body=b"{}"):
        self.seen: list[dict] = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                outer.seen.append({"path": self.path, **{k.lower(): v for k, v in self.headers.items()}})
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.srv.server_address[1]}/v1"

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


@pytest.fixture
def stub():
    made = []

    def make(**kw):
        s = Stub(**kw)
        made.append(s)
        return s

    yield make
    for s in made:
        s.close()


def post(port, body):
    status, _, data = call(port, "POST", "/api/tier", body, cookie=TOKEN, header=TOKEN)
    return status, json.loads(data)


def test_install_splits_token_from_record(bare_home, fake_hermes):
    inv = make_invite()
    seat.install(bare_home, inv)
    p = bare_home.root / "seat.json"
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    rec = json.loads(p.read_text())
    assert rec == {"gateway_url": inv.gateway_url, "agent_id": "sidekick", "client_id": "sk-0123456789ab",
                   "credential_revision": "r1", "expires_at": inv.expires_at}
    assert SECRET not in p.read_text()
    env = bare_home.hermes_dir / ".env"
    assert f"SKSIDEKICK_GATEWAY_TOKEN={SECRET}" in env.read_text().splitlines()
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    assert seat.load(bare_home) == inv


def test_load_none_without_seat_or_token(bare_home):
    assert seat.load(bare_home) is None
    seat.install(bare_home, make_invite())
    hermes_config.set_env(bare_home, "SKSIDEKICK_GATEWAY_TOKEN", "x")
    hermes_config._rewrite_env(bare_home, "SKSIDEKICK_GATEWAY_TOKEN", None)
    assert seat.load(bare_home) is None


def _cli(tmp_path, invite_path, *extra):
    from sksidekick import cli

    return cli.main(["--home", str(tmp_path / "h"), "setup", "--invite", str(invite_path), *extra])


@pytest.fixture
def no_server(monkeypatch):
    from sksidekick.setup import server

    ran = []
    monkeypatch.setattr(server, "_run", lambda *a, **k: ran.append(1) or 0)
    return ran


def _invite_file(tmp_path, **kw):
    f = tmp_path / "invite.json"
    f.write_text(make_invite(**kw).to_json())
    return f


def test_invite_at_start_writes_nothing_to_the_home(tmp_path, fake_hermes, no_server):
    f = _invite_file(tmp_path)
    assert _cli(tmp_path, f) == 0 and no_server
    home = Home(tmp_path / "h")
    assert not (home.root / "seat.json").exists() and not home.hermes_dir.exists()
    assert f.exists() and seat.current(home) == make_invite_like(home)


def make_invite_like(home):
    return seat.pending(home)[0]


def test_init_and_import_still_accept_an_invited_home(tmp_path, fake_hermes, no_server):
    from sksidekick import cli

    f = _invite_file(tmp_path)
    assert _cli(tmp_path, f) == 0
    home = Home(tmp_path / "h")
    assert home.is_empty()
    assert cli.main(["--home", str(home.root), "init"]) == 0


def test_invite_file_removed_only_when_shared_is_saved(running, tmp_path, fake_hermes, stub):
    home, port = running
    gw = stub()
    f = tmp_path / "invite.json"
    f.write_text(make_invite(url=gw.url).to_json())
    seat.stage(home, Invite.from_json(f.read_text()), str(f), False)
    # another tier first: the invite is untouched
    other = stub()
    status, _ = post(port, {"tier": "private", "base_url": other.url, "model": "m"})
    assert status == 200 and f.exists() and not (home.root / "seat.json").exists()


def test_shared_save_installs_and_removes_the_invite_file(running, tmp_path, fake_hermes, stub):
    home, port = running
    gw = stub()
    f = tmp_path / "invite.json"
    f.write_text(make_invite(url=gw.url).to_json())
    seat.stage(home, Invite.from_json(f.read_text()), str(f), False)
    assert post(port, {"tier": "shared"})[0] == 200
    assert not f.exists() and seat.load(home) is not None and seat.pending(home) is None


def test_keep_invite_keeps_it(running, tmp_path, fake_hermes, stub):
    home, port = running
    gw = stub()
    f = tmp_path / "invite.json"
    f.write_text(make_invite(url=gw.url).to_json())
    seat.stage(home, Invite.from_json(f.read_text()), str(f), True)
    assert post(port, {"tier": "shared"})[0] == 200
    assert f.exists() and seat.load(home) is not None


def test_failed_shared_save_keeps_invite_and_home_clean(running, tmp_path, fake_hermes, stub):
    home, port = running
    gw = stub(status=401)
    f = tmp_path / "invite.json"
    f.write_text(make_invite(url=gw.url).to_json())
    seat.stage(home, Invite.from_json(f.read_text()), str(f), False)
    assert post(port, {"tier": "shared"})[0] == 422
    assert f.exists() and not (home.root / "seat.json").exists()


def test_bad_invite_file_is_a_clear_line_and_setup_still_starts(tmp_path, fake_hermes, no_server, capsys):
    f = tmp_path / "invite.json"
    f.write_text("nonsense " + SECRET)
    assert _cli(tmp_path, f) == 0 and no_server
    err = capsys.readouterr()
    assert "invite" in err.err and SECRET not in err.err + err.out
    assert f.exists() and seat.pending(Home(tmp_path / "h")) is None


def test_missing_invite_file_is_a_clear_line(tmp_path, fake_hermes, no_server, capsys):
    assert _cli(tmp_path, tmp_path / "nope.json") == 0 and no_server
    assert "invite" in capsys.readouterr().err


def test_expired_invite_at_start_is_kept_and_shown_disabled(tmp_path, fake_hermes, no_server, capsys):
    f = _invite_file(tmp_path, days=-1)
    assert _cli(tmp_path, f) == 0
    err = capsys.readouterr().err
    assert "turned off or has expired" in err and SECRET not in err and f.exists()
    home = Home(tmp_path / "h")
    st = tier.tiers_state(home)["shared"]
    assert st["enabled"] is False and st["disabled_label"] == tier.REVOKED
    assert not (home.root / "seat.json").exists()


def test_invite_on_a_live_sidekick_is_not_used_or_deleted(tmp_path, fake_hermes, no_server, capsys):
    home = Home(tmp_path / "h")
    home.new_instance()
    f = _invite_file(tmp_path)
    assert _cli(tmp_path, f) == 1
    assert "already set up; the invite was not used" in capsys.readouterr().err
    assert f.exists() and not (home.root / "seat.json").exists() and not no_server


def test_shared_enabled_only_with_seat(running, fake_hermes):
    home, port = running
    shared = json.loads(call(port, "GET", "/api/state", cookie=TOKEN)[2])["tiers"]["shared"]
    assert shared["enabled"] is False and shared["disabled_label"] == "Available when we invite you (a seat)"
    seat.install(home, make_invite(url="https://gw.example.com/v1"))
    _, _, raw = call(port, "GET", "/api/state", cookie=TOKEN)
    shared = json.loads(raw)["tiers"]["shared"]
    assert shared["enabled"] is True and shared["host"] == "gw.example.com"
    assert SECRET.encode() not in raw


def test_env_pair_no_longer_enables_shared(running, fake_hermes, monkeypatch):
    home, port = running
    monkeypatch.setenv("SKSIDEKICK_GATEWAY_URL", "https://gw.example.com/v1")
    monkeypatch.setenv("SKSIDEKICK_GATEWAY_KEY", "gw-secret")
    shared = json.loads(call(port, "GET", "/api/state", cookie=TOKEN)[2])["tiers"]["shared"]
    assert shared["enabled"] is False
    status, body = post(port, {"tier": "shared"})
    assert status == 422 and body["field"] == "tier"
    assert fake_hermes.calls() == []


def test_shared_sends_client_headers(running, fake_hermes, stub):
    home, port = running
    gw = stub()
    seat.install(home, make_invite(url=gw.url))
    status, _ = post(port, {"tier": "shared"})
    assert status == 200
    (req,) = gw.seen
    assert req["path"] == "/v1/models"
    assert req["authorization"] == f"Bearer {SECRET}"
    assert (req["x-agent-id"], req["x-sk-client-id"], req["x-sk-credential-revision"]) == ("sidekick", "sk-0123456789ab", "r1")
    assert fake_hermes.get("model.provider") == "sidekick-gateway"
    assert fake_hermes.get("providers.sidekick-gateway.api") == gw.url
    assert fake_hermes.get("providers.sidekick-gateway.key_env") == "SKSIDEKICK_GATEWAY_TOKEN"
    assert fake_hermes.get("providers.sidekick-gateway.default_model") == "sk-default"
    assert json.loads(fake_hermes.get("providers.sidekick-gateway.extra_headers")) == {
        "x-agent-id": "sidekick", "x-sk-client-id": "sk-0123456789ab", "x-sk-credential-revision": "r1"}
    assert SECRET not in json.dumps(fake_hermes.calls())
    assert f"SKSIDEKICK_GATEWAY_TOKEN={SECRET}" in (home.hermes_dir / ".env").read_text().splitlines()


@pytest.mark.parametrize("code", [401, 403])
def test_revoked_invite_message(running, fake_hermes, stub, code):
    home, port = running
    gw = stub(status=code, body=b'{"error": {"code": "client_auth_denied"}}')
    seat.install(home, make_invite(url=gw.url))
    status, body = post(port, {"tier": "shared"})
    assert status == 422
    assert body == {"field": "tier", "error": "this invite was turned off or has expired; ask for a new one"}
    assert fake_hermes.calls() == []


def test_expired_invite_refused_before_network(running, fake_hermes, stub):
    home, port = running
    gw = stub()
    seat.install(home, make_invite(url=gw.url, days=-1))
    status, body = post(port, {"tier": "shared"})
    assert status == 422
    assert body == {"field": "tier", "error": "this invite was turned off or has expired; ask for a new one"}
    assert gw.seen == [] and fake_hermes.calls() == []


def test_unreachable_gateway_says_nothing_answered(running, fake_hermes):
    home, port = running
    seat.install(home, make_invite(url="http://127.0.0.1:9/v1"))
    status, body = post(port, {"tier": "shared"})
    assert status == 422 and body["field"] == "tier" and "nothing answered" in body["error"]


def test_start_over_removes_the_seat_with_its_token(bare_home, fake_hermes, capsys):
    from sksidekick.setup import start_over, state

    seat.install(bare_home, make_invite())
    state.advance(bare_home, "tier")
    assert start_over.start_over(bare_home, yes=True) == 0
    assert not (bare_home.root / "seat.json").exists() and not bare_home.hermes_dir.exists()
    assert seat.load(bare_home) is None


def test_start_over_lists_the_seat(bare_home, fake_hermes):
    from sksidekick.setup import start_over

    seat.install(bare_home, make_invite())
    assert any("new invite" in w for w in start_over._what(bare_home))


def test_switching_tiers_clears_the_other_side(bare_home, fake_hermes):
    inv = make_invite()
    seat.install(bare_home, inv)
    hermes_config.set_model(bare_home, base_url="https://api.example.com/v1", model="m", api_key="sk-x")
    hermes_config.set_gateway(bare_home, inv, "sk-default")
    env = (bare_home.hermes_dir / ".env").read_text()
    assert "CUSTOM_API_KEY" not in env and f"SKSIDEKICK_GATEWAY_TOKEN={SECRET}" in env
    hermes_config.set_model(bare_home, base_url="https://api.example.com/v1", model="m", api_key=None)
    env = (bare_home.hermes_dir / ".env").read_text()
    assert "SKSIDEKICK_GATEWAY_TOKEN" not in env and SECRET not in env
    assert fake_hermes.get("model.provider") == "custom"


def test_set_gateway_failure_restores_everything(bare_home, fake_hermes, monkeypatch):
    inv = make_invite()
    seat.install(bare_home, inv)
    hermes_config.set_model(bare_home, base_url="https://api.example.com/v1", model="m", api_key="sk-x")
    before = {p: p.read_bytes() for p in (bare_home.hermes_dir / "config.yaml", bare_home.hermes_dir / ".env")}
    n = len(fake_hermes.calls())
    # the first config set of set_gateway is call n+1; fail the 3rd set overall of this run
    monkeypatch.setenv("FAKE_HERMES_FAIL_SET", str(int((fake_hermes.store.with_name("hermes-config.json.n")).read_text()) + 3))
    with pytest.raises(hermes_config.HermesError) as e:
        hermes_config.set_gateway(bare_home, inv, "sk-default")
    assert SECRET not in str(e.value)
    assert {p: p.read_bytes() for p in before} == before


def test_export_keeps_seat_drops_token(bare_home, fake_hermes, tmp_path):
    seat.install(bare_home, make_invite())
    hermes_config.set_gateway(bare_home, make_invite(), "sk-default")
    assert Path("seat.json") in bare_home.story_paths()
    plain = tmp_path / "plain"
    archive.export_plain(bare_home, plain)
    assert (plain / "seat.json").exists()
    blob = b"".join(p.read_bytes() for p in plain.rglob("*") if p.is_file())
    assert SECRET.encode() not in blob
    assert b"sidekick-gateway" in blob and b"x-sk-client-id" in blob
    full = tmp_path / "full"
    archive.export_plain(bare_home, full, with_credentials=True)
    assert SECRET in (full / "hermes" / ".env").read_text()


# ---- rotation: a live sidekick with a seat takes a new invite for the same client id ----

def _live_with_seat(tmp_path, fake_hermes, **kw):
    home = Home(tmp_path / "h")
    home.new_instance()
    inv = make_invite(**kw)
    hermes_config.set_gateway(home, inv, "sk-fancy")
    seat.write_record(home, inv)
    return home, inv


def _rotated_file(tmp_path, **kw):
    inv = make_invite(token="tok-NEW-0123456789abcdefghijklmnopqrstuvwxyz", **kw)
    inv = Invite(inv.gateway_url, inv.agent_id, kw.pop("client_id", None) or inv.client_id, "r2", inv.token,
                 inv.expires_at)
    f = tmp_path / "rot.json"
    f.write_text(inv.to_json())
    return f, inv


def test_rotation_applies_new_credential(tmp_path, fake_hermes, no_server, capsys):
    home, old = _live_with_seat(tmp_path, fake_hermes)
    f, new = _rotated_file(tmp_path)
    assert _cli(tmp_path, f) == 0 and not no_server
    out = capsys.readouterr()
    assert new.token not in out.out + out.err and "applied" in out.out and "restart" in out.out
    got = seat.load(home)
    assert got.token == new.token and got.credential_revision == "r2"
    hdrs = json.loads(fake_hermes.get("providers.sidekick-gateway.extra_headers"))
    assert hdrs["x-sk-credential-revision"] == "r2"
    assert fake_hermes.get("model.default") == "sk-fancy"
    assert not f.exists()
    assert old.token not in (home.hermes_dir / ".env").read_text()


@pytest.mark.parametrize("platform, container, want", [
    ("linux", None, "systemctl --user restart sksidekick"),
    ("darwin", None, "launchctl kickstart -k gui/"),
    ("linux", "1", "docker restart <container>"),
])
def test_rotation_prints_the_real_restart_command(tmp_path, fake_hermes, no_server, capsys, monkeypatch,
                                                   platform, container, want):
    from sksidekick.setup import server
    monkeypatch.setattr(server.sys, "platform", platform)
    if container:
        monkeypatch.setenv("SKSIDEKICK_IN_CONTAINER", container)
    else:
        monkeypatch.delenv("SKSIDEKICK_IN_CONTAINER", raising=False)
    _live_with_seat(tmp_path, fake_hermes)
    f, _ = _rotated_file(tmp_path)
    assert _cli(tmp_path, f) == 0
    out = capsys.readouterr().out
    assert want in out and "if it is running" not in out
    if platform == "darwin":
        assert f"gui/{os.getuid()}/io.skworld.sksidekick" in out


def test_rotation_keep_invite(tmp_path, fake_hermes, no_server):
    _live_with_seat(tmp_path, fake_hermes)
    f, _ = _rotated_file(tmp_path)
    assert _cli(tmp_path, f, "--keep-invite") == 0 and f.exists()


def test_rotation_other_client_refused(tmp_path, fake_hermes, no_server, capsys):
    home, old = _live_with_seat(tmp_path, fake_hermes)
    f = tmp_path / "other.json"
    f.write_text(Invite(old.gateway_url, "sidekick", "sk-ffffffffffff", "r2", "tok-other-0123456789abcdef", _expiry(30)).to_json())
    assert _cli(tmp_path, f) == 1
    assert "invite was not used" in capsys.readouterr().err
    assert f.exists() and seat.load(home) == old


def test_rotation_expired_refused(tmp_path, fake_hermes, no_server, capsys):
    home, old = _live_with_seat(tmp_path, fake_hermes)
    f, _ = _rotated_file(tmp_path, days=-1)
    assert _cli(tmp_path, f) == 1
    assert "turned off or has expired" in capsys.readouterr().err
    assert f.exists() and seat.load(home) == old


def _files(home):
    return {n: (p.read_bytes() if p.exists() else None) for n, p in
            (("cfg", home.hermes_dir / "config.yaml"), ("env", home.hermes_dir / ".env"), ("seat", home.root / "seat.json"))}


def _assert_untouched(home, before, f, capsys, new_token):
    out = capsys.readouterr()
    assert _files(home) == before and f.exists()
    assert new_token not in out.out + out.err and "keeps its old credential" in out.err


def test_rotation_set_gateway_failure_changes_nothing(tmp_path, fake_hermes, no_server, capsys, monkeypatch):
    home, _ = _live_with_seat(tmp_path, fake_hermes)
    f, new = _rotated_file(tmp_path)
    before = _files(home)
    capsys.readouterr()
    n = int(open(str(fake_hermes.store) + ".n").read())
    monkeypatch.setenv("FAKE_HERMES_FAIL_SET", str(n + 3))
    assert _cli(tmp_path, f) == 1
    _assert_untouched(home, before, f, capsys, new.token)


def test_rotation_record_failure_after_set_gateway_changes_nothing(tmp_path, fake_hermes, no_server, capsys, monkeypatch):
    home, _ = _live_with_seat(tmp_path, fake_hermes)
    f, new = _rotated_file(tmp_path)
    before = _files(home)
    capsys.readouterr()

    def boom(*a, **k):
        raise OSError("disk")

    monkeypatch.setattr(seat, "write_record", boom)
    assert _cli(tmp_path, f) == 1
    _assert_untouched(home, before, f, capsys, new.token)


def test_rotation_refused_when_not_on_the_gateway(tmp_path, fake_hermes, no_server, capsys):
    home, _ = _live_with_seat(tmp_path, fake_hermes)
    hermes_config.config_set(home, "model.provider", "custom")
    f, new = _rotated_file(tmp_path)
    before = _files(home)
    capsys.readouterr()
    assert _cli(tmp_path, f) == 1
    out = capsys.readouterr()
    assert "does not use the shared gateway now; the invite was not used" in out.err
    assert _files(home) == before and f.exists() and new.token not in out.out + out.err
