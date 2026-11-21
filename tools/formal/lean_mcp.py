"""Opt-in local MCP service. The compiler runs in a fresh Linux sandbox per request."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, TextContent
from pydantic import Field

PIN = "4.36.0"
DRIVER = Path(__file__).with_name("ProofCheck.lean")
MARKER = f"EchoSpeak kernel accepted; Lean {PIN}; axioms: "
MAX_INPUT = 8192
MAX_OUTPUT = 131072
BUSY = asyncio.Lock()
LOG = logging.getLogger("lean_proof")
SERVER = MCPServer("lean_proof")


def command(toolchain: Path) -> list[str]:
    if sys.platform != "linux":
        raise OSError("Bubblewrap proof execution requires Linux")
    if not toolchain.is_absolute() or not (toolchain / "bin/lean").is_file():
        raise OSError("The configured pinned Lean 4 toolchain is missing")
    return [
        "/usr/bin/prlimit", "--as=4294967296", "--cpu=5:6", f"--fsize={MAX_OUTPUT}",
        "--nofile=64", "--core=0", "--",
        "/usr/bin/bwrap", "--unshare-all", "--unshare-user", "--disable-userns", "--new-session",
        "--die-with-parent", "--cap-drop", "ALL", "--clearenv",
        "--ro-bind", str(toolchain), "/lean", "--ro-bind", "/usr/lib", "/usr/lib",
        "--symlink", "usr/lib", "/lib", "--symlink", "usr/lib", "/lib64",
        "--dev", "/dev", "--proc", "/proc", "--size", "16777216", "--tmpfs", "/tmp",
        "--chdir", "/tmp", "--setenv", "HOME", "/tmp", "--setenv", "TMPDIR", "/tmp",
        "--setenv", "LD_LIBRARY_PATH", "/lean/lib/lean:/lean/lib",
        "--remount-ro", "/", "--", "/lean/bin/lean", "--stdin", "--json", "-j", "1",
        "-t", "0", "-s", "32768",
    ]


def proof_source(statement: str, proof: str) -> bytes:
    terms = (statement, proof)
    if any(len(term) > MAX_INPUT for term in terms):
        raise ValueError(f"Input exceeds {MAX_INPUT} characters")
    if any(not term.strip() or any(ord(ch) < 32 and ch not in "\t\r\n" for ch in term)
           for term in terms):
        raise ValueError("Provide two nonempty Lean terms without control characters")
    payload = " ".join(json.dumps(term, ensure_ascii=False) for term in terms).encode()
    if len(payload) > MAX_INPUT:
        raise ValueError(f"Encoded input exceeds {MAX_INPUT} bytes")
    if any(re.search(r"\b(sorry|admit|native_decide|run_tac|run_elab|set_option)\b", term)
           for term in terms):
        raise ValueError("Proof holes, native evaluation and elaborator commands are unsupported")
    return DRIVER.read_bytes() + b"\ncheckProof " + payload + b"\n"


async def compile_proof(toolchain: Path, source: bytes) -> dict:
    with tempfile.TemporaryFile() as output:
        proc = await asyncio.create_subprocess_exec(
            *command(toolchain), env={}, stdin=asyncio.subprocess.PIPE, stdout=output, stderr=output,
        )
        try:
            await asyncio.wait_for(proc.communicate(source), timeout=9)
        finally:
            if proc.returncode is None:
                proc.kill()
                await asyncio.shield(proc.wait())
        output.seek(0)
        text = output.read(MAX_OUTPUT).decode(errors="replace")
    diagnostics = []
    axioms = None
    for line in text.splitlines():
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if not isinstance(message, dict) or not isinstance(message.get("data"), str):
            continue
        detail = message["data"]
        if message.get("severity") == "information" and detail.startswith(MARKER):
            axioms = detail[len(MARKER):]
        elif len(diagnostics) < 12:
            diagnostics.append({"severity": message.get("severity", "error"), "message": detail[:1600]})
    verified = proc.returncode == 0 and axioms is not None and not diagnostics
    if verified:
        return {"verified": True, "status": "accepted", "lean": PIN, "axioms": axioms, "diagnostics": []}
    if diagnostics and proc.returncode in {0, 1}:
        return {"verified": False, "status": "rejected", "diagnostics": diagnostics}
    return {"verified": False, "status": "unavailable",
            "diagnostics": [{"message": "Pinned checker failed, was restricted, or exceeded a resource limit."}]}


@SERVER.tool()
async def check_proof(statement: Annotated[str, Field(min_length=1, max_length=MAX_INPUT)],
                      proof: Annotated[str, Field(min_length=1, max_length=MAX_INPUT)]) -> CallToolResult:
    """Check one Lean 4 proposition and proof term, with no imports or files. Requires approval.

    Example: statement "∀ n : Nat, n = n", proof "by intro n; rfl".
    """
    started = time.monotonic()
    digest = ""
    try:
        source = proof_source(statement, proof)
        digest = hashlib.sha256(source).hexdigest()
        if BUSY.locked():
            result = {"verified": False, "status": "busy", "diagnostics": []}
        else:
            async with BUSY:
                result = await compile_proof(Path(os.environ.get("ECHOSPEAK_LEAN4_ROOT", "")), source)
    except (ValueError, UnicodeError) as exc:
        result = {"verified": False, "status": "invalid_input", "diagnostics": [{"message": str(exc)[:200]}]}
    except (OSError, TimeoutError):
        result = {"verified": False, "status": "unavailable",
                  "diagnostics": [{"message": "Pinned local sandbox or checker unavailable, or deadline exceeded."}]}
    except asyncio.CancelledError:
        LOG.info("proof=%s status=cancelled", digest)
        raise
    LOG.info("proof=%s status=%s duration_ms=%d", digest, result["status"], (time.monotonic() - started) * 1000)
    return CallToolResult(
        is_error=not result["verified"], structured_content=result,
        content=[TextContent(type="text", text=json.dumps(result, ensure_ascii=False))],
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    SERVER.run(transport="stdio")
