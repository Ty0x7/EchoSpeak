"""Register (or remove) Echo Companion's hook with Claude Code and Codex.

Only official, documented mechanisms are used:

- Claude Code: ``hooks`` in ``~/.claude/settings.json``.
- Codex: ``~/.codex/hooks.json``, plus ``notify`` in ``~/.codex/config.toml`` as a
  fallback. Codex asks the owner to trust new hooks before they run.

Every change backs the file up first, is idempotent, and is reversible with
``uninstall``. The hook only writes to EchoSpeak's private spool. It prints nothing, so
it cannot add context to, block or alter an agent's turn.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from agent.companion import hook_client

MARK = "echo-companion-hook.py"
CLAUDE_EVENTS = ("SessionStart", "UserPromptSubmit", "Stop", "SubagentStop", "Notification", "SessionEnd")
CODEX_EVENTS = ("SessionStart", "UserPromptSubmit", "Stop", "SubagentStop", "PermissionRequest", "SessionEnd")


def companion_home() -> Path:
    return Path(os.environ.get("ECHOSPEAK_COMPANION_HOME") or os.path.expanduser("~/.local/share/echospeak/companion"))


def hook_path() -> Path:
    return companion_home() / MARK


def python() -> str:
    return shutil.which("python3") or "/usr/bin/python3"


def command(agent: str) -> str:
    return f"{python()} {hook_path()} {agent}"


def _backup(path: Path) -> str:
    if not path.exists():
        return ""
    target = path.with_name(f"{path.name}.before-echo-companion-{time.strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(path, target)
    return str(target)


def _strip(groups: list[Any]) -> list[Any]:
    kept = []
    for group in groups if isinstance(groups, list) else []:
        hooks = [h for h in (group.get("hooks") or []) if MARK not in str(h.get("command", ""))] if isinstance(group, dict) else []
        if isinstance(group, dict) and hooks:
            kept.append({**group, "hooks": hooks})
        elif not isinstance(group, dict):
            kept.append(group)
    return kept


def _with_hooks(data: dict[str, Any], events: tuple[str, ...], agent: str, add: bool) -> dict[str, Any]:
    hooks = dict(data.get("hooks") or {})
    for event in set(events) | {k for k in hooks}:
        groups = _strip(hooks.get(event, []))
        if add and event in events:
            groups.append({"hooks": [{"type": "command", "command": command(agent), "timeout": 5}]})
        if groups:
            hooks[event] = groups
        else:
            hooks.pop(event, None)
    out = dict(data)
    if hooks:
        out["hooks"] = hooks
    else:
        out.pop("hooks", None)
    return out


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8") or "{}")
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a JSON object")
    return data


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".echo-companion.tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _codex_notify(text: str, add: bool) -> str:
    lines = [line for line in text.splitlines() if not (line.strip().startswith("notify") and MARK in line)]
    if add and not any(line.strip().startswith("notify") for line in lines):
        # Top-level keys must precede the first [table]; put it first.
        lines.insert(0, f'notify = ["{python()}", "{hook_path()}", "codex"]')
    return "\n".join(lines) + ("\n" if lines else "")


def plan(*, claude_dir: Path, codex_dir: Path, add: bool, agents: tuple[str, ...] = ("claude-code", "codex")) -> list[dict[str, Any]]:
    """The file changes install/uninstall would make, without making them."""

    changes = []
    if "claude-code" in agents:
        path = claude_dir / "settings.json"
        before = _read_json(path)
        after = _with_hooks(before, CLAUDE_EVENTS, "claude-code", add)
        changes.append({"path": path, "before": before, "after": after, "kind": "json"})
    if "codex" in agents:
        path = codex_dir / "hooks.json"
        before = _read_json(path)
        changes.append({"path": path, "before": before, "after": _with_hooks(before, CODEX_EVENTS, "codex", add), "kind": "json"})
        config = codex_dir / "config.toml"
        text = config.read_text(encoding="utf-8") if config.exists() else ""
        changes.append({"path": config, "before": text, "after": _codex_notify(text, add), "kind": "text"})
    return [c for c in changes if c["before"] != c["after"]]


def apply(changes: list[dict[str, Any]], *, add: bool, roots: list[str], agents: tuple[str, ...]) -> dict[str, Any]:
    backups = []
    home = companion_home()
    if add:
        home.mkdir(parents=True, exist_ok=True)
        os.chmod(home, 0o700)
        shutil.copy2(hook_client.__file__, hook_path())
        os.chmod(hook_path(), 0o700)
        scope_path = home / "scope.json"
        scope = _read_json(scope_path) if scope_path.exists() else {}
        scope.setdefault("enabled", True)
        scope.setdefault("capture_prompts", True)
        scope["agents"] = {**scope.get("agents", {}), **{a: True for a in agents}}
        scope["roots"] = sorted({*scope.get("roots", []), *roots})
        _write_json(scope_path, scope)
        os.chmod(scope_path, 0o600)
    for change in changes:
        backup = _backup(change["path"])
        if backup:
            backups.append(backup)
        if change["kind"] == "json":
            _write_json(change["path"], change["after"])
        else:
            change["path"].write_text(change["after"], encoding="utf-8")
    if not add:
        scope_path = home / "scope.json"
        if scope_path.exists():
            scope = _read_json(scope_path)
            scope["agents"] = {**scope.get("agents", {}), **{a: False for a in agents}}
            _write_json(scope_path, scope)
    return {"changed": [str(c["path"]) for c in changes], "backups": backups}


def status(*, claude_dir: Path, codex_dir: Path) -> dict[str, Any]:
    def has(path: Path) -> bool:
        try:
            return MARK in path.read_text(encoding="utf-8")
        except OSError:
            return False

    scope_path = companion_home() / "scope.json"
    scope = _read_json(scope_path) if scope_path.exists() else {}
    spool = companion_home() / "spool"
    return {
        "hook_installed": hook_path().is_file(),
        "claude_code": has(claude_dir / "settings.json"),
        "codex_hooks": has(codex_dir / "hooks.json"),
        "codex_notify": has(codex_dir / "config.toml"),
        "scope": scope,
        "spool_pending": len(list(spool.glob("*.json"))) if spool.is_dir() else 0,
    }


def main(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="companion_hooks", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=["status", "install", "uninstall"])
    parser.add_argument("--apply", action="store_true", help="make the changes (default: show them)")
    parser.add_argument("--agent", action="append", choices=["claude-code", "codex"], help="limit to one agent")
    parser.add_argument("--root", action="append", default=[], help="project folder to observe (repeatable)")
    parser.add_argument("--claude-dir", default=os.path.expanduser("~/.claude"))
    parser.add_argument("--codex-dir", default=os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex"))
    args = parser.parse_args(argv)
    claude_dir, codex_dir = Path(args.claude_dir), Path(args.codex_dir)
    agents = tuple(args.agent or ("claude-code", "codex"))
    if args.action == "status":
        print(json.dumps(status(claude_dir=claude_dir, codex_dir=codex_dir), indent=2))
        return 0
    add = args.action == "install"
    roots = [str(Path(os.path.expanduser(r)).resolve()) for r in args.root]
    if add and not roots:
        print("Choose at least one project folder to observe with --root.", file=sys.stderr)
        return 2
    changes = plan(claude_dir=claude_dir, codex_dir=codex_dir, add=add, agents=agents)
    for change in changes:
        print(f"--- {change['path']}")
        if change["kind"] == "json":
            print(json.dumps(change["after"].get("hooks", {}), indent=2))
        else:
            print(change["after"][:400])
    if add:
        print(f"hook: {hook_path()}  observe: {', '.join(roots)}  agents: {', '.join(agents)}")
    if not args.apply:
        print("(dry run: nothing changed; add --apply)")
        return 0
    print(json.dumps(apply(changes, add=add, roots=roots, agents=agents), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
