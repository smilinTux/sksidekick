"""POST /api/tier: validate the model choice, check it answers, then write it."""

from __future__ import annotations

import http.client
import os
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from sksidekick.home import Home
from sksidekick.setup import content, hermes_config
from sksidekick.setup.state import FieldError

DEFAULT_SHARED_MODEL = "sk-default"
_BAD = "\n\r\0"


def _seat() -> tuple[str, str]:
    return os.environ.get(content.GATEWAY_URL_ENV, ""), os.environ.get(content.GATEWAY_KEY_ENV, "")


def tiers_state() -> dict:
    """What the page needs to draw the three options. The gateway key is never included."""
    url, key = _seat()
    out = {t["id"]: dict(t) for t in content.TIERS}
    enabled = bool(url and key)
    out["shared"].update(enabled=enabled, key_set=bool(key))
    if enabled:
        out["shared"]["base_url"] = url
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
    except (OSError, ValueError, http.client.HTTPException, urllib.error.URLError):  # HTTPError (non-2xx, 3xx) is a URLError
        raise FieldError("base_url", "nothing answered at that address") from None


def handle(home: Home, body: dict) -> dict:
    tier = body.get("tier")
    if tier not in {t["id"] for t in content.TIERS}:
        raise FieldError("tier", "choose one of the three options")
    if tier == "shared":
        url, key = _seat()
        if not (url and key):
            raise FieldError("tier", content.SHARED_DISABLED)
        base_url, api_key = url, key
        model = _text(body, "model") or DEFAULT_SHARED_MODEL
        _check_url(base_url)
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
