"""What a turn did to the project, for the turns after it, and requests that only ask to carry on.

The history a turn sees holds earlier replies only, never their tool calls (runtime._history). That is right for
a conversation, but a reply often says little about the work: in the owner's GTA-clone session (19 November 2026)
one turn wrote a Python package (src/core/engine.py, src/vehicle/car.py, src/game/player.py) and ended on "Great!
Now let me continue building out the map system…". The owner said "why stopped? and continue", and the next turn,
knowing nothing of those files, started a JavaScript game beside them. It also never said why it had stopped: the
request itself, "why stopped? and continue", had become the work's objective.

``turn_line`` is a short, deterministic record of a turn's changes and of how it ended, built from the state
store's tool runs. It's attached to that turn's reply in the history (so it stays byte-identical turn after turn and
the model server's cached prefix survives). ``is_continuation`` recognises a request that only asks to carry on.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

# Tools that change files, and the verb for each in the record.
_FILE_VERBS = {
    "file_write": "wrote", "file_edit": "edited", "file_delete": "deleted", "file_move": "moved",
    "file_copy": "copied", "file_mkdir": "made folder", "artifact_write": "wrote", "checkpoint_undo": "undid changes to",
}
_TERMINALS = {"terminal", "terminal_run", "process_start"}
# How a turn stopped before its work was done, in the words the next turn (and the user) need.
_UNFINISHED = {
    "max_steps": "it reached its step limit before finishing",
    "budget": "it reached its work budget (tokens or time) before finishing",
    "no_answer": "it ended without a final answer, in the middle of the work",
    "cancelled": "it was stopped",
    "stopped": "it was stopped",
    "context": "the conversation outgrew the model's context window",
}

# A request that only asks to carry on, possibly asking why it stopped. Short, and nothing else in it.
_CONTINUE = re.compile(
    r"""(?ix)^\W*
    (?:(?:ok(?:ay)?|yes|yeah|yep|sure|alright|now|so|and|then|please|pls|plz)\b[\s,!.]*)*
    (?:
        (?:please\s+)?(?:continue|keep\s+going|keep\s+at\s+it|carry\s+on|go\s+on|resume|proceed|
           finish(?:\s+(?:it|up|that|the\s+job|the\s+work))?|go\s+ahead|pick\s+(?:it\s+)?up\s+where\s+you\s+left\s+off)
      | why\s+(?:did\s+|have\s+|has\s+)?(?:you\s+|u\s+|it\s+|echo\s+)?(?:stop|stopped|quit)
      | you\s+stopped
    )
    (?:\b|$)""")
_NOT_ABOUT_WORK = re.compile(r"(?i)\b(?:what|how)\s+does\s+continue\b|\bcontinue\s+(?:the\s+)?(?:conversation|chat|talking)\b")


def is_continuation(text: str) -> bool:
    """True for "continue", "keep going!", "why stopped? and continue": a request to carry on the last work."""
    request = " ".join(str(text or "").split())
    if not request or len(request) > 80 or _NOT_ABOUT_WORK.search(request):
        return False
    return bool(_CONTINUE.match(request))


def _show(path: str, root: str) -> str:
    raw = str(path or "").strip()
    if not raw or not root:
        return raw
    try:
        return str(Path(raw).relative_to(Path(root)))
    except ValueError:
        return raw


def _command(text: str) -> str:
    first = str(text or "").strip().splitlines()[0] if str(text or "").strip() else ""
    return first if len(first) <= 90 else first[:87] + "…"


def _ok(run: dict[str, Any]) -> bool:
    outcome = run.get("outcome") or {}
    if isinstance(outcome, dict) and "success" in outcome:
        return bool(outcome.get("success"))
    return str(run.get("status") or "") in {"complete", "completed", "success"}


def stop_reason(turn: dict[str, Any]) -> str:
    """The turn's stop reason, from the chat run or its last reply ('' if neither says)."""
    execution = turn.get("execution") or {}
    chat_run = ((execution.get("metadata") or {}).get("chat_run") or {}) if isinstance(execution, dict) else {}
    reason = str(chat_run.get("stop_reason") or "")
    if not reason:
        for message in reversed(turn.get("messages") or []):
            if message.get("role") == "assistant" and message.get("stop_reason"):
                reason = str(message["stop_reason"])
                break
    return reason


def unfinished(turn: dict[str, Any]) -> str:
    """Why the turn stopped before its work was done, or '' when it finished (or there was no work)."""
    reason = _UNFINISHED.get(stop_reason(turn), "")
    if reason:
        return reason
    if str(turn.get("status") or "") == "failed" or turn.get("success") is False:
        error = " ".join(str(turn.get("error") or (turn.get("execution") or {}).get("error") or "").split())
        return f"it failed: {error[:160]}" if error else "it failed before finishing"
    return ""


def changes(turn: dict[str, Any], root: str = "") -> tuple[list[str], list[str]]:
    """('wrote a.py', …) and the commands that changed things, from a turn's successful tool runs."""
    from agent.learning.episodes import command_kind

    done: list[str] = []
    commands: list[str] = []
    for run in turn.get("tool_runs") or []:
        name = str(run.get("tool_name") or run.get("name") or "")
        if not _ok(run):
            continue
        args = run.get("canonical_arguments") or {}
        if name in _FILE_VERBS:
            path = _show(str(args.get("path") or args.get("filename") or args.get("src") or ""), root)
            if path:
                entry = f"{_FILE_VERBS[name]} {path}"
                if args.get("dst"):
                    entry += f" → {_show(str(args['dst']), root)}"
                if entry not in done:
                    done.append(entry)
        elif name in _TERMINALS:
            command = str(args.get("command") or args.get("cmd") or "")
            if command and command_kind(command) == "change":
                shown = _command(command)
                if shown and shown not in commands:
                    commands.append(shown)
    return done, commands


def turn_line(turn: dict[str, Any], root: str = "") -> str:
    """'[EchoSpeak's record of this turn: wrote …; ran `…`; it ended …]', or '' when there's nothing to record."""
    done, commands = changes(turn, root)
    ended = unfinished(turn)
    if not done and not commands and (not ended or stop_reason(turn) == "max_steps"):
        # A look-up that hit its step limit still answered from what it found (chat_run's grace answer): nothing
        # to carry on, and a note under every such answer would only crowd the history.
        return ""
    parts: list[str] = []
    if done:
        # Same verb runs together: "wrote a, b, c; edited d".
        grouped: dict[str, list[str]] = {}
        for entry in done:
            verb, _, rest = entry.partition(" ")
            if verb == "made":  # "made folder x"
                verb, rest = "made folder", rest.removeprefix("folder ")
            elif verb == "undid":
                verb, rest = "undid changes to", rest.removeprefix("changes to ")
            grouped.setdefault(verb, []).append(rest)
        for verb, paths in grouped.items():
            shown = paths[:10] + ([f"+{len(paths) - 10} more"] if len(paths) > 10 else [])
            parts.append(f"{verb} {', '.join(shown)}")
    if commands:
        parts.append("ran " + ", ".join(f"`{c}`" for c in commands[:3]) + (" and more" if len(commands) > 3 else ""))
    if ended:
        parts.append(ended)
    elif done or commands:
        parts.append("it finished with the reply above")
    return "[EchoSpeak's record of this turn: " + "; ".join(parts)[:900] + "]"


def objective_of(turns: list[dict[str, Any]]) -> str:
    """The request a run of "continue" turns goes back to: the newest one that wasn't itself a continuation."""
    for turn in reversed(turns[-6:]):
        query = str((turn.get("execution") or {}).get("query") or "")
        if not query:
            query = next((str(m.get("text") or "") for m in turn.get("messages") or [] if m.get("role") == "user"), "")
        if query and not is_continuation(query):
            return query.strip()
    return ""
