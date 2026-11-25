from __future__ import annotations

import json
import os
import sys
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


class FakeHermes:
    """A stand-in `hermes` on PATH: logs each call as a JSON line, stores `config set` values."""

    def __init__(self, bin_dir: Path) -> None:
        self.bin_dir = bin_dir
        self.log = bin_dir / "hermes-calls.jsonl"
        self.store = bin_dir / "hermes-config.json"

    def calls(self) -> list[dict]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines() if line]

    def config_file(self) -> Path | None:
        """The config.yaml (JSON content) of the HERMES_HOME the last call used."""
        calls = self.calls()
        return Path(calls[-1]["hermes_home"]) / "config.yaml" if calls else None

    def get(self, key: str) -> str | None:
        f = self.config_file()
        return json.loads(f.read_text()).get(key) if f and f.exists() else None


_FAKE_HERMES = """#!{python}
import json, os, sys
log, store = {log!r}, {store!r}
argv = sys.argv[1:]
with open(log, "a") as f:
    f.write(json.dumps({{"argv": argv, "hermes_home": os.environ.get("HERMES_HOME")}}) + "\\n")
cfg = os.path.join(os.environ["HERMES_HOME"], "config.yaml")
def load():
    try:
        return json.load(open(cfg))
    except FileNotFoundError:
        return {{}}
if argv[:2] == ["config", "set"] and len(argv) == 4:
    n = int(open(store + ".n").read() or 0) + 1 if os.path.exists(store + ".n") else 1
    open(store + ".n", "w").write(str(n))
    if str(n) == os.environ.get("FAKE_HERMES_FAIL_SET"):
        sys.stderr.write("sk-secret refused\\n")
        sys.exit(1)
    data = load()
    data[argv[2]] = argv[3]
    json.dump(data, open(cfg, "w"))
elif argv[:2] == ["config", "get"] and len(argv) == 3:
    data = load()
    if argv[2] not in data:
        sys.stderr.write("Config key not set: " + argv[2] + "\\n")
        sys.exit(1)
    print(data[argv[2]])
else:
    sys.exit(2)
"""


@pytest.fixture
def fake_hermes(tmp_path: Path, monkeypatch) -> FakeHermes:
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    fh = FakeHermes(bin_dir)
    script = bin_dir / "hermes"
    script.write_text(_FAKE_HERMES.format(python=sys.executable, log=str(fh.log), store=str(fh.store)))
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    return fh
