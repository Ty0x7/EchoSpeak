"""Native coding tools for the lean loop.

Echo codes itself instead of delegating: precise edits (exact-text replace,
the same primitive Claude Code and Codex use), line-numbered reads, and
project-wide search. Every write checkpoints the old file first, so
`checkpoint_undo` can roll it back.

All paths go through the same allowed-root checks as the legacy file tools.
"""

from __future__ import annotations
from agent.atomic_replace import replace as atomic_replace

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
    atomic_replace(temp, path)


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
# Exact text first. When that misses, the same whole lines are looked for again
# with Codex apply_patch's passes (codex-rs/apply-patch/src/seek_sequence.rs):
# ignoring trailing spaces, then indentation, then typographic quotes and dashes.
# Small local models often retype a block with one space more or less; an edit
# that matches exactly one place is applied, re-indented to the file's own
# indentation like Gemini CLI does. Nothing fuzzier: a near-miss could change
# the wrong lines.
_TYPOGRAPHY = str.maketrans({
    **{c: "-" for c in "‐‑‒–—―−"},
    **{c: "'" for c in "‘’‚‛"},
    **{c: '"' for c in "“”„‟"},
    **{c: " " for c in "            　"},
})
_LOOSE_PASSES: tuple[tuple[str, Any], ...] = (
    ("ignoring trailing spaces", str.rstrip),
    ("ignoring indentation", str.strip),
    ("ignoring indentation and quote or dash style", lambda line: line.strip().translate(_TYPOGRAPHY)),
)
# Misses per file, so a short file can be rewritten whole after a couple of them.
_MISSES: dict[str, int] = {}
SHORT_FILE_LINES = 200


def _indent(line: str) -> str:
    return line[: len(line) - len(line.lstrip())]


def _loose_match(text: str, old: str, new: str) -> tuple[Optional[str], str, int]:
    """(updated text, how it matched, places found) for an old_text that isn't in the file exactly."""
    pattern = old.splitlines()
    while pattern and not pattern[-1].strip():
        pattern.pop()
    while pattern and not pattern[0].strip():
        pattern.pop(0)
    if not pattern:
        return None, "", 0
    lines = text.splitlines(keepends=True)
    bare = [line.rstrip("\r\n") for line in lines]
    size = len(pattern)
    for label, norm in _LOOSE_PASSES:
        want = [norm(line) for line in pattern]
        found = [i for i in range(len(bare) - size + 1) if all(norm(bare[i + k]) == want[k] for k in range(size))]
        if not found:
            continue
        if len(found) > 1:
            return None, label, len(found)
        start = found[0]
        region = lines[start:start + size]
        eol = "\r\n" if region[0].endswith("\r\n") else "\n"
        ends_line = region[-1].endswith(("\n", "\r"))
        # Move the new lines from old_text's indentation to the file's.
        theirs = next(_indent(line) for line in pattern if line.strip())
        ours = next(_indent(bare[start + k]) for k, line in enumerate(pattern) if line.strip())
        body = []
        for line in new.splitlines():
            if line.strip() and theirs != ours and line.startswith(theirs):
                line = ours + line[len(theirs):]
            body.append(line)
        replacement = eol.join(body) + (eol if body and ends_line else "")
        return "".join(lines[:start]) + replacement + "".join(lines[start + size:]), label, 1
    return None, "", 0


def _visible(line: str) -> str:
    """Show a line's spacing: · for a space, → for a tab, at the start and the end."""
    core = line.strip(" \t")
    if not core:
        return line.replace(" ", "·").replace("\t", "→")
    lead, trail = _indent(line), line[len(line.rstrip(" \t")):]
    return lead.replace(" ", "·").replace("\t", "→") + core + trail.replace(" ", "·").replace("\t", "→")


def _miss_hint(text: str, old: str) -> str:
    """Where old_text comes closest, its first differing line next to the file's, and how to retry."""
    pattern = old.splitlines()
    while pattern and not pattern[0].strip():
        pattern.pop(0)
    if not pattern:
        return ""
    lines = text.splitlines()
    first = pattern[0].strip()
    starts = [i for i, line in enumerate(lines) if line.strip() == first]
    if not starts:
        starts = [i for i, line in enumerate(lines) if first and first in line]
    if not starts:
        close = difflib.get_close_matches(first, [line.strip() for line in lines], n=1, cutoff=0.75)
        starts = [i for i, line in enumerate(lines) if close and line.strip() == close[0]][:1]
    if not starts:
        return (f" Its first line ({first[:80]!r}) isn't in the file. Find the right place with file_search or "
                "file_read before trying again.")
    best_start, best_k = starts[0], -1
    for start in starts[:5]:
        k = 0
        while k < len(pattern) and start + k < len(lines) and lines[start + k].strip() == pattern[k].strip():
            k += 1
        if k > best_k:
            best_start, best_k = start, k
    k = best_k
    head = f" The closest match starts at line {best_start + 1}"
    if k >= len(pattern):
        detail = "."
    elif best_start + k >= len(lines):
        detail = f"; your text runs past the end of the file at its line {k + 1}."
    else:
        detail = (f"; the first difference is at line {best_start + k + 1}:\n"
                  f"  file: {_visible(lines[best_start + k])[:200]}\n"
                  f"  yours: {_visible(pattern[k])[:200]}")
    span_start = max(1, best_start + 1 - 3)
    span = len(pattern) + 6
    return (f"{head}{detail}\nRe-read lines {span_start}–{span_start + span - 1} (file_read offset={span_start}, "
            f"limit={span}) and copy the lines exactly. Replace 2–5 lines at a time, not a whole block.")


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
    key = str(target).casefold()
    if count == 0:
        updated, how, places = _loose_match(text, old, new)
        if updated is not None:
            _MISSES.pop(key, None)
            _atomic_write(target, updated, "file_edit")
            return (f"Edited {target} (1 replacement). old_text wasn't exact; it matched one place {how}, and your "
                    f"new text was fitted to the file's indentation. Check the diff:\n{_diff(text, updated, target.name)}")
        if places > 1:
            return (f"Error: old_text matches {places} places in {target.name} {how}. Include more surrounding lines "
                    "so it is unique.")
        _MISSES[key] = _MISSES.get(key, 0) + 1
        hint = _miss_hint(text, old)
        line_count = len(text.splitlines())
        if _MISSES[key] >= 2 and line_count <= SHORT_FILE_LINES:
            hint += (f"\nThis edit has missed {_MISSES[key]} times and {target.name} is short ({line_count} lines): "
                     "file_read it whole, then rewrite it with file_write, keeping everything you aren't changing.")
        return f"Error: old_text was not found in {target.name}.{hint}"
    if count > 1 and not replace_all:
        return f"Error: old_text appears {count} times in {target.name}. Include more surrounding lines so it is unique, or set replace_all=true."
    updated = text.replace(old, new) if replace_all else text.replace(old, new, 1)
    _MISSES.pop(key, None)
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
                "Change part of an existing file by replacing exact text. file_read it first. old_text must match "
                "the file exactly, including each line's indentation after the line-number prefix (leave the prefix "
                "out), and be unique unless replace_all is true. Keep it small: the 2-5 lines you are changing, not "
                "a whole function. If it misses, re-read just those lines with offset/limit and copy them again. "
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
