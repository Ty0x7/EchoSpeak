"""Explicitly saved project context; temporary web evidence is never personal memory."""
import json
from html import escape


def context_for_session(session_id: str) -> tuple[str, str]:
    from agent.state import get_state_store
    from agent.projects import get_project_manager
    project = get_project_manager().get_project(get_state_store().get_thread_state(session_id).active_project_id or "")
    if not project or project.archived:
        return "", ""
    metadata = dict(project.metadata or {})
    brief = str(metadata.get("brief") or "")[:4000]
    instructions = str(project.context_prompt or "")[:8000]
    trusted = "\n\n".join(x for x in (instructions, brief) if x.strip())
    records = list(metadata.get("research_findings") or [])[-3:]
    # Escape closing tags so saved web text cannot escape its trust boundary.
    evidence = escape(json.dumps(records, ensure_ascii=False)[:18000], quote=False) if records else ""
    return trusted, evidence
