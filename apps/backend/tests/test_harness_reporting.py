"""Report-only tests for the evaluator's explicit case splits."""

from __future__ import annotations

import json
import sys

import pytest

from scripts import harness_judge as judge


@pytest.fixture
def run(tmp_path, monkeypatch):
    cases = tmp_path / "cases"
    cases.mkdir()
    for case_id, score in {"D1": 9, "H1": 8, "XH1": 2, "RH1": 5, "EH2": 3}.items():
        (cases / f"{case_id}-r1.json").write_text(json.dumps({"id": case_id, "score": score}))

    def grade(case, window):
        return {**case, "title": case["id"], "skill": "probe", "execution_success": True, "critical": [],
                "scores": {name: case["score"] for name in judge.WEIGHTS}}

    monkeypatch.setattr(judge, "judge_case", grade)
    return tmp_path


def test_mixed_suite_headlines_use_registered_case_membership(run):
    report = judge.judge_run(run, 16384)
    assert report["aggregate"]["dev"] == 9
    assert report["aggregate"]["heldout"] == 4.5
    assert report["aggregate"]["all"] == 5.4
    assert report["grouping"]["membership"] == {
        "D1": "dev", "H1": "heldout", "XH1": "heldout", "RH1": "heldout", "EH2": "heldout",
    }
    assert report["grouping"]["source"] == "registered_suites"


def test_incomplete_explicit_membership_is_rejected(run):
    (run / "meta.json").write_text(json.dumps({"case_membership": {"D1": "dev"}}))
    with pytest.raises(ValueError, match="Missing.*membership"):
        judge.judge_run(run, 16384)


def test_membership_contradicting_registered_case_is_rejected(run):
    membership = {"D1": "dev", "H1": "heldout", "XH1": "dev", "RH1": "heldout", "EH2": "heldout"}
    (run / "meta.json").write_text(json.dumps({"case_membership": membership}))
    with pytest.raises(ValueError, match="Conflicting.*XH1"):
        judge.judge_run(run, 16384)


def test_unregistered_case_without_explicit_membership_is_rejected(run):
    (run / "cases/custom-r1.json").write_text(json.dumps({"id": "custom", "score": 10}))
    with pytest.raises(ValueError, match="Missing.*custom"):
        judge.judge_run(run, 16384)


def test_corrected_report_preserves_existing_scorecard(run, tmp_path, monkeypatch):
    archived = run / "judge.json"
    archived.write_text("original historical scores\n")
    monkeypatch.setattr(judge, "markdown", lambda *_args: "corrected report\n")
    monkeypatch.setattr(sys, "argv", ["harness_judge.py", str(run)])
    with pytest.raises(SystemExit, match="2"):
        judge.main()
    assert archived.read_text() == "original historical scores\n"

    destination = tmp_path / "regrouped"
    monkeypatch.setattr(sys, "argv", ["harness_judge.py", str(run), "--output-dir", str(destination)])
    assert judge.main() == 0
    assert archived.read_text() == "original historical scores\n"
    assert json.loads((destination / "judge.json").read_text())["aggregate"]["heldout"] == 4.5
