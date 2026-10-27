from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import threading
import time

import pytest

from agent.state import StateStore
from agent.coordination import Coordinator, operation_snapshot
from agent.admission import Admission


def setup_work(tmp_path, **kwargs):
    store = StateStore(tmp_path)
    ledger = store.coordination
    row = ledger.create(objective="Read the project release", agent_id="echo", tools=["file_read", "file_write"], **kwargs)
    return ledger, row


def test_claim_and_occurrence_are_atomic_and_idempotent(tmp_path):
    ledger, row = setup_work(tmp_path, key="daily:2026-10-26T08:00Z")
    again = ledger.create(objective="other", agent_id="echo", key="daily:2026-10-26T08:00Z")
    assert again["id"] == row["id"]
    def claim():
        try:
            return ledger.claim(row["id"])
        except PermissionError:
            return None
    with ThreadPoolExecutor(max_workers=6) as pool:
        claims = list(pool.map(lambda _: claim(), range(6)))
    assert len([claim for claim in claims if claim]) == 1


def test_dependency_principal_transfer_and_revision_boundaries(tmp_path, monkeypatch):
    ledger, row = setup_work(tmp_path)
    dep = ledger.create(objective="Review", agent_id="scout", dependencies=[row["id"]])
    with pytest.raises(PermissionError):
        ledger.claim(dep["id"])
    with pytest.raises(KeyError):
        ledger.get(row["id"], principal="discord:attacker")
    ctx = ledger.claim(row["id"])
    ledger.finish(ctx, status="completed", evidence=["source:release"], verification="observed")
    ledger.claim(dep["id"])
    with pytest.raises(PermissionError):
        ledger.transfer(dep["id"], "echo", revision=1)


def test_children_cannot_expand_tools_or_survive_parent_cancel(tmp_path):
    ledger, row = setup_work(tmp_path, budget={"children": 1})
    ctx = ledger.claim(row["id"])
    child = ledger.create(objective="Help", agent_id="forge", parent_id=row["id"], tools=["file_read", "terminal"])
    assert child["tools"] == ["file_read"]
    child_ctx = ledger.claim(child["id"])
    with pytest.raises(PermissionError):
        ledger.check(child_ctx, "terminal")
    with pytest.raises(PermissionError):
        ledger.create(objective="Too many", agent_id="forge", parent_id=row["id"])
    parent = ledger.get(row["id"])
    ledger.transition(row["id"], "cancelled", revision=parent["revision"])
    with pytest.raises(PermissionError):
        ledger.check(child_ctx, "file_read")
    assert ledger.finish(ctx, status="completed")["status"] == "cancelled"


def test_crash_after_write_intent_is_unknown_and_never_blindly_retried(tmp_path):
    ledger, row = setup_work(tmp_path)
    ctx = ledger.claim(row["id"])
    ledger.action_intent(ctx, "call", "file_write", {"path": str(tmp_path / "result")}, read_only=False)
    restarted = StateStore(tmp_path).coordination
    recovered = restarted.get(row["id"])
    assert recovered["status"] == "unknown"
    with pytest.raises(PermissionError):
        restarted.transition(row["id"], "ready", revision=recovered["revision"])
    restarted.transition(row["id"], "ready", revision=recovered["revision"], reconciliation="Inspected disk: write did not happen")
    assert restarted.claim(row["id"])


def test_read_interruption_and_budget_revocation(tmp_path):
    ledger, row = setup_work(tmp_path, budget={"tokens": 4})
    ctx = ledger.claim(row["id"])
    ledger.action_intent(ctx, "read", "file_read", {}, read_only=True)
    restarted = StateStore(tmp_path).coordination
    recovered = restarted.get(row["id"])
    assert recovered["status"] == "interrupted"
    restarted.transition(row["id"], "ready", revision=recovered["revision"])
    ctx = restarted.claim(row["id"])
    restarted.account(ctx, 4)
    with pytest.raises(PermissionError, match="budget"):
        restarted.check(ctx)


def test_exact_approval_revalidates_files_policy_and_consumes_once(tmp_path, monkeypatch):
    monkeypatch.setattr("agent.coordination.policy_snapshot", lambda: {"privacy": "standard"})
    ledger, row = setup_work(tmp_path)
    ctx = ledger.claim(row["id"])
    target = tmp_path / "file.txt"
    target.write_text("before")
    args = {"path": str(target), "content": "after"}
    ledger.approval(ctx, approval_id="one", tool="file_write", args=args, summary="Write", reason="mutation")
    StateStore(tmp_path)  # interrupted approval remains pending
    ledger.decide("one", "allow")
    assert ledger.consume_approval(ctx, "file_write", args)
    assert not ledger.consume_approval(ctx, "file_write", args)
    ledger.approval(ctx, approval_id="two", tool="file_write", args=args, summary="Write", reason="mutation")
    target.write_text("changed while waiting")
    with pytest.raises(PermissionError, match="stale"):
        ledger.decide("two", "allow")


def test_delivery_unknown_is_not_requeued_and_proposals_are_inactive(tmp_path):
    ledger, row = setup_work(tmp_path)
    proposal = ledger.proposal("specialist", {"name": "Research"})
    assert proposal["status"] == "pending"
    delivery = ledger.enqueue(key="result", channel="discord", destination="owner-id", text="result", work_id=row["id"])
    assert ledger.enqueue(key="result", channel="discord", destination="owner-id", text="retry")["id"] == delivery["id"]
    ledger.delivery_state(delivery["id"], "sending")
    restarted = StateStore(tmp_path).coordination
    assert restarted.view("deliveries")[0]["status"] == "unknown"
    with pytest.raises(PermissionError):
        restarted.delivery_state(delivery["id"], "sending")


def test_corrupt_and_future_schema_preserved(tmp_path):
    path = tmp_path / "coordination.json"
    path.write_text('{"version":99}')
    with pytest.raises(RuntimeError, match="unreadable"):
        StateStore(tmp_path)
    assert json.loads(path.read_text())["version"] == 99


def test_additive_legacy_migration_preserves_backup(tmp_path):
    path = tmp_path / "coordination.json"
    path.write_text(json.dumps({"works": {"old": {"id": "old", "owner": "echo", "principal": "owner", "status": "canceled"}}}))
    ledger = StateStore(tmp_path).coordination
    assert ledger.get("old")["agent_id"] == "echo"
    assert ledger.get("old")["status"] == "cancelled"
    assert path.with_suffix(".v0.backup.json").exists()


def test_admission_bounds_concurrency_and_cancel(tmp_path):
    gate = Admission(slots=1, aging_seconds=.01)
    started = threading.Event()
    cancel = threading.Event()
    def worker():
        with gate.enter(cancel=cancel):
            started.set()
    with gate.enter():
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(worker)
            time.sleep(.03)
            assert not started.is_set()
            cancel.set()
            with pytest.raises(InterruptedError):
                future.result(timeout=1)
    assert gate.active == 0 and not gate.waiters


def test_state_store_initialization_cannot_overwrite_a_replaced_authority(monkeypatch):
    from agent import state
    replacement = object()
    def initialize():
        state._state_store = replacement
        return object()
    monkeypatch.setattr(state, "_state_store", None)
    monkeypatch.setattr(state, "StateStore", initialize)
    assert state.get_state_store() is replacement


def test_retention_drops_old_finished_history_but_keeps_anything_unresolved(tmp_path):
    """Review R10: the ledger is rewritten whole on every change, so finished history must age out."""
    coord = StateStore(tmp_path).coordination

    def finished(name, *, uncertain=False, delivery=None):
        ctx = coord.claim(coord.create(objective=name, agent_id="echo", project_id=name, tools=["file_write"])["id"])
        action = coord.action_intent(ctx, name, "file_write", {"path": name}, read_only=False)
        coord.action_result(action, ok=not uncertain, outcome="ok" if not uncertain else "lost receipt", uncertain=uncertain)
        if delivery:
            row = coord.enqueue(key=name, channel="chat", destination="s", text=name, work_id=ctx.work_id)
            coord.delivery_state(row["id"], delivery)
        coord.finish(ctx, status="completed")
        return ctx.work_id

    old = finished("old", delivery="accepted")
    unknown = finished("unknown-write", uncertain=True)
    unsettled = finished("unsettled", delivery="unknown")
    notice = coord.enqueue(key="notice", channel="desktop", destination="s", text="reminder")
    coord.delivery_state(notice["id"], "accepted")
    later = time.time() + 31 * 86400
    assert coord.prune(now=time.time()) == 0  # nothing is old yet
    assert coord.prune(now=later) == 1
    works = {w["id"] for w in coord.view("works")}
    assert old not in works and {unknown, unsettled} <= works
    assert all(a["work_id"] != old for a in coord.view("actions") + coord.view("attempts"))
    assert {d["key"] for d in coord.view("deliveries")} == {"unsettled"}  # settled notice aged out too
    assert coord.prune(now=later) == 0
