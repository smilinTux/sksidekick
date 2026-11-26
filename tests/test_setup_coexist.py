"""Setup and `sksidekick init` / `import` side by side: setup steps aside, and never shreds keys it did not make."""

from __future__ import annotations

import pytest

from sksidekick import archive, keys
from sksidekick.cli import main
from sksidekick.home import Home
from sksidekick.setup import phrase, server, state
from test_setup_phrase import Setup, at_phrase, confirm_body, key_files, post
from test_setup_server import TOKEN, running  # noqa: F401 (fixture)


@pytest.fixture(autouse=True)
def _fast_watch(monkeypatch):
    monkeypatch.setattr(server, "WATCH_INTERVAL", 0.05)


def _init(home: Home) -> int:
    return main(["--home", str(home.root), "init"])


def test_init_during_setup_ends_setup_and_keeps_keys(bare_home, monkeypatch, capsys):
    s = Setup(bare_home, monkeypatch, capsys)
    assert _init(bare_home) == 0
    p = capsys.readouterr().out
    before = (bare_home.keys_dir / keys.WRAPPED).read_bytes()
    s.t.join(5)
    assert not s.t.is_alive() and s.rc["rc"] == 0
    assert (bare_home.keys_dir / keys.WRAPPED).read_bytes() == before
    assert bare_home.instance()["status"] == "live"
    assert p  # init printed its phrase to its own terminal; setup did not take over


def test_import_during_setup_ends_setup_and_keeps_keys(bare_home, tmp_path, monkeypatch, capsys):
    src = Home(tmp_path / "source")
    assert _init(src) == 0
    import re

    p = re.search(r"([0-9A-HJKMNP-TV-Z]{5}-){7}[0-9A-HJKMNP-TV-Z]{5}", capsys.readouterr().out).group(0)
    arc = tmp_path / "a.sksk"
    archive.export(src, arc)
    s = Setup(bare_home, monkeypatch, capsys)
    archive.import_archive(arc, bare_home, phrase=p)
    s.t.join(5)
    assert not s.t.is_alive() and s.rc["rc"] == 0
    assert key_files(bare_home) == key_files(src)
    assert keys.unwrap_identity((bare_home.keys_dir / keys.WRAPPED).read_bytes(), p)


def test_init_and_import_refused_while_a_setup_phrase_is_pending(bare_home, tmp_path, monkeypatch, capsys):
    at_phrase(bare_home)
    s = Setup(bare_home, monkeypatch, capsys)
    out = post(s.port, "/api/phrase/new", tok=s.tok)[1]
    made = (bare_home.keys_dir / keys.WRAPPED).read_bytes()
    assert _init(bare_home) == 1
    with pytest.raises(FileExistsError):
        archive.import_archive(tmp_path / "missing.sksk", bare_home, phrase=out["phrase"])
    with pytest.raises(FileExistsError):
        keys.create_keys(bare_home)
    assert (bare_home.keys_dir / keys.WRAPPED).read_bytes() == made
    assert s.t.is_alive()
    s.stop()
    assert s.rc["rc"] == 0 and key_files(bare_home) == []  # setup's own unconfirmed keys go with it


def test_stop_never_shreds_keys_setup_did_not_make(bare_home, monkeypatch, capsys):
    at_phrase(bare_home)
    s = Setup(bare_home, monkeypatch, capsys)
    keys.create_keys(bare_home)  # someone else's key set appears while setup runs
    status, err, _, _ = post(s.port, "/api/phrase/new", tok=s.tok)
    assert status == 409 and "error" in err
    s.stop()
    assert key_files(bare_home)


def test_stop_never_shreds_keys_replaced_after_phrase_new(bare_home, monkeypatch, capsys):
    at_phrase(bare_home)
    s = Setup(bare_home, monkeypatch, capsys)
    assert post(s.port, "/api/phrase/new", tok=s.tok)[0] == 200
    keys.shred_keys(bare_home)
    keys.create_keys(bare_home)  # not the set setup made
    foreign = (bare_home.keys_dir / keys.WRAPPED).read_bytes()
    s.stop()
    assert (bare_home.keys_dir / keys.WRAPPED).read_bytes() == foreign


def test_start_keeps_keys_of_a_home_that_has_an_instance(bare_home, monkeypatch, capsys):
    at_phrase(bare_home)  # a leftover setup.json ...
    keys.create_keys(bare_home)
    bare_home.new_instance()  # ... in a home init or import made live
    rc = server.main_setup(bare_home, "127.0.0.1", 0)
    assert rc == 0 and key_files(bare_home)


def test_start_shreds_only_with_setup_progress(bare_home, monkeypatch, capsys):
    bare_home.root.mkdir(parents=True)
    keys.create_keys(bare_home)  # no setup.json: these keys are not from a setup
    phrase.on_start(bare_home)
    assert key_files(bare_home)


def test_no_post_once_an_instance_exists(running):
    home, port = running
    home.new_instance()
    status, err, _, _ = post(port, "/api/tier", tok=TOKEN)
    assert status == 409 and err == {"error": "this sidekick is already set up"}
    assert not (home.state_dir / "setup.json").exists()


def test_watch_ignores_its_own_going_live(bare_home, monkeypatch, capsys):
    """The backup step writes instance.json itself; setup still finishes normally, keys kept."""
    at_phrase(bare_home)
    s = Setup(bare_home, monkeypatch, capsys)
    out = post(s.port, "/api/phrase/new", tok=s.tok)[1]
    assert post(s.port, "/api/phrase/confirm", confirm_body(out["phrase"], out["check"]), tok=s.tok)[0] == 200
    assert post(s.port, "/api/backup", {"kind": "later"}, tok=s.tok)[0] == 200
    s.t.join(5)
    assert s.rc["rc"] == 0 and state.is_complete(bare_home) and key_files(bare_home)


def test_printed_address_is_loopback_for_a_container_bind(bare_home, monkeypatch, capsys):
    import re

    real = server.make_server

    def make(home, host, port, token):
        srv = real(home, "127.0.0.1", 0, token)
        srv.shutdown_soon = __import__("threading").Timer(0.1, srv.shutdown)
        srv.shutdown_soon.start()
        return srv

    monkeypatch.setattr(server, "make_server", make)
    assert server.main_setup(bare_home, "0.0.0.0", 8765) == 0
    assert re.search(r"Open http://127\.0\.0\.1:\d+/\?t=\S+", capsys.readouterr().out)
