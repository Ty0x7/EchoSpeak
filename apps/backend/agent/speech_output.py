"""One speaker for everything EchoSpeak says out loud.

Echo's replies, Echo Companion's commentary and voice previews all go through this
queue, so two voices never talk over each other. Requests are prioritised, deduplicated
and cancellable. Each one is synthesised sentence-group by sentence-group with the
selected local provider (agent.voice_runtime) and played on this machine's audio output
(PipeWire's pw-play, or paplay/aplay). The next chunk is synthesised while the current
one plays.

Every state change becomes an event. A `speech.chunk_started` event carries the chunk's
duration and an amplitude envelope (50 samples per second), so an animated face can move
its mouth with the audio that is actually playing.

Nothing here raises into the caller: a failed or unavailable voice is reported as a
`speech.finished` event with status "failed", and the agent that asked keeps working.
"""

from __future__ import annotations

import heapq
import itertools
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import wave
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from config import config

logger = logging.getLogger(__name__)

CHANNELS = ("echo", "companion", "preview")
# Echo answering the owner outranks commentary about other agents.
DEFAULT_PRIORITY = {"preview": 90, "echo": 80, "companion": 40}
MAX_TEXT = 6000
CHUNK_CHARS = 280
ENVELOPE_HZ = 50
DEDUP_SECONDS = 90.0
COMPANION_STALE_SECONDS = 120.0


@dataclass(order=False)
class SpeechRequest:
    text: str
    channel: str = "echo"
    priority: int = 0
    source: str = ""
    provider_id: str = ""
    voice: str = ""
    speed: float = 0.0
    interrupt: bool = False
    id: str = field(default_factory=lambda: f"speech-{uuid.uuid4().hex[:16]}")
    created_at: float = field(default_factory=time.time)
    status: str = "queued"
    error: str = ""

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id, "channel": self.channel, "priority": self.priority, "source": self.source,
            "text": self.text, "status": self.status, "error": self.error, "created_at": self.created_at,
        }


def split_for_speech(text: str, limit: int = CHUNK_CHARS) -> list[str]:
    """Sentence groups of at most `limit` characters; long sentences split on commas, then words."""

    clean = re.sub(r"```.*?```", " ", str(text or ""), flags=re.S)  # code is not read aloud
    clean = re.sub(r"`([^`]*)`", r"\1", clean)
    clean = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", clean)  # markdown links -> their text
    clean = re.sub(r"[*_#>|]+", " ", clean)
    clean = re.sub(r"\s+", " ", clean).strip()
    if not clean:
        return []
    sentences = re.split(r"(?<=[.!?;:])\s+", clean)
    pieces: list[str] = []
    for sentence in sentences:
        while len(sentence) > limit:
            cut = sentence.rfind(", ", 0, limit)
            cut = cut + 1 if cut > limit // 3 else sentence.rfind(" ", 0, limit)
            cut = cut if cut > 0 else limit
            pieces.append(sentence[:cut].strip())
            sentence = sentence[cut:].strip()
        if sentence:
            pieces.append(sentence)
    # The first sentence is spoken on its own so audio starts quickly; the rest is packed.
    chunks: list[str] = pieces[:1]
    for piece in pieces[1:]:
        if len(chunks) > 1 and len(chunks[-1]) + 1 + len(piece) <= limit:
            chunks[-1] = f"{chunks[-1]} {piece}"
        else:
            chunks.append(piece)
    return chunks


def wav_envelope(path: Path, hz: int = ENVELOPE_HZ) -> tuple[float, list[float]]:
    """(duration seconds, amplitude envelope 0..1) for a 16-bit PCM WAV."""

    import array
    import math

    with wave.open(str(path), "rb") as handle:
        rate, width, channels, frames = handle.getframerate(), handle.getsampwidth(), handle.getnchannels(), handle.getnframes()
        raw = handle.readframes(frames)
    duration = frames / float(rate or 1)
    if width != 2 or not raw:
        return duration, []
    samples = array.array("h")
    samples.frombytes(raw[: len(raw) - (len(raw) % 2)])
    if sys.byteorder == "big":
        samples.byteswap()
    step = max(1, int(rate / hz) * channels)
    values: list[float] = []
    for start in range(0, len(samples), step):
        window = samples[start:start + step]
        if not window:
            break
        values.append(math.sqrt(sum(v * v for v in window) / len(window)) / 32768.0)
    peak = max(values) if values else 0.0
    if peak > 0:
        values = [round(min(1.0, v / peak), 3) for v in values]
    return duration, values


def output_devices() -> list[dict[str, Any]]:
    """Audio outputs PipeWire/PulseAudio knows about: name, description, default."""

    pactl = shutil.which("pactl")
    if not pactl:
        return []
    try:
        default = subprocess.run([pactl, "get-default-sink"], capture_output=True, text=True, timeout=5).stdout.strip()
        raw = subprocess.run([pactl, "-f", "json", "list", "sinks"], capture_output=True, text=True, timeout=5).stdout
        rows = json.loads(raw or "[]")
    except (OSError, subprocess.SubprocessError, ValueError):
        return []
    return [
        {"name": row.get("name", ""), "description": row.get("description", "") or row.get("name", ""),
         "default": row.get("name") == default, "muted": bool(row.get("mute"))}
        for row in rows if row.get("name")
    ]


def _player_command(path: Path, device: str, volume: float) -> list[str]:
    volume = min(1.5, max(0.0, float(volume)))
    if shutil.which("pw-play"):
        cmd = ["pw-play", "--volume", f"{volume:.2f}"]
        if device:
            cmd += ["--target", device]
        return cmd + [str(path)]
    if shutil.which("paplay"):
        cmd = ["paplay", f"--volume={int(volume * 65536)}"]
        if device:
            cmd.append(f"--device={device}")
        return cmd + [str(path)]
    if shutil.which("aplay"):
        return ["aplay", "-q", str(path)]
    raise RuntimeError("No audio player found (pw-play, paplay or aplay)")


Synthesizer = Callable[[str, Path, SpeechRequest], None]
Player = Callable[[Path, "SpeechOutput"], int]


def _default_synthesizer(text: str, output: Path, request: SpeechRequest) -> None:
    from agent.voice_runtime import synthesize_voice_audio

    settings: dict[str, Any] = {}
    if request.voice:
        settings["voice_id"] = request.voice
    if request.speed:
        settings["speed"] = request.speed
    synthesize_voice_audio(text, output, provider_id=request.provider_id, settings=settings,
                           cancelled=lambda: request.status == "cancelled")


class SpeechOutput:
    """The process-wide speech queue. Use get_speech_output()."""

    def __init__(self, *, synthesizer: Optional[Synthesizer] = None, player: Optional[Player] = None,
                 workdir: Optional[Path] = None):
        self._synthesize = synthesizer or _default_synthesizer
        self._play = player or self._play_subprocess
        self._workdir = Path(workdir or tempfile.mkdtemp(prefix="echospeak-speech-"))
        self._cv = threading.Condition()
        self._heap: list[tuple[int, int, SpeechRequest]] = []
        self._order = itertools.count()
        self._current: Optional[SpeechRequest] = None
        self._process: Optional[subprocess.Popen] = None
        self._recent: deque[tuple[float, str, str]] = deque(maxlen=64)
        self._events: deque[dict[str, Any]] = deque(maxlen=200)
        self._seq = itertools.count(1)
        self._subscribers: list[Callable[[dict[str, Any]], None]] = []
        self._muted: set[str] = set()
        self._worker = threading.Thread(target=self._run, name="echospeak-speech", daemon=True)
        self._worker.start()

    # ---- public API -------------------------------------------------------------
    def speak(self, text: str, *, channel: str = "echo", priority: Optional[int] = None, source: str = "",
              provider_id: str = "", voice: str = "", speed: float = 0.0, interrupt: bool = False) -> dict[str, Any]:
        channel = channel if channel in CHANNELS else "echo"
        body = str(text or "").strip()[:MAX_TEXT]
        request = SpeechRequest(text=body, channel=channel, source=str(source or "")[:80],
                                priority=int(DEFAULT_PRIORITY[channel] if priority is None else priority),
                                provider_id=provider_id, voice=voice, speed=float(speed or 0.0), interrupt=interrupt)
        if not body:
            request.status, request.error = "skipped", "empty"
            return request.public()
        if channel in self._muted:
            request.status, request.error = "skipped", "muted"
            self._emit("speech.skipped", request)
            return request.public()
        key = re.sub(r"\W+", " ", body.lower()).strip()
        now = time.time()
        with self._cv:
            if any(ch == channel and k == key and now - at < DEDUP_SECONDS for at, ch, k in self._recent):
                request.status, request.error = "skipped", "duplicate"
                self._emit("speech.skipped", request)
                return request.public()
            self._recent.append((now, channel, key))
            if interrupt:
                self._cancel_locked(lambda r: r.priority <= request.priority)
            heapq.heappush(self._heap, (-request.priority, next(self._order), request))
            self._emit("speech.queued", request)
            self._cv.notify_all()
        return request.public()

    def cancel(self, *, request_id: str = "", channel: str = "") -> int:
        with self._cv:
            return self._cancel_locked(lambda r: (not request_id or r.id == request_id) and (not channel or r.channel == channel))

    def set_muted(self, channel: str, muted: bool) -> None:
        if channel not in CHANNELS:
            return
        with self._cv:
            (self._muted.add if muted else self._muted.discard)(channel)
        if muted:
            self.cancel(channel=channel)

    def state(self) -> dict[str, Any]:
        with self._cv:
            return {
                "speaking": self._current.public() if self._current else None,
                "queued": [item[2].public() for item in sorted(self._heap)],
                "muted": sorted(self._muted),
                "events": list(self._events)[-30:],
            }

    def events_since(self, seq: int) -> list[dict[str, Any]]:
        with self._cv:
            return [event for event in self._events if event["seq"] > seq]

    def subscribe(self, callback: Callable[[dict[str, Any]], None]) -> Callable[[], None]:
        with self._cv:
            self._subscribers.append(callback)

        def unsubscribe() -> None:
            with self._cv:
                if callback in self._subscribers:
                    self._subscribers.remove(callback)
        return unsubscribe

    def wait_idle(self, timeout: float = 30.0) -> bool:
        deadline = time.time() + timeout
        with self._cv:
            while self._heap or self._current is not None:
                left = deadline - time.time()
                if left <= 0:
                    return False
                self._cv.wait(min(left, 0.2))
        return True

    # ---- internals --------------------------------------------------------------
    def _cancel_locked(self, match: Callable[[SpeechRequest], bool]) -> int:
        count = 0
        kept = []
        for item in self._heap:
            if match(item[2]):
                item[2].status = "cancelled"
                self._emit("speech.finished", item[2])
                count += 1
            else:
                kept.append(item)
        self._heap = kept
        heapq.heapify(self._heap)
        if self._current is not None and match(self._current):
            self._current.status = "cancelled"
            count += 1
            if self._process is not None and self._process.poll() is None:
                try:
                    self._process.terminate()
                except OSError:
                    pass
        self._cv.notify_all()
        return count

    def _emit(self, kind: str, request: SpeechRequest, **extra: Any) -> None:
        event = {"seq": next(self._seq), "type": kind, "at": time.time(), "request": request.public(), **extra}
        self._events.append(event)
        for callback in list(self._subscribers):
            try:
                callback(event)
            except Exception:  # a broken listener never stops speech
                logger.debug("speech event listener failed", exc_info=True)

    def _next(self) -> SpeechRequest:
        with self._cv:
            while True:
                while self._heap:
                    _, _, request = heapq.heappop(self._heap)
                    if request.status == "cancelled":
                        continue
                    if request.channel == "companion" and time.time() - request.created_at > COMPANION_STALE_SECONDS:
                        request.status, request.error = "skipped", "stale"
                        self._emit("speech.finished", request)
                        continue
                    request.status = "speaking"
                    self._current = request
                    return request
                self._cv.wait()

    def _run(self) -> None:
        while True:
            request = self._next()
            try:
                self._speak(request)
                if request.status == "speaking":
                    request.status = "completed"
            except Exception as exc:
                if request.status != "cancelled":
                    request.status, request.error = "failed", str(exc)[:300]
                    logger.warning("Speech failed: %s", exc)
            finally:
                with self._cv:
                    self._current = None
                    self._process = None
                    self._emit("speech.finished", request)
                    self._cv.notify_all()

    def _speak(self, request: SpeechRequest) -> None:
        chunks = split_for_speech(request.text)
        self._emit("speech.started", request, chunks=len(chunks))
        pending: dict[int, tuple[threading.Thread, Path, list[BaseException]]] = {}

        def prepare(index: int) -> None:
            if index >= len(chunks) or index in pending:
                return
            path = self._workdir / f"{request.id}-{index}.wav"
            errors: list[BaseException] = []

            def work() -> None:
                try:
                    self._synthesize(chunks[index], path, request)
                except BaseException as exc:  # reported when the chunk is due
                    errors.append(exc)
            thread = threading.Thread(target=work, name=f"echospeak-tts-{index}", daemon=True)
            thread.start()
            pending[index] = (thread, path, errors)

        try:
            prepare(0)
            for index in range(len(chunks)):
                if request.status == "cancelled":
                    return
                thread, path, errors = pending[index]
                thread.join()
                if request.status == "cancelled":
                    return
                if errors:
                    raise RuntimeError(str(errors[0]) or type(errors[0]).__name__)
                prepare(index + 1)
                duration, envelope = wav_envelope(path)
                self._emit("speech.chunk_started", request, chunk=index, text=chunks[index],
                           duration=round(duration, 3), envelope=envelope, envelope_hz=ENVELOPE_HZ)
                code = self._play(path, self)
                self._emit("speech.chunk_finished", request, chunk=index, exit_code=code)
                if code != 0 and request.status != "cancelled":
                    raise RuntimeError(f"Audio playback failed (player exit {code})")
        finally:
            for thread, path, _ in pending.values():
                thread.join(timeout=30)
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass

    def _play_subprocess(self, path: Path, _owner: "SpeechOutput") -> int:
        command = _player_command(path, str(getattr(config, "voice_output_device", "") or ""),
                                  float(getattr(config, "voice_volume", 1.0) or 1.0))
        with self._cv:
            if self._current is not None and self._current.status == "cancelled":
                return 0
            self._process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            process = self._process
        _, stderr = process.communicate()
        if process.returncode not in (0, -15) and stderr:
            logger.warning("Audio player said: %s", stderr.decode("utf-8", "replace")[:300])
        return 0 if process.returncode == -15 else process.returncode


_instance: Optional[SpeechOutput] = None
_instance_lock = threading.Lock()


def get_speech_output() -> SpeechOutput:
    global _instance
    with _instance_lock:
        if _instance is None:
            _instance = SpeechOutput()
        return _instance
