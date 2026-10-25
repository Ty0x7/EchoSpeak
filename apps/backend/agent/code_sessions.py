"""Owner-only external coding sessions, independent of Echo's ThreadManager.

One supervisor loop owns all children. Sync API workers, Stop everything and
settings transitions can synchronously wait for that loop to close process jobs.
No request automatically replays an interrupted prompt.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
import importlib.util
from importlib import metadata
import json
import os
from pathlib import Path
import re
import threading
import time
import uuid

from agent import code_worktree as wt, privacy, project_workspace
from agent.code_runtimes import Acp, Claude, Codex


def sdk_installed():
    try:
        return importlib.util.find_spec("claude_agent_sdk") is not None and metadata.version("claude-agent-sdk") == "0.2.174"
    except metadata.PackageNotFoundError:
        return False


class Session:
    def __init__(self, manager, row):
        self.manager, self.row = manager, row
        self.root = Path(row["worktree"])
        self.directory = self.root.parent
        self.adapter = None
        self.task = None
        self.lease = None
        self.events = []
        self.sequence = 0
        self.approvals = {}
        self.account = {"connected": False, "type": "unverified"}
        self.models = []
        self.turn_id = None
        self.turn_finished = asyncio.Event()
        self.result = "failed"
        self.api_key = ""
        self.redaction_values = []
        self.stop_lock = asyncio.Lock()

    def validate(self):
        item = project_workspace.project(self.row["project_id"])
        if item.trust_state != "trusted" or str(project_workspace.root_for(item)) != self.row["source_root"]:
            raise PermissionError("Project trust or folder binding changed; this session is unavailable")
        expected = self.manager.directory / self.row["id"] / "tree"
        if self.root != expected or self.root.is_symlink() or self.root.resolve() != expected:
            raise ValueError("Owned worktree path changed")
        marker = self.root / ".git"
        if marker.is_symlink() or marker.read_text(encoding="utf-8") != self.row["git_marker"]:
            raise ValueError("Worktree Git binding changed; inspect it manually")

    def acquire(self):
        if self.lease:
            return
        handle = (self.directory / "writer.lock").open("a+b")
        try:
            if not os.fstat(handle.fileno()).st_size:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            raise ValueError("Another EchoSpeak process owns this coding worktree") from None
        try:
            latest = json.loads((self.directory / "session.json").read_text(encoding="utf-8"))
            if any(latest[key] != self.row[key] for key in ("id", "project_id", "runtime_id", "worktree", "source_root", "git_marker")):
                raise ValueError("Worktree session binding changed")
            self.row = latest
        except (OSError, ValueError, KeyError):
            handle.close()
            raise ValueError("Worktree session metadata changed or is unavailable") from None
        self.lease = handle

    def save(self):
        wt.atomic_json(self.directory / "session.json", self.row)

    def emit(self, kind, data):
        from agent.lean.policy import redact_payload
        self.sequence += 1
        # Bound individual events and the replay window. Auth responses never use
        # this channel. Native transcript remains owned by the original harness.
        safe = redact_payload(redact_payload(data), self.redaction_values + ([self.api_key] if self.api_key else []))
        raw = json.dumps(safe, ensure_ascii=False, default=str)
        if len(raw) > 64_000:
            safe = {"notice": "Large runtime event omitted; inspect native history and worktree", "preview": raw[:4000]}
        self.events.append({"seq": self.sequence, "kind": kind, "data": safe, "at": time.time()})
        while len(self.events) > 600 or sum(len(json.dumps(event)) for event in self.events) > 2_000_000:
            self.events.pop(0)

    def public(self, cursor=0):
        return {**{key: value for key, value in self.row.items() if key not in {"command", "git_marker", "creation_context", "creation_key"}},
                "account": self.account, "models": self.models,
                "events": [event for event in self.events if event["seq"] > cursor], "cursor": self.sequence,
                "replay_gap": bool(self.events and cursor < self.events[0]["seq"] - 1),
                "approvals": [{"id": key, "kind": row["kind"], "details": row["details"]} for key, row in self.approvals.items()],
                "rollback_available": (self.directory / "checkpoint.json").is_file() and not self.task,
                "connected": bool(self.adapter and self.adapter.rpc and not self.adapter.rpc.closed and self.row["state"] != "connecting")}

    async def approval(self, kind, details):
        if self.manager.suspended or privacy.mode() != "standard" or not self.task or len(self.approvals) >= 8:
            return False
        from agent.lean.policy import contains_secret, redact_payload
        if len(json.dumps(details)) > 32_000 or contains_secret(details) or (self.api_key and contains_secret(details, [self.api_key])):
            self.emit("permission_denied", {"reason": "Request is too large or contains a stored secret"})
            return False
        request_id = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self.approvals[request_id] = {"future": future, "kind": kind, "details": redact_payload(details)}
        self.emit("approval/requested", {"id": request_id, "kind": kind})
        try:
            return await asyncio.wait_for(future, 180)
        except asyncio.TimeoutError:
            return False
        finally:
            self.approvals.pop(request_id, None)
            self.emit("approval/resolved", {"id": request_id})

    async def stop(self, reason):
        async with self.stop_lock:
            await self._stop(reason)

    async def _stop(self, reason):
        if not self.adapter and not self.task and not self.lease:
            return
        # Deny every pending prompt before terminating the complete owned job.
        for request in list(self.approvals.values()):
            if not request["future"].done():
                request["future"].set_result(False)
        if self.adapter and self.adapter.rpc:
            try:
                if self.task:
                    await asyncio.wait_for(self.adapter.cancel(), 2)
            except Exception:
                pass
            await self.adapter.rpc.close()
        if self.task and self.task is not asyncio.current_task():
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        self.task = None
        self.adapter = None
        self.api_key = ""
        self.row["state"] = "stopped"
        self.save()
        if self.lease:
            self.lease.close()
            self.lease = None
        self.emit("session/stopped", {"reason": reason})


class Sessions:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.sessions = {}
        self.suspended = False
        self.starting = set()
        self.owned_tools = set()
        self.tool_loans = {}
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, name="EchoCodeSupervisor", daemon=True)
        self.thread.start()
        self.call(self._load())
        self.call(self._start_monitor())

    async def _start_monitor(self):
        self.monitor = asyncio.create_task(self._monitor())

    async def _monitor(self):
        while True:
            await asyncio.sleep(1)
            for tool in list(self.owned_tools):
                try:
                    self.admit()
                    tool.workbench.validate(tool.context)
                except (ValueError, OSError, KeyError, PermissionError):
                    await tool.stop("Workspace admission changed")
            for session in list(self.sessions.values()):
                if not session.adapter:
                    continue
                try:
                    self.admit()
                    session.validate()
                except (ValueError, OSError, KeyError, PermissionError):
                    await session.stop("Runtime admission or project trust changed")

    async def shutdown(self):
        self.monitor.cancel()
        await asyncio.gather(self.monitor, return_exceptions=True)
        await self.stop_all("Backend shutdown")

    def call(self, coroutine):
        future = asyncio.run_coroutine_threadsafe(coroutine, self.loop)
        try:
            return future.result(timeout=55)
        except TimeoutError:
            future.cancel()
            raise RuntimeError("Coding runtime operation timed out and was cancelled") from None

    async def _load(self):
        for directory in self.directory.iterdir():
            if not re.fullmatch(r"[0-9a-f]{32}", directory.name) or directory.is_symlink():
                continue
            try:
                row = json.loads((directory / "session.json").read_text(encoding="utf-8"))
                if row["id"] == directory.name and row["runtime_id"] in {"codex", "claude_code", "acp"}:
                    row["state"] = "stopped"  # never auto-reconnect/replay after restart
                    self.sessions[row["id"]] = Session(self, row)
            except (OSError, ValueError, KeyError):
                continue

    def admit(self):
        from agent.lean.stop import paused
        if self.suspended or _privacy_failed or privacy.mode() != "standard":
            raise PermissionError("External runtimes are blocked in Private/Offline mode")
        if paused():
            raise PermissionError("Choose Resume after Stop everything before launching a coding runtime")

    def get(self, session_id):
        session = self.sessions.get(session_id)
        if not session:
            raise KeyError("Coding session unavailable")
        session.validate()
        return session

    async def list(self, project_id):
        return [session.public() for session in self.sessions.values() if session.row["project_id"] == project_id]

    async def create(self, project_id, runtime_id, key, acknowledge_dirty=False, command=None):
        self.admit()
        if runtime_id not in {"codex", "claude_code", "acp"}:
            raise ValueError("Select an external runtime")
        item = project_workspace.project(project_id)
        source = project_workspace.root_for(item)
        if item.trust_state != "trusted":
            raise PermissionError("Mark the project trusted before launching an external harness")
        context = {"project_id": project_id, "runtime_id": runtime_id, "source_root": str(source),
                   "command": command or [], "acknowledge_dirty": acknowledge_dirty}
        for session in self.sessions.values():
            if session.row["creation_key"] == key:
                if session.row["creation_context"] != context:
                    raise ValueError("Creation identity was already used for a different request")
                session.validate()
                return session.public()
        if key in self.starting:
            raise ValueError("This session creation is already in progress")
        if runtime_id == "acp" and (not command or len(command) > 32 or sum(map(len, command)) > 8000):
            raise ValueError("Configure a native ACP executable and bounded arguments first")
        self.starting.add(key)
        session_id = uuid.uuid4().hex
        directory = self.directory / session_id
        try:
            workspace = await asyncio.to_thread(wt.create, source, directory, acknowledge_dirty=acknowledge_dirty)
            # Provisioning performs local Git/blob reads only. Retain the stopped
            # session even if privacy changed while Git worked; connection has
            # its own admission check and can never auto-launch from creation.
            row = {"id": session_id, "project_id": project_id, "runtime_id": runtime_id, "creation_key": key,
                   "creation_context": context, "command": command or [], "state": "stopped", "created_at": time.time(),
                   "native_id": None, **workspace, "git_marker": (Path(workspace["worktree"]) / ".git").read_text(encoding="utf-8")}
            session = self.sessions[session_id] = Session(self, row)
            session.save()
            return session.public()
        finally:
            self.starting.discard(key)

    async def connect(self, session_id, api_key=""):
        self.admit()
        if session_id in self.tool_loans:
            raise ValueError("Close this worktree's terminals before reconnecting the coding runtime")
        session = self.get(session_id)
        if session.row["state"] == "rolled_back":
            raise ValueError("This session was rolled back. Start a new session so native history cannot disagree with restored files.")
        if session.row["state"] == "connecting":
            raise ValueError("This runtime connection is already in progress")
        if session.adapter and session.adapter.rpc and not session.adapter.rpc.closed:
            return session.public()
        runtime = session.row["runtime_id"]
        if runtime == "claude_code":
            if not sdk_installed():
                raise ValueError("Install the optional Claude Agent SDK runtime before connecting")
            session.api_key = api_key or os.environ.get("ECHOSPEAK_CLAUDE_AGENT_API_KEY", "")
            if not session.api_key:
                raise ValueError("Supply this harness's Anthropic API key. Subscription OAuth tokens are not supported.")
            session.redaction_values = list(set(session.redaction_values + [session.api_key]))
        session.acquire()
        if session.row["state"] == "rolled_back":
            session.lease.close()
            session.lease = None
            raise ValueError("This session was rolled back. Start a new session so native history cannot disagree with restored files.")
        session.row["state"] = "connecting"
        session.adapter = {"codex": Codex, "claude_code": Claude, "acp": Acp}[runtime](session)
        try:
            await session.adapter.connect()
            self.admit()
            session.row["state"] = "ready"
            session.save()
        except BaseException:
            await session.stop("Connection failed")
            raise
        return session.public()

    async def login(self, session_id, method_id=""):
        self.admit()
        session = self.get(session_id)
        if not session.adapter or session.adapter.rpc.closed or session.task:
            raise ValueError("Connect an idle runtime first")
        if session.row["runtime_id"] == "codex":
            return await session.adapter.login()
        if session.row["runtime_id"] == "acp":
            # Authentication may outlive an HTTP request. Expose pending state,
            # retaining a single controlled operation in this session.
            raise ValueError("Authenticate this ACP executable in its own terminal before connecting. Interactive ACP auth is not enabled.")
        raise ValueError("Claude uses the dedicated API key supplied at connection")

    async def prompt(self, session_id, text, model=""):
        self.admit()
        session = self.get(session_id)
        if session.task or session.row["state"] == "connecting" or not session.adapter or session.adapter.rpc.closed:
            raise ValueError("Connect an idle runtime before sending")
        if not text.strip() or len(text) > 60_000 or len(model) > 200:
            raise ValueError("Enter a bounded coding task")
        if session.row["runtime_id"] == "codex" and not session.account.get("connected"):
            raise ValueError("Sign into Codex before sending a task")
        session.row["state"] = "preparing"
        # Set the writer task BEFORE awaiting a checkpoint: simultaneous requests
        # cannot both start a turn in the same worktree.
        session.task = asyncio.create_task(self._turn(session, text, model))
        return session.public()

    async def _turn(self, session, text, model):
        before = None
        try:
            before = await asyncio.to_thread(wt.snapshot, session.root)
            self.admit()
            session.validate()
            session.turn_id = None
            session.turn_finished.clear()
            session.result = "failed"
            session.row["state"] = "running"
            session.emit("user/message", {"text": text})
            session.save()
            task = asyncio.create_task(session.adapter.prompt(text, model))
            try:
                while not task.done():
                    await asyncio.sleep(0.15)
                    session.validate()
                    if privacy.mode() != "standard" or session.adapter.rpc.closed:
                        raise RuntimeError("Runtime disconnected or privacy mode changed")
                await task
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
            session.row["state"] = session.result
        except asyncio.CancelledError:
            session.row["state"] = "interrupted"
            raise
        except Exception as exc:
            if session.adapter and session.adapter.rpc:
                await session.adapter.rpc.close()
            session.row["state"] = "failed"
            # Exceptions from adapters are intentionally generic, never raw stderr.
            session.emit("session/error", {"message": str(exc)[:350]})
        finally:
            if before is not None:
                try:
                    after = await asyncio.to_thread(wt.snapshot, session.root)
                    wt.atomic_json(session.directory / "checkpoint.json", {"before": before, "after": wt.fingerprint(after)})
                except Exception:
                    (session.directory / "checkpoint.json").unlink(missing_ok=True)
                    session.emit("rollback/unavailable", {"reason": "Worktree changed beyond checkpoint limits"})
            session.emit("turn/finished", {"state": session.row["state"]})
            session.save()
            session.task = None

    async def decide(self, session_id, request_id, allow):
        self.admit()
        session = self.get(session_id)
        request = session.approvals.get(request_id)
        if not request or request["future"].done():
            raise ValueError("This approval expired or was already answered")
        request["future"].set_result(bool(allow))
        return {"ok": True}

    async def poll(self, session_id, cursor):
        session = self.sessions.get(session_id)
        if not session:
            raise KeyError("Coding session unavailable")
        try:
            session.validate()
        except (ValueError, OSError, KeyError, PermissionError):
            await session.stop("Project trust or folder binding changed")
            raise
        if privacy.mode() != "standard" and session.adapter:
            await session.stop("Privacy mode changed")
        if session.adapter and session.adapter.rpc.closed and session.row["state"] == "ready":
            await session.stop("Runtime exited")
        return session.public(cursor)

    async def inspect(self, session_id):
        session = self.get(session_id)
        from agent.lean.policy import redact_payload
        return redact_payload(await asyncio.to_thread(wt.inspect, session.root), session.redaction_values)

    async def stop(self, session_id):
        session = self.sessions.get(session_id)
        if not session:
            raise KeyError("Coding session unavailable")
        await session.stop("Stopped by owner")
        return session.public()

    async def rollback(self, session_id):
        session = self.get(session_id)
        if session_id in self.tool_loans:
            raise ValueError("Close this worktree's terminals before rollback")
        if session.task or session.adapter:
            raise ValueError("Stop this runtime before rolling back its last turn")
        session.acquire()
        try:
            await asyncio.to_thread(wt.rollback, session.root, session.directory / "checkpoint.json")
            session.row["state"] = "rolled_back"
            session.save()
        finally:
            session.lease.close()
            session.lease = None
        session.emit("worktree/rollback", {"status": "restored", "recovery_copy": True})
        return session.public()

    async def stop_all(self, reason, suspend=False):
        if suspend:
            self.suspended = True
        await asyncio.gather(*(tool.stop(reason) for tool in list(self.owned_tools)))
        await asyncio.gather(*(session.stop(reason) for session in self.sessions.values()))

    async def unsuspend(self):
        self.suspended = False


_instance = None
_lock = threading.Lock()
_privacy_failed = False


def manager():
    global _instance
    with _lock:
        if _instance is None:
            from config import DATA_DIR
            _instance = Sessions(Path(DATA_DIR) / "code-sessions")
        return _instance


def stop_all(reason="Stopped by owner"):
    if _instance is not None:
        _instance.call(_instance.stop_all(reason))


@contextmanager
def privacy_transition():
    global _privacy_failed
    # Lock admission even when no manager has been created yet. The supervisor
    # cannot start between the stop and the committed settings change.
    with _lock:
        if _instance is not None:
            _instance.call(_instance.stop_all("Privacy settings changed", suspend=True))
        try:
            yield
        except BaseException:
            _privacy_failed = True
            raise
        else:
            _privacy_failed = False
            if _instance is not None:
                _instance.call(_instance.unsuspend())
