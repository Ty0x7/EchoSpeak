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


def activity_answer(snapshot: dict[str, Any], agent: str = "") -> str:
    rows = [r for r in snapshot["sessions"] if r["state"] != "ended" and (not agent or r["agent"] == agent)]
    if not rows:
        name = knowledge.label(agent).replace("Claude Code", "Claude") if agent else "Nobody"
        return f"{name} isn't doing anything I can see right now." if agent else "It's quiet. No agent is active right now."
    order = {"needs_permission": 0, "working": 1, "your_turn": 2, "quiet": 3, "idle": 4}
    rows.sort(key=lambda r: (order.get(r["state"], 9), -float(r.get("updated_at") or 0)))
    active = [r for r in rows if r["state"] in {"needs_permission", "working", "your_turn"}]
    resting = [r for r in rows if r not in active]
    sentences = [_describe(r) for r in active[:4]]
    if len(active) > 4:
        sentences.append(f"And {len(active) - 4} more active.")
    if resting:  # idle and quiet sessions are summarised, never listed one by one
        sentences.append(f"{len(resting)} other session{'s are' if len(resting) != 1 else ' is'} resting." if active
                         else f"Nothing's active; {len(resting)} session{'s are' if len(resting) != 1 else ' is'} resting.")
    focus = snapshot.get("focus") or {}
    if not agent and focus.get("uncertain"):
        names = " or ".join(c["label"].replace("Claude Code", "Claude") for c in focus["candidates"][:2])
        sentences.append(f"I'm not sure whether you're working with {names}.")
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
