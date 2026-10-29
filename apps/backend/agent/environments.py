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
"""
from __future__ import annotations

from typing import Any, Optional

ENVIRONMENTS = ("chat", "agents", "code")
# Thread sources the owner starts from the app. Anything else was started by an agent or a channel.
OWNER_SOURCES = frozenset({"web", "desktop", "voice", "chat"})
# Turn sources where the owner is talking live; routines, check-ins and channels stay with their agent.
MODEL_FIRST_TURNS = frozenset({"web", "desktop", "voice", "chat"})


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
    return environment if environment in {"chat", "code"} else "agents"


def model_first(session_id: str, *, source: str, caller_role: str = "owner", work_id: str = "") -> bool:
    """Whether this turn runs as Chat: the owner, live, in a Chat conversation, not resuming agent work."""
    return (caller_role == "owner" and not work_id and source in MODEL_FIRST_TURNS
            and environment_of(session_id) == "chat")
