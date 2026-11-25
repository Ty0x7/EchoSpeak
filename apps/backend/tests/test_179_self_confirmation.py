"""#179: a model-made confirmation of the owner's own requested action is refused; the tool's approval card asks.

Live #171 check (24 November 2026, D4, qwen3.5:9b, 32K): for "Delete old_report.txt" Echo asked
"Confirm deletion: Delete old_report.txt from the project folder?" (Yes/No) instead of calling the tool, so the
harness's approval card and its recorded decision never happened.
"""
from __future__ import annotations

import pytest

from agent.lean.ask import repeats_action_request

YES_NO = ["Yes", "No"]


@pytest.mark.parametrize("question, options, goal", [
    ("Confirm deletion: Delete old_report.txt from the project folder?", YES_NO, "Delete old_report.txt"),   # D4
    ("Are you sure you want me to remove old_report.txt?", ["Yes, remove it", "No, keep it"], "Delete old_report.txt"),
    ("Proceed with deleting old_report.txt?", ["Proceed", "Cancel"], "Please delete old_report.txt."),
    ("Should I go ahead and overwrite config.json with the new settings?", YES_NO,
     "Overwrite config.json with the new settings"),
    ("Do you want me to send the email to Sam now?", ["Yes, send it", "No"], "Send the summary email to Sam"),
    ("Is it OK to install requests with pip?", ["Yes", "No, don't"], "install requests and run the script"),
    ("Do you want to delete old_report.txt?", ["Yes, delete it", "No, keep it"], "Delete old_report.txt"),  # existing case
])
def test_confirming_the_requested_action_is_refused(question, options, goal):
    assert repeats_action_request(question, options, goal)


@pytest.mark.parametrize("question, options, goal", [
    ("Which file should I delete?", ["Draft", "Backup"], "Delete the old file."),                   # real ambiguity
    ("Do you want to delete the backups?", YES_NO, "Inspect the backups."),                         # not requested
    ("Should I also delete the logs folder?", YES_NO, "Delete old_report.txt"),                     # beyond the request
    ("Should the report use ISO week numbers?", YES_NO, "Write the weekly report"),                 # a preference
    ("Confirm deletion: Delete old_report.txt from the project folder?", ["Keep", "Archive"],       # not yes/no
     "Delete old_report.txt"),
])
def test_genuine_questions_stay_allowed(question, options, goal):
    assert not repeats_action_request(question, options, goal)
