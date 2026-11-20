"""Restore drills: prove the latest backup restores, instead of assuming it."""

from __future__ import annotations

import datetime as _dt
import json
import tempfile
from pathlib import Path

from sksidekick import archive, backup, keys
from sksidekick.home import Home


def _path(home: Home) -> Path:
    return home.state_dir / "drill.json"


def run_drill(home: Home) -> dict:
    at = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    latest = backup.latest(home)
    result = {"at": at, "ok": False, "backup": latest.name if latest else None, "created": None, "files": 0, "error": None}
    if latest is None:
        result["error"] = "no backup to test"
    else:
        try:
            with tempfile.TemporaryDirectory(prefix="sksidekick-drill-") as tmp:
                scratch = Home(Path(tmp) / "home")
                manifest = archive.import_archive(latest, scratch, identity=keys.load_identity(home))
                restored = len(scratch.story_paths())
                if restored != len(manifest["files"]):
                    raise archive.ArchiveDamaged(f"restored {restored} files, manifest lists {len(manifest['files'])}")
                result.update(ok=True, files=restored, created=manifest.get("created"))
        except Exception as exc:  # a drill records every failure; it never crashes the caller
            result["error"] = f"{type(exc).__name__}: {exc}"
    home.state_dir.mkdir(parents=True, exist_ok=True)
    _path(home).write_text(json.dumps(result, indent=1))
    return result


def last_drill(home: Home) -> dict | None:
    p = _path(home)
    return json.loads(p.read_text()) if p.exists() else None
