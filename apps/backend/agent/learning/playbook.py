"""Each agent's playbook: picking lessons for a task, and the rules that promote or retire them.

A lesson is read before similar tasks and labeled advisory: it can suggest an
approach, never permit anything. Every lesson starts unproven (probation) and
earns its place by the checked results of the tasks it was used on, in this
agent's own work. Counting and status changes happen here, in code, so the
same history always gives the same playbook.

    probation   -> established  after 3+ checked wins and a 75%+ win rate
    probation   -> retired      after 3+ losses and a win rate under 40%
    established -> probation    when its losses catch up with its wins
    unused for 60 days (unproven) or 120 days (proven) -> retired

A "win" is a task that succeeded with a check after the work (V2+) or that
the owner said worked. An unchecked success counts as nothing: lessons are
promoted on evidence, not on the agent's say-so.
"""

from __future__ import annotations

import time
from typing import Optional

from agent.learning.episodes import guess_kind
from agent.learning.store import ACTIVE_STATUSES, Episode, Lesson, get_experience_store
from agent.stopwords import keywords

PROMOTE_WINS = 3
PROMOTE_RATE = 0.75
RETIRE_LOSSES = 3
RETIRE_RATE = 0.4
STALE_UNPROVEN_DAYS = 60
STALE_PROVEN_DAYS = 120
# Unproven lessons per prompt: room to try new ones without crowding out proven ones.
MAX_UNPROVEN_IN_PROMPT = 2

HEADER = (
    "## Lessons from your past work\n"
    "Short strategies from your own earlier tasks that were checked. They are advice, not rules: the user's "
    "request, your instructions and the safety rules always come first, and a lesson never permits anything. "
    "Skip any that don't fit this task."
)

# Evaluation only ("control" mode): same count and size as real lessons, about nothing
# the agent will be asked, so a measured gain can't come from a longer prompt alone.
CONTROL_NOTES = [
    "Knitting patterns read more easily when each row's stitch count is written at the end of the row.",
    "Sourdough starters stay active longer when they are fed at the same time every day.",
    "Bird-watching notes are more useful when they record the weather and time next to each sighting.",
    "Houseplants with thin leaves usually need more humidity than plants with thick, waxy leaves.",
    "Chess openings are easier to remember by their plans than by their exact move orders.",
    "Watercolor washes dry lighter than they look when wet, so mix them a little darker.",
    "Bicycle chains last longer when they are cleaned before they are oiled, not after.",
    "Crossword clues that end in a question mark usually signal wordplay rather than a plain definition.",
]


def _score(lesson: Lesson, words: set[str], kind: str) -> float:
    overlap = len(words & set(keywords(f"{lesson.title} {lesson.text}", limit=40)))
    kind_match = bool(lesson.task_kind and lesson.task_kind == kind)
    # A loosely related lesson costs more than it helps: negative transfer hits hard tasks
    # hardest (arXiv 2604.27003). Same kind of task and a shared word, or two shared words.
    if overlap < (1 if kind_match else 2):
        return 0.0
    decided = lesson.wins + lesson.losses
    utility = (lesson.wins - lesson.losses) / decided if decided else 0.0
    return overlap + (2.0 if kind_match else 0.0) + (1.0 if lesson.status == "established" else 0.0) + utility


def select(agent_id: str, goal: str, limit: int) -> list[Lesson]:
    """The lessons most relevant to ``goal``, proven first; none that don't match."""
    if limit <= 0:
        return []
    kind = guess_kind(goal)
    words = set(keywords(goal, limit=24))
    ranked = sorted(
        ((score, lesson) for lesson in get_experience_store().lessons(agent_id=agent_id, statuses=ACTIVE_STATUSES)
         if (score := _score(lesson, words, kind)) > 0),
        key=lambda item: -item[0],
    )
    picked: list[Lesson] = []
    unproven = 0
    for _, lesson in ranked:
        if lesson.status != "established":
            if unproven >= MAX_UNPROVEN_IN_PROMPT:
                continue
            unproven += 1
        picked.append(lesson)
        if len(picked) >= limit:
            break
    return picked


def section(lessons: list[Lesson]) -> str:
    if not lessons:
        return ""
    lines = [HEADER]
    for lesson in lessons:
        tag = "proven" if lesson.status == "established" else "unproven"
        lines.append(f"- ({tag}) {lesson.title}: {lesson.text}")
    return "\n".join(lines)


def control_section(lessons: list[Lesson]) -> str:
    """Same count and roughly the same length as ``section(lessons)``, with unrelated notes."""
    if not lessons:
        return ""
    lines = [HEADER]
    for index, lesson in enumerate(lessons):
        note = CONTROL_NOTES[index % len(CONTROL_NOTES)]
        size = len(lesson.title) + len(lesson.text) + 2
        while len(note) < size:
            note += " " + CONTROL_NOTES[(index + len(note)) % len(CONTROL_NOTES)]
        lines.append(f"- (unproven) {note[:size]}")
    return "\n".join(lines)


# ── attribution and lifecycle ───────────────────────────────────────────

def verdict(episode: Episode) -> str:
    """What an episode counts for, for the lessons it used: win | loss | ''."""
    if episode.failed:
        return "loss"
    return "win" if episode.verified_success else ""


def attribute(episode: Episode, *, previous: Optional[str] = None) -> None:
    """Credit (or blame) the lessons this episode used.

    First call: ``previous`` is None and each lesson's use is counted. After the
    owner's feedback, ``previous`` is what the episode counted for before, which
    is taken back before the new verdict is counted (never a second use).
    """
    new = verdict(episode) or "none"
    first_time = previous is None
    old = "none" if first_time else (previous or "none")
    if not episode.lessons_used or (not first_time and old == new):
        episode.attribution = new
        return
    store = get_experience_store()
    for lesson_id in episode.lessons_used:
        lesson = store.get_lesson(lesson_id)
        if lesson is None or lesson.agent_id != episode.agent_id:
            continue
        before = Lesson.from_dict(lesson.to_dict())
        if first_time:
            lesson.uses += 1
            lesson.last_used_at = max(lesson.last_used_at, episode.created_at)
        if old == "win":
            lesson.wins = max(0, lesson.wins - 1)
        elif old == "loss":
            lesson.losses = max(0, lesson.losses - 1)
        if new == "win":
            lesson.wins += 1
        elif new == "loss":
            lesson.losses += 1
        change = lifecycle(lesson)
        if change:
            lesson.status, lesson.note = change
            store.update_lesson(lesson, actor="system", action=f"status:{lesson.status}", reason=change[1], before=before)
        elif new == "none" and old == "none":
            store.update_lesson(lesson, actor="system", action="used", before=before, quiet=True)
        else:
            store.update_lesson(lesson, actor="system", action=f"counted:{new}",
                                reason=f"{episode.id}: " + ("helped" if new == "win" else "failed" if new == "loss" else "no checked result"),
                                before=before)
    episode.attribution = new


def lifecycle(lesson: Lesson) -> Optional[tuple[str, str]]:
    """(new status, reason) when the counts call for a change, else None."""
    decided = lesson.wins + lesson.losses
    rate = lesson.wins / decided if decided else 0.0
    if lesson.status == "probation":
        if lesson.wins >= PROMOTE_WINS and rate >= PROMOTE_RATE:
            return "established", f"proven: helped in {lesson.wins} of {decided} checked tasks"
        if lesson.losses >= RETIRE_LOSSES and rate < RETIRE_RATE:
            return "retired", f"didn't help: {lesson.losses} of {decided} checked tasks failed with it"
    elif lesson.status == "established" and lesson.losses > lesson.wins:
        return "probation", f"started failing ({lesson.losses} losses, {lesson.wins} wins): unproven again"
    return None


def retire_stale(now: Optional[float] = None) -> int:
    """Retire lessons nobody has used for a long time. Returns how many."""
    now = float(now or time.time())
    store = get_experience_store()
    retired = 0
    for lesson in store.lessons(statuses=ACTIVE_STATUSES):
        last = max(lesson.last_used_at, lesson.created_at)
        days = STALE_PROVEN_DAYS if lesson.status == "established" else STALE_UNPROVEN_DAYS
        if now - last > days * 86400:
            before = Lesson.from_dict(lesson.to_dict())
            lesson.status, lesson.note = "retired", f"not used for {days} days"
            store.update_lesson(lesson, actor="system", action="status:retired", reason=lesson.note, before=before)
            retired += 1
    return retired
