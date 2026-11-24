"""A private Ollama server for one isolated evaluation.

The workstation's shared Ollama service serves the installed EchoSpeak at a fixed 16K window, and a served window
can only change when a server starts (agent/lean/window.py). Development comparisons run at 32K (owner decision,
24 November 2026; docs/research/context-strategy.md), so each evaluation gets its own short-lived server:

- the requested window, a q8_0 KV cache, flash attention and one runner, like the shared service;
- llama-server's host prompt cache off (``LLAMA_ARG_CACHE_RAM=0``). With its default 8 GiB limit the shared service
  stored about 3 GiB per Gemma 4 12B prompt and grew to 7.5 GiB in three prompts, taking available RAM below the
  controller's floor (1.7 GB, 24 November 2026). A fresh server per evaluation also keeps runs independent;
- it starts only while the shared service has no model loaded (both can't fit in 8 GB of VRAM), and ``busy()``
  lets the caller stop if the shared service loads one mid-run. The shared service itself is never changed;
- the checks fail closed. A shared-service status that can't be read counts as busy (a refused connection
  means it isn't running). The GPU's own process list is checked independently through ``nvidia-smi``: another
  model server (llama-server, ollama), any process holding 1 GiB or more, or less than 6 GiB free VRAM counts as
  busy, and so does an ``nvidia-smi`` that fails. Small desktop GPU users (a browser, a video player) don't;
- one private evaluation at a time: a machine-wide lock (``flock``, released by the kernel if the holder dies)
  is held for the server's whole life, so two controllers can't start competing models.
"""
from __future__ import annotations

import fcntl
import json
import os
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Iterator

SHARED = "http://127.0.0.1:11434"
DEFAULT_CONTEXT = 32768


def _get(url: str, route: str, timeout: float = 5) -> dict:
    with urllib.request.urlopen(url.rstrip("/") + route, timeout=timeout) as reply:
        return json.load(reply)


def busy(shared: str = SHARED) -> str:
    """Why the shared service can't share the GPU now, or "" (also "" when it isn't running).

    An unreadable status counts as busy: only a refused connection means nothing is there.
    """
    try:
        models = _get(shared, "/api/ps").get("models") or []
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, ConnectionRefusedError):
            return ""
        return f"the shared Ollama service's status couldn't be read ({exc.reason})"
    except (OSError, ValueError, AttributeError) as exc:
        return f"the shared Ollama service's status couldn't be read ({exc})"
    loaded = ", ".join(str(m.get("name") or "?") for m in models)
    return f"the shared Ollama service has {loaded} loaded" if loaded else ""


MODEL_SERVERS = ("llama-server", "ollama")
LARGE_MIB = 1024
MIN_FREE_MIB = 6144


def gpu_busy(own_group: int | None = None, nvidia_smi: str = "nvidia-smi", min_free_mib: int = MIN_FREE_MIB) -> str:
    """Why the GPU can't take a private evaluation model now, or "". Fails closed when it can't be read.

    ``own_group``: the process group of this evaluation's own server, whose runner is expected on the GPU.
    """
    try:
        apps = subprocess.run([nvidia_smi, "--query-compute-apps=pid,process_name,used_memory",
                               "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=10)
        totals = subprocess.run([nvidia_smi, "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
                                capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"the GPU's processes couldn't be read ({exc})"
    if apps.returncode or totals.returncode:
        return f"the GPU's processes couldn't be read (nvidia-smi exited {apps.returncode or totals.returncode})"
    competing = []
    for line in apps.stdout.splitlines():
        parts = [p.strip() for p in line.rsplit(",", 1)]
        head = parts[0].split(",", 1)
        if len(parts) != 2 or len(head) != 2:
            continue
        try:
            pid, used = int(head[0]), int(parts[1])
        except ValueError:
            return f"the GPU's processes couldn't be read ({line[:80]})"
        try:
            group = os.getpgid(pid)
        except (ProcessLookupError, PermissionError):
            group = None
        if own_group is not None and group == own_group:
            continue
        name = head[1].strip()
        if used >= LARGE_MIB or any(server in os.path.basename(name.split()[0] if name else "") for server in MODEL_SERVERS):
            competing.append(f"{os.path.basename(name.split()[0]) if name else '?'} (pid {pid}, {used} MiB)")
    if competing:
        return "another GPU workload is running: " + ", ".join(competing)
    if own_group is None:
        try:
            used, total = (int(v) for v in totals.stdout.splitlines()[0].split(","))
        except (IndexError, ValueError):
            return "the GPU's memory couldn't be read"
        if total - used < min_free_mib:
            return f"only {total - used} MiB of GPU memory is free (an evaluation needs {min_free_mib})"
    return ""


def _lock_path() -> Path:
    return Path(os.environ.get("ECHO_EVAL_GPU_LOCK") or Path.home() / ".cache" / "echospeak" / "eval-gpu.lock")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def environment(port: int, context: int, cache_type: str = "q8_0") -> dict[str, str]:
    return {**os.environ, "OLLAMA_HOST": f"127.0.0.1:{port}", "OLLAMA_CONTEXT_LENGTH": str(context),
            "OLLAMA_NUM_PARALLEL": "1", "OLLAMA_MAX_LOADED_MODELS": "1", "OLLAMA_FLASH_ATTENTION": "1",
            "OLLAMA_KV_CACHE_TYPE": cache_type, "OLLAMA_NO_CLOUD": "1", "OLLAMA_KEEP_ALIVE": "30m",
            "LLAMA_ARG_CACHE_RAM": "0"}


@dataclass
class PrivateServer:
    url: str
    group: int
    shared: str = SHARED
    nvidia_smi: str = "nvidia-smi"

    def competing(self) -> str:
        """A reason to stop the evaluation now: the shared service or another workload took the GPU."""
        return busy(self.shared) or gpu_busy(self.group, self.nvidia_smi)


@contextmanager
def private_server(context: int = DEFAULT_CONTEXT, *, log: IO[str] | None = None, shared: str = SHARED,
                   wait_idle: float = 120, ollama: str = "ollama", cache_type: str = "q8_0",
                   nvidia_smi: str = "nvidia-smi") -> Iterator[PrivateServer]:
    """A private Ollama server serving ``context`` tokens; stopped with its runner afterwards.

    Waits up to ``wait_idle`` seconds for the evaluation lock, an idle shared service and a free GPU, then refuses.
    """
    deadline = time.monotonic() + wait_idle
    lock_file = _lock_path()
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_file, "a+") as lock:
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("another private evaluation holds the GPU (lock "
                                       f"{lock_file}); not starting a second one beside it") from None
                time.sleep(2)
        try:
            while reason := (busy(shared) or gpu_busy(None, nvidia_smi)):
                if time.monotonic() >= deadline:
                    raise RuntimeError(f"{reason}; not starting a private evaluation server beside it")
                time.sleep(5)
            port = _free_port()
            url = f"http://127.0.0.1:{port}"
            server = subprocess.Popen([ollama, "serve"], env=environment(port, context, cache_type),
                                      stdout=log or subprocess.DEVNULL, stderr=subprocess.STDOUT,
                                      start_new_session=True)
            try:
                ready = time.monotonic() + 30
                while True:
                    if server.poll() is not None:
                        raise RuntimeError(f"the private Ollama server exited ({server.returncode})")
                    try:
                        _get(url, "/api/version", timeout=2)
                        break
                    except OSError:
                        if time.monotonic() >= ready:
                            raise RuntimeError("the private Ollama server did not answer within 30s")
                        time.sleep(0.5)
                yield PrivateServer(url, server.pid, shared, nvidia_smi)
            finally:
                stop(server)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def stop(server: subprocess.Popen) -> None:
    """Stop the server's whole process group, its model runner included."""
    for sig, wait in ((signal.SIGTERM, 15), (signal.SIGKILL, 5)):
        try:
            os.killpg(server.pid, sig)
        except ProcessLookupError:
            break
        try:
            server.wait(wait)
        except subprocess.TimeoutExpired:
            continue
        # ollama serve has exited; a runner it left behind in the group gets the same signal.
        try:
            os.killpg(server.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        break


def served_context(url: str) -> int:
    """The window the private server's loaded model serves, or 0 before a model is loaded."""
    models = _get(url, "/api/ps").get("models") or []
    return int(models[0].get("context_length") or 0) if models else 0


if __name__ == "__main__":  # a manual smoke check: python scripts/eval_server.py [context]
    import sys

    size = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CONTEXT
    with private_server(size) as handle:
        print(handle.url, flush=True)
        print(json.dumps(_get(handle.url, "/api/version")))
    print("stopped", flush=True)
