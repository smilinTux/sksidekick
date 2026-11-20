from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

import pytest
from pyrage import x25519

from sksidekick import archive, keys
from sksidekick.home import Home


@pytest.fixture
def keyed(story_home: Home):
    phrase = keys.create_keys(story_home)
    story_home.new_instance()
    return story_home, phrase


def _story(home: Home) -> dict[str, bytes]:
    return {str(p): (home.root / p).read_bytes() for p in home.story_paths()}


def test_roundtrip_with_phrase(keyed, tmp_path: Path):
    home, phrase = keyed
    a = archive.export(home, tmp_path / "out.sksk")
    target = Home(tmp_path / "restored")
    manifest = archive.import_archive(a, target, phrase=phrase)
    assert _story(target) == _story(home)
    assert len(manifest["files"]) == len(home.story_paths())
    assert target.instance()["status"] == "live"
    assert target.instance()["instance_id"] != home.instance()["instance_id"]
    assert str(keys.recipient(target)) == str(keys.recipient(home))


def test_roundtrip_with_live_identity(keyed, tmp_path: Path):
    home, _ = keyed
    a = archive.export(home, tmp_path / "out.sksk")
    target = Home(tmp_path / "restored")
    archive.import_archive(a, target, identity=keys.load_identity(home))
    assert _story(target) == _story(home)


def test_manifest_has_no_file_names(keyed, tmp_path: Path):
    home, _ = keyed
    a = archive.export(home, tmp_path / "out.sksk")
    with tarfile.open(a) as t:
        assert t.getnames() == ["manifest.json", "recovery.identity.age", "payload.age"]
        outer = t.extractfile("manifest.json").read().decode()
    assert "memory/" not in outer and "journal" not in outer
    assert json.loads(outer)["format"] == archive.FORMAT


def test_wrong_phrase_writes_nothing(keyed, tmp_path: Path):
    home, _ = keyed
    a = archive.export(home, tmp_path / "out.sksk")
    target = Home(tmp_path / "restored")
    with pytest.raises(keys.WrongPhrase):
        archive.import_archive(a, target, phrase=keys.generate_phrase())
    assert target.is_empty()


def test_truncated_archive_is_damaged(keyed, tmp_path: Path):
    home, _ = keyed
    a = archive.export(home, tmp_path / "out.sksk")
    with tarfile.open(a) as t:
        m = t.getmember("payload.age")
        cut = m.offset_data + m.size // 2
    a.write_bytes(a.read_bytes()[:cut])
    with pytest.raises(archive.ArchiveDamaged):
        archive.verify(a, keys.load_identity(home))
    target = Home(tmp_path / "restored")
    with pytest.raises(archive.ArchiveDamaged):
        archive.import_archive(a, target, identity=keys.load_identity(home))
    assert target.is_empty()


def test_garbage_file_is_damaged(keyed, tmp_path: Path):
    home, _ = keyed
    p = tmp_path / "x.sksk"
    p.write_bytes(b"not an archive at all")
    with pytest.raises(archive.ArchiveDamaged):
        archive.verify(p, keys.load_identity(home))


def test_tampered_payload_is_damaged(keyed, tmp_path: Path):
    home, _ = keyed
    a = archive.export(home, tmp_path / "out.sksk")
    members = {}
    with tarfile.open(a) as t:
        for n in t.getnames():
            members[n] = t.extractfile(n).read()
    p = bytearray(members["payload.age"])
    p[len(p) // 2] ^= 0xFF
    members["payload.age"] = bytes(p)
    _write_outer(a, members)
    with pytest.raises(archive.ArchiveDamaged):
        archive.verify(a, keys.load_identity(home))


def _write_outer(path: Path, members: dict[str, bytes]) -> None:
    with tarfile.open(path, "w") as t:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))


def _evil_archive(home: Home, path: Path, add) -> Path:
    """An archive sealed with the home's real key whose payload holds a hostile member."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        add(t)
        data = json.dumps({"format": archive.FORMAT, "created": "x", "instance_id": "x", "files": {}}).encode()
        info = tarfile.TarInfo("inner-manifest.json")
        info.size = len(data)
        t.addfile(info, io.BytesIO(data))
    wrapped = (home.keys_dir / keys.WRAPPED).read_bytes()
    archive._write_envelope(path, buf.getvalue(), keys.recipient(home), wrapped)
    return path


def test_member_with_dotdot_is_refused(keyed, tmp_path: Path):
    home, _ = keyed

    def add(t):
        info = tarfile.TarInfo("story/../../evil.txt")
        info.size = 4
        t.addfile(info, io.BytesIO(b"evil"))

    a = _evil_archive(home, tmp_path / "evil.sksk", add)
    target = Home(tmp_path / "restored")
    with pytest.raises(archive.ArchiveDamaged):
        archive.import_archive(a, target, identity=keys.load_identity(home))
    assert not (tmp_path / "evil.txt").exists()
    assert target.is_empty()


def test_absolute_member_is_refused(keyed, tmp_path: Path):
    home, _ = keyed

    def add(t):
        info = tarfile.TarInfo(str(tmp_path / "abs.txt"))
        info.size = 1
        t.addfile(info, io.BytesIO(b"x"))

    a = _evil_archive(home, tmp_path / "evil.sksk", add)
    with pytest.raises(archive.ArchiveDamaged):
        archive.verify(a, keys.load_identity(home))


def test_symlink_member_is_refused(keyed, tmp_path: Path):
    home, _ = keyed

    def add(t):
        info = tarfile.TarInfo("story/link")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        t.addfile(info)

    a = _evil_archive(home, tmp_path / "evil.sksk", add)
    with pytest.raises(archive.ArchiveDamaged):
        archive.verify(a, keys.load_identity(home))


def test_archive_from_another_key_is_refused(keyed, tmp_path: Path):
    home, _ = keyed
    a = archive.export(home, tmp_path / "out.sksk")
    with pytest.raises(archive.ArchiveDamaged):
        archive.verify(a, x25519.Identity.generate())


def test_import_into_nonempty_home_refuses(keyed, tmp_path: Path):
    home, phrase = keyed
    a = archive.export(home, tmp_path / "out.sksk")
    with pytest.raises(FileExistsError):
        archive.import_archive(a, home, phrase=phrase)


def test_no_part_file_left_after_export(keyed, tmp_path: Path):
    home, _ = keyed
    archive.export(home, tmp_path / "out.sksk")
    assert sorted(p.name for p in tmp_path.iterdir() if p.is_file()) == ["out.sksk"]


def test_plain_export_matches_story(keyed, tmp_path: Path):
    home, _ = keyed
    n = archive.export_plain(home, tmp_path / "plain")
    assert n == len(home.story_paths())
    for rel in home.story_paths():
        assert (tmp_path / "plain" / rel).read_bytes() == (home.root / rel).read_bytes()


def test_plain_export_refuses_existing_dir(keyed, tmp_path: Path):
    home, _ = keyed
    (tmp_path / "plain").mkdir()
    with pytest.raises(FileExistsError):
        archive.export_plain(home, tmp_path / "plain")
