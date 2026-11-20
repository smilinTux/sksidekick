"""The open, encrypted archive format (``.sksk``): export, verify, import.

An archive is a plain tar with exactly three members, in this order:

* ``manifest.json``: public fields only (format, created, recipient, payload sha256), no file names.
* ``recovery.identity.age``: the sidekick's key, wrapped with the recovery phrase.
* ``payload.age``: a tar.gz of the story, age-encrypted to the sidekick's key.

See docs/ARCHIVE_FORMAT.md. Everything read from an archive is treated as hostile.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import io
import json
import os
import shutil
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

import pyrage
from pyrage import x25519

from sksidekick import keys
from sksidekick.home import Home

FORMAT = "sksidekick.archive.v1"
OUTER = ("manifest.json", "recovery.identity.age", "payload.age")
INNER_MANIFEST = "inner-manifest.json"
STORY = "story"


class ArchiveDamaged(ValueError):
    """The archive is incomplete, altered, or not a sidekick archive for this key."""


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _add(t: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mtime = 0
    info.mode = 0o600
    t.addfile(info, io.BytesIO(data))


def _write_envelope(dest: Path, payload_plain: bytes, recipient: x25519.Recipient, wrapped: bytes) -> Path:
    payload = pyrage.encrypt(payload_plain, [recipient])
    manifest = {"format": FORMAT, "created": _now(), "recipient": str(recipient), "payload_sha256": _sha256(payload)}
    dest = Path(dest)
    part = dest.with_name(dest.name + ".part")
    with open(part, "wb") as f:
        with tarfile.open(fileobj=f, mode="w") as t:
            _add(t, OUTER[0], json.dumps(manifest, indent=1).encode())
            _add(t, OUTER[1], wrapped)
            _add(t, OUTER[2], payload)
        f.flush()
        os.fsync(f.fileno())
    os.replace(part, dest)
    return dest


def export(home: Home, dest: Path) -> Path:
    """Write an encrypted archive of the whole story to *dest*."""
    files = {}
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for rel in home.story_paths():
            data = (home.root / rel).read_bytes()
            files[rel.as_posix()] = _sha256(data)
            _add(t, f"{STORY}/{rel.as_posix()}", data)
        inner = {"format": FORMAT, "created": _now(), "instance_id": home.instance()["instance_id"], "files": files}
        _add(t, INNER_MANIFEST, json.dumps(inner, indent=1).encode())
    wrapped = (home.keys_dir / keys.WRAPPED).read_bytes()
    return _write_envelope(dest, buf.getvalue(), keys.recipient(home), wrapped)


def _read_outer(path: Path) -> dict[str, bytes]:
    try:
        with tarfile.open(path, mode="r:") as t:
            members = t.getmembers()
            if [m.name for m in members] != list(OUTER) or not all(m.isfile() for m in members):
                raise ArchiveDamaged("not a sidekick archive (unexpected members)")
            out = {m.name: t.extractfile(m).read() for m in members}
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise ArchiveDamaged(f"archive damaged: {exc}") from exc
    for m_name, data in out.items():
        member = next(m for m in members if m.name == m_name)
        if len(data) != member.size:
            raise ArchiveDamaged("archive damaged: a member is truncated")
    return out


def _safe_rel(name: str) -> PurePosixPath:
    p = PurePosixPath(name)
    if p.is_absolute() or ".." in p.parts or not p.parts or p.parts[0] != STORY or len(p.parts) < 2:
        raise ArchiveDamaged(f"archive refused: unsafe member path {name!r}")
    return PurePosixPath(*p.parts[1:])


def _open_payload(path: Path, identity: x25519.Identity) -> tuple[dict, dict[str, bytes]]:
    outer = _read_outer(path)
    try:
        manifest = json.loads(outer["manifest.json"])
    except ValueError as exc:
        raise ArchiveDamaged("archive damaged: manifest unreadable") from exc
    if manifest.get("format") != FORMAT:
        raise ArchiveDamaged(f"unsupported archive format {manifest.get('format')!r}")
    if _sha256(outer["payload.age"]) != manifest.get("payload_sha256"):
        raise ArchiveDamaged("archive damaged: payload checksum does not match")
    try:
        plain = pyrage.decrypt(outer["payload.age"], [identity])
    except pyrage.DecryptError as exc:
        raise ArchiveDamaged("archive damaged or made with another key") from exc
    files: dict[str, bytes] = {}
    inner = None
    try:
        with tarfile.open(fileobj=io.BytesIO(plain), mode="r:gz") as t:
            for m in t.getmembers():
                if not m.isfile():
                    raise ArchiveDamaged(f"archive refused: {m.name!r} is not a regular file")
                data = t.extractfile(m).read()
                if m.name == INNER_MANIFEST:
                    inner = json.loads(data)
                    continue
                rel = _safe_rel(m.name).as_posix()
                if rel in files:
                    raise ArchiveDamaged(f"archive refused: duplicate member {rel!r}")
                files[rel] = data
    except (tarfile.TarError, EOFError, ValueError) as exc:
        if isinstance(exc, ArchiveDamaged):
            raise
        raise ArchiveDamaged(f"archive damaged: {exc}") from exc
    if inner is None or inner.get("format") != FORMAT:
        raise ArchiveDamaged("archive damaged: inner manifest missing")
    listed = inner.get("files", {})
    if set(listed) != set(files):
        raise ArchiveDamaged("archive damaged: files do not match the manifest")
    for rel, digest in listed.items():
        if _sha256(files[rel]) != digest:
            raise ArchiveDamaged(f"archive damaged: {rel} does not match its checksum")
    return inner, files


def verify(path: Path, identity: x25519.Identity) -> dict:
    """Check an archive completely; return its inner manifest or raise ArchiveDamaged."""
    inner, _ = _open_payload(Path(path), identity)
    return inner


def open_identity(path: Path, phrase: str) -> x25519.Identity:
    return keys.unwrap_identity(_read_outer(Path(path))["recovery.identity.age"], phrase)


def import_archive(
    path: Path,
    target: Home,
    *,
    phrase: str | None = None,
    identity: x25519.Identity | None = None,
) -> dict:
    """Restore an archive into an empty home. Returns the inner manifest."""
    if not target.is_empty():
        raise FileExistsError(f"{target.root} already holds a sidekick; import only into an empty home")
    path = Path(path)
    if identity is None:
        if phrase is None:
            raise ValueError("import needs the recovery phrase or the identity")
        identity = open_identity(path, phrase)
    inner, files = _open_payload(path, identity)
    wrapped = _read_outer(path)["recovery.identity.age"]
    target.root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".sksidekick-import-", dir=target.root.parent))
    try:
        for rel, data in files.items():
            dest = staging / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
        for child in staging.iterdir():
            os.replace(child, target.root / child.name)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    keys.install_identity(target, identity, wrapped)
    target.new_instance()
    return inner


def export_plain(home: Home, dest_dir: Path) -> int:
    """Copy the story, decrypted and readable, to a new directory on the user's own device."""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=False)
    for rel in home.story_paths():
        out = dest_dir / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(home.root / rel, out)
    return len(home.story_paths())
