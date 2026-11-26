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

from agent.companion import knowledge, soul
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
        return soul.phrase("needs_you", seed, "{agent} is waiting for your permission{where}.", agent=who, where=where)
    if kind == "waiting_input":  # idle at its prompt, not asking anything in particular
        return soul.phrase("waiting", seed, "{agent} is waiting for you{where}.", agent=who, where=where)
    if kind == "turn_completed" and digest.kind == "question":
        opener = soul.phrase("question", seed, "{agent} has a question for you{where}.", agent=who, where=where).rstrip(".")
        return f"{opener}: {_brief(digest.headline or event.get('text', ''), 14)}"
    if kind == "stage_completed":
        return f"{who} finished a stage{where}: {_brief(event.get('text', ''), 14)}"
    if digest.kind != "report":
        return f"{who} finished a long stretch of work{where}. {_brief(digest.headline, 12)}".strip()
    lines: list[str] = []
    outcome = digest.outcome
    measured = str((event.get("meta") or {}).get("measured") or "")
    if measured in {"verified", "checks_pass"}:
        tests = " EchoSpeak's own checks pass."
    elif digest.tests_passed and not digest.tests_failed:
        tests = f" {digest.tests_passed} tests passing by its account."
    else:
        tests = ""
    if outcome == "success":
        lines.append(soul.phrase("success", seed, "{agent} finished a report{where}.", agent=who, where=where,
                                 project=event.get("project", ""), headline=_brief(digest.headline)) + tests)
        if digest.results:
            lines.append(f"Headline: {_brief(digest.results[0])}.")
        if humor >= 2 and digest.tests_passed and not digest.open and measured not in {"verified", "checks_pass"}:
            lines.append(soul.phrase("all_green", seed, "Everything passed, which is honestly a little suspicious."))
    elif outcome == "failed":
        lines.append(f"{who} hit a wall{where}: {_brief(digest.failures[0])}.")
        if humor >= 1:
            lines.append(soul.phrase("failure", seed, "That didn't work, but we learned something. On to the next one."))
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
        lines.append(soul.phrase("saved", seed, "I saved the key points for Echo."))
    text = " ".join(lines)
    return text.replace("..", ".")


class Commentary:
    def __init__(self, *, speak: Optional[Callable[..., dict]] = None, focus: Optional[Callable[[], dict]] = None,
                 remember: Optional[Callable[..., dict]] = None, archive: Optional[Callable[..., Any]] = None,
                 authorize: Optional[Callable[..., dict]] = None, mark: Optional[Callable[[str], None]] = None):
        self._speak = speak
        self._focus = focus
        self._remember = remember or knowledge.remember
        self._archive = archive or knowledge.archive
        self._authorize = authorize or knowledge.authorize
        self._mark = mark
        self._queue: queue.Queue = queue.Queue(maxsize=1000)
        self.feed: deque[dict[str, Any]] = deque(maxlen=100)
        self._last_session: dict[str, float] = {}
        self._last_any = 0.0
        self._last_greeting = 0.0
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

    def handle(self, event: dict[str, Any], *, catching_up: bool = False) -> Optional[dict[str, Any]]:
        try:
            return self._handle(event, catching_up=catching_up)
        finally:
            if self._mark and event.get("kind") in {"turn_completed", "report_ready"} and bool(_setting("companion_enabled", False)):
                try:
                    self._mark(event["id"])
                except Exception:
                    pass

    def catch_up(self, events: list[dict[str, Any]]) -> int:
        """Keep reports that arrived while the backend was down. They are not spoken: the news is stale."""
        done = 0
        for event in events:
            if self.handle(event, catching_up=True):
                done += 1
        return done

    def _handle(self, event: dict[str, Any], *, catching_up: bool = False) -> Optional[dict[str, Any]]:
        if not bool(_setting("companion_enabled", False)):
            return None
        key = f"{event['agent']}:{event['session_id']}"
        kind = event["kind"]
        if kind == "prompt":
            self._prompt_at[key] = float(event["at"])
            return None
        if catching_up and kind not in {"turn_completed", "report_ready"}:
            return None
        if kind not in {"turn_completed", "needs_permission", "waiting_input", "stage_completed", "report_ready"}:
            return None
        digest = analyse(event.get("text", "")) if kind in {"turn_completed", "report_ready"} else Digest()
        if kind == "report_ready":
            digest.kind = "report"
        from agent.companion import narrate

        now = time.time()
        interacting = self._interacting(event)
        facts = narrate.facts(event, digest, interacting=interacting, now=now)
        reason = "" if catching_up else narrate.worth_saying(facts, str(_setting("companion_frequency", "milestones")))
        # Knowledge doesn't depend on speaking: a finished report is kept (where authorised) even in silence.
        saved, stored, archived = False, {}, ""
        # Only knowledge from *outside* EchoSpeak is imported. Echo and its named agents already keep their own
        # conversations in memory; re-importing their replies would copy imported reports back in (a feedback loop).
        external = str((event.get("meta") or {}).get("via") or "") != "native"
        if external and digest.kind == "report" and kind in {"turn_completed", "report_ready"} and bool(_setting("companion_knowledge", True)):
            try:
                decision = self._authorize(event)
                if decision.get("allowed"):
                    archived = str(self._archive(event, digest))
                    stored = self._remember(event, digest)
                else:
                    stored = {"stored": False, "reason": decision.get("reason", "")}
                saved = bool(stored.get("stored"))
            except Exception as exc:
                stored = {"stored": False, "reason": f"{type(exc).__name__}: {exc}"[:200]}
                logger.warning("Echo Companion could not store a report", exc_info=True)
        if not reason and not catching_up:
            if not stored:
                return None  # nothing worth saying and nothing kept: stay quiet, leave no trace
        greet = bool(facts["name"]) and now - self._last_greeting > 20 * 60
        text = narrate.compose(facts, reason or "report", greet=greet, saved=saved and reason == "report")
        style = {"style": "templates"}
        if reason and str(_setting("companion_soul_style", "templates")) == "local_model":
            text, style = narrate.hybrid(facts, reason, text)
        spoken, why_silent = False, ""
        if catching_up:
            why_silent = "arrived while EchoSpeak was closed"
        elif not reason:
            why_silent = "nothing worth saying"
        elif not bool(_setting("companion_speak", True)):
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
                if greet:
                    self._last_greeting = now
        item = {
            "id": event["id"], "at": now, "agent": event["agent"], "label": knowledge.label(event["agent"]),
            "session_id": event["session_id"], "project": event.get("project", ""), "reason": reason,
            "text": text, "spoken": spoken, "silent_because": why_silent, "outcome": digest.outcome,
            "digest": digest.public() if reason == "report" else {"kind": digest.kind, "headline": digest.headline},
            "memory": stored, "archived": archived, "wording": style,
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
                mark=store.mark_handled,
            )
            store.on_event(_instance.submit)
            pending = store.unhandled(time.time() - 86400)
            if pending:
                threading.Thread(target=_instance.catch_up, args=(pending,), name="echo-companion-catch-up", daemon=True).start()
        return _instance
