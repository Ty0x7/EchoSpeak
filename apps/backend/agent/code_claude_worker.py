"""Echo's private stdio worker around the public Claude Agent SDK API.

Uses an explicitly supplied API key, never a copied claude.ai OAuth token.
The containing process job owns both this worker and the SDK's CLI descendants.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict, is_dataclass
import json
import os
from pathlib import Path
import sys

MAX_FRAME = 1024 * 1024


class Worker:
    def __init__(self):
        self.pending = {}
        self.sequence = 0
        self.running = None

    def send(self, value):
        raw = json.dumps(value, ensure_ascii=False, default=str)
        if len(raw.encode()) > MAX_FRAME:
            raise ValueError("SDK message exceeded its frame limit")
        sys.stdout.write(raw + "\n")
        sys.stdout.flush()

    async def approval(self, tool, inputs):
        self.sequence += 1
        request_id = "approval-" + str(self.sequence)
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        self.send({"id": request_id, "method": "permission", "params": {"tool": tool, "input": inputs}})
        try:
            return bool((await asyncio.wait_for(future, 180)).get("allow"))
        except asyncio.TimeoutError:
            return False
        finally:
            self.pending.pop(request_id, None)

    async def prompt(self, params):
        from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, HookMatcher
        from claude_agent_sdk.types import PermissionResultDeny

        async def gate(data, tool_use_id, context):
            approved = await self.approval(data.get("tool_name"), data.get("tool_input") or {})
            return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                     "permissionDecision": "allow" if approved else "deny",
                     "permissionDecisionReason": "Echo Code owner's once-only decision"}}

        async def fallback(tool, inputs, context):
            # A missing/unhandled hook must never silently authorize a tool.
            return PermissionResultDeny(message="Tool was not approved by Echo Code", interrupt=False)

        options = ClaudeAgentOptions(cwd=str(Path.cwd()), resume=params.get("resume"), model=params.get("model"),
            tools={"type": "preset", "preset": "claude_code"}, system_prompt={"type": "preset", "preset": "claude_code"},
            permission_mode="default", can_use_tool=fallback, setting_sources=[],
            strict_mcp_config=True, mcp_servers={}, include_partial_messages=True,
            hooks={"PreToolUse": [HookMatcher(hooks=[gate], timeout=190)]},
            max_buffer_size=MAX_FRAME, env={"ANTHROPIC_API_KEY": os.environ["ANTHROPIC_API_KEY"]})
        result = {"is_error": True}
        async with ClaudeSDKClient(options=options) as client:
            await client.query(params["text"])
            async for message in client.receive_response():
                data = asdict(message) if is_dataclass(message) else {"value": str(message)}
                kind = type(message).__name__
                # Stream tool/assistant events; session result carries the native id.
                self.send({"method": "sdk/event", "params": {"kind": kind, "data": data}})
                if kind == "ResultMessage":
                    result = {"is_error": data.get("is_error", True), "session_id": data.get("session_id")}
        return result

    async def request(self, message):
        try:
            if message["method"] == "initialize":
                import claude_agent_sdk  # noqa: F401 - optional, explicit installation
                if not os.environ.get("ANTHROPIC_API_KEY"):
                    raise ValueError("API key required")
                result = {"ready": True}
            elif message["method"] == "prompt":
                if self.running:
                    raise ValueError("A turn is already running")
                self.running = asyncio.current_task()
                try:
                    result = await self.prompt(message["params"])
                finally:
                    self.running = None
            else:
                raise ValueError("Unsupported worker method")
            self.send({"id": message["id"], "result": result})
        except Exception:
            self.send({"id": message["id"], "error": {"code": -32000,
                       "message": "Claude SDK failed; verify SDK installation and API authentication"}})

    async def run(self):
        tasks = set()
        while raw := await asyncio.to_thread(sys.stdin.buffer.readline, MAX_FRAME + 1):
            if len(raw) > MAX_FRAME:
                break
            message = json.loads(raw)
            if "method" in message:
                if len(tasks) >= 8:
                    break
                task = asyncio.create_task(self.request(message))
                tasks.add(task)
                task.add_done_callback(tasks.discard)
            else:
                future = self.pending.get(message.get("id"))
                if future and not future.done():
                    future.set_result(message.get("result") or {})
        for task in tasks:
            task.cancel()


def main():
    asyncio.run(Worker().run())


if __name__ == "__main__":
    main()
