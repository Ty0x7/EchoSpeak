"""Owner-facing IDE surfaces bound to an Echo project or an owned agent worktree.

No model calls or implicit setup commands. Execution runs in owned workers and
joins the existing runtime supervisor's stop/privacy lifecycle.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import sys
import time
from urllib.parse import quote
import uuid

from agent import code_worktree as git_ops, project_workspace, privacy
from agent.child_env import child_env
from agent.code_process import RpcProcess
from agent.lean.policy import redact_payload, redact_secrets


class OwnedJob:
    def __init__(self, workbench, context):
        self.workbench, self.context = workbench, context
        self.id = uuid.uuid4().hex
        self.rpc = None
        self.seq = 0
        self.output = []
        self.size = 0
        self.state = "starting"
        self.lease = None
        self.group = ""
        self.is_terminal = False
        self.pending_text = ""
        self.changed = asyncio.Event()

    async def event(self, method, params, request=False):
        if request or method not in {"terminal/output", "terminal/exited"}:
            raise ValueError("Unsupported workbench worker request")
        if method == "terminal/output":
            from agent.lean.policy import _secret_values
            raw = self.pending_text + str(params.get("text", ""))
            secrets = self.workbench.secrets(self.context)
            text = redact_secrets(raw, secrets)
            keep = 0
            for secret in secrets:
                for size in range(min(len(secret) - 1, len(text)), 0, -1):
                    if text.endswith(secret[:size]):
                        keep = max(keep, size)
                        break
            self.pending_text = text[-keep:] if keep else ""
            text = text[:-keep] if keep else text
            self.seq += 1
            self.output.append({"seq": self.seq, "text": text})
            self.size += len(text)
            while self.size > 500_000 or len(self.output) > 1000:
                self.size -= len(self.output.pop(0)["text"])
            self.changed.set()
        else:
            self.state = "exited"
            await self.stop()
        return {}

    def start(self):
        self.workbench.supervisor.admit()
        self.workbench.validate(self.context)
        argv = [sys.executable, "--workbench-worker"] if getattr(sys, "frozen", False) else [sys.executable, "-P", "-m", "agent.workbench_worker"]
        env = child_env()
        if not getattr(sys, "frozen", False):
            env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
        self.rpc = RpcProcess(argv, self.context["root"], env, self.event)
        self.workbench.supervisor.owned_tools.add(self)

    def public(self, cursor=0):
        return {"id": self.id, "state": self.state if self.rpc and not self.rpc.closed else "stopped",
                "project_id": self.context["project_id"], "external_session_id": self.context["external_session_id"],
                "root": self.context["root"], "cursor": self.seq,
                "gap": bool(self.output and cursor < self.output[0]["seq"] - 1),
                "output": [row for row in self.output if row["seq"] > cursor]}

    async def stop(self, reason="Stopped by owner"):
        if self.rpc:
            await self.rpc.close()
        self.state = "stopped"
        self.changed.set()
        self.workbench.supervisor.owned_tools.discard(self)
        self.workbench.release(self)


class Workbench:
    def __init__(self, supervisor):
        self.supervisor = supervisor
        self.terminals = {}
        self.reviews = {}
        self.groups = {}
        self.directory = supervisor.directory.parent / "workbench"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.script_path = self.directory / "scripts.json"

    def secrets(self, context):
        from agent.lean.policy import _secret_values
        session = self.supervisor.sessions.get(context["external_session_id"])
        return _secret_values() + (session.redaction_values if session else [])

    @staticmethod
    def bounded_read(file, maximum):
        with file.open("rb") as stream:
            data = stream.read(maximum + 1)
        if len(data) > maximum:
            raise ValueError("File exceeds this surface's preview limit")
        return data

    def context(self, project_id, external_session_id=""):
        item = project_workspace.project(project_id)
        source = project_workspace.root_for(item)
        root, runtime = source, "echo"
        if external_session_id:
            session = self.supervisor.get(external_session_id)
            if session.row["project_id"] != project_id:
                raise PermissionError("Agent session belongs to another project")
            root, runtime = session.root, session.row["runtime_id"]
        return {"project_id": item.id, "external_session_id": external_session_id,
                "root": str(root), "source_root": str(source), "name": item.name,
                "trust_state": item.trust_state, "runtime": runtime}

    def validate(self, context, execute=True):
        latest = self.context(context["project_id"], context["external_session_id"])
        if latest["root"] != context["root"] or latest["source_root"] != context["source_root"]:
            raise PermissionError("Workspace folder binding changed")
        if execute and latest["trust_state"] != "trusted":
            raise PermissionError("Mark this project trusted before executing workspace commands")
        return latest

    def acquire(self, job):
        context = job.context
        group_key = hashlib.sha256(context["root"].encode()).hexdigest()
        group = self.groups.get(group_key)
        if group:
            if job.is_terminal and group["terminal"] and group["context"] == context:
                group["jobs"].add(job.id)
                job.group = group_key
                return
            raise ValueError("Close this workspace's terminals before changing Git state")
        session_id = context["external_session_id"]
        if session_id:
            session = self.supervisor.get(session_id)
            if session.adapter or session.task or session_id in self.supervisor.tool_loans:
                raise ValueError("Stop the coding runtime and other workspace writers before opening a writable terminal or Git action")
            session.acquire()
            if session.row["state"] == "rolled_back":
                session.lease.close()
                session.lease = None
                raise ValueError("Start a new session after rollback")
            self.supervisor.tool_loans[session_id] = group_key
        lock_path = self.directory / (hashlib.sha256(context["root"].encode()).hexdigest() + ".lock")
        handle = lock_path.open("a+b")
        try:
            if not os.fstat(handle.fileno()).st_size:
                handle.write(b"0"); handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            if session_id:
                self.supervisor.tool_loans.pop(session_id, None)
                session.lease.close(); session.lease = None
            raise ValueError("This workspace has an open terminal or another writer. Stop it before changing Git state.") from None
        self.groups[group_key] = {"handle": handle, "jobs": {job.id}, "terminal": job.is_terminal, "context": dict(context)}
        job.group = group_key

    def release(self, job):
        group = self.groups.get(job.group)
        if not group or job.id not in group["jobs"]:
            return
        group["jobs"].discard(job.id)
        if group["jobs"]:
            return
        group["handle"].close()
        self.groups.pop(job.group, None)
        session_id = job.context["external_session_id"]
        if session_id and self.supervisor.tool_loans.get(session_id) == job.group:
            self.supervisor.tool_loans.pop(session_id, None)
            session = self.supervisor.sessions.get(session_id)
            if session and session.lease and not session.adapter and not session.task:
                session.lease.close(); session.lease = None

    async def command(self, context, argv, mutate=False, fingerprint="", pre_commands=None):
        job = OwnedJob(self, context)
        try:
            if mutate:
                self.acquire(job)
                if fingerprint and (await self.changes(context))["fingerprint"] != fingerprint:
                    raise ValueError("Changes moved since review. Review them again.")
            job.start()
            for command in pre_commands or []:
                staged = await job.rpc.request("exec", {"argv": command}, timeout=48)
                if staged["code"]:
                    raise ValueError(redact_secrets(staged.get("stderr", ""))[:500] or "Staging selected paths failed")
            result = await job.rpc.request("exec", {"argv": argv}, timeout=48)
            if result["code"]:
                message = redact_secrets(result.get("stderr", "") or result.get("stdout", ""))[:500]
                raise ValueError(message or "Workspace command failed")
            return redact_secrets(result["stdout"])
        finally:
            await job.stop()

    @staticmethod
    def git_args(root, *args):
        return [shutil.which("git") or "git", "--no-optional-locks", "-c", "core.hooksPath=", "-c", "core.fsmonitor=false",
                "-c", "transfer.bundleURI=false", *git_ops._filter_overrides(Path(root)), "-C", root, *args]

    async def read_git(self, context, *args):
        return (await asyncio.to_thread(git_ops.git, Path(context["root"]), *args)).decode("utf-8", "replace")

    async def files(self, context, path=""):
        self.validate(context, execute=False)
        root = Path(context["root"])
        directory = project_workspace.safe_path(root, path) if path else root
        if not directory.is_dir():
            raise ValueError("Choose a directory")
        rows, candidates = [], []
        from itertools import islice
        for file in islice(directory.iterdir(), 2000):
            relative = file.relative_to(root).as_posix()
            try:
                project_workspace.safe_path(root, relative)
            except (ValueError, OSError):
                continue
            candidates.append(file)
        for file in sorted(candidates, key=lambda file: (not file.is_dir(), file.name.lower())):
            relative = file.relative_to(root).as_posix()
            rows.append({"name": file.name, "path": relative, "directory": file.is_dir()})
            if len(rows) == 500:
                break
        return {"items": rows}

    async def file(self, context, path):
        self.validate(context, execute=False)
        file = project_workspace.safe_path(Path(context["root"]), path)
        if not file.is_file() or file.stat().st_size > 350_000:
            raise ValueError("Choose a text file under 350 KB")
        text = (await asyncio.to_thread(self.bounded_read, file, 350_000)).decode("utf-8")
        if "\0" in text:
            raise ValueError("Binary files are not displayed")
        return {"path": path, "text": redact_secrets(text)}

    async def summary(self, context):
        self.validate(context, execute=False)
        try:
            head = (await self.read_git(context, "rev-parse", "HEAD")).strip()
            branch = (await self.read_git(context, "rev-parse", "--abbrev-ref", "HEAD")).strip()
            changes = await self.changes(context)
            branches = (await self.read_git(context, "for-each-ref", "--format=%(refname:short)", "refs/heads/")).splitlines()
            try:
                remote = (await self.read_git(context, "remote", "get-url", "origin")).strip()
            except ValueError:
                remote = ""
            repo = self.github_repo(remote)
            git = {"head": head, "branch": branch, "branches": branches[:200], "changes": changes["files"],
                   "fingerprint": changes["fingerprint"], "repository": repo, "is_git": True}
        except ValueError:
            git = {"is_git": False, "branch": "", "head": "", "branches": [], "changes": [], "repository": ""}
        return {**context, "git": git, "computer": platform.node(), "system": platform.system(),
                "scripts": self.load_scripts().get(context["project_id"], []), "terminal_supported": os.name == "nt"}

    @staticmethod
    def github_repo(remote):
        match = re.fullmatch(r"(?:https://github\.com/|git@github\.com:)([A-Za-z0-9][\w.-]*/[A-Za-z0-9][\w.-]*?)(?:\.git)?", remote)
        return match[1] if match else ""

    async def changes(self, context):
        self.validate(context, execute=False)
        head = (await self.read_git(context, "rev-parse", "HEAD")).strip()
        status = await self.read_git(context, "status", "--porcelain=v1", "-z", "--untracked-files=all")
        raw = status.split("\0")
        rows, digest, index = [], hashlib.sha256((head + status).encode()), 0
        while index < len(raw):
            entry = raw[index]; index += 1
            if not entry:
                continue
            code, path = entry[:2], entry[3:]
            if "R" in code or "C" in code:
                index += 1
                continue  # rename commits need an explicit terminal workflow
            try:
                file = project_workspace.safe_path(Path(context["root"]), path, exists=False)
                if file.exists():
                    if not file.is_file() or file.stat().st_size > 2_000_000:
                        continue
                    digest.update(await asyncio.to_thread(self.bounded_read, file, 2_000_000))
                digest.update(path.encode())
            except (ValueError, OSError):
                continue
            rows.append({"path": path, "status": code})
            if len(rows) > 500:
                raise ValueError("Too many changes to review in this surface")
        # Staged content is part of the review identity, even if working bytes
        # stayed the same. No external diff driver or textconv is run.
        staged = await self.read_git(context, "diff", "--cached", "--no-ext-diff", "--no-textconv", "--binary")
        digest.update(staged.encode())
        return {"files": rows, "fingerprint": digest.hexdigest(), "head": head}

    async def diff(self, context, path=""):
        self.validate(context, execute=False)
        changes = await self.changes(context)
        visible = [row["path"] for row in changes["files"]]
        paths = ["--", path] if path else ["--", *visible]
        if path:
            project_workspace.safe_path(Path(context["root"]), path, exists=False)
        text = await self.read_git(context, "diff", "--no-ext-diff", "--no-textconv", "HEAD", *paths) if path or visible else ""
        if path and not text:
            try:
                text = (await self.file(context, path))["text"]
            except (ValueError, OSError):
                pass
        return {"diff": redact_secrets(text), **changes}

    async def review(self, context, action, paths, message, branch):
        self.supervisor.admit(); self.validate(context)
        changes = await self.changes(context)
        if action == "commit":
            available = {row["path"] for row in changes["files"]}
            if not paths or len(paths) > 100 or not set(paths).issubset(available) or not message.strip():
                raise ValueError("Choose reviewed files and enter a commit message")
            from agent.lean.policy import contains_secret
            secrets = self.secrets(context)
            for path in paths:
                file = project_workspace.safe_path(Path(context["root"]), path, exists=False)
                text = (await asyncio.to_thread(self.bounded_read, file, 2_000_000)).decode("utf-8", "replace") if file.is_file() else ""
                if contains_secret({"content": text, "message": message}, secrets):
                    raise PermissionError("Selected content contains a stored credential. Remove it before committing.")
        if action in {"switch", "create_branch"}:
            if await self.read_git(context, "status", "--porcelain", "--untracked-files=all"):
                raise ValueError("Commit or preserve your local changes before switching branches")
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,100}", branch) or ".." in branch or branch.endswith("/"):
                raise ValueError("Enter a valid branch name")
            if context["external_session_id"]:
                raise ValueError("Agent worktrees stay detached. Create or switch branches in a project checkout.")
        remote = ""
        if action == "push":
            branch = (await self.read_git(context, "rev-parse", "--abbrev-ref", "HEAD")).strip()
            if branch == "HEAD":
                raise ValueError("Detached worktrees cannot push directly. Review and integrate their changes in the project checkout.")
            remote = (await self.read_git(context, "remote", "get-url", "origin")).strip()
            if not self.github_repo(remote):
                raise ValueError("Push supports an explicit GitHub origin without embedded credentials")
        key = uuid.uuid4().hex
        result = {"review_id": key, "action": action, "branch": branch, "paths": paths, "message": message,
                  "fingerprint": changes["fingerprint"], "head": changes["head"], "remote": remote,
                  "workspace": context["root"], "expires_at": time.time() + 300}
        self.reviews = {k: v for k, v in self.reviews.items() if v["expires_at"] > time.time()}
        self.reviews[key] = {**result, "context": dict(context)}
        return result

    async def apply(self, context, review_id):
        self.supervisor.admit(); self.validate(context)
        review = self.reviews.pop(review_id, None)
        if not review or review["expires_at"] < time.time() or review["context"] != context:
            raise ValueError("This Git review expired or belongs to another workspace")
        if (await self.changes(context))["fingerprint"] != review["fingerprint"]:
            raise ValueError("Changes moved since review. Inspect and review them again.")
        action = review["action"]
        if action == "commit":
            args = ["commit", "--only", "-m", review["message"], "--", *review["paths"]]
        elif action == "push":
            remote = (await self.read_git(context, "remote", "get-url", "origin")).strip()
            if remote != review["remote"]:
                raise ValueError("Origin changed since review")
            args = ["push", "--", "origin", f"HEAD:refs/heads/{review['branch']}"]
        else:
            args = ["switch", "-c", review["branch"]] if action == "create_branch" else ["switch", "--", review["branch"]]
        pre_commands = [self.git_args(context["root"], "add", "--", *review["paths"])] if action == "commit" else []
        output = await self.command(context, self.git_args(context["root"], *args), mutate=True,
                                    fingerprint=review["fingerprint"], pre_commands=pre_commands)
        return {"output": output, "summary": await self.summary(context)}

    async def terminal_start(self, context, rows, cols):
        self.supervisor.admit(); self.validate(context)
        stopped = [key for key, job in self.terminals.items() if job.rpc and job.rpc.closed]
        for key in stopped[:-32]:
            self.terminals.pop(key, None)
        if os.name != "nt":
            raise ValueError("The interactive terminal currently requires Windows ConPTY")
        if len([job for job in self.terminals.values() if job.rpc and not job.rpc.closed]) >= 8:
            raise ValueError("Close an existing terminal before opening another")
        job = OwnedJob(self, dict(context))
        job.is_terminal = True
        try:
            self.acquire(job)
            job.start()
            details = await job.rpc.request("terminal/start", {"rows": rows, "cols": cols})
            job.state = "running"
            self.terminals[job.id] = job
            return {**job.public(), **details}
        except BaseException:
            await job.stop()
            raise

    async def terminal(self, context, terminal_id, operation, text="", rows=24, cols=80, cursor=0):
        job = self.terminals.get(terminal_id)
        if not job:
            if operation == "stop":
                return {"id": terminal_id, "state": "stopped"}
            raise KeyError("Terminal belongs to another workspace or has expired")
        if any(job.context[key] != context[key] for key in ("project_id", "external_session_id", "root", "source_root")):
            raise KeyError("Terminal belongs to another workspace or has expired")
        if operation == "stop":
            await job.stop()
        else:
            self.validate(context)
            if privacy.mode() != "standard":
                await job.stop()
            if operation == "write":
                self.supervisor.admit()
                await job.rpc.request("terminal/write", {"text": text})
            elif operation == "resize" and not job.rpc.closed:
                await job.rpc.request("terminal/resize", {"rows": rows, "cols": cols})
            elif operation == "wait" and cursor >= job.seq and not job.rpc.closed:
                job.changed.clear()
                try:
                    await asyncio.wait_for(job.changed.wait(), 1)
                except asyncio.TimeoutError:
                    pass
        return job.public(cursor)

    def load_scripts(self):
        try:
            data = json.loads(self.script_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (ValueError, OSError):
            return {}

    async def script_save(self, context, name, command):
        self.validate(context)
        from agent.lean.policy import contains_secret
        if not name.strip() or not command.strip():
            raise ValueError("Enter a script name and command")
        if contains_secret({"command": command}, self.secrets(context)):
            raise ValueError("Keep stored credentials out of project scripts")
        data = self.load_scripts()
        rows = data.get(context["project_id"], [])
        if len(rows) >= 20:
            raise ValueError("This project already has twenty scripts")
        rows.append({"id": uuid.uuid4().hex, "name": name.strip(), "command": command.strip()})
        data[context["project_id"]] = rows
        git_ops.atomic_json(self.script_path, data)
        return rows

    async def script_run(self, context, script_id):
        row = next((row for row in self.load_scripts().get(context["project_id"], []) if row["id"] == script_id), None)
        if not row:
            raise KeyError("Script unavailable")
        job = await self.terminal_start(context, 24, 100)
        await self.terminal(context, job["id"], "write", text=row["command"] + "\r")
        return job

    async def pull_requests(self, context):
        self.supervisor.admit(); self.validate(context)
        remote = (await self.read_git(context, "remote", "get-url", "origin")).strip()
        repo = self.github_repo(remote)
        executable = shutil.which("gh") or str(Path(os.environ.get("ProgramFiles", "")) / "GitHub CLI" / "gh.exe")
        if not repo or not Path(executable).is_file():
            return {"items": [], "available": False, "reason": "Connect a GitHub origin and install/sign into GitHub CLI to load pull requests"}
        output = await self.command(context, [executable, "pr", "list", "--repo", repo, "--state", "all", "--limit", "50",
            "--json", "number,title,url,state,isDraft,headRefName,baseRefName,updatedAt"])
        return {"items": json.loads(output), "available": True, "repository": repo}

    async def usage(self, context):
        from agent.code_accounts import remembered
        # Passive cached account observations are useful before opening a session.
        # Do not launch a CLI or refresh credentials when opening this view.
        accounts = {}
        for runtime in ('codex', 'claude_cli'):
            status = remembered(runtime)
            if status.get('signed_in'):
                accounts[runtime] = {'runtime': runtime, 'plan': status.get('plan', ''),
                                     'subscription_usage': status.get('subscription_usage', {})}
        rows = []
        for session in self.supervisor.sessions.values():
            if session.row["project_id"] != context["project_id"]:
                continue
            row = {"runtime": session.row["runtime_id"], "session_id": session.row["id"], "state": session.row["state"]}
            if session.row["runtime_id"] == "codex" and session.adapter and session.adapter.rpc and not session.adapter.rpc.closed:
                self.supervisor.admit()
                await session.adapter.refresh_usage()
            if session.row.get("reported_usage"):
                row["usage"] = session.row["reported_usage"].get("tokens", {})
                row["observed_at"] = session.row["reported_usage"].get("observed_at")
                row["scope"] = session.row["reported_usage"].get("scope", "Runtime-reported usage")
                row["reported_cost_usd"] = session.row["reported_usage"].get("reported_cost_usd")
            row['subscription_usage'] = session.row.get('subscription_usage', {})
            usage = row['subscription_usage']
            previous = accounts.get(row['runtime'], {}).get('subscription_usage', {})
            if usage and usage.get('observed_at', 0) >= previous.get('observed_at', 0):
                accounts[row['runtime']] = {**accounts.get(row['runtime'], {}),
                                            'runtime': row['runtime'], 'subscription_usage': usage}
            rows.append(row)
        from agent.code_diagnostics import snapshot
        return {"runtimes": redact_payload(rows), "accounts": redact_payload(list(accounts.values())), "diagnostics": snapshot(self.supervisor),
                "note": "Only runtime-reported data is shown. Turn/thread token counts are not account-wide totals or a subscription bill. Missing limits/costs are unavailable, never zero."}

    async def activity(self, context):
        self.validate(context, execute=False)
        session = self.supervisor.sessions.get(context["external_session_id"])
        if not session or session.row["project_id"] != context["project_id"]:
            return {"items": [], "runtime": "echo"}
        items = {}
        for event in session.events:
            data, kind = event["data"], event["kind"]
            if data.get("threadId") and data["threadId"] != session.row.get("native_id"):
                continue
            candidates = []
            if kind in {"item/started", "item/completed"}:
                item = data.get("item") or {}
                if item.get("type") in {"commandExecution", "fileChange", "mcpToolCall", "webSearch", "imageView"}:
                    candidates.append((item.get("id"), item.get("type"), item.get("status", "running" if kind.endswith("started") else "completed"), item))
            elif kind == "sdk/event" and data.get("kind") == "AssistantMessage":
                for block in (data.get("data") or {}).get("content", []):
                    if isinstance(block, dict) and block.get("name") and "input" in block:
                        candidates.append((block.get("id"), block["name"], "reported", block))
            elif kind == "session/update":
                update = data.get("update") or {}
                if update.get("sessionUpdate") in {"tool_call", "tool_call_update"}:
                    candidates.append((update.get("toolCallId"), update.get("title", "Agent tool"), update.get("status", "reported"), update))
            for key, name, state, details in candidates:
                key = str(key or event["seq"])
                previous = items.get(key, {})
                items[key] = {"id": key, "name": name or previous.get("name", "Agent tool"), "status": state,
                              "at": previous.get("at", event["at"]), "research": bool(re.search(r"search|fetch|browse|web", str(name), re.I)),
                              "details": {**previous.get("details", {}), **details}}
        return {"items": list(items.values()), "runtime": session.row["runtime_id"]}

    async def editor(self, context, path=""):
        self.supervisor.admit(); self.validate(context)
        root = Path(context["root"])
        target = project_workspace.safe_path(root, path) if path else root
        if os.name != "nt":
            raise ValueError("Open in VS Code is currently supported on Windows")
        os.startfile("vscode://file/" + quote(target.as_posix(), safe="/:"))
        return {"opened": True}


async def dispatch(supervisor, method, project_id, external_session_id="", **kwargs):
    if not hasattr(supervisor, "workbench"):
        supervisor.workbench = Workbench(supervisor)
    workbench = supervisor.workbench
    context = workbench.context(project_id, external_session_id)
    return redact_payload(await getattr(workbench, method)(context, **kwargs), workbench.secrets(context))
