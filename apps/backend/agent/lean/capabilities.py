"""What an agent can actually use this turn, and why the rest is off.

Built in code from the live toolbox, the owner's Settings › Permissions switches,
Private/Offline mode and the agent's toolsets, never from what the model assumes.
Professional harnesses do the same (Claude Code's permission-mode and tool-search
state, Codex's approval-mode recovery hints): the model is told what exists and
what is off, so it neither promises a tool it doesn't have nor hunts for a
workaround around a switch the owner turned off.

Two uses:
- ``report(toolbox)``: a short "What you can use right now" prompt section.
- ``unavailable_message(name, toolbox)``: the tool result when the model calls a
  tool it doesn't have, saying why and what would turn it on.

Nothing here grants anything: it only describes. Guests never get the report,
since it describes the owner's settings.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

PERMISSION_LABELS = {
    "ENABLE_SYSTEM_ACTIONS": "Allow actions on this computer",
    "ALLOW_FILE_WRITE": "Create and edit files",
    "ALLOW_TERMINAL_COMMANDS": "Run terminal commands",
    "ALLOW_PLAYWRIGHT": "Browse the web in a real browser",
    "ALLOW_OPEN_APPLICATION": "Open apps",
    "ALLOW_OPEN_CHROME": "Open Chrome",
    "ALLOW_DESKTOP_AUTOMATION": "Control the desktop",
    "ALLOW_EMAIL": "Email",
    "ALLOW_SELF_MODIFICATION": "Edit EchoSpeak itself",
}
# Switches that live outside Settings › Permissions.
OTHER_SWITCHES = {"ALLOW_DISCORD_BOT": "Enable Discord in Settings › Channels"}

# Agents › Edit labels (apps/web/src/lean/Dialogs.tsx TOOLSET_INFO).
TOOLSET_LABELS = {
    "core": "Files and basics", "web": "Web search and reading pages", "live": "Live data cards",
    "terminal": "Run commands", "vision": "Read screenshots and images", "desktop": "Mouse, keyboard and app control",
    "comms": "Email and Discord", "memory": "Remember things", "skills": "Installed skills and MCP",
    "self": "Edit EchoSpeak itself",
}


@dataclass(frozen=True)
class Ability:
    label: str
    tools: tuple[str, ...]
    toolset: str
    # Mention it as "off" only when the agent's toolsets include it; for abilities
    # most agents never have (editing EchoSpeak itself) silence is less noise.
    only_if_requested: bool = False


ABILITIES: tuple[Ability, ...] = (
    Ability("Web search and reading pages", ("web_search", "safe_web_fetch"), "web"),
    Ability("Live data cards (weather, sports, stocks, products, videos, images)",
            ("weather_live", "sports_live", "stock_history", "product_search", "video_search", "image_search"), "live"),
    Ability("Reading files", ("file_read", "file_list", "file_find", "file_search"), "core"),
    Ability("Creating and editing files", ("file_edit", "file_write"), "core"),
    Ability("Terminal commands", ("terminal",), "terminal"),
    Ability("Screenshots and reading images", ("take_screenshot", "read_image"), "vision"),
    Ability("Desktop control (mouse and keyboard)", ("computer_action",), "desktop"),
    Ability("Opening apps", ("open_application",), "desktop", only_if_requested=True),
    Ability("A real browser for pages that need clicks or sign-in", ("browse_task",), "live", only_if_requested=True),
    Ability("Email", ("email_read_inbox", "email_search", "email_send", "email_reply"), "comms"),
    Ability("Discord", ("discord_read_channel", "discord_send_channel"), "comms", only_if_requested=True),
    Ability("Memory and past chats", ("memory_save", "memory_search", "chat_search"), "memory"),
    Ability("Artifacts (apps, documents, diagrams in the side panel)", ("create_artifact", "update_artifact"), "core"),
    Ability("Image and video generation", ("create_media",), "core"),
    Ability("Editing EchoSpeak's own code", ("self_edit", "self_read"), "self", only_if_requested=True),
)


def _config() -> Any:
    from config import config

    return config


def _flags_for(name: str) -> tuple[str, ...]:
    from agent.lean.toolbox import NATIVE_POLICY_FLAGS
    from agent.tool_registry import ToolRegistry

    if name in NATIVE_POLICY_FLAGS:
        return NATIVE_POLICY_FLAGS[name]
    entry = ToolRegistry.get(name)
    return tuple(getattr(entry, "policy_flags", ()) or ()) if entry is not None else ()


def _switches_off(name: str) -> list[str]:
    """The owner's switches that keep ``name`` off, as they read in the app ('' entries dropped)."""
    config = _config()
    off = [flag for flag in _flags_for(name) if not bool(getattr(config, flag.lower(), False))]
    # The master switch greys out the others in Settings, so name it alone when it is off.
    if "ENABLE_SYSTEM_ACTIONS" in off:
        off = ["ENABLE_SYSTEM_ACTIONS"]
    return off


def _describe_switches(flags: list[str]) -> str:
    in_permissions = [PERMISSION_LABELS[f] for f in flags if f in PERMISSION_LABELS]
    parts = []
    if in_permissions:
        parts.append("turned off in Settings › Permissions (" + ", ".join(in_permissions) + ")")
    parts += [OTHER_SWITCHES[f] + " is off" for f in flags if f in OTHER_SWITCHES]
    return "; ".join(parts) or "turned off in Settings"


def _privacy_block(name: str) -> str:
    """Why Private/Offline mode would stop ``name`` right now, or ''. Same rule as Toolbox.run."""
    try:
        from agent import privacy

        target = ""
        component = privacy.tool_component(name, {})
        if name == "web_search":
            # Gated inside the search provider: a SearXNG on the user's own network stays allowed.
            component, target = "search", str(getattr(_config(), "searxng_base_url", "") or "")
        if not component:
            return ""
        decision = privacy.decide(component, target)
        return "" if decision.allowed else decision.reason
    except Exception:
        return ""


def why_off(name: str, toolbox: Any) -> str:
    """Why ``name`` isn't usable in this toolbox, in a phrase, or '' when it is (or is unknown)."""
    from agent.lean.toolbox import TOOLSETS
    from agent.tool_registry import ToolRegistry

    switches = _switches_off(name)
    if name in getattr(toolbox, "names", []):
        # Offered when the turn started; a switch may have been turned off since.
        blocked = _describe_switches(switches) if switches else _privacy_block(name)
        return blocked.rstrip(".") if blocked else ""
    reasons: list[str] = []
    if switches:
        reasons.append(_describe_switches(switches))
    requested = set(getattr(toolbox, "requested", set()) or set())
    homes = [toolset for toolset, members in TOOLSETS.items() if name in members and toolset in TOOLSET_LABELS]
    if homes and name not in requested:
        label = TOOLSET_LABELS[homes[0]]
        reasons.append(f"not in this agent's toolsets (the owner can add \"{label}\" under Agents, Edit)")
    entry = ToolRegistry.get(name)
    if not reasons and entry is not None and not getattr(entry, "available", True):
        reasons.append("not set up on this computer")
    return "; and ".join(reasons)


def unavailable_message(name: str, toolbox: Any) -> str:
    """The tool result for a call to a tool this turn doesn't have."""
    reason = why_off(name, toolbox)
    if reason:
        return (f"Error: {name} is not available right now: {reason}. This is the owner's setting, not a fault: "
                "don't retry it or work around it with other tools (for example the terminal). Continue without it, "
                "or tell the user what it needs.")
    close = ", ".join(sorted(getattr(toolbox, "names", []))[:40])
    return f"Error: unknown tool '{name}'. Available tools: {close}"


def _approval_line() -> str:
    from agent.lean import settings

    mode = settings.approval_mode()
    if mode == "always":
        first = "Every action asks the user first."
    elif mode == "never":
        first = "Approvals are off, except desktop control, identity changes and tasks for other agents, which still ask."
    else:
        first = ("Risky actions ask the user first: deleting or overwriting work, dangerous commands, anything that "
                 "sends or posts outside this PC, desktop control and connected-app actions.")
    return (first + " After you read outside content (web, email, screen, connected apps), anything that sends data "
            "out or saves memory asks again. A denied or blocked action is final for this request.")


def report(toolbox: Any) -> str:
    """The "What you can use right now" prompt section for this toolbox."""
    names = set(getattr(toolbox, "names", []) or [])
    requested = set(getattr(toolbox, "requested", set()) or set())
    ready: list[str] = []
    off: list[str] = []
    for ability in ABILITIES:
        present = [tool for tool in ability.tools if tool in names]
        if present:
            blocks = {tool: reason for tool in present if (reason := _privacy_block(tool))}
            if blocks and len(blocks) == len(present):
                off.append(f"{ability.label}: {next(iter(blocks.values())).rstrip('.')}")
            elif blocks:
                ready.append(f"{ability.label} (except {', '.join(blocks)}: {next(iter(blocks.values())).rstrip('.')})")
            else:
                ready.append(ability.label)
            continue
        wanted = any(tool in requested for tool in ability.tools)
        if ability.only_if_requested and not wanted:
            continue
        reason = next((r for tool in ability.tools if (r := why_off(tool, toolbox))), "")
        if reason:
            off.append(f"{ability.label}: {reason}")
    # Deferred connected-app tools are still in the toolbox (find_tools loads their schemas).
    external = [n for n in names if str(getattr(toolbox.entry(n), "origin", "")) in {"mcp", "skill", "connection"}]
    if external:
        ready.append(f"Connected apps and skills ({len(external)} tools)")
    if "delegate_to_agent" in names:
        ready.append("Handing work to teammates")
    if not ready and not off:
        return ""
    lines = ["## What you can use right now",
             "Checked by EchoSpeak for this turn. Trust this over assumptions about your tools."]
    if ready:
        lines.append("- Ready: " + "; ".join(ready) + ".")
    if off:
        lines.append("- Off right now (don't promise these or work around them; if the user needs one, say what turns it on):")
        lines += [f"  - {item}" for item in off]
    lines.append("- Approvals: " + _approval_line())
    return "\n".join(lines)


def can_write_files(toolbox: Any) -> bool:
    names = set(getattr(toolbox, "names", []) or [])
    return bool(names & {"file_write", "file_edit"})


def snapshot(toolbox: Any) -> dict[str, Optional[str]]:
    """{ability label: None when ready, else why it is off}, for diagnostics and tests."""
    names = set(getattr(toolbox, "names", []) or [])
    result: dict[str, Optional[str]] = {}
    for ability in ABILITIES:
        if any(tool in names for tool in ability.tools):
            result[ability.label] = None
        else:
            result[ability.label] = next((r for tool in ability.tools if (r := why_off(tool, toolbox))), "") or "unavailable"
    return result
