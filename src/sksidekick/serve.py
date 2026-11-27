"""``sksidekick serve``: run the Hermes gateway, and only while this copy is the live one.

It refuses to start unless the copy is live and Hermes' private memory store is
empty, restarts the gateway with backoff, stops it within seconds (well inside the
design's 60 s) of the copy leaving ``live`` (a move), and runs backups and restore drills on wall-clock
schedules so a laptop that slept past the time catches up on wake. Problems go to
the user only: ``state/notices.jsonl`` and, when ``config/serve.json`` names a
platform in ``notify_to``, ``hermes send --to <platform>`` (its home channel).
"""

from __future__ import annotations

import datetime as _dt
import errno
import json
import random
import signal
import subprocess
import sys
import os
import time
from pathlib import Path
from typing import Callable

from sksidekick import backup, drill, lifecycle, seat_notice
from sksidekick.home import Home

TICK = 60.0  # backups and drills are considered once a minute
POLL = 5.0  # the gateway and the live status are checked every few seconds
BACKOFF_START, BACKOFF_MAX, STABLE_AFTER = 5.0, 300.0, 300.0
DRILL_EVERY = _dt.timedelta(days=7)
RETRY_EVERY = _dt.timedelta(hours=1)
RENOTIFY_EVERY = _dt.timedelta(hours=24)
DEFAULT_GATEWAY = ["hermes", "gateway", "run"]
NOT_LIVE = "this copy is not the live sidekick"
DISK_FULL = "Your sidekick's disk is full. It keeps running, but it cannot save backups until there is space."
_DISK_ERRNOS = {errno.ENOSPC, errno.EROFS, getattr(errno, "EDQUOT", errno.ENOSPC)}


def backup_failed(error: str, last_success: str | None) -> str:
    tail = f"Your last good backup is from {last_success[:10]}." if last_success else "You have no backup yet."
    return f"Backup failed: {error}. {tail}"


def drill_failed(error: str) -> str:
    return f"Your restore test failed: {error}. Your backups may not restore. Please check your backup destination."


def load_config(home: Home) -> dict:
    cfg = {"notify_to": None, "gateway_argv": DEFAULT_GATEWAY}
    p = home.config_dir / "serve.json"
    if p.exists():
        cfg.update(json.loads(p.read_text()))
    return cfg


def default_notify(home: Home) -> Callable[[str], None]:
    def notify(text: str) -> None:
        now = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
        try:
            home.state_dir.mkdir(parents=True, exist_ok=True)
            with open(home.state_dir / "notices.jsonl", "a") as f:
                f.write(json.dumps({"at": now, "text": text}) + "\n")
        except OSError:
            pass  # a full disk must not stop the message going out
        try:
            to = load_config(home).get("notify_to")
        except (OSError, ValueError):
            to = None
        if to:
            try:
                subprocess.run(["hermes", "send", "--to", to, "--quiet", text], capture_output=True, timeout=60, check=False)
            except (OSError, subprocess.SubprocessError):
                pass
        print(f"sksidekick: {text}", file=sys.stderr)

    return notify


def precheck(home: Home) -> str | None:
    """Why this copy must not start, or None."""
    try:
        lifecycle.require_live(home)
    except RuntimeError as exc:
        return str(exc)
    # Memory must land inside the home, or backups silently miss it.
    hermes_home = Path(os.environ.get("HERMES_HOME") or home.hermes_dir).expanduser()
    if hermes_home.resolve() != home.hermes_dir.resolve():
        return (f"HERMES_HOME is {hermes_home}, not {home.hermes_dir}: Hermes would keep conversations and memory "
                "where backups miss them. Set HERMES_HOME to the sidekick home's hermes/ folder.")
    plugin_cfg = home.hermes_dir / "skmemory.json"
    if plugin_cfg.exists():
        try:
            linked = str(json.loads(plugin_cfg.read_text()).get("skcapstone_home") or "").strip()
        except (OSError, ValueError, AttributeError) as exc:
            return f"{plugin_cfg} is unreadable ({exc}); fix it so memory is known to stay in the home."
        if linked:
            return (f"{plugin_cfg} links memory to {linked}, outside the sidekick home, so backups would miss it. "
                    "Remove skcapstone_home (a sidekick uses the plugin's own store), then start again.")
    return None


def _parse(ts: str) -> _dt.datetime:
    t = _dt.datetime.fromisoformat(ts)
    return t if t.tzinfo else t.replace(tzinfo=_dt.timezone.utc)


def _is_disk_error(exc: BaseException) -> bool:
    return isinstance(exc, OSError) and exc.errno in _DISK_ERRNOS


class _Notices:
    """Say each problem once, then again only if it changes or a day passes."""

    def __init__(self, notify: Callable[[str], None]):
        self.notify = notify
        self.last: dict[str, tuple[str, _dt.datetime]] = {}

    def say(self, kind: str, text: str, now: _dt.datetime) -> None:
        prev = self.last.get(kind)
        if prev and prev[0] == text and now - prev[1] < RENOTIFY_EVERY:
            return
        self.last[kind] = (text, now)
        self.notify(text)

    def clear(self, kind: str) -> None:
        self.last.pop(kind, None)


def spawn_gateway(argv: list[str]):
    """Start the gateway as OUR child. Hermes' s6 image would otherwise hand a bare ``gateway run`` to its own
    supervisor, which keeps a gateway answering after this copy moves out; serve is the only supervisor."""
    env = dict(os.environ, HERMES_GATEWAY_NO_SUPERVISE="1")
    return subprocess.Popen(argv, env=env)


def _stop_child(child) -> None:
    if child is None or child.poll() is not None:
        return
    child.terminate()
    try:
        child.wait(timeout=20)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait()


def _backup_step(home: Home, now: _dt.datetime, state: dict, notices: _Notices, jitter: float) -> None:
    try:
        cfg = backup.load_config(home)
        interval = _dt.timedelta(hours=float(cfg.get("interval_hours", 24)))
        last = backup.status(home, now=now)["last_success"]
    except (OSError, ValueError, TypeError) as exc:
        notices.say("backup", backup_failed(f"backup settings unreadable: {exc}", None), now)
        return
    due = last is None or now - _parse(last) >= interval + _dt.timedelta(seconds=jitter)
    tried = state.get("backup_tried")
    if not due or (tried is not None and now - tried < min(interval, RETRY_EVERY)):
        return
    state["backup_tried"] = now
    try:
        results = backup.run_backup(home, now=now)
    except OSError as exc:
        if _is_disk_error(exc):
            notices.say("disk", DISK_FULL, now)
        else:
            notices.say("backup", backup_failed(str(exc), last), now)
        return
    errors = [r["error"] for r in results if not r["ok"]]
    if any("No space left" in e or "Read-only file system" in e for e in errors):
        notices.say("disk", DISK_FULL, now)
    elif errors:
        notices.say("backup", backup_failed("; ".join(errors), backup.status(home, now=now)["last_success"]), now)
    else:
        notices.clear("backup")
        notices.clear("disk")


def _drill_step(home: Home, now: _dt.datetime, notices: _Notices) -> None:
    last = drill.last_drill(home)
    if last and last.get("at") and now - _parse(last["at"]) < DRILL_EVERY:
        return
    try:
        if backup.latest(home) is None:
            return
    except (OSError, ValueError, KeyError):
        return
    res = drill.run_drill(home, now=now)
    if res.get("ok"):
        notices.clear("drill")
    else:
        notices.say("drill", drill_failed(res.get("error") or "unknown error"), now)


def serve(
    home: Home,
    *,
    gateway_argv: list[str],
    clock: Callable[[], float] = time.monotonic,
    wall: Callable[[], _dt.datetime] = lambda: _dt.datetime.now(_dt.timezone.utc),
    sleep: Callable[[float], None] = time.sleep,
    notify: Callable[[str], None] | None = None,
    spawn: Callable[[list[str]], object] = spawn_gateway,
    jitter: Callable[[], float] = lambda: random.uniform(0, 600),
    stop: Callable[[], bool] = lambda: False,
    notice: Callable[[Home], object] = seat_notice.Notice,
) -> int:
    """Supervise the gateway. Returns 3 when the copy must not run, 0 when stopped."""
    why = precheck(home)
    if why:
        print(f"sksidekick serve: {why}", file=sys.stderr)
        return 3
    notices = _Notices(notify or default_notify(home))
    child = None
    started = next_start = clock()
    backoff = BACKOFF_START
    state: dict = {}
    backup_jitter = jitter()
    last_check: float | None = None
    # While a seat is installed: the plain answer Hermes falls back to when the gateway refuses it (#9).
    seat_note = notice(home)
    try:
        while not stop():
            try:
                lifecycle.require_live(home)
            except RuntimeError as exc:
                print(f"sksidekick serve: {exc}; stopping the gateway", file=sys.stderr)
                return 0
            seat_note.ensure()
            mono = clock()
            if child is not None and child.poll() is not None:
                child = None
                next_start = mono + backoff
                backoff = min(backoff * 2, BACKOFF_MAX)
            if child is None and mono >= next_start:
                try:
                    child, started = spawn(list(gateway_argv)), mono
                except OSError as exc:
                    print(f"sksidekick serve: cannot start the gateway: {exc}", file=sys.stderr)
                    next_start = mono + backoff
                    backoff = min(backoff * 2, BACKOFF_MAX)
            if child is not None and mono - started >= STABLE_AFTER:
                backoff = BACKOFF_START
            if last_check is None or mono - last_check >= TICK:
                last_check = mono
                now = wall()
                _backup_step(home, now, state, notices, backup_jitter)
                try:
                    _drill_step(home, now, notices)
                except OSError as exc:
                    if _is_disk_error(exc):
                        notices.say("disk", DISK_FULL, now)
            wait = POLL if child is not None else max(min(POLL, next_start - clock()), 0.0)
            sleep(wait)
        return 0
    finally:
        _stop_child(child)
        seat_note.close()


def main_serve(home: Home, gateway_argv: list[str] | None) -> int:
    """Entry point for the CLI: SIGTERM or SIGINT stops the loop cleanly."""
    flag = {"stop": False}

    def handler(signum, frame):
        flag["stop"] = True

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, handler)
    argv = gateway_argv or load_config(home).get("gateway_argv") or DEFAULT_GATEWAY

    def nap(s: float) -> None:
        end = time.monotonic() + s
        while not flag["stop"] and time.monotonic() < end:
            time.sleep(min(1.0, end - time.monotonic()))

    return serve(home, gateway_argv=list(argv), sleep=nap, stop=lambda: flag["stop"])
