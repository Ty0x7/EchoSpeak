"""Reading outside the folders a conversation may use: classify, explain, and ask (2026-11-17).

The live failure (conversation 0d7b2dc9, 17 November 2026): the owner asked Echo "whats in /projects" and
then "in dev … go in Work and find projects". ``file_list`` answered "Path not allowed. Allowed roots:
~/Documents; ~/Desktop" twice, and Echo could only repeat the limit. Nothing offered a way forward: the
file roots were a hard wall, with no approval route, while terminal commands in a project's bubblewrap
sandbox could already read the same folders (``--ro-bind / /``). Earlier the same day ``~/Work/echospeak``
was refused although ``~/Work`` was the allowed root, because it is a symlink to ``~/Projects/echospeak``
and the message gave no hint why.

The model now matches Claude Code and Codex (docs/research/environment-access.md):

- Reading inside the conversation's folders needs no approval (the working directory and additional
  directories, in Claude Code's terms).
- Reading an existing folder outside them **asks the owner** on the normal approval card. Allow once covers
  that call; For this chat covers the folder for the conversation; Always allow stores a revocable
  ``read:<folder>`` grant (Settings › Permissions › Always allowed). It is read-only: writing still needs a
  project, and no grant reaches a channel or an unattended routine.
- Credentials and other programs' private data are never readable, granted or not (Claude Code's deny
  rules win over allow rules; here the list is the bubblewrap sandbox's hidden list, so file tools and
  sandboxed commands now hide the same places).

Enforcement is in trusted code: the loop decides before the call (agent/lean/loop.py), and
``agent.tools._safe_file_path`` checks every path against the roots plus the granted folders at the tool
boundary. A model can't grant itself anything: a grant is only ever recorded from the owner's card.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

# Tools that only read files, by the argument that names where.
READ_TOOLS = {"file_read": ("path", "file"), "file_list": ("path",), "file_find": ("path",), "file_search": ("path",)}
GRANT_PREFIX = "read:"

# Absolute system places no agent reads through file tools: other processes' memory and environment
# (the backend's own API keys are in /proc/<pid>/environ), devices, and the system's password files.
_SYSTEM_HIDDEN = ("/proc", "/sys/firmware", "/dev", "/root", "/etc/shadow", "/etc/gshadow", "/etc/sudoers",
                  "/etc/sudoers.d", "/var/lib/sss", "/run/user")


@dataclass(frozen=True)
class Access:
    kind: str            # inside | outside | missing | sensitive | invalid
    path: Optional[Path] = None      # what the call would read (resolved)
    folder: Optional[Path] = None    # what a grant would cover (outside only)
    link: str = ""                   # the path as asked, when it is a symlink that leads elsewhere
    why: str = ""                    # sensitive: what the place holds

    @property
    def key(self) -> str:
        return grant_key(self.folder) if self.folder else ""


def grant_key(folder: Path | str) -> str:
    return GRANT_PREFIX + str(folder)


def is_read_grant(key: str) -> bool:
    return str(key or "").startswith(GRANT_PREFIX)


def _inside(path: Path, folder: Path) -> bool:
    try:
        path.relative_to(folder)
        return True
    except ValueError:
        return False


def hidden_places(home: Optional[Path] = None) -> list[tuple[Path, str]]:
    """(place, what it holds) for everything file tools never read: the sandbox's hidden list, and more."""
    from config import DATA_DIR
    from agent.lean import linux_sandbox

    home = (home or Path.home()).expanduser()
    places: list[tuple[Path, str]] = []
    for rel in linux_sandbox.HIDDEN_DIRS:
        places.append((home / rel, "credentials or another program's private data"))
    for rel in linux_sandbox.HIDDEN_FILES:
        places.append((home / rel, "stored credentials"))
    places.append((Path(DATA_DIR), "EchoSpeak's own settings, grants and memory"))
    for raw in _SYSTEM_HIDDEN:
        places.append((Path(raw), "system or process internals"))
    out: list[tuple[Path, str]] = []
    for place, why in places:
        try:
            out.append((place.resolve(), why))
        except Exception:
            out.append((place, why))
        if place.absolute() != out[-1][0]:
            out.append((place.absolute(), why))  # the path as written, when it is itself a link
    return out


def sensitive(path: Path, roots: Iterable[Path] = ()) -> str:
    """What ``path`` holds when no agent may read it, else "".

    A folder the owner attached or configured inside a hidden place stays usable (the sandbox mounts it again
    the same way, agent/lean/linux_sandbox.py), except EchoSpeak's own data, which no root ever opens.
    """
    from config import DATA_DIR

    try:
        candidates = {path.absolute(), path.resolve()}
    except Exception:
        candidates = {path}
    roots = [Path(r) for r in roots]
    data = Path(DATA_DIR).resolve()
    for place, why in hidden_places():
        if not any(_inside(item, place) for item in candidates):
            continue
        if place != data and any(_inside(root, place) and any(_inside(item, root) for item in candidates) for root in roots):
            continue
        return why
    return ""


def hidden_below(folder: Path) -> list[Path]:
    """Hidden places under ``folder``: recursive searches skip them (a grant of ~ must not reach ~/.ssh)."""
    out = []
    for place, _why in hidden_places():
        if _inside(place, folder) and place != folder:
            out.append(place)
    return out


def target(name: str, args: dict[str, Any]) -> str:
    for key in READ_TOOLS.get(name, ()):
        value = str(args.get(key) or "").strip()
        if value:
            return value
    return "."


def classify(raw: str, roots: Iterable[Path], granted: Iterable[Path] = ()) -> Access:
    """Where a read of ``raw`` falls, given the conversation's folders and its read grants."""
    from agent.tools import _candidate_file_path, _file_tool_root

    raw = str(raw or "").strip() or "."
    try:
        asked = Path(os.path.normpath(_candidate_file_path(raw, _file_tool_root()).expanduser()))
        resolved = asked.resolve()
    except Exception:
        return Access("invalid")
    roots = [Path(r).resolve() for r in roots]
    why = sensitive(asked, roots) or sensitive(resolved, roots)
    if why:
        return Access("sensitive", path=resolved, why=why)
    allowed = roots + [Path(g).resolve() for g in granted]
    if any(_inside(resolved, root) for root in allowed):
        return Access("inside", path=resolved)
    link = str(asked) if asked != resolved and any(_inside(asked, root) for root in roots) else ""
    if not resolved.exists():
        return Access("missing", path=resolved, link=link)
    folder = resolved if resolved.is_dir() else resolved.parent
    return Access("outside", path=resolved, folder=folder, link=link)


def explain(raw: str, roots: Iterable[Path], *, can_ask: bool = False) -> str:
    """The refusal a file tool returns: what happened, why, and what would make it possible."""
    roots = list(roots)
    access = classify(raw, roots)
    listed = "; ".join(str(r) for r in roots) or "none (no folder is attached to this conversation)"
    if access.kind == "sensitive":
        return (f"Not readable: {raw} holds {access.why}. EchoSpeak never lets agents read it, with or without "
                "approval. Don't look for another way in; tell the user if the task needs it.")
    if access.kind == "missing":
        return (f"Not found: {access.path} doesn't exist on this computer. The folders this conversation can read "
                f"are: {listed}.")
    if access.kind == "outside":
        via = f" ({access.link} is a link to {access.path})" if access.link else ""
        how = ("EchoSpeak asks the owner when you call a file tool on it." if can_ask else
               "The owner can allow reading it from an approval card in the EchoSpeak app, or attach it as a "
               "project (/project in the terminal) to work in it.")
        return (f"Not allowed yet: {access.folder}{via} is outside the folders this conversation can read "
                f"({listed}). {how}")
    return f"Path not allowed: {raw}. The folders this conversation can read are: {listed}."


_REFUSALS = ("Not readable:", "Not allowed yet:")


def is_refusal(output: str) -> bool:
    """Whether a read tool's result is the folder boundary's refusal (explain()), as opposed to a fault."""
    text = str(output or "").strip()
    if text.startswith("Error: "):
        text = text[len("Error: "):]
    return text.startswith(_REFUSALS)


def describe_grant(folder: Path | str) -> str:
    return f"Read files in {folder} (read-only)"
