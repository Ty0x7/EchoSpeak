"""Native coding tools for the lean loop.

Echo codes itself instead of delegating: precise edits (exact-text replace,
the same primitive Claude Code and Codex use), line-numbered reads, and
project-wide search. Every write checkpoints the old file first, so
`checkpoint_undo` can roll it back.

All paths go through the same allowed-root checks as the legacy file tools.
"""

from __future__ import annotations

import difflib
import fnmatch
import os
import re
import uuid
from pathlib import Path
from typing import Any, Optional

from agent.lean.toolbox import NativeTool

SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", "target",
    ".next", ".nuxt", ".cache", ".pytest_cache", ".mypy_cache", ".idea", ".vscode",
    "coverage", ".turbo", ".parcel-cache", ".svelte-kit",
}
MAX_FILE_BYTES = 2_000_000


def _resolve(path: str) -> tuple[Optional[Path], str]:
    from agent.tools import _format_file_tool_roots, _safe_file_path

    target = _safe_file_path(str(path or "").strip() or ".")
    if target is None:
        return None, f"Error: path not allowed: {path}. Allowed folders: {_format_file_tool_roots()}"
    return target, ""


def _read_text(path: Path) -> tuple[Optional[str], str]:
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return None, f"Error: {path.name} is larger than 2 MB; read a slice with offset/limit via file_search first."
        data = path.read_bytes()
    except FileNotFoundError:
        return None, f"Error: file not found: {path}"
    except Exception as exc:
        return None, f"Error: could not read {path}: {exc}"
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):  # UTF-16 with a BOM, e.g. from Windows PowerShell `>`
        try:
            return data.decode("utf-16"), ""
        except UnicodeDecodeError:
            pass
    if b"\x00" in data[:4096]:
        return None, f"Error: {path.name} looks like a binary file."
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding), ""
        except UnicodeDecodeError:
            continue
    return None, f"Error: {path.name} is not valid text."


def _atomic_write(path: Path, text: str, reason: str) -> None:
    try:
        from agent.checkpoints import create_checkpoint

        if path.exists():
            create_checkpoint(str(path), reason=reason)
    except Exception:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    with open(temp, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)
    os.replace(temp, path)


def _diff(before: str, after: str, name: str, limit: int = 60) -> str:
    lines = list(difflib.unified_diff(
        before.splitlines(), after.splitlines(), fromfile=f"a/{name}", tofile=f"b/{name}", lineterm="", n=2,
    ))
    if len(lines) > limit:
        lines = lines[:limit] + [f"… ({len(lines) - limit} more diff lines)"]
    return "\n".join(lines)


# ── file_read ───────────────────────────────────────────────────────────
def file_read(args: dict[str, Any]) -> str:
    target, error = _resolve(args.get("path") or args.get("file") or "")
    if error:
        return error
    if target.is_dir():
        return f"Error: {target} is a folder. Use file_list or file_find."
    text, error = _read_text(target)
    if error:
        return error
    lines = text.splitlines()
    try:
        offset = max(1, int(args.get("offset") or 1))
        limit = max(1, min(int(args.get("limit") or 400), 2000))
    except (TypeError, ValueError):
        offset, limit = 1, 400
    chunk = lines[offset - 1: offset - 1 + limit]
    width = len(str(offset + len(chunk)))
    body = "\n".join(f"{str(n).rjust(width)}| {line}" for n, line in enumerate(chunk, start=offset))
    tail = ""
    if offset - 1 + limit < len(lines):
        tail = f"\n… {len(lines) - (offset - 1 + limit)} more lines. Continue with offset={offset + limit}."
    return f"{target} ({len(lines)} lines)\n{body}{tail}"


# ── file_edit ───────────────────────────────────────────────────────────
def file_edit(args: dict[str, Any]) -> str:
    target, error = _resolve(args.get("path") or "")
    if error:
        return error
    old = str(args.get("old_text") if args.get("old_text") is not None else args.get("old_string") or "")
    new = str(args.get("new_text") if args.get("new_text") is not None else args.get("new_string") or "")
    replace_all = bool(args.get("replace_all", False))
    if not target.exists():
        return f"Error: {target} does not exist. Use file_write to create a new file."
    text, error = _read_text(target)
    if error:
        return error
    if not old:
        return "Error: old_text is empty. Copy the exact lines you want to change from file_read (without the line-number prefix)."
    if old == new:
        return "Error: old_text and new_text are identical; nothing to change."
    # Models sometimes paste line-number prefixes from file_read; strip them.
    if old not in text and re.search(r"^\s*\d+\| ", old, re.M):
        old = re.sub(r"(?m)^\s*\d+\| ", "", old)
        new = re.sub(r"(?m)^\s*\d+\| ", "", new)
    # Tolerate Windows line endings.
    if old not in text and "\r\n" in text:
        old_crlf, new_crlf = old.replace("\r\n", "\n").replace("\n", "\r\n"), new.replace("\r\n", "\n").replace("\n", "\r\n")
        if old_crlf in text:
            old, new = old_crlf, new_crlf
    count = text.count(old)
    if count == 0:
        hint = ""
        first = next((line.strip() for line in old.splitlines() if line.strip()), "")
        if first:
            matches = [i + 1 for i, line in enumerate(text.splitlines()) if first in line]
            if matches:
                hint = f" The first line appears near line(s) {matches[:5]}; re-read those lines and copy them exactly (spacing matters)."
        return f"Error: old_text was not found in {target.name}.{hint}"
    if count > 1 and not replace_all:
        return f"Error: old_text appears {count} times in {target.name}. Include more surrounding lines so it is unique, or set replace_all=true."
    updated = text.replace(old, new) if replace_all else text.replace(old, new, 1)
    _atomic_write(target, updated, "file_edit")
    changed = count if replace_all else 1
    return f"Edited {target} ({changed} replacement{'s' if changed != 1 else ''}).\n{_diff(text, updated, target.name)}"


# ── file_search ─────────────────────────────────────────────────────────
def _walk(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".") or d in {".github"}]
        for name in filenames:
            yield Path(dirpath) / name


def file_search(args: dict[str, Any]) -> str:
    pattern = str(args.get("pattern") or args.get("query") or "")
    if not pattern:
        return "Error: give a pattern to search for."
    root, error = _resolve(args.get("path") or ".")
    if error:
        return error
    glob = str(args.get("file_glob") or args.get("glob") or "").strip()
    try:
        regex = re.compile(pattern if args.get("regex", True) else re.escape(pattern), 0 if args.get("case_sensitive") else re.I)
    except re.error:
        regex = re.compile(re.escape(pattern), re.I)
    limit = max(1, min(int(args.get("max_results") or 80), 300))
    hits: list[str] = []
    files = [root] if root.is_file() else _walk(root)
    scanned = 0
    for path in files:
        if glob and not fnmatch.fnmatch(path.name, glob) and not fnmatch.fnmatch(str(path), glob):
            continue
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
            data = path.read_bytes()
        except Exception:
            continue
        if b"\x00" in data[:2048]:
            continue
        scanned += 1
        text = data.decode("utf-8", errors="ignore")
        for number, line in enumerate(text.splitlines(), start=1):
            if regex.search(line):
                rel = path.relative_to(root) if root.is_dir() else path.name
                hits.append(f"{rel}:{number}: {line.strip()[:220]}")
                if len(hits) >= limit:
                    break
        if len(hits) >= limit or scanned > 20000:
            break
    if not hits:
        return f"No matches for {pattern!r} in {root} ({scanned} files searched)."
    more = f"\n… stopped at {limit} matches; narrow the pattern or path." if len(hits) >= limit else ""
    return f"Matches in {root}:\n" + "\n".join(hits) + more


# ── file_find ───────────────────────────────────────────────────────────
def file_find(args: dict[str, Any]) -> str:
    pattern = str(args.get("pattern") or args.get("glob") or "*").strip()
    root, error = _resolve(args.get("path") or ".")
    if error:
        return error
    if not root.is_dir():
        return f"Error: {root} is not a folder."
    found: list[str] = []
    for path in _walk(root):
        rel = str(path.relative_to(root)).replace("\\", "/")
        if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(path.name, pattern):
            found.append(rel)
            if len(found) >= 300:
                break
    if not found:
        return f"No files matching {pattern!r} under {root}."
    return f"{len(found)} file(s) under {root}:\n" + "\n".join(sorted(found))


def project_overview(root: str, limit: int = 40) -> str:
    """A compact top-level listing of the attached project for the system prompt."""
    try:
        base = Path(root)
        entries = sorted(base.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except Exception:
        return ""
    rows: list[str] = []
    for entry in entries:
        if entry.name in SKIP_DIRS or entry.name.startswith(".") and entry.name not in {".env.example", ".github"}:
            continue
        if entry.is_dir():
            try:
                count = sum(1 for _ in entry.iterdir())
            except Exception:
                count = 0
            rows.append(f"  {entry.name}/ ({count} items)")
        else:
            rows.append(f"  {entry.name}")
        if len(rows) >= limit:
            rows.append("  …")
            break
    return "\n".join(rows)


def coding_tools() -> list[NativeTool]:
    return [
        NativeTool(
            name="file_read",
            description="Read a text file with line numbers. Use offset/limit for long files. Read before you edit.",
            parameters={"type": "object", "properties": {
                "path": {"type": "string"},
                "offset": {"type": "integer", "description": "First line to show (1-based)."},
                "limit": {"type": "integer", "description": "Number of lines (default 400)."},
            }, "required": ["path"]},
            func=file_read,
            parallel_safe=True,
        ),
        NativeTool(
            name="file_edit",
            description=(
                "Change part of an existing file by replacing exact text. old_text must match the file exactly "
                "(copy it from file_read without the line-number prefix) and be unique unless replace_all is true. "
                "Prefer this over rewriting whole files."
            ),
            parameters={"type": "object", "properties": {
                "path": {"type": "string"},
                "old_text": {"type": "string", "description": "Exact text currently in the file."},
                "new_text": {"type": "string", "description": "Text to put in its place."},
                "replace_all": {"type": "boolean"},
            }, "required": ["path", "old_text", "new_text"]},
            func=file_edit,
        ),
        NativeTool(
            name="file_search",
            description="Search file contents under a folder (regex, case-insensitive). Returns path:line: text. Skips node_modules, .git, build folders.",
            parameters={"type": "object", "properties": {
                "pattern": {"type": "string"},
                "path": {"type": "string", "description": "Folder or file to search (default: project folder)."},
                "file_glob": {"type": "string", "description": "Only files matching, e.g. *.py"},
            }, "required": ["pattern"]},
            func=file_search,
            parallel_safe=True,
        ),
        NativeTool(
            name="file_find",
            description="Find files by name pattern under a folder, e.g. **/*.tsx or package.json.",
            parameters={"type": "object", "properties": {
                "pattern": {"type": "string"},
                "path": {"type": "string"},
            }, "required": ["pattern"]},
            func=file_find,
            parallel_safe=True,
        ),
    ]
