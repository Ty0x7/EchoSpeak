"""Revocable, project/agent-scoped control grants for an optional local MCP client.

Grant secrets are shown once, stored as hashes and never reused as owner API or
provider credentials. A grant cannot approve tools, change policy or launch a CLI.
"""
from contextlib import contextmanager
import hashlib
import hmac
import secrets
import threading
import time
from pathlib import Path

from agent.code_worktree import atomic_json

_lock = threading.RLock()


class Grants:
    def __init__(self, path):
        self.path = Path(path)

    def _read(self):
        import json
        return json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}

    def list(self):
        with _lock:
            return [{k: v for k, v in row.items() if k != "hash"} for row in self._read().values()]

    def create(self, name, projects, agents=(), control=False, days=7):
        from agent import project_workspace
        from agent.lean.personas import get_persona_store
        if not name.strip() or len(name) > 80 or not 1 <= days <= 30 or not (projects or agents):
            raise ValueError("Name the client and choose its projects or agents (1–30 days)")
        roots = {}
        for pid in set(projects):
            item = project_workspace.project(pid)
            if item.trust_state != "trusted":
                raise PermissionError("Control access requires a trusted project")
            roots[pid] = str(project_workspace.root_for(item))
        known = {p.id for p in get_persona_store().list()}
        if set(agents) - known:
            raise ValueError("Unknown agent")
        token, identity = secrets.token_urlsafe(32), secrets.token_hex(12)
        row = {"id": identity, "name": name.strip(), "projects": roots, "agents": sorted(set(agents)),
               "control": bool(control), "created_at": time.time(), "expires_at": time.time() + days * 86400,
               "hash": hashlib.sha256(token.encode()).hexdigest()}
        with _lock:
            data = self._read()
            if len(data) >= 100:
                raise ValueError("Revoke an old client before adding another")
            data[identity] = row
            self.path.parent.mkdir(parents=True, exist_ok=True)
            atomic_json(self.path, data)
        return {"client": {k: v for k, v in row.items() if k != "hash"}, "token": token}

    def revoke(self, identity):
        with _lock:
            data = self._read()
            data.pop(identity, None)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            atomic_json(self.path, data)
        return {"revoked": True}

    @contextmanager
    def authorize(self, token, write=False):
        # The lock covers admission and dispatch, so revocation cannot race a mutation.
        with _lock:
            digest = hashlib.sha256(str(token).encode()).hexdigest()
            row = next((r for r in self._read().values() if hmac.compare_digest(r["hash"], digest)), None)
            if not row or row["expires_at"] <= time.time() or (write and not row["control"]):
                raise PermissionError("Client access is missing, expired, revoked or read only")
            yield row


def grants():
    from config import DATA_DIR
    return Grants(DATA_DIR / "code-control-clients.json")


def _project(grant, pid):
    from agent import project_workspace
    item = project_workspace.project(pid)
    if (pid not in grant["projects"] or item.trust_state != "trusted"
            or str(project_workspace.root_for(item)) != grant["projects"][pid]):
        raise PermissionError("Project is outside this client's scope or its folder/trust changed")
    return item


def _thread(grant, identity, external):
    if external:
        from agent.code_sessions import manager
        row = manager().sessions.get(identity)
        if not row:
            raise KeyError("Session unavailable")
        _project(grant, row.row["project_id"])
        return row.row["project_id"]
    from agent.state import get_state_store
    from agent.threads import get_thread_manager
    from agent.environments import environment_of
    thread = get_thread_manager().get_thread(identity)
    if not thread:
        raise KeyError("Session unavailable")
    if thread.surface == "code":
        return _project(grant, get_state_store().get_thread_state(identity).active_project_id).id
    from agent.lean.rooms import get_room_store
    room = get_room_store().by_thread(identity)
    if environment_of(identity) != "agents" or not room or not room.agent_ids or not set(room.agent_ids) <= set(grant["agents"]):
        raise PermissionError("Agent conversation is outside this client's scope")
    bound_project = get_state_store().get_thread_state(identity).active_project_id
    if bound_project:
        _project(grant, bound_project)
    return ""


def invoke(token, action, arguments):
    allowed = {"projects", "runtimes", "threads", "status", "create", "send", "stop", "triage"}
    if action not in allowed:
        raise ValueError("Unknown control action")
    import re
    if not isinstance(arguments, dict):
        raise ValueError("Control arguments must be an object")
    if action in {"status", "send", "stop", "triage"} and (not isinstance(arguments.get("id"), str) or not 1 <= len(arguments["id"]) <= 128):
        raise ValueError("Provide a bounded session identity")
    if action in {"send", "create"} and (not isinstance(arguments.get("request_id"), str) or not re.fullmatch(r"[A-Za-z0-9._:-]{8,128}", arguments["request_id"])):
        raise ValueError("Provide a stable request identity")
    if action == "send" and (not isinstance(arguments.get("message"), str) or not arguments["message"].strip() or len(arguments["message"]) > 50000):
        raise ValueError("Provide a bounded message")
    with grants().authorize(token, action not in {"projects", "runtimes", "threads", "status"}) as grant:
        from agent import echo_code
        from agent.code_sessions import manager
        from agent.state import get_state_store
        if action == "projects":
            return {"projects": [{"id": pid, "name": _project(grant, pid).name} for pid in grant["projects"]], "agents": grant["agents"]}
        if action == "runtimes":
            return echo_code.runtime_catalog()
        if action == "threads":
            overview = echo_code.overview()
            projects = [p for p in overview["projects"] if p["id"] in grant["projects"]]
            output = []
            for p in projects:
                _project(grant, p["id"])
                output.extend({"id": s["thread_id"], "title": s["title"], "project_id": p["id"], "external": False, "inbox": s["inbox"]} for s in p["sessions"])
                output.extend({**s, "external": True} for s in manager().call(manager().list(p["id"], summary=True)))
            from agent.lean.rooms import get_room_store
            for r in get_room_store().list():
                if r.agent_ids and set(r.agent_ids) <= set(grant["agents"]):
                    try:
                        _thread(grant, r.thread_id, False)
                        output.append({"id": r.thread_id, "title": r.name, "external": False, "environment": "agents"})
                    except (PermissionError, KeyError):
                        continue
            return {"threads": output}
        if action == "create":
            if arguments.get("agent_id"):
                if arguments["agent_id"] not in grant["agents"]:
                    raise PermissionError("Agent outside client scope")
                from agent.lean.rooms import get_room_store
                from agent.lean.personas import get_persona_store
                room = get_room_store().direct(arguments["agent_id"], get_persona_store().get(arguments["agent_id"]).name)
                _thread(grant, room.thread_id, False)
                return {"id": room.thread_id, "external": False}
            _project(grant, arguments.get("project_id", ""))
            return echo_code.create_session(arguments["project_id"], runtime_id="echo", idempotency_key=arguments["request_id"])
        identity, external = arguments.get("id", ""), bool(arguments.get("external", False))
        _thread(grant, identity, external)
        if action == "status":
            if external:
                session = manager().get(identity)
                return {"id": identity, "state": session.row["state"], "inbox": session.inbox()}
            return {"id": identity, "inbox": get_state_store().code_inbox_snapshot(identity),
                    "queued": len(get_state_store().list_queued_turns(identity))}
        if action == "triage":
            args = (identity, arguments["action"], arguments["activity"], arguments.get("snooze_seconds", 0))
            return manager().call(manager().triage(*args)) if external else echo_code.triage_session(*args)
        if action == "send":
            if external:
                # Only an already connected owner-configured harness. No hidden launch or login.
                return manager().call(manager().control_prompt(identity, arguments["message"], arguments["request_id"]))
            from agent.code_tasks import submit
            return submit(identity, arguments["message"], arguments["request_id"])
        if action == "stop":
            if external:
                manager().call(manager().stop(identity))
            else:
                from agent.code_tasks import cancel
                cancel(identity)
                from api.deps import _ACTIVE_QUERY_CANCEL_LOCK, _ACTIVE_QUERY_CANCELLATIONS
                with _ACTIVE_QUERY_CANCEL_LOCK:
                    for session, event, _ in _ACTIVE_QUERY_CANCELLATIONS.values():
                        if session == identity:
                            event.set()
                get_state_store().set_queue_paused(identity, True)
            return {"stopped": True, "id": identity}
