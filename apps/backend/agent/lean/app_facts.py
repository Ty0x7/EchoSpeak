"""What EchoSpeak is, read from the running app, for every agent profile.

One section, built in trusted code, shared by Echo's conversation prompt, the engineering
prompt and every named agent (docs/research/agent-first.md). The 16 November 2026 conversation
audit found each profile guessing about the app it runs in:

- Echo said there was "no separate chat mode", that EchoSpeak "isn't a harness" and explained
  Unix file permissions when asked how to see permissions in the terminal;
- Echo invented agent categories ("Desktop Control Agents") instead of naming the roster, and
  Jarvis, in a direct conversation, could not see that an agent named Echo exists;
- asked which model it ran on, Jarvis answered with the app's version.

The causes were structural: the roster lived in Chat's prompt only, the team section listed
room members (one, in a direct conversation) and only when delegation was possible, the model
was named only where the identity sentence happened to mention it, and nothing told any agent
which commands the two interfaces really have. So these facts are assembled here, once, from
the persona store, the resolved endpoint, the version module and the assistant mode.

Nothing here grants anything. It describes; permissions are enforced elsewhere.
"""
from __future__ import annotations

import platform
from typing import Any, Optional

SOURCE_URL = "https://github.com/Ty0x7/EchoSpeak"

# The terminal client's commands an agent may point the user to. Kept in step with
# apps/terminal/echospeak_cli/tui/commands.py by tests/test_app_facts.py: a command listed
# here that the terminal doesn't have fails the suite, so the prompt can't drift into fiction.
TERMINAL_COMMANDS: tuple[tuple[str, str], ...] = (
    ("/permissions", "permission switches and the approval mode"),
    ("/model", "choose the model"),
    ("/agents", "list the agents and talk to one"),
    ("/agent", "talk to one agent by name"),
    ("/project", "attach or detach a project folder"),
    ("/sessions", "browse and resume conversations"),
    ("/new", "start a new conversation"),
    ("/status", "backend, model, runs and approvals"),
    ("/help", "every command and key"),
)
# Where the same things live in the desktop app.
DESKTOP_PLACES = "Settings › Permissions, Settings › Models, the Agents page, the project chip in the composer"


def roster() -> list[tuple[str, str]]:
    """Every agent the owner has: (name, title). Echo first. From the persona store, never from memory."""
    try:
        from agent.lean.personas import get_persona_store

        people = [(str(a.name or "").strip(), str(a.title or "").strip()) for a in get_persona_store().list()]
    except Exception:
        people = []
    people = [p for p in people if p[0]]
    if not any(name.lower() == "echo" for name, _ in people):
        people.insert(0, ("Echo", "Personal assistant"))
    people.sort(key=lambda p: p[0].lower() != "echo")
    return people[:12]


def roster_line(you: str = "") -> str:
    names = []
    for name, title in roster():
        label = f"{name} ({title})" if title else name
        names.append(label + (" (this is you, the agent writing this reply; the user is not an agent)" if you and name.lower() == you.lower() else ""))
    return ", ".join(names)


def model_line(name: str, model: str, provider: str) -> str:
    """Who is speaking and which model writes the replies: two different facts, kept apart."""
    from version import APP_VERSION

    model = " ".join(str(model or "").split())[:80]
    provider = " ".join(str(provider or "").split())[:40]
    if not model:
        return (f"- You are {name}. EchoSpeak could not read which model you run on this turn: say so if asked, "
                f"and never answer with the app version ({APP_VERSION}).")
    engine = f"{model} (served by {provider})" if provider else model
    return (f"- You are {name}. The model writing your replies is {engine}; {APP_VERSION} is EchoSpeak's app "
            "version, never a model name.")


def _where() -> str:
    from config import is_packaged

    return "the EchoSpeak desktop app" if is_packaged() else "EchoSpeak running from source (a development build)"


def _mode_line(short: bool = False) -> str:
    from agent import environments

    if environments.unified():
        if short:
            return ("- How it is organised: one assistant. A trusted project folder adds file and terminal work to "
                    "the same conversation; there are no separate chat and code assistants.")
        return ("- How it is organised: one assistant. Echo converses, researches and remembers in any conversation; "
                "a trusted project folder attached to a conversation adds file and terminal work in that folder, "
                "and changes nothing else. Each named agent has its own conversations. There are no separate chat "
                "and code assistants to switch between.")
    return ("- How it is organised: three areas. Chat (the selected model answers), Agents (the roster above, in "
            "their own conversations) and Code (engineering in a project). The owner chose this layout in settings.")


def section(*, name: str, model: str = "", provider: str = "", short: bool = False) -> str:
    """The "About EchoSpeak" prompt section for the agent called ``name`` on ``model``.

    ``short`` is for small local models, whose window is the tightest: every line here is sent on every call.
    The D1-D8 campaign of 17 November 2026 measured the first, longer version (about 2,000 characters) pushing
    the 6,000-line-log case to the 16,384-token window; the short form keeps the facts the audit needed.
    """
    from version import APP_VERSION

    commands = " ".join(name for name, _ in TERMINAL_COMMANDS)
    if short:
        return "\n".join([
            "## About EchoSpeak (from the running app)",
            model_line(name, model, provider),
            f"- EchoSpeak is the harness: one runtime that runs every agent; the desktop app and the terminal "
            f"(`echospeak`) share its conversations. Version {APP_VERSION}, by Ty (ty0x7), {SOURCE_URL}; never take a "
            "version number from memory, files or the web.",
            f"- The agents, all of them: {roster_line(name)}. Tools are not agents.",
            _mode_line(short=True),
            f"- Terminal commands: {commands} (← on an empty input lists sessions). Never invent a command.",
        ])
    return "\n".join([
        "## About EchoSpeak (from the running app)",
        model_line(name, model, provider),
        f"- You are running in {_where()}, version {APP_VERSION}, on {platform.system()} {platform.release()}. "
        f"EchoSpeak was created by Ty (ty0x7); source, releases and pull requests: {SOURCE_URL} (other apps share "
        "the name).",
        f"- {APP_VERSION} is read from the running app and is always current. Version numbers in saved memories, "
        "past chats, files, app or shortcut names, and web pages are older or about something else: never answer "
        "\"what version are you\" from them.",
        "- EchoSpeak is the harness: one shared runtime (model loop, tools, memory, approvals, verification, "
        "sessions) that runs every agent. The desktop app and the terminal client (`echospeak`) are two windows "
        "onto the same backend and conversations.",
        f"- The agents, all of them: {roster_line(name)}. Web search, live-data cards and desktop control are tools "
        "an agent may have, not agents.",
        _mode_line(),
        f"- Finding things. Terminal: {commands}; the left arrow on an empty input opens the session list. "
        f"Desktop: {DESKTOP_PLACES}. Never invent a command, a mode or a setting; /help lists them.",
    ])


def snapshot(*, name: str = "Echo", model: str = "", provider: str = "") -> dict[str, Any]:
    """The same facts as data, for the API (both interfaces show them) and for tests."""
    from agent import environments
    from version import APP_VERSION

    return {
        "app": "EchoSpeak",
        "version": APP_VERSION,
        "build": "desktop" if _packaged() else "source",
        "assistant_mode": environments.assistant_mode(),
        "agent": name,
        "model": model,
        "provider": provider,
        "roster": [{"name": n, "title": t} for n, t in roster()],
        "terminal_commands": [{"name": n, "about": about} for n, about in TERMINAL_COMMANDS],
    }


def _packaged() -> bool:
    from config import is_packaged

    return bool(is_packaged())


def endpoint_names(endpoint: Optional[Any]) -> tuple[str, str]:
    """(model, provider) of a resolved endpoint, or ("", "") when it could not be resolved."""
    if endpoint is None:
        return "", ""
    return str(getattr(endpoint, "model", "") or ""), str(getattr(endpoint, "provider", "") or "")
