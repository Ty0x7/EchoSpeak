"""Owned Git worktrees and bounded, conflict-checked last-turn rollback.

Never fetch, copy dirty/untracked owner files, run Git hooks, merge, commit or
reset the source checkout. Worktrees are isolation between cooperative agents,
not a security boundary against an executable with the owner's OS privileges.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from pathlib import Path, PurePosixPath
import subprocess
import threading
import tempfile
import uuid

from agent.atomic_replace import replace as atomic_replace
from agent.child_env import child_env
from agent.code_process import workspace_env

MAX_SNAPSHOT = 24 * 1024 * 1024
MAX_FILES = 4000


def _filter_overrides(root):
    # Even `git status` may invoke clean/process filters. Query key NAMES only
    # using Git's non-executing config command, then disable every configured
    # filter for this invocation without modifying the owner's configuration.
    try:
        result = subprocess.run(["git", "--no-optional-locks", "-C", str(root), "config", "--null", "--name-only",
                                 "--get-regexp", r"^filter\..*\.(clean|smudge|process|required)$"],
                                capture_output=True, timeout=10, env=workspace_env(child_env()),
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except FileNotFoundError:
        raise ValueError("Git was not found. Install Git and reopen EchoSpeak before starting an isolated runtime.") from None
    except subprocess.TimeoutExpired:
        raise ValueError("Git configuration inspection timed out. Check repository access and retry.") from None
    except OSError:
        raise ValueError("Git could not start. Check its installation and folder permissions, then retry.") from None
    if result.returncode not in {0, 1} or len(result.stdout) > 64_000:
        raise ValueError("Cannot safely inspect Git filter configuration")
    args = []
    for raw in result.stdout.split(b"\0"):
        if not raw:
            continue
        key = raw.decode("utf-8")
        if not re.fullmatch(r"filter\.[^\x00-\x20=]+\.(clean|smudge|process|required)", key):
            raise ValueError("Unsupported Git filter name")
        args += ["-c", key + ("=false" if key.endswith(".required") else "=")]
    return args


def git(root: Path, *args: str, limit=2 * 1024 * 1024, input_data: bytes | None = None) -> bytes:
    # Explicit configuration also disables repository-provided external diff,
    # fsmonitor and hooks, including post-checkout during worktree creation.
    with tempfile.TemporaryFile() as errors, subprocess.Popen(["git", "--no-optional-locks", "-c", "core.hooksPath=", "-c", "core.fsmonitor=false",
                           "-c", "transfer.bundleURI=false", *_filter_overrides(root), "-C", str(root), *args],
                          stdin=subprocess.PIPE if input_data is not None else subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=errors, env=workspace_env(child_env()),
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)) as process:
        expired = threading.Event()
        def timeout():
            expired.set()
            process.kill()
        timer = threading.Timer(30, timeout)
        timer.start()
        def write_input():
            try:
                process.stdin.write(input_data)
                process.stdin.close()
            except OSError:
                pass
        writer = None
        if input_data is not None:
            writer = threading.Thread(target=write_input, daemon=True)
            writer.start()
        try:
            output = process.stdout.read(limit + 1)
        finally:
            timer.cancel()
        if len(output) > limit:
            process.kill()
            raise ValueError("Git output exceeds the inspection limit")
        try:
            code = process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()
            raise ValueError("Git operation timed out") from None
        if expired.is_set():
            raise ValueError("Git operation timed out. Check repository access and retry.")
        if code:
            errors.seek(0)
            raw_detail = errors.read(8192).decode('utf-8', 'replace')
            detail = raw_detail.lower()
            if 'dubious ownership' in detail:
                raise ValueError('Git refused this repository because of its ownership. Inspect ownership in your terminal; Echo does not change safe.directory automatically.')
            if 'not a git repository' in detail:
                raise ValueError('This folder is not a Git repository. Select a repository root, or initialize Git and make an initial commit before starting an isolated runtime.')
            if any(v in detail for v in ('unknown revision','needed a single revision','not a valid object name','bad revision')):
                raise ValueError('This repository has no usable committed HEAD. Make an initial commit in your terminal, then retry. Existing files are not committed automatically.')
            if 'permission denied' in detail or 'cannot lock' in detail:
                raise ValueError('Git could not access or lock this repository. Check folder permissions and close other Git operations before retrying.')
            from agent.lean.policy import redact_secrets
            reason = redact_secrets(raw_detail).strip().replace(str(Path.home()), '~')[:1200]
            raise ValueError(f'Git {args[0] if args else "inspection"} failed (exit {code}): {reason or "Git returned no diagnostic."}')
        if writer:
            writer.join(timeout=2)
        return output


_JSON_LOCKS: dict[str, threading.Lock] = {}
_JSON_LOCKS_GUARD = threading.Lock()


def atomic_json(path: Path, value):
    """Replace a JSON file whole, one writer per path at a time (see agent/atomic_replace.py)."""
    with _JSON_LOCKS_GUARD:
        lock = _JSON_LOCKS.setdefault(str(path.resolve()), threading.Lock())
    serialized = json.dumps(value, ensure_ascii=False)
    with lock:
        temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
        temporary.write_text(serialized, encoding="utf-8")
        try:
            atomic_replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def preflight(root: Path):
    root = Path(root).resolve()
    source = Path(git(root, "rev-parse", "--show-toplevel").decode().strip()).resolve()
    if root != source:
        raise ValueError("Attach the repository root to start an isolated coding session")
    return git(root, 'rev-parse', '--verify', 'HEAD^{commit}').decode().strip()


def create(root: Path, directory: Path, *, acknowledge_dirty: bool):
    head = preflight(root)
    source = root
    dirty = bool(git(root, "status", "--porcelain", "--untracked-files=normal"))
    if dirty and not acknowledge_dirty:
        raise ValueError("The source checkout has local changes. Acknowledge starting from committed HEAD; local changes will stay in the source checkout.")
    entries = []
    for entry in git(root, "ls-tree", "-rz", "--full-tree", head).split(b"\0"):
        if not entry:
            continue
        metadata, name = entry.split(b"\t", 1)
        mode, kind, object_id = metadata.split()
        if kind != b"blob" or mode not in {b"100644", b"100755"}:
            raise ValueError("Isolated sessions currently require regular files, without symlinks or submodules")
        entries.append((name.decode("utf-8"), object_id, mode))
    if len(entries) > MAX_FILES:
        raise ValueError("Repository exceeds the 4,000-file checkpoint limit")
    # Read raw committed blobs. Checkout filters (including Git LFS smudge),
    # hooks and project setup scripts must never execute during provisioning.
    data = git(root, "cat-file", "--batch", limit=MAX_SNAPSHOT + 1024 * 1024,
               input_data=b"".join(object_id + b"\n" for _, object_id, _ in entries))
    tree = directory / "tree"
    offset, total, names = 0, 0, set()
    files = []
    for name, object_id, mode in entries:
        path(tree, name)  # preflight every path before creating Git metadata
        if name.casefold() in names:
            raise ValueError("Repository has case-colliding paths unsupported by Windows worktrees")
        names.add(name.casefold())
        end = data.index(b"\n", offset)
        returned_id, kind, length = data[offset:end].split()
        size = int(length)
        total += size
        if returned_id != object_id or kind != b"blob" or total > MAX_SNAPSHOT:
            raise ValueError("Repository exceeds checkpoint limits or returned an invalid blob")
        content = data[end + 1:end + 1 + size]
        if len(content) != size or data[end + 1 + size:end + 2 + size] != b"\n":
            raise ValueError("Truncated Git blob")
        files.append((name, content, mode))
        offset = end + size + 2
    if offset != len(data):
        raise ValueError("Unexpected Git blob output")
    directory.mkdir(parents=True, exist_ok=False)
    git(root, "worktree", "add", "--no-checkout", "--detach", str(tree), head)
    for name, content, mode in files:
        target = path(tree, name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        if os.name != "nt":
            target.chmod(0o755 if mode == b"100755" else 0o644)
    git(tree, "read-tree", head)
    return {"source_root": str(source), "worktree": str(tree.resolve()), "base_head": head,
            "source_dirty": dirty, "branch": "detached"}


def path(root: Path, name: str) -> Path:
    parts = PurePosixPath(name).parts
    if (not parts or ".." in parts or ":" in name or "\\" in name or name.startswith("/")
            or any(ord(char) < 32 for char in name)):
        raise ValueError("Unsafe worktree path")
    for part in parts:
        if (part.lower() in {".git", "git~1"} or part.endswith((".", " "))
                or re.match(r"^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)", part, re.I)):
            raise ValueError("Unsafe or reserved worktree path")
    candidate = root
    for part in parts:
        candidate /= part
        if candidate.is_symlink() or (hasattr(candidate, "is_junction") and candidate.is_junction()):
            raise ValueError("Linked files cannot be checkpointed or restored")
    if not candidate.resolve().is_relative_to(root.resolve()):
        raise ValueError("Worktree path escaped its root")
    return candidate


def snapshot(root: Path) -> dict:
    names = git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard").decode("utf-8").split("\0")
    files, size = {}, 0
    for name in sorted(set(filter(None, names))):
        file = path(root, name)
        if not file.exists():
            continue
        if not file.is_file():
            raise ValueError("Submodules and special files cannot be checkpointed")
        size += file.stat().st_size
        if size > MAX_SNAPSHOT or len(files) >= MAX_FILES:
            raise ValueError("Worktree exceeds safe rollback limits (4,000 files / 24 MiB). No turn was started.")
        content = file.read_bytes()
        files[name] = {"data": base64.b64encode(content).decode(), "mode": file.stat().st_mode & 0o777}
    index = index_path(root)
    if index.stat().st_size > 4 * 1024 * 1024:
        raise ValueError("Git index exceeds rollback limits")
    return {"head": git(root, "rev-parse", "HEAD").decode().strip(), "files": files,
            "index": base64.b64encode(index.read_bytes()).decode()}


def index_path(root: Path) -> Path:
    # A linked worktree index must live in its own Git administration directory,
    # never the source checkout's index. Reject altered Git binding/overrides.
    admin = Path((root / ".git").read_text(encoding="utf-8").strip().removeprefix("gitdir: ")).resolve()
    index = Path(git(root, "rev-parse", "--git-path", "index").decode().strip()).resolve()
    if (admin.parent.name != "worktrees" or index != admin / "index" or index.is_symlink()
            or Path((admin / "gitdir").read_text(encoding="utf-8").strip()).resolve() != root / ".git"):
        raise ValueError("Worktree index binding changed")
    return index


def fingerprint(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def inspect(root: Path):
    from agent.lean.policy import redact_secrets
    # --no-textconv and --no-ext-diff prevent executing project-configured viewers.
    diff = git(root, "diff", "--no-ext-diff", "--no-textconv", "HEAD", "--", limit=512_000).decode("utf-8", "replace")
    status = git(root, "status", "--short", "--untracked-files=normal").decode("utf-8", "replace")
    return {"status": redact_secrets(status), "diff": redact_secrets(diff),
            "note": "Diff includes tracked changes. Untracked names appear in status. Ignored files are outside rollback coverage."}


def rollback(root: Path, checkpoint: Path):
    record = json.loads(checkpoint.read_text(encoding="utf-8"))
    current = snapshot(root)
    if fingerprint(current) != record["after"]:
        raise ValueError("Worktree changed after the turn. Rollback refused to preserve newer edits.")
    before = record["before"]
    if current["head"] != before["head"]:
        raise ValueError("The agent changed HEAD. Inspect the worktree manually; automatic rollback is unavailable.")
    # Retain an independent recovery copy BEFORE touching any worktree file.
    atomic_json(checkpoint.with_name("rollback-backup-" + uuid.uuid4().hex + ".json"), current)
    for name in set(current["files"]) | set(before["files"]):
        if current["files"].get(name) == before["files"].get(name):
            continue
        target = path(root, name)
        expected = current["files"].get(name)
        actual = {"data": base64.b64encode(target.read_bytes()).decode(), "mode": target.stat().st_mode & 0o777} if target.is_file() else None
        if actual != expected:
            raise ValueError("A file changed during rollback. Newer edits were preserved; use the recovery copy to inspect partial restoration.")
        original = before["files"].get(name)
        if original is None:
            target.unlink(missing_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(target.name + ".echo-restore-" + uuid.uuid4().hex)
            temporary.write_bytes(base64.b64decode(original["data"]))
            temporary.chmod(original["mode"])
            atomic_replace(temporary, target)
    index = index_path(root)
    if base64.b64encode(index.read_bytes()).decode() != current["index"]:
        raise ValueError("Git index changed during rollback; recovery copy retained")
    temporary = index.with_name("index.echo-restore-" + uuid.uuid4().hex)
    temporary.write_bytes(base64.b64decode(before["index"]))
    temporary.replace(index)
    checkpoint.unlink()
