from __future__ import annotations

import os

import pytest

from sksidekick.home import Home


def test_story_paths_skip_keys_state_and_index(bare_home: Home):
    r = bare_home.root
    for rel in ("memory/long-term/a.json", "memory/index.db", "memory/index.db-wal", "keys/x", "state/instance.json", "soul/base.json"):
        p = r / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x")
    assert [str(p) for p in bare_home.story_paths()] == ["memory/long-term/a.json", "soul/base.json"]


def test_is_empty_on_fresh_dir(bare_home: Home):
    assert bare_home.is_empty()
    bare_home.root.mkdir()
    assert bare_home.is_empty()


def test_is_not_empty_with_story(story_home: Home):
    assert not story_home.is_empty()


def test_is_not_empty_with_only_keys(bare_home: Home):
    bare_home.keys_dir.mkdir(parents=True)
    (bare_home.keys_dir / "live.identity").write_text("k")
    assert not bare_home.is_empty()


def test_instance_roundtrip(story_home: Home):
    iid = story_home.new_instance()
    assert story_home.instance() == {"instance_id": iid, "status": "live"}
    story_home.set_status("moving")
    assert story_home.instance() == {"instance_id": iid, "status": "moving"}


def test_set_status_rejects_unknown(story_home: Home):
    story_home.new_instance()
    with pytest.raises(ValueError):
        story_home.set_status("paused")


def test_symlinks_are_reported(story_home: Home, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "m.json").write_text("{}")
    (story_home.memory_dir / "linked").symlink_to(elsewhere)
    assert [str(p) for p in story_home.symlinks()] == ["memory/linked"]


def test_is_not_empty_with_only_an_index(bare_home: Home):
    bare_home.memory_dir.mkdir(parents=True)
    (bare_home.memory_dir / "index.db").write_text("x")
    assert not bare_home.is_empty()


# plan 2, task 1 -------------------------------------------------------------


def _touch(home: Home, *rels: str) -> None:
    for rel in rels:
        p = home.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x")


def test_hermes_logs_tools_cache_plugins_excluded(bare_home: Home):
    _touch(
        bare_home,
        "hermes/logs/gateway.log", "hermes/cache/x.bin", "hermes/tools/node/bin/node", "hermes/plugins/p/plugin.py",
        "hermes/.skmemory/agents/sidekick/logs/x.log", "hermes/gateway.lock", "hermes/gateway.pid", "memory/a.json",
    )
    (bare_home.root / "hermes/tools/node/bin/npx").symlink_to("node")  # toolchains are full of links
    assert [str(p) for p in bare_home.story_paths()] == ["memory/a.json"]
    assert bare_home.symlinks() == []


def test_hermes_runtime_dirs_excluded(bare_home: Home):
    # A plain-install Hermes builds its dependency venvs under HERMES_HOME/installs (full of symlinks);
    # found by the move test on 2026-11-25. These are rebuilt at the new place, never moved.
    _touch(
        bare_home,
        "hermes/installs/k/environments/e/venv/pyvenv.cfg", "hermes/runtime/r.json", "hermes/audio_cache/a.ogg",
        "hermes/image_cache/i.png", "hermes/plugin-update-checks/c.json", "memory/a.json",
    )
    (bare_home.root / "hermes/installs/k/environments/e/venv/python").symlink_to("pyvenv.cfg")
    assert [str(p) for p in bare_home.story_paths()] == ["memory/a.json"]
    assert bare_home.symlinks() == []


def test_plugin_memory_store_is_story(bare_home: Home):
    _touch(bare_home, "hermes/.skmemory/agents/sidekick/memory/long-term/m1.json",
           "hermes/.skmemory/agents/sidekick/memory/index.db", "hermes/.skmemory/agents/sidekick/config/skmemory.yaml")
    assert [str(p) for p in bare_home.story_paths()] == [
        "hermes/.skmemory/agents/sidekick/config/skmemory.yaml", "hermes/.skmemory/agents/sidekick/memory/long-term/m1.json",
    ]
    assert bare_home.agent_memory_dir == bare_home.root / "hermes/.skmemory/agents/sidekick/memory"


def test_hermes_sessions_included(bare_home: Home):
    _touch(bare_home, "hermes/sessions/2026-11-20.jsonl", "hermes/state.db", "hermes/config.yaml", "hermes/.env")
    assert [str(p) for p in bare_home.story_paths()] == [
        "hermes/.env", "hermes/config.yaml", "hermes/sessions/2026-11-20.jsonl", "hermes/state.db",
    ]


def test_sqlite_shm_is_not_story(bare_home: Home):
    # SQLite's shared-memory index comes and goes while Hermes runs; the move test hit one vanishing
    # mid-export on 2026-11-25. The database and its -wal stay story.
    _touch(bare_home, "hermes/response_store.db", "hermes/response_store.db-wal", "hermes/response_store.db-shm")
    assert [str(p) for p in bare_home.story_paths()] == ["hermes/response_store.db", "hermes/response_store.db-wal"]


def test_tmp_files_are_not_story(bare_home: Home):
    # a failed atomic write leaves <name>.tmp; hermes/.env.tmp would carry credentials past the .env redactor
    _touch(bare_home, "hermes/.env", "hermes/.env.tmp", "hermes/config.yaml.tmp", "soul/personality.json.TMP")
    assert [str(p) for p in bare_home.story_paths()] == ["hermes/.env"]


def test_failed_writes_leave_no_tmp(bare_home: Home, monkeypatch):
    import pytest

    from sksidekick.setup import hermes_config, state

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    bare_home.root.mkdir(parents=True)
    for write in (lambda: hermes_config.set_envs(bare_home, {"TELEGRAM_BOT_TOKEN": "1:secret"}),
                  lambda: hermes_config.write_private(bare_home.root / "x.json", b"{}"),
                  lambda: state.advance(bare_home, "tier"),
                  bare_home.new_instance):
        with pytest.raises(OSError):
            write()
    left = [p for p in bare_home.root.rglob("*") if p.name.endswith(".tmp")]
    assert left == []
