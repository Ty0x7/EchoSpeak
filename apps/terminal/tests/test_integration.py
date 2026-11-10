"""Real backend (app.py --mode api, subprocess) + scripted OpenAI-compatible model: a full round trip.

Isolated data/logs/home/runtime dirs, auth on with a test key, no localhost bypass. The terminal
client finds the backend through the endpoint file and talks HTTP only.
"""
from __future__ import annotations

import io
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from echospeak_cli.client import AuthError, Client, ConnectError, new_request_id
from echospeak_cli.render import Renderer
from echospeak_cli.repl import App, Out
from fake_model import MODEL, FakeModel

ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / "apps" / "backend"
LAUNCHER = ROOT / "apps" / "terminal" / "echospeak"
REPLY = "Hello from the scripted model, over the real journal."
KEY = "test-key-" + "x" * 24

pytest.importorskip("fastapi")
pytestmark = pytest.mark.skipif(os.name != "posix", reason="endpoint file discovery is POSIX-only")


def _free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def backend(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("it")
    dirs = {name: tmp / name for name in ("data", "logs", "home", "rt")}
    for d in dirs.values():
        d.mkdir(mode=0o700)
    port = _free_port()
    with FakeModel(REPLY) as model:
        env = {k: v for k, v in os.environ.items() if not k.startswith(("ECHOSPEAK_", "API_", "XDG_RUNTIME"))}
        env.update({
            "ECHOSPEAK_DATA_DIR": str(dirs["data"]), "ECHOSPEAK_LOGS_DIR": str(dirs["logs"]),
            "ECHOSPEAK_HOME": str(dirs["home"]), "SOUL_PATH": str(dirs["data"] / "SOUL.md"),
            "ECHOSPEAK_RUNTIME_DIR": str(dirs["rt"]), "ECHOSPEAK_RUNTIME_KIND": "desktop",
            "API_AUTH_ENABLED": "true", "API_AUTH_LOCALHOST_BYPASS": "false", "API_AUTH_KEY": KEY,
            "USE_LOCAL_MODELS": "true", "LOCAL_MODEL_PROVIDER": "vllm", "LOCAL_MODEL_URL": model.url,
            "LOCAL_MODEL_NAME": MODEL, "ECHOSPEAK_DISABLE_WARMUP": "1", "LEARNING_ENABLED": "0",
            "CHECKIN_MODE": "off", "LEAN_QUARANTINE_READER": "off", "PYTHONUNBUFFERED": "1",
        })
        log = open(tmp / "backend.log", "wb")
        proc = subprocess.Popen([sys.executable, "app.py", "--mode", "api", "--host", "127.0.0.1", "--port", str(port)],
                                cwd=str(BACKEND), env=env, stdout=log, stderr=subprocess.STDOUT)
        client = Client(f"http://127.0.0.1:{port}", KEY, timeout=10)
        deadline = time.monotonic() + 120
        while True:
            assert proc.poll() is None, (tmp / "backend.log").read_text()[-3000:]
            try:
                client.health()
                break
            except ConnectError:
                assert time.monotonic() < deadline, "backend did not start"
                time.sleep(0.5)
        cli_env = {k: v for k, v in os.environ.items() if not k.startswith(("ECHOSPEAK_", "XDG_RUNTIME"))}
        cli_env.update({"ECHOSPEAK_RUNTIME_DIR": str(dirs["rt"]), "NO_COLOR": "1"})
        yield {"client": client, "port": port, "env": cli_env, "rt": dirs["rt"], "proc": proc, "model": model}
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=30)
        log.close()
        assert not (dirs["rt"] / "endpoint.json").exists(), "endpoint file must be removed on clean shutdown"


def cli(backend, *args, stdin=""):
    return subprocess.run([sys.executable, str(LAUNCHER), *args], input=stdin, capture_output=True, text=True,
                          env=backend["env"], timeout=120)


def test_auth_is_required_and_endpoint_file_supplies_the_key(backend):
    with pytest.raises(AuthError):
        Client(f"http://127.0.0.1:{backend['port']}", "").threads()
    with pytest.raises(AuthError):
        Client(f"http://127.0.0.1:{backend['port']}", "wrong-key").threads()
    data = json.loads((backend["rt"] / "endpoint.json").read_text())
    assert data["port"] == backend["port"] and data["auth_required"] is True and data["pid"] == backend["proc"].pid
    assert oct((backend["rt"] / "endpoint.json").stat().st_mode & 0o777) == "0o600"
    res = cli(backend, "status")
    assert res.returncode == 0, res.stderr
    assert "version" in res.stdout and "endpoint-file" in res.stdout and "approvals 0 pending" in res.stdout
    assert KEY not in res.stdout + res.stderr


def test_full_round_trip_render_history_and_attach_replay(backend):
    client = backend["client"]
    buf = io.StringIO()
    app = App(client, Renderer(), Out(buf), interactive=False)
    rid = new_request_id()
    result = app.send("hi there", request_id=rid)
    assert result.ok, buf.getvalue()
    out = buf.getvalue()
    assert REPLY in out and "new session" in out
    sid = app.sid
    # The model really was called through the backend's provider path.
    assert any(r["body"].get("stream") for r in backend["model"].requests)

    history = client.get("/history", thread_id=sid)
    assert "Human: hi there" in history["history"] and f"Assistant: {REPLY}" in history["history"]
    runs = client.runs(sid)
    assert runs[0]["id"] == rid and runs[0]["status"] == "completed"

    # Another client attaches to the finished run: the journal replays the same text.
    res = cli(backend, "attach", sid[:8], "--run", rid)
    assert res.returncode == 0, res.stderr
    assert REPLY in res.stdout and "fake-scripted-model" in res.stdout
    # From a cursor past the tokens, the text is not replayed again.
    res = cli(backend, "attach", sid, "--run", rid, "--after", "9")
    assert REPLY not in res.stdout

    # Retrying the exact same POST is idempotent: it replays rather than starting a second run.
    replay = list(client.stream("POST", "/query/stream", {"message": "hi there", "thread_id": sid, "client_request_id": rid}))
    assert replay[-1]["type"] == "journal_done" and len(client.runs(sid)) == 1


def test_send_pipe_json_sessions_agents(backend):
    res = cli(backend, "send", "what is up")
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == REPLY
    sid = res.stderr.strip().splitlines()[-1].split()[-1]

    res = cli(backend, "send", "-", "--session", sid, "--json", stdin="again please")
    assert res.returncode == 0, res.stderr
    events = [json.loads(line) for line in res.stdout.splitlines()]
    assert events[-1]["type"] == "journal_done" and events[-1]["status"] == "completed"
    assert "".join(e.get("data", "") for e in events if e["type"] == "agent_token") == REPLY

    res = cli(backend, "sessions")
    assert res.returncode == 0 and sid[:8] in res.stdout and "what is up" in res.stdout
    res = cli(backend, "agents")
    assert res.returncode == 0 and "echo" in res.stdout and "chat" in res.stdout
    res = cli(backend, "approvals")
    assert res.returncode == 0
    res = cli(backend, "stop", "--session", sid)
    assert res.returncode == 0 and "nothing running" in res.stdout
