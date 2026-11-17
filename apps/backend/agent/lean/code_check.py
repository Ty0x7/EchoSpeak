"""Code handed over in a reply: checked for what can be checked, and labelled for what wasn't.

A model's description of its code is not evidence. The Pong game returned in the 16 November 2026
audit was called complete and playable; nothing had run it, and reading it showed scores that never
change and arrow keys that never match. When code is written to a file and a check runs, the
verification ledger measures it (agent/lean/verification.py). This covers the other case: code that
only exists as text in the answer.

Two things, both from trusted code and neither a rewrite of the answer:

- **A syntax check** of each fenced block in a language with a parser at hand: Python and JSON in
  process, JavaScript (and the scripts inside HTML) with ``node --check`` when node is installed.
  Parsing never executes the code.
- **An honest label** when a sizeable program is presented as working and nothing in the turn ran it.

It does not judge whether the code is right. A parse proves little; the note says so.
"""
from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

_BLOCK = re.compile(r"```([\w+#.-]*)[^\n]*\n(.*?)```", re.S)
_SCRIPT = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", re.S | re.I)
# "complete, playable Pong game" and "Here's a complete …" (the live audit replay) count as well as "fully working".
_WORKS = re.compile(r"(?i)\b(fully (?:working|functional)|complete(?:ly)?,? (?:working|functional|playable)|ready to (?:play|run|use)|"
                    r"works? (?:perfectly|out of the box|as expected)|tested|production[- ]ready|here'?s (?:your|a) complete|"
                    r"complete,? (?:[\w-]+,? ){0,3}(?:game|app|script|program|code))\b")
# What counts as a program rather than a snippet.
PROGRAM_LINES = 25
_RAN = frozenset({"terminal", "terminal_run", "process_start"})
_PYTHON = {"python", "py", "python3"}
_JS = {"javascript", "js", "mjs", "node"}
_HTML = {"html", "htm"}


def blocks(text: str) -> list[tuple[str, str]]:
    """(language, code) for each fenced block in the reply."""
    return [(match.group(1).strip().lower(), match.group(2)) for match in _BLOCK.finditer(str(text or ""))]


def _node_check(code: str) -> Optional[str]:
    """A JavaScript syntax error from ``node --check``, "" when it parses, None when node isn't available."""
    node = shutil.which("node")
    if not node:
        return None
    try:
        with tempfile.TemporaryDirectory(prefix="echospeak-check-") as folder:
            path = Path(folder) / "snippet.js"
            path.write_text(code, encoding="utf-8")
            done = subprocess.run([node, "--check", str(path)], capture_output=True, text=True, timeout=8)
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode == 0:
        return ""
    lines = [line.strip() for line in (done.stderr or "").splitlines() if line.strip()]
    error = next((line for line in lines if "Error" in line), lines[-1] if lines else "syntax error")
    where = re.search(r"snippet\.js:(\d+)", done.stderr or "")
    return (f"line {where.group(1)}: " if where else "") + error[:160]


def syntax_problem(language: str, code: str) -> Optional[str]:
    """The first syntax error in ``code``, "" when it parses, None when this language isn't checked here."""
    if language in _PYTHON:
        try:
            ast.parse(code)
        except SyntaxError as exc:
            return f"line {exc.lineno}: {exc.msg}"
        return ""
    if language == "json":
        try:
            json.loads(code)
        except ValueError as exc:
            return str(exc)[:160]
        return ""
    if language in _JS:
        return _node_check(code)
    if language in _HTML:
        scripts = [body for body in _SCRIPT.findall(code) if body.strip()]
        if not scripts:
            return None
        results = [_node_check(body) for body in scripts]
        if any(result is None for result in results):
            return None
        return next((result for result in results if result), "")
    return None


def notes(text: str, *, ran: set[str], wrote: bool) -> list[str]:
    """Notes to show under a reply that contains code.

    ``ran`` is the names of tools that succeeded in this turn, ``wrote`` whether a file was written.
    """
    found = blocks(text)
    if not found:
        return []
    out: list[str] = []
    checked = 0
    for language, code in found:
        problem = syntax_problem(language, code)
        if problem is None:
            continue
        checked += 1
        if problem:
            out.append(f"Syntax check failed for the {language} code in this reply ({problem}). It will not run as written.")
    program = max((code.count("\n") + 1 for _language, code in found), default=0) >= PROGRAM_LINES
    executed = bool(ran & _RAN)
    if program and not executed and not wrote and _WORKS.search(_BLOCK.sub(" ", text)):
        parsed = " It parses, which says nothing about whether it behaves correctly." if checked and not out else ""
        out.append("Not run: this code was written but nothing here executed or tested it, so the description of what "
                   "it does is unverified." + parsed)
    return out[:3]
