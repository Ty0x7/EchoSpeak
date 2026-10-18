"""MCP sign-in end to end against a fake server: discovery, registration, browser, issuer check, tokens."""
import asyncio
import json
import threading
import urllib.request
from urllib.parse import parse_qs, urlparse

import pytest

from agent import mcp_oauth

httpx = mcp_oauth.http_module()  # the client the MCP SDK's auth plugs into

MCP = "https://mcp.example.com/mcp"
ISSUER = "https://auth.example.com"


def fake_server(seen, issuer_in_reply=ISSUER):
    def handle(request: httpx.Request) -> httpx.Response:
        url, path = str(request.url), request.url.path
        seen.append(f"{request.method} {request.url.host}{path}")
        if request.url.host == "mcp.example.com" and path == "/mcp":
            if request.headers.get("authorization") == "Bearer tok-1":
                return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {}})
            return httpx.Response(401, headers={"WWW-Authenticate": 'Bearer resource_metadata="https://mcp.example.com/.well-known/oauth-protected-resource/mcp"'})
        if "oauth-protected-resource" in path:
            return httpx.Response(200, json={"resource": MCP, "authorization_servers": [ISSUER]})
        if request.url.host == "auth.example.com" and "oauth-authorization-server" in path:
            return httpx.Response(200, json={
                "issuer": ISSUER, "authorization_endpoint": f"{ISSUER}/authorize", "token_endpoint": f"{ISSUER}/token",
                "registration_endpoint": f"{ISSUER}/register", "response_types_supported": ["code"],
                "grant_types_supported": ["authorization_code", "refresh_token"], "code_challenge_methods_supported": ["S256"],
                "token_endpoint_auth_methods_supported": ["none"], "authorization_response_iss_parameter_supported": True})
        if url == f"{ISSUER}/register":
            body = json.loads(request.content)
            return httpx.Response(201, json={**body, "client_id": "echo-client"})
        if url == f"{ISSUER}/token":
            form = parse_qs(request.content.decode())
            assert form["code"] == ["code-123"] and form["code_verifier"][0]  # PKCE
            return httpx.Response(200, json={"access_token": "tok-1", "token_type": "Bearer", "expires_in": 3600, "refresh_token": "ref-1"})
        return httpx.Response(404)
    return handle


@pytest.fixture
def server(monkeypatch, tmp_path):
    from agent import credential_broker
    monkeypatch.setattr(mcp_oauth, "_index_path", lambda: tmp_path / "oauth.json")
    broker = credential_broker.CredentialBroker(tmp_path / "credentials")
    monkeypatch.setattr(credential_broker, "get_credential_broker", lambda: broker)
    monkeypatch.setattr(mcp_oauth, "CALLBACK_PORT", 0)  # any free port in tests
    seen = []
    real = httpx.AsyncClient

    def client(**kwargs):
        return real(transport=httpx.MockTransport(fake_server(seen, browser.issuer)), **kwargs)
    monkeypatch.setattr(httpx, "AsyncClient", client)

    class Browser:
        issuer = ISSUER
        opened = []

        def open(self, url):
            # The user signs in; the server sends the browser back to EchoSpeak's listener.
            self.opened.append(url)
            query = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
            back = f"{query['redirect_uri']}?code=code-123&state={query['state']}&iss={self.issuer}"
            threading.Thread(target=lambda: urllib.request.urlopen(back, timeout=5).read(), daemon=True).start()
            return True
    browser = Browser()
    monkeypatch.setattr(mcp_oauth.webbrowser, "open", browser.open)
    return seen, browser


def sign_in():
    mcp_oauth._flows["notion"] = {"state": "signing_in", "url": "", "error": ""}
    asyncio.run(mcp_oauth._sign_in("notion", MCP, {}))


def test_sign_in_registers_signs_in_and_saves_tokens_encrypted(server, tmp_path):
    seen, browser = server
    sign_in()
    assert mcp_oauth.signed_in(MCP)
    assert "POST auth.example.com/register" in seen and "POST auth.example.com/token" in seen
    query = parse_qs(urlparse(browser.opened[0]).query)
    assert query["code_challenge_method"] == ["S256"] and query["redirect_uri"][0].startswith("http://127.0.0.1:")
    stored = asyncio.run(mcp_oauth.BrokerStorage(MCP).get_tokens())
    assert stored.access_token == "tok-1" and stored.refresh_token == "ref-1"
    raw = b"".join(p.read_bytes() for p in (tmp_path / "credentials").glob("*.bin"))
    assert b"tok-1" not in raw and b"ref-1" not in raw  # DPAPI ciphertext, not plain JSON
    assert seen[-1] == "POST mcp.example.com/mcp"  # the request that needed sign-in went through


def test_a_reply_from_another_issuer_is_refused(server):
    seen, browser = server
    browser.issuer = "https://evil.example.com"  # mix-up attack: the code came back from the wrong server
    with pytest.raises(Exception):
        sign_in()
    assert not mcp_oauth.signed_in(MCP)
    assert "POST auth.example.com/token" not in seen


def test_sign_out_forgets_the_tokens(server):
    sign_in()
    assert mcp_oauth.sign_out(MCP) and not mcp_oauth.signed_in(MCP)


def test_status_reports_what_the_owner_must_do():
    assert mcp_oauth.status("x", "https://other.example.com/mcp", "HTTP 401 Unauthorized")["state"] == "needed"
    assert mcp_oauth.status("x", "https://other.example.com/mcp", "")["state"] == ""
