"""Production-closure regressions: named-file pin, approval identity, skills truth, research artifacts."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from tests.route_paths import route_paths as _route_paths


def test_skill_status_audit_classifies():
    from agent.skill_status_audit import audit_all_skills

    rows = audit_all_skills(available_capabilities=set(), available_artifacts=set())
    assert rows
    # Disabled packages if any must not be executable
    for r in rows:
        if r["status"] == "disabled":
            assert r["executable"] is False


def test_parallel_orchestrator_route_is_retired():
    from api.server import app

    assert "/orchestrate" not in _route_paths(app)
