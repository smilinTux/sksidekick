from __future__ import annotations

import json
from pathlib import Path

import pytest

from sksidekick.home import Home


def write_story(home: Home) -> None:
    for layer in ("short-term", "mid-term", "long-term"):
        d = home.memory_dir / layer
        d.mkdir(parents=True, exist_ok=True)
        for i in range(2):
            (d / f"{layer}-{i}.json").write_text(json.dumps({"layer": layer, "n": i, "text": "a memory"}))
    home.soul_dir.mkdir(parents=True, exist_ok=True)
    (home.soul_dir / "base.json").write_text(json.dumps({"name": "Sidekick", "type": "companion"}))
    home.journal_path.write_text("# Journal\n\nFirst day.\n")


@pytest.fixture
def bare_home(tmp_path: Path) -> Home:
    return Home(tmp_path / "sidekick")


@pytest.fixture
def story_home(bare_home: Home) -> Home:
    bare_home.root.mkdir(parents=True)
    write_story(bare_home)
    return bare_home


@pytest.fixture(autouse=True)
def _no_host_hermes(monkeypatch):
    """A developer's own HERMES_HOME must never leak into a test."""
    monkeypatch.delenv("HERMES_HOME", raising=False)
