"""What makes a memory worth keeping, and how memories reach the prompt.

A memory is one short, durable fact about the user, written so it still makes
sense months later. This module checks facts before they are saved and renders
the recalled ones compactly:

- One fact per memory, a sentence of at most ~40 words (atomic facts, as in
  mem0: they deduplicate, update and retrieve far better than paragraphs).
- Facts that change over time carry a key ("home_city", "job"): saving a new
  value under the same key replaces the old one instead of piling up.
- Rewordings of a fact already saved are recognised and not stored twice.
- The prompt gets pinned facts first, then the few that match the request,
  capped in count and characters (the smallest set of high-signal tokens).
"""

from __future__ import annotations

import difflib
import re
from typing import Any, Iterable, Optional

from agent.stopwords import STOPWORDS

# What the agent may call a memory -> the store's memory_type.
KINDS: dict[str, str] = {
    "preference": "preference",
    "profile": "profile",
    "person": "relationship",
    "project": "project",
    "instruction": "workflow_preference",
    "goal": "goal",
    "fact": "note",
}

MAX_FACT_CHARS = 280
PROMPT_ROWS = 12
PROMPT_CHARS = 1600

_LEAD_INS = re.compile(
    r"^(please\s+)?(remember|note|keep in mind|save|store)( that|:)?\s+|^(fyi|for the record)[,:]?\s+",
    re.IGNORECASE,
)
# Requests and reminders belong to routines or the chat, not long-term memory.
_TRANSIENT = re.compile(
    r"\b(remind me|right now|at the moment|for now|this (chat|conversation|session)|in this chat)\b",
    re.IGNORECASE,
)


def tidy_fact(text: str) -> str:
    """One line, no 'remember that' lead-in, a capital first letter and a full stop."""
    fact = " ".join(str(text or "").split())
    fact = _LEAD_INS.sub("", fact).strip().strip('"').strip()
    if not fact:
        return ""
    fact = fact[0].upper() + fact[1:]
    if fact[-1] not in ".!?)":
        fact += "."
    return fact


def check_fact(fact: str) -> Optional[str]:
    """None when the fact is fit to keep; otherwise what to fix, for the agent."""
    if not fact:
        return "Error: give the fact to remember in 'fact'."
    if len(fact) > MAX_FACT_CHARS:
        return ("Not saved: too long. Save one short fact (under about 40 words); "
                "split separate facts into separate memory_save calls.")
    if fact.rstrip().endswith("?"):
        return "Not saved: that is a question, not a fact about the user."
    if _TRANSIENT.search(fact):
        return ("Not saved: that only matters now. Memories are for lasting facts; "
                "use a routine for reminders.")
    return None


def normalize_key(key: str) -> str:
    """'Home City' -> 'home_city'. Empty when nothing usable is left."""
    return re.sub(r"[^a-z0-9]+", "_", str(key or "").lower()).strip("_")[:48]


def _words(text: str) -> set[str]:
    tokens = set(re.findall(r"[a-z0-9]{2,}", str(text or "").casefold()))
    return (tokens - STOPWORDS) or tokens


def same_fact(a: str, b: str) -> bool:
    """True when two facts say the same thing in different words."""
    left, right = " ".join(a.lower().split()), " ".join(b.lower().split())
    if not left or not right:
        return False
    if left == right or difflib.SequenceMatcher(a=left, b=right).ratio() >= 0.88:
        return True
    wa, wb = _words(left), _words(right)
    if min(len(wa), len(wb)) < 3:
        return False
    return len(wa & wb) / len(wa | wb) >= 0.8


def find_duplicate(fact: str, records: Iterable[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """An active account memory that already says this fact, if any."""
    for record in records:
        if not bool(record.get("active", True)):
            continue
        if str(record.get("memory_type") or "") == "conversation":
            continue
        if same_fact(fact, str(record.get("text") or "")):
            return record
    return None


_SUBJECT = re.compile(r"^(the\s+)?user(?:'s|’s)\s+", re.IGNORECASE)
_SUBJECT_VERB = re.compile(r"^(the\s+)?user\s+(?=[a-z])", re.IGNORECASE)


def _compact(text: str) -> str:
    """'The user prefers tea.' -> 'Prefers tea.'; 'The user's dog ...' -> 'Their dog ...'."""
    line = " ".join(str(text or "").split())
    if _SUBJECT.match(line):
        line = _SUBJECT.sub("Their ", line, count=1)
    elif _SUBJECT_VERB.match(line):
        rest = _SUBJECT_VERB.sub("", line, count=1)
        line = rest[:1].upper() + rest[1:]
    return line


def render(memories: Iterable[dict[str, Any]]) -> str:
    """The prompt section: pinned facts first, then the relevant ones, compact and capped."""
    rows = [m for m in memories if str(m.get("content") or "").strip() and str(m.get("type") or "") != "conversation"]
    rows.sort(key=lambda m: 0 if m.get("pinned") else 1)
    lines: list[str] = []
    seen: list[str] = []
    used = 0
    for memory in rows:
        line = _compact(memory["content"])
        if any(same_fact(line, other) for other in seen):
            continue
        if lines and used + len(line) > PROMPT_CHARS:
            break
        seen.append(line)
        lines.append(f"- {line}")
        used += len(line)
        if len(lines) >= PROMPT_ROWS:
            break
    if not lines:
        return ""
    return ("## What you know about the user\n"
            "Saved facts, possibly out of date. Use them when they help; don't recite them.\n" + "\n".join(lines))
