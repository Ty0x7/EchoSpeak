"""Idle memory clean-up (agent/lean/memory_tidy.py): merge near-duplicates, retire
facts nobody recalls, flag contradictions for the owner, delete nothing."""
from __future__ import annotations

import threading
from datetime import datetime, timedelta

import pytest

from agent.lean import memory_tidy

NOW = datetime(2026, 10, 8, 12, 0)


def rec(mid: str, text: str, *, days_old: int = 1, recalled_days_ago: int | None = None, pinned: bool = False,
        kind: str = "fact", **extra):
    created = (NOW - timedelta(days=days_old)).isoformat()
    record = {"id": mid, "owner_id": "me", "scope": "account", "project_id": "", "memory_type": kind, "text": text,
              "active": True, "status": "active", "created_at": created, "updated_at": created,
              "contradiction_ids": [], "metadata": {"pinned": pinned}}
    if recalled_days_ago is not None:
        record["last_recalled_at"] = (NOW - timedelta(days=recalled_days_ago)).isoformat()
    record.update(extra)
    return record


class FakeMemory:
    def __init__(self, *records):
        self._records = {r["id"]: r for r in records}
        self._records_lock = threading.RLock()
        self.saves = 0

    def _load_records(self):
        pass

    def _save_records(self):
        self.saves += 1


@pytest.fixture(autouse=True)
def report_file(tmp_path, monkeypatch):
    monkeypatch.setattr(memory_tidy, "_report_path", lambda: tmp_path / "memory-tidy.json")


def test_near_duplicates_merge_into_the_pinned_or_newer_one():
    memory = FakeMemory(
        rec("a", "Prefers short answers.", days_old=10),
        rec("b", "Prefers short answers", days_old=2),
        rec("c", "Has a dog called Biscuit.", days_old=5),
    )
    report = memory_tidy.tidy(memory, now=NOW)
    assert memory._records["a"]["status"] == "merged" and memory._records["a"]["superseded_by"] == "b"
    assert memory._records["b"]["active"] and memory._records["c"]["active"]
    assert report["merged"] == [{"removed": "Prefers short answers.", "kept": "Prefers short answers", "id": "a"}]


def test_pinned_duplicates_are_never_dropped():
    memory = FakeMemory(rec("a", "Prefers short answers.", pinned=True, days_old=10), rec("b", "Prefers short answers", days_old=2))
    memory_tidy.tidy(memory, now=NOW)
    assert memory._records["a"]["active"] and memory._records["b"]["status"] == "merged"


def test_stale_facts_retire_but_pinned_profile_and_recalled_ones_stay(tmp_path):
    memory_tidy._report_path().write_text('{"tracking_since": "2026-01-01T00:00:00"}', encoding="utf-8")
    memory = FakeMemory(
        rec("old", "Was planning a trip to Rome.", days_old=200),
        rec("used", "Works at ACME.", days_old=200, recalled_days_ago=3),
        rec("pin", "Partner is called Sam.", days_old=200, pinned=True),
        rec("prof", "Name is Ty.", days_old=200, kind="profile"),
    )
    report = memory_tidy.tidy(memory, now=NOW)
    assert [r["id"] for r in report["retired"]] == ["old"] and memory._records["old"]["status"] == "retired"
    assert all(memory._records[m]["active"] for m in ("used", "pin", "prof"))


def test_the_first_run_after_upgrading_retires_nothing():
    memory = FakeMemory(rec("old", "Was planning a trip to Rome.", days_old=400))
    report = memory_tidy.tidy(memory, now=NOW)  # no tracking_since yet: recalls start being counted now
    assert report["retired"] == [] and memory._records["old"]["active"]
    assert report["tracking_since"] == NOW.isoformat()


def test_contradictions_are_flagged_not_decided():
    memory = FakeMemory(
        rec("leeds", "Lives in Leeds.", days_old=30),
        rec("york", "Lives in York.", days_old=2),
        rec("tea", "Likes green tea.", days_old=3),
    )
    report = memory_tidy.tidy(memory, now=NOW)
    assert [(f["a"]["id"], f["b"]["id"]) for f in report["flagged"]] == [("york", "leeds")]
    assert memory._records["leeds"]["active"] and memory._records["york"]["active"]
    assert memory_tidy.open_contradictions(memory) == [{"a": {"id": "leeds", "text": "Lives in Leeds."},
                                                        "b": {"id": "york", "text": "Lives in York."}}]


def test_resolving_keeps_one_or_both():
    memory = FakeMemory(rec("leeds", "Lives in Leeds.", days_old=30), rec("york", "Lives in York.", days_old=2))
    memory_tidy.tidy(memory, now=NOW)
    memory_tidy.resolve(memory, "leeds", "york", "york")
    assert memory._records["leeds"]["status"] == "replaced" and memory_tidy.open_contradictions(memory) == []

    memory = FakeMemory(rec("work1", "Works on EchoSpeak.", days_old=30), rec("work2", "Works on the website.", days_old=2))
    memory_tidy.tidy(memory, now=NOW)
    assert memory_tidy.open_contradictions(memory)
    memory_tidy.resolve(memory, "work1", "work2", "both")
    assert memory_tidy.open_contradictions(memory) == []
    assert memory_tidy.plan(memory._records, now=NOW)["flag"] == []  # doesn't come back
    with pytest.raises(ValueError):
        memory_tidy.resolve(memory, "work1", "work2", "neither")


def test_it_runs_once_a_day_after_quiet_and_never_while_paused():
    day = memory_tidy.EVERY_SECONDS
    assert memory_tidy.due(idle=900, last_run=0, now=day + 1)
    assert not memory_tidy.due(idle=60, last_run=0, now=day + 1)  # someone is using it
    assert not memory_tidy.due(idle=900, last_run=day, now=day + 100)  # ran today
    assert not memory_tidy.due(idle=900, last_run=0, now=day + 1, paused=True)


def test_chat_transcripts_are_left_alone():
    memory = FakeMemory(rec("t1", "User: hi AI: hello", kind="conversation", days_old=400),
                        rec("t2", "User: hi AI: hello", kind="conversation", days_old=300))
    memory_tidy._report_path().write_text('{"tracking_since": "2025-01-01T00:00:00"}', encoding="utf-8")
    report = memory_tidy.tidy(memory, now=NOW)
    assert report["merged"] == report["retired"] == [] and all(r["active"] for r in memory._records.values())


def test_a_memory_edited_during_the_pass_is_left_alone(monkeypatch):
    memory = FakeMemory(rec("a", "Prefers short answers.", days_old=10), rec("b", "Prefers short answers", days_old=2))
    real_plan = memory_tidy.plan

    def plan_then_user_edits(records, **kwargs):
        actions = real_plan(records, **kwargs)
        memory._records["a"]["updated_at"] = "2026-10-08T12:30:00"  # the user edited it meanwhile
        return actions

    monkeypatch.setattr(memory_tidy, "plan", plan_then_user_edits)
    report = memory_tidy.tidy(memory, now=NOW)
    assert report["merged"] == [] and memory._records["a"]["active"]


def test_many_memories_tidy_quickly():
    import time as _time

    memory = FakeMemory(*[rec(f"m{i}", f"Fact number {i} about topic {i % 37} and detail {i * 7}", days_old=5) for i in range(1500)])
    started = _time.perf_counter()
    memory_tidy.tidy(memory, now=NOW)
    assert _time.perf_counter() - started < 30
