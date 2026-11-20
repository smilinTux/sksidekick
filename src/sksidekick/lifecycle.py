"""Reset, permanent delete, and moving to a new place with exactly one live copy."""

from __future__ import annotations

import shutil
from pathlib import Path

from sksidekick import archive, keys
from sksidekick.home import Home

NOTICE = (
    "Your sidekick's keys are destroyed and its files on this device are removed. Backups and archives made with "
    "those keys can no longer be opened by anyone who has only those files. Destroy any printed recovery phrase and "
    "any plain exports you made: the sidekick cannot reach those. Outside channels such as Telegram or Slack keep "
    "their own copy of your conversations. On SSDs and Macs the disk can keep traces of removed files until they are "
    "overwritten, so erase the disk before you give the device away."
)


def plan_reset(home: Home, *, keep_settings: bool) -> list[str]:
    wipe = ("memory", "journal.md") if keep_settings else ("memory", "journal.md", "soul", "config")
    return [str(p) for p in home.story_paths() if p.parts[0] in wipe]


def reset(home: Home, *, keep_settings: bool, export_to: Path | None) -> dict:
    """Wipe memory (and settings unless kept). Exports first when *export_to* is given."""
    exported = archive.export(home, Path(export_to)) if export_to else None
    planned = plan_reset(home, keep_settings=keep_settings)
    for rel in planned:
        (home.root / rel).unlink()
    if home.memory_dir.is_dir():
        for idx in home.memory_dir.rglob("index.db*"):
            idx.unlink()
    for d in (home.memory_dir, home.soul_dir, home.config_dir):
        if d.is_dir():
            for sub in sorted((p for p in d.rglob("*") if p.is_dir()), reverse=True):
                if not any(sub.iterdir()):
                    sub.rmdir()
    return {"removed": len(planned), "export": exported}


def delete(home: Home) -> dict:
    """Permanently delete: destroy the keys first (crypto-shred), then every file.

    A home reached through a symlink is deleted at its real location, then the link.
    """
    link = home.root if home.root.is_symlink() else None
    real = Home(home.root.resolve()) if home.root.exists() else home
    destroyed = keys.shred_keys(real)
    count = 0
    if real.root.exists():
        count = sum(1 for p in real.root.rglob("*") if p.is_file() or p.is_symlink())
        shutil.rmtree(real.root)
    if link is not None:
        link.unlink()
    return {"keys_destroyed": destroyed, "files_removed": count, "notice": NOTICE}


def require_live(home: Home) -> None:
    if home.instance().get("status") != "live":
        raise RuntimeError("this copy is not the live sidekick")


def move_out(home: Home, dest: Path) -> Path:
    """Pack the sidekick for a move. This copy stops being live BEFORE the archive exists."""
    require_live(home)
    home.set_status("moving")
    try:
        return archive.export(home, Path(dest), purpose="move")
    except BaseException:
        home.set_status("live")
        raise


def retire(home: Home) -> dict:
    """After the new place is verified: destroy this old copy."""
    if home.instance().get("status") != "moving":
        raise RuntimeError("only a copy that has moved out can be retired")
    home.set_status("retired")
    return delete(home)
