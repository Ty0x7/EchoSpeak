"""HTTP adapters for cloud APIs and an isolated, loopback ComfyUI runtime."""
from __future__ import annotations

import base64
import json
import re
import time
from urllib.parse import quote, urljoin, urlsplit

import httpx

from config import config

GOOGLE = "https://generativelanguage.googleapis.com/v1beta"
MODELS = {"gemini-images": "gemini-2.5-flash-image", "gemini-video": "veo-3.1-generate-preview",
          "minimax-video": "MiniMax-Hailuo-2.3"}
IMAGE_MODEL = "v1-5-pruned-emaonly.safetensors"
VIDEO_MODEL = "wan2.1_t2v_1.3B_fp16.safetensors"


def comfy_base() -> str:
    base = str(config.comfyui_base_url).rstrip("/")
    parts = urlsplit(base)
    if parts.scheme not in {"http", "https"} or parts.hostname not in {"localhost", "127.0.0.1", "::1"} or parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("Local ComfyUI must use a loopback address without credentials.")
    return base


def request(method: str, url: str, *, headers=None, payload=None, max_bytes=32_000_000):
    # Never retry submissions automatically: a timeout may still have incurred a charge.
    with httpx.Client(timeout=180, follow_redirects=False, trust_env=False) as client:
        with client.stream(method, url, headers=headers, json=payload) as response:
            if not 200 <= response.status_code < 300:
                raise RuntimeError(f"Provider returned HTTP {response.status_code}; check the key, model access and account quota.")
            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > max_bytes:
                    raise RuntimeError("Provider response exceeds the download limit.")
    return json.loads(body)


def public_download(url: str) -> bytes:
    from agent.safe_web_retrieval import fetch_public_bytes
    return fetch_public_bytes(url, timeout_seconds=90, max_bytes=256_000_000)[2]


def google_download(url: str, key: str) -> bytes:
    # Only Google's own API receives the key. A signed redirect receives no credentials.
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname != "generativelanguage.googleapis.com" or parts.username:
        raise RuntimeError("Unexpected Google download host.")
    from agent.safe_web_retrieval import _request_pinned_public_url
    status, headers, data = _request_pinned_public_url(url, headers={"x-goog-api-key": key},
        timeout_seconds=90, max_bytes=256_000_000)
    if 300 <= status < 400 and headers.get("location"):
        return public_download(urljoin(url, headers["location"]))
    if status != 200:
        raise RuntimeError(f"Video download returned HTTP {status}.")
    return data


def provider_statuses() -> list[dict]:
    key = bool(config.gemini.api_key)
    rows = [
        {"id": "gemini-images", "label": "Google · images", "locality": "cloud", "kinds": ["image"], "execution_ready": key, "models": [MODELS["gemini-images"], "gemini-3.1-flash-image-preview"], "detail": "Uses your Gemini API key; API billing may apply."},
        {"id": "gemini-video", "label": "Google Veo · video", "locality": "cloud", "kinds": ["video"], "execution_ready": key, "models": [MODELS["gemini-video"], "veo-3.1-fast-generate-preview"], "detail": "Paid Gemini API access required. Clips are generated asynchronously."},
        {"id": "minimax-video", "label": "MiniMax Hailuo · video", "locality": "cloud", "kinds": ["video"], "execution_ready": bool(config.minimax_api_key), "models": [MODELS["minimax-video"]], "detail": "Uses your MiniMax API balance. Creates 6-second 768p clips."},
    ]
    models, ready, detail = [], False, "Start local ComfyUI or install the managed runtime."
    try:
        with httpx.Client(timeout=1.5, trust_env=False, follow_redirects=False) as client:
            response = client.get(comfy_base() + "/object_info/CheckpointLoaderSimple")
            response.raise_for_status()
            models = response.json()["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"][0]
            ready, detail = True, "Local server reachable. Model availability is checked before submission."
    except Exception:
        pass
    rows.append({"id": "comfyui-local", "label": "ComfyUI · this PC", "locality": "local", "kinds": ["image", "video"],
                 "execution_ready": ready, "models": models, "detail": detail})
    return rows


def comfy_workflow(job) -> dict:
    s = job.settings
    positive, negative = "6", "7"
    width, height = min(s.width, 1024), min(s.height, 1024)
    graph = {
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": job.prompt, "clip": ["4", 1]}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": s.negative_prompt, "clip": ["4", 1]}},
        "3": {"class_type": "KSampler", "inputs": {"seed": s.seed or 0, "steps": 20, "cfg": 7,
            "sampler_name": "euler", "scheduler": "normal", "denoise": 1, "model": ["4", 0],
            "positive": [positive, 0], "negative": [negative, 0], "latent_image": ["5", 0]}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
    }
    if job.kind == "image":
        graph["4"] = {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": job.model}}
        graph["5"] = {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}}
        graph["9"] = {"class_type": "SaveImage", "inputs": {"images": ["8", 0], "filename_prefix": "EchoSpeak/" + job.id}}
    else:
        graph["4"] = {"class_type": "UNETLoader", "inputs": {"unet_name": job.model, "weight_dtype": "default"}}
        graph["10"] = {"class_type": "CLIPLoader", "inputs": {"clip_name": "umt5_xxl_fp8_e4m3fn_scaled.safetensors", "type": "wan", "device": "default"}}
        graph["11"] = {"class_type": "VAELoader", "inputs": {"vae_name": "wan_2.1_vae.safetensors"}}
        graph["12"] = {"class_type": "ModelSamplingSD3", "inputs": {"model": ["4", 0], "shift": 8}}
        graph["3"]["inputs"].update(model=["12", 0], cfg=6, sampler_name="uni_pc", scheduler="simple", steps=20)
        graph["6"]["inputs"]["clip"] = graph["7"]["inputs"]["clip"] = ["10", 0]
        graph["8"]["inputs"]["vae"] = ["11", 0]
        graph["5"] = {"class_type": "EmptyHunyuanLatentVideo", "inputs": {"width": 832, "height": 480, "length": min(s.duration_seconds, 5) * 16 + 1, "batch_size": 1}}
        graph["13"] = {"class_type": "CreateVideo", "inputs": {"images": ["8", 0], "fps": 16}}
        graph["9"] = {"class_type": "SaveVideo", "inputs": {"video": ["13", 0], "filename_prefix": "EchoSpeak/" + job.id, "format": "mp4", "codec": "h264"}}
    return graph


def validate_comfy_workflow(job, base: str = "") -> dict:
    graph = comfy_workflow(job)
    info = request("GET", (base or comfy_base()) + "/object_info")
    missing = [node["class_type"] for node in graph.values() if node["class_type"] not in info]
    if missing:
        raise ValueError("Update ComfyUI; missing built-in nodes: " + ", ".join(sorted(set(missing))))
    if job.kind == "video":
        format_input = info["SaveVideo"].get("input", {}).get("required", {}).get("format", [])
        if format_input and format_input[0] == "COMFY_DYNAMICCOMBO_V3":
            graph["9"]["inputs"].pop("codec", None)
            graph["9"]["inputs"]["format.codec"] = "h264"
    for node in graph.values():
        required = info[node["class_type"]].get("input", {}).get("required", {})
        for field in ("ckpt_name", "unet_name", "clip_name", "vae_name"):
            if field in node["inputs"]:
                choices = required.get(field, [None])[0]
                if not isinstance(choices, list) or node["inputs"][field] not in choices:
                    raise ValueError("Install the local model file: " + node["inputs"][field])
    return graph


def validate_local_profile(profile: str, base: str = ""):
    from types import SimpleNamespace
    from agent.generation_runtime import GenerationSettings
    if profile not in {"image", "video"}:
        raise ValueError("Unknown local profile")
    job = SimpleNamespace(id="setup-check", kind=profile, model=IMAGE_MODEL if profile == "image" else VIDEO_MODEL,
                          prompt="A small white circle", settings=GenerationSettings(width=512, height=512, duration_seconds=1))
    validate_comfy_workflow(job, base)
    return {"workflow_ready": True, "render_tested": False, "detail": "Required models and nodes are present. A render test is still needed."}


def generate(job, checkpoint, cancelled) -> tuple[bytes, str]:
    """Submit once, persist remote identity immediately, then poll with bounded waits."""
    def wait():
        if cancelled.wait(3):
            raise InterruptedError("Stopped waiting. A cloud provider may still finish and charge for the job.")
        if time.monotonic() > deadline:
            raise TimeoutError("Generation exceeded 30 minutes. The provider may still be processing it.")
    deadline = time.monotonic() + 1800
    if cancelled.is_set():
        raise InterruptedError("Generation cancelled before submission.")
    if not re.fullmatch(r"[a-zA-Z0-9._ /-]{1,200}", job.model) or ".." in job.model:
        raise ValueError("Invalid model identifier")
    if job.provider_id.startswith("gemini-"):
        headers = {"x-goog-api-key": config.gemini.api_key}
        if not config.gemini.api_key:
            raise ValueError("Add your Gemini API key in Creations settings.")
        model = quote(job.model, safe="-._")
        if job.kind == "image":
            data = request("POST", f"{GOOGLE}/models/{model}:generateContent", headers=headers,
                payload={"contents": [{"parts": [{"text": job.prompt}]}], "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]}})
            for candidate in data.get("candidates", []):
                for part in candidate.get("content", {}).get("parts", []):
                    inline = part.get("inlineData") or part.get("inline_data") or {}
                    if inline.get("data"):
                        return base64.b64decode(inline["data"], validate=True), "image"
            raise RuntimeError("Provider returned no image. Check content restrictions or use another prompt.")
        data = request("POST", f"{GOOGLE}/models/{model}:predictLongRunning", headers=headers,
            payload={"instances": [{"prompt": job.prompt}], "parameters": {"sampleCount": 1, "durationSeconds": 4, "aspectRatio": "16:9"}})
        operation = str(data.get("name") or "")
        if not re.fullmatch(r"[A-Za-z0-9._/-]+", operation) or ".." in operation:
            raise RuntimeError("Provider returned no valid operation ID.")
        checkpoint(operation)
        while True:
            wait()
            data = request("GET", f"{GOOGLE}/{operation}", headers=headers)
            if data.get("error"):
                raise RuntimeError("Video provider rejected the operation: " + str(data["error"].get("message", "unknown error"))[:400])
            if data.get("done"):
                samples = data.get("response", {}).get("generateVideoResponse", {}).get("generatedSamples", [])
                if not samples:
                    raise RuntimeError("Provider returned no video; the request may have been filtered.")
                return google_download(samples[0]["video"]["uri"], config.gemini.api_key), "video"
    if job.provider_id == "minimax-video":
        headers = {"Authorization": "Bearer " + config.minimax_api_key}
        data = request("POST", "https://api.minimax.io/v1/video_generation", headers=headers,
            payload={"model": job.model, "prompt": job.prompt, "duration": 6, "resolution": "768P"})
        task = str(data.get("task_id") or "")
        if not task:
            raise RuntimeError("MiniMax did not accept the task. Check model access and account balance.")
        checkpoint(task)
        while True:
            wait()
            data = request("GET", "https://api.minimax.io/v1/query/video_generation?task_id=" + quote(task, safe=""), headers=headers)
            if data.get("status") == "Fail" or data.get("base_resp", {}).get("status_code", 0):
                raise RuntimeError("MiniMax video generation failed.")
            if data.get("status") == "Success":
                file = request("GET", "https://api.minimax.io/v1/files/retrieve?file_id=" + quote(str(data["file_id"]), safe=""), headers=headers)
                return public_download(file["file"]["download_url"]), "video"
    if job.provider_id != "comfyui-local":
        raise ValueError("Unknown generation provider.")
    base = comfy_base()
    graph = validate_comfy_workflow(job)
    data = request("POST", base + "/prompt", payload={"prompt": graph, "client_id": job.id})
    prompt_id = str(data.get("prompt_id") or "")
    if not prompt_id or data.get("node_errors"):
        raise RuntimeError("ComfyUI rejected the workflow. Check the installed model files.")
    checkpoint(prompt_id)
    while True:
        wait()
        data = request("GET", base + "/history/" + quote(prompt_id, safe="")).get(prompt_id, {})
        if data.get("status", {}).get("status_str") == "error":
            raise RuntimeError("ComfyUI generation failed. Check GPU memory and the local runtime log.")
        outputs = data.get("outputs", {}).get("9", {})
        files = outputs.get("images") or outputs.get("videos") or outputs.get("gifs") or []
        if files:
            file = files[0]
            from urllib.parse import urlencode
            url = base + "/view?" + urlencode({"filename": file["filename"], "subfolder": file.get("subfolder", ""), "type": "output"})
            with httpx.Client(timeout=90, trust_env=False, follow_redirects=False) as client:
                with client.stream("GET", url) as response:
                    response.raise_for_status()
                    output = bytearray()
                    for chunk in response.iter_bytes():
                        output.extend(chunk)
                        if len(output) > 256_000_000:
                            raise RuntimeError("Local output exceeds 256 MB.")
            return bytes(output), job.kind
