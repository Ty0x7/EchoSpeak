"""Linux sandbox for terminal commands: bubblewrap (bwrap). No daemon, no root, runs as you.

Docker was the sandbox on Windows. On Linux it needs the ``docker`` group (root-equivalent:
anyone in it can mount the whole disk into a container) and its root container writes files
into the project as root. bwrap uses unprivileged user namespaces instead, the way Claude
Code and Codex sandbox commands on Linux, and the project keeps its real paths.

What a sandboxed command gets (each point checked on the owner's machine; see
docs/research/provider-and-isolation.md):

- Files: the whole system is read-only. Only the session's workspace folders, a private
  /tmp and the package caches (~/.cache, ~/.npm) are writable.
- Credentials: credential folders and files in your home (ssh, gpg, cloud CLIs, keyrings,
  browser profiles), EchoSpeak's own data folder and your session's runtime folder
  (/run/user/<uid>: D-Bus, ssh-agent, keyring and Wayland sockets) are hidden.
- Network: none (its own network namespace) unless the command asked and was allowed.
- Processes: its own PID namespace (it can't see or signal your programs), a new session
  (no typing into your terminal), no new privileges (sudo and setuid don't work) and no
  capabilities.

What it doesn't do: other files in your home stay readable (as in Claude Code's and Codex's
defaults), it's no defense against kernel bugs, and commands you approve to run outside it
aren't confined. When the sandbox can't start, commands are refused or ask; they never
silently run unconfined.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterable, Mapping, Optional

# Home-relative folders that hold credentials or another program's private state.
HIDDEN_DIRS = (
    ".ssh", ".gnupg", ".aws", ".azure", ".kube", ".docker", ".config/gcloud", ".config/gh", ".config/hub",
    ".local/share/keyrings", ".password-store", ".mozilla", ".config/chromium", ".config/google-chrome",
    ".config/BraveSoftware", ".config/Code/User/globalStorage", ".claude", ".codex", ".config/echospeak",
    ".local/share/ai.echospeak.desktop", ".config/rclone", ".terraform.d", ".vault-token.d",
)
# Home-relative credential files (replaced by an empty file).
HIDDEN_FILES = (".netrc", ".git-credentials", ".pgpass", ".pypirc", ".npmrc", ".vault-token", ".claude.json")
# Package caches: writable so installs and builds work. Nothing here runs by itself.
CACHE_DIRS = (".cache", ".npm")
# Variables that point a command at a socket outside the sandbox.
_SOCKET_VARS = ("DBUS_SESSION_BUS_ADDRESS", "SSH_AUTH_SOCK", "GPG_AGENT_INFO", "WAYLAND_DISPLAY", "DISPLAY",
                "XDG_RUNTIME_DIR", "DOCKER_HOST")

_CHECK: dict[str, object] = {"at": 0.0, "ok": False, "detail": "not checked"}


def bwrap_path() -> Optional[str]:
    return shutil.which("bwrap") if os.name == "posix" else None


def available(max_age: float = 300.0) -> tuple[bool, str]:
    """Can bwrap start a sandbox here? Distros can turn off unprivileged user namespaces
    (Ubuntu's AppArmor rule, a sysctl), so it is tried, not assumed. Cached."""
    now = time.monotonic()
    if now - float(_CHECK["at"]) <= max_age and _CHECK["at"]:
        return bool(_CHECK["ok"]), str(_CHECK["detail"])
    exe = bwrap_path()
    if not exe:
        ok, detail = False, "bubblewrap (bwrap) isn't installed"
    else:
        try:
            proc = subprocess.run([exe, "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc",
                                   "--unshare-pid", "--unshare-net", "--die-with-parent", "--", "true"],
                                  capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL)
            ok = proc.returncode == 0
            detail = "ready" if ok else f"bwrap can't create a sandbox here: {(proc.stderr or '').strip()[-300:]}"
        except (OSError, subprocess.TimeoutExpired) as exc:
            ok, detail = False, f"bwrap failed to start: {exc}"
    _CHECK.update(at=now, ok=ok, detail=detail)
    return ok, detail


def _existing(paths: Iterable[Path]) -> list[Path]:
    out: list[Path] = []
    for path in paths:
        try:
            if path.exists() and not path.is_symlink():
                out.append(path)
        except OSError:
            continue
    return out


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def wrap(argv: list[str], *, cwd: Path, writable: Iterable[Path], network: bool = False,
         hidden: Iterable[Path] = (), home: Optional[Path] = None) -> list[str]:
    """The bwrap command line that runs ``argv`` confined as described in the module docstring.

    Order matters, because bwrap applies mounts in order:
    1. the read-only system;
    2. the writable folders;
    3. the hidden ones, so a workspace set to your home folder still can't read ~/.ssh;
    4. workspaces that lie inside a hidden folder are mounted again, so they stay usable
       (EchoSpeak's own data folder holds the projects in tests).
    """
    exe = bwrap_path() or "bwrap"
    home = (home or Path.home()).resolve()
    roots = _existing(Path(p).resolve() for p in writable if str(p))
    args = [exe, "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp"]
    for path in _existing(home / rel for rel in CACHE_DIRS):
        args += ["--bind", str(path), str(path)]
    for root in roots:
        args += ["--bind", str(root), str(root)]
    hide_dirs = [home / rel for rel in HIDDEN_DIRS] + [Path(p).resolve() for p in hidden if str(p)]
    hide_dirs.append(Path(f"/run/user/{os.getuid()}"))
    masked = [path for path in _existing(hide_dirs) if path.is_dir()]
    for path in masked:
        args += ["--tmpfs", str(path)]
    for path in _existing(home / rel for rel in HIDDEN_FILES):
        if path.is_file():
            args += ["--ro-bind", "/dev/null", str(path)]
    for root in roots:
        if any(_inside(root, path) for path in masked):
            args += ["--bind", str(root), str(root)]
    args += ["--unshare-pid", "--unshare-ipc", "--unshare-uts", "--unshare-cgroup-try"]
    if not network:
        args.append("--unshare-net")
    args += ["--new-session", "--die-with-parent", "--cap-drop", "ALL", "--chdir", str(cwd), "--", *argv]
    return args


def sandbox_env(env: Mapping[str, str]) -> dict[str, str]:
    """The child environment without pointers to sockets the sandbox hides."""
    return {key: value for key, value in env.items() if key not in _SOCKET_VARS}

def python_fixture(root: Path, python_args: list[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
    """Run an evaluation fixture with a disposable filesystem and no network."""
    supported, detail = available()
    if not supported:
        raise RuntimeError(f"Independent fixture execution unavailable: {detail}")

    # The backend venv and its base interpreter can live outside the system library directories.
    # Bind those exact trusted directories without mounting their parent directories.
    prefix = Path(sys.prefix).resolve()
    executable = prefix / "bin" / Path(sys.executable).name
    base_prefix = Path(sys.base_prefix).resolve()
    interpreters = {prefix, base_prefix}
    base_alias = Path(sys._base_executable).parent.parent
    if Path("/") in interpreters:
        raise RuntimeError("Independent fixture execution requires an isolated Python installation")
    command = [bwrap_path() or "bwrap", "--unshare-all", "--new-session", "--die-with-parent",
               "--cap-drop", "ALL", "--clearenv", "--dev", "/dev", "--proc", "/proc",
               "--tmpfs", "/tmp", "--ro-bind", "/usr", "/usr"]
    for path in ("/bin", "/lib", "/lib64", "/etc/ld.so.cache"):
        command.extend(("--ro-bind-try", path, path))
    for directory in sorted(interpreters):
        if not directory.is_relative_to("/usr"):
            command.extend(("--ro-bind", str(directory), str(directory)))
    if base_alias != base_prefix and base_alias.resolve() == base_prefix:
        command.extend(("--ro-bind", str(base_prefix), str(base_alias)))
    command.extend(("--bind", str(root), "/fixture", "--chdir", "/fixture",
                    "--setenv", "HOME", "/fixture", "--setenv", "PATH", "/usr/bin:/bin",
                    "--setenv", "PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1",
                    "--", str(executable), *python_args))
    proc = subprocess.run(command, env={}, stdin=subprocess.DEVNULL, capture_output=True,
                          text=True, errors="replace", timeout=timeout)
    if proc.returncode and proc.stderr.startswith("bwrap:"):
        raise RuntimeError(f"Independent fixture sandbox failed: {proc.stderr.splitlines()[0]}")
    return proc
