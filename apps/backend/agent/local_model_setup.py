"""Owner-requested downloads through the user's existing local model runtime."""
from __future__ import annotations

import threading
import time
import uuid
from urllib.parse import urlsplit

import httpx
from config import config

PRESETS = {
    "ollama": [{"id": "qwen2.5:3b", "label": "Qwen 2.5 · 3B", "detail": "Smaller download; start here on a modest PC."},
               {"id": "qwen2.5:7b", "label": "Qwen 2.5 · 7B", "detail": "More capable; needs more free memory."}],
    "lmstudio": [{"id": "ibm/granite-4-micro", "label": "Granite 4 Micro", "detail": "Compact chat/tool model from LM Studio's catalog."}],
}


def test_response(provider: str, model: str = "", base_url: str = ""):
    from agent.lean.provider import ChatClient, Endpoint, resolve_endpoint
    from agent.lean.policy import redact_secrets
    endpoint = resolve_endpoint(provider, model)
    if base_url:
        base = base_url.rstrip("/").removesuffix("/v1")
        endpoint = Endpoint(base + "/v1", endpoint.api_key, endpoint.model, provider, True)
    if not endpoint.model or endpoint.model == "default":
        return {"ok": False, "check": "generation", "model": endpoint.model, "error_code": "configuration", "message": "Select and load a local model first."}
    client = ChatClient(endpoint)
    client._http.timeout = httpx.Timeout(45, connect=5)
    cancel = threading.Event()
    timer = threading.Timer(45, cancel.set)
    timer.start()
    try:
        result = client.stream_turn([{"role": "user", "content": "Reply with just OK."}], max_tokens=256, cancel=cancel)
        ok = bool(result.content.strip()) and not cancel.is_set()
        return {"ok": ok, "check": "generation", "model": endpoint.model, "error_code": "" if ok else "empty_response", "message": f"{endpoint.model} responded through EchoSpeak's chat adapter. Tool use was not tested." if ok else "The selected model did not produce a response in time."}
    except Exception as exc:
        return {"ok": False, "check": "generation", "model": endpoint.model, "error_code": "generation_failed", "message": redact_secrets(str(exc))[:500]}
    finally:
        timer.cancel()
        client.close()
_LOCK = threading.RLock()
_JOBS: dict[str, dict] = {}


def runtime_connection(provider: str):
    if provider not in PRESETS:
        raise ValueError("Choose LM Studio or Ollama.")
    configured = str(getattr(config.local, "base_url", "") or "")
    selected = str(getattr(config.local.provider, "value", config.local.provider))
    base = configured if selected == provider else ("http://localhost:11434" if provider == "ollama" else "http://localhost:1234")
    p = urlsplit(base)
    if p.scheme not in {"http", "https"} or p.hostname not in {"localhost", "127.0.0.1", "::1"} or p.username or p.password or p.query or p.fragment:
        raise ValueError("Model installation requires a loopback runtime address.")
    base = base.rstrip("/").removesuffix("/v1")
    key = str(getattr(config.local, "api_key", "") or "") if selected == provider else ""
    return base, ({"Authorization": "Bearer " + key} if key else {})


def catalog():
    rows = []
    for provider, presets in PRESETS.items():
        base, headers = runtime_connection(provider)
        try:
            with httpx.Client(timeout=2, trust_env=False, follow_redirects=False) as c:
                r = c.get(base + ("/api/tags" if provider == "ollama" else "/api/v1/models"), headers=headers)
                r.raise_for_status()
            ready, detail = True, "Runtime is reachable. Downloads begin only when you choose Download and load."
        except Exception:
            ready, detail = False, "Start this runtime's local server first. LM Studio requires its native v1 API for downloads."
        rows.append({"provider": provider, "base_url": base, "ready": ready, "detail": detail, "presets": presets})
    return {"items": rows, "detail": "Memory use depends on context and hardware. The final setup response check confirms your selected model can answer."}


def get_job(job_id: str):
    with _LOCK:
        if job_id not in _JOBS:
            raise ValueError("Download job is unavailable; the runtime may still have its download.")
        return dict(_JOBS[job_id])


def start(provider: str, model: str):
    if model not in {p["id"] for p in PRESETS.get(provider, [])}:
        raise ValueError("Choose one of the supported setup models.")
    base, headers = runtime_connection(provider)
    with _LOCK:
        for job in _JOBS.values():
            if job["status"] in {"downloading", "loading"}:
                if job["provider"] == provider and job["model"] == model:
                    return dict(job)
                raise ValueError("Wait for the current model download to finish.")
        job = {"id": uuid.uuid4().hex, "provider": provider, "model": model, "base_url": base,
               "status": "downloading", "progress": 0.0, "detail": "Requesting download from your runtime…"}
        _JOBS[job["id"]] = job
        # Bound the in-memory owner UI history.
        for old in list(_JOBS)[:-20]:
            _JOBS.pop(old, None)
    threading.Thread(target=_download, args=(job, headers), daemon=True, name="local-model-download").start()
    return dict(job)


def _download(job, headers):
    def update(**fields):
        with _LOCK:
            job.update(fields)
    try:
        base, model = job["base_url"], job["model"]
        with httpx.Client(timeout=httpx.Timeout(1800, connect=10), headers=headers, trust_env=False, follow_redirects=False) as c:
            if job["provider"] == "ollama":
                import json
                with c.stream("POST", base + "/api/pull", json={"model": model, "stream": True}) as response:
                    response.raise_for_status()
                    success = False
                    for line in response.iter_lines():
                        if not line:
                            continue
                        event = json.loads(line)
                        if event.get("error"):
                            raise ValueError(str(event["error"]))
                        total = event.get("total") or 0
                        update(detail=str(event.get("status", "Downloading…")), progress=min(1, event.get("completed", 0) / total) if total else 0)
                        success = event.get("status") == "success"
                    if not success:
                        raise ValueError("Ollama ended the download before reporting success.")
                update(status="loading", detail="Loading your model…")
                response = c.post(base + "/api/generate", json={"model": model, "prompt": "", "stream": False, "keep_alive": "10m"})
                response.raise_for_status()
                if response.json().get("error"):
                    raise ValueError(str(response.json()["error"]))
            else:
                response = c.post(base + "/api/v1/models/download", json={"model": model})
                response.raise_for_status()
                data = response.json()
                deadline = time.monotonic() + 7200
                while data.get("status") not in {"completed", "already_downloaded"}:
                    if data.get("status") in {"failed", "paused"} or not data.get("job_id"):
                        raise ValueError("LM Studio paused or failed the download. Check its download manager.")
                    if time.monotonic() > deadline:
                        raise TimeoutError("Download is still in LM Studio. Check its download manager before trying again.")
                    update(detail="Downloading in LM Studio…", remote_job_id=data["job_id"])
                    time.sleep(2)
                    response = c.get(base + "/api/v1/models/download/status/" + data["job_id"])
                    response.raise_for_status()
                    data = response.json()
                update(status="loading", detail="Loading your model…")
                response = c.post(base + "/api/v1/models/load", json={"model": model, "context_length": 4096})
                response.raise_for_status()
                data = response.json()
                if data.get("status") != "loaded":
                    raise ValueError("LM Studio did not confirm that the model loaded.")
                update(model=str(data.get("instance_id") or model))
        update(status="completed", progress=1.0, detail="Downloaded and loaded. Select Use this model, then finish setup's response check.")
    except Exception as exc:
        from agent.lean.policy import redact_secrets
        update(status="failed", detail=redact_secrets(str(exc))[:500])
