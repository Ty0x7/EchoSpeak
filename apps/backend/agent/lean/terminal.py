"""Terminal for the lean loop: real shells, two backends.

host    PowerShell on this PC. Full access to installed tools (node, python,
        git). Pipes, variables, and chaining work.
docker  A persistent Linux dev container ("echospeak-sandbox") with Node,
        Python, and git. Only your workspace folders are mounted (at
        /work/<folder>), so commands cannot touch the rest of the PC. The
        container keeps running between commands, so installed packages and
        caches persist. Network is on by default (npm/pip need it).

Both backends share: a small never-run list, approval for dangerous commands
(see approvals.py), and one way of running a command (Codex's "yield", adapted):

- A command gets no keyboard input (stdin closed, git/pip prompts off), so it
  fails fast instead of hanging; commands that only work interactively are
  refused up front with the non-interactive form to use.
- It runs for up to ``timeout`` seconds. If it is still going then, it is not
  killed: it keeps running as a background process with a process_id, and the
  agent gets the output so far. Servers and watchers go to the background
  after a short look, because they never finish.
- stdout and stderr come back separately, with the exit code and, for common
  failures, a one-line hint.
- Files a command overwrites with shell redirection are checkpointed first,
  so undo still covers them.
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

from agent.child_env import child_env
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
    """The configured mode: "auto" (sandbox when Docker runs, else this PC), "docker" or "host"."""
    raw = str(getattr(config, "terminal_execution_mode", "auto") or "auto").strip().lower()
    if raw in {"docker", "sandbox", "container"}:
        return "docker"
    return "host" if raw == "host" else "auto"


_DOCKER_CHECK: dict[str, Any] = {"at": 0.0, "ok": False}


def docker_available(max_age: float = 30.0) -> bool:
    """Is Docker running right now? Cached briefly so every command doesn't pay for `docker info`."""
    now = time.monotonic()
    if now - float(_DOCKER_CHECK["at"]) > max_age:
        ok, _ = _SANDBOX.daemon_ok()
        _DOCKER_CHECK.update(at=now, ok=ok)
    return bool(_DOCKER_CHECK["ok"])


def resolved_mode() -> str:
    """Where commands actually run: "docker" (sandbox) or "host"."""
    mode = execution_mode()
    if mode == "auto":
        return "docker" if docker_available() else "host"
    return mode


def network_policy() -> str:
    """Sandbox internet access: "ask" (off until a command needs it and you approve), "on" or "off"."""
    value = str(getattr(config, "terminal_docker_network", "") or os.getenv("TERMINAL_DOCKER_NETWORK", "ask")).strip().lower()
    if value in {"none", "off", "false", "0"}:
        return "off"
    if value in {"bridge", "on", "true", "1"}:
        return "on"
    return "ask"


def docker_network() -> str:
    """Network the container is created on. It is disconnected when the policy is not "on"."""
    return "none" if network_policy() == "off" else "bridge"


# In the sandbox the workspace folders are still your real files.
_WORKSPACE_DESTRUCTIVE = re.compile(
    r"(?ix)(^|[\s;&|(])(rm\s|rmdir|unlink\s|shred\s|git\s+reset\s+--hard|git\s+clean|git\s+checkout\s+--|"
    r"git\s+push|find\s+.*-delete|truncate\s)"
)


def approval_for(args: dict[str, Any]) -> tuple[bool, str]:
    """When a terminal command needs the user's OK. Called by approvals.py."""
    from agent.lean.approvals import dangerous_command

    command = str(args.get("command") or args.get("cmd") or "")
    if resolved_mode() == "host":
        return (True, "this command can change or delete things") if dangerous_command(command) else (False, "")
    if str(args.get("where") or "").lower() == "host":
        return True, "this runs directly on your PC, outside the sandbox"
    if args.get("network") and network_policy() == "ask":
        return True, "this command needs internet access"
    if _WORKSPACE_DESTRUCTIVE.search(command):
        return True, "this can delete or overwrite files in your project"
    return False, ""


def _limit(name: str, legacy_default: str, default: str) -> str:
    """Container limits. The old one-shot sandbox defaults (512m / 1 CPU) are too
    small for installs and builds, so they map to the new defaults."""
    value = str(getattr(config, name, "") or "").strip()
    return default if not value or value == legacy_default else value


def _clip(text: str, limit: int = 12000) -> str:
    """Head and tail (errors and summaries are usually at the end), middle dropped."""
    text = text or ""
    if len(text) <= limit:
        return text
    head = text[: limit // 3]
    tail = text[-(limit * 2) // 3:]
    return (f"{head}\n… [{len(text) - limit} characters omitted. To see them, send the output to a file "
            f"(command > out.txt) and read the part you need with file_search or file_read] …\n{tail}")


# ── what kind of command is this? ───────────────────────────────────────
# Servers and watchers never finish on their own.
_LONG_RUNNING = re.compile(
    r"""(?ix)
    \b(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?(?:dev|start|serve|watch|preview)\b
    | \bnpx\s+(?:nodemon|serve|http-server|live-server)\b
    | (?:^|[\s;&|])(?:npx\s+)?vite(?:\s+(?:dev|serve|preview))?(?=\s*(?:$|[;&|]|--))
    | \b(?:npx\s+)?next\s+(?:dev|start)\b
    | \b(?:python3?|py)\s+(?:-m\s+http\.server|manage\.py\s+runserver)\b
    | \bflask\s+run\b | \buvicorn\s | \bgunicorn\s | \bnodemon\b | \bhttp-server\b
    | \bstreamlit\s+run\b | \bjupyter\s+(?:notebook|lab)\b | \bng\s+serve\b | \bhugo\s+server\b
    | \bdocker[\s-]compose\s+up\b(?!.*\s(?:-d|--detach)\b)
    | \s--watch\b | \btail\s+-f\b | \bget-content\b.*\s-wait\b | \bping\s+-t\b
    """
)

# Commands that only work with someone at the keyboard: refused with what to run instead.
_INTERACTIVE: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(?i)^\s*(?:sudo\s+)?(?:vim?|nvim|nano|emacs|pico|less|more|top|htop|man|watch)\b"),
     "it is a full-screen program that needs a keyboard. To view or change a file use file_read or file_edit; "
     "otherwise use a non-interactive command"),
    (re.compile(r"(?i)^\s*(?:python3?|py|node|deno|irb|ghci|psql|mysql|sqlite3|ssh|sftp|ftp|telnet|pwsh|powershell|cmd|bash|sh)\s*$"),
     "it opens an interactive session. Pass the work as arguments instead (python -c \"...\", node script.js, "
     "sqlite3 db.sqlite \"SELECT ...\")"),
    (re.compile(r"(?i)\bnpm\s+init\b(?!.*\s(?:-y|--yes)\b)"), "npm init asks questions. Use npm init -y"),
    (re.compile(r"(?i)\bgit\s+(?:add|checkout|reset|stash)\s+(?:-p|--patch)\b|\bgit\s+add\s+(?:-i|--interactive)\b"),
     "interactive git modes need a keyboard. Name the files instead"),
    (re.compile(r"(?i)\bread-host\b|\bread\s+-[a-z]*p\b"), "it waits for typed input. Put the value in the command"),
]

# No prompts: git credentials, editors and pagers, pip questions.
_QUIET_ENV = {"GIT_TERMINAL_PROMPT": "0", "GIT_EDITOR": "true", "GIT_PAGER": "cat", "PAGER": "cat", "PIP_NO_INPUT": "1"}

# How long a server gets to print its first output before the agent moves on.
SERVER_WAIT = 6
# Background processes kept at once; past this, a command that runs too long is stopped instead.
MAX_BACKGROUND = 8


def interactive_problem(command: str) -> str:
    for pattern, advice in _INTERACTIVE:
        if pattern.search(command or ""):
            return advice
    return ""


def is_long_running(command: str) -> bool:
    return bool(_LONG_RUNNING.search(command or ""))


def failure_hint(code: int, text: str, where: str) -> str:
    """One line on the usual cause of a failure small models get stuck on."""
    low = (text or "").lower()
    if code == 127 or "command not found" in low or "is not recognized as" in low:
        place = "in the sandbox (install it with network=true, or" if where == "docker" else "on this PC (install it, or"
        return f"Hint: that program isn't installed {place} check the name)."
    if "'&&' is not a valid statement separator" in low or "token '&&' is not a valid" in low:
        return "Hint: Windows PowerShell 5.1 has no &&. Use ; between commands."
    if "empty commit message" in low:
        return "Hint: git commit needs the message in the command: git commit -m \"...\"."
    if any(phrase in low for phrase in ("could not resolve host", "temporary failure in name resolution",
                                        "network is unreachable", "getaddrinfo enotfound", "eai_again")):
        return "Hint: no internet for this command" + (". Run it again with network=true." if where == "docker" else ".")
    return ""


# Shell redirection and cmdlets that overwrite a file: >, >>, tee, Set-Content, Out-File, Add-Content.
_REDIRECT = re.compile(r"""(?<![<>&0-9])>{1,2}\s*("[^"]+"|'[^']+'|[^\s;|&<>()]+)""")
_WRITE_CMDLET = re.compile(
    r"""(?ix)\b(?:set-content|add-content|out-file|tee(?:-object)?)\b(?:\s+-(?:a|append|encoding\s+\S+|nonewline))*"""
    r"""\s+(?:-(?:literal)?(?:file)?path\s+)?("[^"]+"|'[^']+'|[^\s;|&<>()]+)"""
)


def redirect_targets(command: str) -> list[str]:
    """Plain file paths a command writes through redirection (not variables or devices)."""
    found = [m.group(1) for m in _REDIRECT.finditer(command or "")]
    found += [m.group(1) for m in _WRITE_CMDLET.finditer(command or "")]
    out: list[str] = []
    for raw in found:
        path = raw.strip("\"'")
        if not path or path.startswith(("$", "-", "&")) or path.lower() in {"/dev/null", "nul", "$null", "null"}:
            continue
        if path not in out:
            out.append(path)
    return out


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
            if network_policy() == "ask":
                # Offline until a command asks for the internet (and you approve).
                self.set_network(False)
            return True, "ready"

    def set_network(self, on: bool) -> bool:
        """Connect or disconnect the sandbox from the internet (bridge network)."""
        verb = "connect" if on else "disconnect"
        proc = self._run(["network", verb, "bridge", CONTAINER_NAME], timeout=30)
        text = (proc.stderr or "") + (proc.stdout or "")
        # Already in the wanted state is fine.
        return proc.returncode == 0 or "already exists" in text or "is not connected" in text

    def exec(self, command: str, workdir: str, timeout: int) -> tuple[int, str]:
        try:
            proc = self._run(["exec", "-w", workdir, CONTAINER_NAME, "bash", "-lc", command], timeout=timeout + 5)
            out = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")
            return proc.returncode, out
        except subprocess.TimeoutExpired:
            return 124, f"Timed out after {timeout}s."

    def launch(self, command: str, workdir: str, wait: int, proc_id: str) -> tuple[bool, int, str, str]:
        """Run in the background inside the container and wait up to ``wait`` seconds, in one exec.

        Returns (finished, exit code or pid, stdout, stderr). Unfinished commands keep
        running (their output stays in /tmp/es-proc-<id>.*); killing the docker client
        never stopped them anyway.
        """
        try:
            proc = self._run(["exec", "-w", workdir, CONTAINER_NAME, "bash", "-c", LAUNCH_SCRIPT, "_", proc_id, str(wait), command],
                             timeout=wait + 30)
        except subprocess.TimeoutExpired:
            return False, 0, "", "The sandbox did not answer."
        return parse_launch(proc.stdout or "", proc.stderr or "")

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


# One command, started in the background and waited on: the same script serves
# terminal (wait = timeout) and background starts (wait = a few seconds).
LAUNCH_SCRIPT = r"""
id="$1"; wait_s="$2"; cmd="$3"
base="/tmp/es-proc-$id"
export GIT_TERMINAL_PROMPT=0 GIT_EDITOR=true GIT_PAGER=cat PAGER=cat PIP_NO_INPUT=1
setsid bash -c 'bash -lc "$1"; echo $? > "$2"' _ "$cmd" "$base.exit" > "$base.log" 2> "$base.err" < /dev/null &
pid=$!
end=$((SECONDS + wait_s))
while kill -0 "$pid" 2>/dev/null && [ "$SECONDS" -lt "$end" ]; do sleep 0.05; done
if kill -0 "$pid" 2>/dev/null; then echo "__ES_RUNNING__ $pid"; exit 0; fi
wait "$pid" 2>/dev/null
echo "__ES_EXIT__ $(cat "$base.exit" 2>/dev/null || echo 1)"
cat "$base.log"
echo "__ES_STDERR__"
cat "$base.err"
rm -f "$base.log" "$base.err" "$base.exit"
"""


def parse_launch(stdout: str, stderr: str = "") -> tuple[bool, int, str, str]:
    first, _, rest = (stdout or "").partition("\n")
    if first.startswith("__ES_RUNNING__"):
        try:
            return False, int(first.split()[1]), "", ""
        except (IndexError, ValueError):
            return False, 0, "", ""
    if first.startswith("__ES_EXIT__"):
        try:
            code = int(first.split()[1])
        except (IndexError, ValueError):
            code = 1
        out, marker, err = rest.rpartition("__ES_STDERR__\n")
        if not marker:
            out, err = rest, ""
        return True, code, out.rstrip("\n"), err.rstrip("\n")
    # The script never ran (container gone, docker error).
    return True, 125, "", ((stderr or "") + (stdout or "")).strip() or "The sandbox could not run the command."


# ── host backend ────────────────────────────────────────────────────────
def _host_shell() -> list[str]:
    pwsh = shutil.which("pwsh")
    if os.name == "nt":
        exe = pwsh or "powershell.exe"
        return [exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command"]
    return [shutil.which("bash") or "/bin/sh", "-lc"]


def _host_command(command: str) -> str:
    if os.name == "nt":
        # Windows PowerShell 5.1 writes UTF-16 for `>` / `>>` and ANSI for Set-Content;
        # make files written by commands plain UTF-8 so file tools (and people) can read them.
        utf8_writes = "".join(
            f"$PSDefaultParameterValues['{cmd}:Encoding']='utf8'; " for cmd in ("Out-File", "Set-Content", "Add-Content")
        )
        return "[Console]::OutputEncoding=[Text.Encoding]::UTF8; $ProgressPreference='SilentlyContinue'; " + utf8_writes + command
    return command


def _host_launch(command: str, cwd: Path, log: Path, err: Path) -> subprocess.Popen:
    """Start on this PC with no keyboard input, output to files, in its own process group."""
    with open(log, "w", encoding="utf-8", errors="replace") as out_handle, \
            open(err, "w", encoding="utf-8", errors="replace") as err_handle:
        return subprocess.Popen(
            [*_host_shell(), _host_command(command)], cwd=str(cwd), stdin=subprocess.DEVNULL,
            stdout=out_handle, stderr=err_handle, env=child_env(_QUIET_ENV),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            start_new_session=os.name != "nt",
        )


def _read(path: Optional[Path], chars: int = 0) -> str:
    try:
        data = path.read_text(encoding="utf-8", errors="replace") if path else ""
    except OSError:
        return ""
    return data[-chars:] if chars else data


# ── background processes ────────────────────────────────────────────────
@dataclass
class BackgroundProcess:
    id: str
    command: str
    cwd: str
    mode: str
    log_path: Path
    err_path: Optional[Path] = None
    started_at: float = field(default_factory=time.time)
    popen: Optional[subprocess.Popen] = None
    container_pid: str = ""
    # Sandbox internet was switched on for it ("ask" policy): off again once it ends.
    network: bool = False


_PROCESSES: dict[str, BackgroundProcess] = {}
_PROC_DIR = Path(DATA_DIR) / "lean" / "processes"


def _process_alive(proc: BackgroundProcess) -> bool:
    if proc.mode == "host":
        return proc.popen is not None and proc.popen.poll() is None
    code, out = _SANDBOX.exec(f"kill -0 {proc.container_pid} 2>/dev/null && echo alive", "/work", 10)
    return "alive" in out


def _exit_code(proc: BackgroundProcess) -> Optional[int]:
    if proc.mode == "host":
        return proc.popen.poll() if proc.popen is not None else None
    _, out = _SANDBOX.exec(f"cat /tmp/es-proc-{proc.id}.exit 2>/dev/null", "/work", 10)
    try:
        return int(out.strip().splitlines()[-1])
    except (IndexError, ValueError):
        return None


def _tail(proc: BackgroundProcess, chars: int = 6000) -> str:
    """Recent stdout, then recent stderr under its own label."""
    if proc.mode == "host":
        out, err = _read(proc.log_path, chars), _read(proc.err_path, chars // 3)
    else:
        _, out = _SANDBOX.exec(f"tail -c {chars} /tmp/es-proc-{proc.id}.log 2>/dev/null", "/work", 15)
        _, err = _SANDBOX.exec(f"tail -c {chars // 3} /tmp/es-proc-{proc.id}.err 2>/dev/null", "/work", 15)
    out, err = out.strip(), err.strip()
    return out + (f"\n[stderr]\n{err}" if err else "")


def _running() -> list[BackgroundProcess]:
    return [proc for proc in _PROCESSES.values() if _process_alive(proc)]


def _release_network(proc: BackgroundProcess) -> None:
    """Close the sandbox's internet again once the last command that needed it ended."""
    if not proc.network:
        return
    proc.network = False
    if not any(p.network and _process_alive(p) for p in _PROCESSES.values()):
        _SANDBOX.set_network(False)


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
            try:
                os.killpg(proc.popen.pid, 15)  # the whole group: a dev server's children too
            except OSError:
                proc.popen.terminate()
        try:
            proc.popen.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.popen.kill()
    elif proc.mode == "docker" and proc.container_pid:
        _SANDBOX.exec(f"kill -TERM -- -{proc.container_pid} 2>/dev/null || kill -TERM {proc.container_pid}", "/work", 15)
    _release_network(proc)


@dataclass
class Launch:
    finished: bool
    code: int = 0
    out: str = ""
    err: str = ""
    proc: Optional[BackgroundProcess] = None


# ── tool surface ────────────────────────────────────────────────────────
class Terminal:
    """Bound to one session's project folder."""

    def __init__(self, project_root: str = "") -> None:
        self.project_root = project_root
        # "docker" (sandbox) or "host"; "auto" resolves to one of them here.
        self.mode = resolved_mode()

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
            shell = "PowerShell 7" if shutil.which("pwsh") else "Windows PowerShell" if os.name == "nt" else "bash"
            return (f"terminal runs {shell} directly on this PC (pipes, $variables and ; chaining work; "
                    "use `;` not `&&` in Windows PowerShell).")
        plan = mount_plan(self.project_root)
        mounts = ", ".join(f"{root} -> {target}" for root, target in plan) or "none"
        policy = network_policy()
        network = {
            "on": "internet on",
            "off": "no internet",
            "ask": "offline by default; set network=true for commands that need the internet (npm/pip install, git clone)",
        }[policy]
        return ("terminal runs bash in a Linux sandbox container (Node 22, Python 3, git; "
                f"{network}). Your folders are mounted: {mounts}. "
                "Windows paths in commands are translated automatically. Only if a task truly needs this PC "
                "(Windows apps, installed tools), set where=\"host\"; that asks the user first.")

    def _checkpoint_redirects(self, command: str, cwd: Path) -> None:
        """Back up files the command overwrites via redirection, so undo covers them like file_write."""
        targets = redirect_targets(command)
        if not targets:
            return
        from agent.checkpoints import create_checkpoint

        for raw in targets[:6]:
            path = Path(raw).expanduser()
            path = path if path.is_absolute() else cwd / path
            if path.is_file() and any(_is_within(path.resolve(), root) for root in workspace_roots(self.project_root) or [cwd]):
                create_checkpoint(str(path), reason="before_terminal_write")

    def _launch(self, command: str, cwd: Path, wait: int, where: str, *, network: bool = False) -> Launch:
        proc_id = uuid.uuid4().hex[:8]
        if where == "host":
            _PROC_DIR.mkdir(parents=True, exist_ok=True)
            log, err = _PROC_DIR / f"{proc_id}.log", _PROC_DIR / f"{proc_id}.err"
            popen = _host_launch(command, cwd, log, err)
            try:
                code = popen.wait(timeout=wait)
            except subprocess.TimeoutExpired:
                proc = BackgroundProcess(id=proc_id, command=command, cwd=str(cwd), mode="host", log_path=log,
                                         err_path=err, popen=popen)
                return Launch(False, proc=proc)
            out, error = _read(log), _read(err)
            for path in (log, err):
                try:
                    path.unlink()
                except OSError:
                    pass
            return Launch(True, code, out, error)
        plan = mount_plan(self.project_root)
        workdir = to_container_path(cwd, plan) or "/work"
        finished, code, out, error = _SANDBOX.launch(translate_windows_paths(command, plan), workdir, wait, proc_id)
        if finished:
            return Launch(True, code, out, error)
        proc = BackgroundProcess(id=proc_id, command=command, cwd=str(cwd), mode="docker",
                                 log_path=Path(f"/tmp/es-proc-{proc_id}.log"), container_pid=str(code), network=network)
        return Launch(False, proc=proc)

    def _start(self, command: str, args: dict[str, Any], wait: int) -> tuple[Optional[Launch], str, Path, str]:
        """Shared checks and launch. Returns (launch, error, cwd, where)."""
        if _NEVER.search(command):
            return None, "Refused: that command can damage the system (disk, boot, or OS-level destruction) and is never run.", Path("."), ""
        problem = interactive_problem(command)
        if problem:
            return None, f"Error: not run, because {problem}.", Path("."), ""
        cwd, error = self._cwd(args.get("cwd"))
        if error:
            return None, error, Path("."), ""
        where = "host" if self.mode == "host" or str(args.get("where") or "").lower() == "host" else "docker"
        want_net = False
        if where == "docker":
            ok, detail = _SANDBOX.ensure_container(mount_plan(self.project_root))
            if not ok:
                return None, (f"Error: the Docker sandbox is not available: {detail} "
                              "Tell the user to start Docker Desktop, or switch Terminal to Auto or This PC in Settings > Terminal."), cwd, where
            policy = network_policy()
            want_net = bool(args.get("network"))
            if want_net and policy == "off":
                return None, "Error: internet access is turned off for the sandbox in Settings > Terminal.", cwd, where
            want_net = want_net and policy == "ask"
            if want_net:
                _SANDBOX.set_network(True)
        self._checkpoint_redirects(command, cwd)
        launch = self._launch(command, cwd, wait, where, network=want_net)
        if want_net and launch.finished:
            _SANDBOX.set_network(False)
        return launch, "", cwd, where

    def _background(self, launch: Launch, wait: int, label: str, cwd: Path, *, server: bool) -> str:
        """A command still running after its wait: keep it as a background process (or stop it if too many)."""
        proc = launch.proc
        assert proc is not None
        running = _running()
        if len(running) >= MAX_BACKGROUND:
            so_far = _tail(proc, 3000)
            _stop(proc)
            others = ", ".join(f"{p.id} ({p.command[:40]})" for p in running)
            return (f"[stopped after {wait}s · {label} · {cwd}]\n{_clip(so_far, 3000) or '(no output)'}\n"
                    f"It was stopped because {len(running)} background processes are already running: {others}. "
                    "Stop ones you no longer need with process_stop, then try again.")
        _PROCESSES[proc.id] = proc
        so_far = _clip(_tail(proc, 4000), 4000) or "(no output yet)"
        headline = ("started in the background (it keeps running)" if server
                    else f"still running after {wait}s, so it keeps going in the background")
        return (f"[{headline} · process {proc.id} · {label} · {cwd}]\nOutput so far:\n{so_far}\n"
                f"Check on it with process_output (process_id={proc.id}); stop it with process_stop. "
                "Don't start it again.")

    def run(self, args: dict[str, Any]) -> str:
        command = str(args.get("command") or args.get("cmd") or "").strip()
        if not command:
            return "Error: command is empty."
        if str(args.get("background") or "").lower() in {"true", "1", "yes"}:
            return self.process_start({**args, "command": command})
        server = is_long_running(command)
        # A server never finishes: look at its first output, then let it run.
        wait = SERVER_WAIT if server and not args.get("timeout") else _timeout(args.get("timeout"), 120)
        started = time.perf_counter()
        launch, error, cwd, where = self._start(command, args, wait)
        if launch is None:
            return error
        label = "this PC" if where == "host" else "sandbox"
        if not launch.finished:
            return self._background(launch, wait, label, cwd, server=server)
        elapsed = time.perf_counter() - started
        status = "ok" if launch.code == 0 else f"exit code {launch.code}"
        body = _clip(launch.out.strip()) or ("(no output)" if not launch.err.strip() else "")
        if launch.err.strip():
            body = (body + "\n" if body else "") + "[stderr]\n" + _clip(launch.err.strip(), 6000)
        hint = failure_hint(launch.code, launch.out + "\n" + launch.err, where) if launch.code != 0 else ""
        return f"[{status} · {elapsed:.1f}s · {label} · {cwd}]\n{body}" + (f"\n{hint}" if hint else "")

    def process_start(self, args: dict[str, Any]) -> str:
        command = str(args.get("command") or "").strip()
        if not command:
            return "Error: command is empty."
        try:
            wait = max(1, min(int(args.get("wait_seconds") or 3), 60))
        except (TypeError, ValueError):
            wait = 3
        launch, error, cwd, where = self._start(command, args, wait)
        if launch is None:
            return error
        label = "this PC" if where == "host" else "sandbox"
        if not launch.finished:
            return self._background(launch, wait, label, cwd, server=True)
        output = (launch.out.strip() + (f"\n[stderr]\n{launch.err.strip()}" if launch.err.strip() else "")).strip()
        return (f"[exited with code {launch.code} within {wait}s · {label} · {cwd}]\n{_clip(output, 6000) or '(no output)'}\n"
                "It already finished, so there is nothing running to check on.")

    def process_output(self, args: dict[str, Any]) -> str:
        proc = _PROCESSES.get(str(args.get("process_id") or args.get("id") or ""))
        if proc is None:
            running = ", ".join(f"{p.id}: {p.command[:60]}" for p in _PROCESSES.values()) or "none"
            return f"Error: no process with that process_id. Known processes: {running}"
        if _process_alive(proc):
            state = f"still running ({int(time.time() - proc.started_at)}s so far)"
        else:
            code = _exit_code(proc)
            state = "finished" + (" (ok)" if code == 0 else f" with exit code {code}" if code is not None else "")
            _release_network(proc)
        return f"Process {proc.id} is {state}: {proc.command}\nRecent output:\n{_clip(_tail(proc), 6000) or '(no output)'}"

    def process_stop(self, args: dict[str, Any]) -> str:
        proc = _PROCESSES.get(str(args.get("process_id") or args.get("id") or ""))
        if proc is None:
            return "Error: no process with that process_id."
        _stop(proc)
        return f"Stopped process {proc.id}."

    def tools(self) -> list[NativeTool]:
        return [
            NativeTool(
                name="terminal",
                description=(
                    "Run a program: builds, tests, git, package managers, scripts, CLIs. "
                    f"{self.describe()} Default folder is the project folder. For files, use the file tools instead: "
                    "file_read to read, file_find / file_search to find, file_write / file_edit to change (scoped to "
                    "the project and undoable). Commands get no keyboard input, so use non-interactive forms "
                    "(git commit -m, npm init -y). Waits up to timeout seconds; a command still running then keeps "
                    "going in the background and you get a process_id for process_output / process_stop. Servers and "
                    "watchers start in the background by themselves (or set background=true). Check the exit code "
                    "and output before saying it worked."
                ),
                parameters={"type": "object", "properties": {
                    "command": {"type": "string"},
                    "cwd": {"type": "string", "description": "Folder to run in (default: project folder)."},
                    "timeout": {"type": "integer", "description": "Seconds to wait before it moves to the background, up to 900 (default 120)."},
                    "background": {"type": "boolean", "description": "Start it in the background right away and return its first output."},
                    **({
                        "network": {"type": "boolean", "description": "True if the command needs the internet (installs, git clone)."},
                        "where": {"type": "string", "enum": ["sandbox", "host"], "description": "Leave as sandbox unless this PC is truly needed."},
                    } if self.mode == "docker" else {}),
                }, "required": ["command"]},
                func=self.run,
            ),
            NativeTool(
                name="process_output",
                description="Whether a background process is still running (or its exit code), and its recent output.",
                parameters={"type": "object", "properties": {"process_id": {"type": "string", "description": "From terminal."}},
                            "required": ["process_id"]},
                func=self.process_output,
                parallel_safe=True,
            ),
            NativeTool(
                name="process_stop",
                description="Stop a background process (and anything it started).",
                parameters={"type": "object", "properties": {"process_id": {"type": "string", "description": "From terminal."}},
                            "required": ["process_id"]},
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
