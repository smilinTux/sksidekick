from __future__ import annotations

import io
import json
import stat
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
    archive._write_envelope(path, buf.getvalue(), keys.recipient(home), wrapped, identity=keys.load_identity(home))
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


# review round 1 -------------------------------------------------------------


def test_export_refuses_symlinks(keyed, tmp_path: Path):
    home, _ = keyed
    other = tmp_path / "other"
    other.mkdir()
    (other / "x.json").write_text("{}")
    (home.memory_dir / "linked").symlink_to(other)
    with pytest.raises(archive.StoryError, match="symlink"):
        archive.export(home, tmp_path / "out.sksk")
    assert not (tmp_path / "out.sksk").exists()


def test_forged_archive_with_real_wrapped_key_is_refused(keyed, tmp_path: Path):
    """A backup host knows the public recipient and holds a real wrapped key: it still cannot forge."""
    home, phrase = keyed
    real = archive.export(home, tmp_path / "real.sksk")
    with tarfile.open(real) as t:
        wrapped = t.extractfile("recovery.identity.age").read()
        manifest = json.loads(t.extractfile("manifest.json").read())
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        data = b"forged memory"
        files = {"memory/long-term/evil.json": __import__("hashlib").sha256(data).hexdigest()}
        info = tarfile.TarInfo("story/memory/long-term/evil.json")
        info.size = len(data)
        t.addfile(info, io.BytesIO(data))
        inner = json.dumps({"format": archive.FORMAT, "created": "x", "instance_id": "x", "files": files}).encode()
        info = tarfile.TarInfo("inner-manifest.json")
        info.size = len(inner)
        t.addfile(info, io.BytesIO(inner))
    import pyrage, hashlib

    payload = pyrage.encrypt(buf.getvalue(), [x25519.Recipient.from_str(manifest["recipient"])])
    manifest["payload_sha256"] = hashlib.sha256(payload).hexdigest()
    forged = tmp_path / "forged.sksk"
    _write_outer(forged, {"manifest.json": json.dumps(manifest).encode(), "recovery.identity.age": wrapped, "payload.age": payload})
    with pytest.raises(archive.ArchiveDamaged, match="not made by this sidekick"):
        archive.verify(forged, keys.load_identity(home))
    target = Home(tmp_path / "restored")
    with pytest.raises(archive.ArchiveDamaged):
        archive.import_archive(forged, target, phrase=phrase)
    assert not target.root.exists()


@pytest.mark.parametrize("name", ["story/keys/live.identity", "story/state/instance.json", "story/memory/index.db"])
def test_member_into_keys_state_or_index_refused(keyed, tmp_path: Path, name):
    home, _ = keyed

    def add(t):
        info = tarfile.TarInfo(name)
        info.size = 1
        t.addfile(info, io.BytesIO(b"x"))

    a = _evil_archive(home, tmp_path / "evil.sksk", add)
    target = Home(tmp_path / "restored")
    with pytest.raises(archive.ArchiveDamaged):
        archive.import_archive(a, target, identity=keys.load_identity(home))
    assert not target.root.exists()


def test_case_insensitive_duplicate_refused(keyed, tmp_path: Path):
    home, _ = keyed

    def add(t):
        for n in ("story/memory/a.json", "story/Memory/a.json"):
            info = tarfile.TarInfo(n)
            info.size = 1
            t.addfile(info, io.BytesIO(b"x"))

    a = _evil_archive(home, tmp_path / "evil.sksk", add)
    with pytest.raises(archive.ArchiveDamaged, match="duplicate"):
        archive.verify(a, keys.load_identity(home))


def test_oversized_story_refused(keyed, tmp_path: Path, monkeypatch):
    home, _ = keyed
    a = archive.export(home, tmp_path / "out.sksk")
    monkeypatch.setattr(archive, "MAX_STORY_BYTES", 10)
    with pytest.raises(archive.ArchiveDamaged, match="too large"):
        archive.verify(a, keys.load_identity(home))


def test_manifest_not_an_object_is_damaged(keyed, tmp_path: Path):
    home, _ = keyed
    a = archive.export(home, tmp_path / "out.sksk")
    with tarfile.open(a) as t:
        members = {n: t.extractfile(n).read() for n in t.getnames()}
    members["manifest.json"] = b"[1, 2]"
    _write_outer(a, members)
    with pytest.raises(archive.ArchiveDamaged):
        archive.verify(a, keys.load_identity(home))


def test_import_into_home_holding_only_an_index_refuses(keyed, tmp_path: Path):
    home, phrase = keyed
    a = archive.export(home, tmp_path / "out.sksk")
    target = Home(tmp_path / "restored")
    target.memory_dir.mkdir(parents=True)
    (target.memory_dir / "index.db").write_text("x")
    with pytest.raises(FileExistsError):
        archive.import_archive(a, target, phrase=phrase)


def test_import_into_existing_empty_dir_works(keyed, tmp_path: Path):
    home, phrase = keyed
    a = archive.export(home, tmp_path / "out.sksk")
    target = Home(tmp_path / "restored")
    (target.root / "memory").mkdir(parents=True)
    archive.import_archive(a, target, phrase=phrase)
    assert _story(target) == _story(home)


def test_restored_files_are_private(keyed, tmp_path: Path):
    home, phrase = keyed
    a = archive.export(home, tmp_path / "out.sksk")
    target = Home(tmp_path / "restored")
    archive.import_archive(a, target, phrase=phrase)
    assert stat.S_IMODE(target.root.stat().st_mode) == 0o700
    for rel in target.story_paths():
        assert stat.S_IMODE((target.root / rel).stat().st_mode) == 0o600


def test_plain_export_is_private(keyed, tmp_path: Path):
    home, _ = keyed
    archive.export_plain(home, tmp_path / "plain")
    assert stat.S_IMODE((tmp_path / "plain").stat().st_mode) == 0o700
    for p in (tmp_path / "plain").rglob("*"):
        if p.is_file():
            assert stat.S_IMODE(p.stat().st_mode) == 0o600


def test_export_refuses_existing_dest(keyed, tmp_path: Path):
    home, _ = keyed
    (tmp_path / "out.sksk").write_text("someone else's file")
    with pytest.raises(FileExistsError):
        archive.export(home, tmp_path / "out.sksk")
    assert (tmp_path / "out.sksk").read_text() == "someone else's file"


def test_purpose_is_recorded(keyed, tmp_path: Path):
    home, phrase = keyed
    a = archive.export(home, tmp_path / "out.sksk", purpose="backup")
    m = archive.import_archive(a, Home(tmp_path / "r"), phrase=phrase)
    assert m["purpose"] == "backup"


# review round 2 -------------------------------------------------------------


def _flip_member(a: Path, member: str) -> None:
    with tarfile.open(a) as t:
        members = {n: t.extractfile(n).read() for n in t.getnames()}
    b = bytearray(members[member])
    b[len(b) // 2] ^= 0x01
    members[member] = bytes(b)
    _write_outer(a, members)


def test_corrupted_recovery_key_in_archive_fails_verify(keyed, tmp_path: Path):
    home, _ = keyed
    a = archive.export(home, tmp_path / "out.sksk")
    _flip_member(a, "recovery.identity.age")
    with pytest.raises(archive.ArchiveDamaged, match="recovery key"):
        archive.verify(a, keys.load_identity(home))


def test_local_recovery_key_corruption_is_caught_before_export(keyed, tmp_path: Path):
    home, _ = keyed
    p = home.keys_dir / keys.WRAPPED
    b = bytearray(p.read_bytes())
    b[len(b) // 2] ^= 0x01
    p.write_bytes(bytes(b))
    with pytest.raises(keys.KeyDamaged):
        archive.export(home, tmp_path / "out.sksk")


@pytest.mark.parametrize("mac", ["é", 5, None, "ab"])
def test_hostile_mac_values_are_refused_cleanly(keyed, tmp_path: Path, mac):
    home, _ = keyed
    a = archive.export(home, tmp_path / "out.sksk")
    with tarfile.open(a) as t:
        members = {n: t.extractfile(n).read() for n in t.getnames()}
    m = json.loads(members["manifest.json"])
    m["mac"] = mac
    members["manifest.json"] = json.dumps(m).encode()
    _write_outer(a, members)
    with pytest.raises(archive.ArchiveDamaged):
        archive.verify(a, keys.load_identity(home))


def test_import_into_a_symlinked_empty_home(keyed, tmp_path: Path):
    home, phrase = keyed
    a = archive.export(home, tmp_path / "out.sksk")
    real = tmp_path / "real-home"
    real.mkdir()
    link = tmp_path / "home-link"
    link.symlink_to(real)
    archive.import_archive(a, Home(link), phrase=phrase)
    assert _story(Home(real)) == _story(home)


@pytest.mark.skipif(__import__("os").geteuid() == 0, reason="root ignores permissions")
def test_import_into_existing_home_under_a_read_only_parent(keyed, tmp_path: Path):
    import os

    home, phrase = keyed
    a = archive.export(home, tmp_path / "out.sksk")
    parent = tmp_path / "ro"
    (parent / "home").mkdir(parents=True)
    os.chmod(parent, 0o500)
    try:
        archive.import_archive(a, Home(parent / "home"), phrase=phrase)
        assert _story(Home(parent / "home")) == _story(home)
    finally:
        os.chmod(parent, 0o700)


def test_leftover_import_staging_does_not_block_a_retry(keyed, tmp_path: Path):
    home, phrase = keyed
    a = archive.export(home, tmp_path / "out.sksk")
    target = Home(tmp_path / "restored")
    stale = target.root / ".sksidekick-import-dead"
    stale.mkdir(parents=True)
    (stale / "half.json").write_text("{}")
    assert target.is_empty()
    archive.import_archive(a, target, phrase=phrase)
    assert not stale.exists()
    assert _story(target) == _story(home)


def test_unicode_normalization_duplicate_refused(keyed, tmp_path: Path):
    home, _ = keyed

    def add(t):
        for n in ("story/memory/café.json", "story/memory/café.json"):
            info = tarfile.TarInfo(n)
            info.size = 1
            t.addfile(info, io.BytesIO(b"x"))

    a = _evil_archive(home, tmp_path / "evil.sksk", add)
    with pytest.raises(archive.ArchiveDamaged, match="duplicate"):
        archive.verify(a, keys.load_identity(home))
