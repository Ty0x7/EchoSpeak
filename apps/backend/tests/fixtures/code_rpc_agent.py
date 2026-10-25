"""Local protocol fixture. No provider, credentials, network, or owner repository."""
import json
from pathlib import Path
import subprocess
import sys
import time


def send(value):
    print(json.dumps(value), flush=True)


mode = sys.argv[1]
for raw in sys.stdin:
    request = json.loads(raw)
    method, params = request.get("method"), request.get("params") or {}
    if "id" not in request:
        continue
    result = {}
    if method == "initialize":
        result = {"protocolVersion": 1, "agentCapabilities": {"loadSession": True}, "authMethods": []}
    elif method == "account/read":
        result = {"account": {"type": "chatgpt"}}
    elif method == "model/list":
        result = {"data": [{"id": "fixture-model", "model": "fixture-model"}]}
    elif method in {"thread/start", "thread/resume"}:
        assert params["approvalPolicy"] == "untrusted" and params["sandbox"] == "workspace-write"
        result = {"thread": {"id": "fixture-thread"}}
    elif method == "account/login/start":
        result = {"authUrl": "https://auth.openai.com/fixture", "loginId": "fixture-login"}
    elif method in {"session/new", "session/load"}:
        assert params["mcpServers"] == []
        result = {"sessionId": "fixture-acp"}
    elif method in {"turn/start", "session/prompt"}:
        if method == "turn/start":
            assert params["sandboxPolicy"]["networkAccess"] is False
            send({"id": request["id"], "result": {"turn": {"id": "fixture-turn"}}})
        if mode == "crash":
            sys.exit(2)
        if mode == "child":
            child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
            Path("child.pid").write_text(str(child.pid))
            time.sleep(120)
        if mode == "codex":
            send({"id": "approval-1", "method": "item/commandExecution/requestApproval", "params": {"threadId": "fixture-thread", "turnId": "fixture-turn", "command": "fixture-edit", "cwd": str(Path.cwd())}})
        else:
            send({"jsonrpc": "2.0", "id": "approval-1", "method": "session/request_permission", "params": {
                "sessionId": "fixture-acp", "toolCall": {"toolCallId": "tool-1", "title": "Fixture edit"},
                "options": [{"optionId": "once-exact", "kind": "allow_once", "name": "Allow"}]}})
        reply = json.loads(next(sys.stdin))
        approved = (reply.get("result") or {}).get("decision") == "accept" if mode == "codex" else (reply.get("result") or {}).get("outcome", {}).get("optionId") == "once-exact"
        if approved:
            Path("hello.txt").write_text("agent edit", encoding="utf-8")
        if mode == "codex":
            send({"method": "item/agentMessage/delta", "params": {"delta": "Fixture streamed reply"}})
            send({"method": "turn/completed", "params": {"threadId": "fixture-thread", "turn": {"id": "fixture-turn", "status": "completed"}}})
            continue
        send({"method": "session/update", "params": {"sessionId": "fixture-acp", "update": {
            "sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "Fixture ACP reply"}}}})
        result = {"stopReason": "end_turn"}
    send({"jsonrpc": "2.0", "id": request["id"], "result": result})
