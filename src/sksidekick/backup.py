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
import shutil
import subprocess
import tempfile
from pathlib import Path

from sksidekick import archive, keys
from sksidekick.home import Home

DEFAULTS = {"destinations": [], "keep": 7, "interval_hours": 24}
NO_DEST = "no backup destination configured"
_NAME = re.compile(r"^sidekick-[0-9a-f]+-\d{8}T\d{6}Z\.sksk$")


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


def _to_dir(src: Path, dest: dict, name: str, keep: int) -> None:
    d = Path(dest["path"]).expanduser()
    d.mkdir(parents=True, exist_ok=True)
    part = d / (name + ".part")
    try:
        shutil.copyfile(src, part)
        with open(part, "rb") as f:
            os.fsync(f.fileno())
        os.replace(part, d / name)
    finally:
        if part.exists():
            part.unlink()
    ours = sorted(p for p in d.iterdir() if _NAME.match(p.name))
    for old in ours[: max(len(ours) - keep, 0)]:
        old.unlink()


def _rclone(*args: str) -> subprocess.CompletedProcess:
    proc = subprocess.run(["rclone", *args], capture_output=True, text=True, timeout=3600, check=False)
    if proc.returncode != 0:
        raise OSError(f"rclone {args[0]} failed: {proc.stderr.strip()[:300]}")
    return proc


def _to_rclone(src: Path, dest: dict, name: str, keep: int) -> None:
    remote = dest["remote"].rstrip("/")
    _rclone("copyto", str(src), f"{remote}/{name}")
    listed = [n.strip() for n in _rclone("lsf", remote).stdout.splitlines()]
    if name not in listed:
        raise OSError("rclone copy did not appear at the destination")
    ours = sorted(n for n in listed if _NAME.match(n))
    for old in ours[: max(len(ours) - keep, 0)]:
        _rclone("deletefile", f"{remote}/{old}")


_WRITERS = {"dir": _to_dir, "rclone": _to_rclone}


def run_backup(home: Home, *, now: _dt.datetime | None = None) -> list[dict]:
    now = now or _dt.datetime.now(_dt.timezone.utc)
    cfg = load_config(home)
    state = _load_state(home)
    if not cfg["destinations"]:
        results = [{"destination": None, "ok": False, "error": NO_DEST}]
        state.update(last_error=NO_DEST, results=results)
        _save_state(home, state)
        return results
    name = f"sidekick-{home.instance()['instance_id']}-{now.strftime('%Y%m%dT%H%M%SZ')}.sksk"
    results = []
    with tempfile.TemporaryDirectory(prefix="sksidekick-backup-") as tmp:
        src = archive.export(home, Path(tmp) / name)
        archive.verify(src, keys.load_identity(home))
        for dest in cfg["destinations"]:
            try:
                _WRITERS[dest["kind"]](src, dest, name, int(cfg["keep"]))
                results.append({"destination": _label(dest), "ok": True, "name": name})
            except (OSError, KeyError, subprocess.SubprocessError) as exc:
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
    for dest in load_config(home)["destinations"]:
        if dest.get("kind") == "dir":
            d = Path(dest["path"]).expanduser()
            ours = sorted(p for p in d.iterdir() if _NAME.match(p.name)) if d.is_dir() else []
            return ours[-1] if ours else None
    return None
