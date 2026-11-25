"""Token-guarded loopback server for the first-run setup page.

Request bodies are never logged (the recovery phrase will pass through later).
"""

from __future__ import annotations

import hmac
import json
import secrets
import signal
import sys
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from sksidekick.home import Home
from sksidekick.setup import backup_step, channels, phrase, soul, state, tier

COOKIE = "setup_token"
HEADER = "X-Setup-Token"
STATIC = Path(__file__).parent / "static"
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml", ".mp3": "audio/mpeg"}
MAX_BODY = 1_000_000
WATCH_INTERVAL = 1.0  # seconds between looks for an instance.json made by `sksidekick init` or `import`

# (step, action) -> handler(home, body) -> dict. action is None for the plain form.
# Later tasks register handlers here; a (step, None) handler runs before the step advances, and may
# finish its step itself (backup also finishes done). An action handler that completes a step advances it.
ROUTES: dict[tuple[str, str | None], object] = {("tier", None): tier.handle, ("channels", None): channels.handle,
    ("personality", None): soul.handle, ("phrase", None): phrase.refuse_plain, ("phrase", "new"): phrase.new,
    ("phrase", "confirm"): phrase.confirm, ("backup", None): backup_step.handle}

# What a completed step shows when the page draws it read-only: ids only, read from a body its handler has
# just accepted (so the ids are known ones). Never a URL, key, token or password.
CHOICES = {
    "tier": lambda b: {"tier": b.get("tier")},
    "channels": lambda b: {"channels": [c.get("id") for c in b.get("channels", []) if isinstance(c, dict)]},
    "personality": lambda b: {"type": b.get("type"), "voice": b.get("voice")},
}

_LOCK = threading.Lock()


def _same(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


def _range(header: str | None, total: int):
    """None: serve everything (no or unusable header, or several ranges). "bad": 416. Else (first, last)."""
    if not header or not header.startswith("bytes=") or "," in header:
        return None
    first, dash, last = header[6:].strip().partition("-")
    if not dash or not (first.isdigit() or first == "") or not (last.isdigit() or last == "") or not (first or last):
        return None
    if first == "":
        n = int(last)
        return "bad" if n == 0 or total == 0 else (max(total - n, 0), total - 1)
    a = int(first)
    b = min(int(last), total - 1) if last else total - 1
    return "bad" if a >= total or b < a else (a, b)


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
            out = {**state.load(self.server.home), "tiers": tier.tiers_state(), "channels": channels.channels_state(self.server.home), **soul.personality_state(),
                   "backup_warning": backup_step.warning(self.server.home)}
            return self._send(200, json.dumps(out).encode(), "application/json", extra)
        rel = "index.html" if url.path == "/" else url.path[len("/static/"):] if url.path.startswith("/static/") else None
        f = (STATIC / rel).resolve() if rel else None
        if f is None or STATIC.resolve() not in f.parents or not f.is_file():
            return self._json(404, {"error": "not found"})
        data, ctype = f.read_bytes(), TYPES.get(f.suffix, "application/octet-stream")
        extra = {**(extra or {}), "Accept-Ranges": "bytes"}
        rng = _range(self.headers.get("Range"), len(data))
        if rng is None:
            return self._send(200, data, ctype, extra)
        if rng == "bad":
            return self._send(416, json.dumps({"error": "range not satisfiable"}).encode(), "application/json",
                              {**extra, "Content-Range": f"bytes */{len(data)}"})
        a, b = rng
        self._send(206, data[a:b + 1], ctype, {**extra, "Content-Range": f"bytes {a}-{b}/{len(data)}"})

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
                if (home.state_dir / "instance.json").exists():  # init or import made it live meanwhile
                    return self._json(409, {"error": "this sidekick is already set up"})
                try:
                    state.check(home, step)
                except state.OutOfOrder:
                    already = step in state.load(home)["completed"]
                    msg = "that step is already done" if already else "finish the earlier step first"
                    return self._json(409, {"error": msg})
                result = handler(home, body) if handler else {}
                if action is None and state.load(home)["next"] == step:
                    choice = CHOICES[step](body) if step in CHOICES else None
                    state.advance(home, step, choice)
                finished = state.is_complete(home)
        except state.Conflict as e:
            return self._json(409, {"error": str(e)})
        except state.FieldError as e:
            return self._json(422, {**e.extra, "field": e.field, "error": e.message})
        except Exception:  # no detail, no traceback: a handler may hold the recovery phrase
            return self._json(500, {"error": "internal error"})
        if finished:
            threading.Thread(target=self.server.shutdown, daemon=True).start()
        self._json(200, result if isinstance(result, dict) else {})


class _Stopped(BaseException):
    """SIGTERM or SIGHUP arrived: unwind so the finally blocks (and the shred) run."""

    def __init__(self, signum: int) -> None:
        super().__init__(signum)
        self.signum = signum


def _raise_stopped(signum, frame) -> None:
    for sig in _stop_signals():  # a second signal must not interrupt the shred
        signal.signal(sig, signal.SIG_IGN)
    raise _Stopped(signum)


def _stop_signals() -> list:
    return [getattr(signal, n) for n in ("SIGTERM", "SIGHUP") if hasattr(signal, n)]


def _catch_stop_signals(previous: dict) -> None:
    """docker stop, systemd, kill and a closed terminal end the process without `finally`: turn them into
    an exception. Only the main thread may set handlers (tests run main_setup in a thread). Every previous
    handler goes into ``previous`` before any new one is installed, so the restore is always complete."""
    if threading.current_thread() is not threading.main_thread():
        return
    for sig in _stop_signals():
        previous[sig] = signal.getsignal(sig)
    for sig in previous:
        signal.signal(sig, _raise_stopped)


def make_server(home: Home, host: str, port: int, token: str) -> ThreadingHTTPServer:
    srv = _Server((host, port), _Handler)
    srv.home = home
    srv.token = token
    return srv


def _made_elsewhere(home: Home) -> bool:
    """instance.json exists but setup did not finish: `sksidekick init` or `import` made this home."""
    with _LOCK:  # the backup step writes instance.json and finishes setup under this lock
        try:
            return (home.state_dir / "instance.json").exists() and not state.is_complete(home)
        except (OSError, ValueError):
            return False


def _watch(home: Home, srv: ThreadingHTTPServer, stop: threading.Event) -> None:
    while not stop.wait(WATCH_INTERVAL):
        if _made_elsewhere(home):
            print("A sidekick was made here another way (sksidekick init or import); setup has closed.", flush=True)
            srv.shutdown()
            return


def _shown_host(host: str) -> str:
    """A container binds every address; the person opens it on loopback (the port is published there)."""
    return "127.0.0.1" if host in ("", "0.0.0.0", "::") else host


def main_setup(home: Home, host: str = "127.0.0.1", port: int = 8765) -> int:
    inst = home.state_dir / "instance.json"
    setup = home.state_dir / "setup.json"
    if (inst.exists() and not setup.exists()) or (setup.exists() and state.is_complete(home)):
        print(f"{home.root} is already set up", file=sys.stderr)
        return 1
    phrase.on_start(home)  # keys from a setup that died before its phrase was confirmed
    token = secrets.token_urlsafe(32)
    previous: dict = {}
    try:
        _catch_stop_signals(previous)
        srv = make_server(home, host, port, token)
        print(f"Open http://{_shown_host(host)}:{srv.server_address[1]}/?t={token}", flush=True)
        stop = threading.Event()
        threading.Thread(target=_watch, args=(home, srv, stop), daemon=True).start()
        try:
            srv.serve_forever()
        finally:
            stop.set()
            srv.server_close()
    except KeyboardInterrupt:
        return 130
    except _Stopped as e:
        return 128 + e.signum
    finally:
        for sig in previous:  # however we got here, no signal may interrupt the shred below
            signal.signal(sig, signal.SIG_IGN)
        phrase.on_stop(home)  # an unconfirmed phrase and its keys never outlive the server
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    return 0
