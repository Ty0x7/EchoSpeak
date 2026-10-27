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


def runtime_catalog() -> dict:
    """Passive availability only. Finding a CLI proves neither login nor compatibility."""
    current_mode = privacy.mode()
    rows = [{
        "id": "echo", "name": "Echo native", "protocol": "native",
        "implemented": True, "installed": True, "authentication": "existing_model_settings",
        "can_start": True, "blockers": [],
        "description": "Echo's own tools, skills, memory, learning, and approval policies. Choose a local or cloud model in the conversation.",
    }]
    definitions = (
        ("codex", "Codex", "codex-app-server", "codex",
         "Use the official app-server and Codex-owned sign-in. ChatGPT access depends on your account and usage limits."),
        ("claude_code", "Claude Agent", "claude-agent-sdk", None,
         "Original Claude tools and sessions through the official Agent SDK. Requires a separate Anthropic API key; subscription OAuth is not copied."),
        ("acp", "Other ACP agents", "acp", None,
         "Connect compatible agents through their own ACP sessions and capability negotiation."),
    )
    from agent.code_executables import locate
    from agent.code_sessions import sdk_installed
    for runtime_id, name, protocol, command, description in definitions:
        # Paths only. No --version or auth probes here: even a health-check process can run hooks.
        # The owner checks sign-in explicitly from Settings › Models › Coding agents.
        found = locate(command) if command else None
        installed = sdk_installed() if runtime_id == "claude_code" else bool(found["path"]) if found else None
        blockers = []
        if installed is False:
            blockers.append(found["problem"] if found else f"{name} was not found.")
        if runtime_id == "claude_code" and not installed:
            blockers = ["The optional Claude Agent SDK is not installed in this backend."]
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
    return {
        "schema_version": 1,
        "project": {"id": item.id, "name": item.name, "workspace_root": str(root), "trust_state": item.trust_state},
        "git_summary": summary(str(root)),
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
