"""Remove chat-owned files using the ownership stored by each producer."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator


def owned_files(directory: Path, pattern: str, session_id: str) -> Iterator[tuple[Path, dict]]:
    for path in directory.glob(pattern):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            continue
        if isinstance(record, dict) and record.get("session_id") == session_id:
            yield path, record


def delete_files(session_id: str) -> None:
    from agent import mcp_apps
    from agent.lean import observations, summaries, tool_outputs, tracing
    from agent.voice_runtime import get_voice_job_store
    from agent.voice_transport import get_voice_transport_store

    summaries._path(session_id).unlink(missing_ok=True)
    with tracing._lock:
        directory = tracing.session_dir(session_id)
        if not directory.is_symlink():
            for path, _record in owned_files(directory, "msg_*.json", session_id):
                path.unlink(missing_ok=True)
            if directory.is_dir() and not any(directory.iterdir()):
                directory.rmdir()
    for module, pattern, suffix in ((tool_outputs, "out-*.json", ".txt"), (observations, "obs-*.json", ".png")):
        with module._lock:
            for path, _record in owned_files(module.root(), pattern, session_id):
                path.with_suffix(suffix).unlink(missing_ok=True)
                path.unlink(missing_ok=True)
    with mcp_apps._lock:
        for path, _record in owned_files(mcp_apps.root(), "app-*.json", session_id):
            path.unlink(missing_ok=True)
    transport = get_voice_transport_store()
    with transport._lock:
        for path, record in owned_files(transport.clips_root, "voice-clip-*.json", session_id):
            # The transport owns a clip named after this exact record.
            if record.get("relative_path") == path.with_suffix(".wav").name:
                path.with_suffix(".wav").unlink(missing_ok=True)
            path.unlink(missing_ok=True)
        for path, _record in owned_files(transport.turns_root, "voice-turn-*.json", session_id):
            path.unlink(missing_ok=True)
    jobs = get_voice_job_store()
    with jobs._lock:
        for path, _record in owned_files(jobs.jobs_root, "voice-*.json", session_id):
            path.unlink(missing_ok=True)
