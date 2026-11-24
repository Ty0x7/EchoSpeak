"""Campaign outcomes with the real judge and disposable evaluation receipts."""

import json
from pathlib import Path
import subprocess
import sys

import pytest

from scripts import harness_campaign as campaign


@pytest.fixture
def run_campaign(monkeypatch, tmp_path):
    monkeypatch.setattr(campaign, "OUT", tmp_path / "reports")
    monkeypatch.setattr(campaign, "baseline_tree", lambda _: tmp_path / "baseline")
    monkeypatch.setattr(campaign, "keep_loaded", lambda *_: 0.0)
    unloaded = []
    monkeypatch.setattr(campaign, "unload", lambda model, _: unloaded.append(model))

    def exercise(label, defect="", baseline=False, upstream=None):
        runs = {}

        def receipt(_backend, model, arm, repeat, _only, _cases, *, upstream="http://127.0.0.1:11434"):
            current = "-current-" in arm
            if current and defect == "interrupt":
                raise KeyboardInterrupt
            run = tmp_path / arm
            cases = run / "cases"
            cases.mkdir(parents=True)
            runs[arm] = run
            membership = {"D4": "dev", "EH2": "heldout"}
            if current and defect == "conflicting":
                membership["EH2"] = "dev"
            (run / "meta.json").write_text(json.dumps({
                "model": model, "cases": list(membership), "case_membership": membership,
                "repeat": repeat, "ended": 1.0, "upstream": upstream,
            }))
            (run / "model-calls.jsonl").write_text("")
            for case in membership:
                if current and case == "EH2" and defect == "partial":
                    continue
                (cases / f"{case}-r1.json").write_text(json.dumps({
                    "id": case, "rep": 1, "title": case, "skill": "control", "truth": {"answer": ["verified"]},
                    "before": {}, "after": {}, "turns": [{"reply": "verified", "events": [], "error": "", "seconds": 0}],
                    "model_calls": [], "ideal_calls": 1,
                }))
            if current and defect == "occupied":
                (run / "judge.json").write_bytes(b"original scorecard\n")
            return run

        monkeypatch.setattr(campaign, "run_eval", receipt)
        argv = ["harness_campaign.py", "--models", "local-fixture", "--only", "D4,EH2",
                "--repeat", "1", "--label", label]
        if baseline:
            argv += ["--baseline-ref", "reviewed"]
        if upstream:
            argv += ["--upstream", upstream]
        monkeypatch.setattr(sys, "argv", argv)
        code = campaign.main()
        folder = next(campaign.OUT.glob(f"*-{label}"))
        return code, json.loads((folder / "campaign.json").read_text()), (folder / "campaign.md").read_text(), runs

    return exercise, unloaded


@pytest.mark.parametrize(("defect", "reason"), [
    ("occupied", "scorecard already exists"),
    ("conflicting", "Conflicting case membership"),
    ("partial", "Incomplete scorecard"),
])
def test_required_judge_failure_fails_the_campaign_and_keeps_baseline(run_campaign, defect, reason):
    exercise, unloaded = run_campaign
    code, report, markdown, runs = exercise(defect, defect, baseline=True)
    assert code != 0
    assert report["status"] == "failed" and "failed" in markdown.lower()
    assert any(reason in item["reason"] and item["arm"] == "current" for item in report["failures"])
    assert "baseline" in report["models"]["local-fixture"]
    assert "current" not in report["models"]["local-fixture"]
    assert unloaded == ["local-fixture"]
    if defect == "occupied":
        assert next(run for label, run in runs.items() if "-current-" in label).joinpath("judge.json").read_bytes() == b"original scorecard\n"


def test_successful_child_that_writes_no_scorecard_still_fails(run_campaign, monkeypatch):
    exercise, _ = run_campaign
    original = subprocess.run

    def omit_scorecard(argv, **kwargs):
        if Path(argv[1]).name == "harness_judge.py":
            return subprocess.CompletedProcess(argv, 0, "", "")
        return original(argv, **kwargs)

    monkeypatch.setattr(campaign.subprocess, "run", omit_scorecard)
    code, report, _, _ = exercise("missing")
    assert code != 0 and report["status"] == "failed"
    assert any("judge.json" in item["reason"] for item in report["failures"])


def test_campaign_routes_both_evaluation_arms_to_the_selected_provider(run_campaign):
    exercise, _ = run_campaign
    upstream = "http://127.0.0.1:34567"
    code, report, _, runs = exercise("upstream", baseline=True, upstream=upstream)
    assert code == 0
    assert [json.loads((run / "meta.json").read_text())["upstream"] for run in runs.values()] == [upstream, upstream]
    assert report["upstream"] == upstream


def test_eval_passes_upstream_to_the_child(tmp_path, monkeypatch):
    upstream = "http://127.0.0.1:34567"
    monkeypatch.setattr(campaign, "OUT", tmp_path / "data/evals/harness")
    monkeypatch.setattr(campaign, "ROOT", tmp_path)

    def launch(argv, *, cwd, check):
        assert cwd == tmp_path and check
        assert argv[argv.index("--upstream") + 1] == upstream
        (campaign.OUT / "stamp-arm").mkdir(parents=True)

    monkeypatch.setattr(campaign.subprocess, "run", launch)
    assert campaign.run_eval(tmp_path, "fixture", "arm", 1, "D4", "all", upstream=upstream).name == "stamp-arm"


def test_upstream_receipt_excludes_url_credentials():
    from scripts.harness_eval import upstream_origin

    assert upstream_origin("http://user:password@[::1]:2345/path?key=value") == "http://[::1]:2345"


def test_fresh_campaign_requires_both_complete_arms(run_campaign):
    exercise, unloaded = run_campaign
    code, report, _, _ = exercise("complete", baseline=True)
    assert code == 0 and report["status"] == "complete"
    assert report["failures"] == []
    sides = report["models"]["local-fixture"]
    assert sides["baseline"]["cases"] == sides["current"]["cases"]
    assert set(sides["current"]["cases"]) == {"D4", "EH2"}
    assert unloaded == ["local-fixture"]


def test_interrupted_campaign_preserves_completed_arm_for_the_next_invocation(run_campaign):
    exercise, unloaded = run_campaign
    try:
        code, report, _, runs = exercise("interrupted", "interrupt", baseline=True)
    except KeyboardInterrupt:
        pytest.fail("The interrupted campaign escaped without recording a failed report")
    old = next(run for label, run in runs.items() if "-baseline-" in label)
    scorecard = (old / "judge.json").read_bytes()
    assert code != 0 and report["status"] == "failed"
    assert "baseline" in report["models"]["local-fixture"]
    assert report["missing_arms"] == [{"model": "local-fixture", "arm": "current"}]
    retry, completed, _, _ = exercise("retry", baseline=True)
    assert retry == 0 and completed["status"] == "complete"
    assert (old / "judge.json").read_bytes() == scorecard
    assert unloaded == ["local-fixture", "local-fixture"]
