"""Context an agent should have without having to ask for it.

The system prompt already carries saved memories (picked by the request's
words) and the chat summary. This module adds the cues that decide when more
is needed, and the "am I still on track?" reminder for long turns.
"""

from __future__ import annotations

import re

# The user pointing back at an earlier conversation.
_POINTS_BACK = re.compile(
    r"""(?ix)\b(
        last\s+(?:time|week|month|night|session)|the\s+other\s+day|yesterday|earlier\s+today|previously|
        we\s+(?:talked|spoke|discussed|chatted|went\s+over|worked\s+on|were\s+working\s+on)|
        you\s+(?:said|told\s+me|mentioned|suggested|recommended|wrote|made|built)|
        i\s+(?:told|asked|mentioned\s+to|showed)\s+you|
        remember\s+(?:when|that|what|the|how|my)|
        as\s+(?:before|usual|discussed|we\s+agreed)|like\s+(?:last\s+time|before|you\s+did)|
        that\s+(?:project|thing|file|idea|plan)\s+(?:we|you|i)
    )\b"""
)


def points_back(text: str) -> bool:
    return bool(_POINTS_BACK.search(str(text or "")))


# Every this many steps of one turn, the goal is restated next to the latest tool result.
REMIND_EVERY = 8


def reminder(goal: str, step: int, recent: list[bool]) -> str:
    """A short "are you still on track?" note: the goal, how far in, and whether things keep failing."""
    failed = sum(1 for ok in recent if not ok)
    lines = [f"[Reminder from EchoSpeak: {step} steps into this task. The goal: {' '.join(str(goal).split())[:400]}"]
    if recent and failed * 2 >= len(recent):
        lines.append(f"{failed} of the last {len(recent)} tool calls failed: change the approach instead of "
                     "retrying the same thing, or say what is blocking you.")
    lines.append("If you already have what you need, finish and answer; otherwise take the next concrete step.]")
    return " ".join(lines)
