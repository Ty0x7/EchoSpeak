"""Official harness adapters. No adapter enters Echo's model or tool router."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shutil
import sys
import time

from agent.child_env import child_env
from agent.code_process import RpcProcess


# Claude Code's effort levels (code.claude.com/docs/en/model-config, "Adjust effort level"). A level the
# model doesn't support falls back to the highest one it does, inside Claude Code itself.
CLAUDE_EFFORTS = ("low", "medium", "high", "xhigh", "max")


def codex_policy(root):
    return {"cwd": str(root), "approvalPolicy": "untrusted", "approvalsReviewer": "user",
            "sandbox": "workspace-write", "config": {"sandbox_workspace_write.network_access": False}}


class Codex:
    def __init__(self, session):
        self.session = session
        self.rpc = None
        self._usage_attempt = None

    async def refresh_usage(self, force=False):
        if not self.rpc or self.rpc.closed:
            return
        if not force and self._usage_attempt is not None and time.monotonic() - self._usage_attempt < 300:
            return
        self._usage_attempt = time.monotonic()
        from agent.code_limits import codex
        try:
            usage = codex(await self.rpc.request('account/rateLimits/read', {}, timeout=3))
        except (RuntimeError, TimeoutError):
            self.session.row['subscription_usage'] = {**self.session.row.get('subscription_usage', {}),
                'refresh_error': 'Refresh failed. Showing the last observation; retrying within 5 minutes.'}
            self.session.save()
            return  # Retain the last observation and its honest timestamp.
        self.session.row['subscription_usage'] = usage
        self.session.save()

    async def connect(self):
        from agent.code_executables import executable
        self.rpc = RpcProcess([executable("codex"), "app-server"], self.session.root, child_env(), self.event)
        await self.rpc.request("initialize", {"clientInfo": {"name": "echospeak", "title": "Echo Code", "version": "1.0.0"}})
        await self.rpc.notify("initialized")
        account = await self.rpc.request("account/read", {"refreshToken": False})
        self.session.account = {"connected": bool(account.get("account")),
                                "type": (account.get("account") or {}).get("type", "none")}
        from agent.code_accounts import codex_models
        self.session.models = codex_models((await self.rpc.request("model/list", {})).get("data", []))
        from agent.code_limits import codex
        self._usage_attempt = time.monotonic()
        try:
            self.session.row['subscription_usage'] = codex(await self.rpc.request('account/rateLimits/read', {}))
        except (RuntimeError, TimeoutError):
            self.session.row['subscription_usage'] = codex({})
        default = next((row for row in self.session.models if row.get("isDefault")), {})
        if not self.session.row.get("model"):
            self.session.record_model(default.get("model"))

    async def login(self):
        result = await self.rpc.request("account/login/start", {"type": "chatgpt"})
        # Return only the official URL and opaque login id, never native tokens.
        from urllib.parse import urlsplit
        url = str(result.get("authUrl", ""))
        host = urlsplit(url).hostname or ""
        if urlsplit(url).scheme != "https" or not any(host == domain or host.endswith("." + domain)
                for domain in ("openai.com", "chatgpt.com")):
            raise ValueError("Codex returned an unsupported sign-in URL")
        return {"url": url, "login_id": result.get("loginId")}

    async def ensure_thread(self, model=""):
        policy = codex_policy(self.session.root)
        if model:
            policy['model'] = model
        if self.session.row.get("native_id"):
            result = await self.rpc.request("thread/resume", {**policy, "threadId": self.session.row["native_id"]})
        else:
            result = await self.rpc.request("thread/start", policy)
        self.session.row["native_id"] = result["thread"]["id"]
        self.session.record_model(result.get("model"))
        self.session.save()

    async def prompt(self, text, model, effort=""):
        model = model or self.session.row.get("model", "")
        await self.ensure_thread(model)
        params = {"threadId": self.session.row["native_id"], "input": [{"type": "text", "text": text}],
                  "approvalPolicy": "untrusted", "approvalsReviewer": "user",
                  "sandboxPolicy": {"type": "workspaceWrite", "writableRoots": [str(self.session.root)], "networkAccess": False}}
        if model:
            params["model"] = model
        if effort and effort in self._efforts(model):
            # turn/start's own per-turn override; the efforts each model supports come from model/list.
            params["effort"] = effort
        response = await self.rpc.request("turn/start", params)
        if model:
            self.session.record_model(model)
        self.session.turn_id = response["turn"]["id"]
        await self.session.turn_finished.wait()

    async def event(self, method, params, request=False):
        if method in {"item/commandExecution/requestApproval", "item/fileChange/requestApproval"}:
            # Server requests must belong to this exact native thread and turn.
            # An approval may arrive immediately before the turn/start reply.
            for _ in range(200):
                if self.session.turn_id or not self.session.task:
                    break
                await asyncio.sleep(.01)
            if (params.get("threadId") != self.session.row.get("native_id")
                    or not self.session.turn_id or params.get("turnId") != self.session.turn_id):
                return {"decision": "decline"}
            # Approval is explicit and once-only. Native approval may authorize
            # execution outside its command sandbox; the UI discloses this.
            approved = await self.session.approval(method, params)
            return {"decision": "accept" if approved else "decline"}
        if method == "item/permissions/requestApproval":
            self.session.emit("permission_denied", {"reason": "Additional persistent permissions are disabled"})
            return {"permissions": {}, "scope": "turn"}
        if method == "item/tool/requestUserInput":
            # Do not invent answers or allow indefinite unattended interaction.
            return {"answers": {}}
        if method == "mcpServer/elicitation/request":
            return {"action": "decline", "content": None}
        if request or "request" in method.lower() or method.startswith("account/chatgptAuthTokens"):
            raise ValueError("Unsupported client request")
        if method == "account/login/completed" and params.get("success"):
            self.session.account = {"connected": True, "type": "chatgpt"}
        if method == "account/rateLimits/updated":
            from agent.code_limits import codex
            self._usage_attempt = time.monotonic()
            self.session.row['subscription_usage'] = codex(params)
            self.session.save()
        if method.startswith("account/"):
            self.session.emit(method, {key: params[key] for key in ("success", "authMode", "planType") if key in params})
            return {}
        if method == "turn/completed":
            turn = params.get("turn") or {}
            if params.get("threadId") != self.session.row.get("native_id"):
                return {}
            for _ in range(200):
                if self.session.turn_id or not self.session.task:
                    break
                await asyncio.sleep(.01)
            if turn.get("id") != self.session.turn_id:
                return {}
            self.session.result = str(turn.get("status", "failed"))
            self.session.turn_finished.set()
        if method == "thread/tokenUsage/updated" and params.get("threadId") == self.session.row.get("native_id"):
            from agent.code_usage import record
            total = (params.get("tokenUsage") or {}).get("total") or {}
            record(self.session, {"usage": {"input_tokens": total.get("inputTokens"), "output_tokens": total.get("outputTokens"),
                                           "total_tokens": total.get("totalTokens"), "cached_input_tokens": total.get("cachedInputTokens"),
                                           "reasoning_output_tokens": total.get("reasoningOutputTokens")}})
            if self.session.row.get("reported_usage"):
                self.session.row["reported_usage"]["scope"] = "Native thread cumulative tokens, as reported by Codex"
                self.session.save()
        self.session.emit(method, params)
        return {}

    def _efforts(self, model):
        rows = self.session.models or []
        row = next((r for r in rows if r.get("model") == model), None) or next((r for r in rows if r.get("isDefault")), {})
        return set(row.get("efforts") or [])

    async def cancel(self):
        if self.session.turn_id and self.session.row.get("native_id"):
            await self.rpc.request("turn/interrupt", {"threadId": self.session.row["native_id"], "turnId": self.session.turn_id}, timeout=5)


class Acp:
    def __init__(self, session):
        self.session, self.rpc = session, None
        self.capabilities = {}
        self.loaded = False

    async def connect(self):
        argv = self.session.row["command"]
        executable = Path(argv[0])
        if not executable.is_absolute() or not executable.is_file() or executable.suffix.lower() in {".cmd", ".bat", ".ps1"}:
            raise ValueError("Choose an absolute native ACP executable; shell wrappers are not supported")
        self.rpc = RpcProcess(argv, self.session.root, child_env(), self.event)
        result = await self.rpc.request("initialize", {"protocolVersion": 1, "clientCapabilities": {},
                                                       "clientInfo": {"name": "echospeak", "version": "1.0.0"}})
        if result.get("protocolVersion") != 1:
            raise ValueError("This agent did not negotiate supported ACP v1")
        self.capabilities = result.get("agentCapabilities") or {}
        self.session.account = {"connected": False, "type": "agent-owned", "auth_methods": result.get("authMethods", [])}

    async def login(self, method_id):
        allowed = {method["id"] for method in self.session.account.get("auth_methods", [])}
        if method_id not in allowed:
            raise ValueError("Select an authentication method advertised by this agent")
        await self.rpc.request("authenticate", {"methodId": method_id}, timeout=180)
        self.session.account["connected"] = True
        return {"authenticated": True}

    async def prompt(self, text, model, effort=""):
        result = {}  # ACP has no standard effort control: it's left to the agent
        native = self.session.row.get("native_id")
        if native and not self.loaded:
            if not self.capabilities.get("loadSession"):
                raise ValueError("This ACP agent cannot resume sessions. Start a new isolated session.")
            result = await self.rpc.request("session/load", {"sessionId": native, "cwd": str(self.session.root), "mcpServers": []})
        elif not native:
            result = await self.rpc.request("session/new", {"cwd": str(self.session.root), "mcpServers": []})
            native = self.session.row["native_id"] = result["sessionId"]
            self.session.save()
        state = result.get("models")
        if isinstance(state, dict):
            self.session.record_model(state.get("currentModelId"))
            self.session.models = [{"id": row["modelId"], "model": row["modelId"], "displayName": str(row.get("name") or row["modelId"])[:200]}
                                   for row in (state.get("availableModels") or [])[:200]
                                   if isinstance(row, dict) and isinstance(row.get("modelId"), str) and 0 < len(row["modelId"]) <= 200]
        self.loaded = True
        result = await self.rpc.request("session/prompt", {"sessionId": native,
                    "prompt": [{"type": "text", "text": text}]}, timeout=1800)
        self.session.result = "completed" if result.get("stopReason") == "end_turn" else str(result.get("stopReason", "failed"))

    async def event(self, method, params, request=False):
        if params.get("sessionId") != self.session.row.get("native_id"):
            raise ValueError("Foreign ACP session")
        if method == "session/request_permission":
            options = params.get("options") or []
            once = next((row for row in options if row.get("kind") == "allow_once"), None)
            approved = once is not None and await self.session.approval(method, params)
            return {"outcome": {"outcome": "selected", "optionId": once["optionId"]}} if approved else {"outcome": {"outcome": "cancelled"}}
        if method != "session/update":
            # No filesystem, terminal, HTTP or Echo tool capability is advertised.
            raise ValueError("Unsupported ACP client capability")
        self.session.emit(method, params)
        return {}

    async def cancel(self):
        if self.session.row.get("native_id"):
            await self.rpc.notify("session/cancel", {"sessionId": self.session.row["native_id"]})


class _TurnHandle:
    """Stands in for a long-lived RPC connection: Claude Code runs one process per turn."""
    def __init__(self, adapter):
        self.adapter, self.closed = adapter, False

    async def close(self):
        if self.closed:
            return
        self.closed = True
        await self.adapter.shutdown()


class ClaudeCli:
    """The owner's installed, unmodified Claude Code, signed in with their own plan.

    Each turn runs `claude -p` in the session's worktree and resumes Claude Code's own session.
    Claude Code keeps its own tools, permissions and transcript. Echo passes no API key, never
    reads Claude Code's credentials, and answers its permission prompts only through the
    documented PermissionRequest hook (agent/code_claude_hook.py) with the owner's decision.
    Sign-in stays in Claude Code's own `claude auth login`.
    """
    def __init__(self, session):
        self.session, self.rpc = session, None
        self.server = None
        self.process = None
        self.job = None
        self.token = ""

    async def connect(self):
        import secrets
        from agent import code_accounts
        status = await asyncio.to_thread(code_accounts.claude_status)
        if not status.get("installed"):
            raise ValueError(status.get("problem") or "Claude Code wasn't found")
        if not status.get("signed_in"):
            raise ValueError("Sign in to Claude Code first: Settings › Models › Coding agents › Claude Code › Sign in")
        plan = str(status.get("plan") or "")
        self.session.account = {"connected": True,
                                "type": f"Your Claude {plan.title()} plan" if plan else f"Claude Code · {status.get('method') or 'signed in'}"}
        self.session.models = status.get("models", [])
        # A passive auth check cannot replace an observed runtime limit event.
        self.session.row.setdefault('subscription_usage', status.get('subscription_usage', {}))
        self.token = secrets.token_urlsafe(32)
        self.server = await asyncio.start_server(self._hook, "127.0.0.1", 0)
        self.rpc = _TurnHandle(self)

    @staticmethod
    def hook_command():
        if getattr(sys, "frozen", False):
            return sys.executable, ["--code-claude-hook"]
        return sys.executable, ["-P", "-m", "agent.code_claude_hook"]

    def argv(self, model, effort=""):
        from agent.code_executables import executable
        command, args = self.hook_command()
        settings = {"hooks": {"PermissionRequest": [{"matcher": "*", "hooks": [
            {"type": "command", "command": command, "args": args, "timeout": 200}]}]}}
        argv = [executable("claude_cli"), "-p", "--output-format", "stream-json", "--verbose", "--include-partial-messages",
                # Edits stay inside the isolated worktree (inspect and roll back in Echo Code);
                # commands, network and anything else Claude Code would ask about go to the owner.
                "--permission-mode", "acceptEdits",
                # The owner's own Claude Code settings only: no hooks or MCP servers from the repository.
                "--setting-sources", "user", "--strict-mcp-config",
                "--settings", json.dumps(settings)]
        if model:
            argv += ["--model", model]
        if effort in CLAUDE_EFFORTS:
            argv += ["--effort", effort]  # for this session only; the owner's saved setting is untouched
        if self.session.row.get("native_id"):
            argv += ["--resume", self.session.row["native_id"]]
        return argv

    async def _hook(self, reader, writer):
        import hmac
        allow = False
        try:
            raw = await asyncio.wait_for(reader.readline(), 10)
            request = json.loads(raw or b"{}")
            if (hmac.compare_digest(str(request.get("token", "")), self.token) and self.session.task
                    and isinstance(request.get("tool_input"), dict)):
                tool = str(request.get("tool_name") or "tool")[:100]
                allow = await self.session.approval("claude/" + tool, {"tool": tool, "input": request["tool_input"]})
        except Exception:
            allow = False
        try:
            writer.write((json.dumps({"allow": bool(allow)}) + "\n").encode())
            await writer.drain()
        finally:
            writer.close()

    async def prompt(self, text, model, effort=""):
        import subprocess
        from agent.code_process import _windows_job, workspace_env
        port = self.server.sockets[0].getsockname()[1]
        env = child_env({"ECHO_CODE_HOOK_PORT": str(port)})
        # Set after child_env, which keeps any *TOKEN* variable it is handed out of children.
        env["ECHO_CODE_HOOK_TOKEN"] = self.token
        if not getattr(sys, "frozen", False):
            env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
        flags = (0x4 | subprocess.CREATE_NO_WINDOW) if os.name == "nt" else 0
        model = model or self.session.row.get("model", "")
        process = self.process = subprocess.Popen(self.argv(model, effort), cwd=self.session.root, env=workspace_env(env), stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=flags,
                                        start_new_session=os.name != "nt")
        if os.name == "nt":
            self.job = _windows_job(process)
        result = None
        try:
            await asyncio.to_thread(self._send_prompt, process, text)
            while True:
                raw = await asyncio.to_thread(process.stdout.readline, 4 * 1024 * 1024)
                if not raw:
                    break
                try:
                    message = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(message, dict):
                    result = self._message(message) or result
            await asyncio.to_thread(process.wait)
        finally:
            self._release()
        self.session.result = "completed" if result and not result["is_error"] else "failed"
        self.session.turn_finished.set()

    @staticmethod
    def _send_prompt(process, text):
        # Prompt on stdin, not argv: no length or quoting limits, nothing in the process list.
        process.stdin.write(text.encode("utf-8"))
        process.stdin.close()

    def _message(self, message):
        kind, subtype = message.get("type"), message.get("subtype")
        if kind == "system" and subtype == "init":
            self._native(message.get("session_id"))
            self.session.record_model(message.get("model"))
        elif kind == "stream_event":
            delta = (message.get("event") or {}).get("delta") or {}
            if delta.get("type") == "text_delta" and not message.get("parent_tool_use_id"):
                self.session.emit("claude/text", {"text": str(delta.get("text", ""))})
        elif kind == "assistant":
            for block in (message.get("message") or {}).get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    inputs = block.get("input") or {}
                    detail = inputs.get("command") or inputs.get("file_path") or inputs.get("pattern") or inputs.get("url") or ""
                    self.session.emit("claude/tool", {"tool": str(block.get("name", ""))[:100], "detail": str(detail)[:300]})
        elif kind == "system" and subtype == "permission_denied":
            self.session.emit("permission_denied", {"reason": f"Not approved: {message.get('tool_name') or 'a tool call'}"})
        elif kind == "rate_limit_event":
            from agent.code_limits import claude
            self.session.row['subscription_usage'] = claude(message.get('rate_limit_info'))
            self.session.save()
        elif kind == "result":
            self._native(message.get("session_id"))
            from agent.code_usage import record
            record(self.session, message)
            if message.get('is_error'):
                errors=message.get('errors') or [message.get('result') or 'Claude Code ended without completing the turn. Check its sign-in, model and limits.']
                if not isinstance(errors, list):
                    errors = [errors]
                self.session.emit('session/error', {'message':' '.join(str(v) for v in errors)[:1000]})
            self.session.emit("claude/result", {"is_error": bool(message.get("is_error")), "subtype": str(subtype or "")[:60]})
            return {"is_error": bool(message.get("is_error"))}
        return None

    def _native(self, session_id):
        if session_id and session_id != self.session.row.get("native_id"):
            self.session.row["native_id"] = str(session_id)[:200]
            self.session.save()

    def _release(self):
        if self.job:
            self.job()  # closes the kill-on-close job: Claude Code and everything it started
            self.job = None
        elif self.process and self.process.poll() is None:
            self.process.kill()
        self.process = None

    async def cancel(self):
        self._release()

    async def shutdown(self):
        self._release()
        if self.server:
            self.server.close()
            self.server = None
