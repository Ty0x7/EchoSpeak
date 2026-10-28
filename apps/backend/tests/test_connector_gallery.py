"""Settings › Connections › Add apps: one-click connect goes through the reviewed path.

The end-to-end test starts tests/fixtures/mock_mcp_server.py through the real settings path
(DPAPI-backed secrets), so no third-party code runs and nothing reaches the network.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from agent import connector_gallery, integration_guides, integrations, mcp_trust
from agent.mcp_client import reset_mcp_manager
from agent.tool_registry import ToolRegistry
from config import config, read_runtime_override_payload, write_runtime_override_payload

FIXTURE_SERVER = Path(__file__).resolve().parent / "fixtures" / "mock_mcp_server.py"
MOCK = {
    "id": "mockapp", "app": "Mock app", "field": "tests", "keywords": ["mock"], "publisher": "official (test)",
    "how": {"kind": "pypi", "package": "mock-mcp"}, "needs": [], "steps": ["Choose Connect."], "watch_out": [],
    "settings": [{"name": "MOCK_API_TOKEN", "secret": True, "required": True, "about": "A test token."}],
    "verify": "", "capability_policies": {"echo": "read"}, "homepage": "",
}


@pytest.fixture
def clean(monkeypatch, tmp_path):
    monkeypatch.setattr(integrations, "_proposals_path", lambda: tmp_path / "proposals.json")
    monkeypatch.setattr(integration_guides, "GUIDES", [*integration_guides.GUIDES, MOCK])
    monkeypatch.setattr(integrations.shutil, "which", lambda name: sys.executable if name == "uvx" else None)
    real_resolve = integrations.resolve

    def resolve(candidate_id):
        if candidate_id != "guide:mockapp":
            return real_resolve(candidate_id)
        candidate = integrations._guide_summary(MOCK)
        return {**candidate, "version": "1.0.0", "launch": {"command": sys.executable, "args": [str(FIXTURE_SERVER)]}}

    monkeypatch.setattr(integrations, "resolve", resolve)
    saved = read_runtime_override_payload()
    reset_mcp_manager()
    yield
    reset_mcp_manager()
    for name in [n for n in ToolRegistry._entries if n.startswith("mcp__")]:
        ToolRegistry._entries.pop(name, None)
    write_runtime_override_payload(saved)
    config.reload()


def _row(guide_id: str) -> dict:
    return next(item for item in connector_gallery.gallery() if item["id"] == guide_id)


@pytest.mark.skipif(sys.platform != "win32", reason="MCP secrets are stored with Windows DPAPI")
def test_one_click_connect_and_disconnect(clean):
    from fastapi import HTTPException

    from api.routes.lean import ProposalApproveRequest, connect_connector, disconnect_connector

    assert _row("mockapp")["state"] == "not_connected"
    with pytest.raises(HTTPException) as missing:
        connect_connector("mockapp", ProposalApproveRequest(values={}))
    assert missing.value.status_code == 400 and "required" in missing.value.detail
    assert "mockapp" not in dict(getattr(config, "mcp_servers", None) or {})

    result = connect_connector("mockapp", ProposalApproveRequest(values={"MOCK_API_TOKEN": "tok-gallery-123456789"}))
    assert result["ok"] and result["server"]["running"] and "auth" not in result
    row = _row("mockapp")
    assert row["state"] == "connected" and row["tool_count"] == 2 and row["server"] == result["name"]
    assert mcp_trust.check(result["name"], config.mcp_servers[result["name"]]) == "trusted"

    with pytest.raises(HTTPException) as twice:
        connect_connector("mockapp", ProposalApproveRequest(values={"MOCK_API_TOKEN": "x"}))
    assert twice.value.status_code == 409

    assert disconnect_connector("mockapp")["removed"] == result["name"]
    assert result["name"] not in dict(getattr(config, "mcp_servers", None) or {})
    assert mcp_trust.check(result["name"], {"command": sys.executable}) == "new"  # the pin is gone too
    assert _row("mockapp")["state"] == "not_connected"


def test_a_sign_in_connector_opens_the_vendors_sign_in_after_approval(monkeypatch, tmp_path):
    from api.routes import lean

    monkeypatch.setattr(integrations, "_proposals_path", lambda: tmp_path / "proposals.json")
    monkeypatch.setattr(connector_gallery, "server_for", lambda guide_id, servers=None: "")
    approved, signed = [], []
    monkeypatch.setattr(lean, "_approve_proposal", lambda proposal_id, values: approved.append(proposal_id) or {"ok": True, "name": "todoist"})
    monkeypatch.setattr(lean, "_start_sign_in", lambda name: signed.append(name) or {"state": "signing_in"})
    result = lean.connect_connector("todoist", lean.ProposalApproveRequest(values={}))
    proposal = integrations.get_proposal(approved[0])
    assert proposal["launch"] == {"transport": "streamable_http", "url": "https://ai.todoist.net/mcp"}
    assert signed == ["todoist"] and result["auth"] == {"state": "signing_in"}


def test_the_gallery_lists_every_guide_with_a_category_and_hosted_sign_in_is_connectable():
    items = connector_gallery.gallery()
    ids = [item["id"] for item in items]
    assert len(ids) == len(set(ids)) == len(integration_guides.GUIDES) >= 31
    added = {"todoist", "zapier", "airtable", "canva", "monday", "sentry", "vercel", "supabase", "huggingface", "context7"}
    assert added <= set(ids)
    for item in items:
        assert item["category"] != "Other", item["id"]
        summary = integrations._guide_summary(integration_guides.guide(item["id"]))
        if item["kind"] in {"signin", "http"}:
            # Hosted sign-in is always https; a plain http one may be an app's own local server (Figma desktop).
            assert not summary["unsupported"] and summary["launch"]["url"].startswith("https://" if item["sign_in"] else "http"), item["id"]
            assert item["sign_in"] is (item["kind"] == "signin")
    for guide_id in added:
        assert integration_guides.guide(guide_id)["publisher"].startswith("official"), guide_id
