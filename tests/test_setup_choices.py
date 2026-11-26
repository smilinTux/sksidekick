"""Completed steps are shown read-only with the choice made: /api/state carries the choices, never a secret."""

from __future__ import annotations

import json

import pytest

from sksidekick.setup import server, state
from test_setup_server import TOKEN, call, running  # noqa: F401 (fixture)

SECRETS = ("sk-secret-key", "pw-in-url", "123:bot-token", "app-pass-secret")


@pytest.fixture(autouse=True)
def _plain_steps(monkeypatch):
    """The handlers have their own tests; here only the router's record of the choice matters."""
    for step in ("tier", "channels", "personality"):
        monkeypatch.setitem(server.ROUTES, (step, None), lambda home, body: {})


def post(port, path, body):
    return call(port, "POST", path, body, cookie=TOKEN, header=TOKEN)


def test_state_carries_choices_and_no_secret(running):
    home, port = running
    assert post(port, "/api/tier", {"tier": "own", "base_url": "http://u:pw-in-url@h/v1", "model": "m",
                                    "api_key": "sk-secret-key"})[0] == 200
    assert post(port, "/api/channels", {"channels": [
        {"id": "telegram", "fields": {"TELEGRAM_BOT_TOKEN": "123:bot-token"}},
        {"id": "nextcloud-talk", "fields": {"NEXTCLOUD_TALK_APP_PASSWORD": "app-pass-secret"}}]})[0] == 200
    assert post(port, "/api/personality", {"type": "mentor", "voice": "female"})[0] == 200
    raw = call(port, "GET", "/api/state", cookie=TOKEN)[2]
    st = json.loads(raw)
    assert st["choices"] == {"tier": {"tier": "own"}, "channels": {"channels": ["telegram", "nextcloud-talk"]},
                             "personality": {"type": "mentor", "voice": "female"}}
    assert not any(s.encode() in raw for s in SECRETS)
    assert not any(s.encode() in (home.state_dir / "setup.json").read_bytes() for s in SECRETS)


def test_no_choice_recorded_for_a_refused_step(running, monkeypatch):
    home, port = running

    def refuse(home, body):
        raise state.FieldError("tier", "no")

    monkeypatch.setitem(server.ROUTES, ("tier", None), refuse)
    assert post(port, "/api/tier", {"tier": "private"})[0] == 422
    assert json.loads(call(port, "GET", "/api/state", cookie=TOKEN)[2])["choices"] == {}
    assert not (home.state_dir / "setup.json").exists()


def test_advance_keeps_choices(bare_home):
    state.advance(bare_home, "tier", {"tier": "private"})
    state.advance(bare_home, "channels", {"channels": []})
    assert state.load(bare_home)["choices"] == {"tier": {"tier": "private"}, "channels": {"channels": []}}
    assert state.load(bare_home)["completed"] == ["tier", "channels"]
