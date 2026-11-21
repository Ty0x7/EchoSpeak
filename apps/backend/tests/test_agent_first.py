"""One agent, several capabilities: the 16 November 2026 conversation audit as regression cases.

Each test names the recorded failure it comes from (docs/research/conversation-audit-2026-11-16.md).
Real ThreadManager, RoomStore, StateStore and persona store in a temp folder; a scripted model where
a turn has to run. No live model: those checks are in docs/research/agent-first.md §6.
"""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agent import attention, environments, session_overview, state, threads
from agent.lean import app_facts, ask, capabilities, chat_run, code_check, code_mode, deliverable, rooms, session_logs
from agent.lean import runtime as lean_runtime
from agent.lean.approvals import get_approval_broker
from agent.lean.loop import LeanTurn
from agent.lean.personas import AgentPersona, PersonaStore
from agent.lean.provider import ModelTurn, ToolCall
from agent.lean.toolbox import NativeTool, Toolbox
from config import config

REPO = Path(__file__).resolve().parents[3]
_ENGINEERING = {"terminal", "file_edit", "file_write"}
_IDS: dict[str, str] = {}


@pytest.fixture
def env(tmp_path, monkeypatch):
    chats = threads.ThreadManager(tmp_path / "threads.json")
    monkeypatch.setattr(threads, "get_thread_manager", lambda: chats)
    room_store = rooms.RoomStore(tmp_path / "rooms.json")
    monkeypatch.setattr(rooms, "get_room_store", lambda: room_store)
    store = state.StateStore(tmp_path / "state")
    monkeypatch.setattr(state, "_state_store", store)
    people = PersonaStore(tmp_path / "agents.json")
    from agent.lean import personas
    monkeypatch.setattr(personas, "get_persona_store", lambda: people)
    monkeypatch.setattr(lean_runtime, "get_persona_store", lambda: people)
    # A fresh store already has the built-in roster: Echo, Jarvis (Researcher) and Glados (Builder).
    folder = tmp_path / "work"
    folder.mkdir()
    import agent.projects
    manager = agent.projects.ProjectManager(tmp_path / "projects")
    trusted = manager.create_project(name="Work", workspace_root=str(folder), trust_state="trusted")
    monkeypatch.setattr(agent.projects, "get_project_manager", lambda: manager)
    _IDS["work"] = trusted.id
    monkeypatch.setattr(config, "assistant_mode", "unified", raising=False)
    monkeypatch.setattr(config, "enable_system_actions", True, raising=False)
    monkeypatch.setattr(config, "allow_terminal_commands", True, raising=False)
    monkeypatch.setattr(config, "allow_file_write", True, raising=False)
    return SimpleNamespace(chats=chats, rooms=room_store, store=store, people=people, folder=folder)


def _session(monkeypatch, session_id: str, model: str = "qwen3.5:9b") -> lean_runtime.LeanSession:
    monkeypatch.setattr(lean_runtime.LeanSession, "_recall", lambda self, query, limit=8: [])
    return lean_runtime.LeanSession(
        agent=SimpleNamespace(memory=None, llm_provider=SimpleNamespace(value="ollama"),
                              model_runtime=SimpleNamespace(model_id=model)),
        session_id=session_id, request_id="req-agent-first", emit=lambda _event: None, cancel=threading.Event(),
        source="web", room=rooms.get_room_store().by_thread(session_id))


def _echo_turn(env, monkeypatch, message: str, *, project: bool):
    chat = env.chats.create_thread(title="Talk", source="web")
    if project:
        env.store.update_thread_state(chat.thread_id, active_project_id=_IDS["work"])
    session = _session(monkeypatch, chat.thread_id)
    persona = session._code_persona() if project else session._chat_persona()
    return session, session._build_turn(persona, message, history=[], depth=0)


@pytest.mark.parametrize(("tool", "reports_failure_directly"), [
    ("mcp__calendar__list_events", True),
    ("mcp__github__list_releases", False),
    ("mcp__github__create_release", True),
])
def test_release_access_reply_only_defers_to_a_github_connection(env, monkeypatch, tool, reports_failure_directly):
    from agent.tool_registry import ToolEntry, ToolRegistry
    from agent.tools import TOOL_METADATA, get_available_tools

    # _echo_turn builds the toolbox directly, outside the startup path that loads built-in tools.
    monkeypatch.setattr(ToolRegistry, "_entries", {})
    ToolRegistry.register_from_metadata(get_available_tools(), TOOL_METADATA)
    monkeypatch.setitem(ToolRegistry._entries, tool, ToolEntry(
        name=tool, func=lambda **_: "fixture", description="Fixture connector tool", origin="mcp",
        mcp_server=tool.split("__")[1], is_action=tool.endswith("create_release"),
    ))
    _, turn = _echo_turn(env, monkeypatch, "What is the latest EchoSpeak release?", project=False)
    assert tool in turn.toolbox.names
    assert turn.prefetch()[0][1]["url"] == chat_run.SELF_RELEASES
    assert (turn.prefetch_reply is not None) == reports_failure_directly


# ── 1. Application and identity awareness ─────────────────────────────────────────────────────

def test_every_profile_is_told_the_same_facts_about_the_app(env, monkeypatch):
    """Audit 20-23: Echo guessed its commands and architecture, Jarvis could not see Echo, and
    answered "EchoSpeak version 11.14.0" when asked for its model."""
    from version import APP_VERSION

    _, talking = _echo_turn(env, monkeypatch, "what agents do I have?", project=False)
    _, working = _echo_turn(env, monkeypatch, "what agents do I have?", project=True)
    direct = env.rooms.direct(next(p.id for p in env.people.list() if p.name == "Jarvis"), "Jarvis")
    session = _session(monkeypatch, direct.thread_id)
    jarvis = next(p for p in env.people.list() if p.name == "Jarvis")
    named = session._build_turn(jarvis, "is there an agent called Echo?", history=[], depth=0)

    for prompt in (talking.system_prompt, working.system_prompt, named.system_prompt):
        # The whole roster, in a direct conversation too, where the team section lists nobody.
        assert "The agents, all of them: Echo" in prompt and "Jarvis (Researcher)" in prompt and "Glados (Builder)" in prompt
        assert "not agents" in prompt
        # The model and the app version are different facts and are told apart.
        assert "The model writing your replies is qwen3.5:9b" in prompt
        assert f"{APP_VERSION} is EchoSpeak's app version, never a model name" in prompt
        assert len(prompt.split("## About EchoSpeak", 1)[1].split("\n## ", 1)[0]) < 1900   # it is sent on every call
        # What EchoSpeak is, and the real commands.
        assert "EchoSpeak is the harness" in prompt and "(`echospeak`)" in prompt
        assert "/permissions" in prompt and "never invent a command" in prompt.lower()
    assert "Jarvis (Researcher) (this is you, the agent writing this reply" in named.system_prompt and "You are Jarvis." in named.system_prompt
    assert "How it is organised: one assistant." in talking.system_prompt


# ── Three separate systems (owner requirement, 18 November 2026; agent/lean/agent_reach.py) ───────────
# Supersedes live check A4 (17 November), which gave every direct conversation the whole roster to delegate to.

def _people(env):
    by = {p.name: p for p in env.people.list()}
    return by["Echo"], by["Jarvis"], by["Glados"]


def test_a_primary_agent_alone_does_not_recruit_the_others(env, monkeypatch):
    """System A, negative: Glados's own conversation, no shared project, so no delegation and no team section."""
    _echo, _jarvis, glados = _people(env)
    direct = env.rooms.direct(glados.id, "Glados")
    session = _session(monkeypatch, direct.thread_id)
    turn = session._build_turn(glados, "ask Jarvis to look something up", history=[], depth=0, allow_handoff=True)
    assert "delegate_to_agent" not in turn.toolbox.names
    assert session._handoff_pool(glados.id) == []
    assert "## Your team" not in turn.system_prompt


def test_project_members_delegate_to_each_other_and_only_to_each_other(env, monkeypatch):
    """System A, positive: in a project whose members are Glados and Jarvis, Glados may hand work to Jarvis only.
    Echo is not a member here, so Echo can't delegate in it and isn't a target."""
    import agent.projects
    echo, jarvis, glados = _people(env)
    agent.projects.get_project_manager().update_project(_IDS["work"], agent_ids=[glados.id, jarvis.id])
    chat = env.chats.create_thread(title="Build", source="web")
    env.store.update_thread_state(chat.thread_id, active_project_id=_IDS["work"])
    session = _session(monkeypatch, chat.thread_id)
    turn = session._build_turn(glados, "ask Jarvis to research the API", history=[], depth=0, allow_handoff=True)
    assert "delegate_to_agent" in turn.toolbox.names
    assert [p.id for p in session._handoff_pool(glados.id)] == [jarvis.id]
    assert session._handoff_pool(echo.id) == []                  # not a member: no delegation here
    echo_turn = session._build_turn(echo, "ask Jarvis to research the API", history=[], depth=0, allow_handoff=True)
    assert "delegate_to_agent" not in echo_turn.toolbox.names
    # A group room the owner composed still delegates among its members only.
    group = env.rooms.create(name="Pair", agent_ids=[glados.id, jarvis.id])
    assert {p.id for p in _session(monkeypatch, group.thread_id)._handoff_pool(glados.id)} == {jarvis.id}


def test_echo_beside_its_project_delegates_to_a_member(env, monkeypatch):
    """Live G1, 18 November: the project listed Echo and Glados, but beside a project Echo speaks as its Code profile
    ("code"), so the shared-project check found no Echo and offered no delegation. Echo's profiles are Echo."""
    import agent.projects
    echo, jarvis, glados = _people(env)
    agent.projects.get_project_manager().update_project(_IDS["work"], agent_ids=[echo.id, glados.id])
    session, turn = _echo_turn(env, monkeypatch, "Ask Glados to create hello.txt in the project", project=True)
    assert session._code_persona().id != echo.id                  # the profile the live run used
    assert "delegate_to_agent" in turn.toolbox.names
    assert [p.id for p in session._handoff_pool(session._code_persona().id)] == [glados.id]
    # Not a member: Jarvis isn't offered, and without membership Echo isn't either.
    agent.projects.get_project_manager().update_project(_IDS["work"], agent_ids=[glados.id, jarvis.id])
    _s, outside = _echo_turn(env, monkeypatch, "Ask Glados to create hello.txt in the project", project=True)
    assert "delegate_to_agent" not in outside.toolbox.names


def test_spawned_workers_exist_without_a_project_and_never_reach_other_agents(env, monkeypatch):
    """System B: Echo's own research workers, in a conversation with no project, under the Research workers setting.
    Workers are not primary agents: no delegation, no messaging, no memory, no further spawning."""
    from agent.lean import swarm
    monkeypatch.setattr(lean_runtime.settings, "research_swarm", lambda: True)
    session, turn = _echo_turn(env, monkeypatch, "Do in-depth research across many sources on home heat pumps",
                               project=False)
    assert "spawn_workers" in turn.toolbox.names
    assert "delegate_to_agent" not in turn.toolbox.names          # B is not A
    assert not swarm.WORKER_TOOLS & {"delegate_to_agent", "message_agent", "spawn_workers", "memory_search",
                                     "memory_save", "chat_search", "terminal", "file_write"}


def test_telling_another_agent_something_sends_one_note_and_grants_nothing(env, monkeypatch):
    """System C: "tell Jarvis …" offers message_agent for Jarvis only, posts one note to Jarvis's own conversation,
    and adds no delegation, no project membership and no further messages."""
    import agent.projects
    echo, jarvis, _glados = _people(env)
    chat = env.chats.create_thread(title="Talk", source="web")
    session = _session(monkeypatch, chat.thread_id)
    session.job = SimpleNamespace(goal="Tell Jarvis the release moved to Friday.")
    from agent.lean import policy
    monkeypatch.setattr(policy, "_secret_values", lambda: ["sk-ant-" + "a" * 30])  # one of the owner's stored keys
    tools = {t.name: t for t in session._message_tool(echo)}
    assert set(tools) == {"message_agent"}
    assert "Error" in tools["message_agent"].func({"agent": "Glados", "message": "hi"})
    out = tools["message_agent"].func({"agent": "Jarvis", "message": "The release moved to Friday. key sk-ant-" + "a" * 30})
    assert out.startswith("Sent to Jarvis") and "Already sent" in tools["message_agent"].func(
        {"agent": "Jarvis", "message": "again"})
    notes = [i for i in env.store.list_items(session.execution_id) if i.payload.get("role") == "agent_message"]
    assert len(notes) == 1 and notes[0].session_id == env.rooms.direct(jarvis.id, "Jarvis").thread_id
    assert "sent at the owner's request" in notes[0].payload["text"] and "sk-ant-aaaa" not in notes[0].payload["text"]
    assert agent.projects.get_project_manager().get_project(_IDS["work"]).agent_ids == []
    assert session._handoff_pool(echo.id) == []


@pytest.mark.parametrize("request_text, target", [
    ("Tell Jarvis the release moved", "Jarvis"), ("let Glados know I'm late", "Glados"),
    ("Please pass this to Jarvis: build is green", "Jarvis"), ("message Glados that tests pass", "Glados"),
    ("What does Jarvis do?", None), ("Jarvis said the build is green", None), ("tell me about Glados", None),
])
def test_only_an_explicit_request_opens_a_message(env, request_text, target):
    from agent.lean.agent_reach import message_target
    found = message_target(request_text, env.people.list(), "echo")
    assert (found.name if found else None) == target


@pytest.mark.parametrize("message, merge", [
    ("@Jarvis and @Glados: each of you, say in one sentence who you are and what you do.", False),
    ("Jarvis, Glados, introduce yourselves", False),
    ("@Jarvis @Glados which is faster for this, Rust or Go?", True),
    ("@Jarvis @Glados is this regex right? ^a+$", True),
])
def test_answers_about_each_agent_are_not_merged(message, merge):
    """Live group check: "each of you, say who you are" was followed by a merged reply in Jarvis's voice."""
    assert lean_runtime._each_for_themselves(message) is not merge


def test_an_unresolved_model_is_said_not_replaced_by_the_version():
    from version import APP_VERSION

    line = app_facts.model_line("Jarvis", "", "")
    assert "could not read which model" in line and f"never answer with the app version ({APP_VERSION})" in line


def test_commands_the_agents_are_told_about_exist_in_the_terminal():
    """A prompt that lists a command the terminal doesn't have is how "/modes" gets invented."""
    source = (REPO / "apps" / "terminal" / "echospeak_cli" / "tui" / "commands.py").read_text(encoding="utf-8")
    real = set(re.findall(r'Command\("(/[a-z]+)"', source))
    listed = {name for name, _about in app_facts.TERMINAL_COMMANDS}
    assert listed and listed <= real, sorted(listed - real)
    assert "/permissions" in listed and "/sessions" in listed
    snapshot = app_facts.snapshot()
    assert snapshot["assistant_mode"] == environments.assistant_mode()
    assert [row["name"] for row in snapshot["terminal_commands"]] == [name for name, _ in app_facts.TERMINAL_COMMANDS]


def test_echo_never_points_the_user_to_a_code_mode_that_no_longer_exists(env, monkeypatch):
    """The review against the original prompt found these left over: in unified mode Echo was still told to
    suggest "using Code" and that "Agents and Code" do what it can't."""
    from agent.lean import chat_mode
    _, talking = _echo_turn(env, monkeypatch, "run ls in my home folder", project=False)
    prompt = talking.system_prompt
    assert "using Code" not in prompt and "Agents and Code" not in prompt
    assert "a trusted project (/project) adds files and commands" in prompt          # the compact rules (9B model)
    assert "attaching a trusted project folder (/project in the terminal" in chat_mode.rules()   # the full rules
    assert "they come with a trusted project" in prompt
    refusal = chat_mode.call_problem("terminal", {})
    assert "Code" not in refusal and "/project" in refusal and "in this conversation" in refusal
    monkeypatch.setattr(config, "assistant_mode", "environments", raising=False)
    assert "Agents and Code can do it" in chat_mode.call_problem("terminal", {})
    assert "using Code" in chat_mode.rules()
    from agent.lean.personas import chat_persona, code_persona
    assert chat_persona("m").title == "Chat" and code_persona("m").title == "Code"
    monkeypatch.setattr(config, "assistant_mode", "unified", raising=False)
    # The header shows who is answering, not a mode: the same title with or without a project.
    assert chat_persona("m").title == code_persona("m").title == "Personal assistant"


def test_the_legacy_layout_is_described_as_what_it_is(monkeypatch):
    monkeypatch.setattr(config, "assistant_mode", "environments", raising=False)
    assert "three areas" in app_facts.section(name="Echo", model="m")
    monkeypatch.setattr(config, "assistant_mode", "unified", raising=False)
    assert "three areas" not in app_facts.section(name="Echo", model="m")


# ── 2. A project adds capabilities; it does not change the assistant ──────────────────────────

def test_a_trusted_project_adds_engineering_and_keeps_the_conversation_tools(env, monkeypatch):
    """Audit 24-26: with the Work project attached, a sports question ran in the engineering profile:
    no sports_live, no evidence ledger, no answer review, eight web calls."""
    _, talking = _echo_turn(env, monkeypatch, "when do the Edmonton Oilers play next?", project=False)
    session, working = _echo_turn(env, monkeypatch, "when do the Edmonton Oilers play next?", project=True)
    plain, attached = set(talking.toolbox.names), set(working.toolbox.names)

    assert not plain & _ENGINEERING and {"terminal", "file_edit"} <= attached   # the project adds
    assert {"set_reminder", "create_artifact", "create_media", "stock_history", "image_search"} <= plain
    assert plain - attached == set()                                         # and takes nothing away
    assert not attached & {"computer_action", "take_screenshot", "spawn_workers", "browse_task", "delegate_to_agent"}

    # The same research safeguards in both: evidence, citation review, URL prefetch, the stale-date note.
    for turn in (talking, working):
        assert turn.tool_observer is not None and turn.answer_review is not None and turn.result_note is not None
    assert session._chat_run is not None and session._chat_run.engineering
    # A small local model still sees the live tools up front, not behind find_tools.
    assert {"sports_live", "weather_live"} <= code_mode.focus()
    # The engineering ledger is still there for engineering.
    assert working.verifier is not None

    prompt = working.system_prompt
    assert "You are Echo" in prompt and "The project is context, not the subject of every message" in prompt
    assert "## When the request is work in the project" in prompt and "How you work in Echo Code" not in prompt


def test_the_research_budget_holds_until_the_turn_works_in_the_project(env, monkeypatch):
    """Audit 32: a sports question took 8 tool calls, two compactions and about 100 seconds."""
    _, working = _echo_turn(env, monkeypatch, "when do the Oilers play next?", project=True)
    run = working.tool_observer.__self__
    budget = chat_run.BUDGETS[run.profile]
    assert working.step_limit() == budget and working._steps_allowed(40) == budget
    run.observe("web_search", {"query": "Oilers next game"}, True, "1. result https://nhl.com/oilers", 1)
    run.observe("sports_live", {"team": "Oilers"}, True, "EDM vs FLA", 2)
    assert working.step_limit() == budget                 # still only looking things up
    run.observe("file_read", {"path": "README.md"}, True, "# Work", 3)
    assert working.step_limit() == 0 and working._steps_allowed(40) == 40   # engineering gets the engineering budget


def test_legacy_code_mode_is_unchanged(env, monkeypatch):
    monkeypatch.setattr(config, "assistant_mode", "environments", raising=False)
    code = env.chats.create_thread(title="Code", source="web", surface="code")
    session = _session(monkeypatch, code.thread_id)
    turn = session._build_turn(session._code_persona(), "Fix the test", history=[], depth=0)
    names = set(turn.toolbox.names)
    assert "sports_live" not in names and "set_reminder" not in names
    assert "## How you work in Echo Code" in turn.system_prompt and turn.step_limit is None


def test_asking_which_version_does_not_fetch_the_release_pages():
    """Live replay: "which model … and what version of echospeak is this" fetched /pulls and /releases (20 s)."""
    assert chat_run.ChatRun(session_id="s", user_message="what version of echospeak is this?").prefetch() == []
    planned = chat_run.ChatRun(session_id="s", user_message="what's new in echospeak?").prefetch()
    assert [args["url"] for _name, args in planned] == [chat_run.SELF_REPO + "/pulls", chat_run.SELF_REPO + "/releases"]
    assert chat_run.ChatRun(session_id="s", user_message="what is the latest version of echospeak?").prefetch()


def test_a_search_for_a_past_year_nobody_asked_about_is_called_out():
    """Audit 25: "Edmonton Oilers next game date 2024", asked on 16 November 2026."""
    assert chat_run.stale_years("Edmonton Oilers next game date 2024", "when do the oilers play next", this_year=2026) == ["2024"]
    assert chat_run.stale_years("Oilers next game November 2026", "when do the oilers play next", this_year=2026) == []
    assert chat_run.stale_years("Oilers 2025-26 season schedule", "when do they play", this_year=2026) == []
    assert chat_run.stale_years("who won the cup in 2019", "who won the stanley cup in 2019?", this_year=2026) == []
    assert chat_run.stale_years("history of hockey 1994", "hockey history", this_year=2026) == []   # not a stale "now"

    run = chat_run.ChatRun(session_id="s", user_message="when do the edmonton oilers play next.")
    from datetime import datetime
    past = str(datetime.now().year - 2)                    # the recorded query said 2024 in 2026
    note = run.advise("web_search", {"query": f"Edmonton Oilers next game date {past}"}, True)
    assert "Today is" in note and past in note and "did not mention" in note
    assert run.advise("web_search", {"query": "Edmonton Oilers next game"}, True) == ""
    assert run.trace()["advice"]


def test_reading_the_same_page_again_is_noticed_then_refused():
    """Audit 25: nhl.com/oilers/schedule was fetched three times in one answer, each with a new "objective"."""
    run = chat_run.ChatRun(session_id="s", user_message="when do the oilers play next")
    url = "https://www.nhl.com/oilers/schedule"
    assert run.advise("safe_web_fetch", {"url": url, "objective": "upcoming games"}, True) == ""
    assert "already read this page" in run.advise("safe_web_fetch", {"url": url + "/", "objective": "November games"}, True)
    # The loop keys a page read by its address, so the third read is refused as a repeat.
    assert LeanTurn._sig("safe_web_fetch", {"url": url, "objective": "a"}) == LeanTurn._sig(
        "safe_web_fetch", {"url": url + "/", "objective": "b", "max_text_chars": 4000})
    assert LeanTurn._sig("safe_web_fetch", {"url": url}) != LeanTurn._sig("safe_web_fetch", {"url": url + "/2027"})


# ── a scripted model, for what only a running turn shows ─────────────────────────────────────

class ScriptedClient:
    def __init__(self, turns: list[ModelTurn]) -> None:
        self.turns = list(turns)
        self.calls: list[list[dict[str, Any]]] = []
        self.tools: list[Any] = []

    def stream_turn(self, messages, *, tools=None, on_reasoning=None, on_content=None, cancel=None, temperature=None, max_tokens=None):
        self.calls.append([dict(m) for m in messages])
        self.tools.append(tools)
        turn = self.turns.pop(0)
        if turn.content and on_content:
            on_content(turn.content)
        return turn


def _loop(client: ScriptedClient, events: list[dict[str, Any]], tools: dict[str, Any]) -> LeanTurn:
    box = Toolbox(toolsets=["none"], session_id="t")
    box.native = {ask.ASK_USER: ask.ask_user_tool()}
    for name, func in tools.items():
        box.native[name] = NativeTool(name, name, {"type": "object", "properties": {}}, func, parallel_safe=False)
    return LeanTurn(client=client, persona=AgentPersona(id="echo", name="Echo"), system_prompt="system", history=[],  # type: ignore[arg-type]
                    toolbox=box, session_id="t", request_id="r", execution_id="e", emit=events.append,
                    cancel=threading.Event(), persist_tool_runs=False)


def _call(name: str, args: dict[str, Any], id_: str = "c1") -> ToolCall:
    return ToolCall(id_, name, json.dumps(args))


@pytest.mark.parametrize("retry", [
    "I couldn't delegate to Cedar. I created `receipt.txt` myself.",
    "`receipt.txt` was created by Cedar. It contains an acknowledgement.",
])
def test_unavailable_agent_cannot_get_credit_for_a_local_write(env, monkeypatch, retry):
    env.people.create({"name": "Cedar"})
    saved = env.folder / "receipt.txt"
    saved.write_text("Pending\n")
    message = "Have Cedar update receipt.txt with an acknowledgement."
    _, turn = _echo_turn(env, monkeypatch, message, project=True)
    client = ScriptedClient([
        ModelTurn(content="", tool_calls=[_call("file_edit", {"path": str(saved), "old_text": "Pending",
                                                             "new_text": "Acknowledged"})]),
        ModelTurn(content="**Cedar** created `receipt.txt`. It contains an acknowledgement."),
        ModelTurn(content=retry),
    ])
    turn.client = client
    events = []
    turn.emit = events.append

    result = turn.run(message)

    assert saved.read_text() == "Acknowledged\n", [event.get("output") for event in events if event["type"] == "tool_end"]
    assert "delegate_to_agent" not in turn.toolbox.names
    assert "Cedar** created" not in result.text
    assert "created by Cedar" not in result.text
    assert "couldn't delegate to Cedar" in result.text
    assert len(client.calls) == 3
    assert sum(event["type"] == "answer_check" for event in events) == 1


@pytest.mark.parametrize("reply", [
    "Indigo North did not check the receipt. I could not delegate to them from this project.",
    "I can’t confirm whether Indigo North checked the receipt.",
])
def test_unavailable_agent_can_be_discussed_honestly(env, monkeypatch, reply):
    env.people.create({"name": "Indigo North"})
    message = "Ask Indigo North to check the receipt."
    _, turn = _echo_turn(env, monkeypatch, message, project=True)
    client = ScriptedClient([ModelTurn(content=reply)])
    turn.client = client

    assert turn.run(message).text == reply
    assert len(client.calls) == 1


@pytest.mark.parametrize("byline", [
    "**Reviewed by:** Mesa (Reviewer)",
    "1. **Reviewed by:** Mesa (Reviewer)",
])
def test_an_unavailable_reviewer_cannot_be_added_to_a_generated_signoff(env, monkeypatch, byline):
    env.people.create({"name": "Mesa"})
    path = env.folder / "inspection.md"
    path.write_text("Review pending\n")
    message = "Ask Mesa to review the report and update inspection.md with their decision."
    _, turn = _echo_turn(env, monkeypatch, message, project=True)
    events = []
    turn.emit = events.append
    turn.client = ScriptedClient([
        ModelTurn(tool_calls=[_call("file_edit", {"path": str(path), "old_text": "Review pending",
                                                 "new_text": byline}, "false-credit")]),
        ModelTurn(tool_calls=[_call("file_edit", {"path": str(path), "old_text": "Review pending",
                                                 "new_text": "Mesa was unavailable for review."}, "corrected")]),
        ModelTurn(content="I updated `inspection.md` to record the unavailable review."),
    ])
    turn.run(message)

    calls = {e["id"]: e for e in events if e["type"] == "tool_end"}
    assert calls["false-credit"]["outcome"] == "skipped"
    assert calls["corrected"]["outcome"] == "ok"
    assert path.read_text() == "Mesa was unavailable for review.\n"


@pytest.mark.parametrize("byline_in_request", [True, False])
def test_a_supplied_or_existing_reviewer_byline_is_preserved(env, monkeypatch, byline_in_request):
    env.people.create({"name": "Mesa"})
    path = env.folder / "inspection.md"
    byline = "Reviewed by: Mesa"
    old = "Inspection pending" if byline_in_request else f"{byline}\nInspection pending"
    path.write_text(old + "\n")
    message = "Ask Mesa to review the report and update inspection.md."
    if byline_in_request:
        message += f" Keep this supplied historical byline: `{byline}`."
    _, turn = _echo_turn(env, monkeypatch, message, project=True)
    turn.client = ScriptedClient([
        ModelTurn(tool_calls=[_call("file_edit", {"path": str(path), "old_text": old,
                                                 "new_text": f"{byline}\nHistorical record preserved."})]),
        ModelTurn(content="I updated the report."),
    ])
    turn.run(message)

    assert path.read_text() == f"{byline}\nHistorical record preserved.\n"


def test_direct_project_reviewer_can_record_completion_on_retry(env, monkeypatch):
    import agent.projects

    echo, _, reviewer = _people(env)
    agent.projects.get_project_manager().update_project(_IDS["work"], agent_ids=[echo.id, reviewer.id])
    (env.folder / "checklist.md").write_text("Packaging pending.\n")
    signoff = env.folder / "signoff.txt"
    signoff.write_text("Review pending.\n")
    chat = env.chats.create_thread(title="Project review", source="web")
    env.store.update_thread_state(chat.thread_id, active_project_id=_IDS["work"])
    session = _session(monkeypatch, chat.thread_id)
    events = []
    session.emit = events.append
    request = f"Ask {reviewer.name} to review checklist.md and write the result in signoff.txt."
    lead = ScriptedClient([
        ModelTurn(tool_calls=[_call("delegate_to_agent", {"agent": reviewer.name, "task": "Review checklist.md."}, "first")]),
        ModelTurn(tool_calls=[_call("delegate_to_agent", {"agent": reviewer.name, "task": "Resume the review."}, "retry")]),
        ModelTurn(tool_calls=[_call("file_edit", {"path": str(signoff), "old_text": "Review pending.",
                                                 "new_text": f"Reviewed by: {reviewer.name}"}, "credit")]),
        ModelTurn(content="The review result is in signoff.txt."),
    ])
    child = ScriptedClient([
        *[ModelTurn(content="I will review the checklist and write the result.") for _ in range(3)],
        ModelTurn(tool_calls=[_call("file_read", {"path": "checklist.md"}, "read-checklist")]),
        ModelTurn(tool_calls=[_call("complete_task", {"summary": "I reviewed the checklist. Packaging is pending."}, "finish-review")]),
        ModelTurn(content="I reviewed the checklist. Packaging is pending."),
    ])
    monkeypatch.setattr(lean_runtime.LeanSession, "_client_for",
                        lambda self, persona, routing=False: child if persona.id == reviewer.id else lead)
    session.run(request)

    assert all("complete_task" not in {tool["function"]["name"] for tool in tools} for tools in lead.tools)
    assert "complete_task" in {tool["function"]["name"] for tool in child.tools[-1]}
    tool_results = {event["id"]: event for event in events if event["type"] == "tool_end"}
    for call_id in ("read-checklist", "finish-review", "credit"):
        assert tool_results[call_id]["outcome"] == "ok", (call_id, tool_results[call_id]["output"])
    assert signoff.read_text() == f"Reviewed by: {reviewer.name}\n"


@pytest.mark.parametrize("handoff", [
    "failed", "promise-only", "completed", "retried", "retry-incomplete", "unrelated-completion", "different-review",
])
def test_reviewer_byline_requires_a_successful_handoff_when_the_reviewer_was_available(env, monkeypatch, handoff):
    import agent.projects
    from agent.lean.loop import TurnResult

    echo, _, reviewer = _people(env)
    agent.projects.get_project_manager().update_project(_IDS["work"], agent_ids=[echo.id, reviewer.id])
    path = env.folder / "inspection.md"
    path.write_text("Review pending\n")
    request = f"Ask {reviewer.name} to review the report and record the result in inspection.md."
    session, turn = _echo_turn(env, monkeypatch, request, project=True)
    failed = TurnResult("I could not finish the checklist.", False, "fixture-review", reviewer.id,
                        error="The review stopped before completion.")
    finished = TurnResult("Checklist reviewed.", True, "fixture-review", reviewer.id, completed="Checklist reviewed.")
    promised = TurnResult("I will review it.", True, "fixture-review", reviewer.id, stop_reason="promise_unfulfilled")
    incomplete = TurnResult("I tried again and still have no review result.", True, "fixture-review", reviewer.id)
    results = iter({"failed": [failed], "promise-only": [promised],
                    "completed": [finished], "retried": [failed, finished],
                    "retry-incomplete": [failed, incomplete],
                    "unrelated-completion": [failed, TurnResult("Files inventoried.", True, "fixture-inventory",
                                                                 reviewer.id, completed="Files inventoried.")],
                    "different-review": [failed, TurnResult("Budget reviewed.", True, "fixture-budget",
                                                              reviewer.id, completed="Budget reviewed.")]}[handoff])
    retries = {"retried": "Resume the review.", "retry-incomplete": "Resume the review.",
               "unrelated-completion": "Inventory the project files.", "different-review": "Review the budget."}
    monkeypatch.setattr(session, "_run_agent", lambda *_args, **_kwargs: next(results))
    events = []
    turn.emit = events.append
    turn.client = ScriptedClient([
        ModelTurn(tool_calls=[_call("delegate_to_agent", {"agent": reviewer.name, "task": "Review the report."}, "handoff")]),
        *([ModelTurn(tool_calls=[_call("delegate_to_agent", {"agent": reviewer.name, "task": retries[handoff]},
                                      "retry-handoff")])] if handoff in retries else []),
        ModelTurn(tool_calls=[_call("file_edit", {"path": str(path), "old_text": "Review pending",
                                                 "new_text": f"Reviewed by: {reviewer.name}"}, "false-credit")]),
        ModelTurn(content="I checked the review result."),
    ])
    assert "delegate_to_agent" in turn.toolbox.names
    turn.run(request)

    draft = next(event for event in events if event["type"] == "tool_end" and event["id"] == "false-credit")
    completed = handoff in {"completed", "retried"}
    assert draft["outcome"] == ("ok" if completed else "skipped")
    assert path.read_text() == (f"Reviewed by: {reviewer.name}\n" if completed else "Review pending\n")


@pytest.mark.parametrize("tool, arguments", [
    ("file_write", {"content": "**Approved by:** Mesa (Custodian)"}),
    ("file_write", {"content": "Reviewed by Mesa (Custodian)"}),
    ("create_artifact", {"content": "- Reviewed by: Mesa"}),
    ("update_artifact", {"edits": [{"find": "Pending", "replace": "Signed off by: Mesa"}]}),
])
def test_reviewer_field_check_covers_the_file_and_artifact_tools(tool, arguments):
    from agent.lean.agent_reach import artifact_attribution_problem

    assert artifact_attribution_problem(tool, arguments, ("Mesa",), "Ask Mesa to check the report.")
    assert not artifact_attribution_problem(tool, arguments, (), "Ask Mesa to check the report.")


def test_a_guess_made_before_checking_is_not_part_of_the_saved_answer():
    """Audit 25 and 32: "…is today against the San Jose Sharks. Let me verify." stayed in the saved
    answer above "…is Tuesday against the Florida Panthers"."""
    guess = "The Oilers' next game is today against the San Jose Sharks. Let me verify that."
    final = "The Oilers' next game is Tuesday, November 17 against the Florida Panthers."
    client = ScriptedClient([ModelTurn(content=guess, tool_calls=[_call("lookup", {"q": "oilers"})]),
                             ModelTurn(content=final)])
    events: list[dict[str, Any]] = []
    turn = _loop(client, events, {"lookup": lambda _a: "EDM @ FLA, Tue Nov 17"})
    turn.promise_guard = False
    result = turn.run("when do the oilers play next?")
    assert result.text == final and "San Jose" not in result.text
    assert next(e for e in events if e["type"] == "agent_done")["text"] == final
    # It is still in the timeline, in order, marked as progress: nothing the model said is hidden.
    texts = [item for item in result.timeline if item["kind"] == "text"]
    assert [bool(item.get("interim")) for item in texts] == [True, False] and "San Jose" in texts[0]["text"]


def test_code_shown_before_the_last_tool_call_stays_in_the_answer():
    """The other half of the rule: a file shown, then "save it as pong.html", answered with both."""
    code = "Here is the file:\n```html\n<canvas id=\"c\"></canvas>\n```"
    client = ScriptedClient([ModelTurn(content=code, tool_calls=[_call("lookup", {})]),
                             ModelTurn(content="Save it as pong.html and open it.")])
    turn = _loop(client, [], {"lookup": lambda _a: "ok"})
    turn.promise_guard = False
    result = turn.run("show me the file")
    assert "<canvas" in result.text and result.text.endswith("Save it as pong.html and open it.")


def test_a_turn_stopped_by_its_research_limit_still_answers():
    client = ScriptedClient([ModelTurn(content="", tool_calls=[_call("lookup", {"q": "a"}, "c1")]),
                             ModelTurn(content="", tool_calls=[_call("lookup", {"q": "b"}, "c2")]),
                             ModelTurn(content="From what I found: Tuesday against Florida.")])
    events: list[dict[str, Any]] = []
    turn = _loop(client, events, {"lookup": lambda _a: "result"})
    turn.promise_guard = False
    turn.step_limit = lambda: 2
    turn.grace_prompt = chat_run.GRACE_PROMPT
    result = turn.run("when do the oilers play next?")
    assert result.stop_reason == "max_steps" and result.text == "From what I found: Tuesday against Florida."
    assert not client.tools[-1]                         # the last call is made without tools
    assert "research budget is used up" in json.dumps(client.calls[-1])


def test_the_third_read_of_one_page_is_refused_whatever_it_is_read_for():
    reads = [ModelTurn(content="", tool_calls=[_call("safe_web_fetch", {"url": "https://www.nhl.com/oilers/schedule",
                                                                     "objective": f"wording {i}"}, f"c{i}")]) for i in range(3)]
    client = ScriptedClient(reads + [ModelTurn(content="Tuesday against Florida.")])
    fetched: list[dict[str, Any]] = []
    turn = _loop(client, [], {"safe_web_fetch": lambda args: fetched.append(args) or "EDM @ FLA Tue Nov 17"})
    turn.promise_guard = False
    turn.run("when do the oilers play next?")
    assert len(fetched) == 2                                # the third never ran
    refusal = next(m["content"] for m in reversed(client.calls[-1]) if m.get("role") == "tool")
    assert "You already called safe_web_fetch" in refusal and "Use that result" in refusal


# ── 3. Permissions, approvals and questions are three different things ────────────────────────

def test_a_question_card_cannot_offer_to_grant_a_permission(monkeypatch):
    """Audit 27: Glados offered "Give me permission to create files and I'll build it right now";
    the owner picked it, and nothing was enabled."""
    monkeypatch.setattr(config, "allow_file_write", False, raising=False)
    args = {"question": "File creation is disabled. How would you like to proceed?",
            "options": ["Give me permission to create files and I'll build it right now", "I'll show you the code"]}
    client = ScriptedClient([ModelTurn(content="", tool_calls=[_call(ask.ASK_USER, args)]),
                             ModelTurn(content="File creation is off in Settings › Permissions.")])
    events: list[dict[str, Any]] = []
    turn = _loop(client, events, {})
    turn.interactive = True
    turn.promise_guard = False
    result = turn.run("make pong.html")
    assert not any(e["type"] == "approval_request" for e in events)          # no card was shown
    assert any(e["type"] == "question_refused" for e in events)
    told = next(m["content"] for m in client.calls[-1] if m.get("role") == "tool")
    assert "can't grant a permission" in told and "Settings › Permissions" in told and "/permissions" in told
    assert "Don't ask for permission in a question again" in told
    assert get_approval_broker().pending_for("t") == []
    assert result.text.startswith("File creation is off")


@pytest.mark.parametrize("options, grants", [
    (["Give me permission to create files and I'll build it right now", "Show the code"], True),
    (["Turn on file writing for me", "Use what I have"], True),
    (["Enable terminal commands", "Skip it"], True),
    (["Copy the code below and save it yourself", "Paste into a text editor like nano or VS Code"], False),
    (["Create config.json and run it again", "Skip it", "Allow me to pick a default"], False),
    (["Read a news site directly", "Set up a search key", "Skip it"], False),
    (["The standard library", "python-dateutil", "Keep the hand-written code"], False),
])
def test_only_options_that_ask_for_a_grant_are_refused(options, grants):
    assert bool(ask.asks_for_permission("How should I proceed?", options)) is grants


def test_an_answer_that_reads_like_a_grant_is_marked_as_a_choice():
    assert "a choice, not a permission" in ask.answer_caveat("yes you have my permission to write files")
    assert ask.answer_caveat("Copy the code below and save it yourself") == ""
    assert "A question is not a permission" in ask.ASK_USER_DESCRIPTION


def test_the_refusal_names_what_is_off_and_where(monkeypatch):
    monkeypatch.setattr(config, "enable_system_actions", True, raising=False)
    monkeypatch.setattr(config, "allow_file_write", False, raising=False)
    monkeypatch.setattr(config, "allow_terminal_commands", False, raising=False)
    box = Toolbox(toolsets=["core", "terminal"], session_id="t")
    off = capabilities.off_summary(box)
    assert "Creating and editing files" in off and "Create and edit files" in off and "Terminal commands" in off
    assert "Off right now:" in ask.permission_refusal(off)
    assert "Nothing you need is switched off" in ask.permission_refusal("")


def test_a_waiting_question_is_announced_once_and_as_a_question(env):
    """Audit 31: one pending card was described as an action awaiting approval and a task awaiting an answer."""
    broker = get_approval_broker()
    pending = broker.open_question(session_id="s-q", request_id="r-q", question="Which library?", options=["A", "B"],
                                   allow_other=True)
    try:
        coordination = env.store.coordination
        row = coordination.create(objective="pick a library", agent_id="echo", principal="owner", session_id="s-q",
                                  project_id="", key="request:r-q", tools=None, taint=[], budget=None)
        context = coordination.claim(row["id"], principal="owner", session_id="s-q", agent_id="echo")
        coordination.question(context, pending.id, {"question": "Which library?", "options": ["A", "B"]})
        items = attention._approvals() + attention._work()
        about = [item for item in items if item.kind in {"approval", "work"}]
        assert [item.title for item in about] == ["A question is waiting for your answer"]
        assert about[0].detail == "Which library?"
    finally:
        with broker._lock:
            broker._pending.pop(pending.id, None)


# ── 4. A deliverable is created, or it is said that it wasn't ────────────────────────────────

PONG = ("whats up glados lets build something! can we make quick little pong game for web game and make it in a "
        "single file soi just need to click it, make it in /projects please")


def _tool(name: str, status: str = "done", outcome: str = "ok") -> dict[str, Any]:
    return {"kind": "tool", "name": name, "status": status, "outcome": outcome}


def _answered(answer: str) -> dict[str, Any]:
    return {"kind": "approval", "tool": "ask_user", "decision": "answered", "answer": answer}


def test_what_became_of_the_requested_file_is_read_from_the_tools_that_ran():
    """Audit 28: no write tool ran, the owner settled for copying the code, and the work record said
    completed against "make it in /projects"."""
    assert deliverable.requested(PONG)
    fallback = deliverable.assess(PONG, [[_tool("file_read", "failed", "failed"), _tool("agent_notes"),
                                          _answered("Give me permission to create files"),
                                          _answered("Copy the code below and save it yourself")]])
    assert fallback["status"] == "fallback" and fallback["fallback"] == "Copy the code below and save it yourself"
    assert "The original request is not done" in fallback["note"]
    assert deliverable.work_status(fallback, "completed") == "completed"
    assert deliverable.VERIFICATION["fallback"] == "fallback accepted"

    missing = deliverable.assess(PONG, [[_tool("file_read", "failed", "failed"), _tool("agent_notes")]])
    assert missing["status"] == "not_created" and deliverable.work_status(missing, "completed") == "failed"
    assert deliverable.VERIFICATION["not_created"] == "not delivered"

    # Asked for a file in a folder; made an artifact in the side panel instead (the live re-test).
    panel = deliverable.assess(PONG, [[_tool("file_list"), _tool("create_artifact")]])
    assert panel["status"] == "elsewhere" and "not as a file on disk" in panel["note"]
    assert deliverable.VERIFICATION["elsewhere"] == "not where asked"
    # Asked for an artifact: an artifact is the delivery.
    assert deliverable.assess("make me a pong game as an artifact", [[_tool("create_artifact")]])["status"] == "delivered"

    made = deliverable.assess(PONG, [[_tool("file_write")]])
    assert made["status"] == "delivered" and made["created_by"] == ["file_write"] and not made["note"]
    # A write that was denied or failed created nothing.
    assert deliverable.assess(PONG, [[_tool("file_write", "failed", "denied")]])["status"] == "not_created"


@pytest.mark.parametrize("request_text, asks", [
    ("create notes.md with today's plan", True),
    ("save it as an artifact", True),
    ("put the report in ~/Documents/reports", True),
    ("write a new file for the config loader", True),
    ("when do the oilers play next?", False),
    ("write me a python function that parses csv", False),
    ("how do I create a file in /tmp?", False),
    ("what does this project build?", False),
])
def test_only_requests_for_something_to_exist_are_deliverables(request_text, asks):
    assert bool(deliverable.requested(request_text)) is asks
    if not asks:
        assert deliverable.assess(request_text, [[]]) is None


def test_the_work_record_says_not_delivered_instead_of_completed(env, monkeypatch):
    """The whole path: a run that asks a question, gets an answer and writes nothing."""
    direct = env.rooms.direct(next(p.id for p in env.people.list() if p.name == "Glados"), "Glados")
    glados = next(p for p in env.people.list() if p.name == "Glados")
    events: list[dict[str, Any]] = []
    monkeypatch.setattr(lean_runtime.LeanSession, "_recall", lambda self, query, limit=8: [])
    session = lean_runtime.LeanSession(
        agent=SimpleNamespace(memory=None, llm_provider=SimpleNamespace(value="ollama"),
                              model_runtime=SimpleNamespace(model_id="qwen3.5:9b")),
        session_id=direct.thread_id, request_id="req-pong", emit=events.append, cancel=threading.Event(),
        source="web", room=direct)
    args = {"question": "File creation is off. What would you like?", "options": ["Show me the code here", "Stop"]}
    client = ScriptedClient([ModelTurn(content="", tool_calls=[_call(ask.ASK_USER, args)]),
                             ModelTurn(content="Here is the code to save yourself:\n```html\n<canvas></canvas>\n```")])
    monkeypatch.setattr(lean_runtime.LeanSession, "_client_for", lambda self, persona, routing=False: client)
    monkeypatch.setattr(lean_runtime.LeanSession, "_learn", lambda self, cancelled: None)
    monkeypatch.setattr(lean_runtime.LeanSession, "_remember_async", lambda self, message, answer: None)
    monkeypatch.setattr(lean_runtime.summaries, "update_in_background", lambda *a, **k: None)
    monkeypatch.setattr("agent.chat_titles.update_in_background", lambda *a, **k: None)

    def answer_when_asked() -> None:
        broker = get_approval_broker()
        for _ in range(400):
            pending = broker.pending_for(direct.thread_id)
            if pending:
                # The route both interfaces call (POST /lean/approvals/{id}): it records the answer on the
                # work as well as waking the turn. The work's question row is written just after the card opens.
                from api.routes.lean import ApprovalDecisionRequest, decide_approval
                for _ in range(200):
                    if any(r["id"] == pending[0]["id"] for r in env.store.coordination.view("approvals")):
                        break
                    threading.Event().wait(0.01)
                decide_approval(pending[0]["id"], ApprovalDecisionRequest(decision="Show me the code here"))
                return
            threading.Event().wait(0.01)
        session.cancel.set()      # never asked: stop instead of waiting out the approval timeout

    helper = threading.Thread(target=answer_when_asked, daemon=True)
    helper.start()
    result = session.run(PONG, persona_id=glados.id)
    helper.join(timeout=5)

    assert result["deliverable"]["status"] == "fallback"
    work = result["work"]
    assert work["status"] == "completed" and work["verification"] == "fallback accepted"
    assert work["result"].startswith("Not created: no file or artifact was written")
    notes = [e for e in events if e["type"] == "answer_note" and e.get("check") == "deliverable"]
    assert notes and "Show me the code here" in notes[0]["note"]
    assert any(e["type"] == "deliverable" and e["status"] == "fallback" for e in events)


# ── 5. Code in a reply is checked, and labelled when it was not run ───────────────────────────

def test_code_handed_over_in_a_reply_is_parsed_and_labelled():
    """Audit 29: the Pong code was called complete and playable; nothing had run it."""
    body = "\n".join(f"    total += {i}" for i in range(30))
    good = f"Here's your complete script, fully working:\n```python\ndef run():\n    total = 0\n{body}\n    return total\n```"
    notes = code_check.notes(good, ran=set(), wrote=False)
    assert len(notes) == 1 and notes[0].startswith("Not run:") and "It parses" in notes[0]
    replay = good.replace("Here's your complete script, fully working:", "Here's a complete, playable Pong game:")
    assert code_check.notes(replay, ran=set(), wrote=False)[0].startswith("Not run:")      # the live replay's wording
    # Once something executed in the turn, the description is no longer unverified by default.
    assert code_check.notes(good, ran={"terminal"}, wrote=True) == []

    bad = "```python\ndef run(:\n    return 1\n```"
    assert code_check.notes(bad, ran=set(), wrote=False)[0].startswith("Syntax check failed for the python code")
    assert code_check.notes('```json\n{"a": 1,}\n```', ran=set(), wrote=False)[0].startswith("Syntax check failed for the json")
    # A short snippet in an explanation is left alone, and a language without a parser is not guessed at.
    assert code_check.notes("Use this:\n```python\nprint('hi')\n```", ran=set(), wrote=False) == []
    assert code_check.syntax_problem("rust", "fn main() {") is None
    assert code_check.notes("No code here.", ran=set(), wrote=False) == []


def test_the_code_note_is_attached_to_the_answer_by_the_loop():
    body = "\n".join(f"x{i} = {i}" for i in range(30))
    reply = f"Here's your complete game, ready to play:\n```python\n{body}\n```"
    events: list[dict[str, Any]] = []
    turn = _loop(ScriptedClient([ModelTurn(content=reply)]), events, {})
    turn.promise_guard = False
    result = turn.run("make me a game")
    notes = [e for e in events if e["type"] == "answer_note" and e.get("check") == "code"]
    assert notes and notes[0]["note"].startswith("Not run:")
    assert any(item["kind"] == "note" and item["text"].startswith("Not run:") for item in result.timeline)


# ── 6. Reviewing another agent's work means reading its log ──────────────────────────────────

def test_agent_sessions_reads_the_logs_themselves(tmp_path, monkeypatch):
    """Audit 30: asked to inspect the latest Claude and Codex sessions, Echo read documentation and a
    summary, and reported "installer not yet built" after it had been installed."""
    claude = tmp_path / ".claude" / "projects" / "-home-dev-Work"
    codex = tmp_path / ".codex" / "sessions" / "2026" / "11" / "16"
    claude.mkdir(parents=True)
    codex.mkdir(parents=True)
    (claude / "969b022c.jsonl").write_text("\n".join(json.dumps(row) for row in [
        {"type": "user", "timestamp": "2026-11-16T20:00:00Z", "message": {"role": "user", "content": "fix the resize bug"}},
        {"type": "assistant", "timestamp": "2026-11-16T20:01:00Z", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "Fixed in layout.py."}, {"type": "tool_use", "name": "Bash", "input": {"command": "pytest"}}]}},
    ]), encoding="utf-8")
    (codex / "rollout-2026-11-16T18-51-10-01a1.jsonl").write_text("\n".join(json.dumps(row) for row in [
        {"timestamp": "2026-11-17T00:52:20Z", "payload": {"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": "<environment_context>cwd</environment_context>"}]}},
        {"timestamp": "2026-11-17T00:52:21Z", "payload": {"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": "finish what my claude session did"}]}},
        {"timestamp": "2026-11-17T01:24:09Z", "payload": {"type": "message", "role": "assistant", "content": [
            {"type": "output_text", "text": "PR #118 is merged. I installed the rebuilt AppImage."}]}},
    ]), encoding="utf-8")
    monkeypatch.setattr(session_logs, "roots", lambda: {"claude": tmp_path / ".claude" / "projects",
                                                         "codex": tmp_path / ".codex" / "sessions"})

    listing = session_logs.run({"action": "list"})
    assert "Claude Code · id 969b022c" in listing and "Codex · id rollout-2026-11-16T18-51-10-01a1" in listing
    assert "first request: fix the resize bug" in listing and "finish what my claude session did" in listing
    read = session_logs.run({"action": "read", "agent": "codex", "id": "latest"})
    assert "the session's own log" in read and "This is the record itself, not a summary" in read
    assert "I installed the rebuilt AppImage" in read and "environment_context" not in read
    assert "Fixed in layout.py." in session_logs.run({"action": "read", "id": "969b022c"})
    assert "[tool Bash" in session_logs.run({"action": "read", "id": "969b", "query": "pytest"})
    assert session_logs.run({"action": "read", "id": "nope"}).startswith("Error: no session log matches")
    # A credential pasted into another agent's session doesn't travel on to Echo's model.
    for secret in ("sk-ant-api03-abcdefghijklmnopqrstuvwx", "ghp_abcdefghijklmnopqrstuvwxyz0123", "api_key=abcd1234efgh5678ijkl"):
        assert secret not in session_logs.redact(f"use {secret} please") and "[redacted secret]" in session_logs.redact(secret)
    assert session_logs.redact("the token budget is fine") == "the token budget is fine"


def test_agent_sessions_is_read_only_outside_content_and_offered_to_echo(env, monkeypatch):
    from agent.lean import chat_mode, policy
    from agent.lean.job import READ_ONLY_TOOLS

    assert session_logs.NAME in READ_ONLY_TOOLS and policy.is_untrusted_source(session_logs.NAME)
    assert chat_mode.allowed(session_logs.NAME)
    _, talking = _echo_turn(env, monkeypatch, "what did my last codex session do?", project=False)
    _, working = _echo_turn(env, monkeypatch, "what did my last codex session do?", project=True)
    assert session_logs.NAME in talking.toolbox.names and session_logs.NAME in working.toolbox.names
    # A guest never gets it: it reads the owner's own logs.
    chat = env.chats.create_thread(title="Guest", source="web")
    monkeypatch.setattr(lean_runtime.LeanSession, "_recall", lambda self, query, limit=8: [])
    guest = lean_runtime.LeanSession(
        agent=SimpleNamespace(memory=None, llm_provider=SimpleNamespace(value="ollama"),
                              model_runtime=SimpleNamespace(model_id="m")),
        session_id=chat.thread_id, request_id="req-guest", emit=lambda _e: None, cancel=threading.Event(),
        source="discord_bot", caller_role="public")
    turn = guest._build_turn(env.people.default(), "hi", history=[], depth=0)
    assert session_logs.NAME not in turn.toolbox.names


# ── 7. One description of every conversation, for both interfaces ────────────────────────────

def test_the_session_overview_says_who_answers_which_project_and_what_is_waiting(env):
    echo_chat = env.chats.create_thread(title="Oilers", source="web")
    env.store.update_thread_state(echo_chat.thread_id, active_project_id=_IDS["work"])
    glados = next(p for p in env.people.list() if p.name == "Glados")
    jarvis = next(p for p in env.people.list() if p.name == "Jarvis")
    direct = env.rooms.direct(glados.id, "Glados")
    group = env.rooms.create(name="Release crew", agent_ids=[jarvis.id, glados.id])
    for room in (direct, group):
        env.chats.create_thread(title=room.name, source="web") if env.chats.get_thread(room.thread_id) is None else None
    execution = env.store.create_execution(request_id="r1", kind="query", thread_id=echo_chat.thread_id, source="web",
                                           status="completed", query="when do the oilers play next?", model_id="qwen3.5:9b",
                                           active_project_id=_IDS["work"])
    env.store.update_execution(execution.id, response_preview="Tuesday against Florida.")
    broker = get_approval_broker()
    pending = broker.open_question(session_id=direct.thread_id, request_id="r2", question="Which?", options=["A", "B"],
                                   allow_other=True)
    try:
        rows = {row["thread_id"]: row for row in session_overview.overview(limit=50)}
    finally:
        with broker._lock:
            broker._pending.pop(pending.id, None)

    echo_row = rows[echo_chat.thread_id]
    assert echo_row["kind"] == "echo" and echo_row["agents"] == [{"id": "echo", "name": "Echo"}]
    assert echo_row["project"] == {"id": _IDS["work"], "name": "Work", "root": str(env.folder), "trusted": True}
    assert echo_row["last_request"] == "when do the oilers play next?" and echo_row["preview"] == "Tuesday against Florida."
    assert echo_row["model"] == "qwen3.5:9b" and echo_row["status"] == "idle"
    if direct.thread_id in rows:
        assert rows[direct.thread_id]["kind"] == "agent" and rows[direct.thread_id]["agents"][0]["name"] == "Glados"
        assert rows[direct.thread_id]["status"] == "needs_answer"
    if group.thread_id in rows:
        assert rows[group.thread_id]["kind"] == "group" and rows[group.thread_id]["room_name"] == "Release crew"
        assert [a["name"] for a in rows[group.thread_id]["agents"]] == ["Jarvis", "Glados"]


def test_a_builder_asked_to_remember_has_memory_save_listed(env, monkeypatch):
    """Live MI1: Glados on a local model (builder focus, no memory tools) said "I've noted that" and saved nothing."""
    _echo, _jarvis, glados = _people(env)
    monkeypatch.setattr(lean_runtime.LeanSession, "_small_builder", lambda self, persona: True)
    direct = env.rooms.direct(glados.id, "Glados")
    offered_tools = []
    for prompt in ("Remember that my build cache key is zebra-123.", "Fix the failing test in calc.py"):
        session = _session(monkeypatch, direct.thread_id)
        session.agent.memory = SimpleNamespace(add_memory_item=lambda *a, **k: "", count_items=lambda: 0)
        client = ScriptedClient([ModelTurn(content="Understood.")] * 2)
        monkeypatch.setattr(lean_runtime.LeanSession, "_client_for", lambda *_a, **_k: client)
        assert session.run(prompt, persona_id=glados.id)["success"]
        offered_tools.append({tool["function"]["name"] for tool in client.tools[0]})
    assert "memory_save" in offered_tools[0]
    assert "memory_save" not in offered_tools[1]


# ── Past-chat search recall (live MC1/MCH1, 18 November 2026) ────────────────────────────────────

def _said(env, text: str) -> str:
    earlier = env.chats.create_thread(title="Earlier", source="web")
    env.store.create_execution(thread_id=earlier.thread_id, query=text, status="completed")
    return earlier.thread_id


def _chat_search(env, monkeypatch, query: str) -> str:
    _session_, turn = _echo_turn(env, monkeypatch, "which venue did I mention?", project=False)
    return turn.toolbox.native["chat_search"].func({"query": query})


def test_past_chat_search_finds_a_fact_without_every_query_word(env, monkeypatch):
    """"offsite venue" missed "the team offsite is at Larkspur Hall": every word had to match."""
    _said(env, "For context: the team offsite is at Larkspur Hall this year.")
    out = _chat_search(env, monkeypatch, "offsite venue")
    assert "Larkspur" in out and "partial match" in out


def test_the_question_being_asked_does_not_crowd_out_the_partial_matches(env, monkeypatch):
    """Live MC1 r2: two old full matches plus the current question made three, so no partial top-up; dropping the
    question then left only the two stale ones, and the fact in a partial match never appeared."""
    _said(env, "Which venue did I mention earlier for the offsite?")
    _said(env, "The venue you mentioned for the offsite is Marlowe Hall.")
    _said(env, "For context: the team offsite is at Kestrel Hall this year.")
    session, turn = _echo_turn(env, monkeypatch, "which venue did I mention?", project=False)
    env.store.add_item(turn_id=session.execution_id, item_type="user_message", status="complete",
                       session_id=session.session_id, payload={"text": "Which venue did I mention for the offsite?"})
    out = turn.toolbox.native["chat_search"].func({"query": "offsite venue"})
    assert "Kestrel" in out


def test_the_latest_mention_comes_first(env, monkeypatch):
    """Live MC1 with a fix that only added partial matches: an earlier venue came first and the model named it."""
    import time as _time
    _said(env, "For context: the team offsite is at Rookwood Hall this year.")
    _time.sleep(0.01)
    _said(env, "For context: the team offsite is at Fenwick Hall this year.")
    out = _chat_search(env, monkeypatch, "offsite venue")
    assert out.index("Fenwick") < out.index("Rookwood")


def test_an_exact_match_is_not_marked_partial_and_stop_words_alone_find_nothing(env, monkeypatch):
    _said(env, "Our vendor's account manager is Dara Okafor.")
    exact = _chat_search(env, monkeypatch, "account manager")
    assert "Dara" in exact and "partial match" not in exact
    assert "No past messages match" in _chat_search(env, monkeypatch, "what was the name")
