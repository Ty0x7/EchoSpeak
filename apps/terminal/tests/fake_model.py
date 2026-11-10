"""A scripted OpenAI-compatible model server (stdlib only) for integration tests."""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODEL = "fake-scripted-model"


class FakeModel:
    def __init__(self, reply: str = "Hello from the scripted model.", delay: float = 0.0) -> None:
        self.reply = reply
        self.delay = delay  # seconds between streamed chunks, to exercise cancel/steer mid-run
        self.requests: list[dict] = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # quiet
                pass

            def _json(self, code, payload):
                body = json.dumps(payload).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path.rstrip("/").endswith("/models"):
                    self._json(200, {"object": "list", "data": [{"id": MODEL, "object": "model", "owned_by": "test"}]})
                else:
                    self._json(404, {"error": "not found"})

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                try:
                    body = json.loads(self.rfile.read(length) or b"{}")
                except ValueError:
                    body = {}
                owner.requests.append({"path": self.path, "body": body})
                if not self.path.rstrip("/").endswith("/chat/completions"):
                    self._json(404, {"error": "not found"})
                    return
                text = owner.reply
                if not body.get("stream"):
                    self._json(200, {"id": "c1", "object": "chat.completion", "created": int(time.time()), "model": MODEL,
                                     "choices": [{"index": 0, "finish_reason": "stop",
                                                  "message": {"role": "assistant", "content": text}}],
                                     "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}})
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                words = text.split(" ")
                chunks = [w + (" " if i < len(words) - 1 else "") for i, w in enumerate(words)]
                for i, chunk in enumerate(chunks):
                    if owner.delay:
                        time.sleep(owner.delay)
                    delta = {"content": chunk}
                    if i == 0:
                        delta["role"] = "assistant"
                    self._sse({"id": "c1", "object": "chat.completion.chunk", "created": int(time.time()), "model": MODEL,
                               "choices": [{"index": 0, "delta": delta, "finish_reason": None}]})
                self._sse({"id": "c1", "object": "chat.completion.chunk", "created": int(time.time()), "model": MODEL,
                           "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                           "usage": {"prompt_tokens": 10, "completion_tokens": len(chunks), "total_tokens": 10 + len(chunks)}})
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()

            def _sse(self, payload):
                self.wfile.write(b"data: " + json.dumps(payload).encode() + b"\n\n")
                self.wfile.flush()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


if __name__ == "__main__":
    import sys

    with FakeModel(sys.argv[1] if len(sys.argv) > 1 else "Hello from the scripted model.",
                   float(sys.argv[2]) if len(sys.argv) > 2 else 0.0) as fm:
        print(fm.url, flush=True)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
