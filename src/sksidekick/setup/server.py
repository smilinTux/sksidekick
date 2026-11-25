"""Token-guarded loopback server for the first-run setup page.

Request bodies are never logged (the recovery phrase will pass through later).
"""

from __future__ import annotations

import hmac
import json
import secrets
import sys
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from sksidekick.home import Home
from sksidekick.setup import state

COOKIE = "setup_token"
HEADER = "X-Setup-Token"
STATIC = Path(__file__).parent / "static"
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml"}
MAX_BODY = 1_000_000

# (step, action) -> handler(home, body) -> dict. action is None for the plain form.
# Later tasks register handlers here; a (step, None) handler runs before the step advances.
ROUTES: dict[tuple[str, str | None], object] = {}

_LOCK = threading.Lock()


def _same(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address) -> None:
        """Never print a traceback (it could carry a request body): one fixed line."""
        sys.stderr.write("setup server: request failed\n")
    home: Home
    token: str


class _Handler(BaseHTTPRequestHandler):
    server: _Server

    def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'self'")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _log(self, text: str) -> None:
        sys.stderr.write(f"setup server: {text}\n")

    def log_request(self, code="-", size="-") -> None:
        """Log method, path (no query: it carries the token) and status only."""
        path = str(getattr(self, "path", "-")).split("?", 1)[0].split("#", 1)[0]
        self._log(f"{getattr(self, 'command', None) or '-'} {path} {code}")

    def log_message(self, format, *args) -> None:
        """Sink for log_error too: the request line may hold the token, so print nothing of it."""
        self._log("request error")

    def send_error(self, code, message=None, explain=None) -> None:
        """Stdlib error pages bypass _send; answer JSON with the same security headers."""
        try:
            short = HTTPStatus(code).phrase
        except ValueError:
            short = "error"
        self.close_connection = True
        self.request_version = "HTTP/1.1"  # an unparsed line leaves HTTP/0.9, which sends no headers
        self._send(code, json.dumps({"error": short.lower()}).encode(), "application/json")

    def _other(self) -> None:
        if not (self._cookie_ok() or _same(self.headers.get(HEADER, ""), self.server.token)):
            return self._json(403, {"error": "forbidden"})
        self._json(405, {"error": "method not allowed"})

    do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = _other

    def _json(self, status: int, obj: dict) -> None:
        self._send(status, json.dumps(obj).encode(), "application/json")

    def _cookie_ok(self) -> bool:
        for part in self.headers.get("Cookie", "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == COOKIE and _same(v, self.server.token):
                return True
        return False

    def do_GET(self) -> None:
        url = urlsplit(self.path)
        extra = None
        if self._cookie_ok():
            pass
        else:
            t = parse_qs(url.query).get("t", [""])[0]
            if not t or not _same(t, self.server.token):
                return self._json(403, {"error": "forbidden"})
            extra = {"Set-Cookie": f"{COOKIE}={self.server.token}; HttpOnly; SameSite=Strict; Path=/"}
        if url.path == "/api/state":
            return self._send(200, json.dumps(state.load(self.server.home)).encode(), "application/json", extra)
        rel = "index.html" if url.path == "/" else url.path[len("/static/"):] if url.path.startswith("/static/") else None
        f = (STATIC / rel).resolve() if rel else None
        if f is None or STATIC.resolve() not in f.parents or not f.is_file():
            return self._json(404, {"error": "not found"})
        self._send(200, f.read_bytes(), TYPES.get(f.suffix, "application/octet-stream"), extra)

    def do_POST(self) -> None:
        header = self.headers.get(HEADER, "")
        if not (self._cookie_ok() and header and _same(header, self.server.token)):
            return self._json(403, {"error": "forbidden"})
        parts = urlsplit(self.path).path.strip("/").split("/")
        if parts[0] != "api" or len(parts) not in (2, 3) or parts[1] not in state.Steps:
            return self._json(404, {"error": "not found"})
        step, action = parts[1], (parts[2] if len(parts) == 3 else None)
        handler = ROUTES.get((step, action))
        if action is not None and handler is None:
            return self._json(404, {"error": "not found"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if not 0 <= n <= MAX_BODY:
                raise ValueError
            body = json.loads(self.rfile.read(n) or b"{}")
            if not isinstance(body, dict):
                raise ValueError
        except ValueError:
            return self._json(400, {"error": "bad request body"})
        home = self.server.home
        try:
            with _LOCK:
                try:
                    state.check(home, step)
                except state.OutOfOrder:
                    already = step in state.load(home)["completed"]
                    msg = "that step is already done" if already else "finish the earlier step first"
                    return self._json(409, {"error": msg})
                result = handler(home, body) if handler else {}
                if action is None:
                    state.advance(home, step)
                finished = state.is_complete(home)
        except Exception:  # no detail, no traceback: a handler may hold the recovery phrase
            return self._json(500, {"error": "internal error"})
        if finished:
            threading.Thread(target=self.server.shutdown, daemon=True).start()
        self._json(200, result if isinstance(result, dict) else {})


def make_server(home: Home, host: str, port: int, token: str) -> ThreadingHTTPServer:
    srv = _Server((host, port), _Handler)
    srv.home = home
    srv.token = token
    return srv


def main_setup(home: Home, host: str = "127.0.0.1", port: int = 8765) -> int:
    inst = home.state_dir / "instance.json"
    setup = home.state_dir / "setup.json"
    if (inst.exists() and not setup.exists()) or (setup.exists() and state.is_complete(home)):
        print(f"{home.root} is already set up", file=sys.stderr)
        return 1
    token = secrets.token_urlsafe(32)
    srv = make_server(home, host, port, token)
    print(f"Open http://{host}:{srv.server_address[1]}/?t={token}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        return 130
    finally:
        srv.server_close()
    return 0
