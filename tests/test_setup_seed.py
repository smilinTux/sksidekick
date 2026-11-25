"""Setup layers on the Hermes seed, as init does; seeding waits for the first step so init/import still work."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sksidekick.cli import main
from sksidekick.home import Home
from sksidekick.setup import hermes_config, seed, server, soul, state
from test_setup_phrase import Setup


@pytest.fixture
def seed_dir(tmp_path: Path, monkeypatch) -> Path:
    d = tmp_path / "seed"
    d.mkdir()
    (d / "config.yaml").write_text(json.dumps({"terminal.timeout": "177"}))  # the fake hermes stores JSON
    (d / ".env").write_text("SEEDED=1\n")
    (d / "SOUL.md").write_text("# seed soul\n")
    sk = tmp_path / "skmemory.json"
    sk.write_text("{}")
    monkeypatch.setenv(seed.SEED_ENV, str(d))
    monkeypatch.setenv(seed.SKMEMORY_JSON_ENV, str(sk))
    return d


def test_first_write_seeds_then_layers(bare_home: Home, seed_dir, fake_hermes):
    hermes_config.set_model(bare_home, base_url="http://127.0.0.1:1/v1", model="stub", api_key=None)
    cfg = json.loads((bare_home.hermes_dir / "config.yaml").read_text())
    assert cfg["terminal.timeout"] == "177" and cfg["model.default"] == "stub"
    hermes_config.set_envs(bare_home, {"TELEGRAM_BOT_TOKEN": "1:a"})
    env = (bare_home.hermes_dir / ".env").read_text().splitlines()
    assert "SEEDED=1" in env and "TELEGRAM_BOT_TOKEN=1:a" in env
    soul.write_soul(bare_home, "mentor", "female")
    assert "Mentor" in (bare_home.hermes_dir / "SOUL.md").read_text()  # setup's soul replaces the seed's
    assert (bare_home.hermes_dir.stat().st_mode & 0o777) == 0o700


def test_seed_never_replaces_a_file(bare_home: Home, seed_dir):
    bare_home.hermes_dir.mkdir(parents=True)
    (bare_home.hermes_dir / ".env").write_text("MINE=1\n")
    state.ensure_hermes_dir(bare_home)
    assert (bare_home.hermes_dir / ".env").read_text() == "MINE=1\n"
    assert (bare_home.hermes_dir / "config.yaml").exists()


def test_no_seed_env_copies_nothing(bare_home: Home, monkeypatch):
    monkeypatch.delenv(seed.SEED_ENV, raising=False)
    d = state.ensure_hermes_dir(bare_home)
    assert list(d.iterdir()) == []


def test_starting_setup_seeds_nothing_so_init_still_works(bare_home: Home, seed_dir, monkeypatch, capsys):
    monkeypatch.setattr(server, "WATCH_INTERVAL", 0.05)
    s = Setup(bare_home, monkeypatch, capsys)
    assert not bare_home.hermes_dir.exists()
    assert main(["--home", str(bare_home.root), "init"]) == 0
    s.t.join(5)
    assert s.rc["rc"] == 0


def test_seed_all_adds_skmemory_and_provider(bare_home: Home, seed_dir, fake_hermes):
    assert seed.main([str(bare_home.root)]) == 0
    d = bare_home.hermes_dir
    assert (d / "skmemory.json").read_text() == "{}"
    assert fake_hermes.get("memory.provider") == "skmemory"
    assert json.loads((d / "config.yaml").read_text())["terminal.timeout"] == "177"
    n = len(fake_hermes.calls())
    seed.main([str(bare_home.root)])  # idempotent: provider already set, nothing rewritten
    assert [c["argv"][:2] for c in fake_hermes.calls()[n:]] == [["config", "get"]]
