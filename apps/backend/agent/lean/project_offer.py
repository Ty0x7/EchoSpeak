"""Chat without a project, asked for work in a folder: offer to work there (owner decision on #187, 25 November 2026).

The owner's GTA_CLONE session (24 November) ran in a conversation without a project, so unified mode gave it Chat:
no file or terminal tools. Asked to continue developing ~/Work/GTA_CLONE, Echo wrote the code into artifacts and
then said it had integrated it. The owner asked that a coding request reaching Chat without project access be
offered an appropriate project attachment instead.

``use_project_folder`` is that offer. It is offered only to Chat turns in unified mode (the conversation has no
project), and it **always** shows the approval card, in every approval mode: trusting a folder with file and
terminal access is the owner's decision, like a folder grant. Nothing happens unless the owner approves.

On approval the folder is attached as a trusted project. The switch itself happens when the turn ends
(``apply_pending``): the project is activated for the conversation through the same path as the attach API, and
a follow-up turn that continues the request is queued, so the work resumes with file and terminal tools. A turn
can't change its own tools part-way, and a project's scope change (tool root, approvals, preview) shouldn't move
under a running turn.
"""
from __future__ import annotations

import logging
import threading
import uuid
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

NAME = "use_project_folder"
REASON = "this lets Echo change files and run commands in that folder from now on in this conversation"
_lock = threading.Lock()
_pending: dict[str, dict[str, Any]] = {}

DESCRIPTION = (
    "Ask the owner to let you work in a folder on this computer: it becomes this conversation's trusted project, "
    "with file and terminal tools, and the work continues there automatically. Use it when the user wants code or "
    "files built, fixed or changed in a folder (\"continue development on GTA_CLONE\"), instead of writing the work "
    "into artifacts. Give the folder's full path. The owner approves or declines on a card.")
PARAMETERS = {"type": "object", "properties": {
    "path": {"type": "string", "description": "The folder to work in, e.g. /home/me/Work/GTA_CLONE or ~/Work/GTA_CLONE."},
    "why": {"type": "string", "description": "One short sentence on what you'll do there."},
}, "required": ["path"]}


def check_folder(raw: str) -> tuple[Optional[Path], str]:
    """(resolved folder, "") or (None, why it can't be offered)."""
    text = str(raw or "").strip().strip("`\"'")
    if not text:
        return None, "Give the folder's full path."
    path = Path(text).expanduser()
    if not path.is_absolute():
        return None, f"Give the full path of {text!r} (for example ~/Work/{text}), after checking where it is."
    try:
        path = path.resolve(strict=True)
    except OSError:
        return None, f"{text} doesn't exist. Check the path (file_list or file_find can help) and try again."
    if not path.is_dir():
        return None, f"{text} is a file, not a folder."
    from agent.lean import file_access

    why = file_access.sensitive(path)
    if why or path == Path.home() or path == Path(path.anchor):
        return None, (f"{path} can't be a project: {why or 'a whole home folder or drive is too broad'}. "
                      "Ask for the specific project folder.")
    return path, ""


def tool(session_id: str, goal: str) -> Any:
    from agent.lean.toolbox import NativeTool

    def run(args: dict[str, Any]) -> str:
        path, problem = check_folder(str(args.get("path") or ""))
        if path is None:
            return "Error: " + problem
        from agent.projects import get_project_manager

        project = get_project_manager().attach_folder(str(path), trust_state="trusted")
        with _lock:
            _pending[str(session_id)] = {"project_id": project.id, "path": str(path), "goal": str(goal or "").strip()}
        return (f"The owner approved: {path} is now this conversation's trusted project. Your file and terminal "
                "tools start in the next turn, which begins automatically and continues the request. Reply now in one "
                "short sentence that you'll continue in that folder. Don't write code or create artifacts in this "
                "reply.")

    return NativeTool(name=NAME, description=DESCRIPTION, parameters=PARAMETERS, func=run)


def pending(session_id: str) -> Optional[dict[str, Any]]:
    with _lock:
        row = _pending.get(str(session_id))
        return dict(row) if row else None


def apply_pending(session_id: str) -> bool:
    """At the end of the turn: activate the approved project and queue the turn that continues the request."""
    with _lock:
        row = _pending.pop(str(session_id), None)
    if not row:
        return False
    try:
        from api.deps import _apply_thread_scope, get_agent

        agent = get_agent(session_id)
        with agent._request_lock:
            _apply_thread_scope(agent, session_id)
            if not agent.activate_project(row["project_id"]):
                logger.warning("Approved project %s could not be activated for %s", row["path"], session_id)
                return False
    except Exception:  # noqa: BLE001
        logger.warning("Approved project %s could not be activated", row.get("path"), exc_info=True)
        return False
    goal = row["goal"] or "the request"
    message = (f"[The owner approved working in {row['path']}. Continue the request there now, with the project's "
               f"files and terminal]: {goal}")
    try:
        from agent.state import get_state_store

        get_state_store().enqueue_turn(session_id, message=message, client_request_id="project-offer-" + uuid.uuid4().hex)
    except Exception:  # noqa: BLE001
        logger.warning("Could not queue the follow-up turn after attaching %s", row["path"], exc_info=True)
    return True
