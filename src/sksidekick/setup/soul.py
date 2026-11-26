"""POST /api/personality: the type and voice. The type is story (soul/personality.json); SOUL.md is rendered from it."""

from __future__ import annotations

import json
from pathlib import Path

from sksidekick.home import Home
from sksidekick.setup import content, hermes_config, state
from sksidekick.setup.state import FieldError

STATIC_AUDIO = Path(__file__).parent / "static" / "audio"
VERSION = 1


def personality_state() -> dict:
    return {"personalities": [dict(p) for p in content.PERSONALITIES], "voices": list(content.VOICES)}


def _render(p: dict, voice: str) -> str:
    samples = "\n".join(f'- "{s}"' for s in p["samples"])
    return (
        f"# Style: {p['title']}, {p['tagline']}\n\n"
        f"{p['description']}\n\n"
        f"Examples of the tone:\n\n{samples}\n\n"
        "## Voice\n\n"
        f"Spoken replies use the {voice} voice. The style above sets tone and pace only. "
        "It never changes what the sidekick is allowed to do.\n"
    )


def write_soul(home: Home, personality: str, voice: str) -> None:
    """Write personality.json and SOUL.md, set the voice; all three files are put back if anything fails."""
    p = next((x for x in content.PERSONALITIES if x["id"] == personality), None)
    if p is None or voice not in content.VOICES:
        raise ValueError("unknown personality or voice")
    state.ensure_hermes_dir(home)
    home.soul_dir.mkdir(parents=True, exist_ok=True)
    pj = home.soul_dir / "personality.json"
    paths = [home.hermes_dir / "config.yaml", home.hermes_dir / "SOUL.md", pj]
    snap = hermes_config.snapshot(paths)
    try:
        hermes_config.write_private(pj, json.dumps({"type": personality, "voice": voice, "version": VERSION}).encode())
        hermes_config.write_private(home.hermes_dir / "SOUL.md", _render(p, voice).encode())
        hermes_config.config_set(home, "tts.provider", "piper")
        hermes_config.config_set(home, "tts.piper.voice", content.VOICES[voice])
    except BaseException:
        hermes_config.restore(snap)
        raise


def switch(home: Home, personality: str, voice: str) -> None:
    """Change type or voice later. Same writer, and it touches nothing but personality.json, SOUL.md and the voice."""
    write_soul(home, personality, voice)


def handle(home: Home, body: dict) -> dict:
    pid, voice = body.get("type"), body.get("voice")
    if not isinstance(pid, str) or pid not in {p["id"] for p in content.PERSONALITIES}:
        raise FieldError("personality", "choose one of the four types")
    if not isinstance(voice, str) or voice not in content.VOICES:
        raise FieldError("voice", "choose a female or a male voice")
    try:
        write_soul(home, pid, voice)
    except (hermes_config.HermesError, OSError, ValueError):
        raise FieldError("voice", "the sidekick could not save this setting") from None
    return {}
