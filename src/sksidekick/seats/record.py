"""The invite file and the private seats record.

The seats record is the only place a client id maps to a person. It lives in
a 0700 directory as a 0600 file on the operator's host.
"""

from __future__ import annotations

import ipaddress
import json
import os
import secrets
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlparse

from sksidekick.seats.registry import ID_RE, REVISION_RE, locked, write_atomic

PLACES = ("container", "mac", "linux", "nextcloud")
KIND = "sksidekick-invite"


def new_client_id() -> str:
    return "sk-" + secrets.token_hex(6)


def check_gateway_url(url: str) -> str:
    """https is required unless the host is loopback, private or link-local."""
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise ValueError("gateway url must be an http(s) URL with a host")
    if p.scheme == "https":
        return url
    host = p.hostname
    if host == "localhost":
        return url
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        raise ValueError("gateway url must use https unless the host is a private address") from None
    if ip.is_loopback or ip.is_private or ip.is_link_local:
        return url
    raise ValueError("gateway url must use https unless the host is a private address")


@dataclass(frozen=True)
class Invite:
    gateway_url: str
    agent_id: str
    client_id: str
    credential_revision: str
    token: str
    expires_at: str

    def to_json(self) -> str:
        return json.dumps({"kind": KIND, "version": 1, **asdict(self)}, indent=2) + "\n"

    @classmethod
    def from_json(cls, text: str) -> "Invite":
        try:
            d = json.loads(text)
        except json.JSONDecodeError:
            raise ValueError("this is not an invite file (not JSON)") from None
        if not isinstance(d, dict) or d.get("kind") != KIND:
            raise ValueError("this is not an sksidekick invite file")
        if d.get("version") != 1:
            raise ValueError(f"unsupported invite version {d.get('version')!r}")
        fields = ("gateway_url", "agent_id", "client_id", "credential_revision", "token", "expires_at")
        for f in fields:
            if not isinstance(d.get(f), str) or not d[f]:
                raise ValueError(f"the invite is missing {f}")
        if not ID_RE.match(d["agent_id"]) or not ID_RE.match(d["client_id"]):
            raise ValueError("the invite has an invalid id")
        if not REVISION_RE.match(d["credential_revision"]):
            raise ValueError("the invite has an invalid credential revision")
        check_gateway_url(d["gateway_url"])
        return cls(**{f: d[f] for f in fields})


def write_private(path: Path, text: str) -> None:
    """Create a new 0600 file; never overwrite one."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)


class Seats:
    """``<dir>/seats.json`` plus its lock, inside a 0700 directory."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.file = self.root / "seats.json"

    def exists(self) -> bool:
        return self.file.exists()

    def init(self, estate: str) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        with locked(self.file):
            if not self.file.exists():
                self._save({"estate": estate, "seats": []})

    @contextmanager
    def transaction(self):
        """Hold the lock over a read-modify-write; yields the document to edit."""
        with locked(self.file):
            doc = self.load()
            yield doc
            self._save(doc)

    def load(self) -> dict:
        return json.loads(self.file.read_text())

    def _save(self, doc: dict) -> None:
        write_atomic(self.file, (json.dumps(doc, indent=2) + "\n").encode())

    def find(self, doc: dict, client_id: str) -> dict:
        for s in doc["seats"]:
            if s["client_id"] == client_id:
                return s
        raise KeyError(client_id)
