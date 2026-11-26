"""POST /api/tier: validate the model choice, check it answers, then write it."""

from __future__ import annotations

import http.client
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from sksidekick.home import Home
from sksidekick.setup import content, hermes_config, seat
from sksidekick.setup.state import FieldError

DEFAULT_SHARED_MODEL = "sk-default"
_BAD = "\n\r\0"


def tiers_state(home: Home) -> dict:
    """What the page needs to draw the three options. The gateway token is never included."""
    inv = seat.load(home)
    out = {t["id"]: dict(t) for t in content.TIERS}
    out["shared"]["enabled"] = inv is not None
    if inv is not None:
        out["shared"]["host"] = urlsplit(inv.gateway_url).hostname or ""
    else:
        out["shared"]["disabled_label"] = content.SHARED_DISABLED
    return out


def _text(body: dict, field: str) -> str:
    v = body.get(field)
    return v.strip() if isinstance(v, str) else ""


def _check_url(value: str) -> None:
    u = urlsplit(value)
    if u.scheme not in ("http", "https") or not u.hostname or any(c in value for c in _BAD):
        raise FieldError("base_url", "enter an address starting with http:// or https://")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None  # the Bearer key must never follow a redirect to another host


_OPENER = urllib.request.build_opener(_NoRedirect)


def _reachable(base_url: str, api_key: str | None) -> None:
    req = urllib.request.Request(base_url.rstrip("/") + "/models")
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")
    try:
        with _OPENER.open(req, timeout=5) as r:
            if not 200 <= r.status < 300:
                raise OSError
    except urllib.error.HTTPError as e:
        if api_key and e.code in (401, 403):  # it answered, and said no to the key
            raise FieldError("api_key", "the provider refused that key") from None
        raise FieldError("base_url", "nothing answered at that address") from None
    except (OSError, ValueError, http.client.HTTPException, urllib.error.URLError):  # other HTTPError (3xx) too
        raise FieldError("base_url", "nothing answered at that address") from None


REVOKED = "this invite was turned off or has expired; ask for a new one"


def _gateway_reachable(inv) -> None:
    """Ask the gateway for its models with the seat's own headers; it meters us under the client id."""
    if seat.expired(inv):
        raise FieldError("tier", REVOKED)
    req = urllib.request.Request(inv.gateway_url.rstrip("/") + "/models")
    req.add_header("Authorization", f"Bearer {inv.token}")
    req.add_header("x-agent-id", inv.agent_id)
    req.add_header("x-sk-client-id", inv.client_id)
    req.add_header("x-sk-credential-revision", inv.credential_revision)
    try:
        with _OPENER.open(req, timeout=5) as r:
            if not 200 <= r.status < 300:
                raise OSError
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):  # client_auth_denied: revoked, expired or unknown credential
            raise FieldError("tier", REVOKED) from None
        raise FieldError("tier", "nothing answered at the gateway") from None
    except (OSError, ValueError, http.client.HTTPException, urllib.error.URLError):
        raise FieldError("tier", "nothing answered at the gateway") from None


def handle(home: Home, body: dict) -> dict:
    tier = body.get("tier")
    if tier not in {t["id"] for t in content.TIERS}:
        raise FieldError("tier", "choose one of the three options")
    if tier == "shared":
        inv = seat.load(home)
        if inv is None:
            raise FieldError("tier", content.SHARED_DISABLED)
        model = _text(body, "model") or DEFAULT_SHARED_MODEL
        if any(c in model for c in _BAD) or model.startswith("-"):
            raise FieldError("model", "enter a model name")
        _gateway_reachable(inv)
        try:
            hermes_config.set_gateway(home, inv, model)
        except (hermes_config.HermesError, ValueError):
            raise FieldError("model", "the sidekick could not save this setting") from None
        return {}
    else:
        base_url, model = _text(body, "base_url"), _text(body, "model")
        api_key = _text(body, "api_key") or None
        _check_url(base_url)
        if not model or model.startswith("-") or any(c in model for c in _BAD):
            raise FieldError("model", "enter a model name")
        if tier == "own" and not api_key:
            raise FieldError("api_key", "enter your API key")
        if api_key and any(c in api_key for c in _BAD):
            raise FieldError("api_key", "that key contains a line break or other characters it cannot hold")
        _reachable(base_url, api_key if tier == "own" else None)
    if any(c in model for c in _BAD):
        raise FieldError("model", "enter a model name")
    if model.startswith("-"):
        raise FieldError("model", "enter a model name")
    try:
        hermes_config.set_model(home, base_url=base_url, model=model, api_key=api_key)
    except (hermes_config.HermesError, ValueError):
        raise FieldError("model", "the sidekick could not save this setting") from None
    return {}
