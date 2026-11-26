"""Shared speech output: one queue, priorities, dedup, cancel, envelopes, failures stay contained."""

from __future__ import annotations

import sys
import threading
import time
import wave
from pathlib import Path

import pytest

from agent import speech_output
from agent.speech_output import SpeechOutput, split_for_speech, wav_envelope


def _write_wav(path: Path, seconds: float = 0.2, rate: int = 8000, loud: bool = True) -> None:
    import array
    import math

    frames = array.array("h", (int(12000 * math.sin(i / 5.0)) if loud else 0 for i in range(int(rate * seconds))))
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(frames.tobytes())


class Recorder:
    def __init__(self, play_seconds: float = 0.0, fail_on: str = ""):
        self.played: list[str] = []
        self.synth: list[str] = []
        self.play_seconds = play_seconds
        self.fail_on = fail_on
        self.release = threading.Event()
        self.release.set()

    def synthesize(self, text, path, request):
        if self.fail_on and self.fail_on in text:
            raise RuntimeError("voice unavailable")
        self.synth.append(text)
        _write_wav(path)

    def play(self, path, owner):
        self.played.append(owner._current.channel + ":" + path.name)
        self.release.wait(5)
        time.sleep(self.play_seconds)
        return 0


def _output(tmp_path, recorder):
    return SpeechOutput(synthesizer=recorder.synthesize, player=recorder.play, workdir=tmp_path)


def test_split_strips_code_and_markdown_and_starts_with_a_short_chunk():
    text = "Done. I fixed the **config** in [the file](http://x). ```print(1)``` " + "More words here. " * 40
    chunks = split_for_speech(text)
    assert chunks[0] == "Done."
    assert "print(1)" not in " ".join(chunks) and "**" not in " ".join(chunks) and "http" not in " ".join(chunks)
    assert all(len(chunk) <= speech_output.CHUNK_CHARS for chunk in chunks)
    assert "the file" in chunks[1]


def test_envelope_reports_duration_and_normalised_amplitude(tmp_path):
    path = tmp_path / "a.wav"
    _write_wav(path, seconds=0.5)
    duration, envelope = wav_envelope(path)
    assert duration == pytest.approx(0.5, abs=0.01)
    assert len(envelope) == pytest.approx(25, abs=1) and max(envelope) == 1.0
    _write_wav(path, seconds=0.2, loud=False)
    assert set(wav_envelope(path)[1]) == {0.0}


def test_echo_outranks_queued_companion_commentary(tmp_path):
    rec = Recorder()
    rec.release.clear()
    out = _output(tmp_path, rec)
    out.speak("First thing.", channel="companion", source="claude-code")
    time.sleep(0.1)  # first request is now playing (held)
    out.speak("Commentary waits.", channel="companion")
    out.speak("Echo answers first.", channel="echo")
    rec.release.set()
    assert out.wait_idle(5)
    assert [p.split(":")[0] for p in rec.played] == ["companion", "echo", "companion"]


def test_duplicate_commentary_is_skipped_and_mute_drops_a_channel(tmp_path):
    rec = Recorder()
    rec.release.clear()
    out = _output(tmp_path, rec)
    assert out.speak("Claude finished.", channel="companion")["status"] == "queued"
    assert out.speak("Claude finished!", channel="companion")["status"] == "skipped"
    out.set_muted("companion", True)
    assert out.speak("Something new.", channel="companion")["error"] == "muted"
    assert out.speak("Echo still talks.", channel="echo")["status"] == "queued"
    rec.release.set()
    assert out.wait_idle(5)
    # Muting also stopped the companion line that was already playing.
    finished = {e["request"]["text"]: e["request"]["status"] for e in out.state()["events"] if e["type"] == "speech.finished"}
    assert finished == {"Claude finished.": "cancelled", "Echo still talks.": "completed"}


def test_cancel_stops_current_and_queued_speech(tmp_path):
    rec = Recorder()
    rec.release.clear()
    out = _output(tmp_path, rec)
    first = out.speak("One. Two. Three.", channel="echo")
    out.speak("Queued.", channel="companion")
    time.sleep(0.1)
    assert out.cancel(channel="") == 2
    rec.release.set()
    assert out.wait_idle(5)
    finished = {e["request"]["id"]: e["request"]["status"] for e in out.state()["events"] if e["type"] == "speech.finished"}
    assert finished[first["id"]] == "cancelled"
    assert len(rec.played) == 1  # the remaining chunks of the cancelled request never played


def test_interrupt_replaces_lower_priority_speech(tmp_path):
    rec = Recorder()
    rec.release.clear()
    out = _output(tmp_path, rec)
    out.speak("Long commentary.", channel="companion")
    time.sleep(0.1)
    out.speak("Stop, owner asked something.", channel="echo", interrupt=True)
    rec.release.set()
    assert out.wait_idle(5)
    statuses = [e["request"]["status"] for e in out.state()["events"] if e["type"] == "speech.finished"]
    assert statuses == ["cancelled", "completed"]


def test_failed_voice_is_reported_and_the_queue_keeps_working(tmp_path):
    rec = Recorder(fail_on="broken")
    out = _output(tmp_path, rec)
    out.speak("This is broken.", channel="companion")
    out.speak("This works.", channel="echo")
    assert out.wait_idle(5)
    finished = [e["request"] for e in out.state()["events"] if e["type"] == "speech.finished"]
    by_text = {r["text"]: r for r in finished}
    assert by_text["This is broken."]["status"] == "failed" and "voice unavailable" in by_text["This is broken."]["error"]
    assert by_text["This works."]["status"] == "completed"


def test_chunk_events_carry_envelope_for_lip_sync(tmp_path):
    rec = Recorder()
    out = _output(tmp_path, rec)
    seen = []
    out.subscribe(seen.append)
    out.speak("Hello there. Second sentence.", channel="companion")
    assert out.wait_idle(5)
    started = [e for e in seen if e["type"] == "speech.chunk_started"]
    assert len(started) == 2
    assert started[0]["duration"] > 0 and started[0]["envelope"] and started[0]["envelope_hz"] == 50


def test_stale_companion_commentary_is_dropped(tmp_path, monkeypatch):
    rec = Recorder()
    rec.release.clear()
    out = _output(tmp_path, rec)
    out.speak("Holding the speaker.", channel="echo")
    time.sleep(0.1)
    out.speak("Old news.", channel="companion")
    monkeypatch.setattr(speech_output, "COMPANION_STALE_SECONDS", 0.0)
    rec.release.set()
    assert out.wait_idle(5)
    finished = {e["request"]["text"]: e["request"] for e in out.state()["events"] if e["type"] == "speech.finished"}
    assert finished["Old news."]["error"] == "stale"


def test_player_command_targets_selected_device(monkeypatch):
    monkeypatch.setattr(speech_output.shutil, "which", lambda name: "/usr/bin/" + name if name == "pw-play" else None)
    cmd = speech_output._player_command(Path("/tmp/x.wav"), "alsa_output.usb", 0.5)
    assert cmd == ["pw-play", "--volume", "0.50", "--target", "alsa_output.usb", "/tmp/x.wav"]


def test_platform_default_is_kokoro_off_windows(monkeypatch):
    from agent import voice_runtime

    monkeypatch.setattr(voice_runtime.sys, "platform", "linux")
    assert voice_runtime.platform_default_tts_provider() == "kokoro-local"
    monkeypatch.setattr(voice_runtime.sys, "platform", "win32")
    assert voice_runtime.platform_default_tts_provider() == "windows-sapi"


def test_kokoro_status_reports_missing_pieces_without_loading(monkeypatch, tmp_path):
    from agent import kokoro_voice, voice_runtime

    monkeypatch.setattr(kokoro_voice, "default_model_dir", lambda: tmp_path)
    monkeypatch.setattr(kokoro_voice.config, "voice_kokoro_model_path", "", raising=False)
    monkeypatch.setattr(kokoro_voice.config, "voice_kokoro_voices_path", "", raising=False)
    monkeypatch.setattr(kokoro_voice, "runtime_installed", lambda: True)
    status = next(p for p in voice_runtime.voice_provider_statuses() if p.id == "kokoro-local")
    assert not status.operation_readiness["text_to_speech"] and "model is missing" in status.detail
    (tmp_path / "kokoro-v1.0.int8.onnx").write_bytes(b"x")
    (tmp_path / "voices-v1.0.bin").write_bytes(b"x")
    status = next(p for p in voice_runtime.voice_provider_statuses() if p.id == "kokoro-local")
    assert status.operation_readiness["text_to_speech"] and status.locality == "local"
