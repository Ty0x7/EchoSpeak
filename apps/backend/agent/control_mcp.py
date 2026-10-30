"""Optional local stdio MCP bridge. Configure ECHOSPEAK_CONTROL_URL and TOKEN.

Never pass an owner API key or provider credential. Token grants are created and
revoked in Code's MCP access panel. No remote bind, OAuth or provider login here.
"""
import json
import os
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler
from urllib.error import HTTPError


def call(action, arguments):
    url = os.environ.get("ECHOSPEAK_CONTROL_URL", "http://127.0.0.1:8000").rstrip("/")
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path:
        raise ValueError("Control URL must identify a local EchoSpeak backend origin")
    token = os.environ.get("ECHOSPEAK_CONTROL_TOKEN", "")
    if not token:
        raise ValueError("Create a scoped MCP client in Echo Code and configure ECHOSPEAK_CONTROL_TOKEN")
    payload = json.dumps({"action": action, "arguments": arguments}).encode()
    request = Request(url + "/echo-code/control/invoke", data=payload,
                      headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    # A local bridge never sends a grant through an ambient HTTP proxy.
    try:
        class NoRedirect(HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                raise ValueError("Control redirects are refused")
        with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=20) as response:
            if response.url != request.full_url:
                raise ValueError("Control redirects are refused")
            return json.loads(response.read(1_000_000))
    except HTTPError as exc:
        raise ValueError(f"EchoSpeak refused this control call (HTTP {exc.code}); check the grant and scope") from None


def server():
    from mcp.server.mcpserver import MCPServer
    mcp = MCPServer("EchoSpeak project control")

    @mcp.tool()
    def echo_projects() -> dict:
        """List only the projects and agent identities explicitly granted to this client."""
        return call("projects", {})

    @mcp.tool()
    def echo_runtimes() -> dict:
        """Passively list native and independently owned coding runtime availability. No probes, login or process launch."""
        return call("runtimes", {})

    @mcp.tool()
    def echo_threads() -> dict:
        """List scoped native Code, external Code and Agents conversations, with observed status."""
        return call("threads", {})

    @mcp.tool()
    def echo_thread_status(id: str, external: bool = False) -> dict:
        """Read a scoped thread's state. This does not connect a runtime or grant tool approval."""
        return call("status", {"id": id, "external": external})

    @mcp.tool()
    def echo_thread_create(request_id: str, project_id: str = "", agent_id: str = "") -> dict:
        """Create a native Code thread or open an approved persistent agent's conversation. Requires control access."""
        return call("create", {"request_id": request_id, "project_id": project_id, "agent_id": agent_id})

    @mcp.tool()
    def echo_thread_send(id: str, message: str, request_id: str, external: bool = False) -> dict:
        """Send through the original harness, or queue behind its active native turn. External runtime must already be connected. Requires control access."""
        return call("send", {"id": id, "message": message, "request_id": request_id, "external": external})

    @mcp.tool()
    def echo_thread_stop(id: str, external: bool = False) -> dict:
        """Stop only this scoped thread; pause its native queue. Never stop other work."""
        return call("stop", {"id": id, "external": external})

    @mcp.tool()
    def echo_thread_triage(id: str, activity: str, action: str, snooze_seconds: int = 0, external: bool = False) -> dict:
        """Settle, snooze or reopen this exact observed activity. Live work/decisions remain protected."""
        return call("triage", {"id": id, "external": external, "activity": activity, "action": action, "snooze_seconds": snooze_seconds})

    return mcp


if __name__ == "__main__":
    server().run(transport="stdio")
