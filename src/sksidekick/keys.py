"""The sidekick's key and the recovery phrase that is the only way back to it.

The running sidekick keeps its identity in ``keys/live.identity`` (it must hold
the key while it runs). ``keys/recovery.identity.age`` is the same identity
wrapped with the recovery phrase, which travels inside every archive. The
phrase itself is never stored.
"""

from __future__ import annotations

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


class WrongPhrase(ValueError):
    """The recovery phrase does not open this key."""


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


def create_keys(home: Home) -> str:
    """Create the sidekick's key; return the recovery phrase (shown once, never stored)."""
    if home.keys_dir.is_dir() and any(home.keys_dir.iterdir()):
        raise FileExistsError(f"{home.keys_dir} already holds keys")
    phrase = generate_phrase()
    identity = x25519.Identity.generate()
    wrapped = passphrase.encrypt(str(identity).encode(), phrase)
    install_identity(home, identity, wrapped)
    return phrase


def load_identity(home: Home) -> x25519.Identity:
    return x25519.Identity.from_str((home.keys_dir / LIVE).read_text().strip())


def recipient(home: Home) -> x25519.Recipient:
    return x25519.Recipient.from_str((home.keys_dir / RECIPIENT).read_text().strip())


def unwrap_identity(wrapped: bytes, phrase: str) -> x25519.Identity:
    try:
        secret = passphrase.decrypt(wrapped, normalize_phrase(phrase))
    except (pyrage.DecryptError, ValueError) as exc:
        raise WrongPhrase("recovery phrase does not open this archive") from exc
    return x25519.Identity.from_str(secret.decode().strip())


def shred_keys(home: Home) -> list[str]:
    """Overwrite and remove every key file. Returns the names destroyed."""
    destroyed = []
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
