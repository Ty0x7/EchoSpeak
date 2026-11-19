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
                # Report the whole prompt as kept, counted like the backend counts it (agent/lean/loop.py
                # _estimate_tokens). A fixed tiny number makes its truncation check (agent/lean/window.py)
                # decide the server cut the prompt to fit a 20-token window, and the run fails.
                size = len(json.dumps(body.get("tools") or [], ensure_ascii=False)) if body.get("tools") else 0
                for message in body.get("messages") or []:
                    size += len(str(message.get("content") or "")) + 16
                prompt_tokens = max(10, int(size / 3.4))
                if not body.get("stream"):
                    self._json(200, {"id": "c1", "object": "chat.completion", "created": int(time.time()), "model": MODEL,
                                     "choices": [{"index": 0, "finish_reason": "stop",
                                                  "message": {"role": "assistant", "content": text}}],
                                     "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": 5,
                                               "total_tokens": prompt_tokens + 5}})
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
                           "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": len(chunks),
                                     "total_tokens": prompt_tokens + len(chunks)}})
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


class ScriptedModel(FakeModel):
    """A model that follows scripts with real tool calls, so the backend runs real tools and emits real events.

    ``scripts`` is ``[(trigger, steps), ...]``. The first trigger found (case-insensitive) in the latest
    user message picks the script. Each assistant reply after that message is one step:
    ``{"tools": [{"name": "file_read", "args": {...}}], "text": "optional preface"}`` or
    ``{"text": "final answer"}``. After the last step it keeps answering with the last step's text.
    A tool the backend didn't offer is never called; the reply says so and ``missing`` records it.
    """

    def __init__(self, scripts: list, default: str = "Okay.", delay: float = 0.0) -> None:
        super().__init__(default, delay)
        self.scripts = scripts
        self.offered: set[str] = set()
        self.missing: list[str] = []
        owner = self
        base = self.server.RequestHandlerClass

        class Handler(base):  # type: ignore[misc, valid-type]
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
                step = owner.step_for(body)
                if step.get("http_error"):
                    self._json(int(step["http_error"]), {"error": {"message": "lab: the model server failed"}})
                    return
                delay = float(step.get("delay", owner.delay) or 0)
                size = sum(len(str(m.get("content") or "")) + 16 for m in body.get("messages") or [])
                size += len(json.dumps(body.get("tools") or []))
                usage = {"prompt_tokens": max(10, int(size / 3.4)), "completion_tokens": 7,
                         "total_tokens": max(10, int(size / 3.4)) + 7}
                calls = [{"index": i, "id": f"call_{int(time.time() * 1000) % 10**8}_{i}", "type": "function",
                          "function": {"name": c["name"], "arguments": json.dumps(c.get("args") or {})}}
                         for i, c in enumerate(step.get("tools") or [])]
                text = step.get("text") or ("" if calls else owner.reply)
                finish = "tool_calls" if calls else "stop"
                if not body.get("stream"):
                    msg = {"role": "assistant", "content": text or None}
                    if calls:
                        msg["tool_calls"] = [{k: v for k, v in c.items() if k != "index"} for c in calls]
                    self._json(200, {"id": "c1", "object": "chat.completion", "created": int(time.time()), "model": MODEL,
                                     "choices": [{"index": 0, "finish_reason": finish, "message": msg}], "usage": usage})
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                words = text.split(" ") if text else []
                for i, w in enumerate(words):
                    if delay:
                        time.sleep(delay)
                    delta = {"content": w + (" " if i < len(words) - 1 else "")}
                    if i == 0:
                        delta["role"] = "assistant"
                    self._sse({"id": "c1", "object": "chat.completion.chunk", "created": int(time.time()), "model": MODEL,
                               "choices": [{"index": 0, "delta": delta, "finish_reason": None}]})
                if calls:
                    self._sse({"id": "c1", "object": "chat.completion.chunk", "created": int(time.time()), "model": MODEL,
                               "choices": [{"index": 0, "delta": {"role": "assistant", "tool_calls": calls},
                                            "finish_reason": None}]})
                self._sse({"id": "c1", "object": "chat.completion.chunk", "created": int(time.time()), "model": MODEL,
                           "choices": [{"index": 0, "delta": {}, "finish_reason": finish}], "usage": usage})
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()

        self.server.RequestHandlerClass = Handler

    def step_for(self, body: dict) -> dict:
        messages = body.get("messages") or []
        offered = {str((t.get("function") or {}).get("name") or "") for t in body.get("tools") or []}
        self.offered |= offered
        last_user = max((i for i, m in enumerate(messages) if m.get("role") == "user"), default=-1)
        if last_user < 0:
            return {"text": self.reply}
        content = messages[last_user].get("content")
        if isinstance(content, list):
            content = " ".join(str(p.get("text") or "") for p in content if isinstance(p, dict))
        said = str(content or "").casefold()
        steps = next((s for trigger, s in self.scripts if trigger.casefold() in said), None)
        if not steps:
            return {"text": self.reply}
        if not body.get("tools"):
            # A side call (a title, a summary): answer with the script's final words, never a tool.
            return {"text": next((s.get("text") for s in reversed(steps) if s.get("text") and not s.get("tools")), self.reply)}
        index = sum(1 for m in messages[last_user + 1:] if m.get("role") == "assistant")
        step = dict(steps[min(index, len(steps) - 1)])
        if index >= len(steps) and step.get("tools"):
            step = {"text": self.reply}
        wanted = [c for c in step.get("tools") or [] if c.get("name") in offered]
        lost = [c["name"] for c in step.get("tools") or [] if c.get("name") not in offered]
        if lost:
            self.missing += lost
            if not wanted:
                return {"text": f"(lab: the backend did not offer {', '.join(lost)})"}
        step["tools"] = wanted
        return step


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
