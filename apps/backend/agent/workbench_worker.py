"""Owned stdio worker for a real ConPTY shell and bounded native command jobs."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys

MAX_FRAME = 1024 * 1024


class Worker:
    def __init__(self):
        self.pty = None
        self.jobs = set()

    def send(self, data):
        raw = json.dumps(data, ensure_ascii=True)
        if len(raw.encode()) > MAX_FRAME:
            raise ValueError("Workbench frame limit")
        print(raw, flush=True)

    async def pump(self):
        try:
            while self.pty and self.pty.isalive():
                chunk = await asyncio.to_thread(self.pty.read, 8192)
                self.send({"method": "terminal/output", "params": {"text": chunk}})
        except (EOFError, OSError):
            pass
        self.send({"method": "terminal/exited", "params": {}})

    async def execute(self, argv):
        process = await asyncio.create_subprocess_exec(*argv, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        async def read(pipe):
            chunks, size = [], 0
            while chunk := await pipe.read(8192):
                size += len(chunk)
                if size > 350_000:
                    raise ValueError("Command output limit")
                chunks.append(chunk)
            return b"".join(chunks).decode("utf-8", "replace")
        try:
            stdout, stderr = await asyncio.wait_for(asyncio.gather(read(process.stdout), read(process.stderr)), 40)
            code = await asyncio.wait_for(process.wait(), 5)
            return {"code": code, "stdout": stdout, "stderr": stderr}
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()

    async def request(self, message):
        try:
            params, method = message.get("params") or {}, message["method"]
            if method == "terminal/start":
                if self.pty or os.name != "nt":
                    raise ValueError("ConPTY requires Windows and a fresh worker")
                from winpty import PtyProcess
                shell = shutil.which("pwsh.exe") or shutil.which("powershell.exe")
                if not shell:
                    raise ValueError("PowerShell was not found")
                self.pty = await asyncio.to_thread(PtyProcess.spawn, [shell, "-NoLogo", "-NoProfile"],
                    cwd=os.getcwd(), env=dict(os.environ), dimensions=(params["rows"], params["cols"]), backend=1)
                asyncio.create_task(self.pump())
                result = {"pid": self.pty.pid, "shell": "PowerShell", "backend": "ConPTY"}
            elif method == "terminal/write":
                if not self.pty or not self.pty.isalive():
                    raise ValueError("Terminal is closed")
                await asyncio.to_thread(self.pty.write, params["text"])
                result = {}
            elif method == "terminal/resize":
                if self.pty:
                    await asyncio.to_thread(self.pty.setwinsize, params["rows"], params["cols"])
                result = {}
            elif method == "exec":
                result = await self.execute(params["argv"])
            else:
                raise ValueError("Unknown workbench request")
            self.send({"id": message["id"], "result": result})
        except Exception:
            self.send({"id": message["id"], "error": {"code": -32000, "message": "Workbench worker failed"}})

    async def run(self):
        try:
            while raw := await asyncio.to_thread(sys.stdin.buffer.readline, MAX_FRAME + 1):
                if len(raw) > MAX_FRAME or len(self.jobs) >= 16:
                    break
                task = asyncio.create_task(self.request(json.loads(raw)))
                self.jobs.add(task)
                task.add_done_callback(self.jobs.discard)
        finally:
            for task in self.jobs:
                task.cancel()
            if self.pty:
                await asyncio.to_thread(self.pty.close, True)


def main():
    asyncio.run(Worker().run())


if __name__ == "__main__":
    main()
