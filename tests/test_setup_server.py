from __future__ import annotations

import http.client
import json
import threading

import pytest

from sksidekick.home import Home
from sksidekick.setup import server, state

TOKEN = "tok-for-tests"


@pytest.fixture(autouse=True)
def _plain_tier_step(monkeypatch):
    """These tests use `tier` as a stand-in first step; the real handler has its own tests."""
    monkeypatch.setitem(server.ROUTES, ("tier", None), lambda home, body: {})


@pytest.fixture
def running(bare_home: Home):
    srv = server.make_server(bare_home, "127.0.0.1", 0, TOKEN)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield bare_home, srv.server_address[1]
    srv.shutdown()
    srv.server_close()


def call(port, method, path, body=None, cookie=None, header=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    headers = {"Content-Type": "application/json"}
    if cookie:
        headers["Cookie"] = f"setup_token={cookie}"
    if header:
        headers["X-Setup-Token"] = header
    c.request(method, path, body=json.dumps(body or {}) if method == "POST" else None, headers=headers)
    r = c.getresponse()
    data = r.read()
    out = (r.status, dict(r.getheaders()), data)
    c.close()
    return out


def test_without_token_everything_is_403(running):
    home, port = running
    for method, path in (("GET", "/"), ("GET", "/api/state"), ("POST", "/api/tier")):
        assert call(port, method, path)[0] == 403
        assert call(port, method, path, cookie="wrong", header="wrong")[0] == 403
    assert call(port, "GET", "/?t=wrong")[0] == 403
    assert not (home.state_dir / "setup.json").exists()


def test_token_cookie_then_header(running):
    home, port = running
    status, headers, _ = call(port, "GET", f"/?t={TOKEN}")
    assert status == 200
    cookie = headers["Set-Cookie"]
    assert f"setup_token={TOKEN}" in cookie and "HttpOnly" in cookie and "SameSite=Strict" in cookie
    assert headers["Cache-Control"] == "no-store"
    assert headers["Content-Security-Policy"] == "default-src 'self'"
    assert call(port, "POST", "/api/tier", cookie=TOKEN)[0] == 403
    assert call(port, "POST", "/api/tier", header=TOKEN)[0] == 403
    assert call(port, "POST", "/api/tier", cookie=TOKEN, header=TOKEN)[0] == 200
    status, _, data = call(port, "GET", "/api/state", cookie=TOKEN)
    assert status == 200 and json.loads(data)["next"] == "channels"


def test_steps_in_order(running):
    home, port = running
    status, _, data = call(port, "POST", "/api/channels", cookie=TOKEN, header=TOKEN)
    assert status == 409
    assert json.loads(data) == {"error": "finish the earlier step first"}
    assert not (home.state_dir / "setup.json").exists()


def test_concurrent_same_step_one_wins(running):
    _, port = running
    results = []

    def go():
        results.append(call(port, "POST", "/api/tier", cookie=TOKEN, header=TOKEN)[0])

    ts = [threading.Thread(target=go) for _ in range(2)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(results) == [200, 409]


def test_unknown_routes_are_404_json(running):
    _, port = running
    for path in ("/api/nope", "/api/tier/nope/more", "/api/phrase/new"):
        status, _, data = call(port, "POST", path, cookie=TOKEN, header=TOKEN)
        assert status == 404 and "error" in json.loads(data)


def test_registered_action_route(running):
    home, port = running
    for s in ("tier", "channels", "personality"):
        assert call(port, "POST", f"/api/{s}", cookie=TOKEN, header=TOKEN)[0] == 200
    server.ROUTES[("phrase", "new")] = lambda home, body: {"ok": True}
    try:
        status, _, data = call(port, "POST", "/api/phrase/new", cookie=TOKEN, header=TOKEN)
        assert status == 200 and json.loads(data) == {"ok": True}
        assert state.load(home)["next"] == "phrase"  # an action does not advance the step
    finally:
        del server.ROUTES[("phrase", "new")]


def test_refuses_a_set_up_home(bare_home, capsys):
    bare_home.new_instance()
    assert not (bare_home.state_dir / "setup.json").exists()
    assert server.main_setup(bare_home, "127.0.0.1", 0) == 1
    assert "already set up" in capsys.readouterr().err


def test_binds_loopback_by_default(bare_home):
    srv = server.make_server(bare_home, "127.0.0.1", 0, TOKEN)
    try:
        assert srv.server_address[0] == "127.0.0.1"
    finally:
        srv.server_close()


def test_state_machine_and_atomic_file(bare_home):
    assert state.Steps == ("tier", "channels", "personality", "phrase", "backup", "done")
    assert state.load(bare_home)["next"] == "tier"
    with pytest.raises(state.OutOfOrder):
        state.advance(bare_home, "phrase")
    for s in state.Steps:
        state.advance(bare_home, s)
    assert state.load(bare_home)["next"] is None
    assert not list(bare_home.state_dir.glob("*.tmp"))


def test_hermes_dir_helper_is_0700(bare_home):
    d = state.ensure_hermes_dir(bare_home)
    assert d == bare_home.hermes_dir and (d.stat().st_mode & 0o777) == 0o700


def test_done_shuts_the_server_down(bare_home, capsys):
    done = {}
    t = threading.Thread(target=lambda: done.setdefault("rc", server.main_setup(bare_home, "127.0.0.1", 0)))
    t.start()
    import re, time
    for _ in range(100):
        m = re.search(r"Open http://127\.0\.0\.1:(\d+)/\?t=(\S+)", capsys.readouterr().out)
        if m:
            break
        time.sleep(0.05)
    port, tok = int(m.group(1)), m.group(2)
    for s in state.Steps:
        assert call(port, "POST", f"/api/{s}", cookie=tok, header=tok)[0] == 200
    t.join(5)
    assert done["rc"] == 0


def test_token_never_reaches_stderr_and_referrer_policy(running, capfd):
    _, port = running
    _, headers, _ = call(port, "GET", f"/?t={TOKEN}")
    call(port, "GET", f"/?t=wrong-{TOKEN}")
    assert headers["Referrer-Policy"] == "no-referrer"
    err = capfd.readouterr().err
    assert TOKEN not in err and "?t=" not in err
    assert "GET /" in err


def test_handler_exception_is_a_bare_500(running, capfd):
    home, port = running
    def boom(home, body):
        raise RuntimeError("secret-phrase-detail")
    server.ROUTES[("tier", None)] = boom
    try:
        status, headers, data = call(port, "POST", "/api/tier", cookie=TOKEN, header=TOKEN)
    finally:
        del server.ROUTES[("tier", None)]
    assert status == 500 and json.loads(data) == {"error": "internal error"}
    assert headers["Referrer-Policy"] == "no-referrer"
    out = capfd.readouterr()
    assert "secret-phrase-detail" not in out.err + out.out and "Traceback" not in out.err
    assert state.load(home)["next"] == "tier"


def test_setup_file_is_0600(bare_home):
    state.advance(bare_home, "tier")
    assert ((bare_home.state_dir / "setup.json").stat().st_mode & 0o777) == 0o600


def _raw(port, data: bytes) -> bytes:
    import socket
    s = socket.create_connection(("127.0.0.1", port), timeout=5)
    s.sendall(data)
    out = b""
    try:
        while chunk := s.recv(4096):
            out += chunk
    except socket.timeout:
        pass
    s.close()
    return out


@pytest.mark.parametrize("line", [f"GET/?t={TOKEN}", "BADLINE", "GET", f"GET /?t={TOKEN} HTTP/1.1 extra junk"])
def test_malformed_request_line_is_safe(running, capfd, line):
    _, port = running
    resp = _raw(port, line.encode() + b"\r\n\r\n")
    assert resp.startswith(b"HTTP/1.") or b"error" in resp.lower()
    assert b"Referrer-Policy: no-referrer" in resp and b"Content-Security-Policy" in resp
    err = capfd.readouterr().err
    assert TOKEN not in err and "Traceback" not in err


def test_put_gets_json_with_security_headers(running):
    _, port = running
    status, headers, data = call(port, "PUT", "/api/tier")
    assert status == 403
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request("PUT", "/api/tier", headers={"X-Setup-Token": TOKEN})
    r = c.getresponse()
    body = r.read()
    assert r.status == 405 and "error" in json.loads(body)
    assert r.getheader("Referrer-Policy") == "no-referrer" and r.getheader("Cache-Control") == "no-store"
    assert r.getheader("Content-Security-Policy") == "default-src 'self'"
    c.close()
    assert headers["Referrer-Policy"] == "no-referrer"
