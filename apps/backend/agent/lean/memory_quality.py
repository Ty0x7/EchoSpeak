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
LIST_LIMIT = 24
LIST_WAIT_SECONDS = 1800

_LIST_OFFER = re.compile(
    r"(?i)^\s*(?:i(?:['’]ll| will| am going to|['’]m going to|mma)|i am about to)\s+"
    r"(?:send|give|share|paste)\b[^\n]{0,80}\blist\b"
)
_NEXT_MESSAGE = re.compile(r"(?i)\bnext (?:message|prompt|turn)\b")
_LIST_CONSENT = re.compile(r"(?i)\b(?:list of (?:things|facts|details|preferences) to remember|"
                           r"remember (?:it|them|these|the list))\b")
_LIST_REQUEST = r"^\s*(?:please[ \t]+)?remember (?:this|the following) list\b"
_DIRECT_LIST = re.compile(_LIST_REQUEST + r"(?: for (?:this (?:chat|session|conversation|project)(?: only)?|"
                          r"my account))?[ \t]*:[ \t]*\r?\n", re.IGNORECASE)
_SCOPE = re.compile(r"(?i)\b(?:for|in|within) (?:this (chat|session|conversation|project)\b|my (account)\b)")
_SCOPE_LINE = re.compile(r"(?im)^[ \t]*(?:(?:remember|save|keep) (?:this|these|it) +)?"
                         r"(?:(?:only |just )?(?:for|in|within) (?:this |the |my |project |chat |session )[^\n]+|"
                         r"(?:only |just )(?:for|in|within) [^\n]+|(?:for|in|within) [^\n]+? only)[ \t]*$")
_LOCAL_ONLY = re.compile(r"(?im)^[ \t]*(?:this )?(chat|session|conversation|project) only[.!]?[ \t]*$")
_WRITE_PREFIX = re.compile(
    r"(?i)^\s*(?:please[ \t]+)?(?:remember(?:[ \t]*:|[ \t]+(?:that|this|my|i|we|our|your|the user)\b)|"
    r"save (?:(?:this|that|it) (?:in|to) (?:my )?memory|to memory)\b|"
    r"keep (?:this|that) in mind\b|do not forget\b|don['’]?t forget\b)"
)
_WRITE_SUFFIX = re.compile(
    r"(?i)(?:[,.!;]\s*|\band +)(?:please +)?(?:remember (?:this|that|it)(?P<replacement> instead)?|"
    r"keep (?:this|that) in mind)[.!]?\s*$"
)
_PROSE_PREFIX = re.compile(
    r"(?i)^(?:please[ \t]+)?(?:remember[ \t]*:|remember[ \t]+(?:(?:that|this)(?:[ \t]+|:))?|"
    r"save (?:(?:this|that|it) (?:in|to) (?:my )?memory|to memory)[ \t]*:?|"
    r"keep (?:this|that) in mind[ \t]*:|(?:do not forget|don['’]?t forget)(?: that)?[ \t]+)"
)
_PROSE_BOUNDARY = re.compile(
    r"""(?P<quoted>"[^"\n]*"|“[^”\n]*”|‘[^’\n]*’|"""
    r"(?<!\w)'(?:[^'\n]|(?<=\w)'(?=\w))+'(?!\w))"
    r"|(?<=[.!?])[ \t]+|(?P<clause>;[ \t]*|\n+)"
)
_ABBREVIATION = re.compile(r"(?i)\b(?:[ap]\.m|e\.g|i\.e|mr|mrs|ms|dr|prof|st|vs)\.$")
_SENTENCE_SUBJECT = re.compile(r"(?:I|My|We|Our|You|Your|He|She|They|The|This|That|It|Also|Please|Actually|Today)\b")
_PROSE_ASIDE = re.compile(
    r"(?i)(?:[ \t]*[,;][ \t]*|[ \t]+(?:and(?: also)?|but)[ \t]+)"
    r"(?=(?:i|my|we|our|also|that)\b)"
)
_PROSE_SCOPE = r"(?:for|in|within) (?:this (?:chat|session|conversation|project)|my account)(?: only)?"
_PROSE_SCOPE_LEAD = re.compile(rf"(?i)^{_PROSE_SCOPE}[ \t]*:[ \t]*")
_PROSE_SCOPE_TAIL = re.compile(rf"(?i)\s+{_PROSE_SCOPE}[.!]?$")
_PROSE_TERMS = re.compile(r"\d+(?::\d+)?|[^\W\d_]+", re.UNICODE)
_CLOCK_VALUE = re.compile(r"(?i)(?<![\w:])(?:[01]?\d|2[0-3]):[0-5]\d(?:[ \t]*[ap]\.?m\b\.?)?(?![\w:])")
_SAVED_CLOCK_PREFIX = re.compile(r"(?i)^(?:from|using) (?:my )?saved memory(?: only)?[, :]+")
_SAVED_CLOCK_SUFFIX = re.compile(
    r"(?i)(?<=\?)[ \t]+(?:i(?:['’]m| am) (?:checking what you have saved|asking for the saved schedule)|"
    r"(?:please )?use (?:only )?(?:my )?saved memory(?: only)?)\.?$"
)
_CLOCK_QUESTION = re.compile(r"(?i)what time (?:is|do|does) ([^?;!.\r\n]{1,160})\?")
_CLOCK_FOLLOWUP = re.compile(
    r"(?i)(?:,|\b(?:and|or|then)\b)\s*(?:please\s+)?(?:"
    r"(?:can|could|should|will|would|do)\s+(?:you|i|we)\b|"
    r"(?:what|when|where|why|how|check|set|tell|look|show|remind|find|open|send|add|book)\b)"
)
_PROSE_NON_SUBJECT = STOPWORDS | {"user", "now", "take", "leave", "depart", "start", "say", "said",
                                    "am", "pm", "time", "schedule", "account", "project"}
_CLOCK_NON_SUBJECT = _PROSE_NON_SUBJECT - {"from", "to", "not", "no", "nor", "only", "and", "or"}
_WEEKDAYS = frozenset({"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"})
_PROSE_REFERENCE = re.compile(r"(?i)(?:(?:this|that|it|for me)(?: please)?|instead|(?:this|that|it) instead)")
_PROSE_PREAMBLE = re.compile(r"(?i)^(?:actually|correction|update|a quick update)[:,]\s*")

_STATUS_PASTE = re.compile(r"(?i)^\s*(?:the )?backup (?:status:|restored successfully\.)")
_STATUS_REQUEST = re.compile(
    r"(?im)(?:^|[.!]\s+)(?:please|can|could|would|now|stop|cancel|remember|keep|save|write|create|make|"
    r"find|look|check|update|fix|run|open|send|tell|show|explain|review|summari[sz]e)\b"
)
_LIST_HEADING = re.compile(r"^(?:#{1,6}\s+|[1-9]\d?\.\s+)")
_LIST_BULLET = re.compile(r"^[-*•]\s+(.+)")
_LIST_PERSONAL = re.compile(r"(?i)^(?:my|your|the user['’]s) ([-a-z][a-z -]{0,46}?) (is|are) (.+)$")
_LIST_PREFERENCE = re.compile(r"(?i)^(?:I|you) (prefer|use|work with|live in|go by|like|want|have) (.+)$")
_LIST_FIELD = re.compile(r"^([A-Za-z][A-Za-z0-9 /&()._-]{0,45}):\s+([^\s].*)$")
_NO_WRITE = re.compile(
    r"(?i)\b(?:(?:do not|don['’]?t|never|instead of|must not)\s+(?:(?:ever|permanently)\s+)?"
    r"(?:save|remember|store|retain|record|persist)|"
    r"without\s+(?:saving|remembering|storing|retaining|recording|persisting)|no (?:persistent )?memory)\b"
)


def refuses_memory(text: str) -> bool:
    return bool(_NO_WRITE.search(text))


def requests_list(text: str) -> bool:
    return bool(re.match(_LIST_REQUEST, text, re.IGNORECASE))


def requested_scope(text: str) -> Optional[str]:
    """Use an explicit scope and reject conflicting or unsupported restrictions."""
    scopes = set()
    for match in (*_SCOPE.finditer(text), *_LOCAL_ONLY.finditer(text)):
        kind = next(group for group in match.groups() if group).lower()
        scopes.add(kind if kind in {"account", "project"} else "session")
    if len(scopes) > 1 or any(not _SCOPE.search(line.group()) for line in _SCOPE_LINE.finditer(text)):
        return None
    return next(iter(scopes), "account")


def explicit_write(text: str) -> bool:
    """Accept a direct save instruction or a single-line statement with a save request."""
    text = text.strip()
    if (not text or text.endswith("?") or refuses_memory(text) or text.startswith((">", "```", "~"))
            or list_offer(text) or requests_list(text)):
        return False
    return bool(_WRITE_PREFIX.match(text) or ("\n" not in text and _WRITE_SUFFIX.search(text)))


def prose_source(text: str) -> str:
    """Bind the write to one current statement named by the owner's save instruction."""
    text = text.strip()
    if not explicit_write(text):
        return ""
    prefix = _PROSE_PREFIX.match(text)
    suffix = _WRITE_SUFFIX.search(text)
    if prefix:
        payload = text[prefix.end():].strip()
        if suffix and suffix.start() >= prefix.end():
            payload = text[prefix.end():suffix.start()].strip()
    elif suffix:
        payload = text[:suffix.start()].strip()
    else:
        return ""
    payload = _PROSE_SCOPE_LEAD.sub("", payload)
    if not payload or payload.startswith((">", "#", "```", '"', "'", "“", "‘")):
        return ""
    # Quoted names and clock abbreviations can contain punctuation without ending the statement.
    edges = [match for match in _PROSE_BOUNDARY.finditer(payload)
             if not match.group("quoted") and (
                 match.group("clause")
                 or not _ABBREVIATION.search(payload, max(0, match.start() - 8), match.start())
                 or _SENTENCE_SUBJECT.match(payload, match.end())
             )]
    if prefix and edges:
        payload = payload[:edges[0].start()]
    elif not prefix and edges:
        payload = payload[edges[-1].end():]
    if payload.rstrip().endswith("?"):
        return ""
    payload = _PROSE_PREAMBLE.sub("", payload)
    payload = _PROSE_SCOPE_TAIL.sub("", payload).strip().rstrip(",;")
    if aside := _PROSE_ASIDE.search(payload):
        # A trailing temporary qualifier can apply to the whole clause.
        if re.search(r"(?i)\b(?:just for today|only today|only for now)\b", payload) and "but" not in aside.group().lower():
            return ""
        payload = payload[:aside.start()]
    from agent.memory_curator import MemoryCurator
    payload = MemoryCurator.strip_action_tail(payload)
    if not payload or _PROSE_REFERENCE.fullmatch(payload):
        return ""
    return tidy_fact(payload)


def _prose_words(text: str) -> Iterable[str]:
    return (word[:-1] if len(word) > 3 and word.endswith("s") else word
            for word in _PROSE_TERMS.findall(text.casefold()))


def _prose_terms(text: str) -> set[str]:
    return set(_prose_words(text)) - _PROSE_NON_SUBJECT


def prose_proposal_matches(source: str, proposal: str) -> bool:
    """A proposal may identify the requested fact. Its wording is never persisted."""
    evidence, proposed = _prose_terms(source), _prose_terms(proposal)
    return (bool(evidence) and {term for term in evidence if term[0].isdigit()} <= proposed
            and len(evidence & proposed) >= min(2, len(evidence)))


def prose_key_matches(source: str, key: str) -> bool:
    subject = _prose_terms(key)
    return bool(subject) and subject <= _prose_terms(source)


def prose_clock_subject(fact: str) -> tuple[str, ...]:
    """Match one clock value without changing the order of route or subject terms."""
    subject, clocks = _CLOCK_VALUE.subn("", fact)
    return _clock_subject(subject) if clocks == 1 else ()


def _clock_subject(text: str) -> tuple[str, ...]:
    words = [word for word in _prose_words(text) if word not in _CLOCK_NON_SUBJECT]
    name = tuple(word for word in words if word not in _WEEKDAYS)
    return (*name, *sorted(set(words) & _WEEKDAYS)) if name else ()


def saved_clock_query(message: str) -> str:
    """Recognize a single schedule question explicitly limited to saved memory."""
    question, prefix = _SAVED_CLOCK_PREFIX.subn("", message.strip())
    question, suffix = _SAVED_CLOCK_SUFFIX.subn("", question)
    match = _CLOCK_QUESTION.fullmatch(question)
    if not (prefix or suffix) or not match or _CLOCK_FOLLOWUP.search(match[1]):
        return ""
    return match[1] if _clock_subject(match[1]) else ""


def saved_clock_reply(memories: Iterable[dict[str, Any]]) -> str:
    matches = list(dict.fromkeys(str(row["content"]) for row in memories))
    if not matches:
        return "I couldn’t find a matching saved schedule."
    intro = "I found this saved schedule." if len(matches) == 1 else "I found multiple matching saved schedules."
    return intro + "\n\n" + "\n\n".join(f"> {fact}" for fact in matches)


def saved_clock_matches(query: str, fact: str) -> bool:
    return prose_clock_subject(fact) == _clock_subject(query)


def requests_prose_replacement(text: str) -> bool:
    suffix = _WRITE_SUFFIX.search(text.strip())
    return bool(suffix and suffix.group("replacement"))


def structured_field_key(fact: str) -> str:
    field = _LIST_FIELD.fullmatch(fact) or _LIST_PERSONAL.fullmatch(fact)
    return normalize_key(field[1]) if field else ""


def list_offer(text: str) -> bool:
    """An offer to provide a memory list later, with no facts attached yet."""
    return (len(text) <= 240 and "\n" not in text and ":" not in text and not refuses_memory(text)
            and bool(_LIST_OFFER.search(text) and _NEXT_MESSAGE.search(text) and _LIST_CONSENT.search(text)))


def list_status_paste(text: str) -> bool:
    """Recognize the short backup receipt that may precede the promised list."""
    return (len(text) <= 1200 and "?" not in text and not refuses_memory(text)
            and not _STATUS_REQUEST.search(text) and bool(_STATUS_PASTE.search(text)))


def _list_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def is_structured_list(text: str) -> bool:
    lines = _list_lines(text)
    return bool(lines and (_LIST_HEADING.match(lines[0]) or _LIST_BULLET.match(lines[0]))
                and sum(bool(_LIST_BULLET.match(line) or _LIST_FIELD.match(line)) for line in lines) >= 2)


def list_needs_consent(text: str) -> bool:
    return is_structured_list(text) and not refuses_memory(text) and not _STATUS_REQUEST.search(text)


def list_cancelled(text: str) -> bool:
    return bool(re.fullmatch(r"(?i)\s*(?:cancel(?: that)?|never mind|nevermind)[.!]?\s*", text))


def list_entries(text: str) -> Optional[tuple[list[dict[str, str]], int]]:
    """Read fields only from a list with an explicit header in the current message."""
    direct = _DIRECT_LIST.match(text)
    if not direct or refuses_memory(text) or requested_scope(text) is None:
        return None
    list_body = text[direct.end():]
    if not is_structured_list(list_body):
        return None

    entries: list[dict[str, str]] = []
    skipped = 0
    fence = ""
    for line in _list_lines(list_body):
        if line.startswith(("```", "~~~")):
            fence = "" if fence == line[:3] else (fence or line[:3])
            skipped += 1
            continue
        if fence:
            skipped += 1
            continue
        if _LIST_HEADING.match(line) or _LOCAL_ONLY.fullmatch(line) or (_SCOPE_LINE.fullmatch(line) and _SCOPE.search(line)):
            continue
        bullet = _LIST_BULLET.match(line)
        body = bullet[1] if bullet else line
        key = structured_field_key(body)
        if personal := _LIST_PERSONAL.fullmatch(body):
            label, verb, value = personal.groups()
            body = f"The user's {label} {verb} {value}"
        elif preference := _LIST_PREFERENCE.fullmatch(body):
            verb, value = preference.groups()
            action = {"prefer": "prefers", "use": "uses", "work with": "works with",
                      "live in": "lives in", "go by": "goes by", "like": "likes", "want": "wants",
                      "have": "has"}[verb.lower()]
            body = f"The user {action} {value}"
        elif not key:
            skipped += 1
            continue
        fact = tidy_fact(body)
        entry = {"fact": fact, "key": key}
        if entry in entries:
            continue
        if check_fact(fact) or len(entries) >= LIST_LIMIT:
            skipped += 1
            continue
        entries.append(entry)
    # A contradictory list shouldn't replace an existing value with an arbitrary winner.
    conflicting = {row["key"] for row in entries if row["key"] and any(
        other["key"] == row["key"] and other["fact"] != row["fact"] for other in entries
    )}
    accepted = [row for row in entries if row["key"] not in conflicting]
    return accepted, skipped + len(entries) - len(accepted)


def list_receipt(timeline: list[dict[str, Any]], requested: int, skipped: int, *, scope: str) -> str:
    rows = [item for item in timeline if item.get("kind") == "tool" and item.get("name") == "memory_save"]
    written = [item["output"].removeprefix("Saved and verified: ") for item in rows
               if item.get("meta", {}).get("memory_written") is True]
    existing = [item["output"].removeprefix("Already remembered (verified on disk): ") for item in rows
                if item.get("meta", {}).get("memory_written") is False and item["meta"].get("memory_verified")]
    parts = []
    if written:
        noun = "entry" if len(written) == 1 else "entries"
        where = {"session": " for this chat", "project": " for this project", "account": ""}[scope]
        parts.append(f"Saved and verified {len(written)} {noun}{where}.\n" + "\n".join(f"- {value}" for value in written))
    if existing:
        parts.append(f"Already in memory ({len(existing)}).\n" + "\n".join(f"- {value}" for value in existing))
    if failed := requested - len(written) - len(existing):
        noun = "entry" if failed == 1 else "entries"
        parts.append(f"I couldn’t verify {failed} {noun}. The tool results show what happened.")
    if skipped:
        noun = "line" if skipped == 1 else "lines"
        parts.append(f"I left {skipped} other {noun} in this conversation. To save a specific detail, use "
                     "a short bullet in the form `Label: value`.")
    return "\n\n".join(parts) or "There were no entries to save."

_LEAD_INS = re.compile(
    r"^(please\s+)?(remember|note|keep in mind|save|store)( that|:)?\s+|^(fyi|for the record)[,:]?\s+",
    re.IGNORECASE,
)
# Requests and reminders belong to routines or the chat, not long-term memory.
_TRANSIENT = re.compile(
    r"\b(remind me|right now|at the moment|for now|for today|only today|this (chat|conversation|session)|in this chat)\b",
    re.IGNORECASE,
)


# A saved claim about which EchoSpeak version is current ("The current version is 10.4.1").
# The running app knows its version (prompt.identity_section), so such claims are only
# ever stale: they are left out of recall instead of being deleted from memory.
_VERSION_NUMBER = re.compile(r"\bv?\d+\.\d+(?:\.\d+)?\b")
_VERSION_CLAIM = re.compile(
    r"(?i)\b(echo\s?speak|current(?:ly)?\s+(?:on\s+)?(?:version|release|build)|latest\s+(?:version|release)|"
    r"you(?:'re|\s+are)\s+(?:on|running)\s+(?:version\s+)?v?\d)"
)
_VERSION_WORD = re.compile(r"(?i)\b(version|ver[is]{2}on|release|build|v\d)")


def stale_version_claim(text: str) -> bool:
    """A remembered statement of which EchoSpeak version is current (outranked by the live version)."""
    body = str(text or "")
    return bool(_VERSION_NUMBER.search(body) and _VERSION_WORD.search(body) and _VERSION_CLAIM.search(body))


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
    rows = [m for m in memories if str(m.get("content") or "").strip() and str(m.get("type") or "") != "conversation"
            and not stale_version_claim(str(m.get("content") or ""))]
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
        # Provenance: when it was saved, and whether it's about this project only, so the model can say
        # "you told me on…" or notice a fact may have changed instead of presenting it as current truth.
        where = {"project": "this project", "session": "this chat"}.get(str(memory.get("scope") or ""), "")
        # And its key, so a change can be saved under it and replace the old value (evaluation LSH2, 19 November).
        key = str(memory.get("semantic_key") or "").rsplit(":", 1)[-1]
        origin = ", ".join(part for part in (f"saved {memory['saved_at']}" if memory.get("saved_at") else "", where,
                                             f"key {key}" if key else "") if part)
        lines.append(f"- {line}" + (f" ({origin})" if origin else ""))
        used += len(line)
        if len(lines) >= PROMPT_ROWS:
            break
    if not lines:
        return ""
    return ("## What you know about the user\n"
            "Saved facts, possibly out of date. When answering from memory, keep values as written and leave unstated "
            "details unspecified, including a reason, date, time zone or AM/PM. Use the facts when they help. When the user says one "
            "of them has changed, save the new value with memory_save and the same key, so it replaces the old one.\n"
            + "\n".join(lines))
