"""Vector search without LangChain: same splits, same search results, and old indexes convert without loss."""
import hashlib
import os
import pickle

import numpy as np
import pytest

from agent import index_integrity
from agent.vector_store import LEGACY, STORE, VECTORS, Document, VectorStore, split_text


class Embedder:
    """Deterministic 16-d vectors from character counts: enough to rank texts consistently."""

    def _vec(self, text):
        v = np.zeros(16, dtype=np.float32)
        for ch in text.lower():
            v[ord(ch) % 16] += 1.0
        return (v / (np.linalg.norm(v) or 1.0)).tolist()

    def embed_documents(self, texts):
        return [self._vec(t) for t in texts]

    def embed_query(self, text):
        return self._vec(text)

    def __call__(self, text):  # LangChain treats some embedders as plain functions
        return self._vec(text)


TEXTS = ["The user prefers dark roast coffee.", "Their dog is called Biscuit.", "They live in Edmonton.",
         "Meeting with Sam moved to Friday.", "Favourite editor: VS Code."]
LONG = ("Paragraph one about gardens and soil.\n\n" + "Tomatoes need sun. " * 80 + "\n\nShort tail.\n" +
        "A line without many breaks " * 60)


def langchain():
    return pytest.importorskip("langchain_community.vectorstores")


def test_split_matches_langchains_recursive_splitter():
    splitter_module = pytest.importorskip("langchain_text_splitters", reason="parity needs LangChain installed")
    ref = splitter_module.RecursiveCharacterTextSplitter(chunk_size=950, chunk_overlap=160)
    for text in [LONG, "short", "x" * 3000, "\n\n".join(TEXTS * 40)]:
        assert split_text(text) == [c.strip() for c in ref.split_text(text.strip()) if c.strip()]


def test_round_trip_never_writes_or_reads_a_pickle(tmp_path):
    store = VectorStore.from_texts(TEXTS, Embedder(), metadatas=[{"n": i} for i in range(5)], ids=list("abcde"))
    store.save_local(tmp_path)
    assert {p.name for p in tmp_path.iterdir()} == {VECTORS, STORE}
    loaded = VectorStore.load_local(tmp_path, Embedder())
    assert loaded.ids == list("abcde") and loaded.docs[1] == Document(TEXTS[1], {"n": 1})
    assert loaded.similarity_search("dog Biscuit", k=1)[0].page_content == TEXTS[1]


def test_old_langchain_index_converts_with_identical_search_results(tmp_path):
    FAISS = langchain().FAISS
    old = FAISS.from_texts(TEXTS, Embedder(), metadatas=[{"memory_id": f"m{i}"} for i in range(5)], ids=[f"m{i}" for i in range(5)])
    old.save_local(str(tmp_path))
    index_integrity.pin(tmp_path)
    expected = [(d.page_content, d.metadata, round(s, 4)) for d, s in old.similarity_search_with_score("coffee roast", k=5)]

    converted = VectorStore.load_local(tmp_path, Embedder())
    got = [(d.page_content, d.metadata, round(s, 4)) for d, s in converted.similarity_search_with_score("coffee roast", k=5)]
    assert got == expected and converted.ids == [f"m{i}" for i in range(5)]
    assert not any((tmp_path / name).exists() for name in LEGACY)  # converted once, pickle gone
    assert VectorStore.load_local(tmp_path, Embedder()).ids == converted.ids


def test_a_tampered_or_malicious_old_index_is_never_unpickled(tmp_path):
    FAISS = langchain().FAISS
    FAISS.from_texts(TEXTS, Embedder()).save_local(str(tmp_path))
    index_integrity.pin(tmp_path)

    class Exploit:
        def __reduce__(self):
            return (os.system, ("echo pwned",))
    payload = pickle.dumps((Exploit(), {0: "x"}))
    (tmp_path / "index.pkl").write_bytes(payload)
    with pytest.raises(index_integrity.TamperedIndex):  # the pin no longer matches
        VectorStore.load_local(tmp_path, Embedder())
    (tmp_path / "index.pkl.sha256").write_text(hashlib.sha256(payload).hexdigest())  # even if the pin is forged
    with pytest.raises(pickle.UnpicklingError, match="not part of a saved index"):
        VectorStore.load_local(tmp_path, Embedder())


def _local_embedder(monkeypatch):
    from agent import embeddings
    from config import config
    monkeypatch.setattr(config, "memory_partition_enabled", False, raising=False)
    monkeypatch.setattr(config.embedding, "provider", "local", raising=False)
    monkeypatch.setattr(embeddings, "local_embeddings",
                        lambda: (Embedder(), {"available": True, "provider": "local", "model": "test", "detail": "Ready"}))


def test_memory_opens_an_index_saved_by_11_7(tmp_path, monkeypatch):
    FAISS = langchain().FAISS
    _local_embedder(monkeypatch)
    folder = tmp_path / "memory"
    FAISS.from_texts(["bootstrap", "The user's dog is called Biscuit."], Embedder(),
                     metadatas=[{"bootstrap": True}, {"id": "m1", "memory_id": "m1", "type": "fact"}],
                     ids=["boot", "m1"]).save_local(str(folder))
    index_integrity.pin(folder)
    from agent.memory import AgentMemory
    memory = AgentMemory(memory_path=str(folder))
    assert isinstance(memory.vector_store, VectorStore) and "m1" in memory.vector_store.ids
    hits = memory._search_store(memory.vector_store, "dog Biscuit", k=2)
    assert hits and hits[0][0].page_content == "The user's dog is called Biscuit."
    assert not (folder / "index.pkl").exists()


def test_document_search_opens_an_index_saved_by_11_7(tmp_path):
    FAISS = langchain().FAISS
    from agent.document_store import DocumentStore
    index_dir = tmp_path / "doc_index"
    FAISS.from_texts(["bootstrap", "Invoice 1042 is due on March 3."], Embedder(),
                     metadatas=[{"bootstrap": True}, {"doc_id": "d1", "chunk": 0}]).save_local(str(index_dir))
    index_integrity.pin(index_dir)
    store = DocumentStore(Embedder(), str(index_dir), str(tmp_path / "documents.json"))
    assert isinstance(store.vector_store, VectorStore) and store.index_health == "healthy"
    assert store.vector_store.similarity_search("invoice due", k=1)[0].metadata["doc_id"] == "d1"
