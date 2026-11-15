"""A recording proxy between EchoSpeak and an OpenAI-compatible model server (Ollama, LM Studio, llama.cpp).

It answers one question the harness can't answer about itself: what exactly did the model
receive, and what did it send back? Every /v1/chat/completions call is forwarded unchanged
(streaming included) and one JSON line is appended per call:

    seq, started/ended (epoch seconds), latency, request (messages, tools, max_tokens, ...),
    response (content, reasoning length, tool calls, finish_reason, usage as the server reported it)

Everything else (/api/ps, /v1/models, ...) is passed through unrecorded. The proxy never
changes a request or a response, so a run through it is the same run without it.

    python scripts/model_recorder.py --upstream http://127.0.0.1:11434 --port 11500 --out calls.jsonl

Standard library only. Loopback only.
"""
from __future__ import annotations

import argparse
import http.client
import json
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

_lock = threading.Lock()
_seq = 0


def summarize_stream(raw: bytes) -> dict[str, Any]:
    """Rebuild one streamed chat completion (SSE) into content, tool calls, finish reason and usage."""
    content: list[str] = []
    reasoning = 0
    thought: list[str] = []
    calls: dict[int, dict[str, str]] = {}
    finish = ""
    usage: dict[str, Any] = {}
    for line in raw.decode("utf-8", errors="replace").splitlines():
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            break
        try:
            chunk = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if isinstance(chunk.get("usage"), dict):
            usage = chunk["usage"]
        for choice in chunk.get("choices") or []:
            delta = choice.get("delta") or {}
            if isinstance(delta.get("content"), str):
                content.append(delta["content"])
            piece = str(delta.get("reasoning_content") or delta.get("reasoning") or "")
            reasoning += len(piece)
            if sum(map(len, thought)) < 1500:
                thought.append(piece)
            for fragment in delta.get("tool_calls") or []:
                slot = calls.setdefault(int(fragment.get("index") or 0), {"name": "", "arguments": ""})
                fn = fragment.get("function") or {}
                slot["name"] += str(fn.get("name") or "")
                slot["arguments"] += str(fn.get("arguments") or "")
            if choice.get("finish_reason"):
                finish = str(choice["finish_reason"])
    return {"content": "".join(content), "reasoning_chars": reasoning, "reasoning": "".join(thought)[:1500], "tool_calls": [calls[i] for i in sorted(calls)],
            "finish_reason": finish, "usage": usage}


class Recorder(BaseHTTPRequestHandler):
    upstream: urllib.parse.SplitResult
    out: Path
    protocol_version = "HTTP/1.1"

    def log_message(self, *_: Any) -> None:  # quiet
        return

    def _forward(self, method: str) -> None:
        global _seq
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        record = self.path.rstrip("/").endswith("/chat/completions") and method == "POST"
        started = time.time()
        conn = http.client.HTTPConnection(self.upstream.hostname, self.upstream.port or 80, timeout=1800)
        headers = {k: v for k, v in self.headers.items() if k.lower() not in {"host", "content-length", "connection"}}
        conn.request(method, self.path, body=body, headers=headers)
        upstream = conn.getresponse()
        self.send_response(upstream.status)
        for key, value in upstream.getheaders():
            if key.lower() not in {"transfer-encoding", "connection", "content-length"}:
                self.send_header(key, value)
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("Connection", "close")
        self.end_headers()
        captured = bytearray()
        try:
            while True:
                chunk = upstream.read1(65536) if hasattr(upstream, "read1") else upstream.read(65536)
                if not chunk:
                    break
                if record:
                    captured.extend(chunk)
                self.wfile.write(f"{len(chunk):X}\r\n".encode() + chunk + b"\r\n")
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
        except (BrokenPipeError, ConnectionResetError):
            pass  # the harness cancelled mid-stream; still record what arrived
        finally:
            conn.close()
        if not record:
            return
        try:
            request = json.loads(body or b"{}")
        except json.JSONDecodeError:
            request = {"unparsed": body[:2000].decode("utf-8", errors="replace")}
        response = (summarize_stream(bytes(captured)) if request.get("stream")
                    else {"raw": json.loads(captured or b"{}") if upstream.status < 400 else captured.decode(errors="replace")})
        with _lock:
            _seq += 1
            row = {"seq": _seq, "started": started, "ended": time.time(), "latency_s": round(time.time() - started, 3),
                   "status": upstream.status, "request": request, "response": response}
            with self.out.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    def do_POST(self) -> None:  # noqa: N802
        self._forward("POST")

    def do_GET(self) -> None:  # noqa: N802
        self._forward("GET")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--upstream", default="http://127.0.0.1:11434")
    parser.add_argument("--port", type=int, default=11500)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    Recorder.upstream = urllib.parse.urlsplit(args.upstream)
    Recorder.out = Path(args.out)
    Recorder.out.parent.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Recorder)
    print(f"Recording {args.upstream} on http://127.0.0.1:{args.port} -> {args.out}", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
