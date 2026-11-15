"""Structured file changes for clients that preview edits (the terminal's diff blocks, approval diffs).

A ``file_change`` record says what a file tool changed (``status: applied``, on ``tool_end.meta``) or
would change (``status: proposed``, on ``approval_request``), with numbered hunks instead of the
model-facing difflib text. It is bounded and redacted, and building it never changes anything:

- a snapshot is read before the tool runs and the record is computed from the file on disk after it,
  so it shows what really happened (not what the tool said);
- a proposal is computed from the call's arguments and the current file, as a dry run.

Fields: change_id, tool_call_id, op (create | edit | write | delete | move), path (absolute),
display_path (relative to the project or file root), move_from, language, hunks
[{old_start, old_lines, new_start, new_lines, lines: [[tag, text]]}] with tag " ", "-" or "+" and 3
lines of context, added, removed, total_lines, truncated, too_large, binary, withheld_reason, status,
and base_sha256 (proposals: the file the preview was computed from).
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from loguru import logger

TRACKED = frozenset({"file_edit", "file_write", "file_delete", "file_move"})
CONTEXT = 3
MAX_DIFF_LINES = 400          # rows across all hunks (Claude Code's MAX_LINES_PER_FILE)
MAX_LINE_CHARS = 200
MAX_RECORD_BYTES = 64 * 1024
MAX_READ_BYTES = 2_000_000    # coding.MAX_FILE_BYTES: larger files are not read for a preview
MAX_DIFF_BYTES = 512 * 1024   # either side larger than this: too_large (difflib is quadratic at worst)

LANGUAGES = {
    ".py": "python", ".pyi": "python", ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".jsx": "jsx", ".ts": "typescript", ".tsx": "tsx", ".json": "json", ".md": "markdown", ".rs": "rust",
    ".go": "go", ".java": "java", ".kt": "kotlin", ".c": "c", ".h": "c", ".cc": "cpp", ".cpp": "cpp",
    ".hpp": "cpp", ".cs": "csharp", ".rb": "ruby", ".php": "php", ".sh": "bash", ".bash": "bash", ".zsh": "bash",
    ".fish": "fish", ".ps1": "powershell", ".html": "html", ".htm": "html", ".css": "css", ".scss": "scss",
    ".toml": "toml", ".yaml": "yaml", ".yml": "yaml", ".ini": "ini", ".cfg": "ini", ".sql": "sql", ".lua": "lua",
    ".swift": "swift", ".vue": "vue", ".svelte": "svelte", ".xml": "xml", ".txt": "text", ".csv": "text",
}
_NAMES = {"dockerfile": "docker", "makefile": "make", "cmakelists.txt": "cmake"}


def language_of(path: str | Path) -> str:
    p = Path(str(path))
    return _NAMES.get(p.name.lower()) or LANGUAGES.get(p.suffix.lower(), "")


def display_path(path: Path, root: str = "") -> str:
    """Project-relative when inside the project (or file root), else ~-relative, else absolute."""
    for base in (root, _file_root()):
        if not base:
            continue
        try:
            rel = path.relative_to(Path(base).expanduser().resolve())
            text = rel.as_posix()
            if text and text != ".":
                return text
        except (ValueError, OSError):
            continue
    try:
        home = Path.home().resolve()
        return "~/" + path.relative_to(home).as_posix()
    except (ValueError, OSError, RuntimeError):
        return str(path)


def _file_root() -> str:
    try:
        from agent.tools import _file_tool_root

        return str(_file_tool_root())
    except Exception:
        return ""


# ── reading ──────────────────────────────────────────────────────────
@dataclass
class Side:
    exists: bool = False
    text: Optional[str] = None
    binary: bool = False
    too_large: bool = False
    folder: bool = False
    sha256: str = ""


def read_side(path: Optional[Path]) -> Side:
    if path is None:
        return Side()
    try:
        if not path.exists():
            return Side()
        if path.is_dir():
            return Side(exists=True, folder=True)
        size = path.stat().st_size
        if size > MAX_READ_BYTES:
            return Side(exists=True, too_large=True)
        data = path.read_bytes()
    except OSError:
        return Side(exists=True, too_large=True)
    digest = hashlib.sha256(data).hexdigest()
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        try:
            return Side(True, data.decode("utf-16"), sha256=digest)
        except UnicodeDecodeError:
            pass
    if b"\x00" in data[:4096]:
        return Side(exists=True, binary=True, sha256=digest)
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return Side(True, data.decode(encoding), sha256=digest)
        except UnicodeDecodeError:
            continue
    return Side(exists=True, binary=True, sha256=digest)


# ── the record ───────────────────────────────────────────────────────
def _clip(line: str) -> tuple[str, bool]:
    line = line.rstrip("\r\n")
    if len(line) > MAX_LINE_CHARS:
        return line[:MAX_LINE_CHARS] + "…", True
    return line, False


def hunks_of(before: str, after: str, context: int = CONTEXT) -> tuple[list[dict[str, Any]], int, int, bool]:
    """(hunks, added, removed, truncated). Counts cover the whole change even when the hunks are capped."""
    a, b = before.splitlines(), after.splitlines()
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    added = removed = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in {"replace", "delete"}:
            removed += i2 - i1
        if tag in {"replace", "insert"}:
            added += j2 - j1
    hunks: list[dict[str, Any]] = []
    rows = 0
    size = 0
    truncated = False
    for group in matcher.get_grouped_opcodes(context):
        if rows >= MAX_DIFF_LINES or size >= MAX_RECORD_BYTES - 4096:
            truncated = True
            break
        first, last = group[0], group[-1]
        hunk: dict[str, Any] = {"old_start": first[1] + 1, "old_lines": last[2] - first[1],
                                "new_start": first[3] + 1, "new_lines": last[4] - first[3], "lines": []}
        if hunk["old_lines"] == 0:
            hunk["old_start"] = first[1]      # unified-diff convention for an empty side
        if hunk["new_lines"] == 0:
            hunk["new_start"] = first[3]
        out = hunk["lines"]
        for tag, i1, i2, j1, j2 in group:
            pieces: list[tuple[str, str]] = []
            if tag == "equal":
                pieces = [(" ", x) for x in a[i1:i2]]
            else:
                pieces = [("-", x) for x in a[i1:i2]] + [("+", x) for x in b[j1:j2]]
            for sign, text in pieces:
                if rows >= MAX_DIFF_LINES or size >= MAX_RECORD_BYTES - 4096:
                    truncated = True
                    break
                clipped, cut = _clip(text)
                truncated = truncated or cut
                out.append([sign, clipped])
                rows += 1
                size += len(clipped.encode("utf-8", "replace")) + 8
        if out:
            hunks.append(hunk)
    return hunks, added, removed, truncated


def build(*, path: Path, op: str, before: Side, after: Side, root: str = "", status: str = "applied",
          move_from: Optional[Path] = None, withheld_reason: str = "") -> dict[str, Any]:
    record: dict[str, Any] = {
        "change_id": "chg_" + uuid.uuid4().hex[:12],
        "op": op,
        "path": str(path),
        "display_path": display_path(path, root),
        "language": language_of(path),
        "hunks": [],
        "added": 0,
        "removed": 0,
        "total_lines": 0,
        "truncated": False,
        "too_large": bool(before.too_large or after.too_large),
        "binary": bool(before.binary or after.binary),
        "status": status,
    }
    if move_from is not None:
        record["move_from"] = display_path(move_from, root)
    reason = withheld_reason
    if before.folder or after.folder:
        reason = reason or "a folder"
    elif record["binary"]:
        reason = reason or "a binary file"
    elif record["too_large"]:
        reason = reason or "too large to show"
    if not reason:
        old, new = before.text or "", after.text or ""
        if len(old.encode("utf-8", "replace")) > MAX_DIFF_BYTES or len(new.encode("utf-8", "replace")) > MAX_DIFF_BYTES:
            record["too_large"] = True
            reason = "too large to show"
        else:
            hunks, added, removed, truncated = hunks_of(old, new)
            record.update(hunks=hunks, added=added, removed=removed, truncated=truncated,
                          total_lines=len(new.splitlines()) if op != "delete" else len(old.splitlines()))
    if reason:
        record["withheld_reason"] = reason
    if status == "proposed":
        record["base_sha256"] = before.sha256
    return _safe(record)


def _safe(record: dict[str, Any]) -> dict[str, Any]:
    """Redact configured secrets, then hold the record to its byte budget."""
    try:
        from agent.lean.policy import redact_payload

        record = redact_payload(record)
    except Exception:
        logger.debug("file_change redaction failed; withholding the diff", exc_info=True)
        record = {**record, "hunks": [], "withheld_reason": "could not be checked for secrets"}
    while record["hunks"] and len(json.dumps(record, ensure_ascii=False).encode("utf-8")) > MAX_RECORD_BYTES:
        record["hunks"] = record["hunks"][:-1]
        record["truncated"] = True
    return record


# ── applied changes: snapshot before, record after ───────────────────
@dataclass
class Snapshot:
    name: str
    path: Optional[Path]
    before: Side = field(default_factory=Side)
    source: Optional[Path] = None          # file_move
    source_before: Side = field(default_factory=Side)


def _resolve(raw: Any) -> Optional[Path]:
    value = str(raw or "").strip()
    if not value:
        return None
    try:
        from agent.tools import _safe_file_path

        return _safe_file_path(value)
    except Exception:
        return None


def snapshot(name: str, args: dict[str, Any]) -> Optional[Snapshot]:
    """The file(s) a tracked call touches, read before it runs. Call inside the tool context."""
    if name not in TRACKED or not isinstance(args, dict):
        return None
    if name == "file_move":
        src = _resolve(args.get("src") or args.get("source"))
        dst = _resolve(args.get("dst") or args.get("destination"))
        if dst is None:
            return None
        return Snapshot(name, dst, read_side(dst), src, read_side(src))
    path = _resolve(args.get("path") or args.get("file_path"))
    if path is None:
        return None
    return Snapshot(name, path, read_side(path))


def applied(snap: Optional[Snapshot], root: str = "") -> Optional[dict[str, Any]]:
    """What the call changed on disk. None when nothing changed or it can't be told."""
    if snap is None or snap.path is None:
        return None
    try:
        after = read_side(snap.path)
        if snap.name == "file_move":
            return build(path=snap.path, op="move", before=snap.source_before, after=after, root=root,
                         move_from=snap.source)
        if snap.name == "file_delete":
            if after.exists:
                return None
            return build(path=snap.path, op="delete", before=snap.before, after=Side(), root=root)
        if not after.exists:
            return None
        if snap.before.exists and snap.before.sha256 and snap.before.sha256 == after.sha256:
            return None   # nothing changed
        op = "edit" if snap.name == "file_edit" else ("write" if snap.before.exists else "create")
        return build(path=snap.path, op=op, before=snap.before, after=after, root=root)
    except Exception:
        logger.debug("file_change for {} failed", snap.name, exc_info=True)
        return None


# ── proposed changes: a dry run for the approval card ────────────────
def _proposed_edit(text: str, args: dict[str, Any]) -> tuple[Optional[str], str]:
    old = str(args.get("old_text") if args.get("old_text") is not None else args.get("old_string") or "")
    new = str(args.get("new_text") if args.get("new_text") is not None else args.get("new_string") or "")
    if not old:
        return None, "the edit names no text to replace"
    if old not in text and re.search(r"^\s*\d+\| ", old, re.M):
        old = re.sub(r"(?m)^\s*\d+\| ", "", old)
        new = re.sub(r"(?m)^\s*\d+\| ", "", new)
    if old not in text and "\r\n" in text:
        crlf_old = old.replace("\r\n", "\n").replace("\n", "\r\n")
        if crlf_old in text:
            old, new = crlf_old, new.replace("\r\n", "\n").replace("\n", "\r\n")
    count = text.count(old)
    if count == 1 or (count > 1 and args.get("replace_all")):
        return (text.replace(old, new) if args.get("replace_all") else text.replace(old, new, 1)), ""
    if count > 1:
        return None, f"the text to replace appears {count} times; the edit will ask for a unique match"
    try:
        from agent.lean.coding import _loose_match

        updated, _how, places = _loose_match(text, old, new)
    except Exception:
        updated, places = None, 0
    if updated is not None:
        return updated, ""
    if places > 1:
        return None, "the text to replace matches several places"
    return None, "the text to replace wasn't found, so the edit will fail without changing the file"


def lost_work(record: Optional[dict[str, Any]]) -> str:
    """Why a proposed write or edit would destroy existing work, or "" when it wouldn't.

    Ordinary edits don't ask in the default approval mode, but the harness promises the model (and
    the owner) that deleting or overwriting work does. An empty write is a delete by another name: in
    the harness evaluation the model, refused `rm old_report.txt`, wrote "" to it instead, and an
    "overwrite budget.csv with an empty file" request ran without a card.
    """
    if not record or record.get("op") not in {"write", "edit"} or record.get("status") != "proposed":
        return ""
    name = record.get("display_path") or record.get("path") or "the file"
    if record.get("op") == "write" and (record.get("binary") or record.get("too_large")):
        return f"this overwrites {name}, which is too large or not text to show the change"
    added, removed, total = int(record.get("added") or 0), int(record.get("removed") or 0), int(record.get("total_lines") or 0)
    before = total - added + removed
    if before <= 0 or record.get("withheld_reason"):
        return ""
    if total == 0:
        return f"this empties {name} ({before} line{'s' if before != 1 else ''} now)"
    if removed >= 5 and removed >= 0.5 * before and removed > 2 * added:
        return f"this removes {removed} of the {before} lines in {name}"
    return ""


def proposed(name: str, args: dict[str, Any], root: str = "") -> Optional[dict[str, Any]]:
    """What this call would change, computed from its arguments and the current file. Never writes."""
    if name not in TRACKED or not isinstance(args, dict):
        return None
    try:
        if name == "file_move":
            src = _resolve(args.get("src") or args.get("source"))
            dst = _resolve(args.get("dst") or args.get("destination"))
            if dst is None:
                return None
            side = read_side(src)
            return build(path=dst, op="move", before=side, after=side, root=root, status="proposed", move_from=src)
        path = _resolve(args.get("path") or args.get("file_path"))
        if path is None:
            return None
        before = read_side(path)
        if name == "file_delete":
            return build(path=path, op="delete", before=before, after=Side(), root=root, status="proposed")
        if name == "file_write":
            try:
                from agent.tools import strip_echo_file_wrapper

                body = strip_echo_file_wrapper(str(args.get("content") or ""))
            except Exception:
                body = str(args.get("content") or "")
            new_text = ((before.text or "") + body) if args.get("append") and before.exists else body
            op = "write" if before.exists else "create"
            if before.exists and before.text is None:
                return build(path=path, op=op, before=before, after=Side(), root=root, status="proposed")
            return build(path=path, op=op, before=before, after=Side(True, new_text), root=root, status="proposed")
        # file_edit
        if not before.exists:
            return build(path=path, op="edit", before=before, after=Side(), root=root, status="proposed",
                         withheld_reason="the file doesn't exist, so the edit will fail")
        if before.text is None:
            return build(path=path, op="edit", before=before, after=Side(), root=root, status="proposed")
        updated, problem = _proposed_edit(before.text, args)
        if updated is None:
            return build(path=path, op="edit", before=before, after=before, root=root, status="proposed",
                         withheld_reason=problem)
        return build(path=path, op="edit", before=before, after=Side(True, updated), root=root, status="proposed")
    except Exception:
        logger.debug("Proposed file_change for {} failed", name, exc_info=True)
        return None
