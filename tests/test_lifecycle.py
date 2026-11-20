from __future__ import annotations

from pathlib import Path

import pytest
from pyrage import x25519

from sksidekick import archive, backup, keys, lifecycle
from sksidekick.home import Home


@pytest.fixture
def keyed(story_home: Home):
    phrase = keys.create_keys(story_home)
    story_home.new_instance()
    backup.save_config(story_home, {"destinations": [], "keep": 7, "interval_hours": 24})
    return story_home, phrase


def test_plan_reset_keep_settings(keyed):
    home, _ = keyed
    keep = lifecycle.plan_reset(home, keep_settings=True)
    assert all(p.startswith("memory/") or p == "journal.md" for p in keep)
    full = lifecycle.plan_reset(home, keep_settings=False)
    assert "soul/base.json" in full and "config/backup.json" in full
    assert not any(p.startswith(("keys/", "state/")) for p in full)


def test_reset_exports_first_then_wipes_memory(keyed, tmp_path: Path):
    home, phrase = keyed
    before = {str(p): (home.root / p).read_bytes() for p in home.story_paths()}
    res = lifecycle.reset(home, keep_settings=True, export_to=tmp_path / "before-reset.sksk")
    assert res["export"] == tmp_path / "before-reset.sksk"
    assert not any(str(p).startswith("memory/") for p in home.story_paths())
    assert (home.soul_dir / "base.json").exists()
    assert home.instance()["status"] == "live"
    restored = Home(tmp_path / "restored")
    archive.import_archive(res["export"], restored, phrase=phrase)
    assert {str(p): (restored.root / p).read_bytes() for p in restored.story_paths()} == before


def test_delete_shreds_keys_before_files(keyed, monkeypatch):
    home, _ = keyed

    def boom(*a, **k):
        raise OSError("disk went away")

    monkeypatch.setattr(lifecycle.shutil, "rmtree", boom)
    with pytest.raises(OSError):
        lifecycle.delete(home)
    assert not any(home.keys_dir.iterdir())


def test_delete_removes_everything_and_reports(keyed):
    home, _ = keyed
    res = lifecycle.delete(home)
    assert sorted(res["keys_destroyed"]) == ["live.identity", "recipient.txt", "recovery.identity.age"]
    assert res["files_removed"] > 0
    assert not home.root.exists()


def test_delete_notice_text_exact(keyed):
    home, _ = keyed
    assert lifecycle.delete(home)["notice"] == lifecycle.NOTICE
    assert "SSD" in lifecycle.NOTICE and "recovery phrase" in lifecycle.NOTICE


def test_backup_unreadable_after_delete(keyed, tmp_path: Path):
    home, _ = keyed
    a = archive.export(home, tmp_path / "backup.sksk")
    live = home.keys_dir / keys.LIVE
    lifecycle.delete(home)
    assert not live.exists()
    with pytest.raises(archive.ArchiveDamaged):
        archive.verify(a, x25519.Identity.generate())


def test_move_out_sets_moving_and_require_live_refuses(keyed, tmp_path: Path):
    home, phrase = keyed
    lifecycle.require_live(home)
    out = lifecycle.move_out(home, tmp_path / "move.sksk")
    assert out.exists() and home.instance()["status"] == "moving"
    with pytest.raises(RuntimeError, match="not the live sidekick"):
        lifecycle.require_live(home)
    new = Home(tmp_path / "new-place")
    archive.import_archive(out, new, phrase=phrase)
    lifecycle.require_live(new)


def test_retire_requires_moving(keyed, tmp_path: Path):
    home, _ = keyed
    with pytest.raises(RuntimeError):
        lifecycle.retire(home)
    lifecycle.move_out(home, tmp_path / "move.sksk")
    assert lifecycle.retire(home)["keys_destroyed"]
    assert not home.root.exists()


# review round 1 -------------------------------------------------------------


def test_reset_removes_the_search_index(keyed):
    home, _ = keyed
    (home.memory_dir / "index.db").write_text("fts of every memory")
    (home.memory_dir / "index.db-wal").write_text("x")
    lifecycle.reset(home, keep_settings=False, export_to=None)
    assert not list(home.memory_dir.glob("index.db*")) if home.memory_dir.exists() else True


def test_delete_through_a_symlinked_home(keyed, tmp_path: Path):
    home, _ = keyed
    link = tmp_path / "link-home"
    link.symlink_to(home.root)
    res = lifecycle.delete(Home(link))
    assert res["keys_destroyed"]
    assert not home.root.exists() and not link.exists() and not link.is_symlink()


def test_move_out_failure_keeps_the_copy_live(keyed, tmp_path: Path, monkeypatch):
    home, _ = keyed

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(lifecycle.archive, "export", boom)
    with pytest.raises(OSError):
        lifecycle.move_out(home, tmp_path / "move.sksk")
    assert home.instance()["status"] == "live"


def test_move_out_marks_moving_before_the_archive_exists(keyed, tmp_path: Path, monkeypatch):
    home, _ = keyed
    seen = []
    real = lifecycle.archive.export

    def spy(h, dest, **kw):
        seen.append(h.instance()["status"])
        return real(h, dest, **kw)

    monkeypatch.setattr(lifecycle.archive, "export", spy)
    lifecycle.move_out(home, tmp_path / "move.sksk")
    assert seen == ["moving"]
