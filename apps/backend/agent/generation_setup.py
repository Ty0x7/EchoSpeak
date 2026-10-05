"""Explicit optional local installs. Never install into EchoSpeak's Python environment."""
from __future__ import annotations

import atexit
import hashlib
import json
import os
import platform
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

import httpx

from agent.child_env import child_env
from config import DATA_DIR

ROOT = Path(DATA_DIR) / "models" / "creations"
RELEASE = "v0.38.0"
RUNTIMES = {
    "nvidia": ("8f137eac345707fd7e42bcf8e29377415243011ca15522a86aed6c77331fbd56", 1994326521),
    "nvidia_cu126": ("bd3bd7e3ce0068d8bc58fe46c912c360dd510f9bcfd0c5029d4906e6fbc677", 1886111296),
}
SD_REPO = "stable-diffusion-v1-5/stable-diffusion-v1-5/resolve/451f4fe16113bff5a5d2269ed5ad43b0592e9a14"
WAN_REPO = "Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/123acf1cc74bccbb9bfff8ac1ee72edc08c2341d/split_files"
PROFILES = {
    "image": [(f"https://huggingface.co/{SD_REPO}/v1-5-pruned-emaonly.safetensors", "checkpoints/v1-5-pruned-emaonly.safetensors", "6ce0161689b3853acaa03779ec93eafe75a02f4ced659bee03f50797806fa2fa", 4265146304)],
    "video": [
        (f"https://huggingface.co/{WAN_REPO}/diffusion_models/wan2.1_t2v_1.3B_fp16.safetensors", "diffusion_models/wan2.1_t2v_1.3B_fp16.safetensors", "be531024cd9018cb5b48c40cfbb6a6191645b1c792eb8bf4f8c1c6e10f924dc5", 2838303560),
        (f"https://huggingface.co/{WAN_REPO}/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors", "text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors", "c3355d30191f1f066b26d93fba017ae9809dce6c627dda5f6a66eaa651204f68", 6735906897),
        (f"https://huggingface.co/{WAN_REPO}/vae/wan_2.1_vae.safetensors", "vae/wan_2.1_vae.safetensors", "2fc39d31359a4b0a64f55876d8ff7fa8d780956ae2cb13463b0223e15148976b", 253815318),
    ],
}
_LOCK = threading.RLock()
_STATE = {"running": False, "message": "", "downloaded": 0, "total": 0, "error": ""}
_CANCEL = threading.Event()
_PROCESS = None


def hardware():
    gpu = {}
    try:
        result = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5, env=child_env(), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode == 0:
            name, memory, driver = result.stdout.splitlines()[0].split(",")
            gpu = {"name": name.strip(), "vram_mb": int(memory.strip()), "driver": driver.strip()}
    except (OSError, ValueError, subprocess.TimeoutExpired, IndexError):
        pass
    return gpu


def runtime_root():
    marker = ROOT / "runtime.json"
    if marker.exists():
        try:
            candidate = (ROOT / json.loads(marker.read_text())["folder"]).resolve()
            if candidate.is_relative_to(ROOT.resolve()) and (candidate / "python_embeded/python.exe").is_file():
                return candidate
        except (ValueError, KeyError, OSError):
            pass
    return None


def status():
    gpu = hardware()
    base = runtime_root()
    disk_path = ROOT if ROOT.exists() else Path(DATA_DIR)
    disk_path.mkdir(parents=True, exist_ok=True)
    return {**dict(_STATE), "hardware": gpu, "supported": platform.system() == "Windows" and bool(gpu),
        "runtime_installed": base is not None, "runtime_running": _PROCESS is not None and _PROCESS.poll() is None,
        "free_gb": round(shutil.disk_usage(disk_path).free / 1e9, 1), "profiles": [
            {"id": name, "label": "Stable Diffusion 1.5 images" if name == "image" else "Wan 2.1 short videos",
             "download_gb": round(sum(row[3] for row in files) / 1e9 + (0 if base else 2), 1),
             "recommended_vram_gb": 6 if name == "image" else 8,
             "installed": bool(base and all((base / "ComfyUI/models" / row[1]).is_file() for row in files)),
             "license": "https://huggingface.co/stable-diffusion-v1-5/stable-diffusion-v1-5" if name == "image" else "https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged"}
            for name, files in PROFILES.items()],
        "detail": "Managed setup supports Windows NVIDIA GPUs. Other hardware can connect an existing ComfyUI server. Models are optional large downloads; generation speed depends on your GPU."}


def download(url: str, target: Path, digest: str, size: int):
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.stat().st_size == size:
        with target.open("rb") as handle:
            if hashlib.file_digest(handle, "sha256").hexdigest() == digest:
                return
    temp = target.with_suffix(target.suffix + ".part")
    hasher, count = hashlib.sha256(), 0
    _STATE.update(message="Downloading " + target.name, downloaded=0, total=size)
    try:
        # URLs are fixed in the shipped manifest, never model/user supplied. No credentials.
        with httpx.Client(timeout=90, follow_redirects=True, trust_env=False) as client:
            with client.stream("GET", url) as response, temp.open("wb") as handle:
                response.raise_for_status()
                for chunk in response.iter_bytes(1024 * 1024):
                    if _CANCEL.is_set():
                        raise InterruptedError("Installation cancelled; completed downloads are retained.")
                    count += len(chunk)
                    if count > size:
                        raise ValueError("Download exceeds the pinned manifest size")
                    hasher.update(chunk)
                    handle.write(chunk)
                    _STATE["downloaded"] = count
        if count != size or hasher.hexdigest() != digest:
            raise ValueError("Download integrity check failed; file was not installed")
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)


def _install(profile: str, gpu: dict):
    try:
        import py7zr
        ROOT.mkdir(parents=True, exist_ok=True)
        base = runtime_root()
        if not base:
            modern = int(gpu["driver"].split(".")[0]) >= 580
            variant = "nvidia" if modern else "nvidia_cu126"
            if "RTX 50" in gpu["name"] and not modern:
                raise ValueError("Update your NVIDIA driver before installing the RTX 50-series runtime.")
            digest, size = RUNTIMES[variant]
            archive = ROOT / ("ComfyUI_" + RELEASE + "_" + variant + ".7z")
            download(f"https://github.com/Comfy-Org/ComfyUI/releases/download/{RELEASE}/ComfyUI_windows_portable_{variant}.7z", archive, digest, size)
            _STATE["message"] = "Extracting the verified runtime (this may take a few minutes)…"
            stage = ROOT / ("runtime-" + uuid.uuid4().hex[:10])
            stage.mkdir()
            with py7zr.SevenZipFile(archive) as package:
                if package.archiveinfo().uncompressed > 18_000_000_000:
                    raise ValueError("Runtime archive expands beyond the allowed size")
                for entry in package.list():
                    target = (stage / entry.filename).resolve()
                    if not target.is_relative_to(stage.resolve()) or ":" in entry.filename or not (entry.is_file or entry.is_directory):
                        raise ValueError("Runtime archive contains an unsafe path")
                package.extractall(path=stage)
            python = next(stage.glob("*/python_embeded/python.exe"), None)
            if python is None:
                raise ValueError("Runtime archive did not contain its embedded Python")
            base = python.parent.parent
            marker = ROOT / "runtime.json"
            marker.write_text(json.dumps({"folder": str(base.relative_to(ROOT)), "release": RELEASE}))
        for url, name, digest, size in PROFILES[profile]:
            download(url, base / "ComfyUI/models" / name, digest, size)
        _STATE["message"] = "Models installed. Starting and verifying the local runtime…"
        start_runtime()
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            if _CANCEL.wait(2):
                raise InterruptedError("Installation finished; readiness check cancelled.")
            try:
                with httpx.Client(timeout=2, trust_env=False) as client:
                    response = client.get("http://127.0.0.1:8188/system_stats")
                    response.raise_for_status()
                _STATE["message"] = "Ready. Select ComfyUI for your image or video provider."
                break
            except Exception:
                if _PROCESS is not None and _PROCESS.poll() is not None:
                    raise RuntimeError("ComfyUI could not start. Check models/creations/runtime.log for GPU/driver errors.")
        else:
            raise RuntimeError("Installed, but ComfyUI did not become ready within 3 minutes. Check runtime.log.")
    except Exception as exc:
        _STATE.update(error=str(exc)[:700], message="Setup stopped. You can retry.")
    finally:
        _STATE["running"] = False


def start_setup(profile: str):
    if profile not in PROFILES:
        raise ValueError("Unknown local generation profile")
    info = status()
    if not info["supported"]:
        raise ValueError(info["detail"])
    required = 20 if profile == "image" else 30
    if info["free_gb"] < required:
        raise ValueError(f"Keep at least {required} GB free for the runtime, model and extraction.")
    if info["hardware"].get("vram_mb", 0) < (4000 if profile == "image" else 8000):
        raise ValueError("This GPU has too little VRAM for the managed starter profile. Choose cloud generation or configure ComfyUI yourself.")
    with _LOCK:
        if _STATE["running"]:
            return dict(_STATE)
        _CANCEL.clear()
        _STATE.update(running=True, message="Preparing local setup…", error="", downloaded=0, total=0)
        threading.Thread(target=_install, args=(profile, info["hardware"]), daemon=True).start()
    return dict(_STATE)


def start_runtime():
    global _PROCESS
    with _LOCK:
        if _PROCESS is not None and _PROCESS.poll() is None:
            return {"started": True}
        base = runtime_root()
        if not base:
            raise RuntimeError("Install the local runtime first")
        # Do not start a second server on an occupied port or replace another installation.
        import socket
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", 8188)) == 0:
                raise RuntimeError("Port 8188 is already in use. Connect your existing server, or close it before starting the managed one.")
        with (ROOT / "runtime.log").open("ab") as log:
            _PROCESS = subprocess.Popen([str(base / "python_embeded/python.exe"), "-s", str(base / "ComfyUI/main.py"),
                "--windows-standalone-build", "--listen", "127.0.0.1", "--port", "8188", "--disable-auto-launch", "--disable-all-custom-nodes"],
                cwd=base, env=child_env(), stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return {"started": True, "detail": "Starting ComfyUI. Refresh provider readiness in a moment."}


def stop_setup():
    _CANCEL.set()
    return {"cancel_requested": True}


@atexit.register
def _stop_owned_runtime():
    if _PROCESS is not None and _PROCESS.poll() is None:
        _PROCESS.terminate()
