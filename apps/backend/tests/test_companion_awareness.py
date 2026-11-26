from types import SimpleNamespace
import time

from agent import admission
from agent.companion import awareness, commentary, hook_client, knowledge, observe, soul
from agent.companion.text import spoken


def _fixture(tmp_path, monkeypatch):
    now = time.time()
    store = observe.CompanionStore(tmp_path / "observe.db")
    scope = {"enabled": True, "capture_prompts": True,
             "agents": {"claude-code": True, "codex": True}, "roots": [str(tmp_path / "work")]}
    monkeypatch.setattr(hook_client, "scope", lambda: scope)
    monkeypatch.setattr(knowledge, "_project_for",
                        lambda cwd: (cwd.rsplit("/", 1)[-1], cwd) if cwd.startswith(str(tmp_path)) else ("", ""))
    monkeypatch.setattr(observe, "echo_sessions", lambda: [])
    monkeypatch.setattr(awareness.config, "companion_observe_echo", True)

    def add(id_, agent, session, kind, text="", path="work/alpha", **meta):
        store.add(dict(id=id_, agent=agent, session_id=session, at=now - len(id_), kind=kind,
                       cwd=str(tmp_path / path), project=path.rsplit("/", 1)[-1], text=text, meta=meta))

    return store, now, add


def test_checkin_keeps_independent_work_distinct_and_respects_scope(tmp_path, monkeypatch):
    store, now, add = _fixture(tmp_path, monkeypatch)
    add("c-prompt-long", "claude-code", "c1", "prompt", "restore the backend package")
    add("c-op", "claude-code", "c1", "operation_done", operation="a test command")
    add("d-prompt", "codex", "d1", "prompt", "check the index recovery")
    add("other", "codex", "d2", "report_ready", "## Summary\nThe simulator passed.", path="work/beta")
    add("revoked", "codex", "private", "report_ready", "## Summary\nPrivate deployment details.", path="private")
    snapshot = observe.activity(store, include_echo=False, now=now, ingest=False)
    cards = awareness.collect(store, snapshot, now - 600, project_id="alpha")
    text, ids = awareness.summarize(cards)
    assert ids == ["c-op"]
    assert "Claude finished a test command" in text and "Codex is working" in text
    assert "same project" in text and "collaborat" not in text and "Private" not in text
    assert "simulator" not in text


def test_checkin_understands_observed_worker_relationship(tmp_path, monkeypatch):
    store, now, add = _fixture(tmp_path, monkeypatch)
    add("root-prompt", "echo", "parent", "prompt", "investigate the package installation", via="native")
    add("worker-done", "researcher", "child", "subagent_completed", "Compared package metadata",
        via="native", parent_session="parent")
    cards = awareness.collect(store, observe.activity(store, include_echo=False, now=now, ingest=False), now - 600)
    assert "finished a research step for Echo" in awareness.summarize(cards)[0]


def test_periodic_review_needs_new_evidence_and_uses_lower_priority(tmp_path, monkeypatch):
    store, now, add = _fixture(tmp_path, monkeypatch)
    for name, value in dict(companion_enabled=True, companion_checkin_minutes=30, companion_speak=True,
                            companion_knowledge=False, companion_soul_style="templates", companion_quiet_hours="").items():
        monkeypatch.setattr(commentary.config, name, value)
    calls = []
    channel = {}
    announcer = commentary.Commentary(store=store, speech_state=lambda: channel,
                                      speak=lambda text, **kw: calls.append((text, kw)) or {"status": "queued", "id": "s1"})
    add("start-main", "codex", "d1", "prompt", "check the local model")
    assert announcer.review(now) is None
    add("last-completion", "codex", "d1", "report_ready", "## Summary\nFound an issue in the settings parser.")
    channel["muted"] = ["companion"]
    assert announcer.review(now) is None
    channel.clear()
    channel["queued"] = [{"priority": 40}]
    assert announcer.review(now) is None
    channel.clear()
    item = announcer.review(now)
    assert item["phase"] == "checkin" and item["speech_id"] == "s1"
    assert "Codex" in item["text"] and calls[0][1]["priority"] == 20
    restarted = commentary.Commentary(store=observe.CompanionStore(tmp_path / "observe.db"))
    assert restarted.review(now + 21) is None
    assert announcer.review(now + 3700) is None
    announcer.schedule_review(10)
    assert announcer._next_review == 1810
    monkeypatch.setattr(commentary.config, "companion_checkin_minutes", 60)
    announcer.schedule_review(20)
    assert announcer._next_review == 3620
    monkeypatch.setattr(commentary.config, "companion_checkin_minutes", 0)
    assert announcer.review(now) is None


def test_synthesis_can_rank_facts_but_cannot_invent_prose(monkeypatch):
    from agent import memory

    lookups = []
    monkeypatch.setattr(awareness.config, "embedding", SimpleNamespace(provider="onnx_local"))
    monkeypatch.setattr(memory, "get_agent_memory", lambda: SimpleNamespace(
        embedding_health={"provider": "onnx_local"},
        retrieve_relevant=lambda *_a, **_kw: [SimpleNamespace(metadata={"memory_id": "foreign"}),
                                             SimpleNamespace(metadata={"memory_id": "known"})],
        runtime_memory_projection=lambda query, **kw: lookups.append(kw) or [
            {"memory_id": "known", "scope": "project", "project_id": "alpha", "content": "the authorized background"},
            {"scope": "project", "project_id": "different", "content": "private other project"},
        ]))
    cards = [awareness.Card("c", "Claude", "alpha", "/alpha", "stage_completed", "the loader", "Claude finished a stage"),
             awareness.Card("d", "Codex", "alpha", "/alpha", "needs_permission", "the install", "Codex needs your permission")]

    def model(messages, **kwargs):
        assert "the authorized background" in messages[-1]["content"]
        assert "private other project" not in messages[-1]["content"]
        return "[0, 1]"

    monkeypatch.setattr(soul, "local_reply", model)
    text, ids = awareness.summarize(cards, use_model=True)
    assert ids == ["c", "d"] and text.startswith("Claude")
    assert len(lookups) == 1
    monkeypatch.setattr(soul, "local_reply", lambda *_a, **_kw: "Everything was deployed.")
    assert awareness.summarize(cards, use_model=True)[1] == ["d", "c"]


def test_preparing_caption_and_speech_is_idempotent():
    text = spoken("## Status\n- **Codex** checked PR #232; see [the report](https://example.org/report).\n"
                  "- The test log is at /home/dev/Work/log.txt. The TUI is ready.")
    assert "Codex checked pull request 232." in text and "terminal interface" in text
    assert all(value not in text for value in (";", "https:", "/home", "##", "*", "Codex*"))
    assert text == spoken(text)


def test_try_admission_yields_when_another_model_is_active():
    import pytest

    gate = admission.Admission()
    with gate.condition:
        gate.active = 1
    with pytest.raises(InterruptedError):
        with gate.enter(wait=False):
            raise AssertionError("the Companion cannot queue behind foreground work")
    assert not gate.waiters
