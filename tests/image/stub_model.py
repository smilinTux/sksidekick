"""A tiny OpenAI-compatible model server for the image test: always answers REPLY.

python3 stub_model.py PORT   (stdlib only; handles plain and streamed chat completions)
With STUB_MODEL_RECORD=FILE set, every chat request body is appended to FILE (one JSON line each).
"""

import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPLY = "Noted: the fox is called Juniper."
MODEL = "stub"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        sys.stderr.write("stub: " + (a[0] % a[1:]) + "\n")

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip("/").endswith("/models"):
            return self._json({"object": "list", "data": [{"id": MODEL, "object": "model", "owned_by": "stub"}]})
        self._json({"ok": True})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) or b"{}"
        req = json.loads(raw)
        if os.environ.get("STUB_MODEL_RECORD"):
            with open(os.environ["STUB_MODEL_RECORD"], "ab") as f:
                f.write(raw.replace(b"\n", b" ") + b"\n")
        now = int(time.time())
        if not self.path.rstrip("/").endswith("/chat/completions"):
            return self._json({"error": {"message": f"stub has no {self.path}"}}, 404)
        if req.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for delta, finish in (({"role": "assistant", "content": REPLY}, None), ({}, "stop")):
                chunk = {"id": "c1", "object": "chat.completion.chunk", "created": now, "model": MODEL,
                         "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
            usage = {"id": "c1", "object": "chat.completion.chunk", "created": now, "model": MODEL, "choices": [],
                     "usage": {"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18}}
            self.wfile.write(f"data: {json.dumps(usage)}\n\ndata: [DONE]\n\n".encode())
            return
        self._json({"id": "c1", "object": "chat.completion", "created": now, "model": MODEL,
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": REPLY}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18}})


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
