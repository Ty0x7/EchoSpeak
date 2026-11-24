"""A private Ollama server for one isolated evaluation.

The workstation's shared Ollama service serves the installed EchoSpeak at a fixed 16K window, and a served window
can only change when a server starts (agent/lean/window.py). Development comparisons run at 32K (owner decision,
24 November 2026; docs/research/context-strategy.md), so each evaluation gets its own short-lived server:

- the requested window, a q8_0 KV cache, flash attention and one runner, like the shared service;
- llama-server's host prompt cache off (``LLAMA_ARG_CACHE_RAM=0``). With its default 8 GiB limit the shared service
  stored about 3 GiB per Gemma 4 12B prompt and grew to 7.5 GiB in three prompts, taking available RAM below the
  controller's floor (1.7 GB, 24 November 2026). A fresh server per evaluation also keeps runs independent;
- it starts only while the shared service has no model loaded (both can't fit in 8 GB of VRAM), and ``busy()``
  lets the caller stop if the shared service loads one mid-run. The shared service itself is never changed.
"""
from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import time
import urllib.request
from contextlib import contextmanager
from typing import IO, Iterator

SHARED = "http://127.0.0.1:11434"
DEFAULT_CONTEXT = 32768


def _get(url: str, route: str, timeout: float = 5) -> dict:
    with urllib.request.urlopen(url.rstrip("/") + route, timeout=timeout) as reply:
        return json.load(reply)


def busy(shared: str = SHARED) -> str:
    """The models the shared service has loaded, or "" (also "" when it isn't running)."""
    try:
        models = _get(shared, "/api/ps").get("models") or []
    except OSError:
        return ""
    return ", ".join(str(m.get("name") or "?") for m in models)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def environment(port: int, context: int, cache_type: str = "q8_0") -> dict[str, str]:
    return {**os.environ, "OLLAMA_HOST": f"127.0.0.1:{port}", "OLLAMA_CONTEXT_LENGTH": str(context),
            "OLLAMA_NUM_PARALLEL": "1", "OLLAMA_MAX_LOADED_MODELS": "1", "OLLAMA_FLASH_ATTENTION": "1",
            "OLLAMA_KV_CACHE_TYPE": cache_type, "OLLAMA_NO_CLOUD": "1", "OLLAMA_KEEP_ALIVE": "30m",
            "LLAMA_ARG_CACHE_RAM": "0"}


@contextmanager
def private_server(context: int = DEFAULT_CONTEXT, *, log: IO[str] | None = None, shared: str = SHARED,
                   wait_idle: float = 120, ollama: str = "ollama", cache_type: str = "q8_0") -> Iterator[str]:
    """Yield the URL of a private Ollama server serving ``context`` tokens; stop it and its runner afterwards."""
    deadline = time.monotonic() + wait_idle
    while loaded := busy(shared):
        if time.monotonic() >= deadline:
            raise RuntimeError(f"the shared Ollama service has {loaded} loaded; not starting a second server "
                               "beside it")
        time.sleep(5)
    port = _free_port()
    url = f"http://127.0.0.1:{port}"
    server = subprocess.Popen([ollama, "serve"], env=environment(port, context, cache_type),
                              stdout=log or subprocess.DEVNULL, stderr=subprocess.STDOUT, start_new_session=True)
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
        yield url
    finally:
        stop(server)


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
    with private_server(size) as address:
        print(address, flush=True)
        print(json.dumps(_get(address, "/api/version")))
    print("stopped", flush=True)
