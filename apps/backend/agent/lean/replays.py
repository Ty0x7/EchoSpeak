"""Replay tests from real chats.

A chat that went wrong becomes a case in tests/replays/<name>.json:

    {
      "name": "search-broken-ai-news",
      "source": "what happened, and when",
      "user": "can you search latest ai news",
      "tool_outputs": {"web_search": "...", "safe_web_fetch": "..."},
      "recorded_turns": [...],      # what the model actually did (the failure)
      "fixed_turns": [...],         # optional: a hand-written good run
      "expect": {"tools_any": ["safe_web_fetch", "ask_user"], "reply_excludes": ["I'm sorry"]}
    }

Turns are {"content": "...", "tool_calls": [{"name": "...", "arguments": {...}}]}.
Tools answer with the recorded output for their name, so a replay is
deterministic and never touches the internet.

Two ways to run them:
  - tests/test_replays.py (every test run, no model): the recorded turns still
    play through the harness, the expectations flag them as a failure (so the
    case really catches the bug), and the fixed turns pass.
  - scripts/replay_eval.py (before a release, real model): the user's message
    goes to your configured model with the recorded tool outputs, and each case
    is scored on tool choice and the reply.
New cases come from scripts/replay_capture.py, which reads a saved chat.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from agent.lean import ask
from agent.lean.loop import LeanTurn
from agent.lean.personas import AgentPersona
from agent.lean.provider import ModelTurn, ToolCall
from agent.lean.toolbox import NativeTool, Toolbox

REPLAY_DIR = Path(__file__).resolve().parents[2] / "tests" / "replays"

# Enough of each tool's real parameters for a live model to call it naturally.
_PARAMS: dict[str, dict[str, Any]] = {
    "web_search": {"query": {"type": "string"}, "read": {"type": "integer"}},
    "safe_web_fetch": {"url": {"type": "string"}, "objective": {"type": "string"}},
    "terminal": {"command": {"type": "string"}},
    "file_read": {"path": {"type": "string"}},
    "file_write": {"path": {"type": "string"}, "content": {"type": "string"}},
    "file_edit": {"path": {"type": "string"}, "old": {"type": "string"}, "new": {"type": "string"}},
    "product_search": {"query": {"type": "string"}},
}

DEFAULT_SYSTEM = (
    "You are Echo, a personal agent on the user's PC. Use your tools to do what's asked. "
    "If a tool fails, read the error and try another route. If something is broken that you can't fix, "
    "say what broke and use ask_user with 2-4 options. Never end on just an apology."
)


@dataclass
class Case:
    name: str
    user: str
    tool_outputs: dict[str, str]
    expect: dict[str, Any]
    recorded_turns: list[dict[str, Any]] = field(default_factory=list)
    fixed_turns: list[dict[str, Any]] = field(default_factory=list)
    source: str = ""
    system: str = ""


@dataclass
class Outcome:
    tools: list[str]
    reply: str
    error: str = ""


def load_cases(folder: Path = REPLAY_DIR) -> list[Case]:
    cases = []
    for path in sorted(Path(folder).glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        cases.append(Case(
            name=str(data.get("name") or path.stem),
            user=str(data["user"]),
            tool_outputs={str(k): str(v) for k, v in dict(data.get("tool_outputs") or {}).items()},
            expect=dict(data.get("expect") or {}),
            recorded_turns=list(data.get("recorded_turns") or []),
            fixed_turns=list(data.get("fixed_turns") or []),
            source=str(data.get("source") or ""),
            system=str(data.get("system") or ""),
        ))
    return cases


def toolbox_for(case: Case) -> Toolbox:
    box = Toolbox(toolsets=["none"], session_id=f"replay-{case.name}")
    native: dict[str, NativeTool] = {ask.ASK_USER: ask.ask_user_tool()}
    for name, output in case.tool_outputs.items():
        native[name] = NativeTool(
            name=name,
            description=f"{name} (replayed from a recorded chat)",
            parameters={"type": "object", "properties": dict(_PARAMS.get(name, {}))},
            func=lambda _args, _out=output: _out,
        )
    box.native = native
    return box


class ScriptedTurns:
    """Plays back case turns as the model."""

    def __init__(self, turns: list[dict[str, Any]]) -> None:
        self.turns = [
            ModelTurn(
                content=str(turn.get("content") or ""),
                tool_calls=[ToolCall(f"r{i}_{j}", str(call["name"]), json.dumps(call.get("arguments") or {}))
                            for j, call in enumerate(turn.get("tool_calls") or [])],
            )
            for i, turn in enumerate(turns)
        ]

    def stream_turn(self, messages, *, tools=None, on_reasoning=None, on_content=None, cancel=None, temperature=None, max_tokens=None):
        turn = self.turns.pop(0) if self.turns else ModelTurn(content="")
        if turn.content and on_content:
            on_content(turn.content)
        return turn


def run_case(case: Case, client: Any, *, answer: str = "") -> Outcome:
    """Run the case's user message against `client`; questions get `answer` (or their first option)."""
    from agent.lean.approvals import get_approval_broker

    events: list[dict[str, Any]] = []
    tools: list[str] = []

    def emit(event: dict[str, Any]) -> None:
        events.append(event)
        if event.get("type") == "tool_start":
            tools.append(str(event.get("name") or ""))
        if event.get("type") == "approval_request":
            reply = answer or (list(event.get("options") or []) or ["allow"])[0]
            if event.get("kind") != "question":
                reply = "allow"
            threading.Timer(0.01, lambda: get_approval_broker().resolve(str(event["id"]), reply)).start()

    turn = LeanTurn(
        client=client,
        persona=AgentPersona(id="echo", name="Echo"),
        system_prompt=case.system or DEFAULT_SYSTEM,
        history=[],
        toolbox=toolbox_for(case),
        session_id=f"replay-{case.name}",
        request_id=f"replay-{case.name}",
        execution_id=f"replay-{case.name}",
        emit=emit,
        cancel=threading.Event(),
        persist_tool_runs=False,
    )
    try:
        result = turn.run(case.user)
    except Exception as exc:  # a crash is a failed case, not a crashed test run
        return Outcome(tools=tools, reply="", error=f"{type(exc).__name__}: {exc}")
    return Outcome(tools=tools, reply=str(result.text or ""))


def score(case: Case, outcome: Outcome) -> list[str]:
    """What's wrong with this run, in plain words. Empty means it passed."""
    expect = case.expect
    problems: list[str] = []
    if outcome.error:
        problems.append(f"crashed: {outcome.error}")
    called = set(outcome.tools)
    any_of = list(expect.get("tools_any") or [])
    if any_of and not called.intersection(any_of):
        problems.append(f"called none of {any_of} (called {outcome.tools or 'nothing'})")
    for name in expect.get("tools_all") or []:
        if name not in called:
            problems.append(f"didn't call {name}")
    for name in expect.get("tools_none") or []:
        if name in called:
            problems.append(f"called {name}, which it shouldn't")
    reply = outcome.reply.lower()
    for phrase in expect.get("reply_excludes") or []:
        if str(phrase).lower() in reply:
            problems.append(f"reply says {phrase!r}")
    includes = list(expect.get("reply_includes_any") or [])
    if includes and not any(str(p).lower() in reply for p in includes):
        problems.append(f"reply mentions none of {includes}")
    return problems


# ── capture ──────────────────────────────────────────────────────────

def capture(session_id: str, name: str, expect: dict[str, Any], *, turn_id: str = "", store: Any = None) -> dict[str, Any]:
    """Turn the latest (or given) turn of a saved chat into a replay case."""
    if store is None:
        from agent.state import get_state_store

        store = get_state_store()
    executions = [e for e in store.list_executions(session_id, limit=50) if not turn_id or e.id == turn_id]
    if not executions:
        raise ValueError("No turns found for that chat.")
    execution = max(executions, key=lambda e: getattr(e, "created_at", 0) or 0)
    timeline: list[dict[str, Any]] = []
    for item in store.list_items(execution.id):
        if item.item_type == "assistant_message":
            timeline.extend(list((item.payload or {}).get("timeline") or []))
    turns: dict[int, dict[str, Any]] = {}
    outputs: dict[str, str] = {}
    for row in timeline:
        step = int(row.get("step") or 0)
        turn = turns.setdefault(step, {"content": "", "tool_calls": []})
        if row.get("kind") == "text":
            turn["content"] = (turn["content"] + "\n" + str(row.get("text") or "")).strip()
        elif row.get("kind") == "tool":
            tool = str(row.get("name") or "")
            turn["tool_calls"].append({"name": tool, "arguments": dict(row.get("args") or {})})
            outputs.setdefault(tool, str(row.get("output") or ""))
    return {
        "name": name,
        "source": f"Captured from chat {session_id}, turn {execution.id}",
        "user": str(execution.query or ""),
        "tool_outputs": outputs,
        "recorded_turns": [turns[k] for k in sorted(turns)],
        "expect": expect,
    }


def save_case(case: dict[str, Any], folder: Path = REPLAY_DIR) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{case['name']}.json"
    path.write_text(json.dumps(case, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def live_client(provider: str = "", model: str = "") -> Any:
    """A ChatClient for the configured (or given) provider and model."""
    from agent.lean.provider import ChatClient, resolve_endpoint
    from api.routes.settings import _resolve_runtime_provider

    provider = provider or _resolve_runtime_provider().value
    return ChatClient(resolve_endpoint(provider, model))


def check_case(case: Case) -> Optional[str]:
    """Problems with the case file itself, or None."""
    if not case.user.strip():
        return "no user message"
    if not case.expect:
        return "no expectations"
    return None
