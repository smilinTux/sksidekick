"""A plain answer when the shared seat stops working (issue #9).

When an operator removes a seat, the shared gateway refuses the sidekick's credential (401 or 403). Hermes
then answers the person with its own copy ("rejected your API key ... run `hermes setup`"), which is no way
back for a sidekick. Hermes has no hook that can rewrite that reply, but it does fall back to the next
provider in ``fallback_providers`` when a provider refuses a credential. So, while a seat is installed,
``sksidekick serve`` runs this small model on loopback and makes it the seat's fallback. It answers every
turn with one sentence: the seat was removed (the gateway refuses the credential, or the seat expired), or
the gateway is not answering (anything else that made Hermes fall back). It never calls a real model and
never sees anything but the turn Hermes sends it, which it does not keep.

The fallback is written into Hermes' config once (and again only when the port has to change), with the
port recorded in ``config/seat_notice.json``.
"""

from __future__ import annotations

import hmac
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

from sksidekick.home import Home
from sksidekick.setup import hermes_config, seat, tier
from sksidekick.setup.state import FieldError

PROVIDER = "sidekick-seat-notice"
MODEL = "seat-notice"
STATE_FILE = "seat_notice.json"
RETRY_AFTER = 60.0  # a failed start is tried again after this many seconds, not on every serve tick
SEAT_REMOVED = (
    "The shared seat this sidekick uses was removed or is no longer valid, so I cannot reach my model. "
    "Ask whoever invited you for a new invite, or pick another model option on the setup page."
)
GATEWAY_DOWN = (
    "The shared model gateway is not answering right now, or it is busy. Try again in a little while."
)


def message(home: Home) -> str:
    """What to tell the person: ask the gateway, with the seat's own headers, whether it still takes them."""
    inv = seat.load(home)
    if inv is None:
        return SEAT_REMOVED
    try:
        tier._gateway_reachable(inv)
    except FieldError as e:
        return SEAT_REMOVED if e.message == tier.REVOKED else GATEWAY_DOWN
    return GATEWAY_DOWN  # the gateway takes the seat, so Hermes fell back for another reason


def _handler(home: Home, answer: Callable[[Home], str]):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass  # nothing about a turn is kept

        def _allowed(self) -> bool:
            token = seat._token(home)
            got = self.headers.get("Authorization") or ""
            return token is not None and hmac.compare_digest(got.encode(), f"Bearer {token}".encode())

        def _json(self, obj: dict, code: int = 200) -> None:
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if not self._allowed():
                return self._json({"error": {"message": "unauthorized"}}, 401)
            if self.path.rstrip("/").endswith("/models"):
                return self._json({"object": "list", "data": [{"id": MODEL, "object": "model", "owned_by": "sksidekick"}]})
            self._json({"error": {"message": "not found"}}, 404)

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b""
            if not self._allowed():
                return self._json({"error": {"message": "unauthorized"}}, 401)
            if not self.path.rstrip("/").endswith("/chat/completions"):
                return self._json({"error": {"message": "not found"}}, 404)
            try:
                stream = bool(json.loads(raw or b"{}").get("stream"))
            except (ValueError, AttributeError):
                stream = False
            text, now = answer(home), int(time.time())
            usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
            if not stream:
                return self._json({"id": "seat-notice", "object": "chat.completion", "created": now, "model": MODEL,
                                   "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                                                "finish_reason": "stop"}], "usage": usage})
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for delta, finish in (({"role": "assistant", "content": text}, None), ({}, "stop")):
                chunk = {"id": "seat-notice", "object": "chat.completion.chunk", "created": now, "model": MODEL,
                         "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
            tail = {"id": "seat-notice", "object": "chat.completion.chunk", "created": now, "model": MODEL,
                    "choices": [], "usage": usage}
            self.wfile.write(f"data: {json.dumps(tail)}\n\ndata: [DONE]\n\n".encode())

    return Handler


def configure(home: Home, port: int, config_set: Callable[[Home, str, str], None] = hermes_config.config_set) -> None:
    """Point Hermes' fallback at the notice on this port. The key is the seat's own token, from .env."""
    base = f"providers.{PROVIDER}"
    config_set(home, f"{base}.api", f"http://127.0.0.1:{port}/v1")
    config_set(home, f"{base}.key_env", hermes_config.GATEWAY_TOKEN_VAR)
    config_set(home, f"{base}.default_model", MODEL)
    config_set(home, "fallback_providers", json.dumps([{"provider": PROVIDER, "model": MODEL}]))


class Notice:
    """The notice model for one home. ``ensure()`` is cheap and safe to call on every serve tick: it starts
    the notice once a seat is installed, and does nothing while there is none."""

    def __init__(self, home: Home, *, config_set: Callable[[Home, str, str], None] = hermes_config.config_set,
                 answer: Callable[[Home], str] = message, clock: Callable[[], float] = time.monotonic):
        self.home = home
        self.clock = clock
        self._retry_at = 0.0
        self.config_set = config_set
        self.answer = answer
        self.server: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None

    def _recorded_port(self) -> int:
        try:
            port = json.loads((self.home.config_dir / STATE_FILE).read_text()).get("port")
            return port if isinstance(port, int) and 0 < port < 65536 else 0
        except (OSError, ValueError, AttributeError):
            return 0

    def _bind(self, port: int) -> ThreadingHTTPServer | None:
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", port), _handler(self.home, self.answer))
        except OSError:
            return None
        srv.daemon_threads = True
        return srv

    @property
    def port(self) -> int | None:
        return self.server.server_address[1] if self.server is not None else None

    def ensure(self) -> None:
        if self.server is not None or self.clock() < self._retry_at or seat.load(self.home) is None:
            return
        recorded = self._recorded_port()
        srv = (self._bind(recorded) if recorded else None) or self._bind(0)
        if srv is None:
            self._retry_at = self.clock() + RETRY_AFTER
            print("sksidekick serve: could not start the seat notice on loopback", file=sys.stderr)
            return
        port = srv.server_address[1]
        if port != recorded:
            try:
                configure(self.home, port, self.config_set)
                self.home.config_dir.mkdir(parents=True, exist_ok=True)
                (self.home.config_dir / STATE_FILE).write_text(json.dumps({"port": port}) + "\n")
            except (hermes_config.HermesError, OSError):
                srv.server_close()
                self._retry_at = self.clock() + RETRY_AFTER
                print("sksidekick serve: could not set the seat notice as Hermes' fallback; trying again later",
                      file=sys.stderr)
                return
        self.server = srv
        self.thread = threading.Thread(target=srv.serve_forever, name="seat-notice", daemon=True)
        self.thread.start()

    def close(self) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
