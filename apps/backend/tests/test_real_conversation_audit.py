"""Regressions from the owner's real Pong and GTA-clone sessions (conversation audit, 19 November 2026).

docs/research/conversation-audit-2026-11-19.md has the evidence for each case:

1. Context at 16K: a file read the window couldn't keep was cut to its first characters, compaction summaries piled
   up in the user message (17 of them, 15.5K characters, by one long task's 72nd call), and the model re-read the
   same unchanged files for 37 of 49 steps.
2. A turn's file work was invisible to the next turn: "continue" rebuilt a Python project as a JavaScript one.
3. "why stopped? and continue" became the work's objective, and nothing told the model why the last turn stopped.
4. Opening the game: a launcher that hands the file to a running browser exits at once, was reported as "nothing
   running", and was launched again and again (six approvals).
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from agent.lean import loop as loop_module
from agent.lean import read_outline
from agent.lean.loop import COMPACT_FAILURES, LeanTurn
from agent.lean.personas import AgentPersona
from agent.lean.provider import ModelTurn, ToolCall
from tests.test_harness_eval_findings import Client, _project, _turn

GAME_JS = """class Game {
    constructor() {
        this.canvas = document.getElementById('game');
        this.ctx = this.canvas.getContext('2d');
        this.input = new InputHandler();
    }

    update(dt) {
        this.player.x += this.input.dx * dt;
    }

    render() {
        this.ctx.fillRect(0, 0, 10, 10);
    }

    loop() {
        this.update(16);
        this.render();
        requestAnimationFrame(() => this.loop());
    }
}

const game = new Game();
game.loop();
"""

INDEX_HTML = """<!DOCTYPE html>
<html>
<body>
    <canvas id="game"></canvas>
    <div id="hud"></div>
    <script src="input_handler.js"></script>
    <script src="game.js"></script>
</body>
</html>
"""

CAR_PY = '''"""Vehicle system."""
import math


class Car:
    def __init__(self, speed):
        self.speed = speed

    def update(self, dt):
        return self.speed * dt


async def spawn(n):
    return [Car(1) for _ in range(n)]
'''


def _read_result(path: str, text: str) -> str:
    """What coding.file_read returns for the whole file."""
    lines = text.splitlines()
    width = len(str(len(lines)))
    return f"{path} ({len(lines)} lines)\n" + "\n".join(f"{str(n).rjust(width)}| {line}" for n, line in enumerate(lines, 1))


# ── 1a. the outline that survives a trimmed read ─────────────────────────
def test_a_trimmed_javascript_read_keeps_its_classes_and_methods_with_line_numbers():
    content = _read_result("/w/GTA_CLONE/game.js", GAME_JS * 3)
    short = read_outline.summary(content, "trimmed to fit the context window")
    assert short is not None and len(short) < len(content) // 3
    assert short.startswith("/w/GTA_CLONE/game.js (72 lines; you read all of it)")
    assert "L1 class Game" in short and "L2 constructor()" in short and "L8 update()" in short and "L16 loop()" in short
    assert "offset and limit" in short
    assert "this.ctx.fillRect" not in short  # the code itself is what's dropped


def test_a_trimmed_html_read_keeps_the_scripts_it_loads_and_python_keeps_its_definitions():
    page = read_outline.summary(_read_result("/w/index.html", INDEX_HTML * 4), "trimmed")
    assert "script input_handler.js" in page and "script game.js" in page and "<canvas id=game>" in page
    car = read_outline.summary(_read_result("/w/car.py", CAR_PY * 4), "trimmed")
    assert "class Car" in car and "__init__()" in car and "spawn()" in car


def test_only_file_reads_get_an_outline():
    assert read_outline.summary("Search provider: duckduckgo\n1. a result", "trimmed") is None
    assert read_outline.summary("Error: no such file", "trimmed") is None
    assert read_outline.read_path(_read_result("/w/game.js", GAME_JS)) == "/w/game.js"


def _pressured_turn(monkeypatch, client=None) -> LeanTurn:
    monkeypatch.setattr(loop_module.settings, "context_tokens", lambda endpoint=None: 6000)
    monkeypatch.setattr(loop_module.settings, "max_output_tokens", lambda endpoint=None: 2048)
    turn = LeanTurn(client=client or Client([]), persona=AgentPersona(id="echo", name="Echo"), system_prompt="s",
                    history=[], toolbox=_project(Path("/tmp"))[0], session_id="t", request_id="r", execution_id="e",
                    emit=lambda e: None, cancel=threading.Event(), persist_tool_runs=False)
    turn._history_in_messages = 0
    return turn


def _reading_steps(n: int, text: str = GAME_JS * 5) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for i in range(n):
        call = {"id": f"c{i}", "type": "function", "function": {"name": "file_read", "arguments": f'{{"path": "f{i}.js"}}'}}
        messages += [{"role": "assistant", "content": "", "tool_calls": [call]},
                     {"role": "tool", "tool_call_id": f"c{i}", "name": "file_read",
                      "content": _read_result(f"/w/f{i}.js", text)}]
    return messages


def test_under_pressure_older_reads_shrink_to_their_outline_not_their_first_lines(monkeypatch):
    turn = _pressured_turn(monkeypatch)
    turn._compact_failures = COMPACT_FAILURES  # trimming only, no summary call
    messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "review the game"}] + _reading_steps(8)
    turn._fit_context(messages, [])
    tools = [m for m in messages if m["role"] == "tool"]
    assert "Outline: L1 class Game" in tools[0]["content"]
    assert "this.canvas = document" not in tools[0]["content"]  # not the head of the file
    assert "this.canvas = document" in tools[-1]["content"]  # the newest read stays whole


# ── 1b. one summary, not a pile of them ─────────────────────────────────
def test_a_second_compaction_replaces_the_first_summary_and_builds_on_it(monkeypatch):
    client = Client([ModelTurn(content="Goal: review. Read f0-f3. Next: fix loop()."),
                     ModelTurn(content="Goal: review. Read f0-f7. Next: fix render().")])
    turn = _pressured_turn(monkeypatch, client)
    messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "review the game"}] + _reading_steps(6)
    assert turn._compact_turn(messages, keep_steps=2)
    messages += _reading_steps(6)
    assert turn._compact_turn(messages, keep_steps=2)
    user = messages[1]["content"]
    assert user.startswith("review the game")
    assert user.count("summarized to fit the context window") == 1  # replaced, not appended
    assert "Next: fix render()" in user and "Next: fix loop()" not in user
    assert "Next: fix loop()" in client.calls[1][0]["content"]  # the new summary was written from the old one


def test_compaction_keeps_the_files_read_so_far_with_their_outlines(monkeypatch):
    turn = _pressured_turn(monkeypatch, Client([ModelTurn(content="Goal: review the game.")]))
    messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "review the game"}] + _reading_steps(5)
    assert turn._compact_turn(messages, keep_steps=2)
    user = messages[1]["content"]
    assert "Files read in this request" in user
    assert "/w/f0.js (" in user and "L1 class Game" in user


# ── 1c. re-reading an unchanged file ────────────────────────────────────
def test_the_third_read_of_an_unchanged_file_says_why_rereading_wont_fit(tmp_path):
    (tmp_path / "game.js").write_text(GAME_JS)
    box, _ran = _project(tmp_path)
    client = Client([
        ModelTurn(tool_calls=[ToolCall("a", "file_read", '{"path": "game.js"}')]),
        ModelTurn(tool_calls=[ToolCall("b", "file_read", '{"path": "game.js", "offset": 1, "limit": 30}')]),
        ModelTurn(tool_calls=[ToolCall("c", "file_read", '{"path": "game.js", "limit": 100}')]),
        ModelTurn(content="Reviewed.", finish_reason="stop"),
    ])
    events: list[dict[str, Any]] = []
    turn = _turn(box, client, [], events)
    turn.run("review game.js")
    results = [m["content"] for m in client.calls[-1] if m.get("role") == "tool"]
    assert "read game.js 3 times" in results[-1] and "offset" in results[-1]
    assert "times" not in results[0] and "times" not in results[1]
    assert turn._rereads == 1
    assert any(e["type"] == "reread" for e in events)


def test_a_read_after_the_file_changed_starts_the_count_again(tmp_path):
    target = tmp_path / "game.js"
    target.write_text(GAME_JS)
    box, _ran = _project(tmp_path)
    turn = _turn(box, Client([]), [], [])
    assert turn._note_reread(_read_result(str(target), GAME_JS)) == ""
    assert turn._note_reread(_read_result(str(target), GAME_JS)) == ""
    target.write_text(GAME_JS + "// changed\n")
    import os
    os.utime(target, ns=(1, 1))
    assert turn._note_reread(_read_result(str(target), GAME_JS)) == ""  # changed: a fresh first read


def test_rereads_are_not_progress_for_automatic_continuation(tmp_path):
    box, _ran = _project(tmp_path)
    turn = _turn(box, Client([]), [], [])
    turn.auto_continue = True
    turn._outcomes = [True] * 12
    turn._segment = (0, 0)
    turn._extended = 0
    calls = {f"sig{i}": 1 for i in range(12)}
    turn._rereads = 11  # eleven of the twelve "new" calls were re-reads of unchanged files
    assert not turn._continue_past([], 12, 12, calls)
    turn._segment, turn._rereads, turn._segment_rereads = (0, 0), 0, 0
    assert turn._continue_past([], 12, 12, calls)  # twelve real new actions do continue


# ── 2. what a turn did reaches the next turn ─────────────────────────────
def _run(name: str, args: dict[str, Any], ok: bool = True) -> dict[str, Any]:
    return {"tool_name": name, "canonical_arguments": args, "status": "complete" if ok else "failed",
            "outcome": {"success": ok}}


def _gta_turns() -> list[dict[str, Any]]:
    """The owner's GTA session (19 November 2026), as the state store keeps it."""
    research = {
        "execution_id": "e-research", "status": "completed", "success": True,
        "execution": {"query": "in GTA_CLONE lets start development with it, first get research and then start coding",
                      "metadata": {"chat_run": {"stop_reason": "no_answer"}}},
        "tool_runs": [
            _run("web_search", {"query": "GTA 6 features mechanics"}),
            _run("terminal", {"command": "mkdir -p GTA_CLONE/{src/{core,game,vehicle},assets,docs}", "where": "bwrap"}),
            _run("file_write", {"path": "GTA_CLONE/src/core/engine.py", "content": "x"}),
            _run("file_write", {"path": "GTA_CLONE/src/vehicle/car.py", "content": "x"}),
            _run("file_write", {"path": "GTA_CLONE/src/game/player.py", "content": "x"}),
            _run("file_read", {"path": "GTA_CLONE/src/game/player.py"}),
        ],
        "messages": [
            {"role": "user", "text": "in GTA_CLONE lets start development with it, first get research and then start coding"},
            {"role": "assistant", "text": "Great! Now let me continue building out the map system and input handler.",
             "agent_id": "code", "agent_name": "Echo", "stop_reason": "no_answer"},
        ],
    }
    chat = {
        "execution_id": "e-chat", "status": "completed", "success": True,
        "execution": {"query": "whats the latest reviews for gta 6", "metadata": {"chat_run": {"stop_reason": "answered"}}},
        "tool_runs": [_run("web_search", {"query": "GTA 6 reviews"})],
        "messages": [{"role": "user", "text": "whats the latest reviews for gta 6"},
                     {"role": "assistant", "text": "Metascore 99.", "agent_id": "code", "agent_name": "Echo"}],
    }
    return [chat, research]


def test_the_record_of_a_turn_names_the_files_it_wrote_and_how_it_ended():
    from agent.lean import work_record

    line = work_record.turn_line(_gta_turns()[1], root="/home/dev/Work")
    assert "GTA_CLONE/src/core/engine.py" in line and "GTA_CLONE/src/vehicle/car.py" in line
    assert "mkdir -p GTA_CLONE" in line
    assert "without a final answer" in line
    assert "web_search" not in line and "player.py" in line  # looking things up isn't work on the project
    assert work_record.turn_line(_gta_turns()[0]) == ""  # a plain answer has nothing to record


def _session(monkeypatch, role: str = "owner"):
    from agent.lean import runtime as lean_runtime
    from agent.state import get_state_store

    monkeypatch.setattr(get_state_store(), "session_timeline", lambda session_id, limit=80: {"turns": _gta_turns()})
    session = lean_runtime.LeanSession.__new__(lean_runtime.LeanSession)
    session.session_id, session.room, session.caller_role = "gta-chat", None, role
    session.personas = lean_runtime.get_persona_store()
    session.project_root = "/home/dev/Work"
    session.execution_id = "e-now"
    return session


def test_the_next_turn_sees_what_the_last_one_built(monkeypatch):
    history = _session(monkeypatch)._history()
    last = history[-1]
    assert last["role"] == "assistant" and last["content"].startswith("Great! Now let me continue")
    assert "GTA_CLONE/src/core/engine.py" in last["content"]
    assert history[1]["content"] == "Metascore 99."  # a turn without project work is unchanged


def test_a_guest_never_sees_the_owners_file_record(monkeypatch):
    history = _session(monkeypatch, role="guest")._history()
    assert "engine.py" not in " ".join(row["content"] for row in history)


# ── 3. "why stopped? and continue" ──────────────────────────────────────
def test_continuation_requests_are_recognised_and_new_requests_are_not():
    from agent.lean import work_record

    for text in ("why stopped? and continue", "continue", "keep going!", "ok carry on", "please continue",
                 "why did you stop", "resume", "go on", "finish it"):
        assert work_record.is_continuation(text), text
    for text in ("continue the conversation about hockey tomorrow and also tell me about the weather in Edmonton",
                 "create a pong game in /work", "what does continue do in python", "stop", "who won"):
        assert not work_record.is_continuation(text), text


def test_continuing_takes_the_unfinished_objective_and_says_why_the_last_turn_stopped(monkeypatch):
    session = _session(monkeypatch)
    session._user_message = "why stopped? and continue"
    goal = session._goal("why stopped? and continue")
    assert goal.startswith("in GTA_CLONE lets start development with it")
    assert "why stopped? and continue" in goal
    note = session._continuation_note()
    assert "without a final answer" in note and "GTA_CLONE" in note
    session._user_message = "whats the score of the oilers game"
    assert session._goal("whats the score of the oilers game") == "whats the score of the oilers game"
    assert session._continuation_note() == ""


def test_a_lookup_that_hit_its_step_limit_but_answered_leaves_no_record():
    from agent.lean import work_record

    turn = {"execution": {"query": "anything new with cyber leek", "metadata": {"chat_run": {"stop_reason": "max_steps"}}},
            "tool_runs": [_run("web_search", {"query": "cyber leek"})], "messages": []}
    assert work_record.turn_line(turn) == ""
    assert work_record.unfinished(turn)  # but "continue" after it still knows it was cut short


# ── 4. opening the game for the user ────────────────────────────────────
def test_an_opener_that_returns_at_once_on_this_pc_counts_as_opened():
    from agent.lean.terminal import launcher_note

    for command in ("chromium /home/dev/Work/pong/index.html 2>&1 &", "gio open file:///home/dev/Work/pong/index.html",
                    "cd /home/dev/Work/pong && xdg-open index.html", "nohup firefox index.html"):
        note = launcher_note(command, 0, "", "", "host")
        assert "Don't launch it again" in note, command


def test_an_opener_in_the_sandbox_that_found_no_desktop_says_nothing_opened():
    from agent.lean.terminal import launcher_note

    err = ("Failed to connect to user scope bus via local transport: $DBUS_SESSION_BUS_ADDRESS and $XDG_RUNTIME_DIR "
           "not defined\njq: parse error: Invalid numeric literal at line 1, column 9")
    note = launcher_note("xdg-open /home/dev/Work/pong/index.html", 0, "", err, "bwrap")
    assert note.startswith("Hint: nothing opened") and 'where="host"' in note


def test_other_commands_get_no_opener_note():
    from agent.lean.terminal import launcher_note

    for command in ("which chromium firefox google-chrome", "npm start", "chromium --version", "ls -la pong/",
                    "python3 -m http.server 8000"):
        assert launcher_note(command, 0, "", "", "host") == "", command
    assert launcher_note("chromium --headless --dump-dom index.html", 0, "<html>…</html>", "", "host") == ""


def test_the_terminal_tool_puts_the_opener_note_on_a_successful_run(tmp_path, monkeypatch):
    from agent.lean import terminal as terminal_module

    class Done:
        finished, code, out, err = True, 0, "", ""

    term = terminal_module.Terminal(str(tmp_path))
    monkeypatch.setattr(term, "_start", lambda command, args, wait: (Done(), "", tmp_path, "host"))
    assert "Don't launch it again" in term.process_start({"command": "chromium index.html"})
    assert "nothing running to check on" in term.process_start({"command": "true"})
