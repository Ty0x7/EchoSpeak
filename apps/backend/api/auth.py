"""Who may call the local API: loopback detection, the optional shared API key, the DNS-rebinding
host check and the allowed browser origins.
"""

import os
import hmac
from typing import Any

from fastapi import Request

from config import config


_ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://localhost:5174",
    "http://localhost:5175",
    "http://localhost:5176",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:5174",
    "http://127.0.0.1:5175",
    "http://127.0.0.1:5176",
    "tauri://localhost",
    "http://tauri.localhost",
    "https://tauri.localhost",
] + [o.strip() for o in os.getenv("ECHOSPEAK_ALLOWED_ORIGINS", "").split(",") if o.strip()]

_PUBLIC_AUTH_PATHS = {
    "/",
    "/health",
    "/metrics",
    "/favicon.ico",
    "/.well-known/agent.json",
}


def _is_local_client(host: str) -> bool:
    h = str(host or "").strip().lower()
    return h in {"127.0.0.1", "::1", "localhost"} or h.startswith("127.")


def _configured_api_auth_key() -> str:
    return str(getattr(config, "api_auth_key", "") or os.getenv("API_AUTH_KEY", "") or "").strip()


def _extract_api_auth_key_from_headers(headers: Any) -> str:
    for key in ("x-echospeak-key", "x-api-key", "x-admin-key"):
        val = str(headers.get(key) or "").strip()
        if val:
            return val
    auth = str(headers.get("authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    # Browsers cannot add arbitrary headers to WebSocket handshakes. The
    # desktop bridge therefore supplies the per-launch key as a constrained
    # subprotocol token; it is not placed in a URL or persisted to storage.
    for protocol in str(headers.get("sec-websocket-protocol") or "").split(","):
        value = protocol.strip()
        if value.startswith("echospeak-auth-"):
            return value[len("echospeak-auth-"):].strip()
    return ""


def _api_auth_required_for_host(host: str) -> bool:
    local = _is_local_client(host)
    if not local:
        # Defense in depth for alternate ASGI launchers/proxies that bypass
        # start_server's bind-time check.
        return True
    return bool(
        getattr(config, "api_auth_enabled", False)
        and not bool(getattr(config, "api_auth_localhost_bypass", True))
    )


def _api_auth_ok(headers: Any, host: str) -> bool:
    if not _api_auth_required_for_host(host):
        return True
    if not bool(getattr(config, "api_auth_enabled", False)):
        return False
    expected = _configured_api_auth_key()
    if not expected:
        return False
    provided = _extract_api_auth_key_from_headers(headers)
    return bool(provided and hmac.compare_digest(provided, expected))


_LOCAL_HOSTNAMES = {"localhost", "127.0.0.1", "::1", "tauri.localhost"}


def _request_hostname(host_header: str) -> str:
    host = str(host_header or "").strip().lower()
    if host.startswith("["):  # [::1]:8000
        return host[1:host.find("]")] if "]" in host else host
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def _origin_allowed(origin: str, host_header: str) -> bool:
    origin = str(origin or "").strip().rstrip("/")
    if origin in _ALLOWED_ORIGINS:
        return True
    # The page the backend serves itself (same origin).
    netloc = origin.split("://", 1)[-1]
    return bool(netloc) and netloc.lower() == str(host_header or "").strip().lower()


def _local_request_guard(method: str, host_header: str, origin: str, client_ip: str) -> str:
    """Reason to refuse a request to the local API, or ''.

    - DNS rebinding: a web page whose domain is re-pointed at 127.0.0.1 sends
      its own name in the Host header. Local requests must name this machine.
    - Cross-site requests: browsers always send Origin on cross-origin writes;
      only the app's own origins may change anything. Clients without an
      Origin header (curl, bots, scripts) are unaffected.
    """
    if _is_local_client(client_ip) and not _api_auth_required_for_host(client_ip):
        extra = {h.strip().lower() for h in os.getenv("ECHOSPEAK_ALLOWED_HOSTS", "").split(",") if h.strip()}
        hostname = _request_hostname(host_header)
        if hostname and hostname not in _LOCAL_HOSTNAMES | extra:
            return f"Host '{hostname}' is not allowed"
    if origin and method.upper() not in {"GET", "HEAD", "OPTIONS"} and not _origin_allowed(origin, host_header):
        return f"Origin '{origin}' is not allowed"
    return ""


def _get_client_ip(request: Request) -> str:
    """Extract client IP, trusting proxy headers only when explicitly configured."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded and bool(getattr(config, "api_trust_proxy_headers", False)):
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
