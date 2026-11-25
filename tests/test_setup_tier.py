from __future__ import annotations

import json
import stat
import subprocess
import threading

import pytest

from sksidekick.home import Home
from sksidekick.setup import hermes_config, server
from test_setup_server import TOKEN, call


@pytest.fixture
def running(bare_home: Home):
    srv = server.make_server(bare_home, "127.0.0.1", 0, TOKEN)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield bare_home, srv.server_address[1]
    srv.shutdown()
    srv.server_close()


def post(port, path, body):
    status, _, data = call(port, "POST", path, body, cookie=TOKEN, header=TOKEN)
    return status, json.loads(data)


def test_private_tier_writes_model(bare_home, fake_hermes):
    hermes_config.set_model(bare_home, base_url="http://127.0.0.1:11434/v1", model="llama3.2", api_key=None)
    assert fake_hermes.get("model.base_url") == "http://127.0.0.1:11434/v1"
    assert fake_hermes.get("model.default") == "llama3.2"
    assert fake_hermes.get("model.provider") == "custom"
    assert all(c["hermes_home"] == str(bare_home.root / "hermes") for c in fake_hermes.calls())
    assert "CUSTOM_API_KEY" not in (bare_home.hermes_dir / ".env").read_text()


def test_own_tier_key_goes_to_env_0600(bare_home, fake_hermes):
    hermes_config.set_model(bare_home, base_url="https://api.example.com/v1", model="m", api_key="sk-x")
    env = bare_home.hermes_dir / ".env"
    assert "CUSTOM_API_KEY=sk-x" in env.read_text().splitlines()
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    assert not any("sk-x" in json.dumps(c) for c in fake_hermes.calls())


def test_set_env_keeps_other_lines_and_replaces(bare_home):
    env = bare_home.hermes_dir
    env.mkdir(parents=True)
    (env / ".env").write_text("A=1\nCUSTOM_API_KEY=old\nB=2\n")
    hermes_config.set_env(bare_home, "CUSTOM_API_KEY", "new")
    assert (env / ".env").read_text().splitlines() == ["A=1", "CUSTOM_API_KEY=new", "B=2"]


def test_env_value_with_newline_refused(bare_home):
    hermes_config.set_env(bare_home, "A", "1")
    before = (bare_home.hermes_dir / ".env").read_text()
    for bad in ("a\nB=c", "a\rb", "a\0b"):
        with pytest.raises(ValueError):
            hermes_config.set_env(bare_home, "X", bad)
    assert (bare_home.hermes_dir / ".env").read_text() == before


def test_hermes_failure_hides_the_key(bare_home, monkeypatch):
    def boom(*a, **k):
        raise subprocess.CalledProcessError(1, a[0], output="sk-secret", stderr="sk-secret")

    monkeypatch.setattr(hermes_config.subprocess, "run", boom)
    with pytest.raises(hermes_config.HermesError) as e:
        hermes_config.set_model(bare_home, base_url="http://x/v1", model="m", api_key="sk-secret")
    assert "sk-secret" not in str(e.value)


def test_shared_disabled_without_seat(running, monkeypatch):
    home, port = running
    monkeypatch.delenv("SKSIDEKICK_GATEWAY_URL", raising=False)
    monkeypatch.delenv("SKSIDEKICK_GATEWAY_KEY", raising=False)
    _, _, data = call(port, "GET", "/api/state", cookie=TOKEN)
    assert json.loads(data)["tiers"]["shared"]["enabled"] is False
    monkeypatch.setenv("SKSIDEKICK_GATEWAY_URL", "https://gw.example/v1")
    monkeypatch.setenv("SKSIDEKICK_GATEWAY_KEY", "gw-secret")
    _, _, raw = call(port, "GET", "/api/state", cookie=TOKEN)
    shared = json.loads(raw)["tiers"]["shared"]
    assert shared["enabled"] is True and shared["base_url"] == "https://gw.example/v1" and shared["key_set"] is True
    assert b"gw-secret" not in raw


def test_shared_tier_posts_from_env(running, fake_hermes, monkeypatch):
    home, port = running
    monkeypatch.setenv("SKSIDEKICK_GATEWAY_URL", "https://gw.example/v1")
    monkeypatch.setenv("SKSIDEKICK_GATEWAY_KEY", "gw-secret")
    status, _ = post(port, "/api/tier", {"tier": "shared"})
    assert status == 200
    assert fake_hermes.get("model.base_url") == "https://gw.example/v1"
    assert "CUSTOM_API_KEY=gw-secret" in (home.hermes_dir / ".env").read_text().splitlines()


def test_shared_tier_refused_without_seat(running, fake_hermes, monkeypatch):
    home, port = running
    monkeypatch.delenv("SKSIDEKICK_GATEWAY_URL", raising=False)
    monkeypatch.delenv("SKSIDEKICK_GATEWAY_KEY", raising=False)
    status, body = post(port, "/api/tier", {"tier": "shared"})
    assert status == 422 and body["field"] == "tier"
    assert fake_hermes.calls() == []


def test_unreachable_model_is_a_field_error(running, fake_hermes):
    home, port = running
    status, body = post(port, "/api/tier", {"tier": "private", "base_url": "http://127.0.0.1:9/v1", "model": "m"})
    assert status == 422
    assert body == {"field": "base_url", "error": "nothing answered at that address"}
    assert fake_hermes.calls() == [] and not home.hermes_dir.exists()
    assert not (home.state_dir / "setup.json").exists()


def test_reachable_model_writes_and_advances(running, fake_hermes):
    import http.server

    seen = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            seen["path"], seen["auth"] = self.path, self.headers.get("Authorization")
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *a):
            pass

    up = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=up.serve_forever, daemon=True).start()
    try:
        home, port = running
        base = f"http://127.0.0.1:{up.server_address[1]}/v1"
        status, _ = post(port, "/api/tier", {"tier": "own", "base_url": base, "model": "m", "api_key": "sk-k"})
        assert status == 200
        assert seen == {"path": "/v1/models", "auth": "Bearer sk-k"}
        assert fake_hermes.get("model.default") == "m"
        assert (home.hermes_dir / ".env").read_text() == "CUSTOM_API_KEY=sk-k\n"
        assert json.loads(call(port, "GET", "/api/state", cookie=TOKEN)[2])["completed"] == ["tier"]
    finally:
        up.shutdown()
        up.server_close()


def test_bad_fields_are_422(running, fake_hermes):
    home, port = running
    assert post(port, "/api/tier", {"tier": "nope"})[1]["field"] == "tier"
    assert post(port, "/api/tier", {"tier": "private", "base_url": "ftp://x", "model": "m"})[1]["field"] == "base_url"
    assert post(port, "/api/tier", {"tier": "private", "base_url": "http://127.0.0.1:1/v1", "model": " "})[1]["field"] == "model"
    assert post(port, "/api/tier", {"tier": "own", "base_url": "http://127.0.0.1:1/v1", "model": "m"})[1]["field"] == "api_key"
    assert post(port, "/api/tier", {"tier": "own", "base_url": "http://127.0.0.1:1/v1", "model": "m", "api_key": "a\nb"})[1]["field"] == "api_key"
    assert fake_hermes.calls() == []
