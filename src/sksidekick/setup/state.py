"""The setup step machine: which step is next, persisted in ``state/setup.json``."""

from __future__ import annotations

import json
import os
from pathlib import Path

from sksidekick.home import Home

Steps = ("tier", "channels", "personality", "phrase", "backup", "done")


class OutOfOrder(Exception):
    """The step asked for is not the next one."""


def _path(home: Home) -> Path:
    return home.state_dir / "setup.json"


def _completed(home: Home) -> list[str]:
    try:
        return list(json.loads(_path(home).read_text()).get("completed", []))
    except FileNotFoundError:
        return []


def load(home: Home) -> dict:
    done = _completed(home)
    return {"completed": done, "next": Steps[len(done)] if len(done) < len(Steps) else None}


def is_complete(home: Home) -> bool:
    return load(home)["next"] is None


def check(home: Home, step: str) -> None:
    """Raise OutOfOrder unless ``step`` is the next one."""
    if step not in Steps or load(home)["next"] != step:
        raise OutOfOrder(step)


def advance(home: Home, step: str) -> None:
    check(home, step)
    done = _completed(home) + [step]
    home.state_dir.mkdir(parents=True, exist_ok=True)
    tmp = _path(home).with_suffix(".tmp")
    tmp.write_text(json.dumps({"completed": done}))
    os.replace(tmp, _path(home))


def ensure_hermes_dir(home: Home) -> Path:
    """Anything that writes under <home>/hermes calls this first: the dir is private (0700)."""
    d = home.hermes_dir
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    return d
