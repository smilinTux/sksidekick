"""POST /api/phrase/new and /api/phrase/confirm: the recovery phrase, shown once and confirmed.

The phrase lives only in this process's memory, between ``phrase/new`` and a good confirm, so the two
typed groups can be checked. It is never written to a file, a log, /api/state or an error message, and
the only response that carries it is the one from ``phrase/new``.

A key set whose phrase was never confirmed is useless (nobody could restore an archive made with it),
so it is shredded when a new phrase is asked for, after 3 wrong confirms, after ``TIMEOUT`` seconds,
when the server stops, and when the server starts and finds one left over (a crash or kill -9).
Once the phrase step is confirmed nothing here touches the keys again.

``check`` indices are 0-based positions among the 8 groups; confirm uses the same numbering, as
strings (JSON object keys): ``{"groups": {"2": "ABCDE", "6": "FGHJK"}}``.
"""

from __future__ import annotations

import hmac
import secrets
import threading
from dataclasses import dataclass, field

from sksidekick import keys
from sksidekick.home import Home
from sksidekick.setup import state
from sksidekick.setup.state import FieldError

TIMEOUT = 30 * 60  # seconds an unconfirmed phrase may wait for its confirm
MAX_MISSES = 3
MISMATCH = "those groups do not match"

_RANDOM = secrets.SystemRandom()
_LOCK = threading.Lock()


@dataclass(eq=False)
class _Pending:
    groups: list[str]
    check: list[int]
    misses: int = 0
    timer: threading.Timer | None = field(default=None, repr=False)

    def __repr__(self) -> str:  # never let a stray debug print show the groups
        return "<pending recovery phrase>"


_PENDING: dict[str, _Pending] = {}  # str(home.root) -> the phrase waiting for its confirm


def _confirmed(home: Home) -> bool:
    return "phrase" in state.load(home)["completed"]


def _drop(home: Home) -> None:
    """Forget the held phrase for ``home`` (caller holds _LOCK)."""
    p = _PENDING.pop(str(home.root), None)
    if p and p.timer:
        p.timer.cancel()


def _shred_unconfirmed(home: Home) -> None:
    """Drop the phrase and destroy the keys, unless the phrase step is already confirmed (caller holds _LOCK)."""
    _drop(home)
    if not _confirmed(home):
        keys.shred_keys(home)


def _expire(home: Home, pending: _Pending) -> None:
    with _LOCK:
        if _PENDING.get(str(home.root)) is pending:
            _shred_unconfirmed(home)


def _norm_group(text: object) -> str:
    if not isinstance(text, str):
        return ""
    chars = "".join(c for c in text.upper() if c not in " -\t\n")
    return "".join(keys.LOOKALIKES.get(c, c) for c in chars)


def new(home: Home, body: dict) -> dict:
    with _LOCK:
        _shred_unconfirmed(home)  # a refresh, or keys left behind: the old phrase is gone for good
        try:
            p = keys.create_keys(home)
        except BaseException:
            keys.shred_keys(home)  # a half-made key set must not outlive this call
            raise
        pending = _Pending(groups=p.split("-"), check=sorted(_RANDOM.sample(range(keys.GROUPS), 2)))
        pending.timer = threading.Timer(TIMEOUT, _expire, args=(home, pending))
        pending.timer.daemon = True
        _PENDING[str(home.root)] = pending
        pending.timer.start()
        return {"phrase": p, "check": list(pending.check)}


def confirm(home: Home, body: dict) -> dict:
    typed = body.get("groups")
    with _LOCK:
        pending = _PENDING.get(str(home.root))
        if pending is None:
            raise FieldError("groups", MISMATCH, {"new_phrase_needed": True})
        ok = isinstance(typed, dict) and set(typed) == {str(i) for i in pending.check}
        for i in pending.check:  # compare both groups, whatever the first one gave
            got = _norm_group(typed.get(str(i)) if isinstance(typed, dict) else None)
            ok &= hmac.compare_digest(got.encode(), pending.groups[i].encode())
        if not ok:
            pending.misses += 1
            if pending.misses >= MAX_MISSES:
                _shred_unconfirmed(home)
                raise FieldError("groups", MISMATCH, {"new_phrase_needed": True})
            raise FieldError("groups", MISMATCH)
        _drop(home)
        state.advance(home, "phrase")  # under _LOCK, so the timer cannot shred in between
        return {}


def refuse_plain(home: Home, body: dict) -> dict:
    """POST /api/phrase on its own must never skip the phrase."""
    raise FieldError("groups", "type the two groups of your recovery phrase to continue")


def on_start(home: Home) -> None:
    """Keys left by a setup that died before the confirm (a crash, kill -9) cannot be trusted: shred them."""
    with _LOCK:
        _shred_unconfirmed(home)


def on_stop(home: Home) -> None:
    """The server is stopping: an unconfirmed phrase and its keys go with it."""
    with _LOCK:
        _shred_unconfirmed(home)
