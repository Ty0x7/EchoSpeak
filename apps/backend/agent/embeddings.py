"""Text embeddings for memory and document search, without PyTorch.

Order of preference:

1. The embedding provider in settings (OpenAI, or the local model server's
   ``/v1/embeddings``, e.g. LM Studio with an embedding model loaded).
2. A small local ONNX model: all-MiniLM-L6-v2 (384 dimensions, ~90 MB), run with
   onnxruntime and tokenizers. It is the same model the 10.x builds ran through
   PyTorch, so existing memory indexes keep working. It downloads once into the
   data folder only when the user requests its installation in Settings.
3. Nothing: memory still works from its records, with keyword recall only.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any, Optional

from loguru import logger

try:
    from langchain_core.embeddings import Embeddings
except ImportError:  # pragma: no cover - langchain is a hard dependency today
    Embeddings = object  # type: ignore[misc,assignment]

from config import DATA_DIR

LOCAL_MODEL_REPO = "sentence-transformers/all-MiniLM-L6-v2"
LOCAL_MODEL_REVISION = "d83dd3760b5bfe921f2fe125446b17bf0b7eda8c"
LOCAL_MODEL_NAME = "all-MiniLM-L6-v2"
LOCAL_MODEL_FILES = ("onnx/model.onnx", "tokenizer.json")
LOCAL_MODEL_DIM = 384
_MAX_TOKENS = 256
_BATCH = 32

_download_lock = threading.Lock()


def local_model_dir() -> Path:
    return Path(DATA_DIR) / "models" / "embeddings" / LOCAL_MODEL_NAME


def local_runtime_available() -> bool:
    """onnxruntime and tokenizers are importable (they ship with local voice too)."""
    import importlib.util

    return all(importlib.util.find_spec(name) is not None for name in ("onnxruntime", "tokenizers", "numpy"))


def local_model_installed() -> bool:
    folder = local_model_dir()
    return all((folder / name).is_file() and (folder / name).stat().st_size > 0 for name in LOCAL_MODEL_FILES)


def local_status() -> dict[str, Any]:
    folder = local_model_dir()
    size = sum((folder / name).stat().st_size for name in LOCAL_MODEL_FILES if (folder / name).is_file())
    return {
        "model": LOCAL_MODEL_REPO,
        "revision": LOCAL_MODEL_REVISION,
        "runtime": "onnxruntime",
        "runtime_available": local_runtime_available(),
        "installed": local_model_installed(),
        "path": str(folder),
        "size_bytes": size,
    }


def download_local_model() -> Path:
    """Fetch the ONNX model and tokenizer into the data folder (once)."""
    folder = local_model_dir()
    with _download_lock:
        if local_model_installed():
            return folder
        from huggingface_hub import hf_hub_download

        folder.mkdir(parents=True, exist_ok=True)
        for name in LOCAL_MODEL_FILES:
            # hf_hub_download writes to a temporary file and renames it into place.
            hf_hub_download(repo_id=LOCAL_MODEL_REPO, filename=name, revision=LOCAL_MODEL_REVISION,
                            local_dir=str(folder))
        logger.info("Local embedding model ready at {}", folder)
        return folder


class OnnxEmbeddings(Embeddings):
    """all-MiniLM-L6-v2 on onnxruntime: mean pooling over tokens, then L2 normalised."""

    def __init__(self, model_dir: Optional[Path] = None):
        self.model_dir = Path(model_dir or local_model_dir())
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
            import onnxruntime as ort
            from tokenizers import Tokenizer

            tokenizer = Tokenizer.from_file(str(self.model_dir / LOCAL_MODEL_FILES[1]))
            tokenizer.enable_truncation(max_length=_MAX_TOKENS)
            tokenizer.enable_padding()
            options = ort.SessionOptions()
            options.intra_op_num_threads = max(1, min(4, (os.cpu_count() or 2) // 2))
            session = ort.InferenceSession(
                str(self.model_dir / LOCAL_MODEL_FILES[0]), sess_options=options, providers=["CPUExecutionProvider"]
            )
            self._input_names = {item.name for item in session.get_inputs()}
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
            hidden = self._session.run(None, {k: v for k, v in feeds.items() if k in self._input_names})[0]
            weights = mask[:, :, None].astype(np.float32)
            pooled = (hidden * weights).sum(axis=1) / np.clip(weights.sum(axis=1), 1e-9, None)
            norms = np.linalg.norm(pooled, axis=1, keepdims=True)
            out.extend((pooled / np.clip(norms, 1e-12, None)).astype(float).tolist())
        return out

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed(list(texts))

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text])[0]


def local_embeddings() -> tuple[Optional[OnnxEmbeddings], dict[str, Any]]:
    """The local ONNX embedder; no network call unless explicitly enabled."""
    health = {"available": False, "provider": "onnx_local", "model": LOCAL_MODEL_REPO, "detail": ""}
    if not local_runtime_available():
        health["detail"] = "onnxruntime/tokenizers are not installed"
        return None, health
    if not local_model_installed():
        health["detail"] = "The local embedding model is not downloaded"
        return None, health
    try:
        embedder = OnnxEmbeddings()
        embedder.embed_query("healthcheck")
    except Exception as exc:
        health["detail"] = f"Local embedding model failed to load: {exc}"
        return None, health
    health.update(available=True, detail="Ready")
    return embedder, health
