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


def gpus():
    devices = []
    try:
        result = subprocess.run(["nvidia-smi", "--query-gpu=index,uuid,name,memory.total,memory.free,driver_version,compute_cap", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5, env=child_env(), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                index, identity, name, memory, free, driver, capability = [part.strip() for part in line.split(",")]
                devices.append({"id": identity, "index": int(index), "name": name, "vram_mb": int(memory),
                                "free_vram_mb": int(free), "driver": driver, "compute_capability": float(capability)})
    except (OSError, ValueError, subprocess.TimeoutExpired, IndexError):
        pass
    return devices


def hardware(gpu_id: str = "", devices: list | None = None):
    devices = gpus() if devices is None else devices
    if not gpu_id:
        try:
            gpu_id = json.loads((ROOT / "gpu.json").read_text())["id"]
        except (OSError, ValueError, KeyError):
            pass
    if gpu_id:
        return next((row for row in devices if row["id"] == gpu_id), {})
    return max(devices, key=lambda row: row["vram_mb"], default={})


def runtime_variant(gpu: dict) -> str:
    driver = tuple(int(part) for part in gpu["driver"].split(".")[:2])
    capability = gpu.get("compute_capability", 0)
    if driver < (528, 33):
        raise ValueError("Update the NVIDIA driver: the managed CUDA runtime requires driver 528.33 or newer.")
    if capability < 5:
        raise ValueError("This GPU is too old for the managed runtime. Connect a compatible ComfyUI installation or use cloud creation.")
    modern = driver >= (580,) and capability >= 7.5
    if capability >= 10 and not modern:
        raise ValueError("Update your NVIDIA driver to 580 or newer before installing the Blackwell runtime.")
    return "nvidia" if modern else "nvidia_cu126"


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
    devices = gpus()
    gpu = hardware(devices=devices)
    issue = ""
    try:
        if gpu:
            runtime_variant(gpu)
    except ValueError as exc:
        issue = str(exc)
    base = runtime_root()
    disk_path = ROOT if ROOT.exists() else Path(DATA_DIR)
    disk_path.mkdir(parents=True, exist_ok=True)
    return {**dict(_STATE), "hardware": gpu, "gpus": devices, "compatibility_issue": issue,
        "supported": platform.system() == "Windows" and bool(gpu) and not issue,
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
    meta = target.with_suffix(target.suffix + ".part.json")
    identity = {"url": url, "sha256": digest, "size": size}
    try:
        stored = json.loads(meta.read_text())
    except (OSError, ValueError):
        stored = {}
    if any(stored.get(key) != value for key, value in identity.items()) or (temp.exists() and temp.stat().st_size > size):
        temp.unlink(missing_ok=True)
        stored = {}
    count = temp.stat().st_size if temp.exists() else 0
    hasher = hashlib.sha256()
    if count:
        with temp.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                if _CANCEL.is_set():
                    raise InterruptedError("Installation cancelled; partial downloads are retained for retry.")
                hasher.update(chunk)
    if count == size and hasher.hexdigest() == digest:
        os.replace(temp, target)
        meta.unlink(missing_ok=True)
        return
    if count == size:
        temp.unlink(missing_ok=True)
        count, hasher = 0, hashlib.sha256()
    headers = {"Accept-Encoding": "identity"}
    if count:
        headers["Range"] = f"bytes={count}-"
        if stored.get("etag"):
            headers["If-Range"] = stored["etag"]
    _STATE.update(message=("Resuming " if count else "Downloading ") + target.name, downloaded=count, total=size)
    try:
        # URLs are fixed in the shipped manifest, never model/user supplied. No credentials.
        with httpx.Client(timeout=90, follow_redirects=True, trust_env=False) as client:
            with client.stream("GET", url, headers=headers) as response:
                response.raise_for_status()
                if response.status_code == 206:
                    expected = f"bytes {count}-{size - 1}/{size}"
                    if response.headers.get("Content-Range") != expected:
                        raise ValueError("Invalid download resume range")
                elif response.status_code == 200:
                    count, hasher = 0, hashlib.sha256()
                else:
                    raise ValueError("Unexpected download response")
                meta.write_text(json.dumps({**identity, "etag": response.headers.get("ETag", "")}))
                with temp.open("ab" if count else "wb") as handle:
                    for chunk in response.iter_bytes(1024 * 1024):
                        if _CANCEL.is_set():
                            raise InterruptedError("Installation cancelled; partial downloads are retained for retry.")
                        count += len(chunk)
                        if count > size:
                            raise ValueError("Download exceeds the pinned manifest size")
                        hasher.update(chunk)
                        handle.write(chunk)
                        _STATE["downloaded"] = count
        if count < size:
            raise RuntimeError("Download incomplete; retry setup to resume the retained partial file.")
        if count != size or hasher.hexdigest() != digest:
            raise ValueError("Download integrity check failed; file was not installed")
        os.replace(temp, target)
        meta.unlink(missing_ok=True)
    except ValueError:
        temp.unlink(missing_ok=True)
        meta.unlink(missing_ok=True)
        raise


def _install(profile: str, gpu: dict):
    try:
        import py7zr
        ROOT.mkdir(parents=True, exist_ok=True)
        base = runtime_root()
        if base:
            manifest = json.loads((ROOT / "runtime.json").read_text())
            if manifest.get("variant") and manifest["variant"] != runtime_variant(gpu):
                base = None
        if not base:
            variant = runtime_variant(gpu)
            digest, size = RUNTIMES[variant]
            archive = ROOT / ("ComfyUI_" + RELEASE + "_" + variant + ".7z")
            download(f"https://github.com/Comfy-Org/ComfyUI/releases/download/{RELEASE}/ComfyUI_windows_portable_{variant}.7z", archive, digest, size)
            _STATE["message"] = "Extracting the verified runtime (this may take a few minutes)…"
            stage = ROOT / ("runtime-" + uuid.uuid4().hex[:10])
            stage.mkdir()
            with py7zr.SevenZipFile(archive) as package:
                expanded = package.archiveinfo().uncompressed
                if expanded > 18_000_000_000:
                    raise ValueError("Runtime archive expands beyond the allowed size")
                needed = expanded + sum(row[3] for row in PROFILES[profile]) + 2_000_000_000
                if shutil.disk_usage(ROOT).free < needed:
                    raise ValueError(f"Free at least {needed / 1e9:.1f} GB for runtime extraction and the selected models. The downloaded archive is retained.")
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
            marker.write_text(json.dumps({"folder": str(base.relative_to(ROOT)), "release": RELEASE, "variant": variant}))
        verify_cuda(base, gpu)
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
                break
            except Exception:
                if _PROCESS is not None and _PROCESS.poll() is not None:
                    raise RuntimeError("ComfyUI could not start. Check models/creations/runtime.log for GPU/driver errors.")
        else:
            raise RuntimeError("Installed, but ComfyUI did not become ready within 3 minutes. Check runtime.log.")
        from agent.generation_providers import validate_local_profile
        validate_local_profile(profile, "http://127.0.0.1:8188")
        _STATE["message"] = "Server and workflow ready at 127.0.0.1:8188. Select this ComfyUI address, then Test local generation to verify a render on your GPU."
    except Exception as exc:
        _STATE.update(error=str(exc)[:700], message="Setup stopped. You can retry.")
    finally:
        _STATE["running"] = False


def start_setup(profile: str, gpu_id: str = ""):
    if profile not in PROFILES:
        raise ValueError("Unknown local generation profile")
    info = status()
    gpu = hardware(gpu_id)
    if platform.system() != "Windows" or not gpu:
        raise ValueError("Select an available NVIDIA GPU on Windows, or connect your own ComfyUI server.")
    runtime_variant(gpu)
    required = 20 if profile == "image" else 30
    if info["free_gb"] < required:
        raise ValueError(f"Keep at least {required} GB free for the runtime, model and extraction.")
    if gpu.get("vram_mb", 0) < (4000 if profile == "image" else 8000):
        raise ValueError("This GPU has too little VRAM for the managed starter profile. Choose cloud generation or configure ComfyUI yourself.")
    with _LOCK:
        if _STATE["running"]:
            return dict(_STATE)
        if _PROCESS is not None and _PROCESS.poll() is None and info["hardware"].get("id") != gpu["id"]:
            raise ValueError("Restart EchoSpeak before changing the GPU used by its running local runtime.")
        _CANCEL.clear()
        ROOT.mkdir(parents=True, exist_ok=True)
        (ROOT / "gpu.json").write_text(json.dumps({"id": gpu["id"]}))
        _STATE.update(running=True, message="Preparing local setup…", error="", downloaded=0, total=0)
        threading.Thread(target=_install, args=(profile, gpu), daemon=True).start()
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
            env = child_env()
            gpu = hardware()
            if not gpu:
                raise RuntimeError("The selected GPU is unavailable. Select it again in local setup.")
            env["CUDA_VISIBLE_DEVICES"] = gpu["id"]
            _PROCESS = subprocess.Popen([str(base / "python_embeded/python.exe"), "-s", str(base / "ComfyUI/main.py"),
                "--windows-standalone-build", "--listen", "127.0.0.1", "--port", "8188", "--disable-auto-launch", "--disable-all-custom-nodes"],
                cwd=base, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return {"started": True, "detail": "Starting ComfyUI. Refresh provider readiness in a moment."}


def verify_cuda(base: Path, gpu: dict):
    """Small actual device calculation before downloading multi-GB model weights."""
    env = child_env()
    env["CUDA_VISIBLE_DEVICES"] = gpu["id"]
    result = subprocess.run([str(base / "python_embeded/python.exe"), "-s", "-c",
        "import torch; assert torch.cuda.is_available(), 'CUDA unavailable'; "
        "x=torch.ones(1, device='cuda')+1; torch.cuda.synchronize(); assert x.item()==2"],
        capture_output=True, text=True, timeout=45, env=env,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode:
        raise RuntimeError("The runtime could not compute on the selected GPU. Check the NVIDIA driver and runtime compatibility. " + result.stderr[-600:])


def stop_setup():
    _CANCEL.set()
    return {"cancel_requested": True}


@atexit.register
def _stop_owned_runtime():
    if _PROCESS is not None and _PROCESS.poll() is None:
        _PROCESS.terminate()
