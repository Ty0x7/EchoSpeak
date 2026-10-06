"""Global pytest isolation for EchoSpeak durable state.

This file is loaded before test modules, so config and agent modules resolve all
durable paths beneath a disposable session root rather than apps/backend/data.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path


_TEST_STATE_ROOT = Path(tempfile.mkdtemp(prefix="echospeak-pytest-state-"))
Path(__file__).resolve().parents[3].joinpath(".test-state").mkdir(parents=True, exist_ok=True)
os.environ["ECHOSPEAK_TESTING"] = "1"
os.environ["ECHOSPEAK_DATA_DIR"] = str(_TEST_STATE_ROOT)
os.environ["ECHOSPEAK_ALLOW_MULTI_WRITER"] = "1"
# Learning (agent/learning) would carry lessons and track records from one test's
# chats into the next one's prompts. Learning tests turn it on with a fresh store.
os.environ.setdefault("LEARNING_ENABLED", "0")


def pytest_sessionfinish(session, exitstatus):  # type: ignore[no-untyped-def]
    shutil.rmtree(_TEST_STATE_ROOT, ignore_errors=True)
