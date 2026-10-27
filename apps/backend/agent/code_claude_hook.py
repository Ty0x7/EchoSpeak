"""Claude Code's PermissionRequest hook for Echo Code: asks the owner, answers Claude Code.

Claude Code runs this (exec form, no shell) whenever it would show a permission prompt in
a session Echo started. It forwards the tool name and input to that session over loopback,
waits for the owner's "Approve once" or "Deny" in Echo Code, and prints the decision in
Claude Code's documented hook format. Anything unexpected is a denial: a `claude -p` run
also denies a request no hook decided, so this never widens what Claude Code allows.
"""
from __future__ import annotations

import json
import os
import socket
import sys

MAX_INPUT = 1024 * 1024
WAIT_SECONDS = 195  # Echo's approval card waits 180 s; the hook's own timeout is longer


def decision(allow: bool, message: str = "") -> dict:
    body = {"behavior": "allow"} if allow else {"behavior": "deny", "message": message or "The owner didn't approve this in Echo Code."}
    return {"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": body}}


def ask(event: dict) -> bool:
    port = int(os.environ["ECHO_CODE_HOOK_PORT"])
    request = {"token": os.environ["ECHO_CODE_HOOK_TOKEN"], "tool_name": str(event.get("tool_name") or ""),
               "tool_input": event.get("tool_input") or {}}
    with socket.create_connection(("127.0.0.1", port), timeout=10) as connection:
        connection.settimeout(WAIT_SECONDS)
        connection.sendall((json.dumps(request) + "\n").encode())
        reply = connection.makefile("rb").readline(4096)
    return json.loads(reply or b"{}").get("allow") is True


def main() -> int:
    try:
        event = json.loads(sys.stdin.buffer.read(MAX_INPUT + 1)[:MAX_INPUT] or b"{}")
        allowed = event.get("hook_event_name") == "PermissionRequest" and ask(event)
        print(json.dumps(decision(allowed)))
    except Exception:
        print(json.dumps(decision(False, "Echo Code couldn't ask the owner, so this was denied.")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
