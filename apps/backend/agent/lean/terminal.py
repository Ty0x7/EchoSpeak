"""Terminal for the lean loop: real shells, two backends.

host    PowerShell on this PC. Full access to installed tools (node, python,
        git). Pipes, variables, and chaining work.
docker  A persistent Linux dev container ("echospeak-sandbox") with Node,
        Python, and git. Only your workspace folders are mounted (at
        /work/<folder>), so commands cannot touch the rest of the PC. The
        container keeps running between commands, so installed packages and
        caches persist. Network is on by default (npm/pip need it).

Both backends share: a small never-run list, approval for dangerous commands
(see approvals.py), long timeouts for builds, and background processes for
dev servers and watchers.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from loguru import logger

from config import DATA_DIR, config
from agent.lean.toolbox import NativeTool

CONTAINER_NAME = "echospeak-sandbox"
IMAGE_TAG = "echospeak/sandbox:1"
DOCKERFILE = """\
FROM node:22-bookworm
RUN apt-get update \\
 && apt-get install -y --no-install-recommends python3-pip python3-venv python-is-python3 ripgrep jq zip unzip \\
 && rm -rf /var/lib/apt/lists/*
ENV PIP_BREAK_SYSTEM_PACKAGES=1 npm_config_update_notifier=false
WORKDIR /work
"""
FALLBACK_IMAGE = "node:22-bookworm"
DOCKER_DESKTOP_EXE = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Docker" / "Docker" / "Docker Desktop.exe"

# Commands that are never run, approval or not. Everything else that is risky
# pauses for approval instead (approvals.py).
_NEVER = re.compile(
    r"""(?ix)
    \b(format(\.com)?\s+[a-z]:|diskpart|bcdedit|cipher\s+/w|vssadmin\s+delete|
       shutdown(\.exe)?\b|restart-computer|stop-computer|clear-disk|initialize-disk|
       reg(\.exe)?\s+delete\s+hk(lm|ey_local_machine)|
       remove-item\s+.*[a-z]:\\?\s*(-recurse|$)|rm\s+-rf?\s+/(\s|$)|rm\s+-rf?\s+[a-z]:[\\/]?\s*$|
       mkfs|dd\s+if=.*of=/dev/)
    """
)


def execution_mode() -> str:
    raw = str(getattr(config, "terminal_execution_mode", "docker") or "docker").strip().lower()
    return "host" if raw == "host" else "docker"


def docker_network() -> str:
    value = str(getattr(config, "terminal_docker_network", "") or os.getenv("TERMINAL_DOCKER_NETWORK", "bridge")).strip().lower()
    return "none" if value in {"none", "off", "false", "0"} else "bridge"


def _limit(name: str, legacy_default: str, default: str) -> str:
    """Container limits. The old one-shot sandbox defaults (512m / 1 CPU) are too
    small for installs and builds, so they map to the new defaults."""
    value = str(getattr(config, name, "") or "").strip()
    return default if not value or value == legacy_default else value


def _clip(text: str, limit: int = 12000) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    head = text[: limit // 3]
    tail = text[-(limit * 2) // 3:]
    return f"{head}\n… [{len(text) - limit} characters omitted] …\n{tail}"


def _timeout(value: Any, default: int = 120) -> int:
    try:
        return max(1, min(int(value or default), 900))
    except (TypeError, ValueError):
        return default


# ── workspace mounts (docker) ───────────────────────────────────────────
def workspace_roots(project_root: str = "") -> list[Path]:
    roots: list[Path] = []
    candidates = [project_root, getattr(config, "file_tool_root", "")] + list(getattr(config, "file_tool_extra_roots", None) or [])
    for raw in candidates:
        if not str(raw or "").strip():
            continue
        try:
            path = Path(str(raw)).expanduser().resolve()
        except Exception:
            continue
        if path.exists() and path.is_dir() and path not in roots:
            # Never mount a drive root or the whole home folder.
            if path == Path(path.anchor) or path == Path.home():
                continue
            roots.append(path)
    # Drop roots nested inside another root (the parent mount covers them).
    roots.sort(key=lambda p: len(p.parts))
    unique: list[Path] = []
    for root in roots:
        if not any(_is_within(root, parent) for parent in unique):
            unique.append(root)
    return unique


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return str(path).casefold().startswith(str(parent).casefold().rstrip("\\/") + os.sep)


def _mount_name(root: Path, used: set[str]) -> str:
    base = re.sub(r"[^A-Za-z0-9._-]+", "-", root.name or "root").strip("-") or "root"
    name, n = base, 2
    while name.lower() in used:
        name, n = f"{base}-{n}", n + 1
    used.add(name.lower())
    return name


def mount_plan(project_root: str = "") -> list[tuple[Path, str]]:
    used: set[str] = set()
    return [(root, f"/work/{_mount_name(root, used)}") for root in workspace_roots(project_root)]


def to_container_path(host_path: Path, plan: list[tuple[Path, str]]) -> Optional[str]:
    for root, target in plan:
        if _is_within(host_path, root):
            try:
                rel = host_path.resolve().relative_to(root)
            except ValueError:
                rel = Path(str(host_path)[len(str(root)):].lstrip("\\/"))
            rel_s = str(rel).replace("\\", "/")
            return target if rel_s in {"", "."} else f"{target}/{rel_s}"
    return None


def translate_windows_paths(command: str, plan: list[tuple[Path, str]]) -> str:
    """Rewrite C:\\Users\\me\\Desktop\\app\\x.js -> /work/Desktop/app/x.js inside commands."""
    out = command
    for root, target in sorted(plan, key=lambda item: -len(str(item[0]))):
        for variant in {str(root), str(root).replace("\\", "/")}:
            pattern = re.compile(re.escape(variant) + r"((?:[\\/][^\s\"'`;|&<>]*)?)", re.I)
            out = pattern.sub(lambda m: target + m.group(1).replace("\\", "/"), out)
    return out


# ── docker backend ──────────────────────────────────────────────────────
class DockerSandbox:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.docker = shutil.which("docker") or str(Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Docker" / "Docker" / "resources" / "bin" / "docker.exe")
        self.image = ""

    def _run(self, args: list[str], timeout: float = 60, input_text: Optional[str] = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            [self.docker, *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, input=input_text,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    def daemon_ok(self) -> tuple[bool, str]:
        if not Path(self.docker).exists() and not shutil.which("docker"):
            return False, "Docker is not installed."
        try:
            proc = self._run(["info", "--format", "{{.ServerVersion}}"], timeout=8)
        except Exception as exc:
            return False, f"Docker did not respond ({type(exc).__name__})."
        if proc.returncode == 0 and proc.stdout.strip():
            return True, f"Docker {proc.stdout.strip()}"
        return False, "Docker Desktop is not running."

    def ensure_daemon(self, wait_seconds: float = 90) -> tuple[bool, str]:
        ok, detail = self.daemon_ok()
        if ok or "not installed" in detail:
            return ok, detail
        if DOCKER_DESKTOP_EXE.exists():
            logger.info("Starting Docker Desktop for the terminal sandbox")
            try:
                subprocess.Popen([str(DOCKER_DESKTOP_EXE)], creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
            except Exception as exc:
                return False, f"Could not start Docker Desktop: {exc}"
            deadline = time.monotonic() + wait_seconds
            while time.monotonic() < deadline:
                time.sleep(3)
                ok, detail = self.daemon_ok()
                if ok:
                    return ok, detail
            return False, "Docker Desktop is starting but not ready yet (first launch may need you to accept its terms in the Docker window)."
        return False, detail

    def ensure_image(self) -> str:
        if self.image:
            return self.image
        custom = str(getattr(config, "terminal_docker_image", "") or "").strip()
        if custom and custom not in {"python:3.12-slim"}:
            self.image = custom
            return custom
        probe = self._run(["image", "inspect", IMAGE_TAG], timeout=20)
        if probe.returncode == 0:
            self.image = IMAGE_TAG
            return IMAGE_TAG
        logger.info("Building {} (first run only)", IMAGE_TAG)
        build = self._run(["build", "-t", IMAGE_TAG, "-"], timeout=900, input_text=DOCKERFILE)
        if build.returncode == 0:
            self.image = IMAGE_TAG
        else:
            logger.warning("Sandbox image build failed, using {}: {}", FALLBACK_IMAGE, build.stderr[-400:])
            self.image = FALLBACK_IMAGE
        return self.image

    def ensure_container(self, plan: list[tuple[Path, str]]) -> tuple[bool, str]:
        with self._lock:
            ok, detail = self.ensure_daemon()
            if not ok:
                return False, detail
            image = self.ensure_image()
            signature = hashlib.sha1(json.dumps(
                {"image": image, "mounts": [(str(r), t) for r, t in plan], "net": docker_network()}, sort_keys=True,
            ).encode()).hexdigest()[:12]
            state = self._run(["inspect", "-f", "{{.State.Running}} {{index .Config.Labels \"echospeak.sig\"}}", CONTAINER_NAME], timeout=15)
            if state.returncode == 0:
                running, _, sig = state.stdout.strip().partition(" ")
                if sig == signature and running == "true":
                    return True, "ready"
                if sig == signature:
                    started = self._run(["start", CONTAINER_NAME], timeout=60)
                    if started.returncode == 0:
                        return True, "ready"
                self._run(["rm", "-f", CONTAINER_NAME], timeout=60)
            args = [
                "run", "-d", "--name", CONTAINER_NAME, "--init", "--label", f"echospeak.sig={signature}",
                "--network", docker_network(), "--memory", _limit("terminal_docker_memory", "512m", "4g"),
                "--cpus", _limit("terminal_docker_cpus", "1.0", "2"),
                "--security-opt", "no-new-privileges", "-w", "/work",
            ]
            for root, target in plan:
                args += ["-v", f"{root}:{target}"]
            args += [image, "sleep", "infinity"]
            created = self._run(args, timeout=180)
            if created.returncode != 0:
                return False, f"Could not start the sandbox container: {created.stderr.strip()[-400:]}"
            return True, "ready"

    def exec(self, command: str, workdir: str, timeout: int) -> tuple[int, str]:
        try:
            proc = self._run(["exec", "-w", workdir, CONTAINER_NAME, "bash", "-lc", command], timeout=timeout + 5)
            out = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")
            return proc.returncode, out
        except subprocess.TimeoutExpired:
            return 124, f"Timed out after {timeout}s. For long-running servers use process_start."

    def remove_container(self) -> None:
        with self._lock:
            self._run(["rm", "-f", CONTAINER_NAME], timeout=60)

    def status(self) -> dict[str, Any]:
        ok, detail = self.daemon_ok()
        info: dict[str, Any] = {"docker_installed": "not installed" not in detail, "docker_running": ok, "detail": detail}
        if ok:
            state = self._run(["inspect", "-f", "{{.State.Running}}", CONTAINER_NAME], timeout=10)
            info["container_running"] = state.returncode == 0 and state.stdout.strip() == "true"
            image = self._run(["image", "inspect", IMAGE_TAG], timeout=10)
            info["image_ready"] = image.returncode == 0
        return info


_SANDBOX = DockerSandbox()


def get_sandbox() -> DockerSandbox:
    return _SANDBOX


# ── host backend ────────────────────────────────────────────────────────
def _host_shell() -> list[str]:
    pwsh = shutil.which("pwsh")
    if os.name == "nt":
        exe = pwsh or "powershell.exe"
        return [exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command"]
    return [shutil.which("bash") or "/bin/sh", "-lc"]


def _host_command(command: str) -> str:
    if os.name == "nt":
        return "[Console]::OutputEncoding=[Text.Encoding]::UTF8; $ProgressPreference='SilentlyContinue'; " + command
    return command


def _run_host(command: str, cwd: Path, timeout: int) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            [*_host_shell(), _host_command(command)], cwd=str(cwd), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        out = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")
        return proc.returncode, out
    except subprocess.TimeoutExpired as exc:
        partial = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        return 124, f"{partial}\nTimed out after {timeout}s. For long-running servers use process_start."


# ── background processes ────────────────────────────────────────────────
@dataclass
class BackgroundProcess:
    id: str
    command: str
    cwd: str
    mode: str
    log_path: Path
    started_at: float = field(default_factory=time.time)
    popen: Optional[subprocess.Popen] = None
    container_pid: str = ""


_PROCESSES: dict[str, BackgroundProcess] = {}
_PROC_DIR = Path(DATA_DIR) / "lean" / "processes"


def _process_alive(proc: BackgroundProcess) -> bool:
    if proc.mode == "host":
        return proc.popen is not None and proc.popen.poll() is None
    code, out = _SANDBOX.exec(f"kill -0 {proc.container_pid} 2>/dev/null && echo alive", "/work", 10)
    return "alive" in out


def _tail(proc: BackgroundProcess, chars: int = 6000) -> str:
    if proc.mode == "host":
        try:
            data = proc.log_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            data = ""
    else:
        _, data = _SANDBOX.exec(f"tail -c {chars} /tmp/es-proc-{proc.id}.log 2>/dev/null", "/work", 15)
    return data[-chars:]


def stop_all_processes() -> None:
    for proc in list(_PROCESSES.values()):
        try:
            _stop(proc)
        except Exception:
            pass


def _stop(proc: BackgroundProcess) -> None:
    if proc.mode == "host" and proc.popen is not None and proc.popen.poll() is None:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.popen.pid), "/T", "/F"], capture_output=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            proc.popen.terminate()
    elif proc.mode == "docker" and proc.container_pid:
        _SANDBOX.exec(f"kill -TERM -- -{proc.container_pid} 2>/dev/null || kill -TERM {proc.container_pid}", "/work", 15)


# ── tool surface ────────────────────────────────────────────────────────
class Terminal:
    """Bound to one session's project folder."""

    def __init__(self, project_root: str = "") -> None:
        self.project_root = project_root
        self.mode = execution_mode()

    def _cwd(self, raw: Any) -> tuple[Optional[Path], str]:
        from agent.tools import _format_file_tool_roots, _safe_file_path

        value = str(raw or "").strip()
        if not value or value == ".":
            base = self.project_root or str(getattr(config, "file_tool_root", "") or "")
            path = Path(base) if base else None
            if path is None or not path.exists():
                roots = workspace_roots(self.project_root)
                path = roots[0] if roots else None
            if path is None:
                return None, "Error: no workspace folder is configured. Attach a project folder or set a workspace root in Settings."
            return path, ""
        target = _safe_file_path(value)
        if target is None:
            return None, f"Error: folder not allowed: {value}. Allowed: {_format_file_tool_roots()}"
        if not target.exists() or not target.is_dir():
            return None, f"Error: folder not found: {target}"
        return target, ""

    def describe(self) -> str:
        if self.mode == "host":
            shell = "PowerShell 7" if shutil.which("pwsh") else "Windows PowerShell"
            return (f"terminal runs {shell} directly on this PC (pipes, $variables and ; chaining work; "
                    "use `;` not `&&` in Windows PowerShell). Start servers and watchers with process_start.")
        plan = mount_plan(self.project_root)
        mounts = ", ".join(f"{root} -> {target}" for root, target in plan) or "none"
        return ("terminal runs bash in a Linux sandbox container (Node 22, Python 3, git; network "
                f"{'on' if docker_network() == 'bridge' else 'off'}). Your folders are mounted: {mounts}. "
                "Windows paths in commands are translated automatically. Start servers and watchers with process_start.")

    def run(self, args: dict[str, Any]) -> str:
        command = str(args.get("command") or args.get("cmd") or "").strip()
        if not command:
            return "Error: command is empty."
        if _NEVER.search(command):
            return "Refused: that command can damage the system (disk, boot, or OS-level destruction) and is never run."
        cwd, error = self._cwd(args.get("cwd"))
        if error:
            return error
        timeout = _timeout(args.get("timeout"), 120)
        started = time.perf_counter()
        if self.mode == "host":
            code, out = _run_host(command, cwd, timeout)
        else:
            plan = mount_plan(self.project_root)
            ok, detail = _SANDBOX.ensure_container(plan)
            if not ok:
                return (f"Error: the Docker sandbox is not available: {detail} "
                        "Tell the user to start Docker Desktop, or switch Terminal to Host in Settings > Terminal.")
            workdir = to_container_path(cwd, plan) or "/work"
            code, out = _SANDBOX.exec(translate_windows_paths(command, plan), workdir, timeout)
        elapsed = time.perf_counter() - started
        status = "ok" if code == 0 else f"exit code {code}"
        return f"[{status} · {elapsed:.1f}s · {self.mode} · {cwd}]\n{_clip(out.strip()) or '(no output)'}"

    def process_start(self, args: dict[str, Any]) -> str:
        command = str(args.get("command") or "").strip()
        if not command:
            return "Error: command is empty."
        if _NEVER.search(command):
            return "Refused: that command is never run."
        cwd, error = self._cwd(args.get("cwd"))
        if error:
            return error
        pid = uuid.uuid4().hex[:8]
        _PROC_DIR.mkdir(parents=True, exist_ok=True)
        proc = BackgroundProcess(id=pid, command=command, cwd=str(cwd), mode=self.mode, log_path=_PROC_DIR / f"{pid}.log")
        if self.mode == "host":
            handle = open(proc.log_path, "w", encoding="utf-8", errors="replace")
            proc.popen = subprocess.Popen(
                [*_host_shell(), _host_command(command)], cwd=str(cwd), stdout=handle, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            )
        else:
            plan = mount_plan(self.project_root)
            ok, detail = _SANDBOX.ensure_container(plan)
            if not ok:
                return f"Error: the Docker sandbox is not available: {detail}"
            workdir = to_container_path(cwd, plan) or "/work"
            inner = translate_windows_paths(command, plan).replace("'", "'\"'\"'")
            code, out = _SANDBOX.exec(f"setsid bash -lc '{inner}' > /tmp/es-proc-{pid}.log 2>&1 & echo $!", workdir, 20)
            proc.container_pid = out.strip().splitlines()[-1] if out.strip() else ""
        _PROCESSES[pid] = proc
        time.sleep(float(args.get("wait_seconds") or 3))
        alive = _process_alive(proc)
        return (f"Started process {pid} ({'running' if alive else 'exited already'}): {command}\n"
                f"First output:\n{_clip(_tail(proc, 3000), 3000) or '(none yet)'}\n"
                f"Use process_output with id={pid} to check on it, process_stop to end it.")

    def process_output(self, args: dict[str, Any]) -> str:
        proc = _PROCESSES.get(str(args.get("id") or ""))
        if proc is None:
            running = ", ".join(f"{p.id}: {p.command[:60]}" for p in _PROCESSES.values()) or "none"
            return f"Error: no process with that id. Known processes: {running}"
        state = "running" if _process_alive(proc) else "exited"
        return f"Process {proc.id} is {state} ({proc.command}).\nRecent output:\n{_clip(_tail(proc), 6000) or '(no output)'}"

    def process_stop(self, args: dict[str, Any]) -> str:
        proc = _PROCESSES.get(str(args.get("id") or ""))
        if proc is None:
            return "Error: no process with that id."
        _stop(proc)
        return f"Stopped process {proc.id}."

    def tools(self) -> list[NativeTool]:
        return [
            NativeTool(
                name="terminal",
                description=f"Run a shell command and get its output. {self.describe()} Default folder is the project folder.",
                parameters={"type": "object", "properties": {
                    "command": {"type": "string"},
                    "cwd": {"type": "string", "description": "Folder to run in (default: project folder)."},
                    "timeout": {"type": "integer", "description": "Seconds, up to 900 (default 120)."},
                }, "required": ["command"]},
                func=self.run,
            ),
            NativeTool(
                name="process_start",
                description="Start a long-running command in the background (dev server, watcher, build). Returns an id.",
                parameters={"type": "object", "properties": {
                    "command": {"type": "string"},
                    "cwd": {"type": "string"},
                }, "required": ["command"]},
                func=self.process_start,
            ),
            NativeTool(
                name="process_output",
                description="Show recent output of a background process and whether it is still running.",
                parameters={"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
                func=self.process_output,
                parallel_safe=True,
            ),
            NativeTool(
                name="process_stop",
                description="Stop a background process.",
                parameters={"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
                func=self.process_stop,
            ),
        ]


def terminal_status() -> dict[str, Any]:
    mode = execution_mode()
    info: dict[str, Any] = {"mode": mode, "network": docker_network()}
    info["docker"] = _SANDBOX.status()
    info["host_shell"] = "PowerShell 7" if shutil.which("pwsh") else "Windows PowerShell" if os.name == "nt" else "bash"
    info["mounts"] = [{"host": str(r), "container": t} for r, t in mount_plan()]
    info["processes"] = [
        {"id": p.id, "command": p.command, "mode": p.mode, "started_at": p.started_at}
        for p in _PROCESSES.values()
    ]
    return info
