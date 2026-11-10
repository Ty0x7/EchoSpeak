import re

from echospeak_cli.render import Renderer, sanitize

# Recorded from a real backend run against the scripted model (trimmed fields).
RUN = [
    {"type": "run_start", "execution_id": "e1", "_replay_seq": 1},
    {"type": "agent_start", "agent": {"id": "chat", "name": "fake-scripted-model", "title": "Chat"},
     "agent_id": "chat", "message_id": "m1", "_replay_seq": 2},
    {"type": "step_start", "step": 1, "message_id": "m1", "_replay_seq": 3},
    {"type": "agent_token", "step": 1, "data": "Hello ", "message_id": "m1", "_replay_seq": 4},
    {"type": "agent_token", "step": 1, "data": "from the scripted model.", "message_id": "m1", "_replay_seq": 5},
    {"type": "token_usage", "prompt": 10, "message_id": "m1", "_replay_seq": 6},
    {"type": "agent_done", "text": "Hello from the scripted model.", "success": True, "error": "", "stop_reason": "",
     "message_id": "m1", "_replay_seq": 7},
    {"type": "final", "runtime": "lean", "response": "Hello from the scripted model.", "success": True, "_replay_seq": 8},
    {"type": "status", "agent_mode": "idle", "_replay_seq": 9},
]

CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def render(events, **kw):
    r = Renderer(**kw)
    return r, "".join(chunk for e in events for chunk in r.feed(e))


def test_plain_run_renders_header_streamed_text_and_no_escapes():
    r, out = render(RUN)
    assert out == "● fake-scripted-model  Chat\nHello from the scripted model.\n"
    assert r.text_so_far() == "Hello from the scripted model." and r.final_success is True
    assert "\x1b" not in out


def test_color_only_when_asked():
    _, out = render(RUN, color=True)
    assert "\x1b[1m" in out and "Hello from the scripted model." in out


def test_tools_reasoning_outcome_and_stop_reason():
    events = [
        {"type": "agent_start", "agent": {"name": "Echo"}, "message_id": "m"},
        {"type": "reasoning_delta", "step": 1, "text": "secret plan", "message_id": "m"},
        {"type": "reasoning_delta", "step": 1, "text": " more", "message_id": "m"},
        {"type": "tool_start", "step": 1, "id": "t1", "name": "web_search", "label": "Searching cats", "message_id": "m"},
        {"type": "tool_end", "step": 1, "id": "t1", "name": "web_search", "ok": True, "outcome": "ok",
         "done_label": "Searched cats", "summary": "14 results", "duration_ms": 1234, "message_id": "m"},
        {"type": "tool_start", "step": 1, "id": "t2", "name": "terminal", "label": "Running ls", "message_id": "m"},
        {"type": "tool_end", "step": 1, "id": "t2", "ok": False, "output": "boom\nline2", "duration_ms": 50, "message_id": "m"},
        {"type": "context_compacted", "steps": 3, "message_id": "m"},
        {"type": "agent_token", "step": 2, "data": "Done", "message_id": "m"},
        {"type": "agent_done", "success": True, "stop_reason": "max_steps", "message_id": "m"},
        {"type": "run_outcome", "status": "stopped", "reason": "you stopped it"},
    ]
    _, out = render(events)
    lines = out.splitlines()
    assert "thinking…" in lines and "secret plan" not in out
    assert "  ✓ Searched cats · 14 results (1.2s)" in lines
    assert "  ✗ Running ls · boom line2 (50ms)" in lines
    assert any("summarized" in l for l in lines)
    assert "Done" in lines and "  stopped: max_steps" in lines
    assert lines[-1] == "■ outcome: stopped — you stopped it"
    _, shown = render(events, show_thinking=True)
    assert "secret plan more" in shown


def test_approval_block_shows_exact_args_and_question_options():
    r = Renderer()
    out = "".join(r.feed({"type": "approval_request", "id": "ap1", "tool": "terminal", "summary": "Run rm -rf build",
                          "reason": "deletes files", "args": {"command": "rm -rf build"}, "message_id": "m"}))
    assert "┌─ approval needed: terminal" in out and '"command": "rm -rf build"' in out
    assert "reason: deletes files" in out and "└─ id ap1" in out and "ap1" in r.pending_approvals
    q = "".join(Renderer().feed({"type": "approval_request", "id": "q1", "kind": "question", "question": "Which?",
                                 "options": ["red", "blue"], "message_id": "m"}))
    assert "question from the agent" in q and "1. red" in q and "2. blue" in q


def test_unknown_and_malformed_events_are_ignored():
    r = Renderer()
    assert r.feed({"type": "brand_new_event", "x": 1}) == []
    assert r.feed({"type": "tool_end", "duration_ms": "not-a-number", "ok": True}) != None  # noqa: E711
    assert r.feed({"type": "task_board", "tasks": "oops"}) == []
    assert r.feed({}) == []


def test_multiple_agents_interleaving_marks_continuation():
    events = [
        {"type": "agent_start", "agent": {"name": "A"}, "message_id": "a"},
        {"type": "agent_token", "data": "one", "message_id": "a"},
        {"type": "agent_start", "agent": {"name": "B"}, "message_id": "b"},
        {"type": "agent_token", "data": "two", "message_id": "b"},
        {"type": "agent_token", "data": " more", "message_id": "a"},
    ]
    _, out = render(events)
    assert out.splitlines() == ["● A", "one", "● B", "two", "(A, continued)", " more"]


def test_stream_text_off_collects_answer_without_printing():
    r, out = render(RUN, stream_text=False)
    assert "Hello" not in out and r.text_so_far() == "Hello from the scripted model."


def test_server_strings_are_sanitized_everywhere():
    evil = "x\x1b]52;c;ZXZpbA==\x07y\x1b]0;pwned\x1b\\z\x1b[2J\x1b[31m!\x9b31m\r\x08"
    events = [
        {"type": "agent_start", "agent": {"name": evil, "title": evil}, "message_id": "m"},
        {"type": "agent_token", "data": evil, "message_id": "m"},
        {"type": "tool_start", "id": "t", "label": evil, "message_id": "m"},
        {"type": "tool_end", "id": "t", "ok": False, "output": evil, "summary": evil, "message_id": "m"},
        {"type": "approval_request", "id": evil, "tool": evil, "summary": evil, "reason": evil, "args": {"k": evil}},
        {"type": "error", "message": evil},
        {"type": "run_outcome", "status": evil, "summary": evil},
    ]
    _, out = render(events)
    assert not CTRL.search(out.replace("\n", "").replace("\t", ""))
    # Approval args are shown exactly, as JSON: control bytes appear as inert, visible \u001b escapes.
    args_line = next(l for l in out.splitlines() if '"k"' in l)
    assert "\\u001b]0;pwned" in args_line
    rest = out.replace(args_line, "")
    assert "pwned" not in rest and "ZXZpbA" not in rest


def test_sanitize_cases():
    assert sanitize("a\x1b]52;c;SGVsbG8=\x07b") == "ab"            # OSC 52 clipboard write (BEL)
    assert sanitize("a\x1b]52;c;SGVsbG8=\x1b\\b") == "ab"          # OSC 52 with ST
    assert sanitize("a\x1b]2;title\x07b") == "ab"                   # window title
    assert sanitize("a\x1b]8;;http://evil\x07link\x1b]8;;\x07") == "alink"  # hyperlink
    assert sanitize("a\x1b[31mred\x1b[0m") == "ared"                # CSI SGR
    assert sanitize("a\x1b[2J\x1b[H") == "a"                        # clear screen / home
    assert sanitize("a\x9b2Jb") == "ab"                             # 8-bit CSI
    assert sanitize("a\x9d0;t\x9cb") == "ab"                        # 8-bit OSC
    assert sanitize("a\x1bPq#0\x1b\\b") == "ab"                     # DCS (sixel)
    assert sanitize("a\x1b]0;unterminated title") == "a"           # unterminated OSC swallowed
    assert sanitize("lone\x1b") == "lone" and sanitize("a\x07b\x08c\x00d\x7f") == "abcd"
    assert sanitize("safe\rEVIL") == "safe\nEVIL"                   # CR overwrite trick made visible
    assert sanitize("a\r\nb\tc") == "a\nb\tc"
    assert sanitize("x‮evil⁦y") == "xevily"               # bidi overrides
    assert sanitize(None) == "" and sanitize(5) == "5"
    # Sequences split across stream chunks never reassemble: each chunk loses its ESC/BEL bytes.
    joined = sanitize("\x1b]0;ti") + sanitize("tle\x07")
    assert "\x1b" not in joined and "\x07" not in joined
