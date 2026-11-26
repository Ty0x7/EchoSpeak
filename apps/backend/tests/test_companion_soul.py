"""Echo Companion's own SOUL: owner-editable phrases and a fact-guarded local-model voice."""

from __future__ import annotations

import pytest

from agent.companion import soul
from agent.companion.commentary import compose
from agent.companion.digest import analyse


@pytest.fixture(autouse=True)
def isolated_soul(tmp_path, monkeypatch):
    monkeypatch.setattr(soul, "soul_path", lambda: tmp_path / "companion" / "SOUL.md")
    soul._cache.update(mtime=-1.0, soul=None)


REPORT = "## Summary\nRebuilt the index.\n\n## Results\n- 38 passed, 0 failed.\n\n## Changes\n- Added retention.\n" + "x " * 200
EVENT = {"id": "e1", "agent": "codex", "session_id": "s", "kind": "turn_completed", "at": 1.0, "project": "harness", "meta": {}}


def test_default_soul_is_created_and_parsed():
    parsed = soul.load()
    assert soul.soul_path().exists() and "dry" in parsed["personality"]
    assert len(parsed["phrases"]["saved"]) == 3 and "{agent}" in parsed["phrases"]["success"][0]


def test_owner_phrases_drive_the_commentary():
    soul.save(soul.DEFAULT_SOUL.replace("success: Report in from {agent}{where}. | {agent} wrapped up{where}. | {agent} finished a report{where}.",
                                        "success: Heads up, {agent} landed something{where}."))
    line = compose(EVENT, analyse(REPORT), humor=0)
    assert line.startswith("Heads up, Codex landed something on harness.")


def test_broken_placeholders_fall_back_safely():
    soul.save("## Phrases\nsuccess: {nonsense} did it\n")
    assert compose(EVENT, analyse(REPORT), humor=0).startswith("Codex finished a report on harness.")


@pytest.mark.parametrize("rewrite,ok", [
    ("Codex wrapped up on harness: 38 tests passing by its account.", True),
    ("Codex wrapped up on harness with 40 tests passing.", False),             # a number that wasn't there
    ("Codex verified the harness: 38 tests passing.", False),                  # a claim that wasn't there
    ("Wrapped up on harness, 38 tests passing.", False),                       # lost who did it
    ("", False),
    ("Codex wrapped up on harness with 38 tests passing.", False),             # a reported result stated as fact
    ("Codex says it wrapped up on harness with 38 tests passing.", True),
])
def test_fact_check_keeps_only_faithful_rewrites(rewrite, ok):
    grounded = "Codex finished a report on harness. 38 tests passing by its account."
    assert soul.fact_check(grounded, rewrite, "Codex")[0] is ok


def test_never_list_is_enforced():
    soul.save(soul.DEFAULT_SOUL.replace("## Never\n", "## Never\n- suspicious\n"))
    grounded = "Codex finished. Everything passed, which is honestly a little suspicious."
    assert soul.fact_check(grounded, "Codex finished; suspicious, frankly.", "Codex")[0] is False


def test_reword_falls_back_without_a_local_model(monkeypatch):
    import agent.lean.provider as provider

    monkeypatch.setattr(provider, "resolve_endpoint", lambda *a, **k: type("E", (), {"local": False})())
    text, info = soul.reword("Codex finished a report.", "Codex")
    assert text == "Codex finished a report." and info["used"] is False and info["reason"] == "ValueError"
