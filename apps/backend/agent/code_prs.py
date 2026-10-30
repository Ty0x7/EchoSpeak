"""Explicit PR links and guarded settlement. No merges or inferred branch ownership."""
import json
import threading
import time
from pathlib import Path

from agent.code_worktree import atomic_json

_lock = threading.RLock()


def _path():
    from config import DATA_DIR
    return DATA_DIR / "code-inbox-pr-links.json"


def _read():
    return json.loads(_path().read_text(encoding="utf-8")) if _path().exists() else {}


async def link(supervisor, identity, external, number, activity):
    from agent import echo_code, project_workspace
    from agent.state import get_state_store
    from agent.workbench import Workbench
    if not 1 <= number <= 1_000_000_000:
        raise ValueError("Enter a pull request number")
    if external:
        session = supervisor.get(identity)
        pid, snapshot = session.row["project_id"], session.inbox()
    else:
        if not echo_code.is_code_thread(identity):
            raise KeyError("Coding session unavailable")
        pid = get_state_store().get_thread_state(identity).active_project_id
        snapshot = get_state_store().code_inbox_snapshot(identity)
    if snapshot["activity"] != activity or not snapshot["can_triage"]:
        raise ValueError("The session changed or is working; refresh before linking")
    bench = Workbench(supervisor)
    context = bench.context(pid, identity if external else "")
    branch = (await bench.read_git(context, "branch", "--show-current")).strip()
    if branch in {"", "main", "master", "HEAD"}:
        raise ValueError("Use the session's feature branch before linking a PR")
    result = await bench.pull_requests(context)
    pr = next((r for r in result.get("items", []) if r["number"] == number and r["headRefName"] == branch), None)
    if not pr:
        raise ValueError("That PR was not found for this checkout's feature branch")
    row = {"id": identity, "external": external, "project_id": pid, "root": context["root"],
           "head": (await bench.read_git(context, "rev-parse", "HEAD")).strip(),
           "branch": branch, "number": number, "url": pr["url"], "activity": activity,
           "repository": result["repository"], "linked_at": time.time(), "state": pr["state"]}
    with _lock:
        data = _read()
        if len(data) >= 1000 and ("external:" if external else "native:") + identity not in data:
            raise ValueError("PR link budget exhausted")
        data[("external:" if external else "native:") + identity] = row
        _path().parent.mkdir(parents=True, exist_ok=True)
        atomic_json(_path(), data)
    return {k: v for k, v in row.items() if k != "root"}


async def sync(supervisor):
    """Called explicitly by the visible inbox, throttled to one forge check per project/minute."""
    from agent import echo_code
    from agent.state import get_state_store
    from agent.workbench import Workbench
    results = []
    with _lock:
        candidates = list(_read().items())
    bench, cache = Workbench(supervisor), {}
    for key, row in candidates:
        if row.get("settled") or time.time() - row.get("checked_at", 0) < 60:
            continue
        try:
            if row["external"]:
                session = supervisor.get(row["id"])
                snapshot = session.inbox()
            else:
                if get_state_store().get_thread_state(row["id"]).active_project_id != row["project_id"]:
                    continue
                snapshot = get_state_store().code_inbox_snapshot(row["id"], waiting=row["id"] in echo_code._waiting_sessions())
            # Preserve every explicit owner decision and refuse all new/blocked work.
            if snapshot["activity"] != row["activity"] or snapshot["state"] != "ready_to_review" or not snapshot["can_triage"]:
                continue
            context = bench.context(row["project_id"], row["id"] if row["external"] else "")
            if context["root"] != row["root"] or (await bench.read_git(context, "branch", "--show-current")).strip() != row["branch"]:
                continue
            # Never settle a dirty checkout or one whose branch was reused after linking.
            if (await bench.read_git(context, "status", "--porcelain")).strip():
                continue
            if (await bench.read_git(context, "rev-parse", "HEAD")).strip() != row["head"]:
                continue
            cache_key = (context["root"], row["repository"])
            if cache_key not in cache:
                cache[cache_key] = await bench.pull_requests(context)
            listing = cache[cache_key]
            if listing.get("repository") != row["repository"]:
                continue
            pr = next((p for p in listing.get("items", []) if p["number"] == row["number"] and p["headRefName"] == row["branch"] and p["url"] == row["url"]), None)
            row["checked_at"] = time.time()
            if pr:
                row["state"] = pr["state"]
            if pr and pr["state"] == "MERGED":
                if row["external"]:
                    await supervisor.triage(row["id"], "settled", row["activity"], automatic=True)
                else:
                    echo_code.triage_session(row["id"], "settled", row["activity"], automatic=True)
                row["settled"] = True
                results.append(row["id"])
            with _lock:
                data = _read()
                # A newer explicit link must not be overwritten by the forge response.
                if data.get(key, {}).get("linked_at") == row["linked_at"]:
                    data[key] = row
                    atomic_json(_path(), data)
        except (ValueError, PermissionError, KeyError, RuntimeError, OSError):
            continue
    return {"settled": results}
