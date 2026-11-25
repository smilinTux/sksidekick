"""POST /api/backup: where backups go (a folder, an rclone remote, or later), then the sidekick goes live.

All or nothing: if going live fails, the backup settings, instance.json and setup.json are put back.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

from sksidekick import backup, keys
from sksidekick.home import Home
from sksidekick.serve import backup_failed
from sksidekick.setup import state
from sksidekick.setup.state import FieldError

# An rclone remote spec: a remote name (no leading dash or space), a colon, then a path.
_REMOTE = re.compile(r"[A-Za-z0-9_.+@][A-Za-z0-9_.+@ -]*:[^\x00-\x1f\x7f]*")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def warning(home: Home) -> str | None:
    """The serve loop's own words when there is nowhere to back up to, for /api/state."""
    try:
        dests = backup.load_config(home)["destinations"]
    except (OSError, ValueError):
        dests = []
    return None if dests else backup_failed(backup.NO_DEST, None)


def _dir(target: str) -> dict:
    if _CONTROL.search(target):
        raise FieldError("target", "that folder name contains a line break or other characters it cannot hold")
    path = Path(target).expanduser()
    if not path.is_absolute():
        raise FieldError("target", "enter the full path of the folder, starting with /")
    if not path.is_dir():
        raise FieldError("target", "there is no folder at that path")
    try:
        fd, probe = tempfile.mkstemp(dir=path, prefix=".sksidekick-write-test-")
        os.close(fd)
        os.unlink(probe)
    except OSError:
        raise FieldError("target", "the sidekick cannot write to that folder") from None
    return {"kind": "dir", "path": str(path)}


def _rclone(target: str) -> dict:
    name = target.partition(":")[0]
    if not _REMOTE.fullmatch(target) or name.endswith(" "):
        raise FieldError("target", "enter an rclone remote as name:path, for example mydrive:sidekick")
    return {"kind": "rclone", "remote": target}


def _snapshot(paths: list[Path]) -> dict[Path, bytes | None]:
    return {p: (p.read_bytes() if p.exists() else None) for p in paths}


def _restore(snap: dict[Path, bytes | None]) -> None:
    for p, data in snap.items():
        if data is None:
            p.unlink(missing_ok=True)
        else:
            p.write_bytes(data)


def handle(home: Home, body: dict) -> dict:
    kind, target = body.get("kind"), body.get("target")
    target = target.strip() if isinstance(target, str) else ""
    if kind == "dir":
        dest = _dir(target)
    elif kind == "rclone":
        dest = _rclone(target)
    elif kind == "later":
        dest = None
    else:
        raise FieldError("kind", "choose a folder, an rclone remote, or later")
    if not (home.keys_dir / keys.LIVE).is_file():
        raise FieldError("kind", "the sidekick has no key yet; go back to the recovery phrase step")
    for d in (home.memory_dir, home.soul_dir, home.config_dir):  # as sksidekick init lays the home out
        d.mkdir(parents=True, exist_ok=True)
    os.chmod(home.root, 0o700)
    snap = _snapshot([backup._config_path(home), home.state_dir / "instance.json", home.state_dir / "setup.json"])
    try:
        if dest:
            cfg = backup.load_config(home)
            cfg["destinations"].append(dest)
            backup.save_config(home, cfg)
        home.new_instance()
        state.advance(home, "backup")
        state.advance(home, "done")
    except BaseException:
        _restore(snap)
        raise
    return {}
