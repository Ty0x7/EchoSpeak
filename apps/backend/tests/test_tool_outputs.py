"""Long tool results are kept whole behind a chat-scoped handle instead of being cut."""
import json
import threading

import pytest

from agent.lean import policy, tool_outputs
from agent.lean.loop import LeanTurn
from agent.lean.personas import AgentPersona
from agent.lean.provider import ModelTurn, ToolCall
from agent.lean.toolbox import NativeTool, Toolbox


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(tool_outputs, "root", lambda: tmp_path / "outputs")
    monkeypatch.setattr(policy, "_secret_values", lambda: [])


LOG = "\n".join(f"line {n}: {'ERROR disk full' if n == 4321 else 'ok'}" for n in range(1, 6001))


def test_slices_and_search_reach_the_middle_a_cut_would_lose():
    ref = tool_outputs.save("chat", "terminal", LOG, untrusted=False)
    shown = tool_outputs.preview(LOG, ref, 14000)
    assert "line 4321" not in shown and ref in shown and len(shown) < 14400
    assert "4321: line 4321: ERROR disk full" in tool_outputs.read("chat", {"id": ref, "query": "error"})
    part = tool_outputs.read("chat", {"id": ref, "offset": 100, "length": 50})
    assert "characters 100–150" in part and "Next: offset 150" in part


def test_outputs_stay_in_their_chat_and_reject_bad_ids():
    ref = tool_outputs.save("chat", "terminal", LOG, untrusted=False)
    with pytest.raises(PermissionError):
        tool_outputs.read("other-chat", {"id": ref})
    with pytest.raises(ValueError):
        tool_outputs.read("chat", {"id": "../../secrets"})


def test_reopened_web_text_is_still_outside_content():
    web = tool_outputs.save("chat", "safe_web_fetch", "page", untrusted=True)
    local = tool_outputs.save("chat", "file_read", "notes", untrusted=False)
    assert policy.is_untrusted_source("read_output", None, {"id": web})
    assert not policy.is_untrusted_source("read_output", None, {"id": local})
    assert policy.is_untrusted_source("read_output", None, {"id": "out-" + "0" * 24})  # unknown counts as outside


def test_loop_saves_long_results_and_the_agent_can_read_them_back():
    big = NativeTool("dump_log", "Print the log", {"type": "object", "properties": {}}, lambda args: LOG)
    box = Toolbox(toolsets=["core"], extra_tools=[big, tool_outputs.tool("chat")], session_id="chat")
    box.native["dump_log"] = big
    seen = []

    class Model:
        def stream_turn(self, messages, **kwargs):
            seen.append(messages[-1]["content"])
            if len(seen) == 1:
                return ModelTurn(tool_calls=[ToolCall("a", "dump_log", "{}")])
            if len(seen) == 2:
                ref = seen[-1].split("is saved as ")[1].split(":")[0]
                return ModelTurn(tool_calls=[ToolCall("b", "read_output", json.dumps({"id": ref, "query": "ERROR"}))])
            return ModelTurn(content="Line 4321 says the disk is full.")

    LeanTurn(client=Model(), persona=AgentPersona(id="echo", name="Echo"), system_prompt="t", history=[], toolbox=box,
             session_id="chat", request_id="r", execution_id="e", emit=lambda e: None, cancel=threading.Event(),
             persist_tool_runs=False).run("What failed?")
    assert "is saved as out-" in seen[1] and len(seen[1]) < 15000
    assert "4321: line 4321: ERROR disk full" in seen[2]


def test_cleared_old_results_get_a_handle_instead_of_vanishing():
    box = Toolbox(toolsets=["core"], extra_tools=[tool_outputs.tool("chat")], session_id="chat")
    turn = LeanTurn(client=None, persona=AgentPersona(id="echo", name="Echo"), system_prompt="t", history=[], toolbox=box,
                    session_id="chat", request_id="r", execution_id="e", emit=lambda e: None, cancel=threading.Event(),
                    persist_tool_runs=False)
    note = turn._clear_note({"role": "tool", "name": "file_read", "content": "x" * 3000}, 500, "older tool output cleared")
    ref = note.split("saved as ")[1].split(",")[0]
    assert len(note) < 650 and tool_outputs.load(ref, "chat")[1] == "x" * 3000
