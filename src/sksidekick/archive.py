"""The open, encrypted archive format (``.sksk``): export, verify, import.

An archive is a plain tar with exactly three members, in this order:

* ``manifest.json``: public fields only (format, created, purpose, recipient, payload and wrapped-key sha256, mac),
  no file names.
* ``recovery.identity.age``: the sidekick's key, wrapped with the recovery phrase.
* ``payload.age``: a tar.gz of the story, age-encrypted to the sidekick's key.

The ``mac`` is an HMAC-SHA256 under a key derived from the sidekick's secret key,
so a backup host (which knows the public recipient and can copy the wrapped key)
can neither read an archive nor forge one. See docs/ARCHIVE_FORMAT.md.
Everything read from an archive is treated as hostile.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import shutil
import tarfile
import tempfile
import unicodedata
from pathlib import Path, PurePosixPath

import pyrage
from pyrage import x25519

from sksidekick import keys, redact
from sksidekick.home import STAGING_PREFIX, Home, excluded

FORMAT = "sksidekick.archive.v1"
OUTER = ("manifest.json", "recovery.identity.age", "payload.age")
INNER_MANIFEST = "inner-manifest.json"
STORY = "story"
PURPOSES = ("export", "backup", "move")
MAX_STORY_BYTES = int(os.environ.get("SKSIDEKICK_MAX_STORY_BYTES", 4 * 1024**3))
_MAC_FIELDS = ("format", "created", "purpose", "recipient", "payload_sha256", "wrapped_sha256")
_HEX_MAC = re.compile(r"[0-9a-f]{64}")
_REFUSED_TOP = {"keys", "state"}


class ArchiveDamaged(ValueError):
    """The archive is incomplete, altered, or not a sidekick archive for this key."""


class StoryError(ValueError):
    """The home cannot be archived faithfully (for example it holds symlinks)."""


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _mac_key(identity: x25519.Identity) -> bytes:
    return hashlib.sha256(b"sksidekick.archive.v1 mac\0" + str(identity).encode()).digest()


def _mac(identity: x25519.Identity, manifest: dict) -> str:
    body = json.dumps({k: manifest.get(k) for k in _MAC_FIELDS}, sort_keys=True, separators=(",", ":")).encode()
    return hmac.new(_mac_key(identity), body, hashlib.sha256).hexdigest()


def _add(t: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mtime = 0
    info.mode = 0o600
    t.addfile(info, io.BytesIO(data))


def _write_envelope(
    dest: Path,
    payload_plain: bytes,
    recipient: x25519.Recipient,
    wrapped: bytes,
    *,
    identity: x25519.Identity | None = None,
    purpose: str = "export",
) -> Path:
    """Seal *payload_plain* into an archive at *dest*. Never overwrites; a failure leaves no file."""
    if purpose not in PURPOSES:
        raise ValueError(f"unknown purpose {purpose!r}")
    dest = Path(dest)
    if dest.exists():
        raise FileExistsError(f"{dest} already exists; choose a new file name")
    payload = pyrage.encrypt(payload_plain, [recipient])
    manifest = {
        "format": FORMAT,
        "created": _now(),
        "purpose": purpose,
        "recipient": str(recipient),
        "payload_sha256": _sha256(payload),
        "wrapped_sha256": _sha256(wrapped),
    }
    manifest["mac"] = _mac(identity, manifest) if identity is not None else None
    part = dest.with_name(f"{dest.name}.{secrets.token_hex(4)}.part")
    try:
        fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            with tarfile.open(fileobj=f, mode="w") as t:
                _add(t, OUTER[0], json.dumps(manifest, indent=1).encode())
                _add(t, OUTER[1], wrapped)
                _add(t, OUTER[2], payload)
            f.flush()
            keys.full_fsync(f.fileno())
        if dest.exists():
            raise FileExistsError(f"{dest} appeared while writing; not overwriting it")
        os.replace(part, dest)
    finally:
        if part.exists():
            part.unlink()
    return dest


def _story_bytes(home: Home, rel: Path, with_credentials: bool, redacted: list, redacted_keys: list) -> bytes:
    data = (home.root / rel).read_bytes()
    fn = None if with_credentials else redact.REDACTORS.get(rel.as_posix())
    if fn is None:
        return data
    text, found = fn(data.decode("utf-8", errors="surrogateescape"))
    if found:
        redacted.append(rel.as_posix())
        redacted_keys.extend(k for k in found if k not in redacted_keys)
    return text.encode("utf-8", errors="surrogateescape")


def export(home: Home, dest: Path, *, purpose: str = "export", with_credentials: bool = True) -> Path:
    """Write an encrypted archive of the whole story to a NEW file *dest*.

    Without *with_credentials*, login secrets in Hermes' ``.env`` and ``config.yaml``
    are replaced by ``__REMOVED__`` and listed in the inner manifest.
    """
    links = home.symlinks()
    if links:
        shown = ", ".join(str(p) for p in links[:5])
        raise StoryError(f"the home holds symlinks, which an archive would leave out: {shown}. Replace them with real files first.")
    files = {}
    redacted: list[str] = []
    redacted_keys: list[str] = []
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for rel in home.story_paths():
            data = _story_bytes(home, rel, with_credentials, redacted, redacted_keys)
            files[rel.as_posix()] = _sha256(data)
            _add(t, f"{STORY}/{rel.as_posix()}", data)
        inner = {
            "format": FORMAT, "created": _now(), "instance_id": home.instance()["instance_id"], "files": files,
            "redacted": redacted, "redacted_keys": redacted_keys,
        }
        _add(t, INNER_MANIFEST, json.dumps(inner, indent=1).encode())
    wrapped = keys.checked_wrapped(home)
    return _write_envelope(
        dest, buf.getvalue(), keys.recipient(home), wrapped, identity=keys.load_identity(home), purpose=purpose
    )


def _read_outer(path: Path) -> dict[str, bytes]:
    try:
        with tarfile.open(path, mode="r:") as t:
            members = t.getmembers()
            if [m.name for m in members] != list(OUTER) or not all(m.isfile() for m in members):
                raise ArchiveDamaged("not a sidekick archive (unexpected members)")
            if sum(m.size for m in members) > MAX_STORY_BYTES:
                raise ArchiveDamaged("archive refused: too large")
            out = {}
            for m in members:
                data = t.extractfile(m).read()
                if len(data) != m.size:
                    raise ArchiveDamaged("archive damaged: a member is truncated")
                out[m.name] = data
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise ArchiveDamaged(f"archive damaged: {exc}") from exc
    return out


def _safe_rel(name: str) -> PurePosixPath:
    p = PurePosixPath(name)
    if p.is_absolute() or ".." in p.parts or not p.parts or p.parts[0] != STORY or len(p.parts) < 2:
        raise ArchiveDamaged(f"archive refused: unsafe member path {name!r}")
    rel = PurePosixPath(*p.parts[1:])
    if rel.parts[0].casefold() in _REFUSED_TOP or rel.name.casefold().startswith("index.db") or excluded(rel):
        raise ArchiveDamaged(f"archive refused: {name!r} is not story content")
    return rel


def _manifest(outer: dict[str, bytes]) -> dict:
    try:
        manifest = json.loads(outer["manifest.json"])
    except ValueError as exc:
        raise ArchiveDamaged("archive damaged: manifest unreadable") from exc
    if not isinstance(manifest, dict):
        raise ArchiveDamaged("archive damaged: manifest is not an object")
    return manifest


def _open_payload(outer: dict[str, bytes], identity: x25519.Identity) -> tuple[dict, dict[str, bytes]]:
    manifest = _manifest(outer)
    if manifest.get("format") != FORMAT:
        raise ArchiveDamaged(f"unsupported archive format {manifest.get('format')!r}")
    if _sha256(outer["payload.age"]) != manifest.get("payload_sha256"):
        raise ArchiveDamaged("archive damaged: payload checksum does not match")
    if _sha256(outer["recovery.identity.age"]) != manifest.get("wrapped_sha256"):
        raise ArchiveDamaged("archive damaged: the recovery key inside it does not match, so the phrase could not open it")
    mac = manifest.get("mac")
    if not isinstance(mac, str) or not _HEX_MAC.fullmatch(mac) or not hmac.compare_digest(mac, _mac(identity, manifest)):
        raise ArchiveDamaged("archive refused: it was not made by this sidekick (authentication failed)")
    try:
        plain = pyrage.decrypt(outer["payload.age"], [identity])
    except pyrage.DecryptError as exc:
        raise ArchiveDamaged("archive damaged or made with another key") from exc
    files: dict[str, bytes] = {}
    seen: set[str] = set()
    inner = None
    total = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(plain), mode="r:gz") as t:
            for m in t:
                if not m.isfile():
                    raise ArchiveDamaged(f"archive refused: {m.name!r} is not a regular file")
                total += m.size
                if total > MAX_STORY_BYTES:
                    raise ArchiveDamaged("archive refused: the story is too large")
                data = t.extractfile(m).read()
                if m.name == INNER_MANIFEST:
                    inner = json.loads(data)
                    continue
                rel = _safe_rel(m.name).as_posix()
                key = unicodedata.normalize("NFC", rel).casefold()  # macOS folds both case and normalization
                if key in seen:
                    raise ArchiveDamaged(f"archive refused: duplicate member {rel!r}")
                seen.add(key)
                files[rel] = data
    except (tarfile.TarError, EOFError, ValueError, OSError) as exc:
        if isinstance(exc, ArchiveDamaged):
            raise
        raise ArchiveDamaged(f"archive damaged: {exc}") from exc
    if not isinstance(inner, dict) or inner.get("format") != FORMAT or not isinstance(inner.get("files"), dict):
        raise ArchiveDamaged("archive damaged: inner manifest missing or malformed")
    listed = inner["files"]
    if set(listed) != set(files):
        raise ArchiveDamaged("archive damaged: files do not match the manifest")
    for rel, digest in listed.items():
        if _sha256(files[rel]) != digest:
            raise ArchiveDamaged(f"archive damaged: {rel} does not match its checksum")
    inner = dict(inner, purpose=manifest.get("purpose"))
    inner.setdefault("redacted", [])
    inner.setdefault("redacted_keys", [])
    return inner, files


def verify(path: Path, identity: x25519.Identity) -> dict:
    """Check an archive completely; return its inner manifest (plus purpose) or raise ArchiveDamaged."""
    inner, _ = _open_payload(_read_outer(Path(path)), identity)
    return inner


def open_identity(path: Path, phrase: str) -> x25519.Identity:
    return keys.unwrap_identity(_read_outer(Path(path))["recovery.identity.age"], phrase)


def _write_private_file(dest: Path, data: bytes) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
        f.flush()
        keys.full_fsync(f.fileno())


def _clear_empty(path: Path) -> None:
    """Remove a directory tree that holds no files (the target was checked empty)."""
    if any(p.is_file() or p.is_symlink() for p in path.rglob("*")):
        raise FileExistsError(f"{path} changed during import; not overwriting it")
    shutil.rmtree(path)


def import_archive(
    path: Path,
    target: Home,
    *,
    phrase: str | None = None,
    identity: x25519.Identity | None = None,
) -> dict:
    """Restore an archive into an empty home. Returns the inner manifest (plus purpose).

    The new home is built in a staging directory INSIDE the target (so it works
    for a mount point, a symlinked home or a read-only parent), then each part is
    renamed into place, with keys and state last. A failure removes what was
    moved, so the target is left empty again.
    """
    if not target.is_empty():
        raise FileExistsError(f"{target.root} already holds a sidekick; import only into an empty home")
    outer = _read_outer(Path(path))
    if identity is None:
        if phrase is None:
            raise ValueError("import needs the recovery phrase or the identity")
        identity = keys.unwrap_identity(outer["recovery.identity.age"], phrase)
    inner, files = _open_payload(outer, identity)
    root = target.root.resolve() if target.root.is_symlink() else target.root
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    for stale in root.glob(STAGING_PREFIX + "*"):
        shutil.rmtree(stale) if stale.is_dir() and not stale.is_symlink() else stale.unlink()
    staging = Path(tempfile.mkdtemp(prefix=STAGING_PREFIX, dir=root))
    moved: list[Path] = []
    try:
        built = Home(staging)
        for rel, data in files.items():
            _write_private_file(staging / rel, data)
        keys.install_identity(built, identity, outer["recovery.identity.age"])
        built.new_instance()
        order = sorted(staging.iterdir(), key=lambda p: (p.name in ("keys", "state"), p.name == "state", p.name))
        for child in order:
            dest = root / child.name
            if dest.is_symlink() or dest.is_file():
                raise FileExistsError(f"{dest} changed during import; not overwriting it")
            if dest.exists():
                _clear_empty(dest)
            os.replace(child, dest)
            moved.append(dest)
    except BaseException:
        for dest in moved:
            shutil.rmtree(dest, ignore_errors=True) if dest.is_dir() else dest.unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return inner


def export_plain(home: Home, dest_dir: Path, *, with_credentials: bool = False) -> int:
    """Copy the story, decrypted and readable, to a new private directory on the user's own device."""
    links = home.symlinks()
    if links:
        raise StoryError(f"the home holds symlinks: {', '.join(str(p) for p in links[:5])}")
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=False, mode=0o700)
    os.chmod(dest_dir, 0o700)
    paths = home.story_paths()
    for rel in paths:
        _write_private_file(dest_dir / rel, _story_bytes(home, rel, with_credentials, [], []))
    return len(paths)
