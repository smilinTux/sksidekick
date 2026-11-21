"""One sidekick's home directory: where its story lives and which copy is live."""

from __future__ import annotations

import fnmatch
import json
import os
import secrets
from pathlib import Path, PurePosixPath

STATUSES = ("live", "moving", "retired")
_NOT_STORY = ("keys", "state")
STAGING_PREFIX = ".sksidekick-import-"
HERMES = "hermes"
SKMEMORY_AGENT = "sidekick"
# hermes-skmemory in its default (isolated) mode keeps the sidekick's memory store here,
# inside HERMES_HOME and so inside the sidekick home: it is story and goes in every archive.
AGENT_STORE = f"{HERMES}/.skmemory/agents/{SKMEMORY_AGENT}"
# Rebuildable or machine-local parts of the Hermes home: never story, never in an archive.
EXCLUDE_DIRS = ("hermes/logs", "hermes/cache", "hermes/tools", "hermes/plugins", f"{AGENT_STORE}/logs")
EXCLUDE_GLOBS = ("*.lock", "*.pid")


def excluded(rel: PurePosixPath | Path | str) -> bool:
    """True for paths under EXCLUDE_DIRS or matching EXCLUDE_GLOBS (compared case-insensitively)."""
    p = PurePosixPath(str(rel).replace("\\", "/"))
    low = p.as_posix().casefold()
    if any(low == d or low.startswith(d + "/") for d in EXCLUDE_DIRS):
        return True
    return any(fnmatch.fnmatch(p.name.casefold(), g) for g in EXCLUDE_GLOBS)


def _skip(rel: Path) -> bool:
    """Keys, state, an interrupted import's staging dir and EXCLUDE are never story."""
    return rel.parts[0] in _NOT_STORY or rel.parts[0].startswith(STAGING_PREFIX) or excluded(rel.as_posix())


class Home:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).expanduser()

    @property
    def memory_dir(self) -> Path:
        return self.root / "memory"

    @property
    def soul_dir(self) -> Path:
        return self.root / "soul"

    @property
    def config_dir(self) -> Path:
        return self.root / "config"

    @property
    def hermes_dir(self) -> Path:
        return self.root / HERMES

    @property
    def agent_memory_dir(self) -> Path:
        """Where Hermes' memory provider keeps the memories (``memory/`` is for memories added outside Hermes)."""
        return self.root / AGENT_STORE / "memory"

    @property
    def keys_dir(self) -> Path:
        return self.root / "keys"

    @property
    def state_dir(self) -> Path:
        return self.root / "state"

    @property
    def journal_path(self) -> Path:
        return self.root / "journal.md"

    def story_paths(self) -> list[Path]:
        """Relative paths of every file that is part of the story.

        Keys and state are not story; the search index is rebuilt from the files.
        """
        if not self.root.is_dir():
            return []
        out = []
        for p in self.root.rglob("*"):
            rel = p.relative_to(self.root)
            if _skip(rel) or p.name.startswith("index.db"):
                continue
            if p.is_file() and not p.is_symlink():
                out.append(rel)
        return sorted(out)

    def symlinks(self) -> list[Path]:
        """Story-area symlinks. Export refuses them rather than silently leaving their targets out."""
        if not self.root.is_dir():
            return []
        out = []
        for p in self.root.rglob("*"):
            rel = p.relative_to(self.root)
            if not _skip(rel) and p.is_symlink():
                out.append(rel)
        return sorted(out)

    def is_empty(self) -> bool:
        """True when the home holds no file or link at all (empty directories and a
        leftover import staging dir are fine)."""
        if not self.root.exists():
            return True
        return not any(
            (p.is_file() or p.is_symlink()) and not p.relative_to(self.root).parts[0].startswith(STAGING_PREFIX)
            for p in self.root.rglob("*")
        )

    def _instance_path(self) -> Path:
        return self.state_dir / "instance.json"

    def instance(self) -> dict:
        return json.loads(self._instance_path().read_text())

    def _write_instance(self, data: dict) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        tmp = self._instance_path().with_suffix(".tmp")
        tmp.write_text(json.dumps(data))
        os.replace(tmp, self._instance_path())

    def new_instance(self) -> str:
        iid = secrets.token_hex(8)
        self._write_instance({"instance_id": iid, "status": "live"})
        return iid

    def set_status(self, status: str) -> None:
        if status not in STATUSES:
            raise ValueError(f"unknown status {status!r}; one of {', '.join(STATUSES)}")
        data = self.instance()
        data["status"] = status
        self._write_instance(data)
