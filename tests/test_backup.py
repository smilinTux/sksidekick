from __future__ import annotations

import datetime as dt
import os
import subprocess
from pathlib import Path

import pytest

from sksidekick import archive, backup, keys
from sksidekick.home import Home

T0 = dt.datetime(2026, 11, 20, 12, 0, tzinfo=dt.timezone.utc)


@pytest.fixture
def keyed(story_home: Home) -> Home:
    keys.create_keys(story_home)
    story_home.new_instance()
    return story_home


def _dest(home: Home, path: Path, keep: int = 7) -> None:
    backup.save_config(home, {"destinations": [{"kind": "dir", "path": str(path)}], "keep": keep, "interval_hours": 24})


def test_defaults_when_no_config(keyed: Home):
    assert backup.load_config(keyed) == {"destinations": [], "keep": 7, "interval_hours": 24}


def test_backup_to_dir_writes_verified_archive(keyed: Home, tmp_path: Path):
    _dest(keyed, tmp_path / "usb")
    res = backup.run_backup(keyed, now=T0)
    assert res[0]["ok"] is True
    files = sorted((tmp_path / "usb").iterdir())
    iid = keyed.instance()["instance_id"]
    assert [f.name for f in files] == [f"sidekick-{iid}-20261120T120000Z.sksk"]
    archive.verify(files[0], keys.load_identity(keyed))
    assert backup.status(keyed, now=T0)["stale"] is False


def test_rotation_keeps_n(keyed: Home, tmp_path: Path):
    _dest(keyed, tmp_path / "usb", keep=3)
    for i in range(5):
        backup.run_backup(keyed, now=T0 + dt.timedelta(days=i))
    names = sorted(f.name for f in (tmp_path / "usb").iterdir())
    assert len(names) == 3
    assert names[-1].endswith("20261124T120000Z.sksk") and names[0].endswith("20261122T120000Z.sksk")


def test_rotation_ignores_other_files(keyed: Home, tmp_path: Path):
    _dest(keyed, tmp_path / "usb", keep=1)
    (tmp_path / "usb").mkdir()
    (tmp_path / "usb" / "holiday.jpg").write_bytes(b"x")
    backup.run_backup(keyed, now=T0)
    backup.run_backup(keyed, now=T0 + dt.timedelta(days=1))
    assert (tmp_path / "usb" / "holiday.jpg").exists()


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_unreachable_destination_records_error_and_keeps_old(keyed: Home, tmp_path: Path):
    usb = tmp_path / "usb"
    _dest(keyed, usb, keep=2)
    backup.run_backup(keyed, now=T0)
    backup.run_backup(keyed, now=T0 + dt.timedelta(days=1))
    os.chmod(usb, 0o500)
    try:
        res = backup.run_backup(keyed, now=T0 + dt.timedelta(days=2))
        assert res[0]["ok"] is False and res[0]["error"]
        assert len(list(usb.iterdir())) == 2
        st = backup.status(keyed, now=T0 + dt.timedelta(days=2))
        assert st["last_error"]
        assert st["last_success"].startswith("2026-11-21")
    finally:
        os.chmod(usb, 0o700)


def test_status_stale_after_two_intervals(keyed: Home, tmp_path: Path):
    _dest(keyed, tmp_path / "usb")
    backup.run_backup(keyed, now=T0)
    assert backup.status(keyed, now=T0 + dt.timedelta(hours=47))["stale"] is False
    assert backup.status(keyed, now=T0 + dt.timedelta(hours=49))["stale"] is True


def test_status_stale_when_never_run(keyed: Home):
    assert backup.status(keyed, now=T0) == {"last_success": None, "stale": True, "last_error": None}


def test_no_destinations_is_an_error_not_silent(keyed: Home):
    res = backup.run_backup(keyed, now=T0)
    assert res == [{"destination": None, "ok": False, "error": "no backup destination configured"}]
    st = backup.status(keyed, now=T0)
    assert st["stale"] is True and st["last_error"] == "no backup destination configured"


def test_rclone_destination_uses_copyto(keyed: Home, monkeypatch):
    calls = []

    def fake_run(argv, **kw):
        calls.append(argv)
        out = ""
        if argv[1] == "lsf":
            out = calls[0][3].rsplit("/", 1)[-1] + "\n"
        return subprocess.CompletedProcess(argv, 0, stdout=out, stderr="")

    monkeypatch.setattr(backup.subprocess, "run", fake_run)
    backup.save_config(keyed, {"destinations": [{"kind": "rclone", "remote": "hub:sidekick/backups"}], "keep": 7, "interval_hours": 24})
    res = backup.run_backup(keyed, now=T0)
    assert res[0]["ok"] is True
    assert calls[0][:2] == ["rclone", "copyto"]
    assert calls[0][3] == f"hub:sidekick/backups/sidekick-{keyed.instance()['instance_id']}-20261120T120000Z.sksk"
    assert calls[1][:2] == ["rclone", "lsf"]


def test_latest_returns_newest(keyed: Home, tmp_path: Path):
    _dest(keyed, tmp_path / "usb")
    assert backup.latest(keyed) is None
    backup.run_backup(keyed, now=T0)
    backup.run_backup(keyed, now=T0 + dt.timedelta(days=1))
    assert backup.latest(keyed).name.endswith("20261121T120000Z.sksk")
