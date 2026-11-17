"""One list of the owner's conversations, described the same way for every interface.

A conversation is one thing whichever window opened it: who answers in it (Echo, a named agent, a
group), which project is attached, what it last said, and whether it is busy or waiting on the
owner. The thread record alone doesn't say most of that, so each client used to guess from the old
Chat / Agents / Code label. This assembles it once, from the stores that own each fact:

- threads (title, activity, archive state) — ``agent.threads``
- rooms (which agents a conversation belongs to) — ``agent.lean.rooms``
- thread state (the attached project, whether a run is in progress) — ``agent.state``
- projects (name, folder, trust) — ``agent.projects``
- approvals and questions waiting on the owner — ``agent.lean.approvals``
- the last request and reply, and the model that wrote it — the execution records

Read-only. Used by ``GET /sessions/overview`` (the terminal's session manager; the desktop can read
the same route). docs/research/agent-first.md describes the design.
"""
from __future__ import annotations

from typing import Any, Optional

RUNNING = {"running", "pending_approval", "streaming"}


def _one_line(text: Any, limit: int) -> str:
    return " ".join(str(text or "").split())[:limit]


def _projects() -> dict[str, dict[str, Any]]:
    try:
        from agent.projects import get_project_manager

        items = get_project_manager().list_projects()
    except Exception:
        return {}
    out = {}
    for item in items or []:
        out[str(item.id)] = {"id": str(item.id), "name": str(getattr(item, "name", "") or ""),
                             "root": str(getattr(item, "workspace_root", "") or ""),
                             "trusted": str(getattr(item, "trust_state", "") or "") == "trusted"}
    return out


def _agent_names() -> dict[str, str]:
    try:
        from agent.lean.personas import get_persona_store

        return {str(a.id): str(a.name) for a in get_persona_store().list()}
    except Exception:
        return {}


def _last_turns(store: Any) -> dict[str, Any]:
    """The newest execution of each conversation, in one pass."""
    latest: dict[str, Any] = {}
    try:
        for record in store.list_executions(None, limit=100_000):
            latest.setdefault(str(record.thread_id), record)
    except Exception:
        return {}
    return latest


def _waiting() -> dict[str, list[str]]:
    """session id -> kinds of cards waiting on the owner there ("question" or "approval")."""
    out: dict[str, list[str]] = {}
    try:
        from agent.lean.approvals import get_approval_broker

        for row in get_approval_broker().pending_for(""):
            kind = "question" if row.get("kind") == "question" or row.get("tool") == "ask_user" else "approval"
            out.setdefault(str(row.get("session_id") or ""), []).append(kind)
    except Exception:
        pass
    return out


def describe(thread: Any, *, room: Optional[Any], state: Any, projects: dict[str, dict[str, Any]],
             names: dict[str, str], last: Optional[Any], waiting: list[str]) -> dict[str, Any]:
    """One conversation as both interfaces show it."""
    from agent import environments

    environment = environments.classify(thread, in_room=room is not None)
    if room is not None:
        agents = [{"id": str(a), "name": names.get(str(a), str(a))} for a in room.agent_ids]
        kind = "group" if room.kind == "group" else "agent"
    elif environment == "agents":
        # Started by an agent or a channel (a check-in, a routine, Discord): Echo's own thread.
        agents, kind = [{"id": "echo", "name": "Echo"}], "agent"
    else:
        agents, kind = [{"id": "echo", "name": "Echo"}], "echo"
    project_id = str(getattr(state, "active_project_id", "") or "")
    project = projects.get(project_id) if project_id else None
    # Busy is read from the last run's own record: a flag left on the thread by a crash would otherwise
    # show a conversation as running for ever.
    busy = str(getattr(last, "status", "") or "") in RUNNING if last is not None else False
    status = "needs_answer" if "question" in waiting else "needs_approval" if waiting else "running" if busy else "idle"
    preview = _one_line(getattr(room, "last_preview", "") if room is not None else "", 200)
    request = ""
    model = ""
    if last is not None:
        preview = _one_line(getattr(last, "response_preview", ""), 200) or preview
        request = _one_line(getattr(last, "query", ""), 160)
        model = str(getattr(last, "model_id", "") or "")
        if str(getattr(last, "status", "") or "") == "failed" and status == "idle":
            status = "failed"
    data = thread.to_dict()
    return {
        "thread_id": data.get("thread_id"),
        "title": _one_line(data.get("title"), 120),
        "created_at": float(data.get("created_at") or 0),
        "last_active_at": float(data.get("last_active_at") or 0),
        "message_count": int(data.get("message_count") or 0),
        "pinned": bool(data.get("pinned")),
        "archived": bool(data.get("archived")),
        "source": str(data.get("source") or ""),
        "environment": environment,
        "kind": kind,                      # echo | agent | group
        "agents": agents,
        "room_name": str(getattr(room, "name", "") or "") if room is not None and room.kind == "group" else "",
        "project_id": project_id,
        "project": project,                # {id, name, root, trusted} or None
        "status": status,                  # idle | running | needs_answer | needs_approval | failed
        "last_request": request,
        "preview": preview,
        "model": model,
    }


def overview(*, limit: int = 100, include_archived: bool = False) -> list[dict[str, Any]]:
    """The owner's conversations, most recently active first."""
    from agent.lean.rooms import get_room_store
    from agent.state import get_state_store
    from agent.threads import get_thread_manager

    store = get_state_store()
    rooms = {room.thread_id: room for room in get_room_store().list()}
    projects, names, latest, waiting = _projects(), _agent_names(), _last_turns(store), _waiting()
    rows = []
    for thread in get_thread_manager().list_threads(include_archived=include_archived, source=None, limit=limit):
        rows.append(describe(thread, room=rooms.get(thread.thread_id), state=store.get_thread_state(thread.thread_id),
                             projects=projects, names=names, last=latest.get(thread.thread_id),
                             waiting=waiting.get(thread.thread_id, [])))
    return rows
