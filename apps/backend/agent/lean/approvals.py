"""Inline approvals for the lean loop.

An approval pauses only the one tool call that needs it. The chat shows an
Allow / Deny card; anything else the user types never cancels it. A denial is
returned to the model as a normal tool result, so the agent can adapt instead
of the whole turn ending.
"""

from __future__ import annotations

import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from agent.lean import settings

# Outward-facing actions: they leave the machine or speak for the user.
EXTERNAL_TOOLS = {
    "email_send", "email_reply",
    "discord_send_channel", "discord_web_send", "discord_contacts_add",
    "telegram_send", "whatsapp_send", "slack_send", "twitter_post", "tweet_post",
    "calendar_create", "calendar_delete", "github_create_issue", "github_comment",
    "notion_create_page", "spotify_control",
}

# Tools that act on the desktop as if they were the user.
DESKTOP_CONTROL_TOOLS = {
    "desktop_click", "desktop_type_text", "desktop_send_hotkey", "notepad_write",
}

# Hermes-style dangerous command detection for terminal_run.
_DANGEROUS_COMMAND = re.compile(
    r"""(?ix)
    (^|[\s;&|(])(
        rm|rmdir|rd|del|erase|remove-item|format|diskpart|shutdown|restart-computer|stop-computer|
        reg|regedit|bcdedit|takeown|icacls|cipher|set-executionpolicy|
        stop-process|kill|taskkill|net\s+user|new-service|sc\s+delete|
        git\s+push|git\s+reset\s+--hard|git\s+clean|git\s+checkout\s+--|git\s+branch\s+-D|
        npm\s+publish|pip\s+uninstall|winget\s+uninstall|choco\s+uninstall|
        invoke-webrequest|iwr|curl|wget|start-bitstransfer
    )\b
    |>\s*[a-z]:\\|\|\s*(iex|invoke-expression|sh|bash)\b
    """
)


def tool_needs_approval(entry: Any, name: str, args: dict[str, Any]) -> tuple[bool, str]:
    """Return (needs_approval, reason)."""
    mode = settings.approval_mode()
    if mode == "never":
        return False, ""
    if mode == "always":
        return bool(getattr(entry, "is_action", False)), "action tool"
    if name in {"terminal_run", "terminal", "process_start"}:
        command = str(args.get("command") or "")
        if _DANGEROUS_COMMAND.search(command):
            return True, "this command can change or delete things"
        return False, ""
    if name in EXTERNAL_TOOLS or str(getattr(entry, "category", "")) in {"discord", "email", "communications"} and getattr(entry, "is_action", False):
        return True, "this sends something outside your machine"
    if name in DESKTOP_CONTROL_TOOLS:
        return True, "this controls your desktop"
    if str(getattr(entry, "risk_level", "")) == "destructive":
        return True, "this can delete or overwrite work"
    if str(getattr(entry, "origin", "")) == "mcp" and getattr(entry, "is_action", False):
        return True, "this runs an external MCP action"
    return False, ""


@dataclass
class PendingApproval:
    id: str
    session_id: str
    request_id: str
    tool: str
    summary: str
    args: dict[str, Any]
    reason: str
    created_at: float = field(default_factory=time.time)
    decision: str = ""
    event: threading.Event = field(default_factory=threading.Event)

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "request_id": self.request_id,
            "tool": self.tool,
            "summary": self.summary,
            "reason": self.reason,
            "args": self.args,
            "created_at": self.created_at,
            "decision": self.decision,
        }


class ApprovalBroker:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: dict[str, PendingApproval] = {}
        # Per-session "always allow this tool" grants (cleared on restart).
        self._session_grants: dict[str, set[str]] = {}

    def granted(self, session_id: str, tool: str) -> bool:
        with self._lock:
            return tool in self._session_grants.get(session_id, set())

    def open(self, *, session_id: str, request_id: str, tool: str, summary: str, args: dict[str, Any], reason: str) -> PendingApproval:
        approval = PendingApproval(
            id=f"apr_{uuid.uuid4().hex[:12]}",
            session_id=session_id,
            request_id=request_id,
            tool=tool,
            summary=summary,
            args=args,
            reason=reason,
        )
        with self._lock:
            self._pending[approval.id] = approval
        return approval

    def wait(self, approval: PendingApproval, cancel: Optional[threading.Event]) -> str:
        deadline = time.monotonic() + settings.approval_timeout_seconds()
        while not approval.event.wait(0.25):
            if cancel is not None and cancel.is_set():
                approval.decision = "cancelled"
                break
            if time.monotonic() >= deadline:
                approval.decision = "timeout"
                break
        with self._lock:
            self._pending.pop(approval.id, None)
        return approval.decision or "deny"

    def resolve(self, approval_id: str, decision: str) -> Optional[PendingApproval]:
        decision = str(decision or "").strip().lower()
        if decision not in {"allow", "deny", "always"}:
            raise ValueError("decision must be allow, deny, or always")
        with self._lock:
            approval = self._pending.get(approval_id)
            if approval is None:
                return None
            if decision == "always":
                self._session_grants.setdefault(approval.session_id, set()).add(approval.tool)
            approval.decision = "allow" if decision == "always" else decision
        approval.event.set()
        return approval

    def pending_for(self, session_id: str = "") -> list[dict[str, Any]]:
        with self._lock:
            return [
                item.public() for item in self._pending.values()
                if not session_id or item.session_id == session_id
            ]


_BROKER = ApprovalBroker()


def get_approval_broker() -> ApprovalBroker:
    return _BROKER
