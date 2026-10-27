"""Sign-in status and sign-in for the owner's own coding agents, from Settings.

Each check runs the vendor's unmodified program in an empty folder and keeps only
"signed in, how, which plan". Sign-in always completes in the vendor's own flow
(ChatGPT's page for Codex, Anthropic's for Claude Code), and EchoSpeak never sees,
copies or stores a token, email or session. These run only when the owner asks:
the runtime catalog stays passive.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import subprocess
import tempfile
from urllib.parse import urlsplit

from agent.child_env import child_env
from agent.code_executables import executable, locate

LOGIN_SECONDS = 600
_codex_login = None  # (process, task) while an official Codex sign-in is open


def _scratch() -> str:
    # Neither program gets a project: no hooks, instructions or files of the owner's in reach.
    return tempfile.mkdtemp(prefix="echo-code-account-")


def _found(runtime: str) -> dict:
    where = locate(runtime)
    return {"installed": bool(where["path"]), "source": where["source"], "problem": where["problem"]}


async def _codex_server(on_event=None):
    from agent.code_process import RpcProcess

    async def handler(method, params, request=False):
        if on_event:
            await on_event(method, params)
        if request:
            raise ValueError("No client requests are served to an account check")
        return {}

    rpc = RpcProcess([executable("codex"), "app-server"], _scratch(), child_env(), handler)
    await rpc.request("initialize", {"clientInfo": {"name": "echospeak", "title": "Echo Code", "version": "1.0.0"}})
    await rpc.notify("initialized")
    return rpc


async def codex_status() -> dict:
    found = _found("codex")
    if not found["installed"]:
        return {**found, "signed_in": False}
    rpc = await _codex_server()
    try:
        account = (await rpc.request("account/read", {"refreshToken": False})).get("account") or {}
        models = (await rpc.request("model/list", {})).get("data", [])
    finally:
        await rpc.close()
    return {**found, "signed_in": bool(account), "method": str(account.get("type") or ""),
            "plan": str(account.get("planType") or ""),
            "models": [str(m.get("displayName") or m.get("model"))[:80] for m in models[:20] if isinstance(m, dict)],
            "login_open": bool(_codex_login and not _codex_login[1].done())}


async def codex_login() -> dict:
    """Start ChatGPT sign-in in Codex's own app-server; returns only its official URL."""
    global _codex_login
    if _codex_login and not _codex_login[1].done():
        _codex_login[1].cancel()
    finished = asyncio.Event()

    async def on_event(method, params):
        if method == "account/login/completed":
            finished.set()

    rpc = await _codex_server(on_event)
    try:
        result = await rpc.request("account/login/start", {"type": "chatgpt"})
        url = str(result.get("authUrl", ""))
        host = urlsplit(url).hostname or ""
        if urlsplit(url).scheme != "https" or not any(host == d or host.endswith("." + d) for d in ("openai.com", "chatgpt.com")):
            raise ValueError("Codex returned an unsupported sign-in URL")
    except BaseException:
        await rpc.close()
        raise

    async def wait():
        # Codex's own local callback finishes the sign-in; then its process is done.
        try:
            await asyncio.wait_for(finished.wait(), LOGIN_SECONDS)
        except asyncio.TimeoutError:
            pass
        finally:
            await rpc.close()

    _codex_login = (rpc, asyncio.create_task(wait()))
    return {"url": url}


def _claude(*args, timeout=30) -> subprocess.CompletedProcess:
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    return subprocess.run([executable("claude_cli"), *args], cwd=_scratch(), env=child_env(), capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=timeout, creationflags=flags)


def claude_status() -> dict:
    found = _found("claude_cli")
    if not found["installed"]:
        return {**found, "signed_in": False}
    done = _claude("auth", "status", "--json")
    try:
        data = json.loads(done.stdout or "{}")
    except ValueError:
        data = {}
    # Kept: signed in, how, which plan. Dropped: email, organization and anything else.
    method = str(data.get("authMethod") or "")
    return {**found, "signed_in": bool(data.get("loggedIn")), "method": method,
            "plan": str(data.get("subscriptionType") or "") if method == "claude.ai" else "",
            "uses_plan": bool(data.get("loggedIn")) and method == "claude.ai"}


def claude_login() -> dict:
    """Open Claude Code's own `claude auth login` in a console window the owner completes."""
    flags = subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0
    subprocess.Popen([executable("claude_cli"), "auth", "login"], cwd=_scratch(), env=child_env(),
                     creationflags=flags, close_fds=True)
    return {"started": True}
