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
from agent.lean.policy import NATIVE_COMPUTER_ACT

# Outward-facing actions: they leave the machine or speak for the user.
EXTERNAL_TOOLS = {
    "email_send", "email_reply",
    "discord_send_channel", "discord_web_send", "discord_contacts_add",
    "telegram_send", "whatsapp_send", "slack_send", "twitter_post", "tweet_post",
    "calendar_create", "calendar_delete", "github_create_issue", "github_comment", "ask_remote_agent",
    "notion_create_page", "spotify_control",
}

# Tools that rewrite an agent's standing instructions (its soul). Everything in
# them is obeyed in every later chat, so a bad write persists (Hermes gates its
# SOUL.md the same way): always shown to the user first, unless approvals are off.
IDENTITY_TOOLS = {"soul_update"}

# Tools that act on the desktop as if they were the user.
DESKTOP_CONTROL_TOOLS = {
    "desktop_click", "desktop_type_text", "desktop_send_hotkey", "notepad_write", "computer_action",
} | NATIVE_COMPUTER_ACT  # Claude's native computer actions run through computer_action's executor

# Hermes-style dangerous command detection for terminal_run.
_DANGEROUS_COMMAND = re.compile(
    r"""(?ix)
    # A quote or backtick also starts a command: cmd /c "rd /s /q …", pwsh -c 'Remove-Item …'.
    (^|[\s;&|("'`])(
        rm|rmdir|rd|del|erase|remove-item|format|diskpart|shutdown|restart-computer|stop-computer|
        reg|regedit|bcdedit|takeown|icacls|cipher|set-executionpolicy|
        stop-process|kill|taskkill|net\s+user|new-service|sc\s+delete|
        git\s+push|git\s+reset\s+--hard|git\s+clean|git\s+checkout\s+--|git\s+branch\s+-D|
        git\s+rebase|git\s+restore|git\s+stash\s+(?:drop|clear)|git\s+filter-branch|
        gh\s+pr\s+(?:merge|close)|gh\s+repo\s+delete|gh\s+release\s+(?:create|delete)|gh\s+issue\s+(?:close|delete)|
        npm\s+publish|pip\s+uninstall|winget\s+uninstall|choco\s+uninstall|
        invoke-webrequest|iwr|invoke-restmethod|irm|curl|wget|start-bitstransfer|certutil|bitsadmin|
        scp|sftp|ftp|ssh|nc|ncat|
        ri|remove-itemproperty|clear-content|set-content|out-file|mv|move|move-item|ren|rename-item
    )\b
    |>\s*[a-z]:\\|\|\s*(iex|invoke-expression|sh|bash)\b
    |\b(?:powershell|pwsh)(?:\.exe)?\b[^|;&]*\s-e(?:nc|ncodedcommand)?\b
    |\b(?:shutil\.rmtree|os\.remove|os\.unlink|os\.rmdir|send2trash)\b|\.unlink\(
    # Inline code that deletes, or hands a string to a shell, in other runtimes.
    |\b(?:rmSync|rmdirSync|unlinkSync|rimraf)\b
    |\[(?:system\.)?io\.(?:file|directory)\]::(?:delete|move)
    |\b(?:invoke-expression|iex)\b
    |\b(?:os\.system|os\.popen|subprocess\.(?:run|call|popen|check_call|check_output)|child_process|execSync)\b
    """
)


def dangerous_command(command: str) -> bool:
    """A shell command that can change or delete things on this PC."""
    return bool(_DANGEROUS_COMMAND.search(command or ""))


# ── approval scopes: what "Always allow" and autonomous mode may cover ────
#
# An approval is about one call. "Always allow" stores a grant for its *scope*, a named
# class of action the owner can read and revoke (Settings › Permissions › Always allowed).
# Only some classes can be granted. Never: deletes and dangerous commands, messages that
# leave the machine, changes to an agent's identity, tasks for other agents (A2A), and
# anything asked because outside content was read (Rule of Two), except on-screen control.

@dataclass(frozen=True)
class Scope:
    key: str
    label: str
    # Autonomous mode approves this class by itself; others are held for the owner.
    autonomous: bool = False


DESKTOP_SCOPE = Scope("desktop_control", "Control the mouse and keyboard", autonomous=True)


def scope_for(name: str, args: dict[str, Any], reason: str = "", rule: str = "") -> Optional[Scope]:
    """The grantable class this approval belongs to, or None when it must be asked every time."""
    if rule and rule != "screen_control":
        return None  # Rule of Two after web, email or app content: always the owner's call
    if name in {"ask_remote_agent", "a2a_delegate"} or name in IDENTITY_TOOLS or name in EXTERNAL_TOOLS:
        return None
    if name in DESKTOP_CONTROL_TOOLS or rule == "screen_control":
        return DESKTOP_SCOPE
    if name in {"terminal", "terminal_run", "process_start"}:
        command = str(args.get("command") or args.get("cmd") or "")
        if dangerous_command(command) or "delete or overwrite" in reason:
            return None
        if "outside the sandbox" in reason:
            return Scope("terminal:host", "Run commands on this PC that aren't dangerous", autonomous=True)
        if "internet" in reason:
            return Scope("terminal:network", "Let sandbox commands use the internet", autonomous=True)
        return None
    if rule == "generation_cost" or name == "create_media":
        return Scope("generation", "Cloud image and video generation (may cost money)")
    if name.startswith("mcp__") or "external MCP action" in reason:
        return Scope(f"mcp:{name}", f"Run the connected-app action {name}")
    return None


class GrantStore:
    """The owner's "Always allow" grants, kept across chats and restarts in DATA_DIR/security."""

    def __init__(self, path: Optional[Any] = None) -> None:
        self._lock = threading.Lock()
        self._path = path

    def _file(self):
        if self._path is not None:
            return self._path
        from pathlib import Path

        from config import DATA_DIR

        return Path(DATA_DIR) / "security" / "approval-grants.json"

    def _read(self) -> dict[str, dict[str, Any]]:
        import json

        try:
            data = json.loads(self._file().read_text(encoding="utf-8"))
            return {str(k): dict(v) for k, v in data.items() if isinstance(v, dict)} if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _write(self, data: dict[str, dict[str, Any]]) -> None:
        import json

        path = self._file()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(path)

    def has(self, key: str) -> bool:
        with self._lock:
            return key in self._read()

    def add(self, scope: Scope) -> None:
        with self._lock:
            data = self._read()
            data[scope.key] = {"label": scope.label, "created_at": time.time()}
            self._write(data)

    def remove(self, key: str) -> bool:
        with self._lock:
            data = self._read()
            if key not in data:
                return False
            data.pop(key)
            self._write(data)
            return True

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return [{"key": key, **value} for key, value in sorted(self._read().items(), key=lambda kv: kv[1].get("created_at", 0))]


_GRANTS = GrantStore()


def get_grant_store() -> GrantStore:
    return _GRANTS


def set_grant_store(store: Optional[GrantStore]) -> None:
    global _GRANTS
    _GRANTS = store or GrantStore()


def tool_needs_approval(entry: Any, name: str, args: dict[str, Any]) -> tuple[bool, str]:
    """Return (needs_approval, reason)."""
    # An independent agent may execute the request on another machine. The
    # explicit approval promised by this integration applies in every mode.
    if name in {"ask_remote_agent", "a2a_delegate"}:
        return True, "this sends a task to an independent agent"
    mode = settings.approval_mode()
    if mode == "never":
        return False, ""
    if name in IDENTITY_TOOLS:
        return True, "this changes how the agent behaves in every future chat"
    if name in DESKTOP_CONTROL_TOOLS:
        return True, "this controls your desktop"
    if mode == "always":
        return bool(getattr(entry, "is_action", False)), "action tool"
    if name in {"terminal_run", "terminal", "process_start"}:
        # Sandbox: ask only to leave it, to go online, or to delete project files.
        # This PC: ask for dangerous commands (terminal.approval_for).
        from agent.lean.terminal import approval_for

        return approval_for(args)
    if name in EXTERNAL_TOOLS or str(getattr(entry, "category", "")) in {"discord", "email", "communications"} and getattr(entry, "is_action", False):
        return True, "this sends something outside your machine"
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
    # "approval" (allow/deny a tool) or "question" (ask_user: pick an option or type an answer).
    kind: str = "approval"
    question: str = ""
    options: list[str] = field(default_factory=list)
    allow_other: bool = False
    answer: str = ""
    # False when "For this chat" cannot cover it (Rule of Two outside screen control).
    grantable: bool = True
    # The class "Always allow" would grant (approvals.scope_for), or None when it can't.
    scope: Optional[Scope] = None

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
            "kind": self.kind,
            "question": self.question,
            "options": list(self.options),
            "allow_other": self.allow_other,
            "answer": self.answer,
            "grantable": self.grantable,
            "persistable": self.scope is not None,
            "scope_label": self.scope.label if self.scope else "",
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

    def open(self, *, session_id: str, request_id: str, tool: str, summary: str, args: dict[str, Any], reason: str,
             grantable: bool = True, scope: Optional[Scope] = None) -> PendingApproval:
        approval = PendingApproval(
            id=f"apr_{uuid.uuid4().hex[:12]}",
            session_id=session_id,
            request_id=request_id,
            tool=tool,
            summary=summary,
            args=args,
            reason=reason,
            grantable=grantable,
            scope=scope,
        )
        with self._lock:
            self._pending[approval.id] = approval
        return approval

    def open_question(self, *, session_id: str, request_id: str, question: str, options: list[str],
                      allow_other: bool) -> PendingApproval:
        """An ask_user question: waits like an approval, resolved with the user's answer."""
        pending = PendingApproval(
            id=f"ask_{uuid.uuid4().hex[:12]}",
            session_id=session_id,
            request_id=request_id,
            tool="ask_user",
            summary=question,
            args={},
            reason="",
            kind="question",
            question=question,
            options=list(options),
            allow_other=allow_other,
        )
        with self._lock:
            self._pending[pending.id] = pending
        return pending

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
        text = " ".join(str(decision or "").split())
        with self._lock:
            question = self._pending.get(approval_id)
            if question is not None and question.kind == "question":
                # The answer is one of the options, or the user's own words when allowed.
                if not text:
                    raise ValueError("an answer is required")
                if not question.allow_other and text.lower() not in {o.lower() for o in question.options}:
                    raise ValueError("answer must be one of the options")
                question.answer = text[:500]
                question.decision = "answered"
                question.event.set()
                return question
        decision = text.lower()
        if decision not in {"allow", "deny", "always", "persist"}:
            raise ValueError("decision must be allow, deny, always or persist")
        with self._lock:
            approval = self._pending.get(approval_id)
            if approval is None:
                return None
            if decision == "persist" and approval.scope is None:
                raise ValueError("this action can't be always allowed; allow it once or for this chat")
            if decision == "always" and approval.grantable:
                self._session_grants.setdefault(approval.session_id, set()).add(approval.tool)
            approval.decision = "allow" if decision in {"always", "persist"} else decision
        if decision == "persist" and approval.scope is not None:
            get_grant_store().add(approval.scope)
        approval.event.set()
        return approval

    def cancel_all(self) -> int:
        """Stop everything: every approval or question still waiting ends as cancelled."""
        with self._lock:
            waiting = list(self._pending.values())
        for item in waiting:
            if not item.decision:
                item.decision = "cancelled"
            item.event.set()
        return len(waiting)

    def pending_for(self, session_id: str = "") -> list[dict[str, Any]]:
        with self._lock:
            return [
                item.public() for item in self._pending.values()
                if not session_id or item.session_id == session_id
            ]


_BROKER = ApprovalBroker()


def get_approval_broker() -> ApprovalBroker:
    return _BROKER
