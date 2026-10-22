"""Text embeddings and reranking for memory and document search, without PyTorch.

Order of preference:

1. The embedding provider in settings (OpenAI, or the local model server's
   ``/v1/embeddings``, e.g. LM Studio with an embedding model loaded).
2. A local ONNX model the owner picked under Settings › Memory & documents ›
   Search by meaning, run with onnxruntime and tokenizers:
   - "fast": all-MiniLM-L6-v2 (English, 384 dimensions, ~90 MB). The model the
     10.x builds ran through PyTorch, so old memory indexes keep working.
   - "better": EmbeddingGemma-300M, 4-bit (100+ languages, 768 dimensions,
     ~220 MB, 2K-token inputs instead of 256).
   Each downloads once into the data folder, only when the owner asks.
3. Nothing ("off", or no model downloaded): memory still works from its
   records, with keyword recall only.

A small ONNX cross-encoder (ms-marco-MiniLM-L6-v2, 8-bit, ~24 MB) can re-order
document hits; it replaces the sentence-transformers path the installed app
could never run. Revisions are pinned, so a download is always the bytes that
were tested (docs/research/embeddings-2026-10-22.md).
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from loguru import logger


class Embeddings:
    """What every embedding backend provides (agent/vector_store.py searches with it)."""

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError

    def embed_query(self, text: str) -> list[float]:
        raise NotImplementedError


from config import DATA_DIR  # noqa: E402


@dataclass(frozen=True)
class LocalModel:
    key: str
    label: str
    repo: str
    revision: str
    folder: str
    # The ONNX graph first, its weights file (if separate) next, the tokenizer last.
    files: tuple[str, ...]
    dim: int
    size_mb: int
    languages: str
    max_tokens: int = 256
    # "mean": mean-pool last_hidden_state over the attention mask. "output": the graph's sentence_embedding.
    pooling: str = "mean"
    query_prefix: str = ""
    document_prefix: str = ""


CATALOG: dict[str, LocalModel] = {
    "fast": LocalModel(
        key="fast", label="Fast · English", repo="sentence-transformers/all-MiniLM-L6-v2",
        revision="d83dd3760b5bfe921f2fe125446b17bf0b7eda8c", folder="all-MiniLM-L6-v2",
        files=("onnx/model.onnx", "tokenizer.json"), dim=384, size_mb=90, languages="English",
    ),
    "better": LocalModel(
        key="better", label="Better · 100+ languages, longer text", repo="onnx-community/embeddinggemma-300m-ONNX",
        revision="5090578d9565bb06545b4552f76e6bc2c93e4a66", folder="embeddinggemma-300m-q4",
        files=("onnx/model_q4.onnx", "onnx/model_q4.onnx_data", "tokenizer.json"), dim=768, size_mb=220,
        languages="100+ languages", max_tokens=2048, pooling="output",
        # EmbeddingGemma's retrieval prompts (model card): queries and documents are framed differently.
        query_prefix="task: search result | query: ", document_prefix="title: none | text: ",
    ),
}
RERANKER = LocalModel(
    key="rerank", label="Sharper document results", repo="cross-encoder/ms-marco-MiniLM-L6-v2",
    revision="233902d25c440f23af6f7d6e94d2946bac0bee0a", folder="ms-marco-MiniLM-L6-v2-int8",
    files=("onnx/model_quint8_avx2.onnx", "tokenizer.json"), dim=1, size_mb=24, languages="English", max_tokens=512,
)
CHOICES = ("off", "fast", "better")

# Kept for callers and older code paths: the default local model.
LOCAL_MODEL_REPO = CATALOG["fast"].repo
LOCAL_MODEL_REVISION = CATALOG["fast"].revision
LOCAL_MODEL_NAME = CATALOG["fast"].folder
LOCAL_MODEL_FILES = CATALOG["fast"].files
LOCAL_MODEL_DIM = CATALOG["fast"].dim
_BATCH = 32

_download_lock = threading.Lock()


def chosen_key() -> str:
    """The owner's local search model: "off", "fast" or "better" (Settings › Search by meaning)."""
    from config import config

    value = str(getattr(config, "embedding_local_model", "") or "fast").strip().lower()
    return value if value in CHOICES else "fast"


def chosen_model() -> Optional[LocalModel]:
    return CATALOG.get(chosen_key())


def folder_for(model: LocalModel) -> Path:
    return Path(DATA_DIR) / "models" / "embeddings" / model.folder


def local_model_dir() -> Path:
    """The folder of the chosen local model (the fast one when search by meaning is off)."""
    return folder_for(chosen_model() or CATALOG["fast"])


def local_runtime_available() -> bool:
    """onnxruntime and tokenizers are importable (they ship with local voice too)."""
    import importlib.util

    return all(importlib.util.find_spec(name) is not None for name in ("onnxruntime", "tokenizers", "numpy"))


def model_installed(model: LocalModel, folder: Optional[Path] = None) -> bool:
    folder = folder or folder_for(model)
    return all((folder / name).is_file() and (folder / name).stat().st_size > 0 for name in model.files)


def local_model_installed() -> bool:
    return model_installed(chosen_model() or CATALOG["fast"], local_model_dir())


def _size(model: LocalModel, folder: Path) -> int:
    return sum((folder / name).stat().st_size for name in model.files if (folder / name).is_file())


def local_status() -> dict[str, Any]:
    """Everything Settings › Search by meaning shows: the choice, each model and the reranker."""
    from config import config

    active = chosen_model() or CATALOG["fast"]
    folder = local_model_dir()
    return {
        # The chosen model, in the shape older clients read.
        "model": active.repo,
        "revision": active.revision,
        "runtime": "onnxruntime",
        "runtime_available": local_runtime_available(),
        "installed": model_installed(active, folder),
        "path": str(folder),
        "size_bytes": _size(active, folder),
        "choice": chosen_key(),
        "models": [
            {"key": m.key, "label": m.label, "repo": m.repo, "size_mb": m.size_mb, "languages": m.languages,
             "dim": m.dim, "installed": model_installed(m)}
            for m in CATALOG.values()
        ],
        "reranker": {"label": RERANKER.label, "repo": RERANKER.repo, "size_mb": RERANKER.size_mb,
                     "installed": model_installed(RERANKER),
                     "enabled": bool(getattr(config, "doc_rerank_enabled", False))},
    }


def download_model(model: LocalModel) -> Path:
    """Fetch a catalog model's pinned files into the data folder (once). Offline mode refuses."""
    folder = folder_for(model)
    with _download_lock:
        if model_installed(model, folder):
            return folder
        from huggingface_hub import hf_hub_download

        from agent import privacy

        privacy.require("downloads", "https://huggingface.co")  # Offline mode: no downloads
        folder.mkdir(parents=True, exist_ok=True)
        for name in model.files:
            # hf_hub_download writes to a temporary file and renames it into place.
            hf_hub_download(repo_id=model.repo, filename=name, revision=model.revision, local_dir=str(folder))
        logger.info("Local model {} ready at {}", model.repo, folder)
        return folder


def download_local_model(key: Optional[str] = None) -> Path:
    """Fetch a local search model: the named one, or the chosen one."""
    model = CATALOG.get(str(key or "")) or chosen_model() or CATALOG["fast"]
    return download_model(model)


def _session(path: Path):
    import onnxruntime as ort

    options = ort.SessionOptions()
    options.intra_op_num_threads = max(1, min(4, (os.cpu_count() or 2) // 2))
    return ort.InferenceSession(str(path), sess_options=options, providers=["CPUExecutionProvider"])


class OnnxEmbeddings(Embeddings):
    """A catalog model on onnxruntime: mean pooling or the graph's own sentence embedding, then L2 normalised."""

    def __init__(self, model_dir: Optional[Path] = None, model: Optional[LocalModel] = None):
        self.model = model or CATALOG["fast"]
        self.model_dir = Path(model_dir or folder_for(self.model))
        self._session = None
        self._tokenizer = None
        self._input_names: set[str] = set()
        self._output_names: list[str] = []
        self._lock = threading.Lock()

    def _load(self) -> None:
        if self._session is not None:
            return
        with self._lock:
            if self._session is not None:
                return
            from tokenizers import Tokenizer

            tokenizer = Tokenizer.from_file(str(self.model_dir / self.model.files[-1]))
            tokenizer.enable_truncation(max_length=self.model.max_tokens)
            tokenizer.enable_padding()
            session = _session(self.model_dir / self.model.files[0])
            self._input_names = {item.name for item in session.get_inputs()}
            self._output_names = [item.name for item in session.get_outputs()]
            self._tokenizer = tokenizer
            self._session = session

    def _embed(self, texts: list[str]) -> list[list[float]]:
        import numpy as np

        self._load()
        out: list[list[float]] = []
        for start in range(0, len(texts), _BATCH):
            batch = [str(text or " ") for text in texts[start:start + _BATCH]]
            encoded = self._tokenizer.encode_batch(batch)
            ids = np.array([e.ids for e in encoded], dtype=np.int64)
            mask = np.array([e.attention_mask for e in encoded], dtype=np.int64)
            feeds = {"input_ids": ids, "attention_mask": mask}
            if "token_type_ids" in self._input_names:
                feeds["token_type_ids"] = np.array([e.type_ids for e in encoded], dtype=np.int64)
            results = dict(zip(self._output_names, self._session.run(None, {k: v for k, v in feeds.items() if k in self._input_names})))
            if self.model.pooling == "output" and "sentence_embedding" in results:
                pooled = results["sentence_embedding"].astype(np.float32)
            else:
                hidden = results.get("last_hidden_state", next(iter(results.values())))
                weights = mask[:, :, None].astype(np.float32)
                pooled = (hidden * weights).sum(axis=1) / np.clip(weights.sum(axis=1), 1e-9, None)
            norms = np.linalg.norm(pooled, axis=1, keepdims=True)
            out.extend((pooled / np.clip(norms, 1e-12, None)).astype(float).tolist())
        return out

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        prefix = self.model.document_prefix
        return self._embed([prefix + str(text or "") for text in texts])

    def embed_query(self, text: str) -> list[float]:
        return self._embed([self.model.query_prefix + str(text or "")])[0]


def local_embeddings() -> tuple[Optional[OnnxEmbeddings], dict[str, Any]]:
    """The chosen local ONNX embedder; no network call unless explicitly enabled."""
    model = chosen_model()
    health = {"available": False, "provider": "onnx_local", "model": (model or CATALOG["fast"]).repo, "detail": ""}
    if model is None:
        health["detail"] = "Search by meaning is off: keyword search only"
        return None, health
    if not local_runtime_available():
        health["detail"] = "onnxruntime/tokenizers are not installed"
        return None, health
    folder = local_model_dir()
    if not model_installed(model, folder):
        health["detail"] = f"The local embedding model ({model.label}) is not downloaded"
        return None, health
    try:
        embedder = OnnxEmbeddings(folder, model)
        embedder.embed_query("healthcheck")
    except Exception as exc:
        health["detail"] = f"Local embedding model failed to load: {exc}"
        return None, health
    health.update(available=True, detail="Ready", label=model.label)
    return embedder, health


# ── reranking ───────────────────────────────────────────────────────────

class OnnxCrossEncoder:
    """Scores (query, passage) pairs with an ONNX cross-encoder; higher is more relevant.

    ``predict`` matches sentence-transformers' CrossEncoder, so the document store can use either.
    """

    def __init__(self, model_dir: Optional[Path] = None, model: LocalModel = RERANKER):
        self.model = model
        self.model_dir = Path(model_dir or folder_for(model))
        self._session = None
        self._tokenizer = None
        self._input_names: set[str] = set()
        self._lock = threading.Lock()

    def _load(self) -> None:
        if self._session is not None:
            return
        with self._lock:
            if self._session is not None:
                return
            from tokenizers import Tokenizer

            tokenizer = Tokenizer.from_file(str(self.model_dir / self.model.files[-1]))
            tokenizer.enable_truncation(max_length=self.model.max_tokens)
            tokenizer.enable_padding()
            session = _session(self.model_dir / self.model.files[0])
            self._input_names = {item.name for item in session.get_inputs()}
            self._tokenizer = tokenizer
            self._session = session

    def predict(self, pairs: list[tuple[str, str]]) -> list[float]:
        import numpy as np

        self._load()
        scores: list[float] = []
        for start in range(0, len(pairs), 16):
            batch = [(str(q or " "), str(d or " ")) for q, d in pairs[start:start + 16]]
            encoded = self._tokenizer.encode_batch(batch)
            feeds = {"input_ids": np.array([e.ids for e in encoded], dtype=np.int64),
                     "attention_mask": np.array([e.attention_mask for e in encoded], dtype=np.int64),
                     "token_type_ids": np.array([e.type_ids for e in encoded], dtype=np.int64)}
            logits = self._session.run(None, {k: v for k, v in feeds.items() if k in self._input_names})[0]
            scores.extend(float(v) for v in np.asarray(logits).reshape(len(batch), -1)[:, 0])
        return scores


def local_reranker() -> Optional[OnnxCrossEncoder]:
    """The ONNX reranker when it's downloaded and the runtime is present, else None (never downloads)."""
    if not local_runtime_available() or not model_installed(RERANKER):
        return None
    try:
        reranker = OnnxCrossEncoder()
        reranker.predict([("check", "check")])
        return reranker
    except Exception as exc:
        logger.warning("Local reranker failed to load: {}", exc)
        return None


# ── LM Studio's embedding models ────────────────────────────────────────

def lmstudio_embedding_models(base_url: str, timeout: float = 3.0) -> list[dict[str, Any]]:
    """Embedding models the local model server knows about: [{id, loaded}]. Empty when it isn't running.

    LM Studio's /api/v0/models says which models are embedding models and whether they're loaded;
    other OpenAI-compatible servers only list ids, so names containing "embed" stand in there.
    """
    import httpx

    root = str(base_url or "http://localhost:1234").rstrip("/")
    if root.endswith("/v1"):
        root = root[:-3]
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.get(f"{root}/api/v0/models")
            if response.status_code == 200:
                rows = response.json().get("data") or []
                return [{"id": str(m.get("id")), "loaded": m.get("state") == "loaded"}
                        for m in rows if str(m.get("type") or "") == "embeddings" and m.get("id")]
            response = client.get(f"{root}/v1/models")
            if response.status_code == 200:
                rows = response.json().get("data") or []
                return [{"id": str(m.get("id")), "loaded": True} for m in rows if "embed" in str(m.get("id") or "").lower()]
    except Exception:
        pass
    return []
