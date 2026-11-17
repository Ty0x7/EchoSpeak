"""Was the thing the user asked to be created actually created?

A run that ends is not a deliverable that exists. In the 16 November 2026 conversation audit the
owner asked Glados for "a single file … make it in /projects". File writing was switched off, so
no write tool ran; Glados asked three questions, the owner settled for copying the code, and the
work record still read *completed* against the original objective ("make it in /projects").

This module answers three separate questions from evidence, never from the reply's own words:

- **requested**: did the request ask for a file or artifact to be created somewhere?
- **created**: did a tool that writes files or artifacts succeed in this run?
- **fallback**: did the owner explicitly accept something else instead (an answered question card)?

The work record then says which of three things happened: delivered, a fallback the owner accepted,
or not delivered. Nothing here changes what a tool may do.
"""
from __future__ import annotations

import re
from typing import Any, Optional

# Tools whose success means a file now exists on disk.
FILE_TOOLS = frozenset({"file_write", "file_edit", "file_copy", "file_move", "save_project_image", "notepad_write", "self_edit"})
# Tools whose success means something exists in EchoSpeak itself (the artifact panel, the media library).
PANEL_TOOLS = frozenset({"create_artifact", "update_artifact", "create_media"})
WRITE_TOOLS = FILE_TOOLS | PANEL_TOOLS
# A command can create a file too (a redirect, a generator, a build).
COMMAND_TOOLS = frozenset({"terminal", "terminal_run", "process_start"})

_CREATE = re.compile(r"(?i)\b(make|create|build|write|save|generate|put|export|produce|draft|add)\b")
_FILE_NAME = re.compile(r"(?<![\w@])[\w.-]+\.(?:md|txt|py|js|ts|tsx|jsx|json|html|css|csv|yaml|yml|toml|xml|ini|"
                        r"ps1|bat|sh|java|c|cpp|cs|go|rs|rb|php|sql|docx|xlsx|pdf|svg|png)\b", re.I)
_TARGET = re.compile(
    r"(?i)\b(?:single|one|a|an|new|the)\s+(?:\w+\s+){0,2}file\b"
    r"|\b(?:in|into|to|under|inside|at)\s+(?:~|\.{0,2}/)[\w./~-]*"
    r"|\b(?:in|into|to|under)\s+(?:my|the|this)\s+[\w -]{1,30}?(?:folder|directory|project|repo)\b"
    r"|\bsave\s+(?:it|this|that|them)\b"
    r"|\bas\s+an?\s+(?:artifact|document|file)\b")
# "How do I create a file in /tmp?" asks for an explanation, not for the file.
_EXPLAIN = re.compile(r"(?i)^\s*(how|what|why|when|where|which|explain|can you explain|is it|does|do you know)\b")


# Wording that places the result on disk: a folder, a path, a file name or "a single file".
_ON_DISK = re.compile(r"(?i)\bfile\b|(?:~|\.{0,2}/)[\w./~-]+|\b(?:folder|directory|project|repo)\b")


def requested(objective: str) -> str:
    """The words that ask for a file or artifact to be created, or "" when the request doesn't."""
    text = " ".join(str(objective or "").split())
    if not text or _EXPLAIN.match(text) or not _CREATE.search(text):
        return ""
    hit = _FILE_NAME.search(text) or _TARGET.search(text)
    return hit.group(0).strip() if hit else ""


def _tools(timelines: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    return [item for timeline in timelines for item in timeline or [] if item.get("kind") == "tool"]


def wants_file(objective: str) -> bool:
    """The request puts the result on disk (in a folder, as a named or single file), not just anywhere."""
    asked = requested(objective)
    return bool(asked) and bool(_FILE_NAME.search(asked) or _ON_DISK.search(asked))


def created(timelines: list[list[dict[str, Any]]]) -> list[str]:
    """Names of write tools (or commands) that succeeded in this run."""
    done = []
    for item in _tools(timelines):
        name = str(item.get("name") or "")
        ok = item.get("status") == "done" and str(item.get("outcome") or "ok") == "ok"
        if ok and (name in WRITE_TOOLS or name in COMMAND_TOOLS):
            done.append(name)
    return done


def accepted_fallback(timelines: list[list[dict[str, Any]]]) -> str:
    """The owner's last answer to a question card in this run: what they chose instead, or ""."""
    answer = ""
    for timeline in timelines:
        for item in timeline or []:
            if item.get("kind") == "approval" and item.get("tool") == "ask_user" and item.get("decision") == "answered":
                answer = " ".join(str(item.get("answer") or "").split()) or answer
    return answer[:200]


def assess(objective: str, timelines: list[list[dict[str, Any]]]) -> Optional[dict[str, Any]]:
    """None when nothing was asked to be created; otherwise what became of it.

    ``status``: "delivered" (a write succeeded), "elsewhere" (a file was asked for and only an artifact was
    made), "fallback" (nothing was written and the owner chose something else) or "not_created" (nothing was
    written and nothing else was agreed).
    """
    asked = requested(objective)
    if not asked:
        return None
    writes = created(timelines)
    on_disk = [name for name in writes if name not in PANEL_TOOLS]
    if writes and (on_disk or not wants_file(objective)):
        return {"requested": asked, "status": "delivered", "created_by": sorted(set(writes)), "fallback": "", "note": ""}
    if writes:
        # Asked for a file in a folder; made an artifact in EchoSpeak's panel instead (Glados, live re-test of the
        # audit's Pong request, 17 November 2026). It exists, but not where it was asked for.
        return {"requested": asked, "status": "elsewhere", "created_by": sorted(set(writes)), "fallback": "",
                "note": (f"Not saved where you asked (“{asked}”): it was made as an artifact in EchoSpeak's side panel, "
                         "not as a file on disk. Download it from the panel, or allow file creation and ask again.")}
    fallback = accepted_fallback(timelines)
    if fallback:
        return {"requested": asked, "status": "fallback", "created_by": [], "fallback": fallback,
                "note": (f"Not created: no file or artifact was written in this run. You chose instead: “{fallback}”. "
                         "The original request is not done.")}
    return {"requested": asked, "status": "not_created", "created_by": [], "fallback": "",
            "note": "Not created: no file or artifact was written in this run, so what you asked for does not exist yet."}


# How the work record's verification field reads for each outcome (shown by both interfaces).
VERIFICATION = {"fallback": "fallback accepted", "not_created": "not delivered", "elsewhere": "not where asked"}


def work_status(assessment: Optional[dict[str, Any]], status: str) -> str:
    """The work record's status: a run that created nothing it was asked to create did not complete it."""
    if assessment and status == "completed" and assessment["status"] == "not_created":
        return "failed"
    return status
