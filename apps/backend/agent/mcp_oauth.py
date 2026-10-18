"""Signing in to hosted MCP servers (OAuth 2.1, MCP spec 2026-07-28).

The official SDK's OAuthClientProvider does the protocol: resource and
authorization-server discovery, PKCE, issuer checks (RFC 9207), client ID
metadata documents when you host one (Settings > Advanced), else dynamic client
registration, and token refresh. EchoSpeak adds the parts a desktop app owns:

- Tokens and the registered client live in the DPAPI credential broker, keyed by
  the server's address, readable only by your Windows account.
- Sign-in runs only when you click Sign in: your browser opens the server's
  page, and a one-shot listener on 127.0.0.1 catches the reply (RFC 8252).
- A normal connection uses saved tokens and refreshes them, but never opens a
  browser. If a server asks for sign-in then, it reports "sign-in needed".
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import time
import webbrowser
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

from loguru import logger

CALLBACK_PORT = 33418  # a fixed port keeps the registered redirect address stable; another is used if busy
SIGN_IN_SECONDS = 300
_lock = threading.Lock()
# server name -> {"state": "signing_in" | "signed_in" | "failed", "url": authorization page, "error": text}
_flows: dict[str, dict[str, Any]] = {}


def _key(url: str) -> str:
    return hashlib.sha256(str(url).strip().rstrip("/").encode()).hexdigest()[:24]


def _index_path() -> Path:
    from config import DATA_DIR
    return Path(DATA_DIR) / "mcp" / "oauth.json"


def _index() -> dict[str, Any]:
    try:
        return json.loads(_index_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_index(data: dict[str, Any]) -> None:
    path = _index_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


class BrokerStorage:
    """The SDK's TokenStorage, kept in the DPAPI credential broker."""

    def __init__(self, url: str) -> None:
        self.key = _key(url)

    def _get(self, field: str) -> Any:
        from agent.credential_broker import CredentialBrokerError, get_credential_broker
        ref = (_index().get(self.key) or {}).get(field)
        if not ref:
            return None
        try:
            return get_credential_broker().resolve(ref)
        except CredentialBrokerError:
            return None

    def _set(self, field: str, value: dict[str, Any]) -> None:
        from agent.credential_broker import get_credential_broker
        with _lock:
            data = _index()
            row = dict(data.get(self.key) or {})
            row[field] = get_credential_broker().put(value, label=f"MCP sign-in {field}", reference=row.get(field, ""))
            row["updated_at"] = time.time()
            data[self.key] = row
            _save_index(data)

    async def get_tokens(self):
        from mcp.shared.auth import OAuthToken
        value = self._get("tokens")
        return OAuthToken.model_validate(value) if value else None

    async def set_tokens(self, tokens) -> None:
        self._set("tokens", tokens.model_dump(mode="json", exclude_none=True))

    async def get_client_info(self):
        from mcp.shared.auth import OAuthClientInformationFull
        value = self._get("client")
        return OAuthClientInformationFull.model_validate(value) if value else None

    async def set_client_info(self, client_info) -> None:
        self._set("client", client_info.model_dump(mode="json", exclude_none=True))


def signed_in(url: str) -> bool:
    return bool((_index().get(_key(url)) or {}).get("tokens"))


def sign_out(url: str) -> bool:
    from agent.credential_broker import get_credential_broker
    with _lock:
        data = _index()
        row = data.pop(_key(url), None)
        _save_index(data)
    for ref in (row or {}).values():
        if isinstance(ref, str) and ref.startswith("credential://"):
            try:
                get_credential_broker().delete(ref)
            except Exception:
                pass
    return row is not None


def _metadata(redirect_uri: str):
    from mcp.shared.auth import OAuthClientMetadata
    return OAuthClientMetadata(client_name="EchoSpeak", client_uri="https://github.com/Ty0x7/EchoSpeak",
                               redirect_uris=[redirect_uri], grant_types=["authorization_code", "refresh_token"],
                               response_types=["code"], token_endpoint_auth_method="none", application_type="native")


def _metadata_url() -> Optional[str]:
    from config import config
    return str(getattr(config, "mcp_oauth_client_metadata_url", "") or "").strip() or None


def provider(url: str):
    """For a normal connection: saved tokens, refreshed as needed, never an interactive sign-in."""
    from mcp.client.auth import OAuthClientProvider, OAuthFlowError

    async def no_browser(_url: str) -> None:
        raise OAuthFlowError("This server needs you to sign in again: use Sign in in Settings › Advanced › Connections & skills.")

    async def no_callback():
        raise OAuthFlowError("Sign-in needed")

    return OAuthClientProvider(url, _metadata(f"http://127.0.0.1:{CALLBACK_PORT}/callback"), BrokerStorage(url),
                               redirect_handler=no_browser, callback_handler=no_callback, client_metadata_url=_metadata_url())


def needs_sign_in(error: str) -> bool:
    text = str(error or "").lower()
    return any(marker in text for marker in ("401", "unauthorized", "invalid_token", "sign in", "sign-in"))


# ── interactive sign-in ─────────────────────────────────────────────────

_PAGE = (b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nConnection: close\r\n\r\n"
         b"<!doctype html><title>EchoSpeak</title><body style='font:16px system-ui;padding:40px'>"
         b"<h2>Signed in</h2><p>You can close this tab and go back to EchoSpeak.</p></body>")


async def _listen(state_holder: dict[str, Any]) -> tuple[asyncio.AbstractServer, asyncio.Future, int]:
    future: asyncio.Future = asyncio.get_running_loop().create_future()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            line = (await asyncio.wait_for(reader.readline(), 10)).decode("latin-1")
            target = line.split(" ")[1] if line.count(" ") >= 2 else ""
            parsed = urlparse(target)
            query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
            if parsed.path == "/callback" and not future.done() and (query.get("code") or query.get("error")):
                future.set_result(query)
            writer.write(_PAGE)
            await writer.drain()
        except Exception:
            pass
        finally:
            writer.close()

    try:
        server = await asyncio.start_server(handle, "127.0.0.1", CALLBACK_PORT)
    except OSError:
        server = await asyncio.start_server(handle, "127.0.0.1", 0)  # the usual port is taken
    port = server.sockets[0].getsockname()[1]
    return server, future, port


def http_module():
    """The HTTP client the MCP SDK's auth plugs into: httpx2 for SDK 2.x, httpx for 1.x."""
    try:
        import httpx2
        return httpx2
    except ImportError:
        import httpx
        return httpx


async def _sign_in(name: str, url: str, headers: dict[str, str]) -> None:
    httpx = http_module()
    from mcp.client.auth import OAuthClientProvider
    from mcp.shared.auth import AuthorizationCodeResult

    flow = _flows[name]
    server, reply, port = await _listen(flow)
    try:
        async def open_browser(authorization_url: str) -> None:
            flow["url"] = authorization_url
            try:
                webbrowser.open(authorization_url)
            except Exception:
                logger.info("Could not open a browser for {}; the settings page shows the link", name)

        async def wait_for_reply() -> AuthorizationCodeResult:
            query = await asyncio.wait_for(reply, SIGN_IN_SECONDS)
            if query.get("error"):
                raise RuntimeError(f"The server refused sign-in: {query.get('error_description') or query['error']}")
            return AuthorizationCodeResult(code=query["code"], state=query.get("state"), iss=query.get("iss"))

        auth = OAuthClientProvider(url, _metadata(f"http://127.0.0.1:{port}/callback"), BrokerStorage(url),
                                   redirect_handler=open_browser, callback_handler=wait_for_reply,
                                   client_metadata_url=_metadata_url())
        # Any authenticated request starts the flow; a ping is the smallest MCP request.
        async with httpx.AsyncClient(auth=auth, headers=headers, timeout=30, follow_redirects=True) as client:
            await client.post(url, json={"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {}},
                              headers={"Accept": "application/json, text/event-stream", "Mcp-Method": "ping"})
    finally:
        server.close()


def start_sign_in(name: str, url: str, headers: Optional[dict[str, str]] = None, on_done=None) -> dict[str, Any]:
    """Begin signing in to one server in the background; poll status(name) for the page and the outcome."""
    from agent import privacy
    privacy.require("remote_connections", url)  # Private mode keeps hosted servers off, sign-in included
    with _lock:
        if (_flows.get(name) or {}).get("state") == "signing_in":
            return dict(_flows[name])
        _flows[name] = {"state": "signing_in", "url": "", "error": "", "started_at": time.time()}

    def run() -> None:
        try:
            asyncio.run(_sign_in(name, url, dict(headers or {})))
            ok = signed_in(url)
            _flows[name].update(state="signed_in" if ok else "failed",
                                error="" if ok else "The server didn't return a sign-in. Check its address.")
        except Exception as exc:
            message = "Sign-in timed out" if isinstance(exc, asyncio.TimeoutError) else str(exc)[:300]
            _flows[name].update(state="failed", error=message or type(exc).__name__)
            logger.info("MCP sign-in for {} failed: {}", name, message)
        if _flows[name]["state"] == "signed_in" and on_done is not None:
            try:
                on_done()
            except Exception:
                logger.exception("Reconnecting after MCP sign-in failed")

    threading.Thread(target=run, name=f"mcp-sign-in-{name}", daemon=True).start()
    return dict(_flows[name])


def status(name: str, url: str, last_error: str = "") -> dict[str, Any]:
    flow = dict(_flows.get(name) or {})
    if flow.get("state") == "signing_in":
        return {"state": "signing_in", "url": flow.get("url", "")}
    if flow.get("state") == "failed":
        return {"state": "failed", "error": flow.get("error", "")}
    if signed_in(url):
        return {"state": "needed" if needs_sign_in(last_error) else "signed_in"}
    return {"state": "needed" if needs_sign_in(last_error) else ""}
