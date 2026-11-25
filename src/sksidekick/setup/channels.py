"""POST /api/channels: write the chosen channels' credentials to hermes/.env, all or nothing."""

from __future__ import annotations

from sksidekick.home import Home
from sksidekick.setup import content, hermes_config
from sksidekick.setup.state import FieldError

_BAD = "\n\r\0"


def _plugin_present(home: Home) -> bool:
    return (home.hermes_dir / "plugins" / "nctalk").is_dir()


def _enabled(home: Home, channel_id: str) -> bool:
    return channel_id != "nextcloud-talk" or _plugin_present(home)


def channels_state(home: Home) -> list[dict]:
    """What the page needs to draw the channels. Secret values are never read into the answer."""
    have = hermes_config.env_keys(home)
    out = []
    for c in content.CHANNELS:
        item = {**c, "fields": list(c["fields"]), "enabled": _enabled(home, c["id"])}
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
            values[name] = v.strip()
    if values:
        try:
            hermes_config.set_envs(home, values)
        except (OSError, ValueError):
            raise FieldError("channels", "the sidekick could not save this setting") from None
    return {}
