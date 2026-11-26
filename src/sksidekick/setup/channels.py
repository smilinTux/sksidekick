"""POST /api/channels: write the chosen channels' credentials to hermes/.env, all or nothing."""

from __future__ import annotations

import os
from pathlib import Path

from sksidekick.home import Home
from sksidekick.setup import content, hermes_config
from sksidekick.setup.state import FieldError

_BAD = "\n\r\0"
# Where the nctalk plugin may be: installed into the sidekick's own Hermes home, or bundled with Hermes as a
# platform plugin (``<hermes>/plugins/platforms/<name>``; the image's Hermes is /opt/hermes, and
# ``HERMES_BUNDLED_PLUGINS`` overrides the bundled dir as in the fork). No image bundles it yet, so
# ``SKSIDEKICK_NCTALK_DIR`` names it outright for an image or install that keeps it somewhere else.
NCTALK_DIR_ENV = "SKSIDEKICK_NCTALK_DIR"
NCTALK_NAMES = ("nctalk", "nextcloud_talk", "nextcloud-talk")
BUNDLED_PLUGINS = Path("/opt/hermes/plugins")


def _plugin_dirs(home: Home) -> list[Path]:
    out = [home.hermes_dir / "plugins" / "nctalk"]
    override = os.environ.get(NCTALK_DIR_ENV)
    if override:
        out.append(Path(override))
    bundled = [Path(os.environ["HERMES_BUNDLED_PLUGINS"])] if os.environ.get("HERMES_BUNDLED_PLUGINS") else []
    for root in bundled + [BUNDLED_PLUGINS]:
        out += [root / "platforms" / n for n in NCTALK_NAMES]
    return out


def _plugin_present(home: Home) -> bool:
    return any(d.is_dir() for d in _plugin_dirs(home))


def _enabled(home: Home, channel_id: str) -> bool:
    return channel_id != "nextcloud-talk" or _plugin_present(home)


def channels_state(home: Home) -> list[dict]:
    """What the page needs to draw the channels. Secret values are never read into the answer."""
    have = hermes_config.env_keys(home)
    out = []
    for c in content.CHANNELS:
        item = {**c, "fields": list(c["fields"]), "enabled": _enabled(home, c["id"]),
                "help": {f: content.FIELD_HELP[f] for f in c["fields"] if f in content.FIELD_HELP}}
        if not item["enabled"]:
            item["disabled_reason"] = content.NCTALK_DISABLED
        item["configured"] = all(f in have for f in c["fields"])
        out.append(item)
    return out


def handle(home: Home, body: dict) -> dict:
    items = body.get("channels", [])
    if not isinstance(items, list):
        raise FieldError("channels", "send a list of channels")
    by_id = {c["id"]: c for c in content.CHANNELS}
    seen: set[str] = set()
    values: dict[str, str] = {}
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or item["id"] not in by_id:
            raise FieldError("channels", "choose from the channels offered")
        cid = item["id"]
        if cid in seen:
            raise FieldError("channels", "each channel can be listed once")
        seen.add(cid)
        if not _enabled(home, cid):
            raise FieldError("channels", content.NCTALK_DISABLED)
        fields = item.get("fields")
        if not isinstance(fields, dict):
            raise FieldError("channels", "send the details for each channel")
        allowed = by_id[cid]["fields"]
        for name in fields:
            if name not in allowed:
                raise FieldError(str(name) if isinstance(name, str) else "channels", "that is not a setting for this channel")
        for name in allowed:
            v = fields.get(name)
            if not isinstance(v, str):
                raise FieldError(name, "enter a value")
            if any(c in v for c in _BAD):
                raise FieldError(name, "that value contains a line break or other characters it cannot hold")
            if not v.strip():
                raise FieldError(name, "enter a value")
            if name in content.DIGIT_FIELDS and not (v.strip().isascii() and v.strip().isdigit()):
                raise FieldError(name, content.DIGIT_FIELDS[name])
            values[name] = v.strip()
    if values:
        try:
            hermes_config.set_envs(home, values)
        except (OSError, ValueError):
            raise FieldError("channels", "the sidekick could not save this setting") from None
    return {}
