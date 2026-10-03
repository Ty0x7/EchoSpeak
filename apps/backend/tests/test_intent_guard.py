"""Tests for Bug 1: Workspace Promotion & Coding Intent Guards."""

import pytest
from unittest.mock import MagicMock
from agent.core import EchoSpeakAgent
from agent.router import RoutingDecision


class DummyAgent(EchoSpeakAgent):
    """Subclass of EchoSpeakAgent bypassing network/LLM init for tests."""
    def __init__(self):
        self._workspace_id = "chat"
        self._router = MagicMock()
        # Mocking memory classes to prevent loading actual database files
        self.conversation_memory = MagicMock()
        self.conversation_memory.messages = []


def test_project_materialization_guard_rejects_novel_information_phrasings():
    """Novel non-build phrasings must not be allowed to allocate/scaffold projects."""
    from agent.intent_guard import may_materialize_project, is_explicit_new_project_request

    negatives = [
        "which clubs are on the pitch later tonight",
        "can you explain what your personality file says",
        "look over the setup notes and summarize them",
        "how likely are the oilers to win the stanley cup",
        "show me the current bitcoin price in cad",
    ]
    for phrase in negatives:
        assert not is_explicit_new_project_request(phrase), phrase
        assert not may_materialize_project(phrase), phrase

    assert not may_materialize_project("create a python script that prints hello world")

    assert may_materialize_project("build a habit tracker app")
    assert may_materialize_project("write a small weather dashboard")
