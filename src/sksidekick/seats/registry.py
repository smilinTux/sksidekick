"""Write skgateway's client-auth registry (schema_version 1) atomically.

The gateway parser is strict, so the checks here mirror it: id and revision
patterns, boolean ``revoked``, a parseable ``expires_at`` and no duplicate
token hashes. Every change holds ``flock`` on ``<path>.lock`` across the
read-modify-write and replaces the file in place of editing it, keeping the
owner, group and mode of the file already there (0600 for a new one).
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import secrets
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ID_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")
HASH_RE = re.compile(r"^[a-f0-9]{64}$")
REVISION_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}$")
EXPIRY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class RegistryError(ValueError):
    pass


def new_revision(prefix: str = "seats") -> str:
    return f"{prefix}-{datetime.now(timezone.utc):%Y%m%d%H%M%S}-{secrets.token_hex(2)}"


@contextmanager
def locked(path: Path):
    lock = Path(str(path) + ".lock")
    fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)  # closing drops the lock


def write_atomic(path: Path, data: bytes, default_mode: int = 0o600) -> None:
    """Replace ``path`` with ``data``, keeping the old file's owner, group and mode."""
    path = Path(path)
    try:
        st = path.stat()
        mode, owner = st.st_mode & 0o777, (st.st_uid, st.st_gid)
    except FileNotFoundError:
        mode, owner = default_mode, None
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "wb") as f:
            os.fchmod(f.fileno(), mode)
            if owner is not None:
                try:
                    os.fchown(f.fileno(), *owner)
                except PermissionError:
                    pass  # not root: the file is ours already, or we cannot change it anyway
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def _check(cred: dict) -> None:
    if not ID_RE.match(str(cred.get("agent_id", ""))):
        raise RegistryError("agent_id is not a valid gateway id")
    if not ID_RE.match(str(cred.get("client_id", ""))):
        raise RegistryError("client_id is not a valid gateway id")
    if not HASH_RE.match(str(cred.get("token_sha256", ""))):
        raise RegistryError("token_sha256 must be 64 lower-case hex digits")
    if not REVISION_RE.match(str(cred.get("credential_revision", ""))):
        raise RegistryError("credential_revision is not valid")
    if not isinstance(cred.get("revoked"), bool):
        raise RegistryError("revoked must be a boolean")
    if not EXPIRY_RE.match(str(cred.get("expires_at", ""))):
        raise RegistryError("expires_at must be UTC ISO 8601 with a Z suffix")


def _load(path: Path) -> dict:
    try:
        doc = json.loads(Path(path).read_text())
    except FileNotFoundError:
        return {"schema_version": 1, "revision": new_revision(), "credentials": []}
    except json.JSONDecodeError:
        raise RegistryError(f"{path} is not valid JSON; refusing to overwrite it") from None
    if doc.get("schema_version") != 1 or not isinstance(doc.get("credentials"), list):
        raise RegistryError(f"{path} is not a schema_version 1 client registry")
    return doc


def _save(path: Path, doc: dict) -> str:
    doc["revision"] = new_revision()
    write_atomic(path, (json.dumps(doc, indent=2) + "\n").encode())
    return doc["revision"]


def _tuple(c: dict) -> tuple:
    return (c["agent_id"], c["client_id"], c["credential_revision"])


def upsert(path, credential: dict) -> str:
    """Add the credential, or replace the one with the same agent, client and revision."""
    path = Path(path)
    cred = dict(credential)
    _check(cred)
    with locked(path):
        doc = _load(path)
        creds = [c for c in doc["credentials"] if _tuple(c) != _tuple(cred)]
        if any(c["token_sha256"] == cred["token_sha256"] for c in creds):
            raise RegistryError("another credential already has this token hash")
        creds.append(cred)
        doc["credentials"] = creds
        return _save(path, doc)


def revoke(path, client_id: str) -> str:
    """Mark every credential of ``client_id`` revoked."""
    path = Path(path)
    with locked(path):
        doc = _load(path)
        hit = [c for c in doc["credentials"] if c.get("client_id") == client_id]
        if not hit:
            raise RegistryError(f"no credential for {client_id} in {path}")
        for c in hit:
            c["revoked"] = True
        return _save(path, doc)


def revoke_revision(path, client_id: str, credential_revision: str) -> str:
    """Revoke one revision of a client, leaving others as they are."""
    path = Path(path)
    with locked(path):
        doc = _load(path)
        hit = [c for c in doc["credentials"]
               if c.get("client_id") == client_id and c.get("credential_revision") == credential_revision]
        if not hit:
            raise RegistryError(f"no credential {client_id} {credential_revision} in {path}")
        for c in hit:
            c["revoked"] = True
        return _save(path, doc)
