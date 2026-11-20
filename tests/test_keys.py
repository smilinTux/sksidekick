from __future__ import annotations

import re
import stat

import pytest

from sksidekick import keys
from sksidekick.home import Home

PHRASE_RE = re.compile(r"^([0-9A-HJKMNP-TV-Z]{5}-){7}[0-9A-HJKMNP-TV-Z]{5}$")


def test_phrase_shape():
    for _ in range(20):
        assert PHRASE_RE.match(keys.generate_phrase())


def test_phrases_differ():
    assert len({keys.generate_phrase() for _ in range(50)}) == 50


def test_normalize_accepts_lowercase_spaces_and_lookalikes():
    p = keys.generate_phrase()
    messy = " ".join(p.lower().replace("-", " ").split()).replace("0", "o").replace("1", "l")
    assert keys.normalize_phrase(messy) == p


@pytest.mark.parametrize("bad", ["", "ABCDE", "U" * 40, "!" * 40])
def test_normalize_rejects_bad(bad):
    with pytest.raises(ValueError):
        keys.normalize_phrase(bad)


def test_create_keys_writes_four_files_and_no_phrase(story_home: Home):
    phrase = keys.create_keys(story_home)
    assert sorted(p.name for p in story_home.keys_dir.iterdir()) == ["live.identity", "recipient.txt", "recovery.identity.age", "recovery.mac"]
    compact = phrase.replace("-", "")
    for f in story_home.root.rglob("*"):
        if f.is_file():
            data = f.read_bytes()
            assert phrase.encode() not in data and compact.encode() not in data


def test_live_identity_mode_0600(story_home: Home):
    keys.create_keys(story_home)
    mode = stat.S_IMODE((story_home.keys_dir / "live.identity").stat().st_mode)
    assert mode == 0o600


def test_create_keys_twice_refuses(story_home: Home):
    keys.create_keys(story_home)
    with pytest.raises(FileExistsError):
        keys.create_keys(story_home)


def test_unwrap_with_phrase_matches_recipient(story_home: Home):
    phrase = keys.create_keys(story_home)
    wrapped = (story_home.keys_dir / "recovery.identity.age").read_bytes()
    ident = keys.unwrap_identity(wrapped, phrase.lower())
    assert str(ident.to_public()) == str(keys.recipient(story_home))
    assert str(keys.load_identity(story_home)) == str(ident)


def test_unwrap_wrong_phrase_raises_wrong_phrase(story_home: Home):
    keys.create_keys(story_home)
    wrapped = (story_home.keys_dir / "recovery.identity.age").read_bytes()
    with pytest.raises(keys.WrongPhrase):
        keys.unwrap_identity(wrapped, keys.generate_phrase())


def test_shred_removes_all_key_files(story_home: Home):
    keys.create_keys(story_home)
    destroyed = keys.shred_keys(story_home)
    assert sorted(destroyed) == ["live.identity", "recipient.txt", "recovery.identity.age", "recovery.mac"]
    assert not any(story_home.keys_dir.iterdir())
