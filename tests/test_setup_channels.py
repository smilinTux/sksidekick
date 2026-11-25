from __future__ import annotations

import json

import pytest

from sksidekick.setup import content, state
from test_setup_server import TOKEN, call
from test_setup_tier import post


@pytest.fixture
def at_channels(running):
    home, port = running
    state.advance(home, "tier")
    return home, port


def _state(port):
    return json.loads(call(port, "GET", "/api/state", cookie=TOKEN)[2])


def _plugin(home):
    (home.hermes_dir / "plugins" / "nctalk").mkdir(parents=True)


def test_telegram_token_written(at_channels):
    home, port = at_channels
    status, _ = post(port, "/api/channels", {"channels": [{"id": "telegram", "fields": {"TELEGRAM_BOT_TOKEN": "123:abc"}}]})
    assert status == 200
    assert (home.hermes_dir / ".env").read_text() == "TELEGRAM_BOT_TOKEN=123:abc\n"
    assert _state(port)["completed"] == ["tier", "channels"]


def test_unknown_field_refused(at_channels):
    home, port = at_channels
    status, body = post(port, "/api/channels", {"channels": [{"id": "telegram", "fields": {"TELEGRAM_BOT_TOKEN": "1:a", "OTHER": "x"}}]})
    assert status == 422 and body["field"] == "OTHER"
    assert not (home.hermes_dir / ".env").exists()
    assert _state(port)["completed"] == ["tier"]


def test_zero_channels_ok(at_channels):
    home, port = at_channels
    home.hermes_dir.mkdir(parents=True)
    (home.hermes_dir / ".env").write_text("A=1\n")
    status, _ = post(port, "/api/channels", {"channels": []})
    assert status == 200
    assert (home.hermes_dir / ".env").read_text() == "A=1\n"
    assert _state(port)["completed"] == ["tier", "channels"]


def test_channel_labels_present(at_channels):
    home, port = at_channels
    chans = {c["id"]: c for c in _state(port)["channels"]}
    assert chans["nextcloud-talk"]["label"] == "Stays inside our own server."
    assert chans["telegram"]["label"] == "Telegram also sees the conversation."
    assert chans["telegram"]["enabled"] is True and chans["telegram"]["configured"] is False


def test_nextcloud_talk_fields_pinned():
    nc = next(c for c in content.CHANNELS if c["id"] == "nextcloud-talk")
    assert nc["fields"] == ["NEXTCLOUD_TALK_SERVER_URL", "NEXTCLOUD_TALK_USERNAME", "NEXTCLOUD_TALK_APP_PASSWORD"]
    assert tuple(nc["fields"]) == content.NCTALK_ENV_NAMES


def test_nextcloud_talk_disabled_without_plugin(at_channels):
    home, port = at_channels
    nc = next(c for c in _state(port)["channels"] if c["id"] == "nextcloud-talk")
    assert nc["enabled"] is False and nc["disabled_reason"] == "Comes with an invite to our Nextcloud"
    fields = dict.fromkeys(content.NCTALK_ENV_NAMES, "x")
    status, body = post(port, "/api/channels", {"channels": [{"id": "nextcloud-talk", "fields": fields}]})
    assert status == 422 and body["field"] == "channels"
    assert not (home.hermes_dir / ".env").exists()


def test_nextcloud_talk_enabled_with_plugin(at_channels):
    home, port = at_channels
    _plugin(home)
    nc = next(c for c in _state(port)["channels"] if c["id"] == "nextcloud-talk")
    assert nc["enabled"] is True and "disabled_reason" not in nc
    fields = dict(zip(content.NCTALK_ENV_NAMES, ["https://nc.example", "bot", "app-pass-secret"]))
    status, _ = post(port, "/api/channels", {"channels": [{"id": "nextcloud-talk", "fields": fields}]})
    assert status == 200
    lines = (home.hermes_dir / ".env").read_text().splitlines()
    assert "NEXTCLOUD_TALK_APP_PASSWORD=app-pass-secret" in lines and len(lines) == 3


def test_secrets_never_in_state(at_channels):
    home, port = at_channels
    post(port, "/api/channels", {"channels": [{"id": "telegram", "fields": {"TELEGRAM_BOT_TOKEN": "123:topsecret"}}]})
    raw = call(port, "GET", "/api/state", cookie=TOKEN)[2]
    assert b"topsecret" not in raw
    assert next(c for c in json.loads(raw)["channels"] if c["id"] == "telegram")["configured"] is True


def test_all_or_nothing_env_untouched(at_channels):
    home, port = at_channels
    _plugin(home)
    (home.hermes_dir / ".env").write_text("A=1\n")
    before = (home.hermes_dir / ".env").read_bytes()
    good = {"id": "telegram", "fields": {"TELEGRAM_BOT_TOKEN": "1:a"}}
    for bad in ("tok\nINJECT=1", "tok\r", "a\0b", "", "   "):
        status, body = post(port, "/api/channels", {"channels": [good, {"id": "nextcloud-talk", "fields": {
            "NEXTCLOUD_TALK_SERVER_URL": "https://nc", "NEXTCLOUD_TALK_USERNAME": "u", "NEXTCLOUD_TALK_APP_PASSWORD": bad}}]})
        assert status == 422 and body["field"] == "NEXTCLOUD_TALK_APP_PASSWORD"
        assert bad.strip() == "" or bad not in json.dumps(body)
        assert (home.hermes_dir / ".env").read_bytes() == before
    assert _state(port)["completed"] == ["tier"]


def test_missing_field_and_bad_shapes_422(at_channels):
    home, port = at_channels
    _plugin(home)
    s, b = post(port, "/api/channels", {"channels": [{"id": "nextcloud-talk", "fields": {"NEXTCLOUD_TALK_SERVER_URL": "https://nc"}}]})
    assert s == 422 and b["field"] == "NEXTCLOUD_TALK_USERNAME"
    for chans in ("telegram", [{"id": "nope", "fields": {}}], [{"id": "telegram"}], [{"id": "telegram", "fields": {"TELEGRAM_BOT_TOKEN": 5}}], ["x"]):
        assert post(port, "/api/channels", {"channels": chans})[0] == 422
    assert not (home.hermes_dir / ".env").exists()


def test_duplicate_channel_ids_422(at_channels):
    home, port = at_channels
    t = {"id": "telegram", "fields": {"TELEGRAM_BOT_TOKEN": "1:a"}}
    status, body = post(port, "/api/channels", {"channels": [t, t]})
    assert status == 422 and body["field"] == "channels"
    assert not (home.hermes_dir / ".env").exists()
