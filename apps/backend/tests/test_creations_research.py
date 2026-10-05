"""10.3 integration contracts without paid API calls or model downloads."""
import base64
import hashlib
import io
import threading
from types import SimpleNamespace

import pytest
from PIL import Image

from agent import generation_providers as providers
from agent import generation_service as service
from agent.generation_runtime import GenerationJob, GenerationJobStore
from agent.media_library import MediaLibraryStore
from agent.research_notebook import ResearchNotebook, TTL


def png():
    out = io.BytesIO()
    Image.new("RGB", (32, 24), "blue").save(out, format="PNG")
    return out.getvalue()


def job(**kwargs):
    return GenerationJob(idempotency_key="same", session_id="chat-a", project_id="", kind="image",
                         provider_id="gemini-images", model="test-model", prompt="blue sky", **kwargs)


def test_notebook_scope_pagination_preservation_expiry(tmp_path):
    book = ResearchNotebook(tmp_path)
    source = book.retain("a", url="https://example.com/paper", title="Study", text="a" * 12000 + "Conclusion", inspected=True)
    book.retain("a", url="https://example.com/paper", title="Search", text="snippet")
    assert book.read("a", source, 12000)["text"] == "Conclusion"
    assert book.read("a", source)["next_offset"] == 12000
    with pytest.raises(ValueError):
        book.read("b", source)
    book.notes("a", "Unanswered: replication")
    assert book.notes("b") == ""
    with book.connect() as db:
        db.execute("UPDATE sources SET updated=updated-?", (TTL + 1,))
    assert book.sources("a") == []
    book.clear("a")
    assert book.notes("a") == ""


def test_notebook_large_metadata_never_corrupts_json(tmp_path):
    book = ResearchNotebook(tmp_path)
    source = book.retain("a", url="https://example.com", title="Title", text="Evidence", metadata={"text": "x" * 50000})
    assert isinstance(book.read("a", source)["metadata"], dict)


def test_cloud_always_asks_and_background_denies():
    from agent.lean.policy import evaluate
    args = {"provider": "gemini-images", "prompt": "sky"}
    assert evaluate("create_media", args, secrets=[]).action == "ask"
    assert evaluate("create_media", args, secrets=[], interactive=False).action == "deny"
    assert evaluate("create_media", {**args, "provider": "comfyui-local"}, secrets=[]).action == "allow"


def test_google_image_contract(monkeypatch):
    monkeypatch.setattr(providers.config.gemini, "api_key", "dummy")
    seen = []
    def request(method, url, **kw):
        seen.append((method, url, kw))
        return {"candidates": [{"content": {"parts": [{"inlineData": {"data": base64.b64encode(png()).decode()}}]}}]}
    monkeypatch.setattr(providers, "request", request)
    data, kind = providers.generate(job(), lambda _: None, threading.Event())
    assert kind == "image" and data == png()
    assert seen[0][0] == "POST" and seen[0][2]["headers"] == {"x-goog-api-key": "dummy"}
    assert seen[0][2]["payload"]["contents"][0]["parts"][0]["text"] == "blue sky"


def test_video_polls_persisted_remote_id_without_resubmitting(monkeypatch):
    monkeypatch.setattr(providers.config.gemini, "api_key", "dummy")
    pending = job().model_copy(update={"kind": "video", "provider_id": "gemini-video"})
    replies = iter([{"name": "models/test/operations/123"}, {"done": False},
        {"done": True, "response": {"generateVideoResponse": {"generatedSamples": [{"video": {"uri": "https://generativelanguage.googleapis.com/file"}}]}}}])
    calls, checkpoints = [], []
    monkeypatch.setattr(providers, "request", lambda method, *a, **k: (calls.append(method), next(replies))[1])
    monkeypatch.setattr(providers, "google_download", lambda *a: b"video")
    cancel = SimpleNamespace(is_set=lambda: False, wait=lambda _: False)
    assert providers.generate(pending, checkpoints.append, cancel) == (b"video", "video")
    assert calls == ["POST", "GET", "GET"] and checkpoints == ["models/test/operations/123"]


def test_google_download_never_sends_key_to_other_host(monkeypatch):
    with pytest.raises(RuntimeError, match="host"):
        providers.google_download("https://attacker.example/video", "secret")
    from agent import safe_web_retrieval
    sent = []
    monkeypatch.setattr(safe_web_retrieval, "_request_pinned_public_url", lambda url, **kw: (sent.append(kw["headers"]), (302, {"location": "https://cdn.example/signed"}, b""))[1])
    monkeypatch.setattr(providers, "public_download", lambda url: b"safe")
    assert providers.google_download("https://generativelanguage.googleapis.com/file", "secret") == b"safe"
    assert sent == [{"x-goog-api-key": "secret"}]


@pytest.mark.parametrize("url", ["https://example.com", "http://localhost@evil.example", "http://127.0.0.1:8188?key=secret"])
def test_comfy_only_connects_to_loopback(monkeypatch, url):
    monkeypatch.setattr(providers.config, "comfyui_base_url", url)
    with pytest.raises(ValueError):
        providers.comfy_base()


def test_worker_saves_verified_asset_without_project_and_supports_archive(monkeypatch, tmp_path):
    jobs, library = GenerationJobStore(tmp_path / "jobs"), MediaLibraryStore(tmp_path / "library")
    monkeypatch.setattr(service, "get_generation_job_store", lambda: jobs)
    monkeypatch.setattr(service, "get_media_library_store", lambda: library)
    monkeypatch.setattr(service.config, "allow_generation_actions", True)
    monkeypatch.setattr(service, "generate", lambda *a: (png(), "image"))
    pending = job()
    service._worker(pending, threading.Event())
    done = jobs.get(pending.id)
    assert done.status == "completed"
    asset = library.get(done.output_asset_ids[0])
    assert asset.storage_scope == "library" and asset.project_id == ""
    data = (library.root / asset.project_relative_path).read_bytes()
    assert hashlib.sha256(data).hexdigest() == asset.sha256
    library.update(asset.id, name="Blue sky", archived=True)
    assert library.get(asset.id).archived and (library.root / asset.project_relative_path).read_bytes() == data


def test_cancel_before_submission_does_not_call_provider(monkeypatch, tmp_path):
    jobs = GenerationJobStore(tmp_path)
    monkeypatch.setattr(service, "get_generation_job_store", lambda: jobs)
    monkeypatch.setattr(service, "generate", lambda *a: pytest.fail("cancelled job submitted"))
    event = threading.Event(); event.set()
    pending = job()
    service._worker(pending, event)
    assert jobs.get(pending.id).status == "cancelled"


def test_corrupt_provider_output_fails_without_registering(monkeypatch, tmp_path):
    jobs = GenerationJobStore(tmp_path)
    monkeypatch.setattr(service, "get_generation_job_store", lambda: jobs)
    monkeypatch.setattr(service.config, "allow_generation_actions", True)
    monkeypatch.setattr(service, "generate", lambda *a: (b"<html>not media</html>", "image"))
    pending = job()
    service._worker(pending, threading.Event())
    assert jobs.get(pending.id).status == "failed"
    assert not jobs.get(pending.id).output_asset_ids


def test_read_plaintext_and_pdf():
    from agent.safe_web_retrieval import _extract_page
    from pypdf import PdfWriter
    body = b"One\nTwo\nhttps://example.com"
    assert _extract_page(body, content_type="text/plain", max_text_chars=1000)[1] == body.decode()
    out = io.BytesIO(); writer = PdfWriter(); writer.add_blank_page(width=100, height=100); writer.write(out)
    assert "[Page 1]" in _extract_page(out.getvalue(), content_type="application/pdf", max_text_chars=1000)[1]


def test_tavily_selection_and_authenticated_request(monkeypatch):
    import httpx
    from agent.web_search_providers import TavilyProvider, resolve_provider_order
    calls = []
    class Client:
        def __init__(self, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, url, **kwargs):
            calls.append(kwargs)
            return httpx.Response(200, json={"results": [{"title": "Source", "url": "https://example.com", "content": "evidence"}]}, request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx, "Client", Client)
    result = TavilyProvider("secret").search("evidence")
    assert result.hits[0].snippet == "evidence"
    assert calls[0]["headers"]["Authorization"] == "Bearer secret"
    assert resolve_provider_order(SimpleNamespace(web_search_provider="tavily")) == ["tavily", "duckduckgo"]


def test_onboarding_persists_skip_and_preserves_existing_installs(monkeypatch, tmp_path):
    from api.routes import onboarding
    from agent import threads
    monkeypatch.setattr(onboarding, "STATE_PATH", tmp_path / "onboarding.json")
    monkeypatch.setattr(threads, "get_thread_manager", lambda: SimpleNamespace(list_threads=lambda **k: []))
    assert onboarding.status()["show"]
    onboarding.progress(onboarding.Progress(status="skipped", step=2))
    assert not onboarding.status()["show"] and onboarding.status()["step"] == 2
    monkeypatch.setattr(onboarding, "STATE_PATH", tmp_path / "existing.json")
    monkeypatch.setattr(threads, "get_thread_manager", lambda: SimpleNamespace(list_threads=lambda **k: [SimpleNamespace(message_count=3)]))
    assert not onboarding.status()["show"]
