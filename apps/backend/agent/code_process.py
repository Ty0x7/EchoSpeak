"""Bounded stdio JSON-RPC and process-tree ownership for external coding harnesses.

Ownership is cleanup, not a filesystem/network sandbox. On Windows the child is
created suspended, assigned to a kill-on-close job, then its primary thread is
resumed. No executable runs before ownership has been established.
"""
from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess

MAX_FRAME = 1024 * 1024
GIT_BINDING_VARS = {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR", "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES"}


def workspace_env(env):
    return {key: value for key, value in env.items() if key.upper() not in GIT_BINDING_VARS}


def _windows_job(process):
    import ctypes as c
    from ctypes import wintypes as w

    class Basic(c.Structure):
        _fields_ = [("process_time", c.c_int64), ("job_time", c.c_int64), ("flags", w.DWORD),
                    ("minimum", c.c_size_t), ("maximum", c.c_size_t), ("processes", w.DWORD),
                    ("affinity", c.c_size_t), ("priority", w.DWORD), ("scheduling", w.DWORD)]

    class IO(c.Structure):
        _fields_ = [(name, c.c_uint64) for name in ("r", "w", "o", "rb", "wb", "ob")]

    class Extended(c.Structure):
        _fields_ = [("basic", Basic), ("io", IO), ("pm", c.c_size_t), ("jm", c.c_size_t),
                    ("ppm", c.c_size_t), ("pjm", c.c_size_t)]

    class Thread(c.Structure):
        _fields_ = [("size", w.DWORD), ("usage", w.DWORD), ("tid", w.DWORD), ("pid", w.DWORD),
                    ("priority", w.LONG), ("delta", w.LONG), ("flags", w.DWORD)]

    k = c.WinDLL("kernel32", use_last_error=True)
    signatures = {
        "CreateJobObjectW": ([c.c_void_p, w.LPCWSTR], w.HANDLE),
        "SetInformationJobObject": ([w.HANDLE, c.c_int, c.c_void_p, w.DWORD], w.BOOL),
        "AssignProcessToJobObject": ([w.HANDLE, w.HANDLE], w.BOOL),
        "CloseHandle": ([w.HANDLE], w.BOOL),
        "CreateToolhelp32Snapshot": ([w.DWORD, w.DWORD], w.HANDLE),
        "Thread32First": ([w.HANDLE, c.POINTER(Thread)], w.BOOL),
        "Thread32Next": ([w.HANDLE, c.POINTER(Thread)], w.BOOL),
        "OpenThread": ([w.DWORD, w.BOOL, w.DWORD], w.HANDLE),
        "ResumeThread": ([w.HANDLE], w.DWORD),
    }
    for name, (args, result) in signatures.items():
        getattr(k, name).argtypes, getattr(k, name).restype = args, result
    job = k.CreateJobObjectW(None, None)
    if not job:
        raise OSError("Cannot own the coding runtime process tree")
    try:
        limits = Extended()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not k.SetInformationJobObject(job, 9, c.byref(limits), c.sizeof(limits)):
            raise OSError("Cannot set coding runtime cleanup limits")
        if not k.AssignProcessToJobObject(job, int(process._handle)):
            raise OSError("Cannot assign coding runtime cleanup job")
        snapshot = k.CreateToolhelp32Snapshot(4, 0)
        if snapshot == c.c_void_p(-1).value:
            raise OSError("Cannot inspect suspended coding runtime")
        resumed = False
        try:
            entry = Thread()
            entry.size = c.sizeof(entry)
            valid = k.Thread32First(snapshot, c.byref(entry))
            while valid:
                if entry.pid == process.pid:
                    thread = k.OpenThread(2, False, entry.tid)  # THREAD_SUSPEND_RESUME
                    if thread:
                        try:
                            resumed = k.ResumeThread(thread) != 0xFFFFFFFF
                        finally:
                            k.CloseHandle(thread)
                    break
                valid = k.Thread32Next(snapshot, c.byref(entry))
        finally:
            k.CloseHandle(snapshot)
        if not resumed:
            raise OSError("Cannot resume owned coding runtime")
    except BaseException:
        k.CloseHandle(job)
        raise
    return lambda: k.CloseHandle(job)


class RpcProcess:
    def __init__(self, argv, cwd, env, handler):
        self.handler = handler
        self.pending = {}
        self.sequence = 0
        self.closed = False
        self.tasks = set()
        self.write_lock = asyncio.Lock()
        flags = (0x4 | subprocess.CREATE_NO_WINDOW) if os.name == "nt" else 0
        self.process = subprocess.Popen(argv, cwd=cwd, env=workspace_env(env), stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        creationflags=flags, start_new_session=os.name != "nt")
        self.job = None
        try:
            if os.name == "nt":
                self.job = _windows_job(self.process)
        except BaseException:
            self.process.kill()
            self.process.wait(timeout=10)
            raise
        self.reader = asyncio.create_task(self._read())
        self.stderr = asyncio.create_task(self._drain_stderr())

    async def send(self, data):
        if self.closed:
            raise RuntimeError("Coding runtime disconnected")
        raw = (json.dumps(data, ensure_ascii=False) + "\n").encode()
        if len(raw) > MAX_FRAME:
            raise ValueError("Runtime frame is too large")
        # The write itself runs on the owner loop; payloads are bounded and there
        # is only one writer. A blocked pipe is bounded by the supervisor timeout.
        async with self.write_lock:
            await asyncio.wait_for(asyncio.to_thread(self._write, raw), 10)

    def _write(self, raw):
        self.process.stdin.write(raw)
        self.process.stdin.flush()

    async def request(self, method, params, timeout=30):
        self.sequence += 1
        request_id = self.sequence
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        try:
            await self.send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
            return await asyncio.wait_for(future, timeout)
        finally:
            self.pending.pop(request_id, None)

    async def notify(self, method, params=None):
        await self.send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    async def _dispatch(self, data):
        try:
            result = await self.handler(data["method"], data.get("params") or {}, "id" in data)
            if "id" in data:
                await self.send({"jsonrpc": "2.0", "id": data["id"], "result": result})
        except Exception:
            if "id" in data and not self.closed:
                await self.send({"jsonrpc": "2.0", "id": data["id"], "error": {
                    "code": -32601, "message": "Unsupported or denied client request"}})

    async def _read(self):
        try:
            while not self.closed:
                raw = await asyncio.to_thread(self.process.stdout.readline, MAX_FRAME + 1)
                if not raw:
                    break
                if len(raw) > MAX_FRAME:
                    raise ValueError("Oversized runtime frame")
                data = json.loads(raw)
                if not isinstance(data, dict):
                    raise ValueError("Invalid runtime frame")
                if "method" in data:
                    if not isinstance(data["method"], str) or len(self.tasks) >= 32:
                        raise ValueError("Runtime request overflow")
                    task = asyncio.create_task(self._dispatch(data))
                    self.tasks.add(task)
                    task.add_done_callback(self.tasks.discard)
                else:
                    future = self.pending.get(data.get("id"))
                    if future and not future.done():
                        if "error" in data:
                            future.set_exception(RuntimeError("Runtime rejected the request; inspect its configuration and account."))
                        else:
                            future.set_result(data.get("result") or {})
        except (OSError, ValueError, TypeError):
            pass
        finally:
            await self.close()

    async def _drain_stderr(self):
        # Do not retain arbitrary startup stderr: it may contain credentials.
        while not self.closed:
            if not await asyncio.to_thread(self.process.stderr.read, 4096):
                break

    async def close(self):
        if self.closed:
            return
        self.closed = True
        if self.job:
            self.job()
            self.job = None
        elif os.name != "nt":
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if self.process.poll() is None:
            self.process.kill()
        await asyncio.to_thread(self.process.wait, 10)
        for future in self.pending.values():
            if not future.done():
                future.set_exception(RuntimeError("Coding runtime disconnected"))
        for task in (self.reader, self.stderr, *self.tasks):
            if task is not asyncio.current_task():
                task.cancel()
        for pipe in (self.process.stdin, self.process.stdout, self.process.stderr):
            pipe.close()
