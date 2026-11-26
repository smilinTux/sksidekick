"""Scheduled encrypted backups to destinations the user picks.

Each run exports once, verifies the archive with the live key, then copies it
to every destination. Old backups are rotated away only where the new copy
landed, so a failing destination never loses what it already holds.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
from pathlib import Path

from sksidekick import archive, keys, lifecycle
from sksidekick.home import Home

DEFAULTS = {"destinations": [], "keep": 7, "interval_hours": 24}
NO_DEST = "no backup destination configured"
_NAME = re.compile(r"^sidekick-(?P<iid>[0-9a-f]+)-(?P<ts>\d{8}T\d{6}Z)\.sksk$")


def _config_path(home: Home) -> Path:
    return home.config_dir / "backup.json"


def _state_path(home: Home) -> Path:
    return home.state_dir / "backup.json"


def load_config(home: Home) -> dict:
    p = _config_path(home)
    cfg = dict(DEFAULTS)
    if p.exists():
        cfg.update(json.loads(p.read_text()))
    return cfg


def save_config(home: Home, cfg: dict) -> None:
    home.config_dir.mkdir(parents=True, exist_ok=True)
    _config_path(home).write_text(json.dumps(cfg, indent=1))


def _load_state(home: Home) -> dict:
    p = _state_path(home)
    return json.loads(p.read_text()) if p.exists() else {"last_success": None, "last_error": None, "results": []}


def _save_state(home: Home, state: dict) -> None:
    home.state_dir.mkdir(parents=True, exist_ok=True)
    _state_path(home).write_text(json.dumps(state, indent=1))


def _label(dest: dict) -> str:
    return dest.get("path") or dest.get("remote") or "?"


def _ours(names, iid: str) -> list[str]:
    """This instance's backups, oldest first (by timestamp, never by instance id)."""
    hits = [(m.group("ts"), n) for n in names if (m := _NAME.match(n)) and m.group("iid") == iid]
    return [n for _, n in sorted(hits)]


def _join(remote: str, name: str) -> str:
    return f"{remote}{name}" if remote.endswith((":", "/")) else f"{remote}/{name}"


def _to_dir(src: Path, dest: dict, name: str, keep: int, iid: str, identity) -> None:
    d = Path(dest["path"]).expanduser()
    d.mkdir(parents=True, exist_ok=True)
    part = d / f"{name}.{secrets.token_hex(4)}.part"
    try:
        shutil.copyfile(src, part)
        with open(part, "rb") as f:
            keys.full_fsync(f.fileno())
        archive.verify(part, identity)  # prove the copy that landed, not just the source
        os.replace(part, d / name)
    finally:
        if part.exists():
            part.unlink()
    ours = _ours((p.name for p in d.iterdir()), iid)
    for old in ours[: max(len(ours) - keep, 0)]:
        (d / old).unlink()


def _rclone(*args: str) -> subprocess.CompletedProcess:
    proc = subprocess.run(["rclone", *args], capture_output=True, text=True, timeout=3600, check=False)
    if proc.returncode != 0:
        raise OSError(f"rclone {args[0]} failed: {proc.stderr.strip()[:300]}")
    return proc


def _to_rclone(src: Path, dest: dict, name: str, keep: int, iid: str, identity) -> None:
    remote = dest["remote"]
    _rclone("copyto", str(src), _join(remote, name))
    listed = [n.strip() for n in _rclone("lsf", remote).stdout.splitlines()]
    if name not in listed:
        raise OSError("rclone copy did not appear at the destination")
    ours = _ours(listed, iid)
    for old in ours[: max(len(ours) - keep, 0)]:
        _rclone("deletefile", _join(remote, old))


_WRITERS = {"dir": _to_dir, "rclone": _to_rclone}


def run_backup(home: Home, *, now: _dt.datetime | None = None) -> list[dict]:
    now = now or _dt.datetime.now(_dt.timezone.utc)
    try:
        state = _load_state(home)
    except (OSError, ValueError):
        state = {"last_success": None, "last_error": None, "results": []}

    def fail(msg: str) -> list[dict]:
        results = [{"destination": None, "ok": False, "error": msg}]
        state.update(last_error=msg, results=results)
        _save_state(home, state)
        return results

    try:
        cfg = load_config(home)
    except (OSError, ValueError) as exc:
        return fail(f"backup settings unreadable: {exc}")
    keep = cfg.get("keep")
    if not isinstance(keep, int) or isinstance(keep, bool) or keep < 1:
        return fail(f"backup setting keep must be a whole number of 1 or more, not {keep!r}; nothing was deleted")
    if not cfg.get("destinations"):
        return fail(NO_DEST)
    try:
        lifecycle.require_live(home)
        iid = home.instance()["instance_id"]
        identity = keys.load_identity(home)
    except (RuntimeError, OSError, ValueError, KeyError) as exc:
        return fail(str(exc) or type(exc).__name__)
    name = f"sidekick-{iid}-{now.strftime('%Y%m%dT%H%M%SZ')}.sksk"
    results = []
    with tempfile.TemporaryDirectory(prefix="sksidekick-backup-") as tmp:
        try:
            src = archive.export(home, Path(tmp) / name, purpose="backup")
            archive.verify(src, identity)
        except (OSError, ValueError) as exc:
            return fail(f"could not make the backup: {exc}")
        for dest in cfg["destinations"]:
            try:
                _WRITERS[dest["kind"]](src, dest, name, keep, iid, identity)
                results.append({"destination": _label(dest), "ok": True, "name": name})
            except (OSError, KeyError, ValueError, subprocess.SubprocessError) as exc:
                results.append({"destination": _label(dest), "ok": False, "error": str(exc) or type(exc).__name__})
    errors = [r["error"] for r in results if not r["ok"]]
    if any(r["ok"] for r in results):
        state["last_success"] = now.isoformat(timespec="seconds")
    state.update(last_error="; ".join(errors) or None, results=results)
    _save_state(home, state)
    return results


def status(home: Home, *, now: _dt.datetime | None = None) -> dict:
    now = now or _dt.datetime.now(_dt.timezone.utc)
    state = _load_state(home)
    last = state.get("last_success")
    interval = _dt.timedelta(hours=float(load_config(home)["interval_hours"]))
    stale = last is None or now - _dt.datetime.fromisoformat(last) > 2 * interval
    return {"last_success": last, "stale": stale, "last_error": state.get("last_error")}


def latest(home: Home) -> Path | None:
    """This instance's newest backup in the first directory destination."""
    iid = home.instance()["instance_id"]
    for dest in load_config(home)["destinations"]:
        if dest.get("kind") == "dir":
            d = Path(dest["path"]).expanduser()
            ours = _ours((p.name for p in d.iterdir()), iid) if d.is_dir() else []
            return d / ours[-1] if ours else None
    return None
