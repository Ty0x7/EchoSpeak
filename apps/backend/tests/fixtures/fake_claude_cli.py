"""Stands in for `claude -p --output-format stream-json`: prints Claude Code's documented stream
events, and for a prompt mentioning "needs-approval" runs the PermissionRequest hook named in
--settings exactly as Claude Code would (exec form, event JSON on stdin)."""
import json
import subprocess
import sys

args = sys.argv[1:]
prompt = sys.stdin.read()
settings = json.loads(args[args.index("--settings") + 1])
session = args[args.index("--resume") + 1] if "--resume" in args else "fake-session-1"
model = args[args.index("--model") + 1] if "--model" in args else "claude-fake"


def emit(value):
    print(json.dumps(value), flush=True)


emit({"type": "system", "subtype": "init", "session_id": session, "model": model})
if "needs-approval" in prompt:
    hook = settings["hooks"]["PermissionRequest"][0]["hooks"][0]
    event = {"hook_event_name": "PermissionRequest", "tool_name": "Bash", "tool_input": {"command": "npm install left-pad"}}
    done = subprocess.run([hook["command"], *hook["args"]], input=json.dumps(event), capture_output=True, text=True, timeout=60)
    decision = json.loads(done.stdout)["hookSpecificOutput"]["decision"]["behavior"]
    emit({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {"command": "npm install left-pad"}}]}})
    if decision != "allow":
        emit({"type": "system", "subtype": "permission_denied", "tool_name": "Bash"})
    text = "ran it" if decision == "allow" else "not allowed"
else:
    text = "hello " + "|".join(a for a in args if a in {"acceptEdits", "user"})
    text += " api-key-present" if __import__("os").environ.get("ANTHROPIC_API_KEY") else " own-sign-in"
    if "write-folder-fixture" in prompt:
        from pathlib import Path
        Path("folder-result.txt").write_text("fixture edit", encoding="utf-8")
emit({"type": "stream_event", "event": {"delta": {"type": "text_delta", "text": text}}})
emit({"type": "result", "subtype": "success", "is_error": False, "session_id": session})
