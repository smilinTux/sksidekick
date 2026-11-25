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


INSIDE_HOME = "that folder is inside the sidekick's own home, so a backup there is lost with it; choose a folder elsewhere"
SAME_DISK = (
    "That folder is on the same disk as the sidekick's data inside this container, so a backup there is lost "
    "with it. Start the container with a folder of this computer mounted, for example "
    "-v ~/sidekick-backups:/backups, then add it with: docker exec -it <container> sksidekick backup add-dir /backups"
)
MOUNTINFO = Path("/proc/self/mountinfo")
DATA_VOLUME = Path("/opt/data")  # the image's data volume (packaging/Dockerfile)


def _inside(path: Path, root: Path) -> bool:
    p, r = path.resolve(), root.resolve()
    return p == r or r in p.parents


def _dir(home: Home, target: str) -> dict:
    if _CONTROL.search(target):
        raise FieldError("target", "that folder name contains a line break or other characters it cannot hold")
    path = Path(target).expanduser()
    if not path.is_absolute():
        raise FieldError("target", "enter the full path of the folder, starting with /")
    if not path.is_dir():
        raise FieldError("target", "there is no folder at that path")
    if _inside(path, home.root):
        raise FieldError("target", INSIDE_HOME)
    try:
        fd, probe = tempfile.mkstemp(dir=path, prefix=".sksidekick-write-test-")
        os.close(fd)
        os.unlink(probe)
    except OSError:
        raise FieldError("target", "the sidekick cannot write to that folder") from None
    return {"kind": "dir", "path": str(path)}


def _mount_points() -> list[str] | None:
    """Mount points from /proc/self/mountinfo (field 5, octal escapes undone), or None where there is none."""
    try:
        text = MOUNTINFO.read_text()
    except OSError:
        return None
    out = []
    for line in text.splitlines():
        f = line.split(" ")
        if len(f) > 4:
            out.append(re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), f[4]))
    return out or None


def _mount_of(path: Path, mounts: list[str]) -> str:
    """The longest mount point that holds ``path`` (already resolved)."""
    p = str(path)
    held = [m for m in mounts if m == "/" or p == m or p.startswith(m.rstrip("/") + "/")]
    return max(held, key=len, default="/")


def same_disk_warning(home: Home, dest: dict | None) -> str | None:
    """In a container, a folder under the data volume, or on the same mount as the home, dies with the data.
    A bind-mounted host folder is its own mount even on the same host disk, so it never warns. Without
    mountinfo (not Linux) only the data volume check applies."""
    if not dest or dest["kind"] != "dir" or not state.in_container():
        return None
    target = Path(dest["path"]).resolve()
    if target == DATA_VOLUME or DATA_VOLUME in target.parents:
        return SAME_DISK
    mounts = _mount_points()
    if mounts is None:
        return None
    return SAME_DISK if _mount_of(target, mounts) == _mount_of(home.root.resolve(), mounts) else None


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
        dest = _dir(home, target)
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
    warn = same_disk_warning(home, dest)
    return {"warning": warn} if warn else {}
