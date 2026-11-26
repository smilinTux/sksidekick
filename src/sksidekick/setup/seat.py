"""The seat an operator's invite gives this sidekick.

The record (everything but the token) lives in ``<home>/seat.json``, which is story: it travels with the
sidekick. The token lives only in Hermes' ``.env``, which a default export redacts.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict
from datetime import datetime, timezone

from sksidekick.home import Home
from sksidekick.seats.record import Invite
from sksidekick.setup import hermes_config

TOKEN_VAR = "SKSIDEKICK_GATEWAY_TOKEN"
FILE = "seat.json"
_FIELDS = ("gateway_url", "agent_id", "client_id", "credential_revision", "expires_at")


def _token(home: Home) -> str | None:
    try:
        text = (home.hermes_dir / ".env").read_text()
    except OSError:
        return None
    for ln in text.splitlines():
        k, _, v = ln.partition("=")
        if k.strip() == TOKEN_VAR and v.strip():
            return v.strip()
    return None


def load(home: Home) -> Invite | None:
    """The installed seat, or None when there is no record or no token (never raises on a bad file)."""
    try:
        rec = json.loads((home.root / FILE).read_text())
        token = _token(home)
        if token is None or not isinstance(rec, dict):
            return None
        return Invite(**{f: rec[f] for f in _FIELDS}, token=token)
    except (OSError, ValueError, KeyError, TypeError):
        return None


def write_record(home: Home, invite: Invite) -> None:
    rec = {k: v for k, v in asdict(invite).items() if k != "token"}
    home.root.mkdir(parents=True, exist_ok=True)
    hermes_config.write_private(home.root / FILE, (json.dumps(rec, indent=2) + "\n").encode())


def install(home: Home, invite: Invite) -> None:
    """Token to hermes/.env (0600), the rest to seat.json (0600). The token is written first: a record
    without a token is ignored by load()."""
    hermes_config.set_env(home, TOKEN_VAR, invite.token)
    write_record(home, invite)


# An invite given at setup start stays OUTSIDE the home (so init and import still accept it) until the
# shared tier is saved. Keyed by home root; held in memory only.
_PENDING: dict[str, tuple[Invite, str, bool]] = {}


def stage(home: Home, invite: Invite, path: str, keep: bool) -> None:
    _PENDING[str(home.root)] = (invite, path, keep)


def pending(home: Home) -> tuple[Invite, str, bool] | None:
    return _PENDING.get(str(home.root))


def current(home: Home) -> Invite | None:
    """The installed seat, else the staged invite."""
    inv = load(home)
    if inv is not None:
        return inv
    p = pending(home)
    return p[0] if p else None


def used(home: Home) -> None:
    """The staged invite was saved: forget it and delete its file unless --keep-invite."""
    p = _PENDING.pop(str(home.root), None)
    if p and not p[2]:
        try:
            os.unlink(p[1])
        except OSError:
            print(f"the invite was installed but {p[1]} could not be removed; delete it", file=sys.stderr)


def expired(invite: Invite) -> bool:
    try:
        end = datetime.strptime(invite.expires_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    return end <= datetime.now(timezone.utc)
