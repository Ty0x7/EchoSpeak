"""Settings › Connections: every connector EchoSpeak has a setup guide for, and one-click connect.

A connector is an MCP server from agent/integration_guides.py. Connecting runs the same reviewed
path as a connection an agent suggests (agent/integrations.py): a proposal with the version
pinned, the owner's approval (their click on Connect), the server saved and its launch pinned
(agent/mcp_trust.py), then, for hosted servers that use it, sign-in through the owner's browser
(agent/mcp_oauth.py). Nothing here bypasses approval or stores a secret outside the DPAPI paths.
"""
from __future__ import annotations

from typing import Any, Optional

from agent import integration_guides, integrations


def _servers() -> dict[str, Any]:
    from config import config
    return dict(getattr(config, "mcp_servers", None) or {})


def server_for(guide_id: str, servers: Optional[dict[str, Any]] = None) -> str:
    """The configured MCP server this guide set up, or ""."""
    servers = _servers() if servers is None else servers
    for proposal in integrations.proposals("approved"):
        name = str(proposal.get("server_name") or "")
        if proposal.get("source_id") == f"guide:{guide_id}" and isinstance(servers.get(name), dict):
            return name
    item = integration_guides.guide(guide_id)
    url = str(((item or {}).get("how") or {}).get("url") or "").rstrip("/")
    if url:
        for name, raw in servers.items():
            if isinstance(raw, dict) and str(raw.get("url") or raw.get("endpoint") or "").rstrip("/") == url:
                return name
    return ""


def gallery() -> list[dict[str, Any]]:
    """Every connector with what it needs and where it stands. Runs nothing."""
    servers = _servers()
    try:
        from agent.mcp_client import get_mcp_manager
        live = {row["name"]: row for row in get_mcp_manager().status().get("servers") or []}
    except Exception:
        live = {}
    items = []
    for item in integration_guides.GUIDES:
        summary = integrations._guide_summary(item)
        name = server_for(item["id"], servers)
        row = live.get(name) or {}
        auth = str((row.get("auth") or {}).get("state") or "")
        state = ("builtin" if item["how"]["kind"] == "builtin"
                 else "not_connected" if not name
                 else "sign_in_needed" if auth in {"needed", "failed"} or (summary["sign_in"] and auth not in {"signed_in", "signing_in"})
                 else "signing_in" if auth == "signing_in"
                 else "connected" if row.get("running")
                 else "starting" if not row.get("last_error") and row.get("approval") in {None, "", "trusted"}
                 else "problem")
        items.append({
            "id": item["id"], "app": item["app"], "field": item["field"],
            "category": integration_guides.CATEGORIES.get(item["id"], "Other"),
            "publisher": item["publisher"], "kind": item["how"]["kind"], "sign_in": summary["sign_in"],
            "where": item["how"].get("where", ""),
            "needs": summary["needs"], "missing": summary["missing"], "steps": summary["steps"], "watch_out": summary["watch_out"],
            "settings": [{k: s.get(k) for k in ("name", "about", "secret", "required", "default")} for s in summary["settings"]],
            "homepage": item.get("homepage", ""), "server": name, "state": state,
            "tool_count": int(row.get("tool_count") or 0), "problem": str(row.get("last_error") or "")[:300],
        })
    return items


def remove_server(name: str) -> None:
    """Take a connector out: its saved sign-in, its pin and its settings entry."""
    from agent import mcp_trust
    from api.deps import _read_runtime_settings
    from api.routes.settings import _sanitize_incoming_settings
    from config import config, write_runtime_override_payload

    raw = _servers().get(name)
    url = str((raw or {}).get("url") or (raw or {}).get("endpoint") or "").strip() if isinstance(raw, dict) else ""
    if url:
        from agent import mcp_oauth
        mcp_oauth.sign_out(url)
    existing = _sanitize_incoming_settings(_read_runtime_settings())
    if isinstance(existing.get("mcp_servers"), dict):
        existing["mcp_servers"].pop(name, None)
    write_runtime_override_payload(existing)
    config.reload()
    mcp_trust.forget(name)
