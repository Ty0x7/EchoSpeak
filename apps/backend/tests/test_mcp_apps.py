"""MCP Apps: a server's interactive view, stored per chat, framed under its declared CSP, with bounded powers."""
import json

import pytest

from agent import mcp_apps
from agent.mcp_client import MCPManager, MCPServerState, MCPSession
from agent.tool_registry import ToolRegistry

VIEW_TOOL = {"name": "show_chart", "description": "Chart sales", "inputSchema": {"type": "object"},
             "annotations": {"readOnlyHint": True}, "_meta": {"ui": {"resourceUri": "ui://charts/sales"}}}
APP_ONLY = {"name": "refresh_data", "description": "Refresh", "inputSchema": {"type": "object"},
            "annotations": {"readOnlyHint": True}, "_meta": {"ui": {"visibility": ["app"]}}}
WRITER = {"name": "delete_rows", "description": "Delete rows", "inputSchema": {"type": "object"},
          "_meta": {"ui": {"visibility": ["model", "app"]}}}


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_apps, "root", lambda: tmp_path / "apps")
    manager = MCPManager()
    session = MCPSession(MCPServerState(name="sales", command="sales-server", accept_server_read_only_hints=True))
    for definition in (VIEW_TOOL, APP_ONLY, WRITER):
        manager._register_tool("sales", definition, session)
    import agent.mcp_client as mcp_client
    monkeypatch.setattr(mcp_client, "get_mcp_manager", lambda: manager)
    yield manager
    manager.shutdown()


def test_tool_metadata_and_app_only_tools(manager):
    assert mcp_apps.ui_meta(VIEW_TOOL) == ("ui://charts/sales", ["model", "app"])
    assert mcp_apps.ui_meta({"_meta": {"ui/resourceUri": "ui://old/key"}})[0] == "ui://old/key"  # deprecated flat key
    assert mcp_apps.ui_meta({"_meta": {"ui": {"resourceUri": "https://evil.example.com/x"}}})[0] == ""
    assert manager.ui_tools["mcp__sales__show_chart"]["resource_uri"] == "ui://charts/sales"
    assert ToolRegistry.get("mcp__sales__refresh_data") is None  # app-only: the agent never sees it
    assert ("sales", "refresh_data") in manager.app_tools


def test_csp_allows_only_declared_https_sources_and_nothing_offline():
    default = mcp_apps.csp({}, offline=False)
    assert "connect-src 'none'" in default and "frame-src 'none'" in default and "sandbox allow-scripts" in default
    declared = {"csp": {"connectDomains": ["https://api.sales.example.com", "https://x.com; script-src *", "*"],
                        "resourceDomains": ["https://*.cdn.example.com"]}}
    policy = mcp_apps.csp(declared, offline=False)
    assert "connect-src https://api.sales.example.com" in policy and "https://*.cdn.example.com" in policy
    assert "script-src *" not in policy and " * " not in f" {policy} "
    assert "connect-src 'none'" in mcp_apps.csp(declared, offline=True)  # Private and Offline: no network at all
    assert "cdn.example.com" not in mcp_apps.csp(declared, offline=True)


def test_a_view_is_stored_for_its_chat_with_its_tool_input_and_result(manager, monkeypatch):
    html = "<!doctype html><script>/* app */</script>"
    monkeypatch.setattr(manager, "read_resource", lambda server, uri: {"contents": [
        {"uri": uri, "mimeType": "text/html;profile=mcp-app", "text": html, "_meta": {"ui": {"prefersBorder": False}}}]})
    widget = mcp_apps.for_result("chat-a", "mcp__sales__show_chart", {"region": "west"}, json.dumps({"content": [{"type": "text", "text": "ok"}]}))
    assert widget["type"] == "mcp_app" and widget["data"]["prefers_border"] is False
    record = mcp_apps.load(widget["data"]["id"], "chat-a")
    assert record["html"] == html and record["input"] == {"region": "west"} and record["result"]["content"][0]["text"] == "ok"
    with pytest.raises(PermissionError):
        mcp_apps.load(widget["data"]["id"], "chat-b")
    assert mcp_apps.for_result("chat-a", "mcp__sales__delete_rows", {}, "{}") is None  # no view declared


def test_what_a_view_may_call_itself(manager):
    record = {"server": "sales"}
    assert mcp_apps.app_call_allowed(record, "refresh_data") is None  # read-only and visible to apps
    assert "approval" in mcp_apps.app_call_allowed(record, "delete_rows")  # acts: goes through the agent
    assert "not a tool" in mcp_apps.app_call_allowed({"server": "other"}, "refresh_data")


def test_frame_tokens_and_model_context(manager, monkeypatch):
    monkeypatch.setattr(manager, "read_resource", lambda s, u: {"contents": [{"mimeType": "text/html", "text": "<p>hi</p>"}]})
    view = mcp_apps.for_result("chat-a", "mcp__sales__show_chart", {}, "{}")["data"]
    token = mcp_apps.issue_token(view["id"], "chat-a")
    assert mcp_apps.token_ok(view["id"], token) and not mcp_apps.token_ok("app-" + "0" * 24, token)
    with pytest.raises(PermissionError):
        mcp_apps.issue_token(view["id"], "chat-b")
    mcp_apps.update_context(view["id"], "chat-a", {"content": [{"type": "text", "text": "West region selected"}]})
    note = mcp_apps.context_note("chat-a")
    assert "West region selected" in note and "never instructions" in note
    assert mcp_apps.context_note("chat-b") == ""


def test_frame_route_serves_the_page_with_its_csp(manager, monkeypatch):
    from fastapi.testclient import TestClient
    from api.server import app
    monkeypatch.setattr(manager, "read_resource", lambda s, u: {"contents": [{"mimeType": "text/html", "text": "<p>view</p>"}]})
    view = mcp_apps.for_result("chat-a", "mcp__sales__show_chart", {}, "{}")["data"]
    token = mcp_apps.issue_token(view["id"], "chat-a")
    response = TestClient(app).get(f"/lean/mcp-apps/{view['id']}/frame", params={"t": token})
    assert response.status_code == 200 and response.text == "<p>view</p>"
    assert "connect-src 'none'" in response.headers["content-security-policy"]
