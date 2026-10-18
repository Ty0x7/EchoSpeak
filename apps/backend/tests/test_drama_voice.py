"""Speech integration contracts; tiny GGUF/WAV fixtures, no models or paid services."""
import json
import struct
import subprocess
import threading
import os
import sys
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent import drama_voice as drama
from agent import voice_runtime as voice
from agent import voice_transport as transport


def _string(value):
    encoded = value.encode()
    return struct.pack("<Q", len(encoded)) + encoded


def _model(path, family="fish_drama3"):
    path.write_bytes(b"GGUF" + struct.pack("<IQQ", 3, 0, 1) + _string("audiocpp.family") + struct.pack("<I", 8) + _string(family))
    return path


def _wav(path):
    with wave.open(str(path), "wb") as clip:
        clip.setnchannels(1); clip.setsampwidth(2); clip.setframerate(24000)
        clip.writeframes(b"\0\0" * 240)


@pytest.fixture
def configured(monkeypatch, tmp_path):
    root = tmp_path / "managed"
    root.mkdir()
    monkeypatch.setattr(drama, "ROOT", root)
    exe = tmp_path / "audiocpp_cli.exe"
    exe.write_bytes(b"fake executable; never run")
    model = _model(tmp_path / "drama.gguf")
    for key, value in {"voice_drama_runtime_path": str(exe), "voice_drama_model_path": str(model),
                       "voice_drama_backend": "cpu", "voice_drama_cpu_layers": 8, "voice_drama_device": 0,
                       "voice_drama_reference_path": "", "voice_drama_reference_text": ""}.items():
        monkeypatch.setattr(drama.config, key, value)
    (root / "runtime-verification.json").write_text(json.dumps({"file": drama._fingerprint(exe), "backends": ["cpu"]}))
    return exe, model


def test_passive_readiness_never_executes_or_loads_a_model(configured, monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("Readiness must not start a process"))
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("Readiness must not load a model"))
    assert drama.readiness()["ready"]
    assert next(p for p in voice.voice_provider_statuses() if p.id == "drama3-local").operation_readiness["text_to_speech"]


def test_changed_runtime_requires_verification_again(configured):
    exe, _ = configured
    exe.write_bytes(b"different executable")
    assert not drama.readiness()["ready"]
    assert not drama.readiness()["runtime_verified"]


@pytest.mark.parametrize("family", ["llama", "pocket_tts"])
def test_wrong_model_family_is_not_ready(configured, family):
    _, model = configured
    _model(model, family)
    assert not drama.readiness()["ready"]


def test_hostile_gguf_metadata_is_bounded(tmp_path):
    model = tmp_path / "bad.gguf"
    model.write_bytes(b"GGUF" + struct.pack("<IQQQ", 3, 0, 1, 2**62))
    with pytest.raises(drama.DramaError, match="limit"):
        drama.validate_model(model)


@pytest.mark.parametrize("path", [r"\\server\voice.wav", "https://example.com/model.gguf", "//host/share/file"])
def test_network_paths_are_rejected_before_access(path):
    with pytest.raises(drama.DramaError, match="network"):
        drama._local_file(path)


def test_old_runtime_is_rejected_without_loading_weights(configured, monkeypatch):
    seen = []
    def probe(args, **kwargs):
        seen.append(args)
        return SimpleNamespace(returncode=0, stdout=json.dumps({"loaders": {"pocket_tts": {}}}))
    monkeypatch.setattr(subprocess, "run", probe)
    with pytest.raises(drama.DramaError, match="does not support Drama"):
        drama.verify_runtime()
    assert seen[0][1:] == ["--list-loaders", "--json"]


def test_verified_runtime_reports_configuration_not_inference_success(configured, monkeypatch):
    def probe(args, **kwargs):
        return SimpleNamespace(returncode=0, stdout="backends: cpu,cuda\n" if args[-1] == "--version" else json.dumps({"loaders": {
            "fish_drama3": {"tasks": {"tts": ["offline", "streaming"]}}
        }}))
    monkeypatch.setattr(subprocess, "run", probe)
    result = drama.verify_runtime()
    assert result["verified"] and "Play a sample" in result["message"]


def test_cpu_only_runtime_does_not_claim_cuda_readiness(configured, monkeypatch):
    monkeypatch.setattr(drama.config, "voice_drama_backend", "cuda")
    assert not drama.readiness()["ready"]
    assert "CPU/CUDA" in drama.readiness()["detail"]


def test_inference_uses_the_verified_native_contract_and_plain_text(configured, monkeypatch, tmp_path):
    seen = []
    def run(args, cwd, **kwargs):
        seen.append(args)
        _wav(Path(args[args.index("--out") + 1]))
    monkeypatch.setattr(drama, "_run_checked", run)
    out = tmp_path / "speech.wav"
    assert voice.synthesize_voice_audio("A plain reply.", out, provider_id="drama3-local") == "drama3-local"
    args = seen[0]
    assert args[args.index("--family") + 1] == "fish_drama3"
    assert args[args.index("--text") + 1] == "A plain reply."
    assert args[args.index("--model-spec-override") + 1] == "embedded"
    assert not drama._ACTIVE


def test_reference_needs_a_matching_transcript_and_stays_local(configured, monkeypatch, tmp_path):
    reference = tmp_path / "reference.wav"
    _wav(reference)
    monkeypatch.setattr(drama.config, "voice_drama_reference_path", str(reference))
    with pytest.raises(drama.DramaError, match="transcript"):
        drama.synthesize("Hello", tmp_path / "out.wav")
    assert not drama._ACTIVE


@pytest.mark.parametrize("cancel", [False, True])
def test_governed_project_speech_keeps_authority_and_cancellation(configured, monkeypatch, tmp_path, cancel):
    from tests.test_voice_generation_runtime import _patch_authority
    from agent.media_library import MediaLibraryStore
    _patch_authority(monkeypatch, voice, tmp_path, project_id="project-1", tool="voice_synthesize_speech", permission="voice_actions")
    jobs = voice.VoiceJobStore(tmp_path / "artifact-jobs")
    media = MediaLibraryStore(tmp_path / "media")
    monkeypatch.setattr(voice, "get_media_library_store", lambda: media)
    def run(args, cwd, **kwargs):
        _wav(Path(args[args.index("--out") + 1]))
        if cancel:
            pending = jobs.list(session_id="session-1")[0]
            pending.cancellation_requested = True
            jobs.save(pending)
    monkeypatch.setattr(drama, "_run_checked", run)
    request = voice.VoiceJob(session_id="session-1", project_id="project-1", operation="text_to_speech",
                             provider_id="drama3-local", text="Project speech", idempotency_key="native-project")
    result = voice.submit_voice_job(request, store=jobs)
    assert result.status == ("cancelled" if cancel else "completed")
    assert bool(result.output_asset_id) is not cancel
    if not cancel:
        assert media.get(result.output_asset_id).provider == "drama3-local"
    monkeypatch.setattr(voice.config, "allow_voice_actions", False)
    with pytest.raises(voice.VoiceRuntimeError, match="configuration blocks"):
        voice.submit_voice_job(request, store=jobs)


def test_cancel_before_launch_cannot_start_a_worker(monkeypatch, tmp_path):
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("Cancelled work must not start"))
    with pytest.raises(InterruptedError):
        drama._run_checked(["never"], tmp_path, cancel=lambda: True, timeout=10, output=tmp_path / "log")


def test_running_worker_is_killed_on_cancel(monkeypatch, tmp_path):
    class Process:
        returncode = None
        pid = 123456
        def poll(self): return self.returncode
        def kill(self): self.returncode = -9
        def wait(self, **kwargs): return self.returncode
    process = Process()
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: process)
    monkeypatch.setattr(drama, "_attach_worker_job", lambda pid: None)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0))
    calls = iter([False, True])
    with pytest.raises(InterruptedError):
        drama._run_checked(["native"], tmp_path, cancel=lambda: next(calls), timeout=10, output=tmp_path / "log")
    assert process.returncode == -9


def test_model_import_links_without_copy_and_requires_license(configured, monkeypatch):
    _, model = configured
    saved = []
    monkeypatch.setattr(drama, "_save", saved.append)
    with pytest.raises(drama.DramaError, match="license"):
        drama.import_model(str(model), False)
    drama.import_model(str(model), True)
    assert saved == [{"voice_drama_model_path": str(model)}]


def test_offline_mode_blocks_installation_but_not_passive_local_readiness(configured, monkeypatch):
    from agent import privacy
    monkeypatch.setattr(privacy, "mode", lambda: "offline")
    monkeypatch.setattr(drama.platform, "system", lambda: "Windows")
    monkeypatch.setattr(drama.shutil, "which", lambda name: "git.exe")
    with pytest.raises(privacy.PrivacyBlocked):
        drama.download_model("q2", True)
    with pytest.raises(privacy.PrivacyBlocked):
        drama.build_runtime("cpu")
    assert drama.readiness()["ready"]


def test_setup_download_uses_its_own_cancellation_and_manifest(configured, monkeypatch):
    from agent import generation_setup, privacy
    seen = []
    monkeypatch.setattr(privacy, "require", lambda *a: None)
    monkeypatch.setattr(drama, "_launch_job", lambda kind, work: work() or {})
    monkeypatch.setattr(drama.shutil, "disk_usage", lambda p: SimpleNamespace(free=100 * 1024**3))
    monkeypatch.setattr(drama, "_save", lambda patch: seen.append(patch))
    def download(url, target, digest, size, **kwargs):
        assert drama.REVISION in url
        assert digest == drama.MODELS["q2"]["sha256"]
        assert kwargs["cancel"] is drama._CANCEL
        assert kwargs["state"] is drama._STATE
        _model(target)
    monkeypatch.setattr(generation_setup, "download", download)
    drama._CANCEL.clear()
    drama.download_model("q2", True)
    assert seen[0]["voice_drama_model_path"].endswith(drama.MODELS["q2"]["file"])


def test_transport_cancel_preserves_cancelled_job_and_discards_result(tmp_path, monkeypatch):
    store = transport.VoiceTransportStore(tmp_path / "transport")
    jobs = voice.VoiceJobStore(tmp_path / "jobs")
    monkeypatch.setattr(transport, "validate_voice_scope", lambda *a: None)
    monkeypatch.setattr(transport, "get_voice_transport_store", lambda: store)
    monkeypatch.setattr(transport, "get_voice_job_store", lambda: jobs)
    def synthesize(text, output, **kwargs):
        turn = store.list_turns("session")[0]
        assert turn.output_job_ids, "Pending worker must already belong to the turn"
        transport.cancel_voice_playback(turn.id, session_id="session")
        assert kwargs["cancelled"]()
        _wav(output)  # model finishing while Stop arrives cannot resurrect playback
        return "drama3-local"
    monkeypatch.setattr(transport, "synthesize_voice_audio", synthesize)
    with pytest.raises(transport.VoiceTransportError, match="cancelled"):
        transport.synthesize_voice_chunk(session_id="session", project_id="", client_turn_id="client-12345",
                                         text="Hello", sequence=0, provider_id="drama3-local")
    assert jobs.list(session_id="session")[0].status == "cancelled"
    assert not list(store.clips_root.glob("*.wav"))


def test_preview_cancel_arriving_first_is_preserved():
    from api.routes import media_runtime as routes
    routes.cancel_voice_preview("early-cancel-test")
    assert routes._preview_cancel("early-cancel-test").is_set()


def test_elevenlabs_key_is_not_inherited_by_native_speech(monkeypatch):
    from agent.child_env import child_env
    monkeypatch.setenv("ELEVENLABS_API_KEY", "keep-private")
    assert "ELEVENLABS_API_KEY" not in child_env()


@pytest.mark.skipif(os.name != "nt", reason="Windows worker ownership")
def test_windows_job_releases_its_owned_child_on_close():
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    close = None
    try:
        close = drama._attach_worker_job(process.pid)
        assert close is not None
        assert process.poll() is None
        close()
        close = None
        # Windows job-close termination can report exit code 0. Exiting within
        # five seconds, rather than finishing the 30-second sleep, proves cleanup.
        process.wait(timeout=5)
        assert process.poll() is not None
    finally:
        if close:
            close()
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
