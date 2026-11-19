"""Slash commands: the registry and the menu filter (spec §3.8)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Command:
    name: str
    desc: str
    args: str = ""
    live: bool = False          # runs immediately during a run; others wait until the turn ends
    needs: str = ""             # capability probe name; hidden when the backend lacks it
    aliases: tuple[str, ...] = ()


COMMANDS: tuple[Command, ...] = (
    Command("/model", "choose the model (this chat and the default)", "[name]", needs="models"),
    Command("/agents", "list agents and talk to one", needs="agents"),
    Command("/agent", "talk to an agent in a new direct session (- for Echo)", "<name|->", needs="agents"),
    Command("/new", "start a new session with your next message", "[--agent NAME]"),
    Command("/sessions", "browse conversations and switch to one (also: ← on an empty input)"),
    Command("/resume", "resume a session by id, or open the session list", "[id]"),
    Command("/project", "attach this folder (or a path) as a project", "[PATH|-]", needs="projects"),
    Command("/mode", "assistant mode: unified or environments", "[unified|environments]", needs="settings"),
    Command("/permissions", "show and change permission switches and the approval mode", "[mode]", needs="settings"),
    Command("/approvals", "answer approvals that are waiting", live=True),
    Command("/memory", "search or list what Echo remembers", "[query]", needs="memory"),
    Command("/status", "backend, model, runs and approvals", live=True),
    Command("/usage", "tokens used in this session and context left", live=True),
    Command("/stop", "stop the running task", live=True),
    Command("/detach", "stop following; the task keeps running on the backend", live=True),
    Command("/clear", "clear the screen and start a new session"),
    Command("/transcript", "open the full session in the pager (ctrl+o)", live=True),
    Command("/diff", "this project's changes in the pager (git diff + new files; else this session's edits)",
            "[session|git]", live=True),
    Command("/preview", "tmux: open or close a live diff pane beside the conversation", live=True),
    Command("/verbose", "toggle full tool output and thinking", live=True),
    Command("/copy", "copy the last answer to the clipboard", live=True),
    Command("/help", "commands and keys", live=True),
    Command("/quit", "exit (the backend keeps running)", live=True, aliases=("/exit", "/q")),
)


def lookup(name: str, commands: tuple[Command, ...] = COMMANDS) -> Command | None:
    name = name.lower()
    for c in commands:
        if c.name == name or name in c.aliases:
            return c
    return None


def visible(commands: tuple[Command, ...], caps: set[str]) -> tuple[Command, ...]:
    """Only commands whose backend routes answered the startup probe."""
    return tuple(c for c in commands if not c.needs or c.needs in caps)


def filter_commands(token: str, commands: tuple[Command, ...]) -> list[Command]:
    """Prefix matches first (in registry order), then substring matches in name or description."""
    t = token.lower().lstrip("/")
    if not t:
        return list(commands)
    prefix = [c for c in commands if c.name[1:].startswith(t) or any(a[1:].startswith(t) for a in c.aliases)]
    rest = [c for c in commands if c not in prefix and (t in c.name[1:] or t in c.desc.lower())]
    return prefix + rest


SHORTCUTS = (
    ("/ for commands", "shift+tab to cycle approval mode", "ctrl+o for the transcript"),
    ("← for your sessions", "← again: all sessions", "→ or esc to come back"),
    ("? for shortcuts", "esc to stop a running task", "ctrl+g to edit in $EDITOR"),
    ("\\ + enter for a newline", "enter while running to steer", "ctrl+r to search history"),
    ("ctrl+j for a newline", "tab while running to queue", "ctrl+c twice to exit"),
)

KEYS_HELP = (
    ("Enter", "send · while running: steer the task"),
    ("Tab", "complete a command · while running: queue a follow-up"),
    ("Esc", "close the menu · while running: stop · Esc Esc clears the draft"),
    ("Ctrl+C", "clear the input; twice exits · while running: stop; twice detaches"),
    ("Ctrl+D", "exit on an empty line · while running: detach"),
    ("Shift+Tab", "cycle approval mode: smart → autonomous → always ask"),
    ("↑ / ↓", "history · while running, ↑ on empty input takes back the last queued message"),
    ("←", "on an empty input: your sessions; ← again: the sessions page (enter resumes, → or esc goes back a level)"),
    ("Ctrl+O", "transcript in the pager"),
    ("Ctrl+R", "search history"),
    ("Ctrl+G", "edit the draft in $EDITOR · in a dialog: full arguments or the full diff"),
    ("PgUp / PgDn", "in an approval with a diff: scroll the diff"),
    ("Ctrl+L", "redraw"),
    ("Ctrl+Z", "suspend"),
    ("\\+Enter, Ctrl+J, Alt+Enter", "newline"),
)
