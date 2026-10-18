"""Portable, stdlib-only project handoff publisher and optional lifecycle receipt hook.

No API, credentials, agent subprocesses or model calls. Never installs hooks itself.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import sys
import time
import uuid


def inside(root: Path, relative: str) -> Path:
    raw = relative.replace("\\", "/")
    if raw.startswith("/") or PureWindowsPath(raw).drive or any(p in {"..", ""} for p in raw.split("/")):
        raise ValueError("Use a project-relative path")
    path = root / raw
    current = root
    for part in raw.split("/"):
        current /= part
        if current.is_symlink() or (hasattr(current, "is_junction") and current.is_junction()):
            raise ValueError("Linked handoff paths are not supported")
    if not path.resolve().is_relative_to(root):
        raise ValueError("Path leaves project")
    return path


def write(path: Path, value: dict):
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temp.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--manifest", help="Project-relative JSON manifest to publish atomically")
    parser.add_argument("--agent", choices=["codex", "claude-code", "echo", "other"], default="other")
    parser.add_argument("--hook", action="store_true", help="Read a lifecycle event; record a receipt, never task completion")
    parser.add_argument("notification", nargs="?", help="Legacy Codex notify JSON argument; hooks otherwise use stdin")
    args = parser.parse_args(argv)
    try:
        root = Path(args.project).expanduser().resolve(strict=True)
        binding_path = inside(root, ".echospeak/project.json")
        binding = json.loads(binding_path.read_text(encoding="utf-8"))
        project_id = binding.get("project_id")
        if not isinstance(project_id, str) or not project_id:
            raise ValueError("Project binding is missing its project_id")
        if args.hook:
            raw = args.notification if args.notification is not None else sys.stdin.read(64001)
            if len(raw) > 64000:
                raise ValueError("Hook input too large")
            value = json.loads(raw)
            event = value.get("hook_event_name") or value.get("type")
            if event not in {"Stop", "StopFailure", "Interrupt", "SessionEnd", "agent-turn-complete"}:
                return 0
            cwd = Path(value.get("cwd") or root).resolve()
            if not cwd.is_relative_to(root):
                return 0
            session = str(value.get("session_id") or value.get("thread-id") or "")[:150]
            turn = str(value.get("turn_id") or value.get("turn-id") or "")[:150]
            # No transcript paths, assistant text, API errors or secrets are copied.
            receipt = {"schema_version": 1, "project_id": project_id, "agent": args.agent, "session_id": session,
                       "turn_id": turn, "event": event, "observed_at": time.time(), "status": "interrupted" if event in {"Interrupt", "StopFailure"} else "needs_review"}
            # Some hosts omit a turn ID. Fingerprint the event for duplicate receipts,
            # without retaining its assistant text or transcript path.
            dedup = turn or hashlib.sha256(raw.encode()).hexdigest()
            identity = hashlib.sha256(f"{args.agent}:{session}:{dedup}:{event}".encode()).hexdigest()[:24]
            destination = inside(root, f".echospeak/events/{identity}.json")
            if not destination.exists():
                write(destination, receipt)
            receipts = [path for path in destination.parent.glob("*.json") if len(path.stem) == 24 and all(c in "0123456789abcdef" for c in path.stem)][:1000]
            receipts.sort(key=lambda path: path.stat().st_mtime, reverse=True)
            for old in receipts[200:]:
                inside(root, old.relative_to(root).as_posix()).unlink(missing_ok=True)
            # Successful Stop hooks require JSON and must not request continuation.
            print("{}")
            return 0
        if not args.manifest:
            parser.error("Choose --manifest or --hook")
        source = inside(root, args.manifest)
        if source.stat().st_size > 64000:
            raise ValueError("Manifest exceeds 64 KB")
        value = json.loads(source.read_text(encoding="utf-8-sig"))
        import re
        if value.get("project_id") != project_id:
            raise ValueError("Manifest project_id does not match shared folder")
        record_id = str(value.get("id") or "")
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}", record_id):
            raise ValueError("Manifest id is invalid")
        revision = value.get("revision", 1)
        if type(revision) is not int or not 1 <= revision <= 1_000_000:
            raise ValueError("Manifest revision is invalid")
        # Stable inbox filenames avoid filling the scan with already imported revisions.
        destination = inside(root, f".echospeak/handoffs/{record_id}.json")
        if destination.exists():
            previous = json.loads(destination.read_text(encoding="utf-8"))
            if previous.get("revision", 1) > revision:
                raise ValueError("A newer revision is already published")
            if previous.get("revision", 1) == revision and previous != value:
                raise ValueError("A different manifest already uses this id and revision")
        write(destination, value)
        print(destination.relative_to(root).as_posix())
        return 0
    except (OSError, ValueError, TypeError) as exc:
        print(f"Project handoff: {exc}", file=sys.stderr)
        # A notification failure should not block or restart the independent agent.
        if args.hook:
            print("{}")
            return 0
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
