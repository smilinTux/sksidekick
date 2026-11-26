from __future__ import annotations

import datetime as dt
import errno
import json
import subprocess
from pathlib import Path

import pytest

from sksidekick import backup, drill, keys, lifecycle, serve
from sksidekick.home import Home

T0 = dt.datetime(2026, 11, 20, 12, 0, tzinfo=dt.timezone.utc)


class Clock:
    """Fake monotonic and wall clocks; sleep advances both unless told otherwise."""

    def __init__(self):
        self.mono = 1000.0
        self.now = T0
        self.slept: list[float] = []
        self.hooks: list = []  # callables run on each sleep, given the clock

    def sleep(self, s: float) -> None:
        self.slept.append(s)
        self.mono += s
        self.now += dt.timedelta(seconds=s)
        for h in list(self.hooks):
            h(self)


class FakeProc:
    def __init__(self, argv, exits: bool = False):
        self.argv = argv
        self.returncode = 1 if exits else None
        self.terminated = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def kill(self):
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode


@pytest.fixture
def live(story_home: Home, tmp_path: Path) -> Home:
    keys.create_keys(story_home)
    story_home.new_instance()
    backup.save_config(story_home, {"destinations": [{"kind": "dir", "path": str(tmp_path / "usb")}], "keep": 7, "interval_hours": 24})
    return story_home


def _run(home: Home, clock: Clock, *, loops: int, spawn=None, notices=None, gateway=("gw",)):
    count = {"n": 0}

    def stop() -> bool:
        count["n"] += 1
        return count["n"] > loops

    procs: list[FakeProc] = []

    def default_spawn(argv):
        p = FakeProc(argv)
        procs.append(p)
        return p

    code = serve.serve(
        home,
        gateway_argv=list(gateway),
        clock=lambda: clock.mono,
        wall=lambda: clock.now,
        sleep=clock.sleep,
        notify=(notices.append if notices is not None else (lambda m: None)),
        spawn=spawn or default_spawn,
        jitter=lambda: 0.0,
        stop=stop,
    )
    return code, procs


def test_refuses_when_not_live(live: Home, capsys):
    live.set_status("moving")
    code, procs = _run(live, Clock(), loops=3)
    assert code == 3 and procs == []
    assert "this copy is not the live sidekick" in capsys.readouterr().err


def test_refuses_when_hermes_home_is_outside_the_home(live: Home, capsys, monkeypatch, tmp_path: Path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "elsewhere"))
    code, procs = _run(live, Clock(), loops=3)
    assert code == 3 and procs == []
    assert "HERMES_HOME" in capsys.readouterr().err


def test_refuses_when_memory_is_linked_outside(live: Home, capsys, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(live.hermes_dir))
    live.hermes_dir.mkdir()
    (live.hermes_dir / "skmemory.json").write_text(json.dumps({"skcapstone_home": "~/.skcapstone", "agent": "x"}))
    code, procs = _run(live, Clock(), loops=3)
    assert code == 3 and procs == []
    assert "skcapstone_home" in capsys.readouterr().err


def test_starts_with_hermes_home_inside(live: Home, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(live.hermes_dir))
    code, procs = _run(live, Clock(), loops=1)
    assert code == 0 and len(procs) == 1


def test_stops_gateway_within_60s_after_move_out(live: Home, tmp_path: Path):
    clock = Clock()
    moved = {}

    def move(c: Clock):
        if "at" not in moved and c.mono >= 1300:
            lifecycle.move_out(live, tmp_path / "move.sksk")
            moved["at"] = c.mono

    clock.hooks.append(move)
    real: list[subprocess.Popen] = []

    def spawn(argv):
        p = subprocess.Popen(["sleep", "3600"])
        real.append(p)
        return p

    code, _ = _run(live, clock, loops=200, spawn=spawn)
    try:
        assert code == 0
        assert real and real[0].poll() is not None  # the real child was stopped
        assert clock.mono - moved["at"] <= serve.POLL
    finally:
        for p in real:
            if p.poll() is None:
                p.kill()


def test_backup_runs_when_stale_on_start(live: Home, tmp_path: Path):
    _run(live, Clock(), loops=1)
    assert backup.status(live, now=T0)["last_success"] is not None
    assert len(list((tmp_path / "usb").iterdir())) == 1


def test_backup_failure_notifies_user_with_last_good_date(live: Home, tmp_path: Path):
    notices: list[str] = []
    clock = Clock()
    _run(live, clock, loops=1, notices=notices)  # good backup at T0
    cfg = backup.load_config(live)
    cfg["destinations"] = [{"kind": "rclone", "remote": "nowhere-such-remote:"}]
    backup.save_config(live, cfg)
    clock.now += dt.timedelta(hours=25)
    _run(live, clock, loops=1, notices=notices)
    fails = [n for n in notices if n.startswith("Backup failed:")]
    assert fails and fails[-1].endswith(f"Your last good backup is from {T0.date().isoformat()}.")


def test_backup_failure_without_any_good_backup(live: Home):
    notices: list[str] = []
    backup.save_config(live, {"destinations": [], "keep": 7, "interval_hours": 24})
    _run(live, Clock(), loops=1, notices=notices)
    assert notices == [f"Backup failed: {backup.NO_DEST}. You have no backup yet."]


def test_failure_notice_is_not_repeated_every_minute(live: Home):
    notices: list[str] = []
    backup.save_config(live, {"destinations": [], "keep": 7, "interval_hours": 24})
    _run(live, Clock(), loops=2500, notices=notices)  # about 3.5 hours of polls
    assert len(notices) == 1


def test_drill_every_7_days(live: Home):
    clock = Clock()
    _run(live, clock, loops=1)
    first = drill.last_drill(live)
    assert first and first["ok"] is True
    clock.now += dt.timedelta(days=6)
    _run(live, clock, loops=1)
    assert drill.last_drill(live)["at"] == first["at"]
    clock.now += dt.timedelta(days=1, minutes=1)
    _run(live, clock, loops=1)
    assert drill.last_drill(live)["at"] != first["at"]


def test_drill_failure_notifies(live: Home, monkeypatch):
    notices: list[str] = []
    monkeypatch.setattr(drill, "run_drill", lambda home, **kw: {"ok": False, "error": "ArchiveDamaged: bad"})
    _run(live, Clock(), loops=1, notices=notices)
    assert (
        "Your restore test failed: ArchiveDamaged: bad. Your backups may not restore. Please check your backup destination."
        in notices
    )


def test_gateway_restart_backoff(live: Home):
    clock = Clock()
    starts: list[float] = []

    def spawn(argv):
        starts.append(clock.mono)
        return FakeProc(argv, exits=True)

    _run(live, clock, loops=400, spawn=spawn)
    # an exit is seen at the next poll, then the restart waits the backoff
    delays = [round(b - a - serve.POLL) for a, b in zip(starts, starts[1:])]
    assert delays[:7] == [5, 10, 20, 40, 80, 160, 300]
    assert len(delays) > 8 and all(d == 300 for d in delays[7:])


def test_disk_full_notifies_and_keeps_running(live: Home, monkeypatch):
    notices: list[str] = []

    def full(home, **kw):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(backup, "run_backup", full)
    code, procs = _run(live, Clock(), loops=5, notices=notices)
    assert code == 0
    assert notices.count(serve.DISK_FULL) == 1
    assert len(procs) == 1  # the gateway kept running; it was only stopped at shutdown


def test_backup_after_wall_clock_jump(live: Home):
    clock = Clock()
    _run(live, clock, loops=1)
    before = backup.status(live, now=clock.now)["last_success"]
    clock.now += dt.timedelta(hours=30)  # the laptop slept; the monotonic clock barely moved
    clock.mono += 1
    _run(live, clock, loops=1)
    assert backup.status(live, now=clock.now)["last_success"] != before


def test_notices_are_kept_in_state(live: Home):
    backup.save_config(live, {"destinations": [], "keep": 7, "interval_hours": 24})
    serve.default_notify(live)("hello")
    lines = (live.state_dir / "notices.jsonl").read_text().splitlines()
    assert json.loads(lines[-1])["text"] == "hello"


def test_default_notify_uses_hermes_send(live: Home, monkeypatch):
    calls = []
    monkeypatch.setattr(serve.subprocess, "run", lambda argv, **kw: calls.append(argv) or subprocess.CompletedProcess(argv, 0))
    (live.config_dir / "serve.json").write_text(json.dumps({"notify_to": "telegram"}))
    serve.default_notify(live)("Backup failed: x. You have no backup yet.")
    assert calls == [["hermes", "send", "--to", "telegram", "--quiet", "Backup failed: x. You have no backup yet."]]


def test_gateway_is_never_handed_to_another_supervisor(live: Home, monkeypatch):
    seen = {}

    def fake_popen(argv, **kw):
        seen["env"] = kw.get("env")
        return FakeProc(argv)

    monkeypatch.setattr(serve.subprocess, "Popen", fake_popen)
    p = serve.spawn_gateway(["hermes", "gateway", "run"])
    assert isinstance(p, FakeProc)
    assert seen["env"]["HERMES_GATEWAY_NO_SUPERVISE"] == "1"  # the fork's s6 image would otherwise keep a gateway after a move
