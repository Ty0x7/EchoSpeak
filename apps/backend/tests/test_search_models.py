"""Search by meaning: the local model catalog, the ONNX reranker, LM Studio's models and the one-click switch."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

pytest.importorskip("onnxruntime")
tokenizers = pytest.importorskip("tokenizers")

from agent import embeddings as emb  # noqa: E402
from config import config  # noqa: E402

WORDS = ["[PAD]", "[UNK]"] + "the cat sat on mat dog ran in park echo likes coffee tea my name is ty".split()
# Building the tiny stand-in ONNX graphs needs the onnx package; only those tests skip without it.
needs_onnx = pytest.mark.skipif(__import__("importlib").util.find_spec("onnx") is None, reason="onnx is not installed")


def _stand_in_model(folder: Path, dim: int = 384) -> Path:
    from tests.test_embeddings import _stand_in_model as build

    return build(folder, dim)


def _stand_in_reranker(folder: Path) -> Path:
    """A cross-encoder stand-in: each word has a relevance weight; a pair's score is their sum."""
    import onnx
    from onnx import TensorProto, helper, numpy_helper

    (folder / "onnx").mkdir(parents=True, exist_ok=True)
    tok = tokenizers.Tokenizer(tokenizers.models.WordLevel(vocab={w: i for i, w in enumerate(WORDS)}, unk_token="[UNK]"))
    tok.pre_tokenizer = tokenizers.pre_tokenizers.Whitespace()
    tok.save(str(folder / "tokenizer.json"))
    weights = np.zeros((len(WORDS), 1), dtype=np.float32)
    weights[WORDS.index("dog")] = 5.0
    weights[WORDS.index("park")] = 2.0
    inputs = [helper.make_tensor_value_info(n, TensorProto.INT64, ["B", "S"]) for n in ("input_ids", "attention_mask", "token_type_ids")]
    output = helper.make_tensor_value_info("logits", TensorProto.FLOAT, ["B", 1])
    nodes = [helper.make_node("Gather", ["table", "input_ids"], ["per_token"], axis=0),
             helper.make_node("ReduceSum", ["per_token", "axis"], ["logits"], keepdims=0)]
    graph = helper.make_graph(nodes, "stand_in_rerank", inputs, [output],
                              [numpy_helper.from_array(weights, "table"), numpy_helper.from_array(np.array([1], dtype=np.int64), "axis")])
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
    model.ir_version = 8
    onnx.save(model, str(folder / emb.RERANKER.files[0]))
    return folder


# ── the catalog and the choice ──────────────────────────────────────────────
@pytest.mark.parametrize("stored, key", [("fast", "fast"), ("better", "better"), ("off", "off"), ("nonsense", "fast"), ("", "fast")])
def test_the_choice_reads_settings_and_falls_back_to_fast(monkeypatch, stored, key):
    monkeypatch.setattr(config, "embedding_local_model", stored, raising=False)
    assert emb.chosen_key() == key


def test_off_means_keywords_with_no_model(monkeypatch):
    monkeypatch.setattr(config, "embedding_local_model", "off", raising=False)
    embedder, health = emb.local_embeddings()
    assert embedder is None and "keyword search only" in health["detail"]


def test_the_better_model_is_pinned_and_frames_queries_and_documents_differently():
    better = emb.CATALOG["better"]
    assert better.revision and len(better.revision) == 40 and better.dim == 768 and better.pooling == "output"
    assert better.files[0].endswith("model_q4.onnx") and better.files[-1] == "tokenizer.json"
    seen: list[list[str]] = []
    embedder = emb.OnnxEmbeddings(Path("unused"), better)
    embedder._embed = lambda texts: seen.append(texts) or [[1.0] for _ in texts]  # type: ignore[method-assign]
    embedder.embed_query("what does my dog eat")
    embedder.embed_documents(["Biscuit eats salmon."])
    assert seen == [["task: search result | query: what does my dog eat"], ["title: none | text: Biscuit eats salmon."]]


@needs_onnx
def test_fast_keeps_its_plain_inputs_and_old_indexes(tmp_path):
    embedder = emb.OnnxEmbeddings(_stand_in_model(tmp_path))
    assert embedder.model.key == "fast" and len(embedder.embed_query("the cat sat")) == 384


def test_status_lists_every_model_and_the_reranker(monkeypatch, tmp_path):
    monkeypatch.setattr(emb, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(config, "embedding_local_model", "better", raising=False)
    status = emb.local_status()
    assert status["choice"] == "better" and [m["key"] for m in status["models"]] == ["fast", "better"]
    assert not any(m["installed"] for m in status["models"]) and status["reranker"]["installed"] is False


# ── the reranker runs on ONNX ───────────────────────────────────────────────
@needs_onnx
def test_the_onnx_reranker_orders_by_relevance(tmp_path):
    reranker = emb.OnnxCrossEncoder(_stand_in_reranker(tmp_path))
    scores = reranker.predict([("dog", "my dog ran"), ("dog", "echo likes tea"), ("dog", "the park")])
    assert scores[0] > scores[2] > scores[1]


@needs_onnx
def test_the_document_store_prefers_the_onnx_reranker(monkeypatch, tmp_path):
    from agent.document_store import DocumentStore

    fake = emb.OnnxCrossEncoder(_stand_in_reranker(tmp_path))
    monkeypatch.setattr(emb, "local_reranker", lambda: fake)
    store = DocumentStore.__new__(DocumentStore)
    store.rerank_enabled, store._reranker, store.rerank_model, store.rerank_k = True, None, "x", 30
    assert store._get_reranker() is fake
    docs = [SimpleNamespace(page_content="echo likes tea"), SimpleNamespace(page_content="my dog ran in the park")]
    assert [d.page_content for d in store._rerank("dog", docs, 2)] == ["my dog ran in the park", "echo likes tea"]


def test_no_reranker_until_it_is_downloaded(monkeypatch, tmp_path):
    monkeypatch.setattr(emb, "DATA_DIR", str(tmp_path))
    assert emb.local_reranker() is None


# ── document uploads (regression: VectorStore lost add_documents when LangChain left in 11.8) ──
def test_the_vector_store_takes_documents_as_uploads_send_them():
    from agent.vector_store import Document, VectorStore

    class Letters:
        def embed_documents(self, texts):
            return [[float(t.count("a")), float(t.count("b")), 1.0] for t in texts]

        def embed_query(self, text):
            return self.embed_documents([text])[0]

    store = VectorStore(Letters())
    ids = store.add_documents([Document("aaa", {"doc_id": "d1"}), Document("bbb", {"doc_id": "d2"})])
    assert len(ids) == 2 and store.dim == 3
    [best] = store.similarity_search("aa", k=1)
    assert best.metadata == {"doc_id": "d1"}


# ── LM Studio's embedding models ────────────────────────────────────────────
class _Response:
    def __init__(self, status: int, data: Any) -> None:
        self.status_code, self._data = status, data

    def json(self) -> Any:
        return self._data


def test_lmstudio_lists_only_embedding_models(monkeypatch):
    import httpx

    class Client:
        def __init__(self, timeout: float) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url: str):
            assert url == "http://localhost:1234/api/v0/models"
            return _Response(200, {"data": [
                {"id": "google/gemma-4-e4b", "type": "vlm", "state": "loaded"},
                {"id": "text-embedding-qwen3-embedding-0.6b", "type": "embeddings", "state": "loaded"},
                {"id": "text-embedding-nomic-embed-text-v1.5", "type": "embeddings", "state": "not-loaded"},
            ]})

    monkeypatch.setattr(httpx, "Client", Client)
    assert emb.lmstudio_embedding_models("http://localhost:1234/v1") == [
        {"id": "text-embedding-qwen3-embedding-0.6b", "loaded": True},
        {"id": "text-embedding-nomic-embed-text-v1.5", "loaded": False},
    ]


def test_lmstudio_not_running_lists_nothing(monkeypatch):
    import httpx

    def boom(*a, **k):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "Client", boom)
    assert emb.lmstudio_embedding_models("http://localhost:1234") == []


# ── memory switches live ────────────────────────────────────────────────────
@needs_onnx
def test_memory_switches_models_without_a_restart(tmp_path, monkeypatch):
    from agent import memory as memory_mod
    from config import ModelProvider

    big = _stand_in_model(tmp_path / "big")
    monkeypatch.setattr(emb, "local_model_dir", lambda: big)
    monkeypatch.setattr(config.embedding, "provider", ModelProvider.OLLAMA, raising=False)
    memory = memory_mod.AgentMemory(str(tmp_path / "memory"))
    memory.add_memory_item("my name is ty", memory_type="profile")
    assert memory.vector_store.dim == 384

    small = _stand_in_model(tmp_path / "small", dim=8)
    monkeypatch.setattr(emb, "local_model_dir", lambda: small)
    result = memory.switch_embeddings()
    assert result["ok"] and result["indexed"] == 1 and memory.vector_store.dim == 8
    assert any("my name is ty" in d.page_content for d in memory.retrieve_relevant("my name", k=3))

    monkeypatch.setattr(config, "embedding_local_model", "off", raising=False)
    off = memory.switch_embeddings()
    assert off["faiss"] is False and memory.embeddings is None and memory.vector_store is None


# ── the one-click switch ────────────────────────────────────────────────────
@pytest.fixture
def switch(monkeypatch):
    from agent import memory as memory_mod
    from api.routes import memory as routes
    from api.routes import settings as settings_routes

    calls: dict[str, Any] = {"downloads": [], "patches": [], "switched": 0}
    monkeypatch.setattr(emb, "local_runtime_available", lambda: True)
    monkeypatch.setattr(emb, "download_model", lambda model: calls["downloads"].append(model.key))
    monkeypatch.setattr(settings_routes, "_apply_settings_patch", lambda patch: calls["patches"].append(patch))
    fake_memory = SimpleNamespace(embeddings=None, embedding_health={"available": False, "detail": "x"},
                                  switch_embeddings=lambda: calls.__setitem__("switched", calls["switched"] + 1) or {"indexed": 3})
    monkeypatch.setattr(memory_mod, "get_agent_memory", lambda *a: fake_memory)
    monkeypatch.setattr(emb, "lmstudio_embedding_models", lambda base: [])
    monkeypatch.setattr(config, "document_rag_enabled", False, raising=False)
    return routes, calls


def test_switching_to_better_downloads_saves_and_reindexes(switch):
    routes, calls = switch
    result = routes.set_search_model(routes.SearchModelRequest(choice="better", rerank=True))
    assert calls["downloads"] == ["better", "rerank"]
    assert calls["patches"] == [{"embedding": {"provider": "ollama"}, "embedding_local_model": "better", "doc_rerank_enabled": True}]
    assert calls["switched"] == 1 and result["reindexed"]["memories"] == 3


def test_switching_to_lmstudio_saves_its_model_and_downloads_nothing(switch):
    routes, calls = switch
    routes.set_search_model(routes.SearchModelRequest(choice="lmstudio", model="text-embedding-qwen3-embedding-0.6b"))
    assert calls["downloads"] == []
    assert calls["patches"] == [{"embedding": {"provider": "lmstudio", "model": "text-embedding-qwen3-embedding-0.6b"}}]


@pytest.mark.parametrize("request_args, code", [
    ({"choice": "nonsense"}, 400), ({"choice": "lmstudio"}, 400),
])
def test_bad_choices_are_refused_before_anything_changes(switch, request_args, code):
    from fastapi import HTTPException

    routes, calls = switch
    with pytest.raises(HTTPException) as err:
        routes.set_search_model(routes.SearchModelRequest(**request_args))
    assert err.value.status_code == code and calls["patches"] == [] and calls["downloads"] == []


def test_offline_mode_refuses_the_download_and_changes_nothing(switch, monkeypatch):
    from fastapi import HTTPException

    from agent.privacy import PrivacyBlocked

    routes, calls = switch

    def blocked(model):
        raise PrivacyBlocked("downloads", "Offline mode is on: downloads are off.")

    monkeypatch.setattr(emb, "download_model", blocked)
    with pytest.raises(HTTPException) as err:
        routes.set_search_model(routes.SearchModelRequest(choice="better"))
    assert err.value.status_code == 403 and "Offline" in err.value.detail and calls["patches"] == []
