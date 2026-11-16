"""Chat, Agents and Code: which environment a conversation belongs to.

One rule, used by the runtime (who answers) and by the session list (where it shows):

- **Code**: an Echo Code conversation (thread surface "code"). Native Code is model-first like
  Chat, but for software engineering (agent/lean/code_mode.py); external runtimes have their own sessions.
- **Agents**: a conversation with a persistent agent. That's a room (a direct chat with
  one agent, or a group), or a conversation an agent started itself: check-ins, routine
  results, reminders, Discord and Telegram.
- **Chat**: everything else the owner started. Chat is model-first: the selected model
  answers in Echo's harness, without Echo's persona, delegation or team.

Nothing is migrated or rewritten. Old conversations keep their records; the rule reads
fields every thread already has, so a conversation's environment can't drift.

**One assistant** (``assistant_mode = "unified"``, docs/research/harness-first.md). The three
names above stay as *capability profiles* of one assistant, and the profile follows facts about
the conversation instead of the tab it was opened from:

- a conversation with an agent (a room, or one an agent started) keeps that agent: "agents";
- the owner's own conversation gets the engineering profile ("code": file and terminal tools,
  the verification ledger) exactly while a **trusted project** is attached to it, and the
  conversation profile ("chat") otherwise. Attaching or detaching a project changes the next turn.

Echo answers in both; the selected model is named as what Echo runs on. Permission switches,
approvals, the policy check and Private/Offline mode apply unchanged in every profile: a profile
only narrows what is offered. EchoSpeak starts no external coding harness in this mode.
"""
from __future__ import annotations

from typing import Any, Optional

ENVIRONMENTS = ("chat", "agents", "code")
# Thread sources the owner starts from the app. Anything else was started by an agent or a channel.
OWNER_SOURCES = frozenset({"web", "desktop", "voice", "chat"})
# Turn sources where the owner is talking live; routines, check-ins and channels stay with their agent.
MODEL_FIRST_TURNS = frozenset({"web", "desktop", "voice", "chat"})


ASSISTANT_MODES = ("environments", "unified")


def assistant_mode() -> str:
    """"environments" (three tabs, 11.13 behaviour) or "unified" (one assistant). Unknown values keep the default."""
    try:
        from config import config
        value = str(getattr(config, "assistant_mode", "") or "").strip().lower()
    except Exception:
        value = ""
    return value if value in ASSISTANT_MODES else "environments"


def unified() -> bool:
    return assistant_mode() == "unified"


def has_trusted_project(session_id: str) -> bool:
    """Whether this conversation has a project the owner marked trusted. Any doubt is "no"."""
    try:
        from agent import project_workspace
        from agent.state import get_state_store

        project_id = str(get_state_store().get_thread_state(session_id).active_project_id or "")
        return bool(project_id) and project_workspace.project(project_id).trust_state == "trusted"
    except Exception:
        return False


def classify(thread: Any, *, in_room: bool) -> str:
    """The environment for a thread record (anything with surface and source)."""
    if thread is None:
        # No record (an API caller's own id, an old voice session): keep Echo, as before.
        return "agents"
    if str(getattr(thread, "surface", "chat") or "chat") == "code":
        return "code"
    if in_room:
        return "agents"
    return "chat" if str(getattr(thread, "source", "web") or "web") in OWNER_SOURCES else "agents"


def room_threads() -> set[str]:
    try:
        from agent.lean.rooms import get_room_store
        return {room.thread_id for room in get_room_store().list()}
    except Exception:
        return set()


def environment_of(session_id: str, *, rooms: Optional[set[str]] = None) -> str:
    from agent.threads import get_thread_manager
    session_id = str(session_id or "")
    try:
        thread = get_thread_manager().get_thread(session_id)
    except Exception:
        thread = None
    return classify(thread, in_room=session_id in (room_threads() if rooms is None else rooms))


def turn_environment(session_id: str, *, source: str, caller_role: str = "owner", work_id: str = "") -> str:
    """Who answers this turn: "chat" or "code" (the selected model, model-first) or "agents" (a persistent agent).

    Only the owner, live, and not resuming an agent's durable work: routines, channels and resumed
    work stay with their agent wherever the conversation shows.
    """
    if caller_role != "owner" or work_id or source not in MODEL_FIRST_TURNS:
        return "agents"
    environment = environment_of(session_id)
    if environment not in {"chat", "code"}:
        return "agents"
    if unified():
        # The profile follows the project, not the tab: a Code session whose project was detached
        # converses; a chat with a trusted project attached can engineer.
        return "code" if has_trusted_project(session_id) else "chat"
    return environment


def model_first(session_id: str, *, source: str, caller_role: str = "owner", work_id: str = "") -> bool:
    """Whether this turn runs as Chat: the owner, live, in a Chat conversation, not resuming agent work."""
    return turn_environment(session_id, source=source, caller_role=caller_role, work_id=work_id) == "chat"
