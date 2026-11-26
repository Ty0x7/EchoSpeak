"""Answers for "what's going on?" when the owner clicks Echo Companion. No model call.

Topics:
- ``activity``: everyone right now.
- ``agent:<id>``: one agent (claude-code, codex, echo).
- ``latest_report``: the most recent report.
- ``discovery``: the most recent finding.
- ``recap``: what finished since the owner was last active, or since a given time.

Every sentence comes from the observation store or the commentary feed, and reported
work is described as reported.
"""

from __future__ import annotations

import re
import time
from typing import Any, Optional

from agent.companion import knowledge
from agent.companion.digest import analyse

STATE_WORDS = {
    "working": "is working", "your_turn": "is waiting for you", "needs_permission": "needs your permission",
    "quiet": "has been quiet for a while", "idle": "is idle", "ended": "finished its session",
}


def _who(row: dict[str, Any]) -> str:
    return str(row.get("label") or knowledge.label(row["agent"])).replace("Claude Code", "Claude")


def clean(text: str) -> str:
    """Prompts as a person would say them: no paste/markup tags, no code fences, no URLs."""
    value = re.sub(r"```.*?(```|$)", " ", str(text or ""), flags=re.S)
    value = re.sub(r"</?[A-Za-z][\w:-]*(\s[^<>]{0,200})?/?>", " ", value)
    value = re.sub(r"https?://\S+", "a link", value)
    return re.sub(r"\s+", " ", value).strip()


def _brief(text: str, words: int = 10) -> str:
    parts = clean(text).split()
    return " ".join(parts[:words]) + ("…" if len(parts) > words else "")


def _describe(row: dict[str, Any]) -> str:
    where = f" on {row['project']}" if row.get("project") else ""
    line = f"{_who(row)} {STATE_WORDS.get(row['state'], row['state'])}{where}"
    if row["state"] == "working" and row.get("last_prompt"):
        line += f", on “{_brief(row['last_prompt'])}”"
    return line + "."


RECENT = 30 * 60.0


def _task_of(row: dict[str, Any]) -> str:
    from agent.companion.text import lower_first, task_title

    from agent.companion.narrate import gerund

    task = row.get("task") or task_title(row.get("last_prompt") or "") or ""
    return gerund(lower_first(clean(task)))


def activity_answer(snapshot: dict[str, Any], agent: str = "") -> str:
    """What the owner is doing and what else is moving, in a sentence or two. Old and resting
    sessions are left out; there is nothing to say about them."""
    from agent.companion.narrate import agent_name
    from agent.companion.text import owner_name

    now = float(snapshot.get("at") or time.time())
    rows = [r for r in snapshot["sessions"] if (not agent or r["agent"] == agent)
            and r["state"] in {"working", "your_turn", "needs_permission"}
            and now - float(r.get("updated_at") or 0) < RECENT
            and (r.get("last_prompt") or r.get("turns") or r["agent"] == "echo")]  # opened but never asked anything
    who = lambda r: agent_name(r["agent"], {"agent_name": r.get("label")})  # noqa: E731
    if not rows:
        if agent:
            return f"{agent_name(agent)} isn't doing anything right now."
        return "It's quiet. Nothing is running right now."
    focus = snapshot.get("focus") or {}
    mine = next((r for r in rows if r.get("interacting")), None)
    others = [r for r in rows if r is not mine]
    sentences = []
    name = owner_name()
    if mine:
        task = _task_of(mine)
        on = f" on {task}" if task else (f" in {mine['project']}" if mine.get("project") else "")
        state = {"working": "it's still at it", "your_turn": "it's waiting for your reply",
                 "needs_permission": "it needs your permission to continue"}[mine["state"]]
        sentences.append(f"{(name + ', y') if name else 'Y'}ou're working with {who(mine)}{on}, and {state}.")
    for row in sorted(others, key=lambda r: {"needs_permission": 0, "working": 1, "your_turn": 2}[r["state"]])[:2]:
        task = _task_of(row)
        label = f"Another {who(row)} session" if mine and row["agent"] == mine["agent"] else who(row)
        what = f" on {task}" if task else (f" in {row['project']}" if row.get("project") else "")
        doing = {"working": f"is working{what} in the background", "your_turn": f"is waiting for you{what}",
                 "needs_permission": f"needs your permission{what}"}[row["state"]]
        sentences.append(f"{label} {doing}.")
    if len(others) > 2:
        sentences.append(f"And {len(others) - 2} more.")
    if not agent and focus.get("uncertain"):
        names = " or ".join(agent_name(c["agent"], {"agent_name": c.get("label")}) for c in focus["candidates"][:2])
        sentences.append(f"I'm not sure whether you're with {names} right now.")
    return " ".join(sentences)


def _reports(store, since: float = 0.0, limit: int = 20) -> list[dict[str, Any]]:
    events = store.events(since=since, kinds=["turn_completed", "report_ready"], limit=200)
    out = []
    for event in events:
        digest = analyse(event.get("text", ""))
        if digest.kind == "report" or event["kind"] == "report_ready":
            out.append({"event": event, "digest": digest})
            if len(out) >= limit:
                break
    return out


def latest_report_answer(store) -> str:
    found = _reports(store, limit=1)
    if not found:
        return "No finished reports yet."
    event, digest = found[0]["event"], found[0]["digest"]
    when = time.strftime("%H:%M", time.localtime(float(event["at"])))
    where = f" on {event['project']}" if event.get("project") else ""
    parts = [f"{knowledge.label(event['agent']).replace('Claude Code', 'Claude')}'s report from {when}{where}: {_brief(digest.headline, 16)}."]
    if digest.results:
        parts.append(f"It says it got {len(digest.results)} thing{'s' if len(digest.results) != 1 else ''} done, including {_brief(digest.results[0], 12)}.")
    if digest.failures:
        parts.append(f"Didn't work: {_brief(digest.failures[0], 12)}.")
    if digest.open:
        parts.append(f"Still open: {_brief(digest.open[0], 12)}.")
    parts.append("That's its own account; I haven't checked it independently.")
    return " ".join(parts)


def discovery_answer(store) -> str:
    for found in _reports(store, limit=10):
        if found["digest"].discoveries:
            event = found["event"]
            return (f"{knowledge.label(event['agent']).replace('Claude Code', 'Claude')} found that "
                    f"{_brief(found['digest'].discoveries[0], 24)}.")
    return "Nothing new discovered in the recent reports."


def recap_answer(store, snapshot: dict[str, Any], since: Optional[float] = None) -> str:
    if since is None:
        prompts = store.events(kinds=["prompt"], limit=1)
        since = float(prompts[0]["at"]) if prompts else time.time() - 8 * 3600
    events = store.events(since=since, kinds=["turn_completed"], limit=1000)
    if not events:
        return "Nothing finished while you were away."
    per_agent: dict[str, int] = {}
    for event in events:
        per_agent[event["agent"]] = per_agent.get(event["agent"], 0) + 1
    parts = ["While you were away: " + ", ".join(
        f"{knowledge.label(a).replace('Claude Code', 'Claude')} finished {n} turn{'s' if n != 1 else ''}" for a, n in per_agent.items()) + "."]
    reports = _reports(store, since=since, limit=3)
    for found in reports:
        event, digest = found["event"], found["digest"]
        parts.append(f"{knowledge.label(event['agent']).replace('Claude Code', 'Claude')} reported {digest.outcome == 'success' and 'success' or 'mixed results'}"
                     f"{' on ' + event['project'] if event.get('project') else ''}: {_brief(digest.headline, 12)}.")
    waiting = [r for r in snapshot["sessions"] if r["state"] in {"needs_permission", "your_turn"} and r["agent"] != "echo"]
    if waiting:
        parts.append(f"{len(waiting)} session{'s are' if len(waiting) != 1 else ' is'} waiting for you.")
    return " ".join(parts)


def answer(topic: str, store, snapshot: dict[str, Any], since: Optional[float] = None) -> str:
    if topic == "activity":
        return activity_answer(snapshot)
    if topic.startswith("agent:"):
        return activity_answer(snapshot, topic.split(":", 1)[1])
    if topic == "latest_report":
        return latest_report_answer(store)
    if topic == "discovery":
        return discovery_answer(store)
    if topic == "recap":
        return recap_answer(store, snapshot, since)
    raise ValueError("unknown topic")
