"""The reflector: after a task, the agent's own model writes down what to do differently.

Runs in the background (the server's learning worker), never during a chat,
at most ``learning_reflection_daily_cap`` times a day. It reads one graded
episode and, when there is one, a similar task by the same agent that went
the other way: contrasting a success with a failure is what makes
ReasoningBank's lessons transfer. The tool log is wrapped as untrusted data,
since tool output can carry injected text.

The model only proposes. agent/learning/curator.py decides what is kept.

Not every episode is worth a model call. Reflected: failures where something
actually ran, successes checked after the work (V2+), and anything the owner
gave feedback on. Skipped: plain answers, unchecked successes (learning from
"it said it worked" is how unverified skills made agents worse in the 2026
skills SoK), and requests with no tool calls.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Callable, Optional

from loguru import logger

from agent.lean.job import parse_review
from agent.lean.policy import wrap_untrusted
from agent.learning import curator
from agent.learning.store import Episode, get_experience_store

MAX_ATTEMPTS = 3
MAX_AGE_DAYS = 7
CONTRAST_DAYS = 90

ClientFactory = Callable[[Episode], Any]


def skip_reason(episode: Episode) -> str:
    """Why an episode isn't worth a reflection, or ''."""
    if episode.feedback:
        return ""
    ran = [t for t in episode.tools if not t.get("not_run")]
    if not ran:
        return "nothing ran"
    if episode.outcome == "answered":
        return "a plain answer"
    if episode.outcome == "success" and episode.level < 2:
        return "succeeded, but nothing checked it"
    return ""


def _tool_log(episode: Episode, limit: int = 25) -> str:
    lines = []
    for tool in episode.tools[-limit:]:
        if tool.get("not_run"):
            status = "not run"
        else:
            status = "ok" if tool.get("ok") else "FAILED"
        line = f"- {tool.get('label') or tool.get('name')} -> {status}"
        if tool.get("output") and not tool.get("ok"):
            line += f": {str(tool['output'])[:160]}"
        lines.append(line)
    if len(episode.tools) > limit:
        lines.insert(0, f"(…{len(episode.tools) - limit} earlier calls not shown)")
    return "\n".join(lines) or "(no tool calls)"


def _describe(episode: Episode, label: str) -> str:
    feedback = {1: "The owner said it worked.", -1: "The owner said it didn't work."}.get(episode.feedback, "")
    if feedback and episode.feedback_note:
        feedback += f' Their note: "{episode.feedback_note[:300]}"'
    return (
        f"{label}\nThe user asked: {episode.goal[:800]}\n"
        f"What ran (tool log):\n{wrap_untrusted('tool log', _tool_log(episode))}\n"
        f"Result: {episode.outcome} ({episode.summary[:300] or 'no summary'}). "
        f"How well it was checked: V{episode.level}, {'; '.join(episode.reasons)[:300]}.\n"
        + (feedback + "\n" if feedback else "")
    )


def find_contrast(episode: Episode) -> Optional[Episode]:
    """The most recent similar task by the same agent that went the other way (trusted only)."""
    won = episode.verified_success
    for other in get_experience_store().episodes(agent_id=episode.agent_id, task_kind=episode.task_kind,
                                                 since=time.time() - CONTRAST_DAYS * 86400, limit=60):
        if other.id == episode.id or not other.trusted or skip_reason(other):
            continue
        if other.verified_success != won and (other.verified_success or other.failed):
            return other
    return None


def prompt_for(episode: Episode, contrast: Optional[Episode]) -> str:
    parts = [
        "You are reviewing your own past work to write down a strategy that will help next time. "
        "Be honest about what went wrong as well as what worked.",
        _describe(episode, "THIS TASK"),
    ]
    if contrast is not None:
        parts.append(_describe(contrast, "A SIMILAR TASK THAT WENT THE OTHER WAY"))
        parts.append("Compare the two: what made the difference?")
    parts.append(
        "Write at most 2 lessons that would make the next similar task go better. Each lesson:\n"
        "- is a general strategy (\"when X, do Y\" or \"avoid Z because ...\"), not a story about this task;\n"
        "- names no files, links, people, numbers or other details from this task;\n"
        "- is never about permissions, approvals, safety rules, secrets, or changing tests or checks;\n"
        "- has a title under 80 characters and text under 300 characters.\n"
        "If there is nothing general to learn, return no lessons.\n"
        'Answer with JSON only: {"lessons": [{"title": "...", "text": "...", "kind": "do" or "avoid"}]}'
    )
    return "\n\n".join(parts)


def _default_client(episode: Episode) -> Any:
    from agent.lean.provider import ChatClient, reasoning_effort_for, resolve_endpoint

    endpoint = resolve_endpoint(episode.provider, episode.model)
    return ChatClient(endpoint, reasoning_effort=reasoning_effort_for(endpoint, False, "low"))


def reflect(episode: Episode, client: Any) -> dict[str, Any]:
    """One reflection: the model proposes, the curator decides. Returns the curator's result."""
    contrast = find_contrast(episode)
    turn = client.stream_turn([{"role": "user", "content": prompt_for(episode, contrast)}],
                              temperature=0.2, max_tokens=700)
    data = parse_review(str(getattr(turn, "content", "") or ""))
    proposals = data.get("lessons") if isinstance(data, dict) else None
    if not isinstance(proposals, list):
        return {"created": [], "merged": [], "refused": ["no JSON lessons in the reply"], "contrast": bool(contrast)}
    result = curator.admit(episode, proposals)
    return {**result, "contrast": bool(contrast)}


def _start_of_today() -> float:
    now = datetime.now()
    return now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def run_pending(*, limit: int = 3, cap: Optional[int] = None,
                client_factory: Optional[ClientFactory] = None) -> list[dict[str, Any]]:
    """Reflect on up to ``limit`` queued episodes, within today's cap."""
    from agent.lean import settings

    if settings.learning_mode() != "on":
        return []
    store = get_experience_store()
    cap = settings.reflection_daily_cap() if cap is None else cap
    factory = client_factory or _default_client
    paused = store.paused_agents()
    done: list[dict[str, Any]] = []
    for row in store.pending_reflections(limit=limit * 3):
        if len(done) >= limit:
            break
        episode = store.get_episode(row["episode_id"])
        if episode is None:
            store.finish_reflection(row["episode_id"], "skipped", "episode no longer exists")
            continue
        if episode.agent_id in paused:
            store.finish_reflection(episode.id, "skipped", "learning is paused for this agent")
            continue
        if time.time() - float(row["created_at"]) > MAX_AGE_DAYS * 86400 or int(row["attempts"]) >= MAX_ATTEMPTS:
            store.finish_reflection(episode.id, "skipped", "too old or failed too often")
            continue
        reason = skip_reason(episode)
        if reason:
            store.finish_reflection(episode.id, "skipped", reason)
            continue
        # Only model calls count toward the cap; the checks above are free.
        if store.reflections_since(_start_of_today()) >= cap:
            break
        client = None
        try:
            client = factory(episode)
            result = reflect(episode, client)
        except Exception as exc:
            # Usually the model server is off: try again later instead of losing the episode.
            logger.info("Reflection on {} failed: {}", episode.id, exc)
            store.finish_reflection(episode.id, "pending" if int(row["attempts"]) + 1 < MAX_ATTEMPTS else "failed",
                                    f"{type(exc).__name__}: {str(exc)[:200]}")
            break
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
        note = (f"created {len(result['created'])}, merged {len(result['merged'])}, refused {len(result['refused'])}"
                + (f" ({'; '.join(result['refused'][:3])})" if result["refused"] else ""))
        store.finish_reflection(episode.id, "done", note)
        done.append({"episode_id": episode.id, "agent_id": episode.agent_id, **result})
    return done
