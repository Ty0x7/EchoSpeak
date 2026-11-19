"""What stays of a file the agent read once the context window can't hold it any more.

A 16K window holds the system prompt, the tools and about one source file. When a turn works across several files
(the owner's GTA clone, 19 November 2026: five files of 30-180 lines), every earlier read was trimmed to its first
250-1,500 characters, so the model read the same files again and again: 37 of 49 steps were re-reads, each one
followed by another compaction. The head of a file is the least useful part to keep. Its shape is the useful part:
how long it is and which classes and functions start on which line. With that, the model can read the twenty lines
it needs (offset/limit) instead of the whole file again.

Everything here works on the tool result's own text (``coding.file_read``'s "<path> (N lines)" header and numbered
lines), so it's deterministic and never touches the disk.
"""
from __future__ import annotations

import re
from typing import Optional

# "<path> (<N> lines)" then "<n>| <text>" lines: agent/lean/coding.py file_read.
_HEADER = re.compile(r"^(?P<path>.+?) \((?P<lines>\d+) lines\)\s*$")
_NUMBERED = re.compile(r"^\s*(?P<n>\d+)\| ?(?P<text>.*)$")

# A definition worth naming, by family. Indented methods count (that is where a class's work is), control-flow
# keywords don't.
_DEFINITIONS = [
    # Python
    re.compile(r"^\s*(?:async\s+)?def\s+(?P<name>\w+)\s*\("),
    re.compile(r"^\s*class\s+(?P<name>\w+)"),
    # JavaScript / TypeScript
    re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+(?P<name>\w+)"),
    re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s*\*?\s*(?P<name>\w+)\s*\("),
    re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+(?P<name>\w+)\s*=\s*(?:async\s*)?(?:function\b|\([^)]*\)\s*=>|\w+\s*=>)"),
    re.compile(r"^\s*(?:static\s+|async\s+|get\s+|set\s+)*(?P<name>[A-Za-z_$][\w$]*)\s*\([^)]*\)\s*\{\s*$"),
    # Go, Rust, shell
    re.compile(r"^\s*func\s+(?:\([^)]*\)\s*)?(?P<name>\w+)\s*\("),
    re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?fn\s+(?P<name>\w+)"),
    re.compile(r"^\s*(?:pub\s+)?(?:struct|enum|trait|impl)\s+(?P<name>\w+)"),
    re.compile(r"^\s*(?:function\s+)?(?P<name>[\w-]+)\s*\(\)\s*\{"),
]
_NOT_NAMES = {"if", "for", "while", "switch", "catch", "return", "else", "do", "try", "with", "function", "new"}
# HTML: what the page loads and what it's built from.
_HTML_SCRIPT = re.compile(r"""<script[^>]*\bsrc\s*=\s*["']([^"']+)["']""", re.I)
_HTML_ID = re.compile(r"""<(\w+)[^>]*\bid\s*=\s*["']([^"']+)["']""", re.I)
_HTML_INLINE = re.compile(r"<script(?![^>]*\bsrc\b)[^>]*>", re.I)


def parse(content: str) -> Optional[tuple[str, int, list[tuple[int, str]]]]:
    """(path, total lines, numbered lines) from a file_read result, or None if it isn't one."""
    lines = str(content or "").splitlines()
    if not lines:
        return None
    head = _HEADER.match(lines[0].strip())
    if not head:
        return None
    numbered: list[tuple[int, str]] = []
    for line in lines[1:]:
        match = _NUMBERED.match(line)
        if match:
            numbered.append((int(match.group("n")), match.group("text")))
    if not numbered:
        return None
    return head.group("path"), int(head.group("lines")), numbered


def outline(numbered: list[tuple[int, str]], path: str = "", limit: int = 24) -> list[str]:
    """'L12 class Game', 'L30 update()' … for the definitions in what was read; HTML: scripts and element ids."""
    entries: list[str] = []
    html = path.lower().endswith((".html", ".htm"))
    for n, text in numbered:
        if html:
            for src in _HTML_SCRIPT.findall(text):
                entries.append(f"L{n} script {src}")
            if _HTML_INLINE.search(text):
                entries.append(f"L{n} inline script")
            for tag, ident in _HTML_ID.findall(text):
                entries.append(f"L{n} <{tag.lower()} id={ident}>")
        else:
            for pattern in _DEFINITIONS:
                match = pattern.match(text)
                if not match or match.group("name") in _NOT_NAMES:
                    continue
                kind = "class " if re.match(r"^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\b", text) else ""
                entries.append(f"L{n} {kind}{match.group('name')}" + ("" if kind else "()"))
                break
        if len(entries) >= limit:
            entries.append("…")
            break
    return entries


def summary(content: str, note: str, max_chars: int = 900) -> Optional[str]:
    """The trimmed form of a file_read result: path, size, the range read and its outline. None if not a read."""
    parsed = parse(content)
    if parsed is None:
        return None
    path, total, numbered = parsed
    first, last = numbered[0][0], numbered[-1][0]
    span = "all of it" if first == 1 and last >= total else f"lines {first}-{last}"
    entries = outline(numbered, path)
    text = f"{path} ({total} lines; you read {span}) …[{note}]"
    if entries:
        shown = ""
        for entry in entries:
            candidate = (shown + " · " if shown else "") + entry
            if len(text) + len(candidate) + 120 > max_chars:
                shown += " · …"
                break
            shown = candidate
        text += f"\nOutline: {shown}"
    text += "\nTo look at code again, read only the lines you need (file_read with offset and limit)."
    return text if len(text) < len(content) else None


_SUMMARY = re.compile(r"^.+ \(\d+ lines; you read [^)]*\) …\[")


def is_summary(content: str) -> bool:
    """True for text summary() wrote (a trimmed read), so it isn't cut down to its first characters."""
    return bool(_SUMMARY.match(str(content or "")))


def read_path(content: str) -> str:
    """The path a file_read result is for, or ''."""
    parsed = parse(content)
    return parsed[0] if parsed else ""
