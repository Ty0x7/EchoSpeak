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
    """Describe observed activity, keeping uncertain and completed sessions distinct."""
    from agent.companion.narrate import agent_name
    from agent.companion.text import owner_name

    now = float(snapshot.get("at") or time.time())
    rows = [r for r in snapshot["sessions"] if not agent or r["agent"] == agent]
    live = {"needs_permission": 0, "your_turn": 1, "operation": 2, "working": 3, "quiet": 4, "paused": 5}
    visible = [r for r in rows if r["state"] in live and
               (r.get("last_prompt") or r.get("turns") or r.get("attention") or r["state"] in {"working", "quiet", "paused"})
               and now - float(r.get("updated_at") or 0) < (8 * RECENT if agent or r["state"] == "quiet" else RECENT)]
    if not visible:
        return f"I can't confirm current activity for {agent_name(agent)}." if agent else "I don't have confirmed live activity to report."
    sentences = []
    who_seen = set()
    for row in sorted(visible, key=lambda r: (live[r["state"]], not r.get("interacting"), -float(r.get("updated_at") or 0)))[:4]:
        who = agent_name(row["agent"], {"agent_name": row.get("label")})
        label = f"Another {who} session" if who in who_seen else who
        who_seen.add(who)
        task = _task_of(row)
        where = f" on {task}" if task else (f" in {row['project']}" if row.get("project") else "")
        state = row["state"]
        if state == "quiet":
            line = f"{label} was working{where}. There hasn't been a recent update, so I'm unsure whether it's still running"
        elif state == "operation":
            line = f"{label} was last seen running {row.get('attention') or 'an operation'}{where}"
        elif state == "working" and row.get("interacting"):
            name = owner_name()
            line = f"{(name + ', y') if name else 'Y'}ou're working with {label}{where}, and it's still at it"
        else:
            action = {"working": "is working", "needs_permission": "needs your permission",
                      "your_turn": "is waiting for your reply", "paused": "was interrupted"}[state]
            line = f"{label} {action}{where}"
        sentences.append(line + ".")
    focus = snapshot.get("focus") or {}
    if not agent and focus.get("uncertain"):
        sentences.append("I'm not sure which of those conversations you're following right now.")
    return " ".join(sentences)


def _reports(store, since: float = 0.0, limit: int = 20, agent: str = "", *, project_id: str = "",
             exclude_agents: frozenset[str] = frozenset()) -> list[dict[str, Any]]:
    events = store.events(since=since, kinds=["turn_completed", "report_ready"], limit=200, agent=agent)
    out = []
    for event in events:
        if event["agent"] in exclude_agents:
            continue
        if project_id and knowledge._project_for(event.get("cwd", ""))[0] != project_id:
            continue
        digest = analyse(event.get("text", ""))
        if digest.kind == "report" or event["kind"] == "report_ready":
            digest.kind = "report"
            out.append({"event": event, "digest": digest})
            if len(out) >= limit:
                break
    return out


def report_answer(found: dict[str, Any], *, reword: bool = False) -> str:
    from agent.companion import narrate

    event, digest = found["event"], found["digest"]
    who = narrate.agent_name(event["agent"], event.get("meta"))
    where = f" on {event['project']}" if event.get("project") else ""
    headline = _brief(digest.headline, 26).rstrip(".!?")
    parts = [f"{who} reported back{where}: {headline}." if headline else f"{who} filed a report{where}."]
    details = (("It says", digest.results), ("It found", digest.discoveries),
               ("A problem it reported", digest.failures), ("Still open", digest.open or digest.proposals))
    for label, lines in details:
        if lines:
            detail = _brief(lines[0], 22).rstrip(".!?")
            if detail and not (detail.casefold() in headline.casefold() or headline.casefold() == detail.casefold()):
                parts.append(f"{label}: {detail}.")
    measured = (event.get("meta") or {}).get("measured")
    parts.append("EchoSpeak recorded passing checks." if measured in {"checks_pass", "verified"} else
                 "That's its own account. I haven't checked it independently.")
    draft = " ".join(parts)
    if reword:
        facts = narrate.facts(event, digest, interacting=False)
        facts.update(substantial=True, name="", question="", task="", permission_for="")
        return narrate.hybrid(facts, "the owner asked for this report", draft, timeout=4.0)[0]
    return draft


def latest_report_answer(store, agent: str = "", *, reword: bool = False, project_id: str = "") -> str:
    found = _reports(store, limit=1, agent=agent, project_id=project_id)
    return report_answer(found[0], reword=reword) if found else "I haven't seen a completed report here yet."


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
    events = store.events(since=since, kinds=["turn_completed", "report_ready"], limit=1000)
    if not events:
        return "I haven’t seen a completion in that period."
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


def answer(topic: str, store, snapshot: dict[str, Any], since: Optional[float] = None, *,
           reword: bool = False, project_id: str = "") -> str:
    if topic in {"activity", "other_agents"} or topic.startswith("agent:"):
        agent = topic.partition(":")[2]
        rows = snapshot["sessions"]
        if project_id:
            rows = [r for r in rows if knowledge._project_for(r.get("cwd", ""))[0] == project_id]
        if topic == "other_agents":
            rows = [r for r in rows if r["agent"] not in {"echo", "codex", "claude-code"}]
        summary = activity_answer({**snapshot, "sessions": rows, "focus": {} if project_id else snapshot.get("focus")}, agent)
        recent = _reports(store, since=float(snapshot.get("at") or time.time()) - 12 * 3600,
                          limit=1, agent=agent, project_id=project_id,
                          exclude_agents=frozenset({"echo", "codex", "claude-code"}) if topic == "other_agents" else frozenset())
        if recent:
            report = report_answer(recent[0], reword=reword and bool(agent))
            summary += f" {report}"
        return summary
    if topic == "latest_report" or topic.startswith("report:"):
        return latest_report_answer(store, topic.partition(":")[2], reword=reword, project_id=project_id)
    if topic == "discovery":
        if project_id:
            reports = _reports(store, limit=10, project_id=project_id)
            match = next((item for item in reports if item["digest"].discoveries), None)
            return report_answer(match) if match else "I haven't seen a recent discovery in this project."
        return discovery_answer(store)
    if topic == "recap":
        if project_id:
            reports = _reports(store, since=since if since is not None else time.time() - 8 * 3600,
                               limit=3, project_id=project_id)
            return " ".join(report_answer(item) for item in reports) or "I haven't seen a completed report in that period."
        return recap_answer(store, snapshot, since)
    raise ValueError("unknown topic")
