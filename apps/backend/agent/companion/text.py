"""Turning what agents and the owner wrote into something worth saying out loud.

- ``clean``: removes markup, paste wrappers, code fences and URLs.
- ``is_system_prompt``: spots harness traffic that arrives through the prompt hook, such as
  background-task notifications or system reminders, so it is never mistaken for the owner's request.
- ``task_title``: the owner's request, as a short phrase. Follow-ups like "continue" don't replace it.
- ``owner_name``: only what EchoSpeak has learned (the display-name setting or a profile memory
  such as "User's name is Ty"). Never a default and never hard-coded. Empty when unknown.
"""

from __future__ import annotations

import re
import threading
import time
from typing import Optional

_SYSTEM = re.compile(r"^\s*(<\s*(task-notification|system-reminder|command-name|local-command-stdout|bash-input|bash-stdout)\b"
                     r"|\[SYSTEM NOTIFICATION|<\s*user-prompt-submit-hook)", re.I)
# Short conversational replies ("you're good, no need for that") answer the agent; they aren't a new task.
_REPLY = re.compile(r"^\s*(you'?re|you are|you|it'?s|that'?s|this is|no|nope|nah|yes|yeah|yep|sure|ok(ay)?|fine|"
                    r"nice|good|great|cool|thanks|thank you|lol|haha|hmm|i think|i guess|maybe|perhaps|all good|never ?mind)\b",
                    re.I)
_FOLLOW_UP = re.compile(r"^\s*(continue|go on|go ahead|yes|yep|ok(ay)?|sure|proceed|do it|keep going|next|thanks?|"
                        r"thank you|great|perfect|sounds good|looks good|lgtm|y|n|no)\b[\s.!]*$", re.I)


_PRAISE = re.compile(r"^(excellent|great|nice|good|awesome|perfect|amazing|thanks|thank you|wow|cool|well done|love it)"
                     r"\b[^.!?]{0,30}[.!?]?$", re.I)
_LEAD_IN = re.compile(r"^(please\s+|can you\s+|could you\s+|would you\s+|i want you to\s+|i'?d like you to\s+|"
                      r"i approve (proceeding with\s+)?|go ahead and\s+|let'?s\s+|now\s+|also\s+|ok(ay)?,?\s+)+", re.I)


def clean(text: str) -> str:
    value = re.sub(r"```.*?(```|$)", " ", str(text or ""), flags=re.S)
    value = re.sub(r"</?[A-Za-z][\w:-]*(\s[^<>]{0,200})?/?>", " ", value)
    value = re.sub(r"https?://\S+", "a link", value)
    value = re.sub(r"[`*_#>|]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def is_system_prompt(prompt: str) -> bool:
    return bool(_SYSTEM.match(str(prompt or "")))


def task_title(prompt: str, words: int = 14) -> str:
    """A short phrase for what the owner asked, or "" when it isn't a new task."""
    raw = str(prompt or "")
    if is_system_prompt(raw):
        return ""
    body = re.sub(r"</?pasted_content[^>]*>", "\n", raw)
    heading = re.search(r"^\s*#{1,3}\s+(.+)$", body, re.M)
    text = clean(heading.group(1)) if heading else ""
    if not text:
        cleaned = clean(body)
        if not cleaned or _FOLLOW_UP.match(cleaned) or len(cleaned.split()) < 3:
            return ""
        if _REPLY.match(cleaned) and len(cleaned.split()) < 10:
            return ""
        for sentence in re.split(r"(?<=[.!?])\s+", cleaned):
            core = _LEAD_IN.sub("", sentence).strip()
            if len(core.split()) >= 3 and not _PRAISE.match(core):
                text = core
                break
        if not text:
            return ""
    parts = text.split()
    return " ".join(parts[:words]).rstrip(",;:.") + ("…" if len(parts) > words else "")


_name: dict = {"at": 0.0, "value": ""}
_name_lock = threading.Lock()


def owner_name(ttl: float = 300.0) -> str:
    """The owner's preferred name if EchoSpeak has learned it; "" otherwise."""
    with _name_lock:
        if time.time() - _name["at"] < ttl:
            return _name["value"]
        value = ""
        try:
            from config import config

            value = str(getattr(config, "user_display_name", "") or "").strip()
            if not value:
                from agent.memory import get_agent_memory

                for item in get_agent_memory().personal_memory_summary():
                    match = re.search(r"\b(?:user'?s|my|owner'?s)\s+(?:preferred\s+)?name\s+is\s+([A-Z][\w'-]{0,30})",
                                      item.get("text", ""), re.I)
                    if match and item.get("type") in {"profile", "preference"}:
                        value = match.group(1)
                        break
        except Exception:
            value = ""
        _name.update(at=time.time(), value=value[:40])
        return _name["value"]


def first_sentence(text: str, words: int = 22) -> str:
    cleaned = clean(text)
    sentence = re.split(r"(?<=[.!?])\s", cleaned, maxsplit=1)[0] if cleaned else ""
    parts = sentence.split()
    return " ".join(parts[:words]).rstrip(",;:") + ("…" if len(parts) > words else "")


_PROPER = {"Claude", "Codex", "Echo", "EchoSpeak", "Glados", "Jarvis", "Kokoro", "Piper", "Hyprland", "Omarchy",
           "Obsidian", "GitHub", "Ollama", "Python", "Linux", "I"}


def lower_first(text: str) -> str:
    """Lower-case a sentence's first letter to continue a sentence, but never a name or an acronym."""
    first = text.split(" ", 1)[0].rstrip(",:;.") if text else ""
    if not text or first in _PROPER or first[:2].isupper() or any(c.isupper() for c in first[1:]):
        return text
    return text[:1].lower() + text[1:]


def project_label(cwd: str) -> Optional[str]:
    """The EchoSpeak project's name for this folder, if it belongs to one."""
    try:
        import os

        from agent.projects import get_project_manager

        here, best = os.path.realpath(cwd), ("", "")
        for project in get_project_manager().list_projects():
            root = str(getattr(project, "workspace_root", "") or "")
            if root and not getattr(project, "archived", False):
                real = os.path.realpath(os.path.expanduser(root))
                if (here == real or here.startswith(real.rstrip(os.sep) + os.sep)) and len(real) > len(best[1]):
                    best = (str(getattr(project, "name", "") or ""), real)
        return best[0] or None
    except Exception:
        return None


def spoken(text: str) -> str:
    """Normalize display and speech together, without changing the stored source."""
    from agent.companion.hook_client import mask

    value = mask(str(text or ""), 4800)
    value = re.sub(r"```.*?(?:```|$)", " ", value, flags=re.S)
    value = re.sub(r"\[([^\]\n]+)\]\(https?://[^)]+\)", r"\1", value)
    value = re.sub(r"https?://[^\s)>]+", "the link", value)
    value = re.sub(r"(?<!\w)(?:(?:~/|/|[A-Za-z]:\\)[\w./\\~+@-]*[\w/]|[\w.-]+/[\w./-]+\.[A-Za-z0-9]+)", "the file", value)
    value = re.sub(r"(?m)^ *#{1,6} +[^\n]*", "", value)
    value = re.sub(r"(?m)^ *(?:[-*•]|\d+[.)]|>) *", "", value)
    value = re.sub(r"\bPRs?\s*#?(?=\d)", "pull request ", value)
    value = re.sub(r"\b(?:TUI|CLI)\b", "terminal interface", value)
    value = re.sub(r"(?i)\b(commit|revision|sha)\s+`?[a-f0-9]{7,40}\b`?", "the recorded revision", value)
    value = re.sub(r"(?<!\w)(?=[a-f0-9]{12,}\b)(?=[a-f0-9]*[0-9])[a-f0-9]+", "the recorded ID", value, flags=re.I)
    value = re.sub(r"`([A-Za-z_][\w.:-]{14,})`", "the named item", value)
    value = re.sub(r"([\w])_+(?=\w)", r"\1 ", value)
    value = re.sub(r"[\[\]()]|[`*_#>|]", " ", value)
    value = re.sub(r"(?:;|:(?!\d))\s*", ". ", value)
    value = re.sub(r"(?<=[\w])\n+(?=\s*[\w])", ". ", value)
    value = re.sub(r"\s+([,.!?])", r"\1", " ".join(value.split()))
    value = re.sub(r"\.{2,}", ".", value).replace("at the file", "in the file").strip(" ,.")
    if not value:
        return ""
    sentences = re.split(r"(?<=[.!?])\s+", value)
    result = " ".join(part[:1].upper() + part[1:] for part in sentences if part)
    return result if result[-1] in ".!?" else result + "."
