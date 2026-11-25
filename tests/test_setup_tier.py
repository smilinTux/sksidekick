from __future__ import annotations

import json
import stat
import subprocess
import threading

import pytest

from sksidekick.home import Home
from sksidekick.setup import hermes_config, server
from test_setup_server import TOKEN, call


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


def test_failure_on_second_set_restores_everything(running, fake_hermes, monkeypatch):
    home, port = running
    hermes_config.set_model(home, base_url="http://old/v1", model="old", api_key="sk-old")
    cfg = home.hermes_dir / "config.yaml"
    old_store = cfg.read_bytes()
    old_env = (home.hermes_dir / ".env").read_text()
    n = int((fake_hermes.store.parent / "hermes-config.json.n").read_text())
    monkeypatch.setenv("FAKE_HERMES_FAIL_SET", str(n + 2))
    import http.server

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *a):
            pass

    up = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=up.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{up.server_address[1]}/v1"
        status, body = post(port, "/api/tier", {"tier": "own", "base_url": base, "model": "new", "api_key": "sk-new"})
    finally:
        up.shutdown()
        up.server_close()
    assert status == 422 and body["field"] == "model"
    assert "sk-" not in json.dumps(body)
    assert cfg.read_bytes() == old_store
    assert (home.hermes_dir / ".env").read_text() == old_env
    assert json.loads(call(port, "GET", "/api/state", cookie=TOKEN)[2])["completed"] == []


def test_non_http_answer_is_unreachable(running, fake_hermes):
    import socket

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)

    def serve():
        c, _ = s.accept()
        c.sendall(b"SSH-2.0-OpenSSH_9\r\n")
        c.close()

    threading.Thread(target=serve, daemon=True).start()
    home, port = running
    status, body = post(port, "/api/tier", {"tier": "private", "base_url": f"http://127.0.0.1:{s.getsockname()[1]}/v1", "model": "m"})
    s.close()
    assert status == 422 and body["field"] == "base_url"
    assert fake_hermes.calls() == []


def test_redirect_is_unreachable_and_key_not_forwarded(running, fake_hermes):
    import http.server

    hits = []

    class Target(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.headers.get("Authorization"))
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *a):
            pass

    tgt = http.server.HTTPServer(("127.0.0.1", 0), Target)

    class Redir(Target):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{tgt.server_address[1]}/models")
            self.send_header("Content-Length", "0")
            self.end_headers()

    red = http.server.HTTPServer(("127.0.0.1", 0), Redir)
    for srv in (tgt, red):
        threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        home, port = running
        base = f"http://127.0.0.1:{red.server_address[1]}/v1"
        status, body = post(port, "/api/tier", {"tier": "own", "base_url": base, "model": "m", "api_key": "sk-k"})
    finally:
        for srv in (tgt, red):
            srv.shutdown()
            srv.server_close()
    assert status == 422 and body["field"] == "base_url" and hits == []
    assert fake_hermes.calls() == []


def test_model_starting_with_dash_refused(running, fake_hermes):
    home, port = running
    status, body = post(port, "/api/tier", {"tier": "private", "base_url": "http://127.0.0.1:9/v1", "model": "--evil"})
    assert status == 422 and body["field"] == "model"
    assert fake_hermes.calls() == []


def test_first_run_failure_leaves_no_files(running, fake_hermes, monkeypatch):
    home, port = running
    monkeypatch.setenv("FAKE_HERMES_FAIL_SET", "2")
    import http.server

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *a):
            pass

    up = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=up.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{up.server_address[1]}/v1"
        status, body = post(port, "/api/tier", {"tier": "own", "base_url": base, "model": "m", "api_key": "sk-new"})
    finally:
        up.shutdown()
        up.server_close()
    assert status == 422 and body["field"] == "model"
    assert not (home.hermes_dir / "config.yaml").exists() and not (home.hermes_dir / ".env").exists()


def test_first_run_success_with_strict_fake(bare_home, fake_hermes):
    hermes_config.set_model(bare_home, base_url="http://127.0.0.1:1/v1", model="m", api_key=None)
    assert fake_hermes.get("model.default") == "m"


def test_restore_error_does_not_mask_original(bare_home, fake_hermes, monkeypatch):
    monkeypatch.setenv("FAKE_HERMES_FAIL_SET", "2")
    monkeypatch.setattr(hermes_config, "_write_private", lambda *a: (_ for _ in ()).throw(OSError("disk")))
    (bare_home.hermes_dir).mkdir(parents=True)
    (bare_home.hermes_dir / ".env").write_text("A=1\n")
    with pytest.raises(hermes_config.HermesError):
        hermes_config.set_model(bare_home, base_url="http://x/v1", model="m", api_key="k")
