"""Small, deterministic presentation of Code activity. No execution or model calls.

The inbox is a view over the existing owners: StateStore for native Code and the
external session supervisor for vendor harnesses. Review choices never alter a
turn, verification evidence, approval, worktree, or provider conversation.
"""
from __future__ import annotations

import hashlib
import json
import time

STATES = {"working", "needs_attention", "ready_to_review", "settled", "snoozed"}
ACTIONS = {"settled", "snoozed", "reopen"}
_LABELS = {"verified": "Verified", "checks_pass": "Checks pass", "partial": "Partly verified",
           "failing": "Checks failed", "unverified": "Not verified", "blocked": "Blocked"}


def activity_key(*values) -> str:
    return hashlib.sha256(json.dumps(values, sort_keys=True, default=str).encode()).hexdigest()[:32]


def project(*, activity: str, status: str, reason: str, choice=None, busy=False,
            waiting=False, verification="", updated_at=0) -> dict:
    woke = False
    until = 0
    if not busy and not waiting and choice and choice.get("activity") == activity:
        if choice.get("state") in {"settled", "snoozed"}:
            until = float(choice.get("until", 0) or 0)
            woke = choice["state"] == "snoozed" and bool(until and until <= time.time())
            if not woke:
                status = choice["state"]
    return {"state": status, "reason": reason, "activity": activity,
            "verification": verification, "updated_at": updated_at, "can_triage": not busy and not waiting,
            "snoozed_until": until if status == "snoozed" else 0, "woke": woke,
            "settled_at": float((choice or {}).get("chosen_at", 0) or 0) if status == "settled" else 0}


def native_snapshot(state, execution, *, waiting=False) -> dict:
    queued = getattr(state, "queued_turns", []) or []
    status = getattr(execution, "status", "")
    # A terminal execution can still be referenced while final cleanup runs.
    busy = status in {"running", "pending_approval", "in_progress", "started"}
    waiting = waiting or status == "pending_approval" or bool(queued)
    activity = activity_key(getattr(execution, "id", ""), status,
                            getattr(execution, "updated_at", 0), queued)
    measured = (getattr(execution, "metadata", {}) or {}).get("verification", {})
    records = list(measured.values()) if isinstance(measured, dict) else []
    records = [r for r in records if isinstance(r, dict) and r.get("status") in _LABELS]
    # Keep the least conclusive result visible if more than one agent contributed.
    order = ["failing", "blocked", "unverified", "partial", "checks_pass", "verified"]
    check = min(records, key=lambda r: order.index(r["status"])) if records else {}
    verification = _LABELS.get(check.get("status"), "Not checked")
    if waiting:
        inbox, reason = "needs_attention", "Approval or answer needed" if not queued else "Queued follow-up waiting"
    elif busy:
        inbox, reason = "working", "Turn running"
    elif status and status != "completed":
        inbox, reason = "needs_attention", "Turn interrupted" if status in {"canceled", "superseded", "interrupted"} else "Turn failed"
    elif check.get("status") in {"failing", "blocked", "partial", "unverified"}:
        inbox, reason = "needs_attention", verification
    elif execution:
        inbox, reason = "ready_to_review", "Turn finished · review the result"
    else:
        inbox, reason = "settled", "No turns yet"
    return project(activity=activity, status=inbox, reason=reason,
                   choice=getattr(state, "code_inbox", None), busy=busy, waiting=waiting,
                   verification=verification, updated_at=getattr(execution, "updated_at", 0))


def external_snapshot(row: dict, *, active=False, waiting=False) -> dict:
    lifecycle = row.get("state", "stopped")
    busy = active or lifecycle in {"connecting", "preparing", "running"}
    result = row.get("last_turn_state") or lifecycle
    activity = activity_key(row.get("inbox_revision", 0), row.get("last_turn_state", ""), lifecycle == "rolled_back")
    if waiting:
        status, reason = "needs_attention", "Approval needed"
    elif busy:
        status, reason = "working", "Preparing workspace" if lifecycle == "preparing" else "Runtime working"
    elif lifecycle == "rolled_back":
        status, reason = "settled", "Rolled back · start a new session"
    elif result == "completed":
        status, reason = "ready_to_review", "Runtime finished · review changes"
    elif result in {"failed", "interrupted"} or row.get("inbox_revision", 0):
        status, reason = "needs_attention", "Turn failed" if result == "failed" else "Session stopped · reconnect to continue"
    else:
        status, reason = "settled", "No turns yet" if not row.get("native_id") else "Session stopped · reconnect to continue"
    return project(activity=activity, status=status, reason=reason, choice=row.get("code_inbox"),
                   busy=busy, waiting=waiting, verification="Runtime-owned checks",
                   updated_at=row.get("last_activity_at", row.get("created_at", 0)))


def triage_choice(snapshot: dict, action: str, expected_activity: str, snooze_seconds: int = 0) -> dict:
    if action not in ACTIONS:
        raise ValueError("Choose settled, snoozed or reopen")
    if snapshot["activity"] != expected_activity or not snapshot["can_triage"]:
        raise ValueError("This session changed or is working. Refresh before changing its inbox state.")
    if not isinstance(snooze_seconds, int) or not 0 <= snooze_seconds <= 30 * 86400:
        raise ValueError("Snooze must be between now and 30 days")
    return {} if action == "reopen" else {"state": action, "activity": snapshot["activity"],
            "chosen_at": str(time.time()), "until": str(time.time() + snooze_seconds) if action == "snoozed" and snooze_seconds else "0"}
