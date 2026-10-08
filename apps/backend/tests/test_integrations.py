"""Echo Connections (agent/integrations.py): find, propose, approve and check connections.

Registry, npm and PyPI answers are recorded here, so nothing touches the network.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent import integrations
from agent.lean import policy

PREMIERE = {
    "server": {
        "name": "io.github.leancoderkavy/premiere-pro", "title": "MCP for Adobe Premiere Pro", "version": "1.21.0",
        "description": "Local-first MCP for supported Adobe Premiere Pro workflows.",
        "repository": {"url": "https://github.com/leancoderkavy/premiere-pro-mcp"},
        "packages": [{"registryType": "npm", "identifier": "premiere-pro-mcp", "version": "1.21.0",
                      "transport": {"type": "stdio"},
                      "environmentVariables": [{"name": "PREMIERE_UXP_TOKEN", "isSecret": True, "isRequired": False}]}],
    },
    "_meta": {"io.modelcontextprotocol.registry/official": {"status": "active", "isLatest": True}},
}
FIGMA = {"server": {"name": "com.figma.mcp/mcp", "version": "1.0.3", "description": "The Figma MCP server",
                    "remotes": [{"type": "streamable-http", "url": "https://mcp.figma.com/mcp"}]}}
NOISE = {"server": {"name": "com.tollblenders/site", "version": "1.0.1", "description": "Toll Blenders: the site's own MCP server",
                    "remotes": [{"type": "streamable-http", "url": "https://tollblenders.com/mcp"}]}}
MCPB_ONLY = {"server": {"name": "io.github.someone/bundle", "version": "0.2.0", "description": "Blender bundle",
                        "packages": [{"registryType": "mcpb", "identifier": "https://x.example/b.mcpb", "version": "0.2.0"}]}}
# REA's real server.json (github.com/morluto/rea, v6.0.0): its MCP server needs the positional "mcp".
REA = {"server": {
    "name": "io.github.morluto/rea", "title": "REA", "version": "6.0.0",
    "description": "Reverse engineer anything from your terminal or agent with one CLI and MCP server.",
    "repository": {"url": "https://github.com/morluto/rea", "source": "github"},
    "packages": [{"registryType": "npm", "identifier": "rea-agents", "version": "6.0.0", "runtimeHint": "npx",
                  "transport": {"type": "stdio"}, "packageArguments": [{"type": "positional", "value": "mcp"}]}],
}}
TEMPLATED = {"server": {"name": "eu.nordicmcp/stripe", "version": "1.0.0", "description": "Hosted Stripe",
                        "remotes": [{"type": "streamable-http", "url": "https://nordicmcp.eu/mcp/stripe/{token}"}]}}


@pytest.fixture
def offline(monkeypatch, tmp_path):
    """Fake catalogs; proposals in a temp folder."""
    calls: list[tuple[str, dict]] = []
    by_search = {"blender": [NOISE, MCPB_ONLY], "premiere": [PREMIERE], "figma": [FIGMA], "stripe": [TEMPLATED]}

    def fake_get(url, *, params=None, timeout=12.0, attempts=2):
        calls.append((url, dict(params or {})))
        if url.endswith("/servers"):
            return {"servers": by_search.get((params or {}).get("search"), [])}
        if "premiere-pro/versions/latest" in url:
            return PREMIERE
        if "morluto%2Frea/versions/latest" in url:
            return REA
        if "registry.npmjs.org" in url:
            return {"version": "1.1.0"}
        if "pypi.org" in url:
            return {"info": {"version": "2.1.9"}}
        return None

    monkeypatch.setattr(integrations, "_get_json", fake_get)
    monkeypatch.setattr(integrations, "_proposals_path", lambda: tmp_path / "proposals.json")
    monkeypatch.setattr(integrations.shutil, "which", lambda name: f"C:\\tools\\{name}.cmd" if name in {"npx", "uvx"} else None)
    return calls


def test_registry_entries_become_pinned_launches():
    launch, settings, why = integrations._launch_from_registry(PREMIERE["server"])
    assert launch == {"command": "npx", "args": ["-y", "premiere-pro-mcp@1.21.0"]} and not why
    assert settings[0]["name"] == "PREMIERE_UXP_TOKEN" and settings[0]["secret"]
    assert integrations._launch_from_registry(FIGMA["server"])[0] == {"transport": "streamable_http", "url": "https://mcp.figma.com/mcp"}
    assert "can't start" in integrations._launch_from_registry(MCPB_ONLY["server"])[2]
    assert "filled in" in integrations._launch_from_registry(TEMPLATED["server"])[2]


def test_declared_start_up_arguments_are_kept():
    """REA's server only speaks MCP when started as `rea-agents mcp`."""
    launch, _, why = integrations._launch_from_registry(REA["server"])
    assert launch == {"command": "npx", "args": ["-y", "rea-agents@6.0.0", "mcp"]} and not why
    named = {"packageArguments": [{"type": "named", "name": "--port", "value": "9877"},
                                  {"type": "named", "name": "--verbose", "isRequired": False}]}
    assert integrations._package_arguments(named) == (["--port", "9877"], False)
    assert integrations._package_arguments({"packageArguments": [{"type": "positional", "isRequired": True}]})[1]


def test_rea_guide_starts_the_mcp_server_with_a_long_timeout_and_trusted_hints(offline):
    proposal = integrations.propose("guide:rea", reason="see how an Electron app's search works")
    assert proposal["launch"]["args"] == ["-y", "rea-agents@6.0.0", "mcp"]
    config = integrations.server_config(proposal, {})
    assert config["timeout_s"] == 300 and config["accept_server_read_only_hints"] is True
    assert "GHIDRA_INSTALL_DIR" not in config.get("env", {})  # optional and unset
    assert [c["id"] for c in integrations.search("decompile this exe")["results"]][0] == "guide:rea"


def test_search_puts_reviewed_guides_first_and_ignores_name_lookalikes(offline):
    found = integrations.search("can you connect to blender")
    ids = [c["id"] for c in found["results"]]
    assert found["terms"] == ["blender"] and ids[0] == "guide:blender"
    assert "com.tollblenders/site" not in ids  # "blender" inside "tollblenders" is not Blender
    assert "io.github.someone/bundle" in ids  # listed, but marked as not startable here
    bundle = next(c for c in found["results"] if c["id"] == "io.github.someone/bundle")
    assert bundle["unsupported"] and "GitHub user someone" in bundle["publisher"]


def test_broad_words_cannot_pull_in_unrelated_listings(offline, monkeypatch):
    """Live run: "stripe payments" surfaced payments.ai; results must name the app itself."""
    unrelated = {"server": {"name": "ai.payments.managed/payments-ai-mcp", "version": "1.0.0",
                            "description": "Payments for agents",
                            "remotes": [{"type": "streamable-http", "url": "https://managed.payments.ai/api/mcp"}]}}
    official = {"server": {"name": "com.stripe/mcp", "version": "0.2.4", "description": "Stripe tools",
                           "remotes": [{"type": "streamable-http", "url": "https://mcp.stripe.com"}]}}
    real_get = integrations._get_json
    monkeypatch.setattr(integrations, "_get_json", lambda url, **kw: (
        {"servers": [unrelated, official]} if url.endswith("/servers") else real_get(url, **kw)))
    ids = [c["id"] for c in integrations.search("stripe payments")["results"]]
    assert ids == ["guide:stripe"]  # com.stripe/mcp is the guide's own server; payments.ai doesn't name Stripe


def test_a_stalled_registry_still_returns_guides_quickly(offline, monkeypatch):
    calls = []

    def stalled(url, **kw):
        calls.append(url)
        raise RuntimeError("could not reach registry.modelcontextprotocol.io: ReadTimeout")

    monkeypatch.setattr(integrations, "_get_json", stalled)
    found = integrations.search("blender render")
    assert [c["id"] for c in found["results"]] == ["guide:blender"] and "ReadTimeout" in found["error"]
    assert len(calls) == 1  # one stalled request, not one per word


def test_the_apps_own_company_ranks_above_third_parties():
    official = integrations._candidate_from_registry(FIGMA)
    third_party = integrations._candidate_from_registry({"server": {
        "name": "io.github.someone/figma", "version": "1.0.0", "description": "The Figma MCP server",
        "remotes": [{"type": "streamable-http", "url": "https://figma.someone.dev/mcp"}]}})
    assert official["publisher"].startswith("published by mcp.figma.com (domain verified")
    assert integrations._score(official, ["figma"]) > integrations._score(third_party, ["figma"])


def test_a_dozen_fields_have_guides():
    fields = {g["field"] for g in integrations.GUIDES}
    assert len(integrations.GUIDES) >= 15 and len(fields) >= 12
    for item in integrations.GUIDES:
        assert item["how"]["kind"] in {"npm", "pypi", "http", "signin", "builtin"}, item["id"]
        assert all(not s.get("secret") or s.get("required") is not None for s in item["settings"])


def test_proposing_pins_the_version_and_never_holds_keys(offline):
    proposal = integrations.propose("guide:obs", reason="switch scenes during my stream")
    assert proposal["status"] == "waiting" and proposal["server_name"] == "obs"
    assert proposal["launch"] == {"command": "npx", "args": ["-y", "obs-mcp@1.1.0"]}
    password = next(s for s in proposal["settings"] if s["name"] == "OBS_WEBSOCKET_PASSWORD")
    assert password["secret"] and "value" not in password
    again = integrations.propose("guide:obs")
    assert again["id"] == proposal["id"] and len(integrations.proposals("waiting")) == 1  # updated, not duplicated
    blender = integrations.propose("guide:blender")
    assert blender["launch"]["args"] == ["mcp-for-blender==2.1.9"]
    assert blender["capability_policies"] == {"execute_blender_code": "destructive"}


def test_registry_backed_guides_add_the_packages_own_settings(offline):
    proposal = integrations.propose("guide:premiere")
    assert proposal["launch"]["args"] == ["-y", "premiere-pro-mcp@1.21.0"]
    assert "PREMIERE_UXP_TOKEN" in {s["name"] for s in proposal["settings"]}


def test_proposals_that_cannot_work_are_refused(offline):
    with pytest.raises(ValueError, match="browser sign-in"):
        integrations.propose("guide:notion")
    with pytest.raises(ValueError, match="already has this"):
        integrations.propose("guide:obsidian")
    with pytest.raises(ValueError, match="already set up"):
        integrations.propose("guide:obs", configured={"obs": {"command": "npx", "args": ["-y", "obs-mcp@1.1.0"]}})
    with pytest.raises(ValueError, match="exactly as"):
        integrations.propose("../../etc")


def test_waiting_suggestions_are_capped(offline, monkeypatch):
    monkeypatch.setattr(integrations, "MAX_WAITING", 1)
    integrations.propose("guide:obs")
    with pytest.raises(ValueError, match="already waiting"):
        integrations.propose("guide:blender")


def test_approved_settings_become_an_mcp_server(offline):
    obs = integrations.propose("guide:obs")
    config = integrations.server_config(obs, {"OBS_WEBSOCKET_PASSWORD": "pw"})
    assert config["command"] == "C:\\tools\\npx.cmd" and config["args"] == ["-y", "obs-mcp@1.1.0"]
    assert config["env"] == {"OBS_WEBSOCKET_URL": "ws://localhost:4455", "OBS_WEBSOCKET_PASSWORD": "pw"}
    with pytest.raises(ValueError, match="required"):
        integrations.server_config(obs, {})
    with pytest.raises(ValueError, match="Not a setting"):
        integrations.server_config(obs, {"OBS_WEBSOCKET_PASSWORD": "pw", "PATH": "C:\\evil"})
    github = integrations.propose("guide:github")
    remote = integrations.server_config(github, {"Authorization": "github_pat_x"})
    assert remote["url"] == "https://api.githubcopilot.com/mcp/" and remote["headers"] == {"Authorization": "Bearer github_pat_x"}
    assert "command" not in remote and "env" not in remote


def test_check_reports_waiting_running_and_broken_connections(offline):
    integrations.propose("guide:obs")
    assert "waiting for the user to review" in integrations.check("obs", status={"servers": []})
    blender = integrations.propose("guide:blender")
    integrations.mark(blender["id"], "approved")
    tools = {"mcp__blender__get_scene_info": SimpleNamespace(is_action=False),
             "mcp__blender__execute_blender_code": SimpleNamespace(is_action=True)}
    running = {"servers": [{"name": "blender", "running": True, "tool_count": 2}]}
    report = integrations.check("blender", status=running, registry_tools=tools)
    assert "connected, 2 tools" in report and "get_scene_info" in report.split("read-only one:")[1]
    assert integrations.get_proposal(blender["id"])["connected_at"]  # remembered as a working setup
    refused = {"servers": [{"name": "blender", "running": False, "last_error": "[WinError 10061] connection refused"}]}
    assert "Blender isn't answering" in integrations.check("blender", status=refused)
    for error, expect in [("timed out", "first start downloads"), ("401 Unauthorized", "refused the key"),
                          ("spawn ENOENT", "wasn't found")]:
        assert expect in integrations.check("blender", status={"servers": [{"name": "blender", "last_error": error}]})
    assert "find_integrations" in integrations.check("nothing", status={"servers": []})


def test_registry_text_counts_as_outside_content():
    assert policy.is_untrusted_source("find_integrations") and policy.is_untrusted_source("check_integration")
    assert not policy.is_external_action("propose_integration")  # it only queues a suggestion for the user


def _fake_registry(monkeypatch, count: int):
    from agent.tool_registry import ToolRegistry

    entries = {
        f"mcp__photoshop__tool_{i}": SimpleNamespace(
            name=f"mcp__photoshop__tool_{i}", origin="mcp", mcp_server="photoshop", available=True, policy_flags=(),
            description="Apply a gaussian blur to a layer" if i == 7 else f"Photoshop operation {i}",
            input_schema={"type": "object", "properties": {"layer": {"type": "string"}}}, func=None, is_action=True)
        for i in range(count)
    }
    monkeypatch.setattr(ToolRegistry, "get_all", classmethod(lambda cls: dict(entries)))
    monkeypatch.setattr(ToolRegistry, "get", classmethod(lambda cls, name: entries.get(name)))
    monkeypatch.setattr(ToolRegistry, "get_names", classmethod(lambda cls: list(entries)))
    return entries


def test_many_connected_app_tools_load_on_demand(monkeypatch):
    from agent.lean.toolbox import Toolbox

    _fake_registry(monkeypatch, 40)
    box = Toolbox(toolsets=["skills"])
    sent = {s["function"]["name"] for s in box.schemas()}
    assert "find_tools" in sent and not any(n.startswith("mcp__") for n in sent)
    assert any("40 tools from connected apps" in note and "photoshop (40)" in note for note in box.notes)
    version = box.schema_version
    out = box.run("find_tools", {"query": "blur a layer"}).output
    assert "mcp__photoshop__tool_7" in out.splitlines()[1]
    assert box.schema_version > version and "mcp__photoshop__tool_7" in {s["function"]["name"] for s in box.schemas()}


def test_a_few_connected_app_tools_are_sent_as_before(monkeypatch):
    from agent.lean.toolbox import Toolbox

    _fake_registry(monkeypatch, 5)
    names = {s["function"]["name"] for s in Toolbox(toolsets=["skills"]).schemas()}
    assert "find_tools" not in names and sum(n.startswith("mcp__") for n in names) == 5


def test_trusted_hints_let_session_only_tools_run_without_asking():
    """REA marks pure analysis readOnly=false (it records Evidence) but closed-world,
    non-destructive and idempotent. With the owner trusting its hints, those run unasked."""
    from agent.mcp_client import MCPManager, MCPServerState

    trusted = MCPServerState(name="rea", accept_server_read_only_hints=True)
    untrusted = MCPServerState(name="rea")
    session_only = {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False, "idempotentHint": True}
    risk = MCPManager._capability_risk
    assert risk(None, trusted, {"name": "procedure_pseudo_code", "annotations": session_only}) == ("safe", False)
    assert risk(None, untrusted, {"name": "procedure_pseudo_code", "annotations": session_only}) == ("moderate", True)
    launches = {**session_only, "openWorldHint": True}  # open_binary starts Ghidra
    assert risk(None, trusted, {"name": "open_binary", "annotations": launches}) == ("moderate", True)
    silent = {k: v for k, v in session_only.items() if k != "openWorldHint"}  # MCP's default is open-world
    assert risk(None, trusted, {"name": "x", "annotations": silent}) == ("moderate", True)
    discards = {**session_only, "destructiveHint": True}
    assert risk(None, trusted, {"name": "close_binary", "annotations": discards}) == ("destructive", True)


def test_the_trust_pin_covers_tool_hints(monkeypatch, tmp_path):
    from agent import mcp_trust

    monkeypatch.setattr(mcp_trust, "_path", lambda: tmp_path / "mcp-trust.json")
    tools = [{"name": "analyze_function", "description": "d", "inputSchema": {},
              "annotations": {"destructiveHint": False, "openWorldHint": False, "idempotentHint": True}}]
    mcp_trust.approve("rea", {"command": "npx"})
    assert mcp_trust.tools_ok("rea", tools)
    flipped = [{**tools[0], "annotations": {**tools[0]["annotations"], "openWorldHint": False, "destructiveHint": True}}]
    assert not mcp_trust.tools_ok("rea", flipped)  # same tool, new effect claims: approve again
    # A pin from before annotations were pinned upgrades once, without asking the owner again.
    mcp_trust._save({"servers": {"old": {"config": "x", "tools": mcp_trust._legacy_tools_fingerprint(tools)}}})
    assert mcp_trust.tools_ok("old", tools)
    assert mcp_trust._load()["servers"]["old"]["tools"].startswith("v2:")


def test_structured_results_are_not_sent_twice():
    from agent.mcp_client import _drop_duplicate_text

    payload = {"result": {"functions": 3}, "evidence_id": "ev_1"}
    result = {"content": [{"type": "text", "text": '{"result": {"functions": 3}, "evidence_id": "ev_1"}'},
                          {"type": "text", "text": "Ghidra 12.1 finished"}],
              "structuredContent": payload, "isError": False}
    assert _drop_duplicate_text(result)["content"] == [{"type": "text", "text": "Ghidra 12.1 finished"}]
    assert _drop_duplicate_text({"content": [{"type": "text", "text": "{}"}]})["content"]  # no structured copy: untouched


def test_guests_never_get_connection_tools():
    from agent.lean.runtime import GUEST_TOOLS

    for tools in GUEST_TOOLS.values():
        assert not {"find_integrations", "propose_integration", "check_integration", "find_tools"} & set(tools)
