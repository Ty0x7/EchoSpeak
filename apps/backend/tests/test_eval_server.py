"""The private evaluation server (scripts/eval_server.py) and the controller's --context path."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from scripts import auto_test_fix as atf
from scripts import eval_server


def _shared(models: list[dict]) -> tuple[ThreadingHTTPServer, str]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            body = json.dumps({"models": models}).encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def test_busy_names_loaded_models_and_tolerates_a_stopped_service():
    server, url = _shared([{"name": "gemma4-12b-it:q4_K_M"}])
    try:
        assert eval_server.busy(url) == "gemma4-12b-it:q4_K_M"
    finally:
        server.shutdown()
    assert eval_server.busy(url) == ""


def test_refuses_to_start_beside_a_loaded_shared_model():
    server, url = _shared([{"name": "qwen3.5:9b"}])
    try:
        with pytest.raises(RuntimeError, match="qwen3.5:9b loaded"):
            with eval_server.private_server(32768, shared=url, wait_idle=0, ollama="/bin/false"):
                pass
    finally:
        server.shutdown()


FAKE_OLLAMA = textwrap.dedent("""\
    import json, os, subprocess, sys
    from http.server import BaseHTTPRequestHandler, HTTPServer
    record = sys.argv[1]
    runner = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
    env = {k: v for k, v in os.environ.items() if k.startswith(("OLLAMA_", "LLAMA_ARG_"))}
    open(record, "w").write(json.dumps({"env": env, "runner": runner.pid}))
    host, port = os.environ["OLLAMA_HOST"].rsplit(":", 1)
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.end_headers(); self.wfile.write(b'{"version": "fake"}')
        def log_message(self, *a): pass
    HTTPServer((host, int(port)), H).serve_forever()
""")


def test_private_server_runs_with_the_window_and_no_prompt_cache_then_stops_its_runner(tmp_path):
    script = tmp_path / "fake_ollama.py"
    script.write_text(FAKE_OLLAMA)
    record = tmp_path / "record.json"
    launcher = tmp_path / "ollama"
    launcher.write_text(f"#!/bin/sh\nexec {sys.executable} {script} {record}\n")
    launcher.chmod(0o755)
    stopped, url = _shared([])
    stopped.shutdown()  # an idle shared service: nothing answers
    with eval_server.private_server(32768, shared=url, ollama=str(launcher)) as address:
        assert eval_server._get(address, "/api/version") == {"version": "fake"}
        seen = json.loads(record.read_text())
        assert seen["env"]["OLLAMA_CONTEXT_LENGTH"] == "32768"
        assert seen["env"]["LLAMA_ARG_CACHE_RAM"] == "0"
        assert seen["env"]["OLLAMA_HOST"] == address.removeprefix("http://")
        runner = seen["runner"]
        os.kill(runner, 0)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(runner, 0)
        except ProcessLookupError:
            break
        # A zombie still answers kill(0); it has no live state left.
        if Path(f"/proc/{runner}/stat").read_text().split(") ", 1)[1].startswith("Z"):
            break
        time.sleep(0.1)
    else:
        pytest.fail("the private server's runner outlived it")


def test_guard_stops_a_live_evaluation(tmp_path):
    argv = [sys.executable, "-c", "import time; print('started', flush=True); time.sleep(60)"]
    calls = []

    def guard():
        calls.append(1)
        return "the shared Ollama service loaded qwen3.5:9b during the evaluation" if len(calls) > 2 else ""

    started = time.monotonic()
    with pytest.raises(RuntimeError, match="shared Ollama service loaded"):
        atf.run_live_eval(argv, tmp_path, tmp_path / "eval.log", guard=guard)
    assert time.monotonic() - started < 30
    assert "started" in (tmp_path / "eval.log").read_text()


def test_throttled_checks_at_most_once_per_interval():
    calls = []
    check = atf.throttled(lambda: calls.append(1) or "x", 60)
    assert [check(), check(), check()] == ["x", "", ""]
    assert len(calls) == 1


class _Session:
    def __init__(self, path: Path, context: int):
        self.dir = path
        self.state = {"context": context}


def _receipt(tmp_path: Path) -> Path:
    run = tmp_path / "run"
    run.mkdir()
    (run / "judge.json").write_text(json.dumps({"results": []}))
    return run


@pytest.mark.parametrize("context", [0, 32768])
def test_run_eval_uses_a_private_server_only_with_a_context(tmp_path, monkeypatch, context):
    tree = tmp_path / "tree"
    (tree / "apps" / "backend").mkdir(parents=True)
    run = _receipt(tmp_path)
    seen = {}

    def live(argv, backend, log, timeout=7200, guard=None):
        seen["argv"], seen["guard"] = argv, guard
        return f"python scripts/harness_judge.py {run}\n"

    class Server:
        def __enter__(self):
            return "http://127.0.0.1:45555"

        def __exit__(self, *_):
            return False

    monkeypatch.setattr(atf, "run_live_eval", live)
    monkeypatch.setattr(atf.eval_server, "private_server", lambda size, log=None: Server())
    monkeypatch.setattr(atf.eval_server, "served_context", lambda url: 32768)
    monkeypatch.setattr(atf, "sh", lambda *a, **k: subprocess.CompletedProcess(a, 0, "", ""))
    monkeypatch.setattr(atf.harness_campaign, "require_complete_scorecard", lambda *a: None)
    session = _Session(tmp_path / "session", context)
    session.dir.mkdir()
    atf.run_eval(tree, ["C1"], 1, "qwen3.5:9b", "label", session)
    if context:
        assert seen["argv"][-4:] == ["--upstream", "http://127.0.0.1:45555", "--env", "LOCAL_MODEL_CONTEXT=32768"]
        assert seen["guard"] is not None
    else:
        assert "--upstream" not in seen["argv"] and seen["guard"] is None


def test_run_eval_rejects_a_server_that_served_another_window(tmp_path, monkeypatch):
    tree = tmp_path / "tree"
    (tree / "apps" / "backend").mkdir(parents=True)
    run = _receipt(tmp_path)

    class Server:
        def __enter__(self):
            return "http://127.0.0.1:45555"

        def __exit__(self, *_):
            return False

    monkeypatch.setattr(atf, "run_live_eval", lambda *a, **k: f"python scripts/harness_judge.py {run}\n")
    monkeypatch.setattr(atf.eval_server, "private_server", lambda size, log=None: Server())
    monkeypatch.setattr(atf.eval_server, "served_context", lambda url: 16384)
    session = _Session(tmp_path / "session", 32768)
    session.dir.mkdir()
    with pytest.raises(RuntimeError, match="served 16384 tokens instead of 32768"):
        atf.run_eval(tree, ["C1"], 1, "qwen3.5:9b", "label", session)
