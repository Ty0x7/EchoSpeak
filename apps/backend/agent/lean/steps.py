"""How a finished step reads in the chat.

Every tool row starts with a present-tense label from ``toolbox.describe_call``
("Searching “budget mic”"). When the tool finishes, the row switches to a
past-tense label (``done_label``: "Searched “budget mic”") and a one-line
summary of what came back (``summarize``: "14 results · read 2 pages",
"12 tests passed", "+24 lines"). Both are derived from the call and its
output, never from what the model claims, so they can be trusted at a glance.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

# Present-tense openings from describe_call -> what they read as once done.
_PAST = (
    ("Shopping for", "Checked prices for"),
    ("Searching your documents", "Searched your documents"),
    ("Searching code", "Searched code"),
    ("Searching", "Searched"),
    ("Reading", "Read"),
    ("Writing", "Wrote"),
    ("Editing", "Edited"),
    ("Running", "Ran"),
    ("Finding", "Found"),
    ("Creating", "Created"),
    ("Updating", "Updated"),
    ("Deleting", "Deleted"),
    ("Listing", "Listed"),
    ("Moving", "Moved"),
    ("Copying", "Copied"),
    ("Saving", "Saved"),
    ("Recalling", "Recalled"),
    ("Asking", "Asked"),
    ("Starting", "Started"),
    ("Checking", "Checked"),
    ("Stopping", "Stopped"),
)


def done_label(label: str) -> str:
    """'Searching “x”' -> 'Searched “x”'; labels without a known verb are kept."""
    for present, past in _PAST:
        if label.startswith(present):
            return past + label[len(present):]
    return label


def short_url(url: str, limit: int = 48) -> str:
    """'https://www.rtings.com/microphone/reviews/best' -> 'rtings.com/microphone/reviews/best'."""
    parsed = urlparse(str(url or "").strip())
    host = (parsed.netloc or "").removeprefix("www.")
    if not host:
        return str(url or "")[:limit]
    path = parsed.path.rstrip("/")
    text = host + (path if path else "")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _plural(n: int, one: str, many: str = "") -> str:
    return f"{n:,} {one if n == 1 else (many or one + 's')}"


def _first_line(text: str, limit: int = 90) -> str:
    for line in str(text or "").splitlines():
        line = line.strip(" -:\t")
        if line:
            return line if len(line) <= limit else line[: limit - 1] + "…"
    return ""


_EXIT = re.compile(r"\[exit code (-?\d+)|exit(?:code)?[=: ]+(-?\d+)", re.IGNORECASE)
_PASSED = re.compile(r"(\d+)\s+(?:tests?\s+)?passed", re.IGNORECASE)
_FAILED = re.compile(r"(\d+)\s+(?:tests?\s+)?failed", re.IGNORECASE)


def _terminal_summary(output: str) -> str:
    passed, failed = _PASSED.search(output), _FAILED.search(output)
    if passed or failed:
        parts = []
        if passed:
            parts.append(f"{int(passed.group(1)):,} passed")
        if failed:
            parts.append(f"{int(failed.group(1)):,} failed")
        return "Tests: " + ", ".join(parts)
    if "still running" in output or "started in the background" in output:
        return "Still running in the background"
    match = _EXIT.search(output)
    code = int(next(g for g in match.groups() if g is not None)) if match else None
    tail = ""
    for line in reversed([line.strip() for line in output.splitlines() if line.strip()]):
        if not line.startswith("[") and not line.lower().startswith("hint:"):
            tail = line[:80]
            break
    if code is None:
        return tail
    return ("Done" if code == 0 else f"Exit code {code}") + (f" · {tail}" if tail else "")


def _lines(text: Any) -> int:
    value = str(text or "")
    return value.count("\n") + 1 if value else 0


def _words(text: str) -> int:
    return len(re.findall(r"\w+", text))


def _page_text(output: str) -> str:
    """Drop the metadata lines safe_web_fetch puts around the page text."""
    kept = [line for line in output.splitlines() if not re.match(r"^\s*(source_id|url|final_url|title|content_type|"
                                                                 r"retrieved_at|status|links?)\s*[=:]", line, re.IGNORECASE)]
    return "\n".join(kept)


def summarize(name: str, args: dict[str, Any], ok: bool, output: str, meta: dict[str, Any] | None = None) -> str:
    """One short line about what the step produced, or why it failed."""
    output = str(output or "")
    meta = meta or {}
    if not ok:
        reason = _first_line(output.removeprefix("Error:").strip(), 80)
        return f"Failed: {reason}" if reason else "Failed"
    if name == "web_search":
        results = int(meta.get("results") or 0)
        pages = int(meta.get("pages") or 0)
        if not results:
            return "No results"
        return _plural(results, "result") + (f" · read {_plural(pages, 'page')}" if pages else "")
    if name == "safe_web_fetch":
        words = int(meta.get("words") or _words(_page_text(output)))
        return f"Read {_plural(words, 'word')}" if words else "Read the page"
    if name in {"terminal", "terminal_run"}:
        return _terminal_summary(output)
    if name == "file_write":
        return f"+{_plural(_lines(args.get('content') or args.get('text')), 'line')}"
    if name == "file_edit":
        added = _lines(args.get("new_text") or args.get("new_string"))
        removed = _lines(args.get("old_text") or args.get("old_string"))
        return f"+{added} −{removed} lines"
    if name == "file_read":
        return _plural(_lines(output), "line")
    if name == "file_list":
        entries = [line for line in output.splitlines() if line.strip() and not line.startswith(("[", "Folder", "Directory"))]
        return _plural(len(entries), "item")
    if name in {"file_search", "file_find"}:
        hits = [line for line in output.splitlines() if line.strip() and not line.lower().startswith(("no ", "found "))]
        return _plural(len(hits), "match", "matches") if hits else "No matches"
    if name == "product_search":
        products = int(meta.get("products") or 0)
        return _plural(products, "product") if products else _first_line(output, 60)
    if name in {"image_search", "video_search"}:
        found = int(meta.get("items") or 0)
        return _plural(found, "image" if name == "image_search" else "video") if found else ""
    if name == "memory_search":
        found = sum(1 for line in output.splitlines() if line.startswith("- "))
        return _plural(found, "memory", "memories") if found else "Nothing saved about that"
    if name == "memory_save":
        return "Already remembered" if output.startswith("Already") else "Saved"
    if name in {"create_artifact", "update_artifact"}:
        return "Saved to Artifacts"
    return ""


def widget_counts(widgets: list[dict[str, Any]] | None) -> dict[str, int]:
    """Counts the summary can use from a tool's cards (products, images, videos)."""
    counts: dict[str, int] = {}
    for widget in widgets or []:
        kind = str(widget.get("type") or "")
        data = widget.get("data") or {}
        items = data.get("items") if isinstance(data, dict) else None
        if isinstance(items, list):
            if kind == "product_carousel":
                counts["products"] = counts.get("products", 0) + len(items)
            elif kind == "media":
                counts["items"] = counts.get("items", 0) + len(items)
    return counts


# Questions where reading a couple of sources (not just snippets) makes the answer trustworthy.
_READ_WORTHY = re.compile(
    r"(?i)\b(best|vs\.?|versus|compare|comparison|review|reviews|price|prices|pricing|cheapest|"
    r"worth it|top \d+|which (?:one|is better)|recommend\w*|latest|release date)\b"
)


def default_reads(query: str) -> int:
    """How many top results web_search reads when the model didn't say: 2 for comparisons, prices, 'best X'."""
    return 2 if _READ_WORTHY.search(query or "") else 0


_RESULT_URL = re.compile(r"^\s*URL:\s*(\S+)", re.MULTILINE)


def result_urls(search_output: str) -> list[str]:
    """Result links in web_search's own order, without duplicates."""
    return list(dict.fromkeys(_RESULT_URL.findall(search_output or "")))
