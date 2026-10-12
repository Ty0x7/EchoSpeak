"""Smart model choice (agent/learning/routing.py): off by default, explains itself, never
uses a model, provider or budget the user didn't allow, and learns only from real outcomes."""
from __future__ import annotations

import time

import pytest

from agent import learning
from agent.learning import profiles, routing
from agent.learning.episodes import classify_error
from agent.learning.store import Episode, ExperienceStore
from config import config

LOCAL_A = ("ollama", "qwen3.6:35b-a3b")
LOCAL_B = ("lmstudio", "gemma-4-12b")
CLOUD = ("gemini", "gemini-flash")


@pytest.fixture()
def store(monkeypatch, tmp_path):
    monkeypatch.setenv("LEARNING_ENABLED", "1")
    monkeypatch.setenv("LEARNING_MODE", "on")
    fresh = ExperienceStore(tmp_path / "experience.db")
    learning.set_experience_store(fresh)
    profiles.forget_cache()
    routing.forget_cache()
    yield fresh
    learning.set_experience_store(None)
    routing.forget_cache()
    fresh.close()


@pytest.fixture()
def settings(monkeypatch):
    def apply(**values):
        defaults = {"routing_mode": "off", "routing_pool": [], "routing_auto_agents": [],
                    "routing_allow_cloud": False, "routing_daily_cloud_tokens": 0}
        for key, value in {**defaults, **values}.items():
            monkeypatch.setattr(config, key, value, raising=False)
        routing.forget_cache()
    apply()
    return apply


@pytest.fixture()
def everything_available(monkeypatch):
    monkeypatch.setattr(routing, "availability", lambda ref: (True, ""))


def _record(store, ref, kind, outcome, n, *, level=2, error_kind="", routed="user", tokens=0, agent="echo"):
    for _ in range(n):
        store.add_episode(Episode(agent_id=agent, goal=f"{kind} task", outcome=outcome, task_kind=kind, level=level,
                                  provider=ref[0], model=ref[1], error_kind=error_kind, routed=routed,
                                  tokens=tokens, duration_s=10.0 if tokens else 0.0))
    routing.forget_profiles()


@pytest.mark.parametrize("text,kind", [
    ("Model provider returned HTTP 503: overloaded", "outage"),
    ("ConnectError: [WinError 10061] connection refused", "outage"),
    ("Gemini: API key rejected (HTTP 401).", "config"),
    ("Private mode is on: ai models would send ...", "config"),
    ("Rate limit reached (HTTP 429).", "rate_limit"),
    ("This model's maximum context length is 8192 tokens", "context"),
    ("something odd", "other"),
])
def test_why_a_model_call_failed(text, kind):
    assert classify_error(text) == kind


def test_outages_never_count_against_a_model(store):
    _record(store, LOCAL_A, "coding", "success", 3)
    _record(store, LOCAL_A, "coding", "failure", 1)
    _record(store, LOCAL_A, "coding", "error", 4, error_kind="outage")
    stats = routing.profiles()[LOCAL_A]["kinds"]["coding"].public()
    assert stats["wins"] == 3 and stats["losses"] == 1 and stats["decided"] == 4
    assert stats["errors"] == {"outage": 4}


def test_a_few_lucky_wins_are_not_strong_evidence():
    assert routing.wilson_lower(9, 10) > routing.wilson_lower(3, 3)  # 9 of 10 beats 3 of 3
    assert routing.wilson_lower(9, 10) > routing.mean(4, 9) + routing.MARGIN


def test_off_by_default_changes_nothing(store, settings):
    assert routing.mode() == "off"
    assert routing.decide("echo", LOCAL_B, "fix the failing test") is None


def test_suggest_mode_explains_but_never_switches(store, settings, everything_available):
    settings(routing_mode="suggest", routing_pool=["ollama:qwen3.6:35b-a3b", "lmstudio:gemma-4-12b"])
    _record(store, LOCAL_A, "coding", "success", 9)
    _record(store, LOCAL_A, "coding", "failure", 1)
    _record(store, LOCAL_B, "coding", "success", 4)
    _record(store, LOCAL_B, "coding", "failure", 5)
    decision = routing.decide("echo", LOCAL_B, "fix the failing test in cart.py")
    assert decision.kind == "coding" and decision.chosen == LOCAL_A and not decision.applied
    assert decision.note().startswith("Suggestion: qwen3.6:35b-a3b (ollama)")
    assert "9 of 10 coding tasks" in decision.reason and "4 of 9" in decision.reason


def test_auto_mode_switches_only_agents_you_opted_in(store, settings, everything_available):
    settings(routing_mode="auto", routing_pool=["ollama:qwen3.6:35b-a3b", "lmstudio:gemma-4-12b"],
             routing_auto_agents=["jarvis"])
    _record(store, LOCAL_A, "coding", "success", 9)
    _record(store, LOCAL_A, "coding", "failure", 1)
    assert not routing.decide("echo", LOCAL_B, "fix the failing test").applied
    picked = routing.decide("jarvis", LOCAL_B, "fix the failing test")
    assert picked.applied and picked.note().startswith("Model: qwen3.6:35b-a3b (ollama) instead of")


def test_not_enough_experience_keeps_your_model(store, settings, everything_available):
    settings(routing_mode="auto", routing_pool=["ollama:qwen3.6:35b-a3b"], routing_auto_agents=["echo"])
    _record(store, LOCAL_A, "coding", "success", routing.MIN_EVIDENCE - 1)
    decision = routing.decide("echo", LOCAL_B, "fix the failing test")
    assert decision.chosen == LOCAL_B and not decision.note() and "Not enough experience" in decision.reason


def test_paid_cloud_models_need_permission_and_respect_the_daily_cap(store, settings, everything_available):
    settings(routing_mode="auto", routing_pool=["gemini:gemini-flash"], routing_auto_agents=["echo"])
    _record(store, CLOUD, "research", "success", 10)
    blocked = routing.decide("echo", LOCAL_B, "research the latest local models")
    assert blocked.chosen == CLOUD and not blocked.applied and "aren't allowed" in blocked.reason
    settings(routing_mode="auto", routing_pool=["gemini:gemini-flash"], routing_auto_agents=["echo"],
             routing_allow_cloud=True, routing_daily_cloud_tokens=50_000)
    assert routing.decide("echo", LOCAL_B, "research the latest local models").applied
    _record(store, CLOUD, "chat", "answered", 1, routed="router", tokens=60_000)
    capped = routing.decide("echo", LOCAL_B, "research the latest local models")
    assert not capped.applied and "cap" in capped.reason


def test_an_unavailable_model_falls_back_to_a_listed_one(store, settings, monkeypatch):
    settings(routing_mode="auto", routing_pool=["ollama:qwen3.6:35b-a3b"], routing_auto_agents=["echo"])
    monkeypatch.setattr(routing, "availability",
                        lambda ref: (False, "its server isn't running") if ref == LOCAL_B else (True, ""))
    decision = routing.decide("echo", LOCAL_B, "hello there")
    assert decision.applied and decision.chosen == LOCAL_A and "isn't available (its server isn't running)" in decision.reason


def test_private_mode_makes_cloud_models_unavailable(store, settings, monkeypatch):
    from agent.cloud_providers import cloud_config

    monkeypatch.setattr(config, "privacy_mode", "private", raising=False)
    monkeypatch.setattr(cloud_config("gemini"), "api_key", "AIza-test-key-123456", raising=False)
    ok, why = routing._check_availability(CLOUD)
    assert not ok and why.startswith("Private mode is on")


def test_episodes_record_role_provenance_tokens_and_failure_kind(store):
    from agent.lean.loop import TurnResult
    from agent.learning.episodes import build_episodes

    def result(agent, text="", success=True, error="", fresh=0):
        return TurnResult(text=text, success=success, error=error, agent_id=agent, message_id=f"m-{agent}", usage={"fresh": fresh, "total": fresh * 5},
                          timeline=[{"kind": "text", "text": text, "at": 100.0}, {"kind": "text", "text": "", "at": 112.5}])

    episodes = build_episodes(goal="research then write", results=[result("echo", "Done.", fresh=900),
                              result("scout", success=False, error="HTTP 503 overloaded")],
                              job=None, taint=[], session_id="s", execution_id="x", source="web",
                              names={}, endpoints={"echo": LOCAL_A, "scout": CLOUD}, lessons_used={}, team=True,
                              lead="echo", routed={"scout": "router"})
    echo, scout = episodes
    assert (echo.role, echo.routed, echo.tokens, echo.duration_s) == ("lead", "user", 900, 12.5)
    assert (scout.role, scout.routed, scout.outcome, scout.error_kind) == ("worker", "router", "error", "outage")


def test_a_routed_agent_uses_the_picked_model_and_says_why(store, settings, everything_available, monkeypatch):
    from agent.lean import runtime as lean_runtime
    from agent.lean.provider import ModelTurn
    from tests.test_lean_group import _session
    from tests.test_lean_runtime import ScriptedClient

    settings(routing_mode="auto", routing_pool=["ollama:qwen3.6:35b-a3b"], routing_auto_agents=["echo"])
    _record(store, LOCAL_A, "research", "success", 9)
    _record(store, LOCAL_A, "research", "failure", 1)
    monkeypatch.setattr(lean_runtime.LeanSession, "_configured_model", lambda self, persona: LOCAL_B)
    used = []
    client = ScriptedClient([ModelTurn(content="Here's what I found.")])
    session, events = _session(monkeypatch, {"echo": client})
    monkeypatch.setattr(lean_runtime.LeanSession, "_client_for",
                        lambda self, persona, routing=False: used.append(self._endpoint_for(persona)) or client)
    session.run("research the latest local model releases", persona_id="echo")
    assert (used[0].provider, used[0].model) == LOCAL_A
    start = next(e for e in events if e["type"] == "agent_start")
    assert start["model_route"].startswith("Model: qwen3.6:35b-a3b (ollama) instead of gemma-4-12b (lmstudio)")
    assert session._route_overrides == {"echo": LOCAL_A}
