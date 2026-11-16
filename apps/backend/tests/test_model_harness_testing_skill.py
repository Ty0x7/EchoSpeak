"""The model-harness-testing skill (.agents/skills/model-harness-testing) stays usable and true to the code.

It is the procedure behind every live evaluation of this harness. It must parse as a skill under EchoSpeak's own
rules (EchoSpeak lists the repo's .agents/skills folder as an import source), and everything it tells an agent to
run must exist: the scripts, their flags, the judge's weights and the note template. A renamed flag or a changed
weight would otherwise make the next evaluation quietly incomparable.
"""
from __future__ import annotations

import re
from pathlib import Path

from agent.lean.agent_skills import parse_skill_md

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parents[1]
SKILL = REPO / ".agents" / "skills" / "model-harness-testing"
POINTER = REPO / ".claude" / "skills" / "model-harness-testing" / "SKILL.md"


def _flags(script: str) -> set[str]:
    source = (BACKEND / "scripts" / script).read_text(encoding="utf-8")
    return set(re.findall(r"add_argument\(\s*\"(--[a-z][a-z-]*)\"", source))


def test_both_skill_files_parse_under_echospeaks_rules():
    for path in (SKILL / "SKILL.md", POINTER):
        meta, body, problem = parse_skill_md(path.read_text(encoding="utf-8"))
        assert problem == "", f"{path}: {problem}"
        assert meta["name"] == "model-harness-testing"
        # The trigger says when to use it and when not (no evaluations during unrelated work).
        assert "only for model/harness testing" in " ".join(str(meta["description"]).split()).lower()


def test_the_pointer_names_the_canonical_files():
    text = POINTER.read_text(encoding="utf-8")
    for named in re.findall(r"`(\.agents/skills/model-harness-testing/[^`]+)`", text):
        assert (REPO / named).is_file(), named


def test_every_script_the_skill_names_exists():
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    # The tools table (one row per script, possibly two scripts in a row); llama.cpp's converter is external.
    named = {s for row in re.findall(r"^\| (`[a-z_]+\.py`(?:, `[a-z_]+\.py`)*) \|", text, re.M)
             for s in re.findall(r"`([a-z_]+\.py)`", row)}
    assert {"harness_campaign.py", "harness_eval.py", "harness_judge.py", "model_recorder.py", "replay_call.py"} <= named
    for script in named:
        assert (BACKEND / "scripts" / script).is_file(), script


def test_every_flag_the_skill_documents_is_real():
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    rows = {m.group(1): m.group(2) for m in re.finditer(r"^\| `([a-z_]+\.py)` \| (.+) \|$", text, re.M)}
    for script, row in rows.items():
        documented = set(re.findall(r"`?(--[a-z][a-z-]*)", row))
        if documented:
            assert documented <= _flags(script), f"{script}: {sorted(documented - _flags(script))}"
    # Flags named in the procedure text itself (learning off, the A/B arms, the full case set).
    for flag in ("--set", "--env", "--agents-md", "--skill", "--only", "--repeat", "--label"):
        assert flag in text and flag in _flags("harness_eval.py"), flag


def test_the_rubric_in_the_skill_matches_the_judge():
    from scripts.harness_judge import WEIGHTS

    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    stated = [int(n) for n in re.findall(r"^\| [A-Z][^|]+\| (\d+)% \|$", text, re.M)]
    assert stated == [round(w * 100) for w in WEIGHTS.values()] == [30, 25, 20, 15, 10]


def test_the_obsidian_note_template_has_the_required_sections():
    template = (SKILL / "assets" / "obsidian-evaluation-note.md").read_text(encoding="utf-8")
    for heading in ("## Setup", "## Scores", "## What failed, and why", "## Harness changes",
                    "## Security and isolation", "## Regressions and limits", "## Links"):
        assert heading in template, heading
    assert "evidence_status:" in template and "[[EchoSpeak/27 Live model evaluation" in template
