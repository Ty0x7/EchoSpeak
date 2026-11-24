"""Keep a removal within what the user asked to remove.

An ambiguous request ("delete the notes file" with notes-draft.md and notes-final.md present) can be resolved by
deleting every candidate. The approval card shows the command, but nothing compared its targets with the request,
and autonomous mode has no card at all (qwen3.5:9b ran `rm notes-draft.md notes-final.md`, 24 November 2026).

The rule: in one request, at most one pre-existing file or folder the user didn't name may be removed, unless the
request asks for several (a quantifier, a number, a plural "files"/"folders", or a wildcard). A further removal
isn't run; the agent is told to ask the user which ones they meant. A single unnamed target stays allowed ("delete
the log" when only app.log exists). Files the agent created or changed during this request are its own to clean up.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

# Commands that remove what they're given. Commands whose targets can't be listed from their arguments
# (`find -delete`, `git clean`) keep the ordinary approval only.
_REMOVAL_COMMAND = re.compile(r"(?:^|[\s;&|(])(?:sudo\s+)?(?:rm|rmdir|unlink|shred)(?=\s)", re.I)
REMOVAL_TOOLS = frozenset({"file_delete"})
COMMAND_TOOLS = frozenset({"terminal", "terminal_run"})

_COLLECTIVE = re.compile(
    r"\b(?:all|every|each|both|any|these|those|everything|entire|whole|multiple|several|many|"
    r"two|three|four|five|six|seven|eight|nine|ten|[2-9]|\d{2,})\b"
    r"|\b(?:files|folders|directories|dirs|documents|copies|duplicates)\b"
    r"|[*?]",
    re.I,
)


def is_removal(name: str, args: dict[str, Any]) -> bool:
    if name in REMOVAL_TOOLS:
        return True
    if name in COMMAND_TOOLS:
        return bool(_REMOVAL_COMMAND.search(str(args.get("command") or args.get("cmd") or "")))
    return False


def collective(request: str) -> bool:
    """The request asks for more than one thing to be removed."""
    return bool(_COLLECTIVE.search(request or ""))


def named(path: Path, request: str) -> bool:
    """The request names this file or folder: its name, or its stem as a whole word."""
    text = (request or "").lower()
    name = path.name.lower()
    if name and name in text:
        return True
    stem = path.stem.lower()
    return len(stem) >= 3 and re.search(rf"(?<![\w.-]){re.escape(stem)}(?![\w-])", text) is not None


def unnamed_targets(targets: Iterable[str], request: str, started: float,
                    own: Iterable[str] = ()) -> list[str]:
    """Pre-existing targets the request didn't name, in a stable order.

    ``started`` is when the request began: a target changed since then is the agent's own work. ``own`` lists
    paths the agent wrote through file tools in this request.
    """
    mine = {str(p) for p in own}
    found = []
    for target in sorted(set(targets)):
        path = Path(target)
        if target in mine or named(path, request):
            continue
        try:
            if path.lstat().st_mtime >= started:
                continue
        except OSError:
            continue
        found.append(target)
    return found


def problem(new: list[str], already: set[str], request: str, root: Path | None = None) -> str:
    """Why this removal isn't run, or "" when it may go ahead to its ordinary approval."""
    if not new or collective(request) or len(already | set(new)) < 2:
        return ""

    def show(target: str) -> str:
        path = Path(target)
        try:
            return str(path.relative_to(root)) if root is not None else path.name
        except ValueError:
            return str(path)

    listed = ", ".join(show(t) for t in sorted(already | set(new)))
    return (f"Not run: this request asked to remove one item without naming it, and this would remove more than one "
            f"existing file or folder it didn't name ({listed}). Ask the user which one they mean, and remove only "
            "what they choose. Don't remove them one at a time instead.")
