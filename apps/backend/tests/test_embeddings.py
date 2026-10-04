"""Local embeddings run on onnxruntime, not PyTorch, and memory survives a model change."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

onnx = pytest.importorskip("onnx")
pytest.importorskip("onnxruntime")
tokenizers = pytest.importorskip("tokenizers")

from agent import embeddings as emb  # noqa: E402
from agent.lean.policy import is_untrusted_source  # noqa: E402

WORDS = ["[PAD]", "[UNK]"] + "the cat sat on mat dog ran in park echo likes coffee tea my name is ty".split()


def _stand_in_model(folder: Path, dim: int = 384) -> Path:
    """A tiny BERT-shaped ONNX model (token embedding lookup) plus a word tokenizer."""
    from onnx import TensorProto, helper, numpy_helper

    (folder / "onnx").mkdir(parents=True, exist_ok=True)
    tok = tokenizers.Tokenizer(tokenizers.models.WordLevel(vocab={w: i for i, w in enumerate(WORDS)}, unk_token="[UNK]"))
    tok.pre_tokenizer = tokenizers.pre_tokenizers.Whitespace()
    tok.save(str(folder / "tokenizer.json"))
    table = np.random.default_rng(0).normal(size=(len(WORDS), dim)).astype(np.float32)
    table[0] = 1000.0  # padding rows dominate unless the attention mask is applied
    inputs = [helper.make_tensor_value_info(n, TensorProto.INT64, ["B", "S"]) for n in ("input_ids", "attention_mask", "token_type_ids")]
    output = helper.make_tensor_value_info("last_hidden_state", TensorProto.FLOAT, ["B", "S", dim])
    graph = helper.make_graph(
        [helper.make_node("Gather", ["table", "input_ids"], ["last_hidden_state"], axis=0)],
        "stand_in", inputs, [output], [numpy_helper.from_array(table, "table")],
    )
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
    model.ir_version = 8
    onnx.save(model, str(folder / "onnx" / "model.onnx"))
    return folder


def test_onnx_embeddings_are_mean_pooled_unit_vectors_and_ignore_padding(tmp_path):
    embedder = emb.OnnxEmbeddings(_stand_in_model(tmp_path))
    alone = embedder.embed_query("the cat sat")
    batched = embedder.embed_documents(["the cat sat", "my name is ty and echo likes coffee and tea in the park"])
    assert len(alone) == 384
    assert abs(float(np.linalg.norm(alone)) - 1.0) < 1e-5
    assert np.allclose(alone, batched[0], atol=1e-5)
    assert float(np.dot(batched[0], batched[1])) < 0.9


def test_without_the_model_and_no_download_memory_falls_back_quietly(tmp_path, monkeypatch):
    monkeypatch.setattr(emb, "local_model_dir", lambda: tmp_path / "missing")
    monkeypatch.setenv("ECHOSPEAK_TESTING", "1")
    embedder, health = emb.local_embeddings()
    assert embedder is None
    assert health["available"] is False
    assert "not downloaded" in health["detail"]


def test_memory_uses_the_local_model_and_rebuilds_the_index_when_the_size_changes(tmp_path, monkeypatch):
    from agent import memory as memory_mod
    from config import config, ModelProvider

    model_dir = _stand_in_model(tmp_path / "model")
    monkeypatch.setattr(emb, "local_model_dir", lambda: model_dir)
    monkeypatch.setattr(config.embedding, "provider", ModelProvider.OPENAI, raising=False)
    monkeypatch.setattr(config.openai, "api_key", "", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    store_dir = tmp_path / "memory"
    first = memory_mod.AgentMemory(str(store_dir))
    assert first.embedding_health["provider"] == "onnx_local"
    assert first.use_faiss
    first.add_memory_item("my name is ty", memory_type="profile")
    first._save_to_disk()
    assert first.vector_store.index.d == 384

    # A different embedding model (8 dimensions) must not search the old 384-d vectors.
    small = _stand_in_model(tmp_path / "small", dim=8)
    monkeypatch.setattr(emb, "local_model_dir", lambda: small)
    second = memory_mod.AgentMemory(str(store_dir))
    assert second.vector_store.index.d == 8
    hits = second.retrieve_relevant("my name", k=3)
    assert any("my name is ty" in doc.page_content for doc in hits)


def test_uploaded_documents_count_as_outside_content():
    assert is_untrusted_source("document_search")


def test_agents_can_search_uploaded_documents_in_their_project(monkeypatch):
    import threading
    import uuid

    from agent.lean import runtime as lean_runtime

    calls: list[tuple[str, str, str]] = []

    class Store:
        enabled = True

        def query(self, query, k=4, *, project_id="", session_id=""):
            calls.append((query, project_id, session_id))
            return ("[doc: notes.pdf #2] The launch is on Friday.", [{"filename": "notes.pdf"}]) if "launch" in query else ("", [])

    session_id = f"docs-{uuid.uuid4().hex[:8]}"
    session = lean_runtime.LeanSession(
        agent=type("Agent", (), {"memory": None, "document_store": Store()})(), session_id=session_id,
        request_id="req-docs", emit=lambda event: None, cancel=threading.Event(), source="web",
    )
    tools = {tool.name: tool for tool in session._native_tools(session.personas.default(), 0)}
    assert "document_search" in tools
    assert "Friday" in tools["document_search"].func({"query": "launch date"})
    assert tools["document_search"].func({"query": "budget"}) == "No uploaded document matches."
    assert calls[0] == ("launch date", "", session_id)
