"""Native work coordination, owned by StateStore's authoritative JSON store.

This is an application state contract around Lean, not a second agent runtime.
One atomic document commits work, action intent, approvals and delivery together.
The backend's existing process lock is the single-writer boundary. External
effects remain at-least-once/unknown: we never infer that a lost receipt is safe
to repeat.
"""
from __future__ import annotations

import contextvars
import copy
import hashlib
import json
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

VERSION = 1
BUCKETS = ("works", "attempts", "actions", "approvals", "deliveries", "proposals", "responsibilities", "notes")
TERMINAL = {"completed", "failed", "cancelled"}
WAITING = {"waiting_input", "waiting_approval", "waiting_dependency", "retry_wait", "blocked", "unknown", "interrupted", "paused"}
STATES = TERMINAL | WAITING | {"ready", "running", "verifying"}
_CURRENT = contextvars.ContextVar("echo_work_context", default=None)


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def operation_snapshot(tool: str, args: dict, base_path: str = "") -> dict:
    """Exact arguments + filesystem identities, without reading credentials."""
    resources = {}
    for key in ("path", "file_path", "source", "destination", "cwd"):
        value = args.get(key)
        if not isinstance(value, str) or not value or "://" in value:
            continue
        path = Path(value).expanduser()
        if not path.is_absolute() and base_path:
            path = Path(base_path) / path
        path = path.resolve()
        try:
            stat = path.stat()
            resources[key] = [str(path), stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns]
        except FileNotFoundError:
            resources[key] = [str(path), "absent"]
    return {"tool": tool, "arguments_hash": digest(args), "resources": resources}


def policy_snapshot() -> dict:
    from config import config
    from agent import privacy
    from agent.lean.toolbox import NATIVE_POLICY_FLAGS
    from agent.tool_registry import ToolRegistry
    flags = {flag for values in NATIVE_POLICY_FLAGS.values() for flag in values}
    for entry in ToolRegistry.get_all().values():
        flags.update(getattr(entry, "policy_flags", ()) or ())
    return {"privacy": privacy.mode(), "flags": {flag: bool(getattr(config, flag.lower(), False)) for flag in sorted(flags)}}


def _resource(tool: str, work: dict) -> str:
    """What a writing action reserves. There is one desktop (mouse, keyboard, focus); writes are
    scoped to the work's project, or to its own chat when it has none. Review R2: every project-less
    chat used to share one "project:unscoped" reservation, so one chat's uncertain write blocked all."""
    if any(k in tool for k in ("computer", "mouse", "keyboard", "browser")):
        return "desktop"
    if work.get("project_id"):
        return "project:" + work["project_id"]
    return "session:" + (work.get("session_id") or "unscoped")


def _process_running(action: dict) -> bool:
    """Whether a background action's process is still running in this backend. Unknown: assume so."""
    ids = [e.get("running") for e in action.get("effects", []) if e.get("kind") == "process" and e.get("running")]
    if not ids:
        return False
    try:
        from agent.lean.terminal import _PROCESSES, _process_alive
    except Exception:
        return True
    for process_id in ids:
        proc = _PROCESSES.get(str(process_id))
        if proc is None or _process_alive(proc):
            return True
    return False


@dataclass(frozen=True)
class WorkContext:
    coordinator: Any
    work_id: str
    attempt_id: str
    principal: str
    agent_id: str
    tools: Optional[tuple[str, ...]] = None


def current() -> Optional[WorkContext]:
    return _CURRENT.get()


@contextmanager
def work_scope(context: Optional[WorkContext]):
    token = _CURRENT.set(context)
    try:
        yield context
    finally:
        _CURRENT.reset(token)


class Coordinator:
    def __init__(self, store):
        self.store = store
        self.path = store.root / "coordination.json"
        self.reconcile()

    def _read(self) -> dict:
        raw = self.store._require_mapping(self.path, self.store._read_json(self.path))
        if not raw:
            return {"version": VERSION, **{key: {} for key in BUCKETS}}
        if raw.get("version", 0) == 0:
            # Additive migration: preserve the original before the first write.
            backup = self.path.with_suffix(".v0.backup.json")
            if not backup.exists():
                self.store._write_json(backup, raw)
            raw = {"version": VERSION, **{key: raw.get(key, {}) for key in BUCKETS}}
            for row in raw["works"].values():
                row.setdefault("agent_id", row.pop("owner", "echo"))
                if row.get("status") == "canceled":
                    row["status"] = "cancelled"
        if raw.get("version") != VERSION or any(not isinstance(raw.get(key), dict) for key in BUCKETS):
            self.store._fail_corrupt_state(self.path, ValueError("Unsupported coordination schema"), kind="schema")
        for row in raw["works"].values():
            if not isinstance(row, dict) or row.get("status") not in STATES:
                self.store._fail_corrupt_state(self.path, ValueError("Invalid work state"), kind="schema")
        return raw

    def _tx(self, change: Callable[[dict], Any]):
        with self.store._lock, self.store._path_write_lock(self.path):
            data = self._read()
            result = change(data)
            self.store._write_json(self.path, data)
            return copy.deepcopy(result)

    def view(self, bucket: str, *, principal: str = "owner") -> list[dict]:
        if bucket not in BUCKETS:
            raise ValueError("Unknown coordination record type")
        with self.store._lock:
            return copy.deepcopy([r for r in self._read()[bucket].values() if r.get("principal") == principal])

    def get(self, work_id: str, *, principal: str = "owner") -> dict:
        with self.store._lock:
            row = self._read()["works"].get(work_id)
            if row is None or row.get("principal") != principal:
                raise KeyError("Work not available to this principal")
            return copy.deepcopy(row)

    @staticmethod
    def _work(data, work_id, principal):
        row = data["works"].get(work_id)
        if row is None or row.get("principal") != principal:
            raise PermissionError("Work is outside the caller's authority")
        return row

    def create(self, *, objective: str, agent_id: str, principal: str = "owner", session_id: str = "", project_id: str = "",
               key: str = "", parent_id: str = "", tools: Optional[list[str]] = None, acceptance: str = "", dependencies=(),
               budget: Optional[dict] = None, taint=(), provenance: Optional[dict] = None) -> dict:
        if not objective.strip() or not agent_id or not principal:
            raise ValueError("Work requires an objective, agent and principal")
        work_id = "work_" + (digest([principal, key])[:24] if key else uuid.uuid4().hex[:24])
        def change(data):
            if work_id in data["works"]:
                return self._work(data, work_id, principal)
            if parent_id:
                parent = self._work(data, parent_id, principal)
                ancestor = parent
                while ancestor:
                    if ancestor["agent_id"] == agent_id:
                        raise PermissionError("Circular delegation is not permitted")
                    ancestor = data["works"].get(ancestor.get("parent_id"))
                count = sum(1 for r in data["works"].values() if r.get("parent_id") == parent_id)
                if count >= parent["budget"]["children"]:
                    raise PermissionError("Delegation budget exhausted")
                inherited = parent.get("tools")
                if inherited is None:
                    raise PermissionError("Parent capabilities have not been bound")
                tools_bound = sorted(set(tools or inherited) & set(inherited))
                tainted = sorted(set(taint) | set(parent.get("taint", [])))
            else:
                tools_bound, tainted = tools, list(taint)
            for dependency in dependencies:
                self._work(data, dependency, principal)
                if dependency == work_id:
                    raise ValueError("Circular work dependency")
            limits = {"tokens": 64000, "seconds": 900, "children": 6}
            limits.update({k: max(0, min(int(v), {"tokens": 256000, "seconds": 3600, "children": 6}[k])) for k, v in (budget or {}).items() if k in limits})
            row = {"id": work_id, "principal": principal, "agent_id": agent_id, "objective": objective[:16000],
                   "acceptance": (acceptance or objective)[:4000], "plan": [], "wake": {}, "session_id": session_id, "sessions": [session_id] if session_id else [],
                   "project_id": project_id, "parent_id": parent_id, "dependencies": list(dependencies), "status": "ready",
                   "revision": 1, "tools": sorted(set(tools_bound)) if tools_bound is not None else None, "taint": tainted,
                   "budget": limits, "tokens": 0, "evidence": [], "result": "", "verification": "unverified",
                   "created_at": time.time(), "updated_at": time.time(), "provenance": provenance or {}, "next_wake": None}
            data["works"][work_id] = row
            return row
        return self._tx(change)

    def progress(self, context, plan):
        def update(data):
            row = self._work(data, context.work_id, context.principal)
            row.update(plan=list(plan)[:30], updated_at=time.time(), revision=row["revision"] + 1)
            return row
        return self._tx(update)

    def claim(self, work_id: str, *, principal="owner", session_id="", agent_id="") -> WorkContext:
        def change(data):
            row = self._work(data, work_id, principal)
            if row["status"] != "ready":
                raise PermissionError(f"Work is {row['status']}; explicit reconciliation or resume is required")
            if agent_id and row["agent_id"] != agent_id:
                raise PermissionError("This agent does not own the work")
            if any(self._work(data, dep, principal)["status"] != "completed" for dep in row["dependencies"]):
                raise PermissionError("Dependencies have not completed")
            attempt_id = "attempt_" + uuid.uuid4().hex[:24]
            data["attempts"][attempt_id] = {"id": attempt_id, "work_id": work_id, "principal": principal,
                "agent_id": row["agent_id"], "session_id": session_id, "status": "running", "started_at": time.time(), "execution_id": ""}
            row.update(status="running", attempt_id=attempt_id, updated_at=time.time(), revision=row["revision"] + 1)
            if session_id and session_id not in row["sessions"]:
                row["sessions"].append(session_id)
            return attempt_id, row["agent_id"]
        attempt_id, actor = self._tx(change)
        return WorkContext(self, work_id, attempt_id, principal, actor)

    def bind(self, context: WorkContext, tools: list[str], *, execution_id="", model: Optional[dict] = None, project_root="") -> set[str]:
        def change(data):
            row = self._work(data, context.work_id, context.principal)
            original_root = row["provenance"].get("project_root")
            if original_root and project_root and original_root != project_root:
                raise PermissionError("The original work resource scope changed")
            if project_root:
                row["provenance"]["project_root"] = project_root
            responsibility = data["responsibilities"].get(row.get("provenance", {}).get("responsibility_id", ""))
            if responsibility:
                if responsibility.get("tools") is None:
                    responsibility["tools"] = sorted(set(tools))
                tools_bound = set(tools) & set(responsibility["tools"])
            else:
                tools_bound = set(tools)
            if row["tools"] is None:
                row["tools"] = sorted(tools_bound)
            allowed = set(row["tools"]) & tools_bound
            attempt = data["attempts"][context.attempt_id]
            attempt.update(execution_id=execution_id or attempt.get("execution_id", ""), model=model or attempt.get("model", {}))
            return sorted(allowed)
        return set(self._tx(change))

    def account(self, context: WorkContext, tokens: int) -> None:
        def change(data):
            row = self._work(data, context.work_id, context.principal)
            while row:
                row["tokens"] += max(0, tokens)
                row = data["works"].get(row.get("parent_id"))
        self._tx(change)

    def check(self, context: WorkContext, tool: str = "") -> dict:
        row = self.get(context.work_id, principal=context.principal)
        responsibility_id = row.get("provenance", {}).get("responsibility_id")
        if responsibility_id:
            responsibility = next((r for r in self.view("responsibilities", principal=context.principal) if r["id"] == responsibility_id), None)
            if not responsibility or responsibility["status"] != "active":
                raise PermissionError("Responsibility is paused or removed")
        attempt = next(r for r in self.view("attempts", principal=context.principal) if r["id"] == context.attempt_id)
        if row["status"] not in {"running", "waiting_approval"} or attempt["status"] != "running":
            raise PermissionError(f"Work is {row['status']}")
        if row["tokens"] >= row["budget"]["tokens"] or time.time() - attempt["started_at"] > row["budget"]["seconds"]:
            raise PermissionError("Work budget exhausted")
        if tool and (tool not in (row["tools"] or []) or (context.tools is not None and tool not in context.tools)):
            raise PermissionError("Tool is outside the original work grant")
        parent = row.get("parent_id")
        if parent:
            parent_row = self.get(parent, principal=context.principal)
            if parent_row["status"] in TERMINAL | {"paused", "unknown", "blocked"}:
                raise PermissionError("Parent work is no longer active")
            if parent_row["tokens"] >= parent_row["budget"]["tokens"]:
                raise PermissionError("Parent work budget exhausted")
        return row

    def finish(self, context: WorkContext, *, status: str, result="", evidence=(), verification="unverified") -> dict:
        if status not in STATES:
            raise ValueError("Invalid work status")
        def change(data):
            row = self._work(data, context.work_id, context.principal)
            # Cancellation/pause, pending approval and uncertain writes win over a text reply.
            if row["status"] in {"paused", "cancelled", "waiting_approval", "waiting_input", "blocked", "unknown"}:
                effective = row["status"]
            elif any(a["work_id"] == row["id"] and a["status"] == "unknown" for a in data["actions"].values()):
                effective = "unknown"
            elif status == "completed" and any(c.get("parent_id") == row["id"] and c["status"] not in TERMINAL for c in data["works"].values()):
                effective = "waiting_dependency"
            else:
                # A process the work left running (a dev server) doesn't keep the work open: it can
                # run for days. Its action stays "background" and keeps its reservation against other
                # work until its exit is observed (review R3).
                effective = status
            row.update(status=effective, result=str(result)[:16000], verification=verification,
                       evidence=list(evidence)[:200], updated_at=time.time(), revision=row["revision"] + 1)
            data["attempts"][context.attempt_id].update(status=effective, ended_at=time.time())
            return row
        return self._tx(change)

    def transition(self, work_id, status, *, principal="owner", revision: int, reconciliation=""):
        if status not in {"ready", "paused", "cancelled", "failed"}:
            raise ValueError("Unsupported user transition")
        def change(data):
            row = self._work(data, work_id, principal)
            if row["revision"] != revision:
                raise ValueError("Work changed; refresh before deciding")
            if status == "ready":
                if row["status"] in TERMINAL | {"running", "waiting_approval", "waiting_input"}:
                    raise PermissionError("Work cannot resume in its current state")
                uncertain = [a for a in data["actions"].values() if a["work_id"] == work_id and a["status"] == "unknown"]
                if uncertain and not reconciliation.strip():
                    raise PermissionError("Uncertain effects require an explicit reconciliation record")
                for action in uncertain:
                    action.update(status="reconciled", reconciliation=reconciliation[:4000])
            row.update(status=status, revision=revision + 1, updated_at=time.time())
            if status in {"paused", "cancelled"}:
                descendants = {work_id}
                for _ in range(8):
                    descendants |= {r["id"] for r in data["works"].values() if r.get("parent_id") in descendants}
                for child_id in descendants - {work_id}:
                    child = data["works"][child_id]
                    if child["status"] not in TERMINAL:
                        child.update(status=status, revision=child["revision"] + 1)
                for approval in data["approvals"].values():
                    if approval["work_id"] in descendants and approval["status"] in {"pending", "approved", "answered"}:
                        approval["status"] = "cancelled"
            return row
        return self._tx(change)

    def transfer(self, work_id, agent_id, *, principal="owner", revision: int):
        from agent.lean.personas import get_persona_store
        if get_persona_store().get(agent_id) is None:
            raise ValueError("Unknown specialist")
        def change(data):
            row = self._work(data, work_id, principal)
            if row["revision"] != revision or row["status"] in TERMINAL | {"running", "waiting_approval", "unknown"}:
                raise PermissionError("Transfer requires inactive, reconciled work at the current revision")
            row.update(agent_id=agent_id, revision=revision + 1, updated_at=time.time())
            return row
        return self._tx(change)

    def action_intent(self, context, call_id, tool, args, *, read_only: bool):
        self.check(context, tool)
        if not read_only and self.previous_effect(context, tool, args):
            raise PermissionError("This exact effect completed in an earlier attempt; inspect it instead of repeating it")
        action_id = "action_" + digest([context.attempt_id, call_id])[:24]
        base_path = self.get(context.work_id, principal=context.principal)["provenance"].get("project_root", "")
        snapshot = operation_snapshot(tool, args, base_path)
        def change(data):
            existing = data["actions"].get(action_id)
            if existing:
                raise PermissionError("Action already recorded; reconcile rather than execute twice")
            work = self._work(data, context.work_id, context.principal)
            # Cooperative resource exclusion, not an OS sandbox. Unknown writes
            # retain their reservation until the owner records reconciliation.
            bookkeeping = tool in {"delegate_to_agent", "spawn_workers", "complete_task", "assign_tasks", "work_status", "agent_notes", "propose_agent_change"}
            own_process = tool == "process_stop" and any(any(e.get("running") == args.get("process_id") for e in a.get("effects", [])) for a in data["actions"].values())
            resource = "" if read_only or bookkeeping or own_process else _resource(tool, work)
            if resource:
                blockers = []
                for other in data["actions"].values():
                    if other.get("resource") != resource or other["status"] not in {"intent", "unknown", "background"}:
                        continue
                    if other["status"] == "background":
                        if not _process_running(other):
                            # Its process has exited: the reservation ends (it was only a running process).
                            other.update(status="completed", ended_at=time.time(), process_receipt=True)
                            continue
                        if other["work_id"] == context.work_id:
                            continue  # review R3: a task's own dev server doesn't block its next edit
                    blockers.append(other)
                if blockers:
                    held = blockers[0]
                    why = {"intent": "another action is writing to it right now",
                           "unknown": "an earlier action's outcome is unknown and needs checking",
                           "background": "a process another task started is still running"}[held["status"]]
                    raise PermissionError(f"Resource {resource} is held: {why} (work {held['work_id']}, {held['tool']}). "
                                          "Wait for it, or inspect and reconcile it in Activity › Agent work.")
            data["actions"][action_id] = {"id": action_id, "work_id": context.work_id, "attempt_id": context.attempt_id,
                "principal": context.principal, "agent_id": context.agent_id, "tool": tool, "snapshot": snapshot,
                "read_only": read_only or bookkeeping, "resource": resource, "status": "intent", "created_at": time.time()}
            return action_id
        return self._tx(change)

    def previous_effect(self, context, tool, args):
        if tool in {"delegate_to_agent", "spawn_workers", "complete_task", "assign_tasks", "agent_notes", "work_status", "propose_agent_change"}:
            return None
        return next((r for r in self.view("actions", principal=context.principal)
                     if r["work_id"] == context.work_id and r["attempt_id"] != context.attempt_id
                     and not r["read_only"] and r["status"] == "completed" and r["tool"] == tool
                     and r["snapshot"]["arguments_hash"] == digest(args)), None)

    def action_result(self, action_id, *, ok: bool, outcome: str, effects=(), uncertain=True):
        def change(data):
            row = data["actions"][action_id]
            background = ok and any(e.get("kind") == "process" and e.get("running") for e in effects)
            row.update(status="background" if background else "completed" if ok else "unknown" if uncertain and not row["read_only"] else "failed",
                       outcome=outcome, effects=list(effects)[:30], ended_at=time.time())
        self._tx(change)

    def process_observed(self, context, process_id, *, success):
        # Process IDs are unique per backend run, so a stop seen from any chat of the same owner
        # ends the reservation of the task that started it.
        def update(data):
            for action in data["actions"].values():
                if action["principal"] == context.principal and action["status"] == "background" and any(e.get("running") == process_id for e in action.get("effects", [])):
                    action.update(status="completed" if success else "failed", ended_at=time.time(), process_receipt=True)
        self._tx(update)

    def approval(self, context, *, approval_id, tool, args, summary, reason):
        from agent.lean.policy import redact_payload
        if redact_payload(args) != args:
            raise PermissionError("Use a configured credential reference; secret-bearing arguments cannot be saved for approval")
        base_path = self.get(context.work_id, principal=context.principal)["provenance"].get("project_root", "")
        snapshot = operation_snapshot(tool, args, base_path)
        policy = policy_snapshot()
        def change(data):
            row = self._work(data, context.work_id, context.principal)
            row.update(status="waiting_approval", wake={"kind": "approval", "id": approval_id}, revision=row["revision"] + 1)
            record = {"id": approval_id, "principal": context.principal, "work_id": row["id"], "attempt_id": context.attempt_id,
                "agent_id": context.agent_id, "tool": tool, "args": args, "summary": summary, "reason": reason,
                "owner_agent_id": row["agent_id"],
                "snapshot": snapshot, "base_path": base_path, "policy": policy, "status": "pending", "expires_at": time.time() + 86400,
                "session_id": data["attempts"][context.attempt_id]["session_id"], "created_at": time.time()}
            data["approvals"][approval_id] = record
            return record
        return self._tx(change)

    def decide(self, approval_id, decision, *, principal="owner"):
        if decision not in {"allow", "deny"}:
            raise ValueError("Recovered approvals permit exact-action allow or deny only")
        def change(data):
            record = data["approvals"].get(approval_id)
            if not record or record["principal"] != principal or record["status"] != "pending":
                raise KeyError("Approval is unavailable")
            if decision == "allow" and (time.time() > record["expires_at"] or record["snapshot"] != operation_snapshot(record["tool"], record["args"], record.get("base_path", "")) or record["policy"] != policy_snapshot()):
                raise PermissionError("Approval is stale; inspect and request a fresh action")
            record["status"] = "approved" if decision == "allow" else "denied"
            row = data["works"][record["work_id"]]
            if row["status"] != "waiting_approval" or row["agent_id"] != record.get("owner_agent_id", record["agent_id"]) or record["tool"] not in (row["tools"] or []):
                raise PermissionError("Approval owner or original capability grant changed")
            active = data["attempts"].get(record["attempt_id"], {}).get("status") == "running"
            row.update(status=("running" if active else "ready") if decision == "allow" else "blocked", revision=row["revision"] + 1)
            return record
        return self._tx(change)

    def consume_approval(self, context, tool, args) -> bool:
        base_path = self.get(context.work_id, principal=context.principal)["provenance"].get("project_root", "")
        snapshot = operation_snapshot(tool, args, base_path)
        policy = policy_snapshot()
        def change(data):
            for record in data["approvals"].values():
                if record["work_id"] == context.work_id and record["status"] == "approved" and record["tool"] == tool and record["snapshot"] == snapshot:
                    if time.time() > record["expires_at"] or record["policy"] != policy:
                        raise PermissionError("Approved action became stale")
                    record["status"] = "consumed"
                    return True
            return False
        return self._tx(change)

    def question(self, context, identity, args):
        record = self.approval(context, approval_id=identity, tool="ask_user", args=args,
                               summary=str(args.get("question", "")), reason="Waiting for user input")
        def update(data):
            data["approvals"][identity].update(kind="question", question=args.get("question", ""), options=args.get("options", []), allow_other=args.get("allow_other", True))
            data["works"][context.work_id].update(status="waiting_input", wake={"kind": "input", "id": identity})
        self._tx(update)
        return record

    def answer(self, identity, answer, *, principal="owner"):
        def update(data):
            record = data["approvals"].get(identity)
            if not record or record["principal"] != principal or record.get("kind") != "question" or record["status"] != "pending":
                raise KeyError("Question is no longer pending")
            if time.time() > record["expires_at"] or (not record.get("allow_other", True) and answer not in record.get("options", [])):
                raise ValueError("Question expired or answer is unavailable")
            work = data["works"][record["work_id"]]
            if work["status"] != "waiting_input":
                raise PermissionError("Work is no longer waiting for this answer")
            record.update(status="answered", answer=str(answer)[:2000])
            active = data["attempts"][record["attempt_id"]]["status"] == "running"
            work.update(status="running" if active else "ready", revision=work["revision"] + 1)
            return record
        return self._tx(update)

    def consume_answer(self, context, args):
        def update(data):
            for record in data["approvals"].values():
                if record["work_id"] == context.work_id and record.get("kind") == "question" and record["status"] == "answered" and record["snapshot"] == operation_snapshot("ask_user", args):
                    record["status"] = "consumed"
                    return record["answer"]
            return None
        return self._tx(update)

    def enqueue(self, *, key, channel, destination, text, agent_id="echo", work_id="", session_id="", principal="owner"):
        if channel not in {"desktop", "discord", "telegram", "chat"} or not destination:
            raise ValueError("Unsupported or missing delivery destination")
        delivery_id = "delivery_" + digest([principal, key, channel, destination])[:24]
        def change(data):
            if work_id:
                self._work(data, work_id, principal)
            return data["deliveries"].setdefault(delivery_id, {"id": delivery_id, "principal": principal, "key": key,
                "channel": channel, "destination": destination, "text": text[:16000], "agent_id": agent_id,
                "work_id": work_id, "session_id": session_id, "status": "queued", "created_at": time.time()})
        return self._tx(change)

    def delivery_state(self, delivery_id, status, *, receipt="", error=""):
        if status not in {"sending", "accepted", "confirmed", "failed", "unknown"}:
            raise ValueError("Invalid transport status")
        def change(data):
            row = data["deliveries"][delivery_id]
            if status == "sending" and row["status"] not in {"queued", "failed"}:
                raise PermissionError("Delivery already claimed or outcome uncertain")
            row.update(status=status, receipt=receipt[:1000], error=error[:1000], updated_at=time.time())
            return row
        return self._tx(change)

    def reconcile_delivery(self, identity, status, evidence, *, principal="owner"):
        if status not in {"confirmed", "failed"} or not str(evidence).strip():
            raise ValueError("Record the inspected destination outcome before deciding")
        def update(data):
            row = data["deliveries"].get(identity)
            if not row or row["principal"] != principal or row["status"] not in {"unknown", "sending"}:
                raise PermissionError("Delivery is not awaiting reconciliation")
            row.update(status=status, reconciliation=str(evidence)[:2000], updated_at=time.time())
            return row
        return self._tx(update)

    def proposal(self, kind, payload, *, principal="owner", proposed_by="echo"):
        if kind not in {"specialist", "responsibility", "transfer"}:
            raise ValueError("Unknown proposal type")
        record = {"id": "proposal_" + uuid.uuid4().hex[:24], "principal": principal, "kind": kind,
                  "payload": payload, "proposed_by": proposed_by, "status": "pending", "revision": 1, "created_at": time.time()}
        return self._tx(lambda data: data["proposals"].setdefault(record["id"], record))

    def note(self, agent_id, project_id, text="", *, principal="owner"):
        key = digest([principal, agent_id, project_id])
        if not text:
            with self.store._lock:
                return self._read()["notes"].get(key, {}).get("text", "")
        def change(data):
            if text:
                data["notes"][key] = {"principal": principal, "agent_id": agent_id, "project_id": project_id,
                                      "text": text[:6000], "updated_at": time.time()}
            return data["notes"].get(key, {}).get("text", "")
        return self._tx(change)

    def prune(self, *, keep_days: float = 30, now: Optional[float] = None) -> int:
        """Drop finished history older than the retention window (review R10); returns works removed.

        Only finished works go, with their attempts, actions, decided approvals and settled
        deliveries. Anything unknown, waiting, running, still referenced by live work, or with a
        delivery whose outcome is unsettled stays until the owner resolves it.
        """
        cutoff = (now if now is not None else time.time()) - keep_days * 86400

        def candidates(data) -> set[str]:
            works = data["works"]
            old = {i for i, r in works.items() if r["status"] in TERMINAL and float(r.get("updated_at") or 0) < cutoff}
            kept = set(works) - old
            old -= {r.get("parent_id") for i, r in works.items() if i in kept}
            old -= {d for i in kept for d in works[i].get("dependencies", [])}
            old -= {a["work_id"] for a in data["actions"].values() if a["status"] in {"intent", "unknown", "background"}}
            old -= {d["work_id"] for d in data["deliveries"].values() if d["status"] in {"queued", "sending", "unknown"}}
            old -= {a["work_id"] for a in data["approvals"].values() if a["status"] == "pending"}
            return old

        def settled(row) -> bool:
            # A delivery without a work (reminders, notices) ages out on its own once its outcome is settled.
            return (not row.get("work_id") and row["status"] not in {"queued", "sending", "unknown"}
                    and float(row.get("updated_at") or row.get("created_at") or 0) < cutoff)

        with self.store._lock:
            if not self.path.exists():
                return 0
            data = self._read()
            if not candidates(data) and not any(settled(r) for r in data["deliveries"].values()):
                return 0  # nothing to drop: don't rewrite the document

        def change(data):
            gone = candidates(data)
            for work_id in gone:
                del data["works"][work_id]
            for bucket in ("attempts", "actions", "approvals", "deliveries"):
                data[bucket] = {k: v for k, v in data[bucket].items() if v.get("work_id") not in gone}
            data["deliveries"] = {k: v for k, v in data["deliveries"].items() if not settled(v)}
            return len(gone)
        return self._tx(change)

    def reconcile(self):
        if not self.path.exists():
            return
        def change(data):
            for action in data["actions"].values():
                if action["status"] in {"intent", "background"}:
                    action["status"] = "interrupted" if action["read_only"] else "unknown"
                    work = data["works"][action["work_id"]]
                    if not action["read_only"]:
                        work.update(status="unknown", revision=work["revision"] + 1)
            for attempt in data["attempts"].values():
                if attempt["status"] == "running":
                    attempt["status"] = "interrupted"
                    row = data["works"][attempt["work_id"]]
                    if row["status"] == "running":
                        unknown = any(a["work_id"] == row["id"] and a["status"] == "unknown" for a in data["actions"].values())
                        row.update(status="unknown" if unknown else "interrupted", revision=row["revision"] + 1)
            for delivery in data["deliveries"].values():
                if delivery["status"] == "sending":
                    delivery["status"] = "unknown"
        self._tx(change)
