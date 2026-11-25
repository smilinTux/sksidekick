"""The setup step machine: which step is next, persisted in ``state/setup.json``."""

from __future__ import annotations

import json
import os
from pathlib import Path

from sksidekick.home import Home

Steps = ("tier", "channels", "personality", "phrase", "backup", "done")


class OutOfOrder(Exception):
    """The step asked for is not the next one."""


class Conflict(Exception):
    """The request cannot run now (for example the server is stopping): the router answers 409 {error}."""


class FieldError(Exception):
    """A handler rejects one form field: the router answers 422 {field, error, **extra} and does not advance."""

    def __init__(self, field: str, message: str, extra: dict | None = None) -> None:
        super().__init__(message)
        self.field = field
        self.message = message
        self.extra = extra or {}


def _path(home: Home) -> Path:
    return home.state_dir / "setup.json"


def _read(home: Home) -> dict:
    try:
        return dict(json.loads(_path(home).read_text()))
    except FileNotFoundError:
        return {}


def _completed(home: Home) -> list[str]:
    return list(_read(home).get("completed", []))


def load(home: Home) -> dict:
    """Progress, and the choice made at each completed step (ids only, never a secret) for the page to show."""
    data = _read(home)
    done = list(data.get("completed", []))
    choices = {k: v for k, v in dict(data.get("choices", {})).items() if k in done}
    return {"completed": done, "next": Steps[len(done)] if len(done) < len(Steps) else None, "choices": choices}


def is_complete(home: Home) -> bool:
    return load(home)["next"] is None


def check(home: Home, step: str) -> None:
    """Raise OutOfOrder unless ``step`` is the next one."""
    if step not in Steps or load(home)["next"] != step:
        raise OutOfOrder(step)


def advance(home: Home, step: str, choice: dict | None = None) -> None:
    """Mark ``step`` done, with the (non-secret) choice made there, in one atomic write."""
    check(home, step)
    data = _read(home)
    data["completed"] = list(data.get("completed", [])) + [step]
    if choice is not None:
        data["choices"] = {**dict(data.get("choices", {})), step: choice}
    home.state_dir.mkdir(parents=True, exist_ok=True)
    tmp = _path(home).with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps(data))
    os.chmod(tmp, 0o600)
    os.replace(tmp, _path(home))


def ensure_hermes_dir(home: Home) -> Path:
    """Anything that writes under <home>/hermes calls this first: the dir is private (0700), and it holds
    the Hermes seed files (see setup.seed) before setup's own settings are layered on them."""
    from sksidekick.setup import seed

    return seed.seed_files(home)
