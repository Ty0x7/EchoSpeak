"""Optional Drama-3 speech, with no model load or subprocess on application startup.

Only owner settings actions install/verify native code. Inference is one bounded,
serialized CLI worker; cancellation kills only that worker and releases its memory.
The agent cannot choose executables, model paths, references or CLI arguments.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import struct
import subprocess
import tempfile
import threading
import time
import wave
from pathlib import Path
from typing import Callable

from agent.child_env import child_env
from config import DATA_DIR, config

ROOT = Path(DATA_DIR) / "voice" / "drama3"
REVISION = "17a5b40c5dc2c2be986f5371a7e650108b828e29"
RUNTIME_REVISION = "e065d66b7aa70bb697e39b1e7a952197f017baef"
RUNTIME_REPO = "https://github.com/0xShug0/audio.cpp.git"
MODEL_REPO = "https://huggingface.co/Jmica/Drama-3-GGUF"
MODELS = {
    "q2": {"file": "drama-3-backbone-8bit-experts-Q2_K.gguf", "label": "Q2 experts / Q8 backbone", "size": 17053639872,
           "sha256": "041be86fbb53bf4002d59bf7d5f0c99c321e0b21f5aa3b53fec380eec6f892d3", "cpu_layers": 8},
    "q3": {"file": "drama-3-backbone-8bit-experts-Q3_K_S.gguf", "label": "Q3 experts / Q8 backbone", "size": 20325196992,
           "sha256": "b946a8c9806deba559590b98e37ffc9e0957109263e111513734fa93284db16b", "cpu_layers": 12},
    "q4": {"file": "drama-3-backbone-8bit-experts-Q4_K_S.gguf", "label": "Q4 experts / Q8 backbone", "size": 24596497888,
           "sha256": "05af716baf78e7c20863b9ccebb3ebdeca9607dd3ea7d0b72c35079e675b212b", "cpu_layers": 24},
    "q4-backbone": {"file": "drama-3-backbone-4bit-experts-Q4_K_S.gguf", "label": "Q4 experts / Q4 backbone", "size": 23386029088,
           "sha256": "ae65d77b4fe21d5d176cf9bb8123fa21982c65d6e40a32cbed1b85c31390fdb6", "cpu_layers": 20},
    "q4-f32": {"file": "drama-3-backbone-8bit-F32-routers-experts-Q4_K_S.gguf", "label": "Q4 / FP32 routers", "size": 24665234144,
           "sha256": "2e3da9f7c36f51f1eda7490c110469847757c0f7e715b9bddf65b8aeb95b14d7", "cpu_layers": 24},
    "q4-bf16": {"file": "drama-3-backbone-8bit-F32-routers-BF16-embed-head-experts-Q4_K_S.gguf", "label": "Q4 / FP32 routers / BF16 head", "size": 25634020064,
           "sha256": "ea8b2e595026e6ec8570606d8633415ed3b016659f8415ba24de315e8ce60aeb", "cpu_layers": 24},
}
_LOCK = threading.RLock()
_SPEECH = threading.Lock()
_CANCEL = threading.Event()
_STATE = {"running": False, "kind": "", "message": "", "downloaded": 0, "total": 0, "error": ""}
_HARDWARE: tuple[float, dict] = (0, {})
_ACTIVE = False


class DramaError(RuntimeError):
    pass


def _local_file(raw: str, suffix: str | None = None) -> Path:
    value = str(raw or "").strip()
    # Network paths could send a voice reference or credentials off-machine.
    if not value or value.startswith(("\\\\", "//")) or "://" in value:
        raise DramaError("Choose an existing file on this PC, not a URL or network share.")
    if os.name == "nt":
        import ctypes
        anchor = Path(value).expanduser().absolute().anchor
        drive_type = ctypes.windll.kernel32.GetDriveTypeW
        drive_type.argtypes = [ctypes.c_wchar_p]
        drive_type.restype = ctypes.c_uint
        if drive_type(anchor) == 4:
            raise DramaError("Network drives cannot be used for local speech files.")
    try:
        path = Path(value).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise DramaError("The configured local file is missing. Choose it again in Settings › Voice.") from exc
    if str(path).startswith(("\\\\", "//")) or not path.is_file() or (suffix and path.suffix.lower() != suffix):
        raise DramaError(f"Choose a local {suffix or 'runtime'} file.")
    return path


def validate_model(path: Path) -> None:
    """Read only bounded GGUF metadata, never model tensors or executable pickle."""
    with path.open("rb") as handle:
        if handle.read(4) != b"GGUF":
            raise DramaError("This is not a GGUF model.")
        header = handle.read(20)
        if len(header) != 20:
            raise DramaError("Incomplete GGUF header.")
        version, _tensors, count = struct.unpack("<IQQ", header)
        if version not in (2, 3) or count > 100000:
            raise DramaError("Unsupported GGUF metadata.")
        limit = min(path.stat().st_size, 8 * 1024 * 1024)

        def read(size):
            if size < 0 or handle.tell() + size > limit:
                raise DramaError("GGUF metadata exceeds the safe inspection limit.")
            value = handle.read(size)
            if len(value) != size:
                raise DramaError("Incomplete GGUF metadata.")
            return value

        def string():
            length = struct.unpack("<Q", read(8))[0]
            return read(length).decode("utf-8", errors="replace")

        def skip(kind, depth=0):
            sizes = {0: 1, 1: 1, 2: 2, 3: 2, 4: 4, 5: 4, 6: 4, 7: 1, 10: 8, 11: 8, 12: 8}
            if kind in sizes:
                read(sizes[kind])
            elif kind == 8:
                string()
            elif kind == 9 and depth < 2:
                element, size = struct.unpack("<IQ", read(12))
                if size > 1000000:
                    raise DramaError("GGUF array exceeds the inspection limit.")
                if element in sizes:
                    read(size * sizes[element])
                else:
                    for _ in range(size):
                        skip(element, depth + 1)
            else:
                raise DramaError("Unsupported GGUF metadata type.")

        for _ in range(count):
            key, kind = string(), struct.unpack("<I", read(4))[0]
            if key == "audiocpp.family" and kind == 8:
                if string() == "fish_drama3":
                    return
                raise DramaError("This GGUF is for another audio model, not Drama-3.")
            skip(kind)
    raise DramaError("Choose an audio.cpp Drama-3 GGUF with the fish_drama3 model contract.")


def _fingerprint(path: Path) -> dict:
    stat = path.stat()
    return {"path": str(path), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def _verification(path: Path) -> dict:
    try:
        data = json.loads((ROOT / "runtime-verification.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) and data.get("file") == _fingerprint(path) else {}
    except (OSError, ValueError):
        return {}


def verify_runtime(raw: str = "") -> dict:
    path = _local_file(raw or getattr(config, "voice_drama_runtime_path", ""))
    if path.name.lower() not in {"audiocpp_cli.exe", "audiocpp_cli"}:
        raise DramaError("Choose audiocpp_cli from a trusted compatible audio.cpp build, with its DLLs beside it.")
    try:
        result = subprocess.run([str(path), "--list-loaders", "--json"], capture_output=True, text=True,
                                timeout=20, env=child_env(), cwd=path.parent,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        loaders = json.loads(result.stdout).get("loaders", {}) if result.returncode == 0 else {}
        modes = loaders.get("fish_drama3", {}).get("tasks", {}).get("tts", [])
        if "offline" not in modes:
            raise DramaError("This runtime does not support Drama-3 offline speech. Use a newer build with fish_drama3 enabled.")
        version = subprocess.run([str(path), "--version"], capture_output=True, text=True, timeout=20,
                                 env=child_env(), cwd=path.parent, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        match = re.search(r"^backends:\s*(.+)$", version.stdout, re.MULTILINE | re.IGNORECASE)
        backends = re.findall(r"\b(cpu|cuda|vulkan|metal|hip)\b", match[1].lower()) if match else []
        if version.returncode or "cpu" not in backends:
            raise DramaError("Could not verify this runtime's CPU/CUDA build capabilities. Choose a compatible newer audio.cpp build.")
    except (OSError, ValueError, AttributeError, TypeError, subprocess.TimeoutExpired) as exc:
        raise DramaError("The runtime could not be verified. Check its DLLs and install a compatible audio.cpp build.") from exc
    record = {"file": _fingerprint(path), "verified_at": time.time(), "family": "fish_drama3", "backends": backends}
    ROOT.mkdir(parents=True, exist_ok=True)
    (ROOT / "runtime-verification.json").write_text(json.dumps(record), encoding="utf-8")
    return {"verified": True, "message": "Drama-3 speech loader verified. Play a sample to test your model and hardware."}


def readiness() -> dict:
    """Passive, cheap check; never executes code, loads weights or probes a GPU."""
    runtime, model, verified = None, None, False
    detail = "Choose and verify audio.cpp, then download or import a Drama-3 model."
    try:
        runtime = _local_file(getattr(config, "voice_drama_runtime_path", ""))
        record = _verification(runtime)
        verified = bool(record)
        if not verified:
            detail = "Runtime found. Verify it in Settings › Voice before use."
        else:
            model = _local_file(getattr(config, "voice_drama_model_path", ""), ".gguf")
            validate_model(model)
            backend = str(getattr(config, "voice_drama_backend", "cpu"))
            if backend not in {"cpu", "cuda"} or backend not in record.get("backends", []):
                raise DramaError("The chosen CPU/CUDA backend is not verified for this runtime. Verify again or choose a compatible build.")
            if not 0 <= int(config.voice_drama_cpu_layers) <= 40 or not 0 <= int(config.voice_drama_device) <= 32:
                raise DramaError("Choose 0–40 CPU expert layers and a valid GPU index in Settings › Voice.")
            reference = str(getattr(config, "voice_drama_reference_path", "") or "").strip()
            if reference:
                _local_file(reference, ".wav")
                if not str(getattr(config, "voice_drama_reference_text", "") or "").strip():
                    raise DramaError("Add the matching reference transcript in Settings › Voice.")
            detail = "Configured locally. Play a sample to confirm speech and hardware compatibility. The model loads only when you request speech."
    except (DramaError, OSError, ValueError, TypeError) as exc:
        model = None
        if runtime:
            detail = str(exc)
    return {"detected": bool(runtime), "configured": bool(verified and model), "ready": bool(verified and model),
            "runtime_verified": verified, "detail": detail, "active": _ACTIVE}


def hardware() -> dict:
    global _HARDWARE
    if time.monotonic() - _HARDWARE[0] < 60:
        return _HARDWARE[1]
    from agent.generation_setup import gpus
    ram = 0
    if os.name == "nt":
        import ctypes
        class Memory(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [(name, ctypes.c_ulonglong) for name in
                       ("total", "available", "page", "available_page", "virtual", "available_virtual", "extended")]
        memory = Memory()
        memory.length = ctypes.sizeof(memory)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)):
            ram = memory.total
    data = {"ram_bytes": ram, "gpus": gpus(), "disk_free_bytes": shutil.disk_usage(ROOT if ROOT.exists() else Path(DATA_DIR)).free}
    _HARDWARE = (time.monotonic(), data)
    return data


def status() -> dict:
    state = dict(_STATE)
    state["progress"] = min(1, state["downloaded"] / state["total"]) if state["total"] else 0
    folder = ROOT / "models"
    return {**readiness(), "job": state, "hardware": hardware(), "runtime_revision": RUNTIME_REVISION,
            "models_dir": str(folder), "build_supported": platform.system() == "Windows" and bool(shutil.which("git")),
            "models": [{"id": key, **{k: v for k, v in row.items() if k != "sha256"},
                        "installed": (folder / row["file"]).is_file()} for key, row in MODELS.items()]}


def _save(patch: dict) -> None:
    from config import read_runtime_override_payload, write_runtime_override_payload
    payload = read_runtime_override_payload(include_secrets=False, migrate_legacy=False)
    payload.update(patch)
    write_runtime_override_payload(payload)
    config.apply_overrides(patch)


def import_model(raw: str, accepted: bool) -> dict:
    if not accepted:
        raise DramaError("Read and accept the Fish Audio model license before importing or downloading a model.")
    path = _local_file(raw, ".gguf")
    validate_model(path)
    _save({"voice_drama_model_path": str(path)})
    return {"message": "Model linked in place; no large copy was made. Play a sample to verify it."}


def _launch_job(kind: str, work: Callable[[], None]) -> dict:
    with _LOCK:
        if _STATE["running"]:
            raise DramaError("A Drama-3 setup job is already running. Cancel it or wait for it to finish.")
        _CANCEL.clear()
        _STATE.update(running=True, kind=kind, error="", downloaded=0, total=0, message="Preparing…")

    def run():
        try:
            work()
            _STATE["message"] = "Ready. Your selected speech provider has not changed."
        except Exception as exc:
            _STATE["error"] = str(exc)[:600]
        finally:
            _STATE["running"] = False
    threading.Thread(target=run, name="drama-setup", daemon=True).start()
    return dict(_STATE)


def download_model(key: str, accepted: bool) -> dict:
    if not accepted or key not in MODELS:
        raise DramaError("Choose a supported model and accept its license first.")
    from agent import privacy
    privacy.require("downloads", MODEL_REPO)
    row = MODELS[key]
    target = ROOT / "models" / row["file"]
    target.parent.mkdir(parents=True, exist_ok=True)
    remaining = row["size"] - (target.with_suffix(".gguf.part").stat().st_size if target.with_suffix(".gguf.part").exists() else 0)
    if shutil.disk_usage(target.parent).free < remaining + 1024 ** 3:
        raise DramaError("Not enough free disk space for this model plus 1 GiB of headroom.")

    def work():
        from agent.generation_setup import download
        download(f"{MODEL_REPO}/resolve/{REVISION}/{row['file']}", target, row["sha256"], row["size"], state=_STATE, cancel=_CANCEL)
        if _CANCEL.is_set():
            raise InterruptedError("Setup cancelled. Partial downloads are kept for Resume.")
        validate_model(target)
        for name in ("LICENSE", "NOTICE"):
            source = Path(__file__).resolve().parents[1] / "resources" / "drama3" / name
            shutil.copyfile(source, target.parent / name)
        _save({"voice_drama_model_path": str(target), "voice_drama_cpu_layers": row["cpu_layers"]})
    return _launch_job("model", work)


def _run_checked(args: list[str], cwd: Path, *, cancel: Callable[[], bool], timeout: int, output: Path) -> None:
    """No shell, no console, no inherited app keys; bounded output goes to a private file."""
    if cancel():
        raise InterruptedError("Speech or setup cancelled.")
    with output.open("wb") as log:
        process = subprocess.Popen(args, cwd=cwd, stdout=log, stderr=subprocess.STDOUT,
                                   env=child_env(), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        started = time.monotonic()
        job = None
        try:
            try:
                job = _attach_worker_job(process.pid) if process.poll() is None else None
            except DramaError:
                if process.poll() is None:
                    raise
            while process.poll() is None:
                if cancel():
                    raise InterruptedError("Speech or setup cancelled.")
                if time.monotonic() - started > timeout:
                    raise DramaError("The runtime timed out. Try CPU offload, a smaller model, or another voice provider.")
                if output.stat().st_size > 4 * 1024 * 1024:
                    raise DramaError("The runtime produced too much diagnostic output; the worker was stopped.")
                time.sleep(0.1)
            if process.returncode:
                raise DramaError("audio.cpp could not complete this operation. Check runtime DLLs, available RAM/VRAM and offload settings; then test again.")
        finally:
            try:
                if process.poll() is None:
                    if os.name == "nt" and job is None:
                        subprocess.run(["taskkill.exe", "/PID", str(process.pid), "/T", "/F"], capture_output=True,
                                       timeout=15, env=child_env(), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    if process.poll() is None:
                        process.kill()
                    process.wait(timeout=10)
            finally:
                if job is not None:
                    job()


def _attach_worker_job(pid: int):
    """A Windows-owned job releases workers even if the backend itself crashes."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes as w
    class Basic(ctypes.Structure):
        _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64), ("flags", w.DWORD),
                    ("minimum", ctypes.c_size_t), ("maximum", ctypes.c_size_t), ("processes", w.DWORD),
                    ("affinity", ctypes.c_size_t), ("priority", w.DWORD), ("scheduling", w.DWORD)]
    class IO(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]
    class Extended(ctypes.Structure):
        _fields_ = [("basic", Basic), ("io", IO), ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                    ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
    kernel.CreateJobObjectW.restype = w.HANDLE
    kernel.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
    kernel.SetInformationJobObject.restype = w.BOOL
    kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    kernel.OpenProcess.restype = w.HANDLE
    kernel.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
    kernel.AssignProcessToJobObject.restype = w.BOOL
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.CloseHandle.restype = w.BOOL
    job = kernel.CreateJobObjectW(None, None)
    if not job:
        raise DramaError("Windows could not create an isolated speech worker.")
    limits = Extended()
    limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    process = None
    try:
        if not kernel.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            raise DramaError("Windows could not set speech worker cleanup limits.")
        process = kernel.OpenProcess(0x0101, False, pid)  # SET_QUOTA | TERMINATE, only our child
        if not process or not kernel.AssignProcessToJobObject(job, process):
            raise DramaError("Windows could not attach the speech worker to its cleanup job.")
    except Exception:
        kernel.CloseHandle(job)
        raise
    finally:
        if process:
            kernel.CloseHandle(process)
    return lambda: kernel.CloseHandle(job)


def build_runtime(backend: str) -> dict:
    if platform.system() != "Windows" or backend not in {"cpu", "cuda"} or not shutil.which("git"):
        raise DramaError("Managed builds need Windows and Git. Install Visual Studio 2022 C++ tools, CMake and Ninja; CUDA builds also need the CUDA toolkit. You can import an existing runtime instead.")
    from agent import privacy
    privacy.require("downloads", "https://github.com/0xShug0/audio.cpp")

    def work():
        source = ROOT / f"runtime-{RUNTIME_REVISION}"
        ROOT.mkdir(parents=True, exist_ok=True)
        log = ROOT / "runtime-build.log"
        run = lambda args, cwd: _run_checked(args, cwd, cancel=_CANCEL.is_set, timeout=3600, output=log)
        _STATE["message"] = "Fetching pinned audio.cpp source…"
        if not (source / ".git").is_dir():
            source.mkdir(parents=True, exist_ok=True)
            run([shutil.which("git"), "init", str(source)], ROOT)
        run([shutil.which("git"), "fetch", "--depth", "1", RUNTIME_REPO, RUNTIME_REVISION], source)
        run([shutil.which("git"), "checkout", "--detach", "--force", RUNTIME_REVISION], source)
        # Execute only the reviewed pinned upstream helper. No arbitrary URL or command from requests.
        _STATE["message"] = "Building audio.cpp. This may take several minutes; no speech model is loading."
        preset = f"windows-{backend}-release"
        script = source / "scripts" / "build_windows.ps1"
        run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script),
             "-Preset", preset, "-Target", "audiocpp_cli", "-ModelSet", "custom", "-Models", "fish_drama3", "-DeploymentBuild", "-Jobs", "4"], source)
        path = source / "build" / preset / "bin" / "audiocpp_cli.exe"
        verify_runtime(str(path))
        _save({"voice_drama_runtime_path": str(path), "voice_drama_backend": backend})
    return _launch_job("runtime", work)


def cancel_setup() -> dict:
    _CANCEL.set()
    return {"message": "Cancellation requested. Partial model downloads are kept for Resume."}


def synthesize(text: str, output: Path, *, cancelled: Callable[[], bool] | None = None) -> None:
    global _ACTIVE
    if not readiness()["ready"]:
        raise DramaError(readiness()["detail"])
    if not _SPEECH.acquire(blocking=False):
        raise DramaError("Drama-3 is speaking in another request. Wait or stop that request before trying again.")
    try:
        _ACTIVE = True
        path = _local_file(config.voice_drama_runtime_path)
        model = _local_file(config.voice_drama_model_path, ".gguf")
        backend = str(config.voice_drama_backend)
        if backend not in {"cpu", "cuda"}:
            raise DramaError("Choose CPU or CUDA in Settings › Voice.")
        layers = int(config.voice_drama_cpu_layers)
        device = int(config.voice_drama_device)
        if not 0 <= layers <= 40 or not 0 <= device <= 32:
            raise DramaError("Invalid CPU offload or GPU selection.")
        args = [str(path), "--task", "tts", "--family", "fish_drama3", "--mode", "offline", "--backend", backend,
                "--device", str(device), "--model", str(model), "--model-spec-override", "embedded", "--text", text,
                "--max-tokens", "1000", "--threads", "4", "--session-option", f"fish_drama3.cpu_expert_layers={layers}",
                "--session-option", "fish_drama3.context_length=2048", "--out", str(output)]
        reference = str(getattr(config, "voice_drama_reference_path", "") or "").strip()
        if reference:
            ref = _local_file(reference, ".wav")
            if ref.stat().st_size > 5 * 1024 * 1024:
                raise DramaError("Choose a short voice reference smaller than 5 MiB.")
            try:
                with wave.open(str(ref), "rb") as wav:
                    if wav.getcomptype() != "NONE" or not 0 < wav.getnframes() / wav.getframerate() <= 30:
                        raise DramaError("Choose a PCM WAV voice reference up to 30 seconds long.")
            except (wave.Error, ZeroDivisionError) as exc:
                raise DramaError("The voice reference is not a valid PCM WAV.") from exc
            transcript = str(getattr(config, "voice_drama_reference_text", "") or "").strip()
            if not transcript or len(transcript) > 2000:
                raise DramaError("Add the matching reference transcript (up to 2,000 characters).")
            args += ["--voice-ref", str(ref), "--request-option", f"reference_text={transcript}"]
        with tempfile.TemporaryDirectory(prefix="echo-drama-") as folder:
            _run_checked(args, path.parent, cancel=cancelled or (lambda: False), timeout=600, output=Path(folder) / "worker.log")
        if cancelled and cancelled():
            raise InterruptedError("Speech cancelled.")
        with wave.open(str(output), "rb") as wav:
            if wav.getnframes() <= 0 or wav.getcomptype() != "NONE":
                raise DramaError("The runtime returned an invalid speech clip.")
    except (OSError, ValueError, wave.Error) as exc:
        raise DramaError("The speech runtime could not produce a WAV. Check the model, runtime and available memory.") from exc
    finally:
        _ACTIVE = False
        _SPEECH.release()
