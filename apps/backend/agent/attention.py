"""What needs the owner right now, gathered from EchoSpeak's own state with no model call.

Echo isn't only a reply: it can come to you. This module answers "is there anything I should
tell them?" from real records: approvals waiting, work that stopped or failed, reminders
coming up, apps that need signing in or keep failing, coding agents that got signed out, and
model or API errors. agent/checkins.py turns new items into notices you can reply to, and
the friendly check-in (agent/heartbeat.py) gives the whole list to Echo as context.

Every source is read on its own and defensively: one that fails never hides the others.
Items carry a stable key, so the same thing isn't announced twice.
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from loguru import logger

RECENT_SECONDS = 24 * 3600
SOON = timedelta(minutes=60)


@dataclass
class Item:
    key: str        # stable identity, so one thing is announced once
    kind: str       # approval | work | reminder | routine | connection | coding_agent | model | delivery
    urgency: str    # "now" (waiting on you), "soon" (coming up or worth fixing), "fyi" (context only)
    title: str
    detail: str = ""
    where: str = ""  # where in EchoSpeak to act on it

    def to_dict(self) -> dict:
        return asdict(self)


def _approvals() -> list[Item]:
    from agent.state import get_state_store
    from agent.lean.approvals import get_approval_broker

    items = []
    for row in get_approval_broker().pending_for(""):
        items.append(Item(f"approval:{row.get('id')}", "approval", "now", "An action is waiting for your approval",
                          str(row.get("summary") or row.get("tool") or "")[:200], "the chat that asked"))
    for row in get_state_store().coordination.view("approvals"):
        if row.get("status") == "pending":
            label = "A question is waiting for your answer" if row.get("kind") == "question" else "An action is waiting for your approval"
            items.append(Item(f"approval:{row.get('id')}", "approval", "now", label, str(row.get("summary") or "")[:200],
                              "Activity › Agent work"))
    return items


def _work() -> list[Item]:
    from agent.state import get_state_store

    now = time.time()
    items = []
    for row in get_state_store().coordination.view("works"):
        status = row.get("status")
        objective = " ".join(str(row.get("objective") or "").split())[:160]
        if status in {"unknown", "blocked", "waiting_input", "interrupted"}:
            title = {"unknown": "A task stopped and its last action needs checking",
                     "blocked": "A task is blocked", "waiting_input": "A task is waiting for your answer",
                     "interrupted": "A task was interrupted"}[status]
            items.append(Item(f"work:{row.get('id')}:{status}", "work", "now" if status == "waiting_input" else "soon",
                              title, objective, "Activity › Agent work"))
        elif status == "failed" and now - float(row.get("updated_at") or 0) < RECENT_SECONDS:
            items.append(Item(f"work:{row.get('id')}:failed", "work", "soon", "A task failed",
                              (objective + (f" ({str(row.get('result') or '')[:120]})" if row.get("result") else "")).strip(),
                              "Activity › Agent work"))
    for row in get_state_store().coordination.view("deliveries"):
        if row.get("status") == "unknown":
            items.append(Item(f"delivery:{row.get('id')}", "delivery", "soon", "A message may not have been delivered",
                              f"To {row.get('channel')}. Check it arrived.", "Activity › Agent work"))
    return items


def _reminders_and_routines() -> list[Item]:
    from agent.routines import get_routine_manager

    now = datetime.now(timezone.utc)
    items = []
    for routine in get_routine_manager().list_routines(enabled_only=True):
        try:
            due = datetime.fromisoformat(str(routine.next_run).replace("Z", "+00:00")) if routine.next_run else None
        except ValueError:
            due = None
        if due and now <= due <= now + SOON:
            minutes = max(1, int((due - now).total_seconds() // 60))
            reminder = routine.action_type == "remind"
            items.append(Item(f"upcoming:{routine.id}:{routine.next_run}", "reminder" if reminder else "routine", "fyi",
                              f"{'Reminder' if reminder else 'Routine'} in {minutes} min: {routine.name}"[:200],
                              str((routine.action_config or {}).get("message") or routine.description or "")[:200],
                              "Settings › Automations"))
        if routine.last_result_status == "failed" or (routine.last_error and routine.last_result_status != "completed"):
            items.append(Item(f"routine-failed:{routine.id}:{routine.last_run}", "routine", "soon",
                              f"The routine \"{routine.name}\" failed last time"[:200], str(routine.last_error or "")[:200],
                              "Settings › Automations"))
    return items


def _connections() -> list[Item]:
    from agent.mcp_client import get_mcp_manager

    items = []
    for row in get_mcp_manager().status().get("servers") or []:
        if not row.get("enabled", True):
            continue
        name = str(row.get("name") or "")
        auth = str((row.get("auth") or {}).get("state") or "")
        if auth in {"needed", "failed"}:
            items.append(Item(f"connection:{name}:sign-in", "connection", "soon", f"{name} needs you to sign in again",
                              "Its connection stopped working until you sign in.", "Settings › Connections"))
        elif row.get("approval") == "changed":
            items.append(Item(f"connection:{name}:changed", "connection", "soon", f"{name} changed how it starts",
                              "It's paused until you review and approve it again.", "Settings › Connections"))
        elif row.get("last_error") and not row.get("running"):
            items.append(Item(f"connection:{name}:error", "connection", "soon", f"{name} isn't working",
                              str(row.get("last_error"))[:200], "Settings › Connections"))
    try:
        from agent.connections import ConnectionHealth, get_connection_registry
        for record in get_connection_registry().list_unscoped():
            if record.enabled and record.health in {ConnectionHealth.UNHEALTHY}:
                items.append(Item(f"account:{record.id}:{record.health.value}", "connection", "soon",
                                  f"{record.display_name or record.provider} isn't working", "", "Settings › Connections"))
    except Exception:
        logger.debug("Connection registry unavailable for attention", exc_info=True)
    return items


def _coding_agents() -> list[Item]:
    from agent import code_accounts

    items = []
    for runtime, name in (("codex", "Codex"), ("claude_cli", "Claude Code")):
        status = code_accounts.remembered(runtime)
        if status.get("installed") and status.get("signed_in") is False:
            items.append(Item(f"coding-agent:{runtime}:signed-out", "coding_agent", "soon", f"{name} is signed out",
                              "Sign in again to use your plan in Echo Code.", "Settings › Models › Coding agents"))
    return items


_MODEL_TROUBLE = ("401", "403", "429", "unauthorized", "invalid api key", "api key", "quota", "rate limit", "insufficient",
                  "connection refused", "couldn't reach", "could not reach", "timed out", "not found on the model server")


def _model_errors() -> list[Item]:
    from agent.state import get_state_store

    now = time.time()
    failures = [row for row in get_state_store().list_executions(limit=200)
                if row.status == "failed" and now - float(row.created_at or 0) < RECENT_SECONDS
                and any(word in str(row.error or "").lower() for word in _MODEL_TROUBLE)]
    if not failures:
        return []
    latest = max(failures, key=lambda row: row.created_at)
    return [Item(f"model-errors:{int(latest.created_at // 3600)}", "model", "soon",
                 f"Replies failed {len(failures)} time{'s' if len(failures) != 1 else ''} today with a model or API error",
                 str(latest.error or "")[:200], "Settings › Models")]


SOURCES: tuple[Callable[[], list[Item]], ...] = (_approvals, _work, _reminders_and_routines, _connections, _coding_agents, _model_errors)


def collect() -> list[Item]:
    """Everything that may need the owner, most urgent first; no duplicate keys."""
    found: dict[str, Item] = {}
    for source in SOURCES:
        try:
            for item in source():
                found.setdefault(item.key, item)
        except Exception:
            logger.debug("Attention source {} failed", source.__name__, exc_info=True)
    order = {"now": 0, "soon": 1, "fyi": 2}
    return sorted(found.values(), key=lambda item: (order.get(item.urgency, 3), item.kind, item.title))


def digest(items: list[Item]) -> str:
    """The list as plain lines for a notice or a check-in prompt."""
    return "\n".join(f"- {item.title}" + (f": {item.detail}" if item.detail else "") + (f" ({item.where})" if item.where else "")
                     for item in items)
