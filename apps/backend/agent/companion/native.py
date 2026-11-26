"""EchoSpeak's own agents report to Echo Companion in-process.

``LeanSession`` calls ``observe()`` from its single ``emit()`` choke point and ``finished()`` at
the end of a run. Both only queue a small record for a daemon thread, so a turn never waits on
the companion and a companion error can never break a turn.

Echo differs from Claude Code and Codex: EchoSpeak measured its own work. A turn that
carries a verification record (``agent/lean/verification.py``) has an evidence status:
verified, checks_pass, partial, failing, unverified or blocked. That status travels with the
event, so commentary and memory can tell "Echo's checks pass" apart from "reported".
"""

from __future__ import annotations

import hashlib
import json
import logging
import queue
import re
import threading
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_queue: "queue.Queue[dict[str, Any]]" = queue.Queue(maxsize=500)
_thread: Optional[threading.Thread] = None
_lock = threading.Lock()
MEASURED = {"verified", "checks_pass"}


def agent_key(name: str) -> str:
    """'Echo' -> 'echo'; a named agent 'Glados' -> 'glados' (shown as its own name)."""
    slug = re.sub(r"[^a-z0-9-]+", "-", str(name or "").strip().lower()).strip("-")[:40]
    return slug or "echo"


def _worker() -> None:
    from agent.companion.observe import get_companion_store

    while True:
        event = _queue.get()
        try:
            get_companion_store().add(event)
        except Exception:
            logger.debug("Echo Companion could not record a native event", exc_info=True)


def _publish(event: dict[str, Any]) -> None:
    global _thread
    try:
        from config import config

        if not bool(getattr(config, "companion_enabled", False)) or not bool(getattr(config, "companion_observe_echo", True)):
            return
    except Exception:
        return
    with _lock:
        if _thread is None or not _thread.is_alive():
            _thread = threading.Thread(target=_worker, name="echo-companion-native", daemon=True)
            _thread.start()
    try:
        _queue.put_nowait(event)
    except queue.Full:
        pass


def _event(agent: str, session_id: str, kind: str, *, text: str = "", turn: str = "", cwd: str = "",
           meta: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    at = time.time()
    basis = [agent, session_id, kind, turn or round(at, 2), text[:200]]
    return {"id": hashlib.sha256(json.dumps(basis).encode()).hexdigest()[:24], "agent": agent, "session_id": session_id,
            "kind": kind, "at": at, "turn_id": turn, "cwd": cwd, "project": Path(cwd).name if cwd else "",
            "text": text[:12000], "transcript_path": "", "subagent": (meta or {}).get("subagent", ""),
            "meta": {"via": "native", **(meta or {})}}


def _project_root(session: Any) -> str:
    try:
        from agent.projects import get_project_manager

        project = get_project_manager().get_project(session._active_project())
        return str(getattr(project, "workspace_root", "") or "") if project else ""
    except Exception:
        return ""


def observe(session: Any, event: dict[str, Any]) -> None:
    """Called for every Lean event; keeps only the few that matter to the companion."""
    kind = event.get("type")
    if kind not in {"run_start", "delegation", "run_outcome"}:
        return
    try:
        sid = str(session.session_id)
        if kind == "run_start":
            if getattr(session, "source", "") == "heartbeat":
                return  # a hidden scheduled prompt is not the owner talking
            prompt = str(getattr(session, "_pending_prompt", "") or "")
            _publish(_event("echo", sid, "prompt", text=prompt[:600], turn=str(event.get("execution_id") or ""),
                            cwd=_project_root(session)))
        elif kind == "delegation":
            target = session._name_of(str(event.get("to") or "")) if hasattr(session, "_name_of") else str(event.get("to") or "")
            _publish(_event(agent_key(target), sid, "prompt", text=str(event.get("task") or "")[:600],
                            cwd=_project_root(session), meta={"delegated": True, "subagent": target}))
        elif kind == "run_outcome":
            summary = str(event.get("summary") or event.get("reason") or "")
            if summary:
                _publish(_event("echo", sid, "stage_completed", text=summary, cwd=_project_root(session),
                                meta={"status": str(event.get("status") or "")}))
    except Exception:
        logger.debug("Echo Companion native observe failed", exc_info=True)


def finished(session: Any, *, final_text: str, success: bool, cancelled: bool, execution_id: str,
             verification: Optional[dict[str, Any]] = None) -> None:
    """One finished Echo turn, attributed to the agent that answered, with its measured status."""
    try:
        if cancelled or not final_text:
            return
        last = next((r for r in reversed(getattr(session, "results", [])) if getattr(r, "text", "")), None)
        name = "Echo"
        if last is not None and hasattr(session, "_name_of"):
            try:
                name = session._name_of(str(last.agent_id)) or "Echo"
            except Exception:
                name = "Echo"
        key = "echo" if name.lower() in {"echo", "chat", "code"} else agent_key(name)
        statuses = [str((record or {}).get("status") or "") for record in (verification or {}).values()]
        measured = next((s for s in ("failing", "blocked", "partial", "unverified", "checks_pass", "verified") if s in statuses), "")
        _publish(_event(key, str(session.session_id), "turn_completed", text=final_text, turn=execution_id,
                        cwd=_project_root(session), meta={"success": bool(success), "measured": measured,
                                                          "agent_name": name}))
    except Exception:
        logger.debug("Echo Companion native finish failed", exc_info=True)


def worker_status(session: Any, name: str, index: int, goal: str, status: str) -> None:
    """Publish a research worker's observed state under its parent's session and execution."""
    try:
        sid = f"{session.session_id}:worker:{session.execution_id}:{index}"
        label = f"{name} research worker {index}"
        kind = {"running": "prompt", "done": "subagent_completed", "partial": "subagent_completed"}.get(status, "interrupted")
        text = goal if kind == "prompt" else f"Research worker {status} after looking into {goal}"
        _publish(_event(agent_key(label), sid, kind, text=text[:600], cwd=_project_root(session),
                        meta={"agent_name": label, "subagent": label, "parent_session": str(session.session_id), "status": status}))
    except Exception:
        logger.debug("Echo Companion worker observation failed", exc_info=True)


def post_stage(agent: str, session_id: str, text: str, *, cwd: str = "", kind: str = "stage_completed") -> None:
    """For EchoSpeak tools in another process (the loop-13 controller): tell the running backend.

    Uses the backend's own endpoint file and normal API auth. Best effort, background, 2 s cap.
    """

    def send() -> None:
        try:
            import urllib.request

            from agent import endpoint_file

            found = endpoint_file.read_endpoint()
            if not found:
                return
            body = json.dumps({"agent": agent, "session_id": session_id[:200], "kind": kind,
                               "text": text[:12000], "cwd": cwd}).encode()
            request = urllib.request.Request(f"http://{found['host']}:{int(found['port'])}/companion/events", data=body,
                                             method="POST", headers={"Content-Type": "application/json"})
            if found.get("auth_key"):
                request.add_header("x-echospeak-key", str(found["auth_key"]))
            urllib.request.urlopen(request, timeout=2).read()
        except Exception:
            pass

    threading.Thread(target=send, name="echo-companion-stage", daemon=True).start()
