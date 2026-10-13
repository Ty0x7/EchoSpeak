"""Screen-guided desktop control can be allowed for one chat; other outside content still asks every time."""
import json
import threading
import uuid

import pytest
from PIL import Image

from agent.lean import computer, observations, policy
from agent.lean.approvals import get_approval_broker
from agent.lean.loop import LeanTurn
from agent.lean.personas import AgentPersona
from agent.lean.provider import ModelTurn, ToolCall
from agent.lean.toolbox import Toolbox
from config import config


@pytest.fixture
def screen(monkeypatch, tmp_path):
    monkeypatch.setattr(observations, "root", lambda: tmp_path / "observations")
    monkeypatch.setattr(config, "enable_system_actions", True)
    monkeypatch.setattr(config, "allow_desktop_automation", True)
    monkeypatch.setattr(policy, "_secret_values", lambda: [])
    audits = []
    monkeypatch.setattr(policy, "audit", lambda decision, **kw: audits.append((decision.rule, kw.get("outcome"))))
    session = "chat-" + uuid.uuid4().hex[:8]
    item = observations.save(Image.new("RGB", (100, 80), "red"), session, "Game")
    monkeypatch.setattr(computer, "_grab", lambda *a: item)
    tools = computer.tools(session, threading.Event())
    ran = []
    # No real input: the action returns the same screen.
    next(t for t in tools if t.name == "computer_action").func = lambda args: ran.append(args) or computer._result(item, "Simulated.")
    box = Toolbox(toolsets=["vision", "desktop"], extra_tools=tools, session_id=session)
    return session, item, box, ran, audits


def run_turn(session, item, box, decision, taint=None, batches=3):
    broker = get_approval_broker()
    requests = []

    def emit(event):
        if event.get("type") == "approval_request":
            requests.append(event)
            broker.resolve(event["id"], decision)  # the owner clicks right away

    class Model:
        count = 0

        def stream_turn(self, messages, **kwargs):
            self.count += 1
            if self.count == 1:
                return ModelTurn(tool_calls=[ToolCall("s", "take_screenshot", "{}")])
            if self.count <= 1 + batches:
                return ModelTurn(tool_calls=[ToolCall(f"a{self.count}", "computer_action", json.dumps({
                    "observation_id": item["id"], "actions": [{"action": "key", "text": "w"}]}))])
            return ModelTurn(content="Done.")

    LeanTurn(client=Model(), persona=AgentPersona(id="echo", name="Echo"), system_prompt="test", history=[],
             toolbox=box, session_id=session, request_id="r", execution_id="e", emit=emit,
             cancel=threading.Event(), persist_tool_runs=False, taint=taint).run("Play")
    return requests


def test_for_this_chat_covers_later_screen_guided_batches(screen):
    session, item, box, ran, audits = screen
    requests = run_turn(session, item, box, "always")
    assert len(ran) == 3 and len(requests) == 1
    assert requests[0]["grantable"] is True and "on screen" in requests[0]["reason"]
    assert audits.count(("screen_control", "allowed for this chat")) == 2


def test_allow_once_still_asks_for_the_next_batch(screen):
    session, item, box, ran, _ = screen
    assert len(run_turn(session, item, box, "allow")) == 3 and len(ran) == 3


def test_web_content_in_the_turn_keeps_asking_every_time(screen):
    session, item, box, ran, _ = screen
    requests = run_turn(session, item, box, "always", taint=["web_search"])
    assert len(ran) == 3 and len(requests) == 3
    assert all(r["grantable"] is False and "web_search" in r["reason"] for r in requests)
    assert not get_approval_broker().granted(session, "computer_action")


def test_screen_only_taint_is_not_trusted_without_a_person():
    decision = policy.evaluate("computer_action", {}, tainted_by=["take_screenshot"], interactive=False)
    assert decision.action == "deny" and decision.rule == "screen_control"
    assert policy.evaluate("email_send", {}, tainted_by=["take_screenshot"]).rule == "rule_of_two"
