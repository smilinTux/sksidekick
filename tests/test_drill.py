from __future__ import annotations

import tarfile
import tempfile
from pathlib import Path

import pytest

from sksidekick import backup, drill, keys
from sksidekick.home import Home


@pytest.fixture
def backed(story_home: Home, tmp_path: Path) -> Home:
    keys.create_keys(story_home)
    story_home.new_instance()
    backup.save_config(story_home, {"destinations": [{"kind": "dir", "path": str(tmp_path / "usb")}], "keep": 7, "interval_hours": 24})
    return story_home


def test_drill_passes_on_good_backup(backed: Home):
    backup.run_backup(backed)
    res = drill.run_drill(backed)
    assert res["ok"] is True and res["files"] == len(backed.story_paths()) and res["error"] is None
    assert drill.last_drill(backed) == res


def test_drill_fails_on_damaged_backup(backed: Home):
    backup.run_backup(backed)
    latest = backup.latest(backed)
    with tarfile.open(latest) as t:
        m = t.getmember("payload.age")
        cut = m.offset_data + m.size // 2
    latest.write_bytes(latest.read_bytes()[:cut])
    res = drill.run_drill(backed)
    assert res["ok"] is False and "damaged" in res["error"]


def test_drill_without_backup(backed: Home):
    res = drill.run_drill(backed)
    assert res["ok"] is False and res["error"] == "no backup to test"


def test_drill_leaves_no_temp_dir(backed: Home, tmp_path: Path, monkeypatch):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    backup.run_backup(backed)
    assert drill.run_drill(backed)["ok"] is True
    assert list(scratch.iterdir()) == []
