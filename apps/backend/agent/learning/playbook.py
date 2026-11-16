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
# Transferable lessons are held back this often, so transfer has its own control group.
HOLDOUT_SHARED = 0.2
# Prompt budget for lessons, in characters: small local models get less (ACE's "not every task needs rich context").
FULL_CHARS = 900
COMPACT_CHARS = 420
# Requests shorter than this (in content words) get no lessons.
MIN_GOAL_WORDS = 3
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


# Meaning-based matching (agent/semantic.py, when the local embedding model is installed): a lesson this
# close in meaning matches even without shared words; keyword matching alone otherwise.
SEMANTIC_MATCH = 0.6


def _similarities(goal: str, lessons: list[Lesson]) -> dict[str, float]:
    if not lessons:
        return {}
    try:
        from agent import semantic

        found = semantic.similarities(goal, [f"{l.title}: {l.text}" for l in lessons])
    except Exception:
        found = None
    return {l.id: float(v) for l, v in zip(lessons, found)} if found else {}


def _score(lesson: Lesson, words: set[str], kind: str, similarity: float = 0.0) -> float:
    overlap = len(words & set(keywords(f"{lesson.title} {lesson.text}", limit=40)))
    kind_match = bool(lesson.task_kind and lesson.task_kind == kind)
    # A loosely related lesson costs more than it helps: negative transfer hits hard tasks
    # hardest (arXiv 2604.27003). Same kind of task and a shared word, or two shared words,
    # or (with the embedding model) a close match in meaning.
    if overlap < (1 if kind_match else 2) and similarity < SEMANTIC_MATCH:
        return 0.0
    overlap += 3 * max(0.0, similarity - 0.4)
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
    pool = [l for l in get_experience_store().lessons(agent_id=agent_id, statuses=ACTIVE_STATUSES) if _in_scope(l, project_id)]
    sims = _similarities(goal, pool)
    ranked = sorted(
        ((score, lesson) for lesson in pool if (score := _score(lesson, words, kind, sims.get(lesson.id, 0.0))) > 0),
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


def holdout(lessons: list[Lesson], rng: Optional[random.Random] = None,
            rate: Optional[float] = None) -> tuple[list[Lesson], list[Lesson]]:
    """(lessons to show, lessons held back this time as the control group). ``rate`` overrides the usual one."""
    rng = rng or random
    used: list[Lesson] = []
    withheld: list[Lesson] = []
    fixed = rate
    for lesson in lessons:
        rate = (fixed if fixed is not None else 0.0 if lesson.origin == "owner"
                else HOLDOUT_PROVEN if lesson.status == "established" else HOLDOUT_UNPROVEN)
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
    if first_time:
        # Only lessons for the kind of work this turned out to be are credited or blamed (applies()).
        unrelated = [i for i, _ in counted if (l := store.get_lesson(i)) is not None and not applies(l, episode)]
        counted = [(i, held) for i, held in counted if i not in unrelated]
        if unrelated:
            episode.lessons_used = [i for i in episode.lessons_used if i not in unrelated]
            episode.lessons_withheld = [i for i in episode.lessons_withheld if i not in unrelated]
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
    """Count another agent's use of a proven lesson apart from its home record, against its own control group.

    Transferable lessons are held back at random too (HOLDOUT_SHARED); those tasks are the comparison.
    Transfer stops when tasks in the other agent's work go clearly better without it, or, before there's
    enough held-back data, when it keeps failing there.
    """
    rows = [(i, False) for i in episode.lessons_shared] + [(i, True) for i in episode.lessons_shared_withheld
                                                              if i not in episode.lessons_shared]
    if not rows or (not first_time and old == new):
        return
    store = get_experience_store()
    for lesson_id, held in rows:
        lesson = store.get_lesson(lesson_id)
        if lesson is None or lesson.agent_id == episode.agent_id or not applies(lesson, episode):
            continue
        before = Lesson.from_dict(lesson.to_dict())
        wins, losses = ("shared_held_wins", "shared_held_losses") if held else ("shared_wins", "shared_losses")
        if first_time and not held:
            lesson.shared_uses += 1
        if old == "win":
            setattr(lesson, wins, max(0, getattr(lesson, wins) - 1))
        elif old == "loss":
            setattr(lesson, losses, max(0, getattr(lesson, losses) - 1))
        if new == "win":
            setattr(lesson, wins, getattr(lesson, wins) + 1)
        elif new == "loss":
            setattr(lesson, losses, getattr(lesson, losses) + 1)
        reason = transfer_harm(lesson)
        if reason and lesson.transfer:
            lesson.transfer = False
            store.update_lesson(lesson, actor="system", action="transfer:stopped",
                                reason=f"{episode.id}: {reason}: no longer shared", before=before)
        else:
            store.update_lesson(lesson, actor="system", action=f"{'shared-control' if held else 'shared'}:{new}",
                                reason=f"{episode.id}: {'held back from' if held else 'used by'} "
                                       f"{episode.agent_name or episode.agent_id}", before=before, quiet=True)


def transfer_harm(lesson: Lesson) -> str:
    """Why transfer should stop, or '': worse than its control group, or failing before there's one."""
    decided = lesson.shared_wins + lesson.shared_losses
    held = lesson.shared_held_wins + lesson.shared_held_losses
    rate = lesson.shared_wins / decided if decided else 0.0
    if held >= MIN_HELD and decided >= TRANSFER_STOP_LOSSES:
        held_rate = lesson.shared_held_wins / held
        if rate + HARM_MARGIN < held_rate:
            return (f"other agents did better without it ({lesson.shared_wins} of {decided} with it, "
                    f"{lesson.shared_held_wins} of {held} without)")
        return ""
    if lesson.shared_losses >= TRANSFER_STOP_LOSSES and rate < RETIRE_RATE:
        return f"didn't help other agents ({lesson.shared_wins} of {decided} checked tasks)"
    return ""


def applies(lesson: Lesson, episode: Episode) -> bool:
    """Whether the episode's result says anything about the lesson: the same kind of task.

    A lesson read before a request that turned out to be a different kind of work (a coding lesson on a
    question that became research) neither helped nor hurt it, so it isn't credited or blamed.
    """
    return not lesson.task_kind or not episode.task_kind or lesson.task_kind == episode.task_kind


# ── keeping prompts lean, and the playbook tidy ─────────────────────────

def worth_lessons(goal: str) -> bool:
    """Small talk and one-line requests get no lessons: they would only cost context."""
    return len(keywords(goal or "", limit=24)) >= MIN_GOAL_WORDS


def cost(lesson: Lesson) -> int:
    return len(lesson.title) + len(lesson.text) + 16


def fit(lessons: list[Lesson], budget: int) -> list[Lesson]:
    """The lessons, in order, that fit the prompt budget (characters)."""
    kept, used = [], 0
    for lesson in lessons:
        if used + cost(lesson) > budget:
            continue
        kept.append(lesson)
        used += cost(lesson)
    return kept


def consolidate(agent_id: str = "") -> int:
    """Merge active lessons that say the same thing (deterministic; no model call). Returns how many merged.

    The curator already merges a new proposal into its twin; this catches twins that were admitted apart
    (different wording at different times). The better-proven one keeps the evidence of both.
    """
    from agent.learning.curator import same_lesson

    store = get_experience_store()
    merged = 0
    lessons = sorted(store.lessons(agent_id=agent_id, statuses=ACTIVE_STATUSES),
                     key=lambda l: (l.status != "established", -(l.wins - l.losses), l.created_at))
    kept: list[Lesson] = []
    for lesson in lessons:
        twin = next((k for k in kept if k.agent_id == lesson.agent_id and k.project_id == lesson.project_id
                     and same_lesson(f"{k.title} {k.text}", f"{lesson.title} {lesson.text}")), None)
        if twin is None:
            kept.append(lesson)
            continue
        before_twin, before = Lesson.from_dict(twin.to_dict()), Lesson.from_dict(lesson.to_dict())
        twin.source_episodes = list(dict.fromkeys(twin.source_episodes + lesson.source_episodes))[-20:]
        twin.level = max(twin.level, lesson.level)
        store.update_lesson(twin, actor="system", action="consolidated", reason=f"merged {lesson.id}, which said the same",
                            before=before_twin)
        lesson.status, lesson.note = "retired", f"merged into {twin.id}, which says the same"
        store.update_lesson(lesson, actor="system", action="status:retired", reason=lesson.note, before=before)
        merged += 1
    return merged


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
