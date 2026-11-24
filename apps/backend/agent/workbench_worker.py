"""Owned stdio worker for interactive shells and bounded native command jobs."""
from __future__ import annotations

import asyncio
import json
import os
import codecs
import signal
import shutil
import subprocess
import sys

MAX_FRAME = 1024 * 1024


SHELLS = {
    "powershell": ("PowerShell", ("pwsh", "powershell"), ("-NoLogo", "-NoProfile")),
    "bash": ("Bash", ("bash",), ("--noprofile", "--norc", "-i")),
    "zsh": ("Zsh", ("zsh",), ("-f", "-i")),
    "fish": ("Fish", ("fish",), ("--no-config", "-i")),
}


def installed_shells():
    order = ("powershell", "bash", "zsh", "fish") if os.name == "nt" else ("bash", "zsh", "fish", "powershell")
    suffix = ".exe" if os.name == "nt" else ""
    result = {}
    for key in order:
        label, names, flags = SHELLS[key]
        for name in names:
            path = shutil.which(name + suffix)
            if path and os.path.isabs(path):
                result[key] = (label, [path, *flags])
                break
    return result


def exec_posix_shell(argv):
    import fcntl
    import termios

    fcntl.ioctl(0, termios.TIOCSCTTY, 0)
    os.execv(argv[0], argv)


class PosixTerminal:
    def __init__(self, argv, rows, cols):
        import pty

        self.master, slave = pty.openpty()
        self.decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        entry = ["--workbench-shell"] if getattr(sys, "frozen", False) else ["-P", "-m", "agent.workbench_worker", "--workbench-shell"]
        env = dict(os.environ, TERM="xterm-256color")
        # These environment variables can load startup files even with no-profile flags.
        for key in ("BASH_ENV", "ENV"):
            env.pop(key, None)
        try:
            self._resize(slave, rows, cols)
            self.process = subprocess.Popen([sys.executable, *entry, *argv], stdin=slave, stdout=slave, stderr=slave,
                                            env=env, start_new_session=True)
        except BaseException:
            os.close(self.master)
            raise
        finally:
            os.close(slave)
        self.pid = self.process.pid

    @staticmethod
    def _resize(fd, rows, cols):
        import fcntl
        import struct
        import termios

        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    def read(self, size):
        try:
            data = os.read(self.master, size)
        except OSError as exc:
            import errno

            if exc.errno != errno.EIO:  # Linux signals a closed slave with EIO.
                raise
            data = b""
        if not data:
            tail = self.decoder.decode(b"", final=True)
            if tail:
                return tail
            raise EOFError
        return self.decoder.decode(data)

    def isalive(self):
        return self.process.poll() is None

    def write(self, text):
        data = memoryview(text.encode("utf-8"))
        while data:
            data = data[os.write(self.master, data):]

    def setwinsize(self, rows, cols):
        self._resize(self.master, rows, cols)

    def close(self, force=False):
        os.close(self.master)  # Hangs up the shell's controlling terminal.
        if self.isalive():
            self.process.send_signal(signal.SIGHUP)
        try:
            self.process.wait(timeout=.4)
        except subprocess.TimeoutExpired:
            if force:
                self.process.kill()
                self.process.wait(timeout=1)



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
            while self.pty:
                chunk = await asyncio.to_thread(self.pty.read, 8192)
                if chunk:
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
                if self.pty:
                    raise ValueError("Terminal already started")
                choices = installed_shells()
                label, argv = choices[params["shell"]]
                rows, cols = params["rows"], params["cols"]
                if os.name == "nt":
                    from winpty import PtyProcess

                    self.pty = await asyncio.to_thread(PtyProcess.spawn, argv, cwd=os.getcwd(),
                        env=dict(os.environ), dimensions=(rows, cols), backend=1)
                    backend = "ConPTY"
                else:
                    self.pty = PosixTerminal(argv, rows, cols)
                    backend = "PTY"
                asyncio.create_task(self.pump())
                result = {"pid": self.pty.pid, "shell": label, "backend": backend}
            elif method == "terminal/stop":
                terminal, self.pty = self.pty, None
                if terminal:
                    await asyncio.to_thread(terminal.close, True)
                result = {}
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
    if sys.argv[1:2] == ["--workbench-shell"]:
        exec_posix_shell(sys.argv[2:])
    else:
        main()
