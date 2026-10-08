"""Echo Connections end to end: a suggestion, the owner's approval, a real MCP server starting,
and the check that confirms it. The server is tests/fixtures/mock_mcp_server.py, so no
third-party code runs. Settings go through the real path (DPAPI-backed secrets)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from agent import integrations, mcp_trust
from agent.mcp_client import get_mcp_manager, reset_mcp_manager
from agent.tool_registry import ToolRegistry
from config import SETTINGS_PATH, config, read_runtime_override_payload, write_runtime_override_payload

FIXTURE_SERVER = Path(__file__).resolve().parent / "fixtures" / "mock_mcp_server.py"


@pytest.fixture
def clean(monkeypatch, tmp_path):
    monkeypatch.setattr(integrations, "_proposals_path", lambda: tmp_path / "proposals.json")
    saved = read_runtime_override_payload()
    reset_mcp_manager()
    yield
    reset_mcp_manager()
    for name in [n for n in ToolRegistry._entries if n.startswith("mcp__")]:
        ToolRegistry._entries.pop(name, None)
    write_runtime_override_payload(saved)
    config.reload()


def _suggest() -> dict:
    proposal = {
        "id": "prop_e2etest01", "status": "waiting", "server_name": "mockapp", "source_id": "guide:mockapp",
        "title": "Mock app", "summary": "", "publisher": "test", "version": "1.0.0",
        "launch": {"command": sys.executable, "args": [str(FIXTURE_SERVER)]},
        "settings": [{"name": "MOCK_API_TOKEN", "kind": "env", "secret": True, "required": True, "about": ""}],
        "needs": [], "missing": [], "steps": [], "watch_out": [], "verify": "", "homepage": "", "reason": "",
        "capability_policies": {"echo": "read"},
    }
    integrations._save([proposal])
    return proposal


@pytest.mark.skipif(sys.platform != "win32", reason="MCP secrets are stored with Windows DPAPI")
def test_suggest_approve_connect_and_check(clean):
    from fastapi import HTTPException

    from api.routes.lean import ProposalApproveRequest, approve_integration

    proposal = _suggest()
    # Nothing runs before approval, and a missing key is refused.
    assert "mockapp" not in dict(getattr(config, "mcp_servers", None) or {})
    with pytest.raises(HTTPException) as refused:
        approve_integration(proposal["id"], ProposalApproveRequest(values={}))
    assert refused.value.status_code == 400 and "required" in refused.value.detail

    result = approve_integration(proposal["id"], ProposalApproveRequest(values={"MOCK_API_TOKEN": "tok-e2e-123456789"}))
    assert result["ok"] and result["server"]["running"] and result["server"]["tool_count"] == 2

    # The key reached the server's config but not settings.json in plain text.
    assert config.mcp_servers["mockapp"]["env"]["MOCK_API_TOKEN"] == "tok-e2e-123456789"
    assert "tok-e2e-123456789" not in Path(SETTINGS_PATH).read_text(encoding="utf-8")
    assert mcp_trust.check("mockapp", config.mcp_servers["mockapp"]) == "trusted"
    assert integrations.get_proposal(proposal["id"])["status"] == "approved"

    # Its tools are registered like any MCP server's; the read-only one is offered as the check.
    status = get_mcp_manager().status()
    report = integrations.check("mockapp", status=status, registry_tools=ToolRegistry.get_all())
    assert "connected, 2 tools" in report and "mcp__mockapp__echo" in report
    assert json.loads(get_mcp_manager().call("mcp__mockapp__echo", {"text": "hi"}))["structuredContent"]["text"] == "echo:hi"
    assert integrations.get_proposal(proposal["id"])["connected_at"]

    # Approving twice, or a suggestion that no longer waits, is refused.
    with pytest.raises(HTTPException) as again:
        approve_integration(proposal["id"], ProposalApproveRequest(values={"MOCK_API_TOKEN": "x"}))
    assert again.value.status_code == 404

    # A changed launch afterwards (a rug pull) stops it until the owner approves again.
    changed = {**config.mcp_servers, "mockapp": {**config.mcp_servers["mockapp"], "args": [str(FIXTURE_SERVER), "--evil"]}}
    status = get_mcp_manager().initialize_servers(changed)
    row = next(r for r in status["servers"] if r["name"] == "mockapp")
    assert row["approval"] == "changed" and not row["running"]
    assert "waiting for approval" in integrations.check("mockapp", status=status)
