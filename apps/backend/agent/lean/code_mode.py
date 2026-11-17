"""Echo Code's native environment: the selected model as a coding assistant, in the shared Lean harness.

Chat, Code and Agents share one runtime (agent/lean/runtime.py), loop, toolbox, verification ledger,
approvals and learning. They differ in who answers and what they're for:

- **Chat** (agent/lean/chat_mode.py): the model itself, for conversation and research.
- **Code** (here): the model itself, for software engineering in the session's project. It has no
  agent identity, roster, team, delegation, soul or agent-work tools. It gets an engineering
  workflow, the project and git context, file, terminal and docs tools, and the 11.11.5
  verification ledger, which measures checks and states completion.
- **Agents**: Echo and the other persistent agents, with identities, teams, routines and work.

External harnesses (Codex, Claude Code, ACP) run in Echo Code too, through agent/echo_code.py; they
never pass through here. Native Code uses the selected configured local or API endpoint;
external coding subscriptions retain their own harness, authentication and session ownership.
"""
from __future__ import annotations

from typing import Any

from agent.lean.toolbox import BUILDER_FOCUS

IDENTITY = (
    "You are {model}, working as a coding assistant in Echo Code, EchoSpeak's coding environment. "
    "You're the model the user picked, not one of EchoSpeak's agents: no persona, no team. "
    "You work in the user's project on this computer with file, terminal and search tools."
)

# One assistant (agent/environments.py): Echo does the engineering, in whichever conversation has the project.
IDENTITY_UNIFIED = (
    "You are Echo, the user's personal AI assistant in EchoSpeak, running on {model}. "
    "You answer, research and remember as you do in any conversation. This conversation also has a trusted "
    "project folder attached, so you can work in it as its engineer, with file and terminal tools. "
    "The project is context, not the subject of every message: a greeting, a question about the news, sports, "
    "the weather or EchoSpeak itself gets a normal answer, without touching the project."
)

RULES_HEADING = "## How you work in Echo Code"
# One assistant: the same workflow, named for when it applies.
RULES_HEADING_UNIFIED = ("## When the request is work in the project\n"
                         "Use this workflow for engineering requests. For anything else, answer directly and use the "
                         "research rules above; don't inspect the project to answer an unrelated question.")

RULES = """## How you work in Echo Code
Understand the request → inspect the project → plan when it's not trivial → edit files → run commands → verify → adapt → report.
- Read before you edit: open the files involved and follow the code's existing style. Use file_search / file_find to locate things; don't guess paths.
- Make precise edits with file_edit (exact text replace); use file_write for new files. Never leave placeholders or stubs.
- Run things with the terminal: tests, builds, linters, the program itself. Prefer the project's own commands (its scripts, test runner, package manager).
- A command that ran is not a task that's done. Verify with the check that matters (the failing test, the build, the type checker) after your last change. For a substantial change, call set_objective first with the acceptance criteria and the check.
- When a check fails, read the actual error and fix the cause. Don't edit tests to make them pass unless the user asked for test changes; don't repeat the same fix.
- Keep changes scoped to what was asked. Mention anything else you noticed instead of fixing it unasked.
- Git: inspect with git status / git diff. Don't commit, push, rebase or discard changes unless the user asks.
- Finish with a short report: what changed (files), how it was verified (commands and results), and anything not verified or left to do. Use code fences for code and commands; no charts or cards."""

# What native Code works with: files, terminal, the web for docs, memory, skills, and reading images
# (a screenshot of an error). Not live-data cards, shopping, media generation, reminders or desktop control.
CODE_TOOLSETS = ["core", "web", "terminal", "vision", "memory", "skills"]
_NOT_IN_CODE = {
    "set_reminder", "list_reminders", "cancel_reminder", "create_media", "creation_status", "create_artifact",
    "update_artifact", "save_project_image", "spawn_workers", "computer_action", "take_screenshot", "open_application",
    "product_search", "video_search", "image_search", "stock_history", "weather_live", "sports_live",
}
# Small local models see these up front; the rest load through find_tools (agent/lean/toolbox.py focus).
CODE_FOCUS = BUILDER_FOCUS | {"web_search", "safe_web_fetch", "memory_search", "set_objective", "read_output"}

# One assistant (docs/research/agent-first.md): attaching a project adds engineering tools to Echo; it
# doesn't take the conversation ones away. Before, a trusted project swapped Echo for the Code profile,
# and "when do the Oilers play next?" was answered without sports_live, by eight web calls, with no
# evidence ledger and no citation review (conversation audit, 16 November 2026).
CONVERSATION_TOOLS = frozenset({
    "set_reminder", "list_reminders", "cancel_reminder", "create_media", "creation_status", "create_artifact",
    "update_artifact", "product_search", "video_search", "image_search", "stock_history", "weather_live", "sports_live",
})
# Still not offered beside a project: research workers, the desktop and the screen, and the real browser.
_NOT_BESIDE_A_PROJECT = (_NOT_IN_CODE - CONVERSATION_TOOLS) | {"browse_task"}
UNIFIED_FOCUS = CODE_FOCUS | {"sports_live", "weather_live", "calculate", "memory_save"}


def _unified() -> bool:
    from agent import environments

    return environments.unified()


def toolsets() -> list[str]:
    return CODE_TOOLSETS + ["live"] if _unified() else list(CODE_TOOLSETS)


def focus() -> set[str]:
    return set(UNIFIED_FOCUS if _unified() else CODE_FOCUS)


def rules() -> str:
    if not _unified():
        return RULES
    return RULES.replace(RULES_HEADING, RULES_HEADING_UNIFIED, 1).replace(
        "; no charts or cards.", ".")


def identity(model: str) -> str:
    from agent import environments

    return (IDENTITY_UNIFIED if environments.unified() else IDENTITY).format(model=model or "a coding model")


def restrict(toolbox: Any) -> None:
    """Code's tool boundary, in trusted code: what isn't for engineering is never offered."""
    out = _NOT_BESIDE_A_PROJECT if _unified() else _NOT_IN_CODE
    toolbox.entries = {name: entry for name, entry in toolbox.entries.items() if name not in out}
    toolbox.native = {name: tool for name, tool in toolbox.native.items() if name not in out}
    toolbox.deferred -= out
