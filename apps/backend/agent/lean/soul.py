"""The soul: an agent's standing identity and rules, loaded at the start of every chat.

Echo's soul is SOUL.md; a teammate's is the ``soul`` field in its persona. Both
are edited here, by the user (Settings) or by the agent itself (``soul_update``).

An agent edit only counts once it is on disk and reads back the same through
the loader the next chat uses. Anything else is reported as a failure, so an
agent can't tell the user "I've updated my soul" when nothing changed.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any, Callable, Optional

from config import BASE_DIR, DATA_DIR, config

# Agent additions go under this heading so the user can find and review them.
ADDED_HEADING = "## Added at your request"

DESCRIPTION = (
    "Change your soul: your standing instructions about who you are and how you behave, loaded at the "
    "start of every chat. Use it only when the user asks you to change your personality, tone or standing "
    "rules for how you work (\"from now on, keep answers short\", \"stop using bullet points\"). Facts about "
    "the user, their people or projects go in memory_save instead. action: add (a new instruction in "
    "'text'), replace ('old_text' -> 'text'), remove ('old_text'). The change is saved and read back: only "
    "tell the user it's saved if this tool says \"Saved and verified\"."
)

PARAMETERS = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["add", "replace", "remove"]},
        "text": {"type": "string", "description": "add: the new instruction, one sentence. replace: what replaces old_text."},
        "old_text": {"type": "string", "description": "replace/remove: the exact words to change, copied from your soul."},
    },
    "required": ["action"],
}


def soul_settings() -> tuple[bool, str, int]:
    """(enabled, configured path, max_chars)."""
    soul = getattr(config, "soul", None)
    if soul is None:
        return False, "./SOUL.md", 8000
    return bool(getattr(soul, "enabled", True)), str(getattr(soul, "path", "./SOUL.md")), int(getattr(soul, "max_chars", 8000) or 8000)


def soul_path() -> Path:
    """Where SOUL.md lives. Packaged desktop installs keep it in the data folder."""
    _, configured, _ = soul_settings()
    path = Path(configured).expanduser()
    if not path.is_absolute():
        desktop = os.getenv("ECHOSPEAK_RUNTIME_KIND", "").strip().lower() == "desktop"
        path = (DATA_DIR if desktop else BASE_DIR) / path
    return path


def write_atomic(path: Path, text: str) -> None:
    """Temp file + fsync + rename: a crash never leaves a half-written soul."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".soul_", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    except BaseException:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise


def apply_edit(current: str, action: str, text: str = "", old_text: str = "") -> tuple[str, str]:
    """(new soul, '') or ('', error). Pure: no I/O."""
    action = str(action or "").strip().lower()
    text = " ".join(str(text or "").split())
    old = str(old_text or "").strip()
    body = str(current or "").rstrip()
    if action == "add":
        if not text:
            return "", "give the new instruction in 'text'"
        if text in body:
            return "", "your soul already says that"
        if ADDED_HEADING in body:
            return f"{body}\n- {text}\n", ""
        return f"{body}\n\n{ADDED_HEADING}\n\n- {text}\n".lstrip(), ""
    if action in {"replace", "remove"}:
        if not old:
            return "", f"'old_text' is required for {action}: copy the exact words from your soul"
        count = body.count(old)
        if count == 0:
            return "", "those words aren't in your soul (copy them exactly)"
        if count > 1:
            return "", "those words appear more than once; include more of the sentence so they're unique"
        if action == "replace":
            if not text:
                return "", "give the replacement in 'text'"
            return body.replace(old, text, 1) + "\n", ""
        lines = body.replace(old, "", 1).split("\n")
        # Drop a bullet left empty by the removal.
        lines = [line for line in lines if line.strip() not in {"-", "*"}]
        return "\n".join(lines).rstrip() + "\n", ""
    return "", "action must be add, replace or remove"


def update_soul(
    *,
    action: str,
    text: str,
    old_text: str,
    read: Callable[[], str],
    write: Callable[[str], None],
    read_back: Callable[[], str],
    max_chars: int,
) -> tuple[bool, str, str]:
    """Apply one edit and prove it took. Returns (ok, message for the model, new soul)."""
    current = read()
    new_text, problem = apply_edit(current, action, text, old_text)
    if problem:
        return False, f"Failed: {problem}. Nothing was changed.", current
    if len(new_text.strip()) > max_chars:
        return False, (f"Failed: the soul would be {len(new_text.strip()):,} characters, over its limit of "
                       f"{max_chars:,}. Nothing was changed. Shorten or replace something instead."), current
    try:
        write(new_text)
    except Exception as exc:
        return False, f"Failed: the soul could not be saved ({exc}). Nothing was changed.", current
    try:
        stored = read_back()
    except Exception as exc:
        stored = ""
        problem = str(exc)
    if stored.strip() != new_text.strip():
        return False, ("Failed: the soul was written but did not read back the same"
                       + (f" ({problem})" if problem else "")
                       + ", so the change can't be trusted. Tell the user it did not save."), current
    what = {"add": f"added \"{' '.join(text.split())}\"", "replace": f"replaced \"{old_text.strip()}\"",
            "remove": f"removed \"{old_text.strip()}\""}.get(action.strip().lower(), "changed it")
    return True, (f"Saved and verified: {what}. Read back from disk, so it applies from now on, "
                  "including new chats. This update is complete; don't repeat it."), new_text


def describe(args: dict[str, Any]) -> str:
    """One line for the approval card: the exact change."""
    action = str(args.get("action") or "").lower()
    text, old = str(args.get("text") or "").strip(), str(args.get("old_text") or "").strip()
    if action == "add":
        return f"Add to my soul: “{text[:200]}”"
    if action == "replace":
        return f"Change my soul: “{old[:120]}” → “{text[:120]}”"
    if action == "remove":
        return f"Remove from my soul: “{old[:200]}”"
    return "Change my soul"


def read_file(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def load_check(loader: Optional[Callable[[], str]], path: Path) -> Callable[[], str]:
    """Read back through the agent's own loader (what the next chat uses) when there is one."""
    if loader is not None:
        return loader
    return lambda: read_file(path)
