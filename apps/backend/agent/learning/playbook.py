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

Wins alone can't tell a helpful lesson from one that rides along on tasks the
agent would have done anyway, and self-written playbooks can do worse than no
playbook at all (arXiv 2610.18427, "Reliable Memory from Unstable Growth").
So a matching lesson is sometimes held back at random, and the checked results
of those tasks are its control group: the same kind of task, picked the same way.

    probation -> stays unproven  when it has 3+ held-back results and does no better with it
    probation -> retired         when tasks went clearly better without it
    established -> probation     the same, for a proven lesson (held back less often)

Scope and transfer (one learning system across Chat, Agents and Code):
- A lesson belongs to the agent that learned it. Echo in Agents and in Code is one agent, so
  its lessons already serve both; Chat is its own agent.
- A lesson learned about one project is read only in that project.
- A *proven*, trusted, general lesson may be offered to another agent for the same kind of task
  (one per prompt, after the agent's own). Those uses are counted apart from its home results,
  and transfer stops when it fails there, so negative transfer can't spread or harm its home record.
- A lesson learned while reading outside content (trusted=False) never transfers, and is retired
  after 2 losses without a win.
- A lesson waiting for review that nobody reviews in 30 days is set aside: learning never waits on the owner.
"""

from __future__ import annotations

import random
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
# How often a matching lesson is held back to measure tasks without it. The owner's own
# lessons are never held back.
HOLDOUT_UNPROVEN = 0.2
HOLDOUT_PROVEN = 0.1
# Held-back checked results needed before the comparison can block a promotion or retire.
MIN_HELD = 3
# Lessons learned while reading outside content: quicker to retire.
UNTRUSTED_RETIRE_LOSSES = 2
# Transfer to other agents: at most this many per prompt; stops after this many losses there at a low rate.
MAX_SHARED_IN_PROMPT = 1
TRANSFER_STOP_LOSSES = 2
# Unreviewed lessons are set aside after this long.
PENDING_EXPIRY_DAYS = 30
# How much better tasks must go without a lesson before it counts as harmful.
HARM_MARGIN = 0.25

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


def _in_scope(lesson: Lesson, project_id: str) -> bool:
    return not lesson.project_id or lesson.project_id == project_id


def select(agent_id: str, goal: str, limit: int, project_id: str = "") -> list[Lesson]:
    """The lessons most relevant to ``goal``, proven first; none that don't match."""
    if limit <= 0:
        return []
    kind = guess_kind(goal)
    words = set(keywords(goal, limit=24))
    ranked = sorted(
        ((score, lesson) for lesson in get_experience_store().lessons(agent_id=agent_id, statuses=ACTIVE_STATUSES)
         if _in_scope(lesson, project_id) and (score := _score(lesson, words, kind)) > 0),
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


def transferable(agent_id: str, goal: str, project_id: str = "", taken: Optional[list[Lesson]] = None) -> list[Lesson]:
    """Proven lessons other agents learned, for the same kind of task (at most MAX_SHARED_IN_PROMPT).

    Only established, trusted lessons that still transfer; a project lesson only in its project; never
    one this agent already has in its own words.
    """
    kind = guess_kind(goal)
    words = set(keywords(goal, limit=24))
    own = [f"{l.title} {l.text}" for l in (taken or [])]
    from agent.learning.curator import same_lesson

    ranked = sorted(
        ((score, lesson) for lesson in get_experience_store().lessons(statuses=("established",))
         if lesson.agent_id != agent_id and lesson.trusted and lesson.transfer and lesson.task_kind == kind
         and _in_scope(lesson, project_id) and (score := _score(lesson, words, kind)) > 0
         and not any(same_lesson(text, f"{lesson.title} {lesson.text}") for text in own)),
        key=lambda item: -item[0],
    )
    return [lesson for _, lesson in ranked[:MAX_SHARED_IN_PROMPT]]


def holdout(lessons: list[Lesson], rng: Optional[random.Random] = None) -> tuple[list[Lesson], list[Lesson]]:
    """(lessons to show, lessons held back this time as the control group)."""
    rng = rng or random
    used: list[Lesson] = []
    withheld: list[Lesson] = []
    for lesson in lessons:
        rate = 0.0 if lesson.origin == "owner" else HOLDOUT_PROVEN if lesson.status == "established" else HOLDOUT_UNPROVEN
        (withheld if rng.random() < rate else used).append(lesson)
    return used, withheld


def section(lessons: list[Lesson], shared: Optional[list[Lesson]] = None) -> str:
    if not lessons and not shared:
        return ""
    lines = [HEADER]
    for lesson in lessons:
        tag = "proven" if lesson.status == "established" else "unproven"
        lines.append(f"- ({tag}) {lesson.title}: {lesson.text}")
    for lesson in shared or []:
        lines.append(f"- (proven in another agent's work, new to yours) {lesson.title}: {lesson.text}")
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
    _attribute_shared(episode, old, new, first_time)
    counted = [(lesson_id, False) for lesson_id in episode.lessons_used]
    counted += [(lesson_id, True) for lesson_id in episode.lessons_withheld if lesson_id not in episode.lessons_used]
    if not counted or (not first_time and old == new):
        episode.attribution = new
        return
    store = get_experience_store()
    for lesson_id, held in counted:
        lesson = store.get_lesson(lesson_id)
        if lesson is None or lesson.agent_id != episode.agent_id:
            continue
        before = Lesson.from_dict(lesson.to_dict())
        if first_time and not held:
            lesson.uses += 1
            lesson.last_used_at = max(lesson.last_used_at, episode.created_at)
        wins, losses = ("held_wins", "held_losses") if held else ("wins", "losses")
        if old == "win":
            setattr(lesson, wins, max(0, getattr(lesson, wins) - 1))
        elif old == "loss":
            setattr(lesson, losses, max(0, getattr(lesson, losses) - 1))
        if new == "win":
            setattr(lesson, wins, getattr(lesson, wins) + 1)
        elif new == "loss":
            setattr(lesson, losses, getattr(lesson, losses) + 1)
        change = lifecycle(lesson)
        if change:
            lesson.status, lesson.note = change
            store.update_lesson(lesson, actor="system", action=f"status:{lesson.status}", reason=change[1], before=before)
        elif new == "none" and old == "none":
            store.update_lesson(lesson, actor="system", action="held_back" if held else "used", before=before, quiet=True)
        elif held:
            store.update_lesson(lesson, actor="system", action=f"control:{new}",
                                reason=f"{episode.id}: held back at random; the task "
                                       + ("succeeded" if new == "win" else "failed" if new == "loss" else "had no checked result")
                                       + " without it",
                                before=before)
        else:
            store.update_lesson(lesson, actor="system", action=f"counted:{new}",
                                reason=f"{episode.id}: " + ("helped" if new == "win" else "failed" if new == "loss" else "no checked result"),
                                before=before)
    episode.attribution = new


def _attribute_shared(episode: Episode, old: str, new: str, first_time: bool) -> None:
    """Count another agent's use of a proven lesson apart from its home record; stop transfer that hurts."""
    if not episode.lessons_shared or (not first_time and old == new):
        return
    store = get_experience_store()
    for lesson_id in episode.lessons_shared:
        lesson = store.get_lesson(lesson_id)
        if lesson is None or lesson.agent_id == episode.agent_id:
            continue
        before = Lesson.from_dict(lesson.to_dict())
        if first_time:
            lesson.shared_uses += 1
        if old == "win":
            lesson.shared_wins = max(0, lesson.shared_wins - 1)
        elif old == "loss":
            lesson.shared_losses = max(0, lesson.shared_losses - 1)
        if new == "win":
            lesson.shared_wins += 1
        elif new == "loss":
            lesson.shared_losses += 1
        decided = lesson.shared_wins + lesson.shared_losses
        stop = lesson.transfer and lesson.shared_losses >= TRANSFER_STOP_LOSSES and lesson.shared_wins / decided < RETIRE_RATE
        if stop:
            lesson.transfer = False
        store.update_lesson(lesson, actor="system", action="transfer:stopped" if stop else f"shared:{new}",
                            reason=(f"{episode.id}: didn't help {episode.agent_name or episode.agent_id} "
                                    f"({lesson.shared_wins} of {decided} checked tasks): no longer shared" if stop
                                    else f"{episode.id}: used by {episode.agent_name or episode.agent_id}"),
                            before=before, quiet=not stop)


def lifecycle(lesson: Lesson) -> Optional[tuple[str, str]]:
    """(new status, reason) when the counts call for a change, else None."""
    decided = lesson.wins + lesson.losses
    rate = lesson.wins / decided if decided else 0.0
    held = lesson.held_wins + lesson.held_losses
    held_rate = lesson.held_wins / held if held else 0.0
    compared = held >= MIN_HELD
    harmful = compared and decided >= RETIRE_LOSSES and rate + HARM_MARGIN < held_rate
    versus = f"{lesson.wins} of {decided} checked tasks with it, {lesson.held_wins} of {held} without"
    if lesson.status == "probation":
        if harmful:
            return "retired", f"tasks went better without it: {versus}"
        if not lesson.trusted and lesson.losses >= UNTRUSTED_RETIRE_LOSSES and not lesson.wins:
            return "retired", f"learned while reading outside content and failed {lesson.losses} checked tasks"
        if lesson.wins >= PROMOTE_WINS and rate >= PROMOTE_RATE and not (compared and rate <= held_rate):
            return "established", f"proven: helped in {lesson.wins} of {decided} checked tasks" + (
                f" ({lesson.held_wins} of {held} without it)" if compared else "")
        if lesson.losses >= RETIRE_LOSSES and rate < RETIRE_RATE:
            return "retired", f"didn't help: {lesson.losses} of {decided} checked tasks failed with it"
    elif lesson.status == "established":
        if harmful:
            return "probation", f"tasks went better without it ({versus}): unproven again"
        if lesson.losses > lesson.wins:
            return "probation", f"started failing ({lesson.losses} losses, {lesson.wins} wins): unproven again"
    return None


def retire_stale(now: Optional[float] = None) -> int:
    """Retire lessons nobody has used for a long time. Returns how many."""
    now = float(now or time.time())
    store = get_experience_store()
    retired = 0
    for lesson in store.lessons(statuses=("pending_review",)):
        if now - lesson.created_at > PENDING_EXPIRY_DAYS * 86400:
            before = Lesson.from_dict(lesson.to_dict())
            lesson.status, lesson.note = "quarantined", f"set aside: not reviewed within {PENDING_EXPIRY_DAYS} days, never used"
            store.update_lesson(lesson, actor="system", action="status:quarantined", reason=lesson.note, before=before)
            retired += 1
    for lesson in store.lessons(statuses=ACTIVE_STATUSES):
        last = max(lesson.last_used_at, lesson.created_at)
        days = STALE_PROVEN_DAYS if lesson.status == "established" else STALE_UNPROVEN_DAYS
        if now - last > days * 86400:
            before = Lesson.from_dict(lesson.to_dict())
            lesson.status, lesson.note = "retired", f"not used for {days} days"
            store.update_lesson(lesson, actor="system", action="status:retired", reason=lesson.note, before=before)
            retired += 1
    return retired
