"""What each approval mode really decides, call by call (campaign 24 November 2026, workstream 9).

Runs agent.lean.approvals.tool_needs_approval for representative calls under every mode and prints a table.
Folder-access grants (agent/lean/file_access.py), permission switches and privacy modes are separate gates;
they are listed as such, not simulated here.

    .venv/bin/python scripts/approval_matrix.py [--json]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.lean import approvals, settings  # noqa: E402

CALLS = [
    ("read a file", "file_read", {"path": "notes.md"}, {}),
    ("write a file", "file_write", {"path": "notes.md", "content": "x"}, {"is_action": True}),
    ("edit a file", "file_edit", {"path": "app.py"}, {"is_action": True}),
    ("delete a file", "file_delete", {"path": "old.txt"}, {"is_action": True, "risk_level": "destructive"}),
    ("run `pytest -q` (sandbox)", "terminal", {"command": "pytest -q"}, {}),
    ("run `ls` on this PC", "terminal", {"command": "ls", "where": "host"}, {}),
    ("run `rm -rf build` on this PC", "terminal", {"command": "rm -rf build", "where": "host"}, {}),
    ("run `rm -rf ~`", "terminal", {"command": "rm -rf ~", "where": "host"}, {}),
    ("send an email", "email_send", {"to": "a@b.c"}, {"is_action": True, "category": "email"}),
    ("MCP action", "mcp_tool", {}, {"is_action": True, "origin": "mcp"}),
    ("click on the desktop", "desktop_click", {"x": 1, "y": 1}, {"is_action": True}),
    ("change its own soul", "soul_update", {"text": "x"}, {"is_action": True}),
    ("open a pull request", next(iter(approvals.ALWAYS_ASK_TOOLS), "github_create_pr"), {}, {"is_action": True}),
    ("ask a remote agent", "ask_remote_agent", {}, {"is_action": True}),
    ("web search", "web_search", {"query": "x"}, {}),
]


def main() -> int:
    table = {}
    for mode in ("always", "smart", "autonomous", "never"):
        settings.approval_mode = (lambda m=mode: m)
        for label, name, args, meta in CALLS:
            entry = SimpleNamespace(is_action=meta.get("is_action", False), risk_level=meta.get("risk_level", ""),
                                    category=meta.get("category", ""), origin=meta.get("origin", ""))
            needs, reason = approvals.tool_needs_approval(entry, name, args)
            held = ""
            if needs and mode == "autonomous":
                scope = approvals.scope_for(name, args, reason)
                held = "auto" if getattr(scope, "autonomous", False) else "held"
            table.setdefault(label, {})[mode] = ("ask" + (f" ({held})" if held else "")) if needs else "runs"
    if "--json" in sys.argv:
        print(json.dumps(table, indent=1))
        return 0
    modes = ("always", "smart", "autonomous", "never")
    print("| call | " + " | ".join(modes) + " |\n|---|" + "---|" * len(modes))
    for label, row in table.items():
        print(f"| {label} | " + " | ".join(row[m] for m in modes) + " |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
