"""``sksidekick-seat``: the operator's seats record and gateway credentials."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sksidekick.seats import registry
from sksidekick.seats.record import (
    PLACES, Invite, Seats, check_gateway_url, new_client_id, write_private,
)

AGENT_ID = "sidekick"


class SeatError(Exception):
    """A plain-language refusal; the command exits 2."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _credential_revision() -> str:
    return f"cr-{_now():%Y%m%d}-{secrets.token_hex(2)}"


def _open_seats(a, create: bool) -> Seats:
    seats = Seats(Path(a.seats_dir))
    if not seats.exists():
        if not create:
            raise SeatError(f"no seats record in {seats.root}; run init first")
        seats.init(a.estate)
    owner = seats.load()["estate"]
    if owner != a.estate:
        raise SeatError(f"this seats directory belongs to estate {owner}, not {a.estate}")
    host = os.environ.get("SKWORLD_ESTATE")
    if host and host != owner:
        raise SeatError(f"this seats directory belongs to estate {owner}, but this host is estate {host}")
    return seats


def _reload(a) -> None:
    cmd = ["systemctl", "--user", "kill", "-s", "HUP", a.reload_unit or "<gateway-unit>"]
    if a.reload_unit:
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            print(f"the gateway reload failed: {r.stderr.strip()}", file=sys.stderr)
            print("run it yourself: " + " ".join(cmd))
        return
    print("the gateway rereads the registry on SIGHUP: " + " ".join(cmd))


def _issue(a, client_id: str) -> tuple[Invite, dict, Path]:
    """Make a token, write the invite, and return the credential for the registry."""
    check_gateway_url(a.gateway_url)
    token = secrets.token_urlsafe(32)
    expires = _stamp(_now() + timedelta(days=a.days))
    rev = _credential_revision()
    invite = Invite(a.gateway_url, AGENT_ID, client_id, rev, token, expires)
    out = Path(a.invite_out) if a.invite_out else Path(a.seats_dir) / "invites" / f"{client_id}-{rev}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    if a.invite_out is None:
        os.chmod(out.parent, 0o700)
    try:
        write_private(out, invite.to_json())
    except FileExistsError:
        raise SeatError(f"{out} already exists; pick another --invite-out") from None
    cred = {
        "agent_id": AGENT_ID, "client_id": client_id,
        "token_sha256": hashlib.sha256(token.encode()).hexdigest(),
        "credential_revision": rev, "revoked": False, "expires_at": expires,
    }
    return invite, cred, out


def _cmd_init(a) -> int:
    _open_seats(a, create=True)
    print(f"seats record ready in {a.seats_dir} (estate {a.estate})")
    return 0


def _cmd_add(a) -> int:
    seats = _open_seats(a, create=True)
    client_id = new_client_id()
    invite, cred, out = _issue(a, client_id)
    try:
        registry.upsert(a.registry, cred)
        with seats.transaction() as doc:
            doc["seats"].append({
                "client_id": client_id, "owner": a.owner, "contact": a.contact or "",
                "place": a.place, "estate": doc["estate"], "created_at": _stamp(_now()),
                "state": "invited", "credential_revision": invite.credential_revision,
            })
    except BaseException:
        out.unlink(missing_ok=True)  # no seat was recorded, so keep no orphan token
        raise
    print(f"seat {client_id} for {a.owner} ({a.place})")
    print(f"invite: {out}")
    print(f"expires: {invite.expires_at}")
    print("The invite holds the sidekick's key. Deliver it through your private file share or vault, never a chat.")
    _reload(a)
    return 0


def _cmd_list(a) -> int:
    doc = _open_seats(a, create=False).load()
    for s in doc["seats"]:
        print(f"{s['client_id']}  {s['state']:<8} {s['place']:<9} {s['owner']}")
    if not doc["seats"]:
        print("no seats")
    return 0


def _cmd_rotate(a) -> int:
    seats = _open_seats(a, create=False)
    with seats.transaction() as doc:
        try:
            seat = seats.find(doc, a.client_id)
        except KeyError:
            raise SeatError(f"no seat {a.client_id}") from None
        if seat["state"] == "removed":
            raise SeatError(f"seat {a.client_id} was removed")
        old = seat["credential_revision"]
        invite, cred, out = _issue(a, a.client_id)
        try:
            registry.upsert(a.registry, cred)
            registry.revoke_revision(a.registry, a.client_id, old)
        except BaseException:
            out.unlink(missing_ok=True)
            raise
        seat["credential_revision"] = invite.credential_revision
    print(f"seat {a.client_id}: new credential {invite.credential_revision}; {old} is revoked")
    print(f"invite: {out}")
    print(f"expires: {invite.expires_at}")
    _reload(a)
    return 0


def _cmd_remove(a) -> int:
    seats = _open_seats(a, create=False)
    with seats.transaction() as doc:
        try:
            seat = seats.find(doc, a.client_id)
        except KeyError:
            raise SeatError(f"no seat {a.client_id}") from None
        registry.revoke(a.registry, a.client_id)
        seat["state"] = "removed"
        seat["removed_at"] = _stamp(_now())
    print(f"seat {a.client_id} ({seat['owner']}): credential revoked, record marked removed")
    print("The sidekick itself is a standalone island; the operator cannot reach into it. The user runs:")
    print("  sksidekick export <file>   (keep their story)")
    print("  sksidekick delete          (remove it from the machine)")
    if seat["place"] == "container":
        print("If you host it in a container: run both inside it, then stop and remove the container and its volume.")
    _reload(a)
    return 0


def _cmd_usage(a) -> int:
    try:
        doc = json.loads(Path(a.json).read_text())
    except (OSError, json.JSONDecodeError) as e:
        raise SeatError(f"cannot read {a.json}: {e}") from None
    print("Attribution is only trustworthy on a sidekick gateway that meters by the verified client id "
          "(client auth on).", file=sys.stderr)
    totals: dict[str, dict] = {}
    rows = (doc.get("spend") or {}).get("rows")
    if rows:
        for r in rows:
            t = totals.setdefault(str(r.get("client")), {"calls": 0, "tokens": 0, "dollars": 0.0})
            t["calls"] += r.get("calls") or 0
            t["tokens"] += (r.get("input_tokens") or 0) + (r.get("output_tokens") or 0)
            t["dollars"] += r.get("dollars") or 0.0
    else:  # no spend block: fall back to today's counters
        for r in (doc.get("today") or {}).get("clients", []):
            t = totals.setdefault(str(r.get("client")), {"calls": 0, "tokens": 0, "dollars": 0.0})
            t["calls"] += r.get("calls") or 0
            t["tokens"] += r.get("tokens") or 0
            t["dollars"] += r.get("dollars") or 0.0
    owners = {s["client_id"]: s["owner"] for s in _open_seats(a, create=False).load()["seats"]}
    for client, t in sorted(totals.items()):
        who = owners.get(client, "(not a seat)")
        print(f"{client}  {who}  calls={t['calls']} tokens={t['tokens']} dollars={t['dollars']:.4f}")
    if not totals:
        print("no usage in this response")
    return 0


def _build() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="sksidekick-seat", description=__doc__)
    p.add_argument("--seats-dir", required=True, help="the private seats directory (mode 0700)")
    p.add_argument("--estate", required=True, help="the estate this seats directory belongs to")
    sub = p.add_subparsers(dest="cmd", required=True)

    def gw(sp, need_url: bool):
        sp.add_argument("--registry", required=True, help="the gateway's client credentials file")
        sp.add_argument("--reload-unit", help="systemd user unit to send SIGHUP after a change")
        if need_url:
            sp.add_argument("--gateway-url", required=True)
            sp.add_argument("--days", type=int, default=365)
            sp.add_argument("--invite-out", help="where to write the invite (default: in the seats dir)")

    sub.add_parser("init", help="create the seats directory")
    s = sub.add_parser("add", help="add a seat and write its invite")
    s.add_argument("--owner", required=True)
    s.add_argument("--contact")
    s.add_argument("--place", required=True, choices=PLACES)
    gw(s, True)
    sub.add_parser("list", help="list seats")
    s = sub.add_parser("rotate", help="issue a new credential and revoke the old one")
    s.add_argument("client_id")
    gw(s, True)
    s = sub.add_parser("remove", help="revoke the credential and mark the seat removed")
    s.add_argument("client_id")
    gw(s, False)
    s = sub.add_parser("usage", help="show usage per seat from a saved /admin/clients/usage response")
    s.add_argument("--json", required=True, metavar="FILE")
    return p


def main(argv: list[str] | None = None) -> int:
    a = _build().parse_args(argv)
    handler = {"init": _cmd_init, "add": _cmd_add, "list": _cmd_list, "rotate": _cmd_rotate,
               "remove": _cmd_remove, "usage": _cmd_usage}[a.cmd]
    try:
        if getattr(a, "days", 1) < 1:
            raise SeatError("--days must be at least 1")
        return handler(a)
    except (SeatError, ValueError) as e:
        print(f"sksidekick-seat: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
