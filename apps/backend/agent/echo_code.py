"""Echo Code native sessions and passive external-runtime availability.

Runtime identity is deliberately separate from ModelProvider. External harnesses
must never enter Echo's query loop as if they were model endpoints.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import threading

from agent import privacy, project_workspace

_CREATION_LOCK = threading.RLock()


class RuntimeUnavailable(ValueError):
    pass


CODE_MODEL_RULE = "Native Code uses your selected local or API model; external coding subscriptions keep their own harnesses."


def is_code_thread(session_id: str) -> bool:
    """Whether this conversation belongs to Echo Code (not Chat)."""
    try:
        from agent.threads import get_thread_manager
        thread = get_thread_manager().get_thread(str(session_id or ""))
        return bool(thread and getattr(thread, "surface", "chat") == "code")
    except Exception:
        return False


def code_provider_problem(session_id: str, provider: str) -> str:
    """Keep the admission hook compatible while native Code supports local and API models."""
    # Provider readiness and capabilities are enforced by the shared native runtime.
    return ""


def runtime_catalog() -> dict:
    """Passive availability only. Finding a CLI proves neither login nor compatibility."""
    current_mode = privacy.mode()
    rows = [{
        "id": "echo", "name": "Echo native", "protocol": "native",
        "implemented": True, "installed": True, "authentication": "existing_model_settings",
        "can_start": True, "blockers": [],
        "description": "The model you pick on this PC as a coding assistant, in EchoSpeak's own harness: files, "
                       "terminal, checks that verify the work, learning, and your approval rules. "
                       "Local and API models use the native engineering harness. Coding subscriptions use their own external harness.",
    }]
    definitions = (
        ("codex", "Codex", "codex-app-server", "codex",
         "Use the official app-server and Codex-owned sign-in. ChatGPT access depends on your account and usage limits."),
        ("claude_cli", "Claude Code", "claude-code-cli", "claude_cli",
         "Your installed Claude Code, signed in with your own Claude plan through its own login. Claude Code keeps "
         "its tools and permissions; EchoSpeak asks you before any action Claude Code would ask about."),
        ("acp", "Other ACP agents", "acp", None,
         "Connect compatible agents through their own ACP sessions and capability negotiation."),
    )
    from agent.code_executables import locate
    for runtime_id, name, protocol, command, description in definitions:
        # Paths only. No --version or auth probes here: even a health-check process can run hooks.
        # The owner checks sign-in explicitly from Settings › Models › Coding agents.
        found = locate(command) if command else None
        installed = bool(found["path"]) if found else None
        blockers = []
        if installed is False:
            blockers.append(found["problem"] if found else f"{name} was not found.")
        if current_mode != "standard":
            blockers.append("External runtimes are blocked in Private/Offline mode until their process network access can be enforced.")
        rows.append({
            "id": runtime_id, "name": name, "protocol": protocol,
            "implemented": True, "installed": installed,
            "authentication": "unverified", "can_start": not blockers,
            "blockers": blockers, "description": description,
        })
    return {"schema_version": 1, "privacy_mode": current_mode, "runtimes": rows}


def _session_rows(project_id: str) -> list[dict]:
    from agent.state import get_state_store
    from agent.threads import get_thread_manager
    # Listing must not create durable state for sessions that have none yet.
    states = {row.thread_id: row for row in get_state_store().list_thread_states()}
    rows = []
    for thread in get_thread_manager().list_threads(limit=2000):
        scope = states.get(thread.thread_id)
        if thread.surface != "code" or not scope or scope.active_project_id != project_id:
            continue
        rows.append({**thread.to_dict(), "project_id": project_id})
        if len(rows) >= 100:
            break
    return rows


def workspace(project_id: str) -> dict:
    item = project_workspace.project(project_id)
    root = project_workspace.root_for(item)
    from agent.lean.git_context import summary
    from agent.code_worktree import preflight
    external_blocker = ""
    try:
        preflight(root)
    except (ValueError, OSError) as exc:
        external_blocker = str(exc) if isinstance(exc, ValueError) else "Git is unavailable. Install Git and reopen EchoSpeak."
    return {
        "schema_version": 1,
        "project": {"id": item.id, "name": item.name, "workspace_root": str(root), "trust_state": item.trust_state},
        "git_summary": summary(str(root)),
        "external_blocker": "",  # External harnesses can work directly in any trusted folder.
        "worktree_available": not bool(external_blocker),
        "worktree_unavailable": external_blocker,
        "sessions": _session_rows(project_id),
        "can_start_native": item.trust_state == "trusted",
        "native_blocker": "" if item.trust_state == "trusted" else "This project is not marked trusted. Coding sessions are unavailable for this folder.",
    }


def create_session(project_id: str, *, runtime_id: str, idempotency_key: str) -> dict:
    """Create a native coding session using the existing stores and query transport.

    External runtimes fail closed. No native fallback, copied transcript, shared
    credential, agent initialization, or additional model router is involved.
    """
    runtime = next((r for r in runtime_catalog()["runtimes"] if r["id"] == runtime_id), None)
    if runtime_id != "echo" or not runtime or not runtime["can_start"]:
        raise RuntimeUnavailable("External runtimes use isolated external sessions; they cannot enter the native query transport.")
    item = project_workspace.project(project_id)
    root = project_workspace.root_for(item)
    if item.trust_state != "trusted":
        raise PermissionError("This project is not marked trusted. Coding sessions are unavailable for this folder.")
    from agent.state import get_state_store
    from agent.threads import get_thread_manager
    store, manager = get_state_store(), get_thread_manager()
    # Keep the old ThreadManager fingerprint unchanged for legacy Chat requests.
    context = json.dumps({"surface": "code", "runtime": runtime_id, "project": item.id,
                          "root": os.path.normcase(str(root))}, sort_keys=True)
    with _CREATION_LOCK:
        try:
            thread = manager.create_thread(
                title=f"Code · {item.name}", source="web", surface="code", agent_runtime="echo",
                idempotency_key=idempotency_key, idempotency_context=context,
            )
        except ValueError as exc:
            raise RuntimeUnavailable(str(exc)) from exc
        existing_scope = next((row for row in store.list_thread_states() if row.thread_id == thread.thread_id), None)
        state = existing_scope or store.get_thread_state(thread.thread_id)
        if existing_scope is not None and not state.active_project_id:
            raise RuntimeUnavailable("This session's project was detached. Start a new coding session.")
        if state.active_project_id:
            # Retrying creation must not undo a user's later project change.
            if state.active_project_id != item.id or Path(state.workspace_root).resolve() != root:
                raise RuntimeUnavailable("This session's project has changed. Start a new coding session.")
        else:
            store.update_thread_state(thread.thread_id, active_project_id=item.id,
                                      workspace_root=str(root), project_path=str(root))
        return {**thread.to_dict(), "project_id": item.id}


def _waiting_sessions() -> set[str]:
    """The same live and recovered decision owners used by /lean/approvals."""
    from agent.state import get_state_store
    from agent.lean.approvals import get_approval_broker
    return ({row["session_id"] for row in get_approval_broker().pending_for()}
            | {row["session_id"] for row in get_state_store().coordination.view("approvals") if row["status"] == "pending"})


def overview() -> dict:
    """Echo Code's sidebar: each usable project with its native coding sessions, and the rest.

    A session whose project was removed, archived or detached is "unorganized": still
    reachable, and movable into a project. External runtimes' sessions are listed by the
    route, since they live in their own session store with their own worktrees.
    """
    from agent.projects import get_project_manager
    from agent.state import get_state_store
    from agent.threads import get_thread_manager
    projects = [item for item in get_project_manager().list_projects() if not item.archived and item.workspace_root]
    known = {item.id for item in projects}
    states = {row.thread_id: row for row in get_state_store().list_thread_states()}
    waiting = _waiting_sessions()
    grouped: dict[str, list[dict]] = {item.id: [] for item in projects}
    unorganized: list[dict] = []
    for thread in get_thread_manager().list_threads(limit=2000):
        if thread.surface != "code":
            continue
        project_id = str(getattr(states.get(thread.thread_id), "active_project_id", "") or "")
        row = {**thread.to_dict(), "project_id": project_id if project_id in known else "",
               "inbox": get_state_store().code_inbox_snapshot(thread.thread_id, waiting=thread.thread_id in waiting)}
        (grouped[project_id] if project_id in known else unorganized).append(row)
    return {
        "schema_version": 1,
        "projects": [{"id": item.id, "name": item.name, "workspace_root": item.workspace_root,
                      "trust_state": item.trust_state, "sessions": grouped[item.id][:100]} for item in projects],
        "unorganized": unorganized[:100],
    }


def triage_session(thread_id: str, action: str, activity: str, snooze_seconds: int = 0, automatic: bool = False) -> dict:
    from agent.state import get_state_store
    from agent.threads import get_thread_manager
    from agent.lean.approvals import get_approval_broker
    thread = get_thread_manager().get_thread(thread_id)
    if not thread or thread.surface != "code":
        raise KeyError("Coding session unavailable")
    try:
        return get_state_store().update_code_inbox(thread_id, action, activity,
                    waiting=bool(get_approval_broker().pending_for(thread_id)), snooze_seconds=snooze_seconds, automatic=automatic)
    except ValueError as exc:
        raise RuntimeUnavailable(str(exc)) from exc


def move_session(thread_id: str, project_id: str) -> dict:
    """Move a native coding session into another project (drag and drop in Code's sidebar).

    Only Echo's own sessions move: they work in the project folder itself, so moving one
    re-scopes its next turn to the new folder. History stays with the session. External
    runtimes' sessions own a worktree inside their project and never move.
    """
    from agent.state import get_state_store
    from agent.threads import get_thread_manager
    thread = get_thread_manager().get_thread(str(thread_id or ""))
    if thread is None or thread.surface != "code":
        raise KeyError("Coding session unavailable")
    item = project_workspace.project(project_id)
    root = project_workspace.root_for(item)
    if item.trust_state != "trusted":
        raise PermissionError("This project is not marked trusted. Coding sessions are unavailable for this folder.")
    store = get_state_store()
    state = store.get_thread_state(thread.thread_id)
    if state.current_execution_id or state.active_turn_id:
        raise RuntimeUnavailable("This session is working. Move it when its turn has finished.")
    if state.active_project_id != item.id:
        store.update_thread_state(thread.thread_id, active_project_id=item.id, workspace_root=str(root), project_path=str(root))
    return {**thread.to_dict(), "project_id": item.id}
