"""Execution evidence: what happened to each tool call, recorded where it is known.

A call goes through four stages, and the harness used to keep only the first and the
last as prose:

    attempted   the model asked for it (the tool call)
    executed    it ran, or the harness didn't let it (``outcome``)
    observed    what the harness saw it change: a file on disk, a process's exit code,
                the screen (``effects``)
    verified    a later check that it worked (learning's V-ladder, agent/learning/episodes.py)

Before this, the loop and the toolbox knew exactly why a call didn't run (policy, privacy,
the owner's "no", a repeat), wrote that into the result text, and then the refusal breaker,
the learning grader and the claim checks each re-guessed it by matching phrases such as
"blocked by EchoSpeak's safety policy". A reworded message silently changed what
counted as a refusal or a failure. Now the producer sets ``ToolResult.outcome`` once and
every consumer reads it; the phrase matching stays only as a fallback for records saved
before this existed and for refusals a registry tool reports in its own text.

Effects are observations, not the tool's say-so: a file write is checked on disk, so
"Wrote mics.md" from a tool that wrote somewhere else doesn't count. False success is the
most common agent failure in recent studies (arXiv 2606.09863: 45-76% of failures, and LLM
judges barely beat chance at spotting it), so the evidence comes from the environment.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

# ok         ran and did what it reported
# failed     ran and failed (an error, a non-zero exit, a write that isn't on disk)
# no_effect  ran, but the harness saw nothing change (a click on empty screen)
# refused    a setting, the policy, privacy or Blocked apps stopped it; not the agent's failure
# denied     the owner said no, or didn't answer
# held       autonomous mode kept it for the owner
# skipped    never run: a repeat, bad arguments, an earlier step failed, or Stop
OUTCOMES = ("ok", "failed", "no_effect", "refused", "denied", "held", "skipped")
NOT_RUN = frozenset({"refused", "denied", "held", "skipped"})
# The owner's settings or decision: what the refusal circuit breaker counts.
REFUSED = frozenset({"refused", "denied", "held"})

FILE_CHANGE_TOOLS = {"file_write", "file_edit", "file_copy", "file_move", "file_delete", "file_mkdir"}
TERMINAL_TOOLS = {"terminal", "terminal_run", "process_start"}
# How little of the screen may change before an action counts as having no visible effect.
SCREEN_UNCHANGED = 0.005

_HEADER = re.compile(r"^\[(ok|exit code (-?\d+))\s·\s[\d.]+s\s·\s(this PC|sandbox)\s·")
_BACKGROUND = re.compile(r"^\[[^\]]*·\s*process\s+(\w+)\s*·\s*(this PC|sandbox)\s*·")


def ran(outcome: str) -> bool:
    return outcome not in NOT_RUN


def _resolve(raw: Any) -> Path | None:
    """The real path a file tool used, under the same folder rules (call inside the tool context)."""
    value = str(raw or "").strip()
    if not value:
        return None
    try:
        from agent.tools import _safe_file_path

        return _safe_file_path(value)
    except Exception:
        return None


def _file(path: Path | None, *, action: str) -> dict[str, Any]:
    if path is None:
        return {}
    exists = path.exists()
    effect: dict[str, Any] = {"kind": "file", "action": action, "path": str(path), "exists": exists}
    if exists and path.is_file():
        effect["bytes"] = path.stat().st_size
    return effect


def observe(name: str, args: dict[str, Any], output: str, meta: dict[str, Any] | None = None) -> tuple[list[dict[str, Any]], str]:
    """(effects, problem) for a call that reported success. ``problem`` is '' when what it says matches what is there.

    Only cheap, local observations: a stat of the files it names, the terminal's own exit
    header, the screen change computer_action measured. Nothing re-runs, and nothing asks a model.
    """
    effects: list[dict[str, Any]] = []
    if name in {"file_write", "file_edit", "file_mkdir"}:
        effect = _file(_resolve(args.get("path") or args.get("file_path")), action="wrote" if name != "file_mkdir" else "made")
        if effect:
            effects.append(effect)
            if not effect["exists"]:
                return effects, f"{name} reported success, but {effect['path']} isn't there"
    elif name in {"file_copy", "file_move"}:
        target = _file(_resolve(args.get("dst") or args.get("destination")), action="wrote")
        if target:
            effects.append(target)
            if not target["exists"]:
                return effects, f"{name} reported success, but {target['path']} isn't there"
        if name == "file_move":
            source = _file(_resolve(args.get("src") or args.get("source")), action="moved away")
            if source:
                effects.append(source)
    elif name == "file_delete":
        effect = _file(_resolve(args.get("path")), action="deleted")
        if effect:
            effects.append(effect)
            if effect["exists"]:
                return effects, f"file_delete reported success, but {effect['path']} is still there"
    elif name in TERMINAL_TOOLS:
        head = str(output or "").lstrip()
        match = _HEADER.match(head)
        if match:
            code = 0 if match.group(1) == "ok" else int(match.group(2))
            effects.append({"kind": "process", "where": "host" if match.group(3) == "this PC" else "sandbox",
                            "exit_code": code, "command": str(args.get("command") or "")[:200]})
        elif (background := _BACKGROUND.match(head)):
            effects.append({"kind": "process", "where": "host" if background.group(2) == "this PC" else "sandbox",
                            "running": background.group(1), "command": str(args.get("command") or "")[:200]})
    if meta and isinstance(meta.get("screen_change"), (int, float)):
        effects.append({"kind": "screen", "changed": round(float(meta["screen_change"]), 4)})
    return effects, ""


def outcome_for(result_ok: bool, outcome: str, effects: list[dict[str, Any]]) -> str:
    """A screen action that changed nothing visible ran, but had no effect."""
    if result_ok and outcome == "ok" and any(e.get("kind") == "screen" and e.get("changed", 1) < SCREEN_UNCHANGED
                                             for e in effects):
        return "no_effect"
    return outcome


def written_files(effects: list[dict[str, Any]]) -> set[str]:
    """Lower-cased names of files observed on disk after a write, copy or move."""
    return {Path(str(e["path"])).name.lower() for e in effects
            if e.get("kind") == "file" and e.get("action") in {"wrote", "made"} and e.get("exists")}
