"""Reset, permanent delete, and moving to a new place with exactly one live copy."""

from __future__ import annotations

import json
import os
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
    agent_mem = home.agent_memory_dir.relative_to(home.root).as_posix() + "/"
    return [str(p) for p in home.story_paths() if p.parts[0] in wipe or p.as_posix().startswith(agent_mem)]


def reset(home: Home, *, keep_settings: bool, export_to: Path | None) -> dict:
    """Wipe memory (and settings unless kept). Exports first when *export_to* is given."""
    exported = archive.export(home, Path(export_to)) if export_to else None
    planned = plan_reset(home, keep_settings=keep_settings)
    for rel in planned:
        (home.root / rel).unlink()
    if home.root.is_dir():
        for idx in home.root.rglob("index.db*"):
            if idx.relative_to(home.root).parts[0] not in ("keys", "state") and (idx.is_file() or idx.is_symlink()):
                idx.unlink()
    for d in (home.memory_dir, home.agent_memory_dir, home.soul_dir, home.config_dir):
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
    if real.root.exists() and not (
        (real.keys_dir / keys.RECIPIENT).exists() or (real.state_dir / "instance.json").exists()
    ):
        raise RuntimeError(f"{real.root} is not a sidekick home (no keys or instance file); nothing was deleted")
    destroyed = keys.shred_keys(real)
    count = 0
    if real.root.exists():
        count = sum(1 for p in real.root.rglob("*") if p.is_file() or p.is_symlink())
        shutil.rmtree(real.root)
    if link is not None:
        link.unlink()
    return {"keys_destroyed": destroyed, "files_removed": count, "notice": NOTICE}


def _status(home: Home) -> str | None:
    try:
        return home.instance().get("status")
    except FileNotFoundError:
        raise RuntimeError(f"{home.root} has no instance file; it is not a working sidekick home") from None
    except ValueError as exc:
        raise RuntimeError(f"the instance file in {home.root} is damaged: {exc}") from exc


def require_live(home: Home) -> None:
    if _status(home) != "live":
        raise RuntimeError("this copy is not the live sidekick")


def _move_record(home: Home):
    return home.state_dir / "move.json"


def move_out(home: Home, dest: Path) -> Path:
    """Pack the sidekick for a move. This copy stops being live BEFORE the archive exists."""
    require_live(home)
    home.set_status("moving")
    try:
        out = archive.export(home, Path(dest), purpose="move")
        mac = archive._manifest(archive._read_outer(out))["mac"]
        tmp = _move_record(home).with_suffix(".tmp")
        tmp.write_text(json.dumps({"path": str(out.resolve()), "mac": mac}))
        os.replace(tmp, _move_record(home))
        return out
    except BaseException:
        home.set_status("live")
        raise


def cancel_move(home: Home) -> None:
    """Undo a move-out: this copy answers again. Any copy imported from the move archive must then be deleted."""
    if _status(home) != "moving":
        raise RuntimeError("this copy is not moving out; nothing to cancel")
    home.set_status("live")
    _move_record(home).unlink(missing_ok=True)


def retire(home: Home, *, move_archive: Path | None = None) -> dict:
    """After the new place is verified: destroy this old copy.

    Refused unless the move archive still exists and verifies, so a move that
    never finished cannot cost the only copy.
    """
    if _status(home) != "moving":
        raise RuntimeError("only a copy that has moved out can be retired")
    try:
        rec = json.loads(_move_record(home).read_text())
        path = Path(move_archive) if move_archive else Path(rec["path"])
        if archive._manifest(archive._read_outer(path)).get("mac") != rec["mac"]:
            raise archive.ArchiveDamaged(f"{path} is not the archive this copy packed")
        m = archive.verify(path, keys.load_identity(home))
        if m.get("purpose") != "move":
            raise archive.ArchiveDamaged(f"{path} is not a move archive")
    except (OSError, ValueError, KeyError) as exc:
        raise RuntimeError(
            f"the move archive could not be checked ({exc}); this copy was NOT retired. "
            "Pass --archive PATH if you moved the file, or run move-cancel."
        ) from exc
    home.set_status("retired")
    return delete(home)
