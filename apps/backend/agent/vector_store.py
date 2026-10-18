"""Vector search for memory and documents, without LangChain.

EchoSpeak used three things from LangChain here: a Document (text plus
metadata), a FAISS wrapper (add texts, search by meaning, save and load), and a
text splitter. This module provides the same, in plain Python and NumPy:

- VectorStore keeps vectors in a NumPy array and searches with exact squared L2
  distance, the same results as the IndexFlatL2 LangChain built. A few thousand
  memories or document chunks search in well under a millisecond.
- It saves as vectors.npy plus store.json. Loading never unpickles anything, so
  a file changed on disk can't run code.
- An index saved by an older EchoSpeak (index.faiss + index.pkl) is converted
  once on load: its vectors are read with faiss and its texts through a
  restricted unpickler that recognises only LangChain's document classes, after
  the existing tamper check (agent/index_integrity.py). Nothing is re-embedded.
  If the old files can't be read, callers rebuild from their own records.
- split_text is LangChain's recursive character splitter, ported.
- OpenAICompatibleEmbeddings calls /embeddings on OpenAI or LM Studio directly,
  after the privacy check.
"""
from __future__ import annotations

import io
import json
import os
import pickle
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

import numpy as np
from loguru import logger

VECTORS, STORE = "vectors.npy", "store.json"
LEGACY = ("index.faiss", "index.pkl")


@dataclass
class Document:
    page_content: str
    metadata: dict[str, Any] = field(default_factory=dict)


class VectorStore:
    """Texts, their metadata and their vectors; exact nearest-neighbour search."""

    def __init__(self, embeddings: Any, dim: int = 0) -> None:
        self.embeddings = embeddings
        self.ids: list[str] = []
        self.docs: list[Document] = []
        self.vectors = np.zeros((0, dim), dtype=np.float32)

    @property
    def dim(self) -> int:
        return int(self.vectors.shape[1]) if self.vectors.ndim == 2 else 0

    @classmethod
    def from_texts(cls, texts: list[str], embeddings: Any, metadatas: Optional[list[dict]] = None,
                   ids: Optional[list[str]] = None) -> "VectorStore":
        store = cls(embeddings)
        store.add_texts(texts, metadatas=metadatas, ids=ids)
        return store

    def add_texts(self, texts: Iterable[str], metadatas: Optional[list[dict]] = None,
                  ids: Optional[list[str]] = None) -> list[str]:
        texts = [str(t) for t in texts]
        if not texts:
            return []
        metadatas = list(metadatas or [{} for _ in texts])
        ids = [str(i) for i in (ids or [uuid.uuid4().hex for _ in texts])]
        if len(metadatas) != len(texts) or len(ids) != len(texts):
            raise ValueError("texts, metadatas and ids must be the same length")
        vectors = np.asarray(self.embeddings.embed_documents(texts), dtype=np.float32)
        if vectors.ndim != 2 or vectors.shape[0] != len(texts):
            raise ValueError("the embedder returned the wrong number of vectors")
        if self.vectors.shape[0] and vectors.shape[1] != self.dim:
            raise ValueError(f"{vectors.shape[1]}-d vectors don't fit a {self.dim}-d index")
        self.vectors = vectors if not self.vectors.shape[0] else np.vstack([self.vectors, vectors])
        self.ids.extend(ids)
        self.docs.extend(Document(t, dict(m or {})) for t, m in zip(texts, metadatas))
        return ids

    def similarity_search_with_score(self, query: str, k: int = 4) -> list[tuple[Document, float]]:
        if not self.ids:
            return []
        q = np.asarray(self.embeddings.embed_query(query), dtype=np.float32)
        distances = ((self.vectors - q) ** 2).sum(axis=1)  # squared L2, as IndexFlatL2 reports
        order = np.argsort(distances, kind="stable")[: max(0, int(k))]
        return [(self.docs[i], float(distances[i])) for i in order]

    def similarity_search(self, query: str, k: int = 4) -> list[Document]:
        return [doc for doc, _ in self.similarity_search_with_score(query, k)]

    def get(self) -> dict[str, list]:
        return {"ids": list(self.ids), "documents": [d.page_content for d in self.docs],
                "metadatas": [dict(d.metadata) for d in self.docs]}

    # ── on disk ─────────────────────────────────────────────────────────

    def save_local(self, folder: str | Path) -> None:
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        stamp = f".tmp.{os.getpid()}.{time.time_ns()}"
        vectors, store = folder / (VECTORS + stamp), folder / (STORE + stamp)
        with vectors.open("wb") as handle:
            np.save(handle, self.vectors, allow_pickle=False)
        store.write_text(json.dumps({"format": 1, "dim": self.dim, "ids": self.ids,
                                     "docs": [{"text": d.page_content, "metadata": d.metadata} for d in self.docs]},
                                    ensure_ascii=False, default=str), encoding="utf-8")
        os.replace(vectors, folder / VECTORS)
        os.replace(store, folder / STORE)
        for name in LEGACY:  # converted: the old pickle is no longer needed
            (folder / name).unlink(missing_ok=True)
        (folder / "index.pkl.sha256").unlink(missing_ok=True)

    @classmethod
    def load_local(cls, folder: str | Path, embeddings: Any) -> "VectorStore":
        folder = Path(folder)
        if (folder / STORE).is_file() and (folder / VECTORS).is_file():
            data = json.loads((folder / STORE).read_text(encoding="utf-8"))
            store = cls(embeddings)
            store.vectors = np.load(folder / VECTORS, allow_pickle=False).astype(np.float32, copy=False)
            store.ids = [str(i) for i in data["ids"]]
            store.docs = [Document(str(d.get("text") or ""), dict(d.get("metadata") or {})) for d in data["docs"]]
            if store.vectors.shape[0] != len(store.ids) or len(store.ids) != len(store.docs):
                raise ValueError("the saved index is inconsistent")
            return store
        if all((folder / name).is_file() for name in LEGACY):
            store = _convert_legacy(folder, embeddings)
            store.save_local(folder)
            logger.info("Converted the vector index in {} from the LangChain format", folder)
            return store
        raise FileNotFoundError(f"No vector index in {folder}")


def has_index(folder: str | Path) -> bool:
    folder = Path(folder)
    return (folder / STORE).is_file() or (folder / LEGACY[1]).is_file()


# ── converting a LangChain FAISS index ──────────────────────────────────

class _Saved:
    """Stands in for LangChain's Document and InMemoryDocstore while their pickle is read."""

    def __setstate__(self, state: Any) -> None:
        self.__dict__["_state"] = state


_ALLOWED = {
    ("langchain_core.documents.base", "Document"), ("langchain.schema.document", "Document"),
    ("langchain.docstore.document", "Document"), ("langchain_community.docstore.in_memory", "InMemoryDocstore"),
    ("langchain.docstore.in_memory", "InMemoryDocstore"),
}


class _LegacyUnpickler(pickle.Unpickler):
    def find_class(self, module: str, name: str) -> Any:
        if (module, name) in _ALLOWED:
            return _Saved
        if (module, name) in {("builtins", "dict"), ("builtins", "list"), ("builtins", "set"), ("builtins", "object"),
                              ("copyreg", "_reconstructor")}:
            return super().find_class(module, name)
        raise pickle.UnpicklingError(f"{module}.{name} is not part of a saved index")  # anything else could run code


def _fields(saved: Any) -> dict[str, Any]:
    state = getattr(saved, "_state", saved)
    if isinstance(state, tuple):  # (__dict__, slots) pairs from some pickle protocols
        state = next((s for s in state if isinstance(s, dict)), {})
    if isinstance(state, dict) and isinstance(state.get("__dict__"), dict):
        return state["__dict__"]  # pydantic v2 models
    return state if isinstance(state, dict) else {}


def _convert_legacy(folder: Path, embeddings: Any) -> VectorStore:
    from agent.index_integrity import check
    check(folder)  # refuse a pickle that changed outside EchoSpeak
    with (folder / "index.pkl").open("rb") as handle:
        docstore, index_to_id = _LegacyUnpickler(io.BytesIO(handle.read())).load()
    stored = _fields(docstore).get("_dict") or {}
    import faiss  # reads the old vectors; nothing is embedded again
    index = faiss.read_index(str(folder / "index.faiss"))
    vectors = index.reconstruct_n(0, index.ntotal) if index.ntotal else np.zeros((0, index.d), dtype=np.float32)
    store = VectorStore(embeddings)
    store.vectors = np.asarray(vectors, dtype=np.float32)
    for position in range(index.ntotal):
        doc_id = str(index_to_id[position])
        fields = _fields(stored.get(doc_id))
        store.ids.append(doc_id)
        store.docs.append(Document(str(fields.get("page_content") or ""), dict(fields.get("metadata") or {})))
    return store


# ── splitting ───────────────────────────────────────────────────────────

def split_text(text: str, chunk_size: int = 950, chunk_overlap: int = 160,
               separators: tuple[str, ...] = ("\n\n", "\n", " ", "")) -> list[str]:
    """LangChain's RecursiveCharacterTextSplitter: split on the coarsest separator that keeps chunks small."""

    def merge(splits: list[str], separator: str) -> list[str]:
        chunks, current, total = [], [], 0
        for piece in splits:
            size = len(piece)
            if total + size + (len(separator) if current else 0) > chunk_size and current:
                chunk = separator.join(current).strip()
                if chunk:
                    chunks.append(chunk)
                while total > chunk_overlap or (total + size + (len(separator) if current else 0) > chunk_size and total > 0):
                    total -= len(current[0]) + (len(separator) if len(current) > 1 else 0)
                    current.pop(0)
            current.append(piece)
            total += size + (len(separator) if len(current) > 1 else 0)
        chunk = separator.join(current).strip()
        if chunk:
            chunks.append(chunk)
        return chunks

    def recurse(value: str, seps: tuple[str, ...]) -> list[str]:
        separator, rest = seps[-1], ()
        for i, candidate in enumerate(seps):
            if candidate == "" or candidate in value:
                separator, rest = candidate, seps[i + 1:]
                break
        splits = [s for s in (value.split(separator) if separator else list(value)) if s]
        out, good = [], []
        for piece in splits:
            if len(piece) < chunk_size:
                good.append(piece)
                continue
            if good:
                out.extend(merge(good, separator))
                good = []
            out.extend(recurse(piece, rest) if rest else [piece])
        if good:
            out.extend(merge(good, separator))
        return out

    clean = (text or "").strip()
    return [c for c in recurse(clean, separators) if c.strip()] if clean else []


# ── OpenAI-compatible embeddings ────────────────────────────────────────

class OpenAICompatibleEmbeddings:
    """POST /embeddings on OpenAI, LM Studio or another compatible server. Strings, never token arrays."""

    def __init__(self, model: str, base_url: str, api_key: str = "", timeout: float = 60.0) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def _embed(self, texts: list[str]) -> list[list[float]]:
        import httpx
        from agent import privacy
        privacy.require("models", self.base_url)  # memory text leaves this PC only where Private mode allows
        out: list[list[float]] = []
        for start in range(0, len(texts), 64):
            batch = texts[start:start + 64]
            response = httpx.post(f"{self.base_url}/embeddings", timeout=self.timeout,
                                  headers={"Authorization": f"Bearer {self.api_key or 'not-needed'}"},
                                  json={"model": self.model, "input": batch})
            response.raise_for_status()
            rows = sorted(response.json()["data"], key=lambda row: row.get("index", 0))
            out.extend([list(map(float, row["embedding"])) for row in rows])
        return out

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed([str(t) for t in texts]) if texts else []

    def embed_query(self, text: str) -> list[float]:
        return self._embed([str(text)])[0]
