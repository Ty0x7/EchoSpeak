"""Private Mode (agent/privacy.py): the policy, the gates, the backstop and the check.

Nothing here reaches the internet: blocked lookups are refused before any DNS query,
and probes go to closed local ports or are stubbed.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from agent import privacy
from config import config


@pytest.fixture
def mode(monkeypatch):
    def set_mode(value: str, **extra):
        monkeypatch.setattr(config, "privacy_mode", value, raising=False)
        monkeypatch.setattr(config, "privacy_overrides", extra.get("overrides", {}), raising=False)
        monkeypatch.setattr(config, "privacy_trusted_hosts", extra.get("trusted", []), raising=False)
        privacy.reset_log()
    set_mode("standard")
    # Name lookups are stubbed: "*.example" is the internet, "nas.lan.test" resolves to a home address.
    real = privacy._resolve

    def fake_resolve(host):
        if host.endswith((".example", ".com", ".org", ".io", ".co")):
            return ["93.184.216.34"]
        if host == "nas.lan.test":
            return ["192.168.1.40"]
        return real(host) if host == "localhost" else []
    monkeypatch.setattr(privacy, "_resolve", fake_resolve)
    with privacy._permit_lock:
        privacy._permits.clear()
        privacy._permit_ips.clear()
    return set_mode


@pytest.mark.parametrize("target,expected", [
    ("http://localhost:1234/v1", "this_pc"), ("127.0.0.1", "this_pc"), ("[::1]:8080", "this_pc"),
    ("192.168.1.20", "your_network"), ("10.0.0.5:8888", "your_network"), ("100.101.1.2", "your_network"),
    ("http://searx.local:8080", "your_network"), ("nas", "your_network"), ("nas.lan.test", "your_network"),
    ("https://api.openai.com/v1", "internet"), ("8.8.8.8", "internet"),
])
def test_where_a_host_is(mode, target, expected):
    assert privacy.locality(target) == expected


def test_trusted_hosts_count_as_yours(mode):
    mode("private", trusted=["search.my-vps.example", "*.tailnet.example"])
    assert privacy.locality("https://search.my-vps.example/search") == "trusted"
    assert privacy.locality("box.tailnet.example") == "trusted"
    assert privacy.decide("search", "https://search.my-vps.example").allowed


def test_standard_mode_changes_nothing(mode):
    for item in privacy.iter_components():
        assert privacy.decide(item.id, "https://somewhere.example").allowed, item.id


def test_private_mode_keeps_your_content_on_your_machines(mode):
    mode("private")
    cloud = privacy.decide("models", "https://api.openai.com/v1")
    assert not cloud.allowed and "local model" in cloud.reason
    assert privacy.decide("models", "http://localhost:1234/v1").allowed  # LM Studio
    assert privacy.decide("models", "http://192.168.1.30:11434").allowed  # Ollama on another PC at home
    assert not privacy.decide("search", "https://api.search.brave.com").allowed
    assert "SearXNG" in privacy.decide("search", "https://duckduckgo.com").reason
    assert privacy.decide("search", "http://searx.local:8080").allowed
    # No content leaves: reading a page (the site sees its address), downloads, the catalog.
    assert privacy.decide("web_pages", "https://news.example").allowed
    assert privacy.decide("downloads", "https://huggingface.co").allowed
    assert not privacy.decide("channels").allowed and not privacy.decide("creation").allowed


def test_offline_mode_allows_only_your_own_network(mode):
    mode("offline", overrides={"channels": "allow"})
    assert not privacy.decide("web_pages", "https://news.example").allowed
    assert not privacy.decide("downloads", "https://huggingface.co").allowed
    assert not privacy.decide("channels").allowed  # Offline is strict: overrides can't open the internet
    assert privacy.decide("models", "http://localhost:1234/v1").allowed
    assert privacy.decide("search", "http://192.168.1.9:8888").allowed
    assert privacy.status()["updates_allowed"] is False


def test_overrides_open_or_close_one_component(mode):
    mode("private", overrides={"channels": "allow", "web_pages": "block"})
    assert privacy.decide("channels").allowed
    assert not privacy.decide("web_pages", "https://news.example").allowed
    mode("standard", overrides={"updates": "block"})
    assert not privacy.status()["updates_allowed"]
    assert "switched off" in privacy.decide("updates", "https://github.com").reason


def test_gates_raise_and_log_without_content(mode):
    mode("private")
    with pytest.raises(privacy.PrivacyBlocked) as blocked:
        privacy.require("models", "https://api.anthropic.com/v1/messages?secret=1")
    assert blocked.value.component == "models"
    entry = privacy.recent()[0]
    assert entry == {**entry, "component": "models", "host": "api.anthropic.com", "allowed": False, "via": "gate"}
    assert "secret" not in str(privacy.recent())  # hosts only: no paths, queries or content
    privacy.require("web_pages", "https://news.example/a")  # allowed: the backstop now lets it through
    privacy.check_connection("socket.getaddrinfo", ("news.example", 443))
    privacy.check_connection("socket.connect", (None, ("93.184.216.34", 443)))


def test_backstop_refuses_unapproved_internet_connections(mode):
    mode("standard")
    privacy.check_connection("socket.getaddrinfo", ("tracker.example", 443))  # Standard: counted, never blocked
    assert privacy.status()["other"]["hosts"] == ["tracker.example"]
    mode("private")
    with pytest.raises(privacy.PrivacyBlocked):
        privacy.check_connection("socket.getaddrinfo", ("tracker.example", 443))
    with pytest.raises(privacy.PrivacyBlocked):
        privacy.check_connection("socket.connect", (None, ("8.8.8.8", 53)))
    privacy.check_connection("socket.getaddrinfo", ("localhost", 1234))
    privacy.check_connection("socket.getaddrinfo", ("nas.lan.test", 8080))  # resolves to a home address
    privacy.check_connection("socket.connect", (None, ("192.168.1.40", 8080)))
    privacy.check_connection("socket.connect", (None, "/tmp/socket"))  # Unix sockets: not network
    # Known destinations follow their component's rule: downloads carry no content.
    privacy.check_connection("socket.getaddrinfo", ("huggingface.co", 443))
    privacy.check_connection("socket.connect", (None, ("93.184.216.34", 443)))  # its address is now permitted
    with privacy.scope("live_data"), pytest.raises(privacy.PrivacyBlocked):
        privacy.check_connection("socket.getaddrinfo", ("weather.example", 443))
    assert privacy.status()["other"]["blocked"] >= 1


def test_the_real_backstop_in_a_process(tmp_path):
    """The audit hook itself: an internet lookup is refused in Private mode, localhost still works."""
    script = tmp_path / "probe.py"
    script.write_text(
        "import socket, sys\n"
        f"sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})\n"
        "from agent import privacy\n"
        "privacy.install_backstop()\n"
        "socket.getaddrinfo('localhost', 80)\n"
        "try:\n"
        "    socket.getaddrinfo('echospeak-backstop-test.example', 443)\n"
        "    print('LEAKED')\n"
        "except PermissionError as exc:\n"
        "    print('REFUSED', type(exc).__name__)\n",
        encoding="utf-8",
    )
    env = {**os.environ, "PRIVACY_MODE": "private", "ECHOSPEAK_DATA_DIR": str(tmp_path / "data")}
    out = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=60, env=env)
    assert "REFUSED PrivacyBlocked" in out.stdout, out.stdout + out.stderr


def test_tools_that_reach_outside_are_stopped_with_a_reason(mode):
    from agent.lean.toolbox import Toolbox

    mode("private")
    box = Toolbox(toolsets=["live"])
    result = box.run("weather_live", {"location": "Calgary"})
    assert not result.ok and "privacy setting" in result.output and "live data" in result.output.lower()
    assert privacy.tool_block_reason("create_media", {"provider": "runway"})
    assert not privacy.tool_block_reason("create_media", {"provider": "comfyui-local"})
    assert not privacy.tool_block_reason("file_read")


def test_web_search_uses_only_your_searxng_in_private_mode(mode, monkeypatch):
    from agent import web_search_providers as wsp

    mode("private")
    monkeypatch.setattr(config, "searxng_base_url", "", raising=False)
    monkeypatch.setattr(config, "web_search_provider", "auto", raising=False)
    result = wsp.run_web_search("latest local model releases", config=config, enrich_extract=False)
    assert not result.hits and any("Private mode is on" in e for e in result.errors)
    advice = wsp.describe_search_failure(result.errors)
    assert "privacy setting working as intended" in advice and "SearXNG" in advice


def test_cloud_models_are_refused_before_any_request(mode):
    from agent.lean.provider import ChatClient, Endpoint, ProviderError

    mode("private")
    client = ChatClient(Endpoint(base_url="https://api.openai.com/v1", api_key="sk-test", model="gpt-x",
                                 provider="openai", local=False))
    try:
        with pytest.raises(ProviderError) as refused:
            client.stream_turn([{"role": "user", "content": "hi"}])
    finally:
        client.close()
    assert str(refused.value).startswith("Private mode is on")


def test_hosted_mcp_servers_wait_in_private_mode(mode):
    from agent.mcp_client import MCPServerState, MCPSession

    mode("private")
    session = MCPSession(MCPServerState(name="remote", transport="streamable_http", url="https://mcp.example.com/mcp"),
                         lambda _s: None)
    assert session.start() is False
    assert "Private mode is on" in session.state.last_error


def test_offline_mode_cuts_the_sandbox_off(mode):
    from agent.lean import terminal

    mode("offline")
    assert terminal.network_policy() == "off"


def test_the_check_reports_what_this_setup_really_does(mode, monkeypatch):
    from agent import privacy_check

    mode("private")
    monkeypatch.setattr(config, "searxng_base_url", "http://192.168.1.9:8888", raising=False)
    calls = []

    def fake_probe(url, *, params=None, timeout=4.0):
        calls.append(url)
        if "8888" in url:
            return True, {"results": [{"title": "a"}, {"title": "b"}]}, ""
        return False, None, "ConnectError"
    monkeypatch.setattr(privacy_check, "_probe", fake_probe)
    report = privacy_check.run()
    by_id = {item["id"]: item for item in report["items"]}
    assert by_id["search:searxng"]["status"] == "ok" and "2 results" in by_id["search:searxng"]["detail"]
    assert by_id["search:duckduckgo"]["status"] == "blocked"
    assert all("api." not in url and "duckduckgo" not in url for url in calls)  # probes stay on your machines
    assert report["mode"] == "private" and report["not_enforced"]
