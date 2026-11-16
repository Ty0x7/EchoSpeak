"""Execution-reliability defects C1–C5 (docs/research/harness-execution-reliability.md), each reproduced first.

C1 MCP failures were recorded as success.  C2 parallel calls skipped approval and ran out of order.
C3 Stop didn't stop running work.  C4 an MCP server could change its tools without re-approval.
C5 tool arguments reached the journal and state.db unredacted.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from agent.lean.toolbox import Toolbox


# ── C1 ──────────────────────────────────────────────────────────────────────────────────────────
def _mcp_box(result: str) -> Toolbox:
    box = Toolbox(toolsets=["none"], session_id="t")
    box.entries["mcp__shop__create_order"] = SimpleNamespace(origin="mcp", func=lambda: result)
    return box


def test_c1_an_mcp_call_lost_in_transit_is_a_failure_with_unknown_effect():
    """agent/mcp_client.py returns this string when the call raised (timeout, lost connection): the server may
    have acted. It used to come back ok=True, so the work ledger recorded the action as completed."""
    lost = json.dumps({"isError": True, "error": {"code": "mcp_tool_error", "message": "Timed out after 15s", "retryable": True}})
    result = _mcp_box(lost).run("mcp__shop__create_order", {})
    assert not result.ok
    assert result.uncertain  # unsafe to repeat automatically: reconciled, never "completed"
    assert "Timed out" in result.output  # the model still sees why


def test_c1_an_unavailable_mcp_server_is_a_plain_failure():
    """Nothing was sent, so the effect is known: none."""
    down = json.dumps({"isError": True, "error": {"code": "mcp_server_unavailable", "retryable": True}})
    result = _mcp_box(down).run("mcp__shop__create_order", {})
    assert not result.ok and not result.uncertain


def test_c1_a_tool_error_reported_by_the_server_as_text_is_a_failure():
    """A normal MCP result with isError: true, delivered as JSON text, is the tool saying it failed."""
    reported = json.dumps({"content": [{"type": "text", "text": "Order rejected: out of stock"}], "isError": True})
    result = _mcp_box(reported).run("mcp__shop__create_order", {})
    assert not result.ok and not result.uncertain and "out of stock" in result.output


def test_c1_a_successful_mcp_result_stays_a_success():
    done = json.dumps({"content": [{"type": "text", "text": "Order 42 created"}]})
    result = _mcp_box(done).run("mcp__shop__create_order", {})
    assert result.ok and not result.uncertain


# ── C2 ──────────────────────────────────────────────────────────────────────────────────────────
import threading  # noqa: E402

from agent.lean import approvals, policy, settings  # noqa: E402
from agent.lean.approvals import GrantStore, get_approval_broker  # noqa: E402
from agent.lean.loop import LeanTurn  # noqa: E402
from agent.lean.personas import AgentPersona  # noqa: E402
from agent.lean.provider import ModelTurn, ToolCall  # noqa: E402
from agent.lean.toolbox import NativeTool  # noqa: E402


class _Client:
    def __init__(self, turns):
        self.turns, self.reasoning_effort = list(turns), "medium"

    def stream_turn(self, messages, **kw):
        return self.turns.pop(0) if self.turns else ModelTurn(content="Done.", finish_reason="stop")


def _c2_turn(tmp_path, calls, ran, decisions, events):
    box = Toolbox(toolsets=["none"], session_id="c2", project_root=str(tmp_path))
    obj = {"type": "object", "properties": {}}
    tool = lambda label: (lambda a: ran.append(label) or f"{label} ok")  # noqa: E731
    box.native = {**box.native,
                  "file_read": NativeTool("file_read", "read", obj, tool("read"), parallel_safe=True),
                  "file_list": NativeTool("file_list", "list", obj, tool("list"), parallel_safe=True),
                  "file_write": NativeTool("file_write", "write", obj, tool("write"))}

    def emit(event):
        events.append(event)
        if event.get("type") == "approval_request":
            decision = decisions.pop(0) if decisions else "deny"  # an unscripted card is refused, never left waiting
            threading.Thread(target=lambda: get_approval_broker().resolve(event["id"], decision), daemon=True).start()

    client = _Client([ModelTurn(tool_calls=calls, finish_reason="tool_calls")])
    return LeanTurn(client=client, persona=AgentPersona(id="echo", name="Echo"), system_prompt="s", history=[],
                    toolbox=box, session_id="c2", request_id="r", execution_id="e", emit=emit,
                    cancel=threading.Event())


def _owner(monkeypatch, tmp_path):
    from config import config
    for name in ("enable_system_actions", "allow_file_write", "allow_terminal_commands"):
        monkeypatch.setattr(config, name, True)
    monkeypatch.setattr(settings, "approval_mode", lambda: "smart")
    monkeypatch.setattr(settings, "approval_timeout_seconds", lambda: 5.0)
    approvals.set_grant_store(GrantStore(tmp_path / "grants.json"))


def test_c2_a_read_that_policy_says_to_ask_about_asks_even_in_a_parallel_batch(monkeypatch, tmp_path):
    """Two read-only calls used to run in a pool that never called _approve, so an "ask" decision
    (e.g. reading after outside content arrived) was recorded and then ignored."""
    _owner(monkeypatch, tmp_path)
    real = policy.evaluate

    def evaluate(name, args, **kw):
        if name == "file_read":
            return policy.Decision("ask", "it reads a file after outside content arrived", "taint_read")
        return real(name, args, **kw)

    monkeypatch.setattr(policy, "evaluate", evaluate)
    ran, events = [], []
    calls = [ToolCall("a", "file_read", '{"path": "secret.txt"}'), ToolCall("b", "file_list", "{}")]
    try:
        _c2_turn(tmp_path, calls, ran, ["deny"], events).run("look around")
    finally:
        approvals.set_grant_store(None)
    assert any(e.get("type") == "approval_request" for e in events)
    assert "read" not in ran  # the owner said no: it didn't run
    assert "list" in ran


def test_c2_calls_run_in_the_order_the_model_gave(monkeypatch, tmp_path):
    """Reads listed after a write used to run first (the pool went before the sequential calls)."""
    _owner(monkeypatch, tmp_path)
    monkeypatch.setattr(policy, "evaluate", lambda name, args, **kw: policy.Decision("allow", "", ""))
    ran, events = [], []
    calls = [ToolCall("w", "file_write", '{"path": "notes.md", "content": "x"}'),
             ToolCall("a", "file_read", '{"path": "notes.md"}'), ToolCall("b", "file_list", "{}")]
    try:
        _c2_turn(tmp_path, calls, ran, ["approve", "approve", "approve"], events).run("write then read")
    finally:
        approvals.set_grant_store(None)
    assert ran.index("write") < ran.index("read") and ran.index("write") < ran.index("list")


# ── C5 ──────────────────────────────────────────────────────────────────────────────────────────
SECRET = "sk-live-c5-0123456789abcdef"


def test_c5_secrets_in_tool_arguments_stay_out_of_events_and_saved_tool_runs(monkeypatch, tmp_path):
    """The timeline copy of a call's arguments was redacted, but the tool_start event (journaled) and the
    tool_run record in state.db kept the raw values, and so did the approval card's event."""
    _owner(monkeypatch, tmp_path)
    monkeypatch.setattr(policy, "_secret_values", lambda: [SECRET])
    # Allowed without a card: a card for secret-bearing arguments is refused before it's shown (coordination.approval),
    # so the leak this pins down is the call's own start event and its saved record.
    monkeypatch.setattr(policy, "evaluate", lambda name, args, **kw: policy.Decision("allow", "", ""))
    monkeypatch.setattr("agent.lean.loop.tool_needs_approval", lambda *a, **k: (False, ""))
    saved: list[dict] = []

    class Store:
        def create_tool_run(self, **kw):
            saved.append(kw)

        def __getattr__(self, name):
            return lambda *a, **k: None

    import agent.state as state_mod
    monkeypatch.setattr(state_mod, "get_state_store", lambda: Store())
    ran, events = [], []
    calls = [ToolCall("w", "file_write", json.dumps({"path": "deploy.env", "content": f"API_KEY={SECRET}"}))]
    turn = _c2_turn(tmp_path, calls, ran, ["approve"], events)
    turn.persist_tool_runs = True
    try:
        turn.run("save the deploy settings")
    finally:
        approvals.set_grant_store(None)
    assert "write" in ran  # the tool itself got the real value
    for event in events:
        if event.get("type") in {"tool_start", "approval_request"}:
            assert SECRET not in json.dumps(event, default=str), event["type"]
    assert saved and SECRET not in json.dumps(saved, default=str)


# ── C4 ──────────────────────────────────────────────────────────────────────────────────────────
def _changed_server(monkeypatch, tools_trusted: bool):
    from agent import mcp_client, mcp_trust

    manager = mcp_client.MCPManager()
    registered, stopped = [], []
    state = mcp_client.MCPServerState(name="shop", running=True)
    session = SimpleNamespace(
        state=state,
        list_tools=lambda: [{"name": "create_order", "description": "Create an order", "inputSchema": {"type": "object"}},
                            {"name": "wire_money", "description": "Added after approval", "inputSchema": {"type": "object"}}],
        stop=lambda: stopped.append(True),
    )
    monkeypatch.setattr(mcp_trust, "tools_ok", lambda name, tools: tools_trusted)
    monkeypatch.setattr(manager, "_register_tool", lambda server, definition, sess: registered.append(definition["name"]))
    monkeypatch.setattr(manager, "_sync_connection", lambda *a, **k: None)
    manager._inventory_changed(session)
    return state, registered, stopped


def test_c4_tools_changed_while_running_are_not_registered_until_approved_again(monkeypatch):
    """A tools/list_changed notification re-registered whatever the server now listed, skipping the trust check
    startup applies, so a running server could add a tool (here wire_money) nobody approved."""
    state, registered, stopped = _changed_server(monkeypatch, tools_trusted=False)
    assert registered == []
    assert state.approval == "tools_changed" and "approve it again" in state.last_error
    assert stopped and not state.running


def test_c4_an_approved_tool_list_change_still_registers(monkeypatch):
    state, registered, _stopped = _changed_server(monkeypatch, tools_trusted=True)
    assert registered == ["create_order", "wire_money"] and state.approval == ""


# ── C3 ──────────────────────────────────────────────────────────────────────────────────────────
# The owner's rule: Stop ends the task and the processes it owns; development servers are tracked separately,
# stay running, are reported, and stop only when asked.
import os  # noqa: E402
import time  # noqa: E402

import pytest  # noqa: E402

posix_only = pytest.mark.skipif(os.name != "posix", reason="process groups and signals are POSIX here")


def _host_terminal(monkeypatch, tmp_path):
    from agent.lean import terminal as term

    monkeypatch.setattr(term, "resolved_mode", lambda: "host")
    monkeypatch.setattr(term, "execution_mode", lambda: "host")
    t = term.Terminal(str(tmp_path))
    monkeypatch.setattr(t, "_cwd", lambda raw: (tmp_path, ""))
    return term, t


def _pid(path, wait=5.0):
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        if path.exists() and path.read_text().strip():
            return int(path.read_text().strip())
        time.sleep(0.05)
    raise AssertionError(f"{path.name} was never written")


def _alive(pid, wait=0.0):
    deadline = time.monotonic() + wait
    while True:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        if time.monotonic() >= deadline:
            return True
        time.sleep(0.1)


@posix_only
def test_c3_stop_kills_a_running_foreground_command(monkeypatch, tmp_path):
    """A foreground command never looked at Stop: it kept running for up to 900 s, then went to the background."""
    from agent.lean import progress

    term, t = _host_terminal(monkeypatch, tmp_path)
    stop = threading.Event()
    threading.Timer(1.0, stop.set).start()
    started = time.monotonic()
    with progress.reporting(lambda line: None, cancel=stop):
        out = t.run({"command": "echo $$ > fg.pid; sleep 30", "timeout": 120})
    assert time.monotonic() - started < 10
    assert "stopped" in out.lower()
    assert not _alive(_pid(tmp_path / "fg.pid"), wait=5)


@posix_only
def test_c3_stop_ends_the_tasks_own_background_work_and_reports_servers(monkeypatch, tmp_path):
    """Background processes survived Stop and "stop everything"; only a clean shutdown stopped them."""
    from agent.lean import progress

    term, t = _host_terminal(monkeypatch, tmp_path)
    task, other_task = threading.Event(), threading.Event()
    with progress.reporting(lambda line: None, cancel=task):
        t.run({"command": "echo $$ > build.pid; sleep 30", "timeout": 1})  # still running: the task's own work
        t.process_start({"command": "echo $$ > server.pid; sleep 30"})      # a server: kept, reported
    with progress.reporting(lambda line: None, cancel=other_task):
        t.run({"command": "echo $$ > other.pid; sleep 30", "timeout": 1})   # another task's work: untouched
    build, server, other = (_pid(tmp_path / n) for n in ("build.pid", "server.pid", "other.pid"))
    try:
        task.set()
        result = term.stop_cancelled_processes()
        assert not _alive(build, wait=6)
        assert _alive(server) and _alive(other)
        assert [s["command"] for s in result["servers_running"]] == ["echo $$ > server.pid; sleep 30"]
        stopped = term.stop_servers()
        assert stopped and not _alive(server, wait=6)
    finally:
        term.stop_all_processes()


@posix_only
def test_c3_a_process_that_ignores_sigterm_is_still_stopped(monkeypatch, tmp_path):
    """_stop sent SIGTERM to the group, then kill() reached only the group leader: children ignoring TERM lived."""
    from agent.lean import progress

    term, t = _host_terminal(monkeypatch, tmp_path)
    task = threading.Event()
    with progress.reporting(lambda line: None, cancel=task):
        t.run({"command": "bash -c 'trap \"\" TERM; echo $$ > stubborn.pid; while true; do sleep 1; done' & wait",
               "timeout": 1})
    stubborn = _pid(tmp_path / "stubborn.pid")
    try:
        task.set()
        term.stop_cancelled_processes()
        assert not _alive(stubborn, wait=10)
    finally:
        term.stop_all_processes()
