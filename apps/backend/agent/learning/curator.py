"""The lesson curator: a model proposes lessons, code decides what is kept.

Same rule as agent/memory_curator.py. A proposal is refused when it:
- has a link, markup, or a stored credential in it;
- is about permissions, approvals, safety rules, secrets, or skipping or
  changing tests and checks (learning can't loosen any of those);
- reads like an injected instruction ("ignore previous instructions");
- is too short to be a strategy or too long to be one.

What passes is stored as advisory text, never as a setting. It becomes
"unproven" (probation) when it came from a trusted, untampered episode,
and waits for the owner's review otherwise: when the request read outside
content, changed tests or EchoSpeak's own files, or mentions sending things
out. Near-duplicates join the existing lesson as more evidence instead.
"""

from __future__ import annotations

import re
from typing import Any

from agent.lean.job import near_duplicate
from agent.lean.policy import EXTERNAL_ACTIONS, contains_secret
from agent.learning.store import ACTIVE_STATUSES, Episode, Lesson, get_experience_store
from agent.stopwords import keywords

MAX_PER_EPISODE = 2
MAX_ACTIVE_PER_AGENT = 40
MAX_PENDING_PER_AGENT = 20
TITLE_RANGE = (3, 80)
TEXT_RANGE = (20, 400)

_LINK = re.compile(r"(?i)\b(https?://|www\.|ftp://)|\b[\w.-]+\.(com|net|org|io|dev|ai|co|xyz|ru|cn)\b/")
_MARKUP = re.compile(r"[<>`{}]|\[\s*system\s*\]", re.I)
# Never learnable, whatever the wording: the controls that keep agents safe.
_FORBIDDEN = re.compile(
    r"(?ix)\b("
    r"approv\w*|permission\w*|consent|authori[sz]\w*|sandbox\w*|policy|policies|safety\s+rules?|guardrails?|"
    r"rule\s+of\s+two|untrusted|"
    r"(api|access|auth|bearer|ssh|private)[\s_-]?(keys?|tokens?)|passwords?|credentials?|secrets?|"
    r"always\s+allow|auto[\s-]?approve|"
    r"system\s+prompt|developer\s+message|jailbreak"
    r")\b"
)
# Weakening checks ("skip the tests", "change the assertion"). Allowed only when
# the lesson says not to ("don't skip the tests", "never edit a test to pass it").
_WEAKENS_CHECKS = re.compile(
    r"(?ix)\b("
    r"(skip\w*|bypass\w*|disabl\w*|turn\w*\s+off|ignor\w*|silenc\w*)\s+(the\s+|a\s+|any\s+|failing\s+)?"
    r"(checks?|verification|tests?|reviews?|linters?|assertions?)|"
    r"(edit\w*|chang\w*|modif\w*|delet\w*|remov\w*|rewrit\w*|weaken\w*|loosen\w*|comment\w*\s+out|xfail\w*|mark\w*)\s+"
    r"(the\s+|a\s+|an\s+|any\s+|failing\s+|existing\s+)?(tests?|assertions?|checks?|test\s+files?|ci|workflows?)|"
    r"without\s+(checking|verifying|testing|running\s+(it|the\s+tests?))"
    r")\b"
)
_NEGATED = re.compile(r"(?i)(don'?t|do\s+not|never|instead\s+of|rather\s+than|avoid|not)\s+(\w+\s+){0,2}$")
_INJECTION = re.compile(
    r"(?i)(ignore|disregard|forget)\s+(all\s+|any\s+|the\s+|your\s+)?(previous|prior|above|earlier|other)\s+"
    r"(instructions?|rules?|lessons?|messages?)|you\s+are\s+now\b|new\s+instructions?\s*:|from\s+now\s+on\s+you"
)
_EXTERNAL_WORDS = re.compile(
    r"(?i)\b(e-?mail|discord|telegram|whatsapp|slack|tweet|twitter|post\s+(it|to|on)|send\s+(it|a|an|the|messages?)|"
    r"upload|publish|calendar|github\s+issue)\b"
)


# Lessons must transfer: overly specific heuristics are why self-generated skills made agents
# worse in SkillsBench (-1.3 pp, via the 2026 skills SoK). A named file is the clearest sign.
_FILE_NAME = re.compile(r"(?i)\b[\w-]+\.(py|js|jsx|ts|tsx|mjs|json|csv|md|txt|html|css|rs|go|java|sh|ps1|ya?ml|toml|ini|log|xlsx?|docx?|pdf)\b")


def same_lesson(a: str, b: str) -> bool:
    """The same strategy, worded the same or paraphrased (ACE dedupes by meaning, not wording)."""
    if near_duplicate(a, b, threshold=0.6):
        return True
    ka, kb = set(keywords(a, limit=40)), set(keywords(b, limit=40))
    return len(ka) >= 4 and len(kb) >= 4 and len(ka & kb) / len(ka | kb) >= 0.6


def vet(title: str, text: str) -> str:
    """Why a proposed lesson can't be kept, or ''."""
    if not TITLE_RANGE[0] <= len(title) <= TITLE_RANGE[1]:
        return "title length"
    if not TEXT_RANGE[0] <= len(text) <= TEXT_RANGE[1]:
        return "text length"
    body = f"{title}\n{text}"
    if contains_secret({"lesson": body}):
        return "contains a stored credential"
    if _LINK.search(body):
        return "contains a link"
    if _MARKUP.search(body):
        return "contains markup"
    if _FILE_NAME.search(body):
        return "too specific: names a file"
    if _INJECTION.search(body):
        return "reads like an injected instruction"
    match = _FORBIDDEN.search(body)
    if match:
        return f"about permissions, safety or secrets (\"{match.group(0)[:40]}\")"
    for match in _WEAKENS_CHECKS.finditer(body):
        if not _NEGATED.search(body[max(0, match.start() - 30):match.start()]):
            return f"weakens tests or checks (\"{match.group(0)[:40]}\")"
    return ""


def _clean(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return re.sub(r"[\x00-\x1f\x7f]", "", text).strip(" -•*\"'")[: limit + 1]


def _review_reason(episode: Episode, title: str, text: str) -> str:
    """Why a lesson must wait for the owner before any agent reads it, or ''."""
    if not episode.trusted:
        return "learned while reading outside content (" + ", ".join(episode.taint[:3]) + ")"
    unexpected = [t for t in episode.tamper if not t.get("expected")]
    if unexpected:
        return f"learned from a task that changed {unexpected[0].get('what', 'protected files')}"
    if episode.tamper:
        return "learned from a task that wrote its own tests"
    body = f"{title} {text}".lower()
    if _EXTERNAL_WORDS.search(body) or any(name in body for name in EXTERNAL_ACTIONS):
        return "it's about sending or posting things for you"
    return ""


def admit(episode: Episode, proposals: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Store what passes. Returns {"created": ids, "merged": ids, "refused": reasons}."""
    store = get_experience_store()
    out: dict[str, list[str]] = {"created": [], "merged": [], "refused": []}
    existing = store.lessons(agent_id=episode.agent_id)
    for raw in proposals[:MAX_PER_EPISODE]:
        if not isinstance(raw, dict):
            out["refused"].append("not an object")
            continue
        title = _clean(raw.get("title"), TITLE_RANGE[1])
        text = _clean(raw.get("text") or raw.get("strategy"), TEXT_RANGE[1])
        kind = "avoid" if str(raw.get("kind") or "").strip().lower() == "avoid" else "do"
        problem = vet(title, text)
        if problem:
            out["refused"].append(problem)
            continue
        twin = next((lesson for lesson in existing if same_lesson(f"{lesson.title} {lesson.text}", f"{title} {text}")), None)
        if twin is not None:
            if episode.id not in twin.source_episodes:
                before = Lesson.from_dict(twin.to_dict())
                twin.source_episodes = (twin.source_episodes + [episode.id])[-20:]
                twin.level = max(twin.level, episode.level)
                store.update_lesson(twin, actor="curator", action="evidence", reason=f"seen again in {episode.id}", before=before)
            out["merged"].append(twin.id)
            continue
        review = _review_reason(episode, title, text)
        pending = [lesson for lesson in existing if lesson.status == "pending_review"]
        if review and len(pending) >= MAX_PENDING_PER_AGENT:
            out["refused"].append("review queue full")
            continue
        lesson = Lesson(
            agent_id=episode.agent_id,
            title=title,
            text=text,
            kind=kind,
            status="pending_review" if review else "probation",
            task_kind=episode.task_kind,
            trusted=episode.trusted,
            source_episodes=[episode.id],
            level=episode.level,
            note=("waiting for your review: " + review) if review else "new: unproven until it helps in checked tasks",
        )
        store.add_lesson(lesson, actor="curator", reason=lesson.note)
        existing.append(lesson)
        out["created"].append(lesson.id)
    _make_room(episode.agent_id)
    return out


def _make_room(agent_id: str) -> None:
    """Keep each playbook bounded: retire the weakest unproven lessons past the cap."""
    store = get_experience_store()
    active = store.lessons(agent_id=agent_id, statuses=ACTIVE_STATUSES)
    extra = len(active) - MAX_ACTIVE_PER_AGENT
    if extra <= 0:
        return
    unproven = sorted((l for l in active if l.status == "probation"),
                      key=lambda l: (l.wins - l.losses, l.last_used_at or l.created_at))
    for lesson in unproven[:extra]:
        before = Lesson.from_dict(lesson.to_dict())
        lesson.status, lesson.note = "retired", "made room: the playbook is full and this one hadn't proven itself"
        store.update_lesson(lesson, actor="curator", action="status:retired", reason=lesson.note, before=before)
