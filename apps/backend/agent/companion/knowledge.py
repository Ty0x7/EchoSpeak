"""Carry what other agents report into EchoSpeak's memory, with provenance.

A finished report from Claude Code, Codex or one of Echo's agents is:

1. **Archived verbatim** as Markdown with front matter in
   ``DATA_DIR/companion/reports/<date>/``. The front matter records the agent, session,
   project, folder, time, the agent's transcript reference and the event id.
2. **Condensed** into one canonical memory item through ``AgentMemory.add_memory_item``,
   so Echo's normal recall, project isolation, sensitive-text filter and Obsidian
   projection all apply.

The memory text always names the agent and says the work was reported and not
independently verified. Instruction-like lines are already gone (see ``digest``), and
the memory type is ``project``, never ``instruction``. Nothing here changes Echo's SOUL,
permissions or learning rewards. The same report never produces a second memory:
``source_item_id`` is the event id and ``semantic_key`` is the agent turn.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Optional

from config import DATA_DIR, config

from agent.companion.digest import Digest
from agent.companion.observe import AGENT_LABELS


def label(agent: str) -> str:
    return AGENT_LABELS.get(agent, agent.replace("-", " ").title())


def _short(text: str, words: int = 18) -> str:
    parts = str(text or "").split()
    return " ".join(parts[:words]) + ("…" if len(parts) > words else "")


def reports_dir() -> Path:
    return Path(DATA_DIR) / "companion" / "reports"


def archive(event: dict[str, Any], digest: Digest) -> Path:
    when = time.localtime(float(event["at"]))
    folder = reports_dir() / time.strftime("%Y-%m-%d", when)
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{time.strftime('%H%M%S', when)}-{event['agent']}-{event['session_id'][:8]}-{event['id'][:8]}.md"
    path = folder / re.sub(r"[^A-Za-z0-9._-]", "_", name)
    front = {
        "agent": event["agent"], "agent_label": label(event["agent"]), "session_id": event["session_id"],
        "turn_id": event.get("turn_id", ""), "project": event.get("project", ""), "cwd": event.get("cwd", ""),
        "at": time.strftime("%Y-%m-%dT%H:%M:%S%z", when), "event_id": event["id"],
        "transcript_path": event.get("transcript_path", ""), "kind": digest.kind, "outcome": digest.outcome,
        "evidence": "reported by the agent; not independently verified by EchoSpeak",
        "prs": digest.prs, "issues": digest.issues, "commits": digest.commits,
    }
    body = "---\n" + "\n".join(f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in front.items()) + "\n---\n\n"
    body += "> Untrusted reference material: the agent's own words, kept verbatim.\n\n" + str(event.get("text") or "")
    temporary = path.with_suffix(".tmp")
    temporary.write_text(body + "\n", encoding="utf-8")
    os.replace(temporary, path)
    return path


def memory_text(event: dict[str, Any], digest: Digest) -> str:
    who = label(event["agent"])
    day = time.strftime("%Y-%m-%d %H:%M", time.localtime(float(event["at"])))
    where = f" in project {event['project']}" if event.get("project") else ""
    parts = [f"{who} reported on {day}{where} (session {event['session_id'][:8]}): {_short(digest.headline, 30)}"]
    for title, items in (("Done", digest.results), ("Found", digest.discoveries), ("Decided", digest.decisions),
                         ("Failed", digest.failures), ("Still open", digest.open), ("Proposed", digest.proposals)):
        if items:
            parts.append(f"{title}: " + "; ".join(_short(item, 16) for item in items[:4]))
    refs = [f"PR #{n}" for n in digest.prs] + [f"issue #{n}" for n in digest.issues[:4]]
    if refs:
        parts.append("Refs: " + ", ".join(refs))
    if digest.tests_passed or digest.tests_failed:
        parts.append(f"Tests reported: {digest.tests_passed} passed, {digest.tests_failed} failed")
    parts.append(f"Status: reported by {who}, not independently verified by EchoSpeak.")
    return ". ".join(p.rstrip(". ") for p in parts)[:1800] + "."


def _project_for(cwd: str) -> tuple[str, str]:
    """(project_id, workspace_root) of the EchoSpeak project containing this folder, if any."""

    if not cwd:
        return "", ""
    try:
        from agent.projects import get_project_manager

        here = os.path.realpath(cwd)
        best = ("", "")
        for project in get_project_manager().list_projects():
            root = str(getattr(project, "workspace_root", "") or "")
            if not root or getattr(project, "archived", False):
                continue
            real = os.path.realpath(os.path.expanduser(root))
            if (here == real or here.startswith(real.rstrip(os.sep) + os.sep)) and len(real) > len(best[1]):
                best = (str(project.id), real)
        return best
    except Exception:
        return "", ""


def remember(event: dict[str, Any], digest: Digest, *, scope_mode: Optional[str] = None,
             memory: Any = None) -> dict[str, Any]:
    """Store one condensed memory. Returns {"stored": bool, "id", "scope", "reason"}."""

    mode = str(scope_mode or getattr(config, "companion_memory_scope", "auto") or "auto")
    if mode == "off":
        return {"stored": False, "reason": "knowledge transfer is off"}
    project_id, root = _project_for(event.get("cwd", ""))
    if mode == "project_only" and not project_id:
        return {"stored": False, "reason": "folder is not an EchoSpeak project"}
    scope = "project" if project_id and mode in {"auto", "project_only"} else "account"
    if memory is None:
        from agent.memory import get_agent_memory

        memory = get_agent_memory()
    item_id = memory.add_memory_item(
        memory_text(event, digest), memory_type="project", source="echo-companion",
        project_path=root or None, project_id=project_id, scope=scope,
        source_item_id=event["id"], semantic_key=f"companion:{event['agent']}:{event['session_id']}:{event.get('turn_id') or event['id']}",
    )
    return {"stored": bool(item_id), "id": item_id or "", "scope": scope, "project_id": project_id,
            "reason": "" if item_id else "memory declined the item (duplicate or sensitive)"}
