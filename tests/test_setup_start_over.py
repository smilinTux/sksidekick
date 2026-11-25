"""`sksidekick setup --start-over`: an unfinished setup can be thrown away; a live sidekick never is."""

from __future__ import annotations

import json
import os

from sksidekick import keys
from sksidekick.cli import main
from sksidekick.home import Home
from sksidekick.setup import server, start_over, state
from test_setup_phrase import Setup


def _half_set_up(home: Home) -> None:
    """As a setup that stopped at the phrase step leaves a home: progress, settings, personality, keys."""
    state.advance(home, "tier")
    state.advance(home, "channels")
    state.advance(home, "personality")
    home.hermes_dir.mkdir(parents=True)
    (home.hermes_dir / ".env").write_text("CUSTOM_API_KEY=sk-x\nTELEGRAM_BOT_TOKEN=1:a\n")
    (home.hermes_dir / "config.yaml").write_text("model: {}\n")
    (home.hermes_dir / "SOUL.md").write_text("# Style\n")
    home.soul_dir.mkdir(parents=True)
    (home.soul_dir / "personality.json").write_text(json.dumps({"type": "mentor", "voice": "female"}))
    keys.create_keys(home)


def _cli(home: Home, *args: str) -> int:
    return main(["--home", str(home.root), "setup", "--start-over", *args])


def test_start_over_leaves_a_home_init_accepts(bare_home, capsys):
    _half_set_up(bare_home)
    assert main(["--home", str(bare_home.root), "init"]) == 1  # refused before
    capsys.readouterr()
    assert _cli(bare_home, "--yes") == 0
    assert "empty again" in capsys.readouterr().out
    assert bare_home.is_empty()
    assert not (bare_home.state_dir / "setup.json").exists() and not bare_home.hermes_dir.exists()
    assert main(["--home", str(bare_home.root), "init"]) == 0
    assert bare_home.instance()["status"] == "live"


def test_start_over_refuses_a_live_home(bare_home, capsys):
    assert main(["--home", str(bare_home.root), "init"]) == 0
    before = sorted(p.name for p in bare_home.keys_dir.iterdir())
    capsys.readouterr()
    assert _cli(bare_home, "--yes") == 1
    assert "is a sidekick already" in capsys.readouterr().err
    assert sorted(p.name for p in bare_home.keys_dir.iterdir()) == before


def test_start_over_asks_first(bare_home, capsys):
    _half_set_up(bare_home)
    asked = []
    for answer in ("no", ""):
        assert start_over.start_over(bare_home, ask=lambda q: asked.append(q) or answer) == 2
        assert (bare_home.keys_dir / keys.LIVE).exists() and (bare_home.state_dir / "setup.json").exists()
    assert "the keys" in capsys.readouterr().out and asked

    def eof(q):
        raise EOFError

    assert start_over.start_over(bare_home, ask=eof) == 2
    assert start_over.start_over(bare_home, ask=lambda q: "YES ") == 0
    assert bare_home.is_empty()


def test_start_over_never_runs_while_setup_runs(bare_home, monkeypatch, capsys):
    _half_set_up(bare_home)
    os.chmod(bare_home.root, 0o700)
    s = Setup(bare_home, monkeypatch, capsys)  # its start shreds the dead setup's keys; settings stay
    try:
        assert _cli(bare_home, "--yes") == 1
        assert "setup is running" in capsys.readouterr().err
        assert (bare_home.state_dir / "setup.json").exists() and (bare_home.hermes_dir / ".env").exists()
    finally:
        s.stop()
    assert s.rc["rc"] == 0
    assert _cli(bare_home, "--yes") == 0  # once it has stopped
    assert bare_home.is_empty()


def test_setup_refuses_while_start_over_holds_the_home(bare_home, capsys):
    fd = start_over.hold(bare_home, exclusive=True)
    try:
        assert server.main_setup(bare_home, "127.0.0.1", 0) == 1
        assert "being started over" in capsys.readouterr().err
    finally:
        os.close(fd)


def test_nothing_to_start_over(bare_home, capsys):
    assert _cli(bare_home, "--yes") == 0
    assert not bare_home.root.exists()
    assert main(["--home", str(bare_home.root), "setup", "--yes"]) == 2


def test_a_filesystem_without_locks_is_not_a_running_setup(bare_home, monkeypatch, capsys):
    """Only EWOULDBLOCK/EAGAIN mean "held"; ENOTSUP and the like say so, and start-over refuses."""
    import errno
    import fcntl

    _half_set_up(bare_home)
    for code in (errno.ENOTSUP, errno.ENOLCK):
        def no_locks(fd, op, code=code):
            raise OSError(code, os.strerror(code))

        monkeypatch.setattr(fcntl, "flock", no_locks)
        assert _cli(bare_home, "--yes") == 1
        err = capsys.readouterr().err
        assert os.strerror(code) in err and "stop it first" not in err
        assert (bare_home.keys_dir / keys.LIVE).exists()

    def held(fd, op):
        raise BlockingIOError(errno.EWOULDBLOCK, "busy")

    monkeypatch.setattr(fcntl, "flock", held)
    assert _cli(bare_home, "--yes") == 1
    assert "stop it first" in capsys.readouterr().err


def test_setup_runs_without_the_lock_where_locks_are_unsupported(bare_home, monkeypatch, capsys):
    import errno
    import fcntl

    def no_locks(fd, op):
        raise OSError(errno.ENOTSUP, os.strerror(errno.ENOTSUP))

    monkeypatch.setattr(fcntl, "flock", no_locks)
    s = Setup(bare_home, monkeypatch, capsys)
    s.stop()
    assert s.rc["rc"] == 0
