"""Who a primary agent may reach, and how: three separate systems (owner requirement, 18 November 2026).

**A. Delegation between primary agents** (Echo, Jarvis, Glados: independent identities, conversations, memory).
A primary agent may hand a task to another primary agent only when both take part in the same authorised
environment:

- a project the conversation is attached to, whose owner-set member list (``Project.agent_ids``) includes both;
- or a group room the owner composed (its members).

A direct conversation with no shared project has no delegation targets. Before this, it offered the whole roster
(``_handoff_pool``), so any agent could recruit any other into its work.

**B. Spawned task workers** (``spawn_workers``, agent/lean/swarm.py). These are not primary agents: they are
temporary workers acting for their parent, with a subset of the parent's read-only research tools. They have no
memory tools, no delegation and no messaging. They are available wherever the parent is, with or without a
project, under the owner's Research workers setting and the existing depth, budget and cancel limits.

**C. An explicit message to another primary agent** (``message_agent``). It is offered only when the owner's
current request explicitly asks to tell, inform or pass something to a named agent. It posts one bounded,
secret-redacted note into that agent's own direct conversation, marked as sent at the owner's request. It grants
nothing: no project membership, no delegation, no transfer of the conversation or memory, and nothing for later
turns.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Optional

MESSAGE_LIMIT = 2000

# "tell Jarvis …", "let Glados know …", "pass this to Jarvis", "send Glados a message", "message Jarvis", "inform Echo"
_ASK = r"(tell|inform|notify|let|message|ping|remind|pass|send|forward|relay|give)"


def primary_id(persona_id: str) -> str:
    """The primary agent a profile belongs to: Echo's Chat and Code profiles are Echo."""
    from agent.lean.personas import CHAT_AGENT_ID, CODE_AGENT_ID
    return "echo" if persona_id in {CHAT_AGENT_ID, CODE_AGENT_ID} else persona_id


def delegation_pool(persona_id: str, roster: Iterable[Any], *, room_kind: str = "", room_member_ids: Iterable[str] = (),
                    project_member_ids: Iterable[str] = ()) -> list[Any]:
    """System A: the primary agents ``persona_id`` may delegate to here (never itself)."""
    roster = list(roster)
    # Echo speaks as its Chat profile in a plain conversation and as its Code profile beside a project: both are Echo
    # for membership. Live G1 (18 November): the project listed "echo", the turn asked for "code", nothing was offered.
    persona_id = primary_id(persona_id)
    project = [str(x) for x in project_member_ids if str(x)]
    members: list[str] = []
    if project and persona_id in project:
        members = project
    elif room_kind == "group":
        members = [str(x) for x in room_member_ids]
    allowed = set(members) - {persona_id}
    return [p for p in roster if getattr(p, "id", None) in allowed]


def message_target(request: str, roster: Iterable[Any], persona_id: str) -> Optional[Any]:
    """System C: the agent the owner explicitly asked this agent to tell something, or None.

    Deterministic: the request must pair an asking verb with a roster agent's name ("tell Jarvis", "let Glados
    know", "pass this to Jarvis"). Talking about an agent ("what does Jarvis do?") is not a request to message it.
    """
    text = str(request or "")
    for persona in roster:
        name = str(getattr(persona, "name", "") or "")
        if not name or getattr(persona, "id", None) == primary_id(persona_id):
            continue
        n = re.escape(name)
        patterns = (
            rf"(?i)\b{_ASK}\s+{n}\b",                                   # tell Jarvis, message Glados, remind Echo
            rf"(?i)\blet\s+{n}\s+know\b",
            rf"(?i)\b(pass|send|forward|relay|give)\b[^.?!\n]{{0,60}}\bto\s+{n}\b",  # pass this to Jarvis
        )
        if any(re.search(p, text) for p in patterns):
            return persona
    return None


def unavailable_delegation(request: str, roster: Iterable[Any], persona_id: str,
                           available: Iterable[Any]) -> tuple[str, ...]:
    """Roster names explicitly requested for work this turn without an available handoff."""
    allowed = {agent.id for agent in available} | {primary_id(persona_id)}
    return tuple(agent.name for agent in roster if agent.id not in allowed and agent.name and re.search(
        rf"\b(?:(?:ask|get)\s+@?{re.escape(agent.name)}\s+to|(?:have|let)\s+@?{re.escape(agent.name)})\s+"
        r"(?:write|create|edit|update|save|build|fix|run|test|check|review|look|research|send|handle|complete|do)\b",
        request, re.I,
    ))


def correct_delegation_attribution(reply: str, unavailable: tuple[str, ...]) -> str:
    """Correct direct claims of execution by an unavailable delegate in this reply."""
    parts = re.split(r"((?<=[.!?])\s+|\n+)", reply)
    action = r"wrote|written|created|edited|updated|saved|built|fixed|ran|run|tested|checked|reviewed|completed|handled|sent"
    for index, part in enumerate(parts):
        if not part.strip() or re.search(r"\b(?:earlier|yesterday|previously|last\s+time|whether)\b", part, re.I):
            continue
        plain = part.replace("**", "").replace("`", "")
        for name in unavailable:
            target = re.escape(name)
            if re.search(rf"(?<!\w)@?{target}\s+(?:has\s+)?(?:successfully\s+)?(?:{action})\b", plain, re.I) or re.search(
                rf"\b(?:was|were|been)\s+(?:{action})\s+by\s+@?{target}(?!\w)", plain, re.I,
            ):
                parts[index] = f"I couldn't delegate to {name} in this conversation."
                break
    return "".join(parts)


_REVIEW_LABEL = r"(?:reviewed|approved|signed off)[ \t]+by"
_BYLINE_SEPARATOR = r"(?:[ \t]*:[ \t]*|[ \t]+)"
_REVIEW_BYLINE = re.compile(
    rf"(?im)^[ \t]*(?:(?:[-*+]|\d{{1,9}}[.)])[ \t]+)?({_REVIEW_LABEL}){_BYLINE_SEPARATOR}(.+)$"
)


def artifact_attribution_problem(tool: str, args: dict[str, Any], unavailable: tuple[str, ...], request: str) -> str:
    """Require a correction to a newly generated reviewer field for an unavailable delegate."""
    if tool == "file_edit":
        edits = [(args.get("old_text", ""), args.get("new_text", ""))]
    elif tool == "update_artifact" and not args.get("content"):
        rows = args.get("edits")
        if not isinstance(rows, list):
            return ""
        edits = [(row.get("find", ""), row.get("replace", "")) for row in rows if isinstance(row, dict)]
    elif tool in {"file_write", "create_artifact", "update_artifact"}:
        edits = [("", args.get("content", ""))]
    else:
        return ""

    def plain(value: Any) -> str:
        return str(value or "").replace("**", "").replace("`", "")

    owner_text = plain(request)
    for old, new in edits:
        existing = owner_text + "\n" + plain(old)
        for label, value in _REVIEW_BYLINE.findall(plain(new)):
            for name in unavailable:
                target = rf"@?{re.escape(name)}(?=$|[\s(.,;])"
                if not re.match(target, value, re.I):
                    continue
                supplied = rf"\b{re.escape(label)}{_BYLINE_SEPARATOR}{target}"
                if not re.search(supplied, existing, re.I):
                    return (f"Draft was not saved because it names {name} as the reviewer or approver, and no handoff "
                            "to that agent was available in this turn. Use a reviewer supported by the tool results "
                            "or omit that field, then submit the corrected draft.")
    return ""


def message_text(sender: str, text: str) -> str:
    """The note as it appears in the recipient's conversation: who sent it, and that the owner asked for it."""
    from agent.lean import policy

    body = policy.redact_secrets(" ".join(str(text or "").split()))[:MESSAGE_LIMIT]
    return f"Message from {sender}, sent at the owner's request: {body}"
