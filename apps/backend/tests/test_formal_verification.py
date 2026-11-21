import asyncio
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from types import SimpleNamespace

import pytest

from agent.lean.toolbox import Toolbox
from agent.mcp_client import MCPManager
from agent.tool_registry import ToolRegistry

FORMAL = Path(__file__).resolve().parents[3] / "tools" / "formal"


def test_project_scoped_mcp_tool_rechecks_existing_toolbox_after_project_change(monkeypatch):
    from agent import state

    current = SimpleNamespace(active_project_id="proof-project")
    monkeypatch.setattr(state, "get_state_store", lambda: SimpleNamespace(get_thread_state=lambda _: current))
    name = "mcp__proof_fixture__check"
    calls = []
    monkeypatch.setattr(ToolRegistry, "_entries", {})
    manager = MCPManager()
    session = SimpleNamespace(
        state=SimpleNamespace(project_ids=["proof-project"], session_ids=["proof-chat"], allow_global=False,
                              connection_id="mcp-proof", accept_server_read_only_hints=False,
                              capability_policies={}),
        call_tool=lambda *args: calls.append(args) or '{"verified": true}',
    )
    monkeypatch.setattr(manager, "_capability_risk", lambda *_: ("moderate", True))
    manager._register_tool("proof_fixture", {"name": "check"}, session)
    inside = Toolbox(toolsets=[name], session_id="proof-chat")
    outside = Toolbox(toolsets=[name], session_id="other-chat")
    assert name in inside.names
    assert name not in outside.names
    assert ToolRegistry.get(name).approval_required
    idle = ToolRegistry.get(name).func.invoke({})
    assert json.loads(idle)["isError"] and not calls
    current.active_project_id = "another-project"
    assert not inside._run_entry(name, {}).ok
    assert not calls
    current.active_project_id = "proof-project"
    assert inside._run_entry(name, {}).ok and len(calls) == 1


@pytest.mark.skipif(not os.getenv("ECHOSPEAK_LEAN4_ROOT"), reason="optional pinned local Lean 4 checker")
def test_sandboxed_valid_invalid_and_unavailable_proofs(monkeypatch):
    monkeypatch.syspath_prepend(str(FORMAL))
    from lean_mcp import check_proof

    async def exercise():
        proof = await check_proof("∀ n : Nat, n = n", "by intro n; rfl")
        assert proof.structured_content == {
            "verified": True, "status": "accepted", "lean": "4.36.0", "axioms": "[]", "diagnostics": [],
        }
        for term in ("by rfl", "by skip", "by sorry"):
            result = await check_proof("(1 : Nat) = 2", term)
            assert result.is_error and result.structured_content["verified"] is False
        malformed = await check_proof("True\naxiom fabricated : False", "by trivial")
        assert malformed.is_error
        monkeypatch.delenv("ECHOSPEAK_LEAN4_ROOT")
        unavailable = await check_proof("True", "by trivial")
        assert unavailable.is_error and unavailable.structured_content["status"] == "unavailable"

    asyncio.run(exercise())


@pytest.mark.skipif(not os.getenv("ECHOSPEAK_LEAN4_ROOT"), reason="optional pinned local Lean 4 checker")
def test_proof_sandbox_omits_host_environment_files_and_loopback(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(FORMAL))
    from lean_mcp import command

    secret = tmp_path / "synthetic-host-marker"
    secret.write_text("fixture")
    argv = command(Path(os.environ["ECHOSPEAK_LEAN4_ROOT"]))
    readonly = argv.index("--remount-ro")
    probe = """
import os, socket, sys
assert os.getenv("FIXTURE_ONLY_MARKER") is None
assert not os.path.exists(sys.argv[1])
assert os.environ["HOME"] == "/tmp"
try:
    socket.create_connection(("127.0.0.1", int(sys.argv[2])), timeout=1)
except OSError:
    pass
else:
    raise AssertionError("Host listener was reachable")
"""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        subprocess.run([*argv[:readonly], "--ro-bind", "/usr/bin/python3", "/probe",
                        "--remount-ro", "/", "--",
                        "/probe", "-I", "-c", probe, str(secret), str(listener.getsockname()[1])],
                       check=True, timeout=5, env={"FIXTURE_ONLY_MARKER": "test"})


def test_cancelling_a_proof_wait_reaps_the_worker(monkeypatch):
    monkeypatch.syspath_prepend(str(FORMAL))
    import lean_mcp

    monkeypatch.setattr(lean_mcp, "command", lambda _: [sys.executable, "-c", "import time; time.sleep(30)"])

    async def exercise():
        spawned = asyncio.get_running_loop().create_future()
        original = asyncio.create_subprocess_exec

        async def capture(*args, **kwargs):
            process = await original(*args, **kwargs)
            spawned.set_result(process)
            return process

        monkeypatch.setattr(asyncio, "create_subprocess_exec", capture)
        task = asyncio.create_task(lean_mcp.compile_proof(Path("/unused"), b""))
        process = await asyncio.wait_for(spawned, 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert process.returncode is not None

    asyncio.run(exercise())
