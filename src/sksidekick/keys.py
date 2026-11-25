"""The sidekick's key and the recovery phrase that is the only way back to it.

The running sidekick keeps its identity in ``keys/live.identity`` (it must hold
the key while it runs). ``keys/recovery.identity.age`` is the same identity
wrapped with the recovery phrase, which travels inside every archive. The
phrase itself is never stored.

The wrap is a plain age X25519 file, encrypted to a key derived from the phrase
with scrypt at one FIXED cost (``KDF``). Not age's passphrase mode: that times
the machine on every decrypt and refuses a file whose cost is over 16x its
estimate of one second there (and needs 1 GiB of memory at the cost a fast
computer picks), so a correct phrase could fail on a small or busy computer.
The phrase is 200 random bits, so a fixed salt and a low cost are enough.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import shutil

import pyrage
from pyrage import passphrase, x25519

from sksidekick.home import Home

ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford base32, no I L O U
GROUPS, GROUP_LEN = 8, 5
LOOKALIKES = {"O": "0", "I": "1", "L": "1"}

LIVE = "live.identity"
WRAPPED = "recovery.identity.age"
RECIPIENT = "recipient.txt"
WRAPPED_MAC = "recovery.mac"


class WrongPhrase(ValueError):
    """The recovery phrase does not open this key."""


class KeyTooCostly(ValueError):
    """An older (age passphrase) recovery key that age will not open on this computer, phrase unchecked."""


class KeyDamaged(ValueError):
    """The phrase-wrapped recovery key on this device no longer matches the key it was made from."""


def generate_phrase() -> str:
    chars = "".join(secrets.choice(ALPHABET) for _ in range(GROUPS * GROUP_LEN))
    return _group(chars)


def _group(chars: str) -> str:
    return "-".join(chars[i : i + GROUP_LEN] for i in range(0, len(chars), GROUP_LEN))


def normalize_phrase(phrase: str) -> str:
    chars = "".join(c for c in phrase.upper() if c not in " -\t\n")
    chars = "".join(LOOKALIKES.get(c, c) for c in chars)
    if len(chars) != GROUPS * GROUP_LEN or any(c not in ALPHABET for c in chars):
        raise ValueError("a recovery phrase is 8 groups of 5 letters and digits")
    return _group(chars)


def full_fsync(fd: int) -> None:
    """fsync that really reaches the disk on macOS too (F_FULLFSYNC)."""
    import fcntl

    if hasattr(fcntl, "F_FULLFSYNC"):
        try:
            fcntl.fcntl(fd, fcntl.F_FULLFSYNC)
            return
        except OSError:
            pass
    os.fsync(fd)


def _write_private(path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
        f.flush()
        full_fsync(f.fileno())


def install_identity(home: Home, identity: x25519.Identity, wrapped: bytes) -> None:
    """Write a key set into an empty ``keys/`` (used by create and by import)."""
    home.keys_dir.mkdir(parents=True, exist_ok=True)
    if any(home.keys_dir.iterdir()):
        raise FileExistsError(f"{home.keys_dir} already holds keys")
    os.chmod(home.keys_dir, 0o700)
    _write_private(home.keys_dir / LIVE, str(identity).encode())
    _write_private(home.keys_dir / WRAPPED, wrapped)
    _write_private(home.keys_dir / RECIPIENT, str(identity.to_public()).encode())
    _write_private(home.keys_dir / WRAPPED_MAC, wrapped_mac(identity, wrapped).encode())


def wrapped_mac(identity: x25519.Identity, wrapped: bytes) -> str:
    """HMAC of the wrapped key under the live key, so damage to it is caught before it is ever needed."""
    k = hashlib.sha256(b"sksidekick wrapped key mac\0" + str(identity).encode()).digest()
    return hmac.new(k, wrapped, hashlib.sha256).hexdigest()


def checked_wrapped(home: Home) -> bytes:
    """The wrapped recovery key, after proving it is the one made with this sidekick's key."""
    wrapped = (home.keys_dir / WRAPPED).read_bytes()
    try:
        stored = (home.keys_dir / WRAPPED_MAC).read_text().strip()
    except FileNotFoundError as exc:
        raise KeyDamaged("the recovery key check file is missing; run sksidekick verify on a recent archive") from exc
    if not hmac.compare_digest(stored.encode(), wrapped_mac(load_identity(home), wrapped).encode()):
        raise KeyDamaged(
            "the recovery key on this device is damaged, so the phrase could not restore new archives. "
            "Restore keys/ from a known good archive before making more backups."
        )
    return wrapped


def create_keys(home: Home) -> str:
    """Create the sidekick's key; return the recovery phrase (shown once, never stored)."""
    if home.keys_dir.is_dir() and any(home.keys_dir.iterdir()):
        raise FileExistsError(f"{home.keys_dir} already holds keys")
    phrase = generate_phrase()
    identity = x25519.Identity.generate()
    wrapped = wrap_identity(identity, phrase)
    install_identity(home, identity, wrapped)
    return phrase


def load_identity(home: Home) -> x25519.Identity:
    return x25519.Identity.from_str((home.keys_dir / LIVE).read_text().strip())


def recipient(home: Home) -> x25519.Recipient:
    return x25519.Recipient.from_str((home.keys_dir / RECIPIENT).read_text().strip())


# Part of the archive format (docs/ARCHIVE_FORMAT.md): changing any of these strands every archive made before.
KDF = {"n": 2**15, "r": 8, "p": 1}  # 32 MiB and well under a second, the same on every computer
KDF_SALT = b"sksidekick recovery key v2"
_BECH32 = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"


def _bech32_polymod(values) -> int:
    gen = (0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3)
    chk = 1
    for v in values:
        top = chk >> 25
        chk = (chk & 0x1FFFFFF) << 5 ^ v
        for i in range(5):
            chk ^= gen[i] if (top >> i) & 1 else 0
    return chk


def bech32_secret_key(seed: bytes) -> str:
    """``seed`` (32 bytes) as an age secret key string (BIP 173 bech32, HRP ``age-secret-key-``)."""
    if len(seed) != 32:
        raise ValueError("an age secret key is 32 bytes")
    hrp, acc, bits, data = "age-secret-key-", 0, 0, []
    for b in seed:
        acc, bits = acc << 8 | b, bits + 8
        while bits >= 5:
            bits -= 5
            data.append(acc >> bits & 31)
    if bits:
        data.append(acc << (5 - bits) & 31)
    values = [ord(c) >> 5 for c in hrp] + [0] + [ord(c) & 31 for c in hrp] + data
    poly = _bech32_polymod(values + [0] * 6) ^ 1
    checksum = [poly >> 5 * (5 - i) & 31 for i in range(6)]
    return (hrp + "1" + "".join(_BECH32[d] for d in data + checksum)).upper()


def phrase_identity(phrase: str) -> x25519.Identity:
    """The age identity the recovery phrase stands for (normalized first; raises ValueError on a bad shape)."""
    seed = hashlib.scrypt(normalize_phrase(phrase).encode(), salt=KDF_SALT, maxmem=64 * 1024 * 1024, dklen=32, **KDF)
    return x25519.Identity.from_str(bech32_secret_key(seed))


def wrap_identity(identity: x25519.Identity, phrase: str) -> bytes:
    return pyrage.encrypt(str(identity).encode(), [phrase_identity(phrase).to_public()])


def is_legacy(wrapped: bytes) -> bool:
    """True for a key wrapped in age passphrase mode (before 2026-11-25)."""
    return b"\n-> scrypt " in wrapped.split(b"\n---", 1)[0]


def _identity_from(secret: bytes) -> x25519.Identity:
    try:
        return x25519.Identity.from_str(secret.decode().strip())
    except (UnicodeDecodeError, pyrage.IdentityError, ValueError) as exc:
        raise KeyDamaged("the recovery key opened but does not hold a sidekick key") from exc


def unwrap_identity(wrapped: bytes, phrase: str) -> x25519.Identity:
    if is_legacy(wrapped):
        return _unwrap_legacy(wrapped, phrase)
    try:
        secret = pyrage.decrypt(wrapped, [phrase_identity(phrase)])
    except (pyrage.DecryptError, ValueError) as exc:
        raise WrongPhrase("recovery phrase does not open this archive") from exc
    return _identity_from(secret)


def _unwrap_legacy(wrapped: bytes, phrase: str) -> x25519.Identity:
    """A key wrapped before 2026-11-25, in age passphrase mode."""
    try:
        secret = passphrase.decrypt(wrapped, normalize_phrase(phrase))
    except ValueError as exc:
        raise WrongPhrase("recovery phrase does not open this archive") from exc
    except pyrage.DecryptError as exc:
        if "excessive work" in str(exc).lower():
            raise KeyTooCostly(
                "this older recovery key needs more memory or time than age allows on this computer, so the "
                "phrase was not checked. Open the archive on a bigger computer, then export it again."
            ) from exc
        raise WrongPhrase("recovery phrase does not open this archive") from exc
    return _identity_from(secret)


def shred_keys(home: Home) -> list[str]:
    """Overwrite and remove every key file. Returns the names destroyed."""
    destroyed = []
    if home.keys_dir.is_symlink():  # never shred whatever a link points at
        home.keys_dir.unlink()
        return destroyed
    if not home.keys_dir.is_dir():
        return destroyed
    for p in sorted(home.keys_dir.iterdir()):
        if p.is_symlink():
            p.unlink()
        elif p.is_dir():
            for f in p.rglob("*"):
                if f.is_file() and not f.is_symlink():
                    _overwrite(f)
            shutil.rmtree(p)
        else:
            _overwrite(p)
            p.unlink()
        destroyed.append(p.name)
    return destroyed


def _overwrite(p) -> None:
    size = p.stat().st_size
    with open(p, "r+b") as f:
        f.write(secrets.token_bytes(max(size, 1)))
        f.flush()
        full_fsync(f.fileno())
