from __future__ import annotations

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
