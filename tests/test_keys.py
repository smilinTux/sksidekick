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


# fixed-cost recovery key (2026-11-25) ----------------------------------------
# age passphrase mode times the machine on every decrypt and refuses a file whose scrypt cost is more than
# 16x its estimate of one second here (and needs 1 GiB of RAM at the log_n 20 a fast machine picks), so a
# correct phrase could read as wrong on a small box, or on a busy one. The phrase is 200 random bits, so the
# key is derived at one fixed, modest cost instead and wrapped as a plain age X25519 file.

import hashlib  # noqa: E402

import pyrage  # noqa: E402
from pyrage import passphrase, x25519  # noqa: E402

PHRASE = "0123A-BCDEF-GHJKM-NPQRS-TVWXY-Z0123-45678-9ABCD"


def test_phrase_key_is_fixed_and_documented():
    # The derivation is part of the archive format (docs/ARCHIVE_FORMAT.md shows it); it must never drift.
    assert keys.KDF == {"n": 2**15, "r": 8, "p": 1}
    seed = hashlib.scrypt(PHRASE.encode(), salt=keys.KDF_SALT, n=2**15, r=8, p=1, maxmem=64 * 1024 * 1024, dklen=32)
    assert str(keys.phrase_identity(PHRASE)) == keys.bech32_secret_key(seed)
    assert str(keys.phrase_identity(PHRASE.lower().replace("-", " "))) == str(keys.phrase_identity(PHRASE))


def _bech32_payload(key: str) -> bytes:
    data = [keys._BECH32.index(c) for c in key.lower().split("1", 1)[1][:-6]]
    acc, bits, out = 0, 0, bytearray()
    for d in data:
        acc, bits = acc << 5 | d, bits + 5
        if bits >= 8:
            bits -= 8
            out.append(acc >> bits & 0xFF)
    return bytes(out)


def test_bech32_matches_age():
    # Re-encode keys age itself made: byte-for-byte the same string, checksum included.
    for _ in range(20):
        real = str(x25519.Identity.generate())
        assert keys.bech32_secret_key(_bech32_payload(real)) == real
    with pytest.raises(ValueError):
        keys.bech32_secret_key(b"short")


def test_new_wrap_is_a_plain_age_x25519_file(story_home: Home):
    keys.create_keys(story_home)
    wrapped = (story_home.keys_dir / keys.WRAPPED).read_bytes()
    assert wrapped.startswith(b"age-encryption.org/v1\n-> X25519 ")
    assert b"-> scrypt" not in wrapped


def test_wrap_unwrap_round_trip_and_wrong_phrase():
    ident = x25519.Identity.generate()
    wrapped = keys.wrap_identity(ident, PHRASE)
    assert str(keys.unwrap_identity(wrapped, PHRASE)) == str(ident)
    with pytest.raises(keys.WrongPhrase):
        keys.unwrap_identity(wrapped, PHRASE.replace("0123A", "0123B"))
    with pytest.raises(keys.WrongPhrase):
        keys.unwrap_identity(wrapped, "not a phrase")


def test_legacy_passphrase_wrap_still_opens():
    ident = x25519.Identity.generate()
    legacy = passphrase.encrypt(str(ident).encode(), PHRASE)
    assert str(keys.unwrap_identity(legacy, PHRASE)) == str(ident)
    with pytest.raises(keys.WrongPhrase):
        keys.unwrap_identity(legacy, PHRASE.replace("0123A", "0123B"))


def test_legacy_wrap_too_costly_here_is_not_a_wrong_phrase(monkeypatch):
    def refuse(data, phrase):
        raise pyrage.DecryptError("Excessive work parameter for passphrase.")

    monkeypatch.setattr(keys.passphrase, "decrypt", refuse)
    with pytest.raises(keys.KeyTooCostly) as exc:
        keys.unwrap_identity(b"age-encryption.org/v1\n-> scrypt abc 20\n", PHRASE)
    assert "phrase" in str(exc.value) and "computer" in str(exc.value)
    assert not isinstance(exc.value, keys.WrongPhrase)


def test_documented_stock_tools_script_matches(monkeypatch, capsys):
    # docs/ARCHIVE_FORMAT.md promises an archive opens without sksidekick; its script must derive our key.
    from pathlib import Path

    doc = (Path(__file__).parents[1] / "docs/ARCHIVE_FORMAT.md").read_text()
    script = doc.split("`phrase-key.py`:\n\n```python\n", 1)[1].split("```", 1)[0]
    import getpass

    monkeypatch.setattr(getpass, "getpass", lambda prompt="": PHRASE.lower() + "\n")
    exec(compile(script, "phrase-key.py", "exec"), {})
    assert capsys.readouterr().out.strip() == str(keys.phrase_identity(PHRASE))
