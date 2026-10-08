"""Memory clean-up when the PC is idle.

Memories pile up: the same fact saved twice in different words, facts nobody has
needed for months, and pairs that can't both be true ("Lives in Leeds" and "Lives
in York"). Once a day, after ten quiet minutes, this pass:

  - merges near-duplicates (same owner, scope and kind; keeps the pinned or the
    newer one, and the other records which one replaced it)
  - retires facts nobody has recalled for 90 days (never pinned ones or profile
    facts; retired facts stay on disk and can be restored)
  - flags likely contradictions for the owner to settle in Settings; it never
    picks a side by itself

Nothing is deleted. Each run's report is saved, and Settings shows the latest.
"""

from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable, Optional

STALE_DAYS = 90
DUPLICATE_RATIO = 0.9
CONTRADICTION_RATIO = 0.55
IDLE_SECONDS = 600
EVERY_SECONDS = 24 * 3600
KEEP_TYPES = {"profile", "identity", "preference"}
# Old chat transcripts have their own switch (Remove chat transcripts); this pass is for facts.
SKIP_TYPES = {"conversation"}

_scheduler: Optional[threading.Thread] = None
_scheduler_lock = threading.Lock()


def _report_path() -> Path:
    from config import DATA_DIR

    return Path(DATA_DIR) / "memory-tidy.json"


def last_report() -> dict[str, Any]:
    try:
        data = json.loads(_report_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", str(text or "").lower())


def _when(value: Any) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _pinned(record: dict[str, Any]) -> bool:
    return bool((record.get("metadata") or {}).get("pinned") or record.get("pinned"))


def _group_key(record: dict[str, Any]) -> tuple[str, str, str, str]:
    return (str(record.get("owner_id") or ""), str(record.get("scope") or "account"),
            str(record.get("project_id") or ""), str(record.get("memory_type") or ""))


def _similar(a: dict[str, Any], b: dict[str, Any]) -> float:
    return SequenceMatcher(None, " ".join(_words(a.get("text", ""))), " ".join(_words(b.get("text", "")))).ratio()


def _same_subject(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Same opening words ("lives in", "prefers", "partner is"), different ending."""
    wa, wb = _words(a.get("text", "")), _words(b.get("text", ""))
    head = min(3, len(wa) - 1, len(wb) - 1)
    return head >= 2 and wa[:head] == wb[:head] and wa[head:] != wb[head:]


def plan(records: dict[str, dict[str, Any]], *, now: Optional[datetime] = None, stale_days: int = STALE_DAYS,
         tracking_since: Optional[datetime] = None) -> dict[str, Any]:
    """What a tidy pass would do, without changing anything.

    Recalls are only known from `tracking_since` on: a fact is stale when neither it nor its
    last recall falls within `stale_days`, counting from when tracking began at the earliest.
    """
    now = now or datetime.now()
    tracking_since = tracking_since or now
    active = [r for r in records.values()
              if bool(r.get("active", True)) and str(r.get("memory_type") or "") not in SKIP_TYPES]
    merges: list[tuple[str, str]] = []  # (dropped, kept)
    dropped: set[str] = set()
    groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    for record in active:
        groups.setdefault(_group_key(record), []).append(record)
    flags: list[tuple[str, str]] = []
    for members in groups.values():
        members.sort(key=lambda r: (_pinned(r), str(r.get("updated_at") or r.get("created_at") or "")), reverse=True)
        for i, keep in enumerate(members):
            if keep["id"] in dropped:
                continue
            for other in members[i + 1:]:
                if other["id"] in dropped:
                    continue
                la, lb = len(str(keep.get("text") or "")), len(str(other.get("text") or ""))
                if min(la, lb) * 2 < max(la, lb) and not _same_subject(keep, other):
                    continue  # too different in length to be either (keeps big memories fast)
                ratio = _similar(keep, other)
                if ratio >= DUPLICATE_RATIO and not _pinned(other):
                    merges.append((other["id"], keep["id"]))
                    dropped.add(other["id"])
                elif ratio >= CONTRADICTION_RATIO and _same_subject(keep, other):
                    reviewed = set((keep.get("metadata") or {}).get("contradiction_ok") or [])
                    if other["id"] not in reviewed and other["id"] not in set(keep.get("contradiction_ids") or []):
                        flags.append((keep["id"], other["id"]))
    retire: list[str] = []
    cutoff = now - timedelta(days=stale_days)
    for record in active:
        if record["id"] in dropped or _pinned(record) or str(record.get("memory_type") or "") in KEEP_TYPES:
            continue
        last = _when(record.get("last_recalled_at")) or _when(record.get("created_at"))
        if last is not None and max(last, tracking_since) < cutoff:
            retire.append(record["id"])
    return {"merge": merges, "retire": retire, "flag": flags}


def tidy(memory: Any, *, now: Optional[datetime] = None, stale_days: int = STALE_DAYS) -> dict[str, Any]:
    """Run a tidy pass on an AgentMemory and save the report."""
    stamp = (now or datetime.now()).isoformat()
    previous = last_report()
    tracking_since = _when(previous.get("tracking_since")) or (now or datetime.now())
    # Plan on a copy, so chats can save and recall memories while the comparisons run;
    # lock again only to apply, skipping anything that changed in between.
    with memory._records_lock:
        memory._load_records()
        snapshot = {mid: json.loads(json.dumps(r, default=str)) for mid, r in memory._records.items()}
    actions = plan(snapshot, now=now, stale_days=stale_days, tracking_since=tracking_since)
    with memory._records_lock:
        memory._load_records()
        records = memory._records

        def unchanged(mid: str) -> bool:
            live = records.get(mid)
            return bool(live) and bool(live.get("active", True)) and live.get("updated_at") == snapshot[mid].get("updated_at")

        actions = {
            "merge": [(gone, kept) for gone, kept in actions["merge"] if unchanged(gone) and kept in records],
            "retire": [mid for mid in actions["retire"] if unchanged(mid)],
            "flag": [(a, b) for a, b in actions["flag"] if a in records and b in records],
        }
        for gone, kept in actions["merge"]:
            records[gone].update({"active": False, "status": "merged", "superseded_by": kept, "deleted_at": stamp, "updated_at": stamp})
        for memory_id in actions["retire"]:
            records[memory_id].update({"active": False, "status": "retired", "deleted_at": stamp, "updated_at": stamp})
        for a, b in actions["flag"]:
            for one, other in ((a, b), (b, a)):
                ids = list(records[one].get("contradiction_ids") or [])
                if other not in ids:
                    records[one]["contradiction_ids"] = ids + [other]
        if any(actions.values()):
            memory._save_records()
        text = {mid: str(r.get("text") or "") for mid, r in records.items()}
    report = {
        "ran_at": time.time(),
        "tracking_since": tracking_since.isoformat(),
        "merged": [{"removed": text.get(gone, ""), "kept": text.get(kept, ""), "id": gone} for gone, kept in actions["merge"]],
        "retired": [{"text": text.get(mid, ""), "id": mid} for mid in actions["retire"]],
        "flagged": [{"a": {"id": a, "text": text.get(a, "")}, "b": {"id": b, "text": text.get(b, "")}} for a, b in actions["flag"]],
    }
    path = _report_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(report, indent=2), encoding="utf-8")
    tmp.replace(path)
    return report


def open_contradictions(memory: Any) -> list[dict[str, Any]]:
    """Flagged pairs still waiting for the owner, each listed once."""
    with memory._records_lock:
        memory._load_records()
        records = {mid: dict(r) for mid, r in memory._records.items() if bool(r.get("active", True))}
    pairs, seen = [], set()
    for mid, record in records.items():
        for other in record.get("contradiction_ids") or []:
            key = tuple(sorted((mid, other)))
            if other in records and key not in seen:
                seen.add(key)
                pairs.append({"a": {"id": key[0], "text": records[key[0]].get("text", "")},
                              "b": {"id": key[1], "text": records[key[1]].get("text", "")}})
    return pairs


def resolve(memory: Any, a: str, b: str, keep: str) -> None:
    """keep = a | b | both. The one not kept is retired (restorable); 'both' stops the flag coming back."""
    if keep not in {a, b, "both"}:
        raise ValueError("keep must be one of the two memories, or 'both'")
    stamp = datetime.now().isoformat()
    with memory._records_lock:
        memory._load_records()
        records = memory._records
        if a not in records or b not in records:
            raise KeyError("memory not found")
        for one, other in ((a, b), (b, a)):
            records[one]["contradiction_ids"] = [x for x in records[one].get("contradiction_ids") or [] if x != other]
            if keep == "both":
                meta = records[one].setdefault("metadata", {})
                meta["contradiction_ok"] = sorted(set(meta.get("contradiction_ok") or []) | {other})
        if keep != "both":
            dropped = b if keep == a else a
            records[dropped].update({"active": False, "status": "replaced", "superseded_by": keep,
                                     "deleted_at": stamp, "updated_at": stamp})
        memory._save_records()


def due(*, idle: float, last_run: float, now: Optional[float] = None, paused: bool = False) -> bool:
    now = time.time() if now is None else now
    return not paused and idle >= IDLE_SECONDS and now - last_run >= EVERY_SECONDS


def ensure_scheduler(get_memory: Callable[[], Any], *, interval: float = 300.0) -> None:
    """Start the once-a-day idle pass (one background thread per process)."""
    global _scheduler
    with _scheduler_lock:
        if _scheduler is not None and _scheduler.is_alive():
            return

        def loop() -> None:
            from loguru import logger

            from agent.lean import stop

            while True:
                time.sleep(interval)
                try:
                    if due(idle=stop.idle_seconds(), last_run=float(last_report().get("ran_at") or 0), paused=stop.paused()):
                        memory = get_memory()
                        if memory is not None and hasattr(memory, "_records_lock"):
                            report = tidy(memory)
                            logger.info("Memory tidy: merged {}, retired {}, flagged {}", len(report["merged"]),
                                        len(report["retired"]), len(report["flagged"]))
                except Exception:
                    logger.exception("Memory tidy failed")

        _scheduler = threading.Thread(target=loop, name="echospeak-memory-tidy", daemon=True)
        _scheduler.start()
