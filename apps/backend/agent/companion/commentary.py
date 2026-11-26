"""Decide what is worth saying, say it in Echo Companion's voice, and remember reports.

The pipeline runs on its own worker thread, fed by the observation store, so ingesting
hook files never waits for analysis, memory or speech. For each event:

1. **Significance.** Reports always count. Questions and permission waits count when they
   come from a session the owner is not looking at. Long background turns count when
   the frequency setting is "chatty". Ordinary replies in the session the owner is typing
   into are never narrated, because the owner is already reading them.
2. **Analysis.** ``digest.analyse`` runs deterministically, with no model call.
3. **Knowledge.** Reports are archived and condensed into memory *before* anything is
   said. A line about saving is only spoken after storage is confirmed.
4. **Commentary.** A short line from templates grounded in the digest. Humour is
   optional and only appears when the facts support it. Nothing is claimed as verified.
5. **Speech.** The line is sent to the shared queue on the ``companion`` channel, with
   cooldowns and quiet hours. Captions are kept even when speech is muted.

Any failure is logged and dropped. The agents never notice.
"""

from __future__ import annotations

import hashlib
import logging
import queue
import threading
import time
from collections import deque
from datetime import datetime
from typing import Any, Callable, Optional

from config import config

from agent.companion import knowledge
from agent.companion.digest import Digest, analyse

logger = logging.getLogger(__name__)

SESSION_COOLDOWN = 90.0
GLOBAL_COOLDOWN = 20.0
LONG_TURN = 5 * 60.0


def _setting(name: str, default: Any) -> Any:
    value = getattr(config, name, default)
    return default if value is None or value == "" else value


def in_quiet_hours(spec: str, now: Optional[datetime] = None) -> bool:
    """`spec` like "22:00-08:00"; empty means never quiet."""

    try:
        start, end = [datetime.strptime(part.strip(), "%H:%M").time() for part in str(spec or "").split("-", 1)]
    except ValueError:
        return False
    current = (now or datetime.now()).time()
    return start <= current < end if start <= end else (current >= start or current < end)


def _pick(options: list[str], seed: str) -> str:
    return options[int(hashlib.sha256(seed.encode()).hexdigest(), 16) % len(options)]


def _brief(text: str, words: int = 12) -> str:
    parts = str(text or "").replace("\n", " ").split()
    return " ".join(parts[:words]).rstrip(",;:") + ("…" if len(parts) > words else "")


def compose(event: dict[str, Any], digest: Digest, *, humor: int = 1, saved: bool = False) -> str:
    """One or two short spoken sentences, grounded in the event and its digest."""

    who = knowledge.label(event["agent"]).replace("Claude Code", "Claude")
    where = f" on {event['project']}" if event.get("project") else ""
    seed = event["id"]
    kind = event["kind"]
    if kind == "needs_permission":
        return f"{who} is waiting for your permission{where}."
    if kind == "waiting_input" or (kind == "turn_completed" and digest.kind == "question"):
        return f"{who} has a question for you{where}: {_brief(digest.headline or event.get('text', ''), 14)}"
    if kind == "stage_completed":
        return f"{who} finished a stage{where}: {_brief(event.get('text', ''), 14)}"
    if digest.kind != "report":
        return f"{who} finished a long stretch of work{where}. {_brief(digest.headline, 12)}".strip()
    lines: list[str] = []
    outcome = digest.outcome
    tests = f" {digest.tests_passed} tests passing by its account." if digest.tests_passed and not digest.tests_failed else ""
    if outcome == "success":
        lines.append(_pick([f"{who} finished a report{where}.", f"{who} wrapped up{where}.",
                            f"Report in from {who}{where}."], seed) + tests)
        if digest.results:
            lines.append(f"Headline: {_brief(digest.results[0])}.")
        if humor >= 2 and digest.tests_passed and not digest.open:
            lines.append("Everything passed, which is honestly a little suspicious.")
    elif outcome == "failed":
        lines.append(f"{who} hit a wall{where}: {_brief(digest.failures[0])}.")
        if humor >= 1:
            lines.append(_pick(["That didn't work, but we learned something. On to the next one.",
                                "Not this time. The notes are worth keeping, though."], seed))
    else:
        done, still = len(digest.results), len(digest.open)
        lines.append(f"{who} reported back{where}: {done} thing{'s' if done != 1 else ''} done"
                     + (f", {still} still open" if still else "")
                     + (f", and {len(digest.failures)} that didn't work" if digest.failures else "") + ".")
        focus = digest.open[0] if digest.open else (digest.failures[0] if digest.failures else "")
        if focus:
            lines.append(f"Open: {_brief(focus)}.")
    if digest.prs:
        lines.append(f"PR {', '.join(digest.prs[:2])} is up.")
    if digest.asks_owner:
        lines.append("It's waiting on you.")
    if saved:
        lines.append(_pick(["I saved the key points for Echo.", "Echo can use what it found from now on.",
                            "The important parts are saved for Echo."], seed + "s"))
    text = " ".join(lines)
    return text.replace("..", ".")


class Commentary:
    def __init__(self, *, speak: Optional[Callable[..., dict]] = None, focus: Optional[Callable[[], dict]] = None,
                 remember: Optional[Callable[..., dict]] = None, archive: Optional[Callable[..., Any]] = None):
        self._speak = speak
        self._focus = focus
        self._remember = remember or knowledge.remember
        self._archive = archive or knowledge.archive
        self._queue: queue.Queue = queue.Queue(maxsize=1000)
        self.feed: deque[dict[str, Any]] = deque(maxlen=100)
        self._last_session: dict[str, float] = {}
        self._last_any = 0.0
        self._prompt_at: dict[str, float] = {}
        self._listeners: list[Callable[[dict[str, Any]], None]] = []
        self._thread = threading.Thread(target=self._run, name="echo-companion-commentary", daemon=True)
        self._thread.start()

    def submit(self, event: dict[str, Any]) -> None:
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            pass

    def on_item(self, callback: Callable[[dict[str, Any]], None]) -> Callable[[], None]:
        self._listeners.append(callback)

        def unsubscribe() -> None:
            if callback in self._listeners:
                self._listeners.remove(callback)
        return unsubscribe

    def drain(self, timeout: float = 5.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._queue.unfinished_tasks == 0:
                return True
            time.sleep(0.02)
        return False

    def _run(self) -> None:
        while True:
            event = self._queue.get()
            try:
                self.handle(event)
            except Exception:
                logger.warning("Echo Companion could not process an event", exc_info=True)
            finally:
                self._queue.task_done()

    # ---- policy ---------------------------------------------------------------
    def _interacting(self, event: dict[str, Any]) -> bool:
        try:
            focus = self._focus() if self._focus else {}
        except Exception:
            focus = {}
        return focus.get("agent") == event["agent"] and focus.get("session_id") == event["session_id"]

    def handle(self, event: dict[str, Any]) -> Optional[dict[str, Any]]:
        if not bool(_setting("companion_enabled", False)):
            return None
        key = f"{event['agent']}:{event['session_id']}"
        kind = event["kind"]
        if kind == "prompt":
            self._prompt_at[key] = float(event["at"])
            return None
        if kind not in {"turn_completed", "needs_permission", "waiting_input", "stage_completed", "report_ready"}:
            return None
        digest = analyse(event.get("text", "")) if kind in {"turn_completed", "report_ready"} else Digest()
        if kind == "report_ready":
            digest.kind = "report"
        frequency = str(_setting("companion_frequency", "milestones"))
        interacting = self._interacting(event)
        long_turn = kind == "turn_completed" and float(event["at"]) - self._prompt_at.get(key, float(event["at"])) >= LONG_TURN
        if digest.kind == "report":
            reason = "report"
        elif kind in {"needs_permission", "waiting_input"} or digest.kind == "question":
            reason = "needs_you" if not interacting and frequency != "quiet" else ""
        elif kind == "stage_completed":
            reason = "stage" if frequency != "quiet" else ""
        elif long_turn and not interacting and frequency == "chatty":
            reason = "long_turn"
        else:
            reason = ""
        if not reason:
            return None
        saved = False
        stored: dict[str, Any] = {}
        archived = ""
        if reason == "report" and bool(_setting("companion_knowledge", True)):
            try:
                archived = str(self._archive(event, digest))
                stored = self._remember(event, digest)
                saved = bool(stored.get("stored"))
            except Exception as exc:
                stored = {"stored": False, "reason": f"{type(exc).__name__}: {exc}"[:200]}
                logger.warning("Echo Companion could not store a report", exc_info=True)
        text = compose(event, digest, humor=int(_setting("companion_humor", 1)), saved=saved)
        now = time.time()
        spoken, why_silent = False, ""
        if not bool(_setting("companion_speak", True)):
            why_silent = "speech off"
        elif in_quiet_hours(str(_setting("companion_quiet_hours", ""))):
            why_silent = "quiet hours"
        elif reason != "report" and (now - self._last_session.get(key, 0) < SESSION_COOLDOWN or now - self._last_any < GLOBAL_COOLDOWN):
            why_silent = "cooldown"
        elif self._speak is not None:
            result = self._speak(text, channel="companion", source=event["agent"],
                                 voice=str(_setting("companion_voice", "") or ""))
            spoken = result.get("status") == "queued"
            why_silent = "" if spoken else str(result.get("error") or result.get("status"))
            if spoken:
                self._last_session[key] = now
                self._last_any = now
        item = {
            "id": event["id"], "at": now, "agent": event["agent"], "label": knowledge.label(event["agent"]),
            "session_id": event["session_id"], "project": event.get("project", ""), "reason": reason,
            "text": text, "spoken": spoken, "silent_because": why_silent, "outcome": digest.outcome,
            "digest": digest.public() if reason == "report" else {"kind": digest.kind, "headline": digest.headline},
            "memory": stored, "archived": archived,
        }
        self.feed.append(item)
        for callback in list(self._listeners):
            try:
                callback(item)
            except Exception:
                pass
        return item


_instance: Optional[Commentary] = None
_lock = threading.Lock()


def get_commentary() -> Commentary:
    global _instance
    with _lock:
        if _instance is None:
            from agent.companion.observe import activity, get_companion_store
            from agent.speech_output import get_speech_output

            store = get_companion_store()
            _instance = Commentary(
                speak=lambda text, **kw: get_speech_output().speak(text, **kw),
                focus=lambda: activity(store, include_echo=False, ingest=False)["focus"],
            )
            store.on_event(_instance.submit)
        return _instance
