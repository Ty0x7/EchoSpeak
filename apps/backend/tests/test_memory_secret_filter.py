"""Memory refuses secrets, not the words engineers use about them (Echo Companion closeout, 2026-11-26)."""
import pytest

from agent.memory import AgentMemory

filter_ = AgentMemory._is_sensitive_text


@pytest.mark.parametrize("text", [
    "My OpenAI key is sk-proj-abc123def456ghi789",
    "token: ghp_aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789",
    "password = hunter2!x",
    "The api_key is 9f8e7d6c5b4a",
    "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abc",
    "-----BEGIN OPENSSH PRIVATE KEY-----",
    "AWS access key AKIAABCDEFGHIJKLMNOP",
    "my pin is 4821-77",
])
def test_secrets_are_refused(text):
    assert filter_(None, text) is True


@pytest.mark.parametrize("text", [
    "The idle timer only counted content tokens, so long reasoning phases were cut off.",
    "Codex found the harness adds 3,000 prompt tokens per call.",
    "Secret masking now covers GitHub tokens.",
    "The password field on the login page is now validated.",
    "Rotate the API key every 90 days.",
    "token budget raised to 32k",
])
def test_engineering_words_are_kept(text):
    assert filter_(None, text) is False
