from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path

import pytest

from sksidekick import backup, keys, serve
from sksidekick.home import Home
from sksidekick.setup import backup_step, phrase, server, state
from test_setup_server import TOKEN, call


def post(port, path, body=None, tok=TOKEN):
    status, headers, data = call(port, "POST", path, body, cookie=tok, header=tok)
    return status, json.loads(data), headers, data


def at_phrase(home: Home) -> None:
    for s in ("tier", "channels", "personality"):
        state.advance(home, s)


def confirm_body(p: str, check: list[int], lower=True) -> dict:
    groups = p.split("-")
    typed = {str(i): (groups[i].lower().replace("0", "o") if lower else groups[i]) for i in check}
    return {"groups": typed}


def wait_for(cond, seconds=5.0) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


def key_files(home: Home) -> list[str]:
    return sorted(p.name for p in home.keys_dir.iterdir()) if home.keys_dir.is_dir() else []


class Setup:
    """main_setup in a thread, with its server object at hand so a test can stop it."""

    def __init__(self, home: Home, monkeypatch, capsys) -> None:
        self.home, self.rc, made = home, {}, {}
        real = server.make_server

        def make(*a, **k):
            made["srv"] = real(*a, **k)
            return made["srv"]

        monkeypatch.setattr(server, "make_server", make)
        self.t = threading.Thread(target=lambda: self.rc.setdefault("rc", server.main_setup(home, "127.0.0.1", 0)))
        self.t.start()
        m = None
        for _ in range(200):
            m = re.search(r"Open http://127\.0\.0\.1:(\d+)/\?t=(\S+)", capsys.readouterr().out)
            if m:
                break
            time.sleep(0.02)
        assert m, "setup server did not start"
        self.port, self.tok, self.srv = int(m.group(1)), m.group(2), made["srv"]

    def stop(self) -> None:
        self.srv.shutdown()
        self.t.join(5)


@pytest.fixture
def at_phrase_running(running):
    home, port = running
    at_phrase(home)
    yield home, port
    phrase.on_stop(home)


def test_phrase_shown_once(at_phrase_running, capfd, tmp_path):
    home, port = at_phrase_running
    status, out, headers, raw = post(port, "/api/phrase/new")
    assert status == 200 and headers["Cache-Control"] == "no-store"
    p, check = out["phrase"], out["check"]
    assert re.fullmatch(r"([0-9A-HJKMNP-TV-Z]{5}-){7}[0-9A-HJKMNP-TV-Z]{5}", p)
    assert len(check) == 2 and len(set(check)) == 2 and all(0 <= i < 8 for i in check)
    secrets_ = [p.encode(), p.replace("-", "").encode(), *(g.encode() for g in p.split("-"))]
    _, _, st = call(port, "GET", "/api/state", cookie=TOKEN)
    assert not any(s in st for s in secrets_)
    status, _, _, raw2 = post(port, "/api/phrase/confirm", confirm_body(p, check))
    assert status == 200 and not any(s in raw2 for s in secrets_)
    _, _, st = call(port, "GET", "/api/state", cookie=TOKEN)
    assert not any(s in st for s in secrets_)
    captured = capfd.readouterr()
    assert not any(s.decode() in captured.out + captured.err for s in secrets_)
    for f in tmp_path.rglob("*"):
        if f.is_file():
            data = f.read_bytes()
            assert not any(s in data for s in secrets_), f
    assert state.load(home)["next"] == "backup"


def test_refresh_makes_a_new_phrase(at_phrase_running):
    home, port = at_phrase_running
    first = post(port, "/api/phrase/new")[1]["phrase"]
    second = post(port, "/api/phrase/new")[1]["phrase"]
    assert first != second
    wrapped = (home.keys_dir / keys.WRAPPED).read_bytes()
    assert keys.unwrap_identity(wrapped, second)
    with pytest.raises(keys.WrongPhrase):
        keys.unwrap_identity(wrapped, first)


def test_unconfirmed_keys_shredded_on_stop(bare_home, monkeypatch, capsys):
    at_phrase(bare_home)
    s = Setup(bare_home, monkeypatch, capsys)
    assert post(s.port, "/api/phrase/new", tok=s.tok)[0] == 200
    assert key_files(bare_home)
    s.stop()
    assert s.rc["rc"] == 0 and key_files(bare_home) == []
    assert str(bare_home.root) not in phrase._PENDING


def test_confirm_accepts_lookalikes(at_phrase_running):
    home, port = at_phrase_running
    out = post(port, "/api/phrase/new")[1]
    p = out["phrase"]
    body = confirm_body(p, out["check"])
    for i in out["check"]:  # also an I or L typed for a 1, and spaces around
        body["groups"][str(i)] = " " + body["groups"][str(i)].replace("1", "l") + " "
    assert post(port, "/api/phrase/confirm", body)[0] == 200
    assert state.load(home)["next"] == "backup"
    assert key_files(home)


def test_confirm_wrong_indices_refused(at_phrase_running):
    home, port = at_phrase_running
    out = post(port, "/api/phrase/new")[1]
    p, check = out["phrase"], out["check"]
    other = [i for i in range(8) if i not in check][:2]
    status, err, _, _ = post(port, "/api/phrase/confirm", confirm_body(p, other))
    assert status == 422 and err["field"] == "groups"
    assert state.load(home)["next"] == "phrase"
    assert post(port, "/api/phrase/confirm", confirm_body(p, check))[0] == 200


def test_three_misses_shreds(at_phrase_running):
    home, port = at_phrase_running
    out = post(port, "/api/phrase/new")[1]
    check = out["check"]
    wrong = {"groups": {str(i): "ZZZZZ" if out["phrase"].split("-")[i] != "ZZZZZ" else "YYYYY" for i in check}}
    for _ in range(2):
        status, err, _, _ = post(port, "/api/phrase/confirm", wrong)
        assert status == 422 and err == {"field": "groups", "error": "those groups do not match"}
        assert key_files(home)
    status, err, _, _ = post(port, "/api/phrase/confirm", wrong)
    assert status == 422
    assert err == {"field": "groups", "error": "those groups do not match", "new_phrase_needed": True}
    assert key_files(home) == []
    # even the right groups no longer count: that phrase is gone
    status, err, _, _ = post(port, "/api/phrase/confirm", confirm_body(out["phrase"], check))
    assert status == 422 and err.get("new_phrase_needed") is True
    assert post(port, "/api/phrase/new")[0] == 200 and key_files(home)


def test_timeout_shreds(at_phrase_running, monkeypatch):
    home, port = at_phrase_running
    monkeypatch.setattr(phrase, "TIMEOUT", 0.2)
    out = post(port, "/api/phrase/new")[1]
    assert key_files(home)
    assert wait_for(lambda: key_files(home) == [])
    status, err, _, _ = post(port, "/api/phrase/confirm", confirm_body(out["phrase"], out["check"]))
    assert status == 422 and err.get("new_phrase_needed") is True


def test_timer_never_touches_confirmed_keys(at_phrase_running, monkeypatch):
    home, port = at_phrase_running
    monkeypatch.setattr(phrase, "TIMEOUT", 0.3)
    out = post(port, "/api/phrase/new")[1]
    assert post(port, "/api/phrase/confirm", confirm_body(out["phrase"], out["check"]))[0] == 200
    time.sleep(0.5)
    assert key_files(home)
    phrase.on_stop(home)
    assert key_files(home)


def test_start_shreds_leftover_unconfirmed_keys(bare_home, monkeypatch, capsys):
    at_phrase(bare_home)
    keys.create_keys(bare_home)  # as if the server was killed after phrase/new
    s = Setup(bare_home, monkeypatch, capsys)
    try:
        assert key_files(bare_home) == []
    finally:
        s.stop()


def test_start_keeps_confirmed_keys(bare_home, monkeypatch, capsys):
    at_phrase(bare_home)
    keys.create_keys(bare_home)
    state.advance(bare_home, "phrase")
    s = Setup(bare_home, monkeypatch, capsys)
    s.stop()
    assert key_files(bare_home)


def test_no_new_phrase_after_confirm(at_phrase_running):
    home, port = at_phrase_running
    out = post(port, "/api/phrase/new")[1]
    assert post(port, "/api/phrase/confirm", confirm_body(out["phrase"], out["check"]))[0] == 200
    before = (home.keys_dir / keys.LIVE).read_bytes()
    status, err, _, raw = post(port, "/api/phrase/new")
    assert status == 409 and err == {"error": "that step is already done"}
    assert (home.keys_dir / keys.LIVE).read_bytes() == before


def test_plain_phrase_post_does_not_skip_the_phrase(at_phrase_running):
    home, port = at_phrase_running
    status, err, _, _ = post(port, "/api/phrase")
    assert status == 422 and state.load(home)["next"] == "phrase"


def confirmed(home: Home, port: int) -> None:
    out = post(port, "/api/phrase/new")[1]
    assert post(port, "/api/phrase/confirm", confirm_body(out["phrase"], out["check"]))[0] == 200


def test_backup_dir_must_be_writable(at_phrase_running, tmp_path):
    home, port = at_phrase_running
    confirmed(home, port)
    ro = tmp_path / "readonly"
    ro.mkdir()
    ro.chmod(0o500)
    try:
        for target in (str(ro), "relative/dir", str(tmp_path / "missing"), str(tmp_path / "x\ny")):
            status, err, _, _ = post(port, "/api/backup", {"kind": "dir", "target": target})
            assert status == 422 and err["field"] == "target", target
    finally:
        ro.chmod(0o700)
    assert list(ro.iterdir()) == []
    assert backup.load_config(home)["destinations"] == []
    _, _, st = call(port, "GET", "/api/state", cookie=TOKEN)
    assert json.loads(st)["backup_warning"] == serve.backup_failed(backup.NO_DEST, None)
    status, _, _, _ = post(port, "/api/backup", {"kind": "later"})
    assert status == 200 and backup.load_config(home)["destinations"] == []
    assert backup_step.warning(home) == serve.backup_failed(backup.NO_DEST, None)  # the server has shut down


def test_backup_dir_and_rclone_saved(at_phrase_running, tmp_path):
    home, port = at_phrase_running
    confirmed(home, port)
    for bad in ("nocolon", "-flag:x", "name:pa\nth", ":"):
        status, err, _, _ = post(port, "/api/backup", {"kind": "rclone", "target": bad})
        assert status == 422 and err["field"] == "target", bad
    assert post(port, "/api/backup", {"kind": "nope"})[0] == 422
    dest = tmp_path / "backups"
    dest.mkdir()
    assert post(port, "/api/backup", {"kind": "dir", "target": str(dest)})[0] == 200
    assert backup.load_config(home)["destinations"] == [{"kind": "dir", "path": str(dest)}]
    assert list(dest.iterdir()) == []
    assert backup_step.warning(home) is None


def test_backup_rclone_saved(at_phrase_running):
    home, port = at_phrase_running
    confirmed(home, port)
    assert post(port, "/api/backup", {"kind": "rclone", "target": "skhub-nor:backups/sidekick"})[0] == 200
    assert backup.load_config(home)["destinations"] == [{"kind": "rclone", "remote": "skhub-nor:backups/sidekick"}]


def test_backup_rolled_back_if_going_live_fails(at_phrase_running, tmp_path, monkeypatch):
    home, port = at_phrase_running
    confirmed(home, port)
    dest = tmp_path / "backups"
    dest.mkdir()

    def boom(self):
        raise OSError("disk")

    monkeypatch.setattr(Home, "new_instance", boom)
    assert post(port, "/api/backup", {"kind": "dir", "target": str(dest)})[0] == 500
    assert backup.load_config(home)["destinations"] == []
    assert not (home.state_dir / "instance.json").exists()
    assert state.load(home)["next"] == "backup"


def test_done_makes_it_live(bare_home, monkeypatch, capsys, tmp_path):
    at_phrase(bare_home)
    s = Setup(bare_home, monkeypatch, capsys)
    out = post(s.port, "/api/phrase/new", tok=s.tok)[1]
    assert post(s.port, "/api/phrase/confirm", confirm_body(out["phrase"], out["check"]), tok=s.tok)[0] == 200
    assert post(s.port, "/api/backup", {"kind": "later"}, tok=s.tok)[0] == 200
    s.t.join(5)
    assert s.rc["rc"] == 0
    assert bare_home.instance()["status"] == "live"
    assert state.is_complete(bare_home)
    assert key_files(bare_home)  # going live keeps the confirmed key
    assert keys.unwrap_identity((bare_home.keys_dir / keys.WRAPPED).read_bytes(), out["phrase"])
