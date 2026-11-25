from __future__ import annotations

import http.client
import json
import re
import stat

import pytest

from sksidekick.setup import content, soul, state
from test_setup_server import TOKEN, call
from test_setup_tier import post

SAMPLES = {
    "companion": [
        "Morning. You said yesterday was rough, so I kept today light.",
        "Your mom's birthday is Sunday. Want me to remind you Friday so there is time for a card?",
        "No rush on this one. I'm here when you want to pick it back up.",
    ],
    "assistant": [
        "Three things today. Reply to Maria by noon, the invoice is due Friday, your flight is confirmed.",
        "I drafted the reply. Say send, or tell me what to change.",
        "Done. Next up is the insurance form, about ten minutes.",
    ],
    "creative": [
        "Okay, wild idea. What if the party is a treasure hunt built on inside jokes?",
        "I have five versions of that headline. Two are terrible on purpose.",
        "Want the safe option or the fun one? I can do both.",
    ],
    "mentor": [
        "Let's slow down for a second. What outcome do you actually want?",
        "Say it in one sentence, and we'll write back from there.",
        "You've done harder things than this. What worked last time?",
    ],
}


@pytest.fixture
def at_personality(running):
    home, port = running
    state.advance(home, "tier")
    state.advance(home, "channels")
    return home, port


def test_four_types_exactly():
    assert [p["id"] for p in content.PERSONALITIES] == ["companion", "assistant", "creative", "mentor"]
    for p in content.PERSONALITIES:
        assert p["samples"] == SAMPLES[p["id"]]
        assert p["audio"] == {v: f"static/audio/{p['id']}-{v}.mp3" for v in ("female", "male")}


def test_soul_written_and_is_story(bare_home, fake_hermes):
    soul.write_soul(bare_home, "mentor", "male")
    assert "soul/personality.json" in {p.as_posix() for p in bare_home.story_paths()}
    assert json.loads((bare_home.soul_dir / "personality.json").read_text()) == {"type": "mentor", "voice": "male", "version": 1}
    assert stat.S_IMODE((bare_home.soul_dir / "personality.json").stat().st_mode) == 0o600
    text = (bare_home.hermes_dir / "SOUL.md").read_text()
    assert "calm and patient" in text
    assert all(line in text for line in SAMPLES["mentor"])
    assert "## Voice" in text and "never changes what the sidekick is allowed to do" in text
    assert not re.search("[\\u2013\\u2014]", text)


def test_voice_sets_piper(bare_home, fake_hermes):
    soul.write_soul(bare_home, "mentor", "male")
    sets = [c["argv"] for c in fake_hermes.calls() if c["argv"][:2] == ["config", "set"]]
    assert ["config", "set", "tts.provider", "piper"] in sets
    assert ["config", "set", "tts.piper.voice", "en_US-ryan-medium"] in sets


def test_switch_keeps_memory(bare_home, fake_hermes):
    mem = bare_home.memory_dir / "long-term"
    mem.mkdir(parents=True)
    (mem / "m.json").write_text('{"t": 1}')
    soul.write_soul(bare_home, "companion", "female")
    before = {p: (bare_home.root / p).read_bytes() for p in bare_home.story_paths() if p.parts[0] != "hermes" and p.name != "personality.json"}
    soul.switch(bare_home, "creative", "female")
    after = {p: (bare_home.root / p).read_bytes() for p in bare_home.story_paths() if p.parts[0] != "hermes" and p.name != "personality.json"}
    assert before == after and (mem / "m.json").read_text() == '{"t": 1}'
    assert json.loads((bare_home.soul_dir / "personality.json").read_text())["type"] == "creative"


def test_audio_served(running):
    home, port = running
    files = sorted((soul.STATIC_AUDIO).glob("*.mp3"))
    assert len(files) == 8 and all(f.stat().st_size < 100_000 for f in files)
    status, headers, data = call(port, "GET", "/static/audio/companion-female.mp3", cookie=TOKEN)
    assert status == 200 and headers["Content-Type"] == "audio/mpeg"
    assert data == (soul.STATIC_AUDIO / "companion-female.mp3").read_bytes()


def test_post_advances_and_state_lists(at_personality, fake_hermes):
    home, port = at_personality
    state_before = json.loads(call(port, "GET", "/api/state", cookie=TOKEN)[2])
    assert [p["id"] for p in state_before["personalities"]] == ["companion", "assistant", "creative", "mentor"]
    assert state_before["voices"] == ["female", "male"]
    status, _ = post(port, "/api/personality", {"type": "assistant", "voice": "female"})
    assert status == 200
    assert json.loads(call(port, "GET", "/api/state", cookie=TOKEN)[2])["completed"] == ["tier", "channels", "personality"]


@pytest.mark.parametrize("body,field", [
    ({"type": "nope", "voice": "male"}, "personality"),
    ({"type": "mentor", "voice": "robot"}, "voice"),
    ({"voice": "male"}, "personality"),
    ({"type": "mentor"}, "voice"),
    ({"type": ["mentor"], "voice": "male"}, "personality"),
])
def test_unknown_refused(at_personality, fake_hermes, body, field):
    home, port = at_personality
    status, out = post(port, "/api/personality", body)
    assert status == 422 and out["field"] == field
    assert not (home.soul_dir / "personality.json").exists() and not (home.hermes_dir / "SOUL.md").exists()
    assert state.load(home)["next"] == "personality"


def test_failed_set_restores_everything(at_personality, fake_hermes, monkeypatch):
    home, port = at_personality
    soul.write_soul(home, "companion", "female")
    paths = [home.hermes_dir / "config.yaml", home.hermes_dir / "SOUL.md", home.soul_dir / "personality.json"]
    before = [p.read_bytes() for p in paths]
    fake_hermes.store.with_name(fake_hermes.store.name + ".n").write_text("0")
    for n in ("1", "2"):
        fake_hermes.store.with_name(fake_hermes.store.name + ".n").write_text("0")
        monkeypatch.setenv("FAKE_HERMES_FAIL_SET", n)
        status, out = post(port, "/api/personality", {"type": "mentor", "voice": "male"})
        assert status == 422 and out["field"] == "voice" and "sk-secret" not in json.dumps(out)
        assert [p.read_bytes() for p in paths] == before
        assert state.load(home)["next"] == "personality"


def test_failed_first_write_leaves_nothing(at_personality, fake_hermes, monkeypatch):
    home, port = at_personality
    monkeypatch.setenv("FAKE_HERMES_FAIL_SET", "2")
    status, _ = post(port, "/api/personality", {"type": "mentor", "voice": "male"})
    assert status == 422
    assert not (home.soul_dir / "personality.json").exists() and not (home.hermes_dir / "SOUL.md").exists()
    assert not (home.hermes_dir / "config.yaml").exists() or json.loads((home.hermes_dir / "config.yaml").read_text()) == {}


def _get(port, path, rng):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request("GET", path, headers={"Cookie": f"setup_token={TOKEN}", "Range": rng})
    r = c.getresponse()
    out = (r.status, dict(r.getheaders()), r.read())
    c.close()
    return out


MP3 = "/static/audio/companion-female.mp3"


def test_accept_ranges_on_200(running):
    _, port = running
    status, headers, _ = call(port, "GET", MP3, cookie=TOKEN)
    assert status == 200 and headers["Accept-Ranges"] == "bytes"


def test_range_206(running):
    _, port = running
    full = (soul.STATIC_AUDIO / "companion-female.mp3").read_bytes()
    status, headers, data = _get(port, MP3, "bytes=0-99")
    assert status == 206 and data == full[:100] and headers["Content-Length"] == "100"
    assert headers["Content-Range"] == f"bytes 0-99/{len(full)}" and headers["Content-Type"] == "audio/mpeg"
    assert headers["Referrer-Policy"] == "no-referrer"
    status, headers, data = _get(port, MP3, "bytes=-50")
    assert status == 206 and data == full[-50:] and headers["Content-Range"] == f"bytes {len(full) - 50}-{len(full) - 1}/{len(full)}"
    status, _, data = _get(port, MP3, f"bytes={len(full) - 10}-")
    assert status == 206 and data == full[-10:]
    assert _get(port, MP3, "bytes=0-1,5-9")[0] == 200


def test_range_416(running):
    _, port = running
    size = (soul.STATIC_AUDIO / "companion-female.mp3").stat().st_size
    status, headers, _ = _get(port, MP3, f"bytes={size}-")
    assert status == 416 and headers["Content-Range"] == f"bytes */{size}"
    assert _get(port, MP3, "bytes=-0")[0] == 416


def test_range_still_needs_token(running):
    _, port = running
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request("GET", MP3, headers={"Range": "bytes=0-9"})
    assert c.getresponse().status == 403
