"""Search by meaning for small lists (tools, setup guides), with the embedder EchoSpeak already has.

"make my video brighter" shares no word with a Premiere tool called adjust_lumetri,
but their meanings are close. This reuses the local ONNX model behind memory
search (agent/embeddings.py, all-MiniLM-L6-v2): nothing new to download, nothing
leaves the PC, and when the model isn't installed every caller falls back to its
keyword matching. Vectors for repeated texts (tool descriptions) are cached.
"""

from __future__ import annotations

import threading
from typing import Optional

_MAX_CACHED = 4000
_cache: dict[str, list[float]] = {}
_lock = threading.Lock()
_embedder = None
_unavailable = False


def _model():
    global _embedder, _unavailable
    if _embedder is not None or _unavailable:
        return _embedder
    with _lock:
        if _embedder is None and not _unavailable:
            try:
                from agent.embeddings import local_embeddings

                _embedder, _health = local_embeddings()  # never downloads
            except Exception:
                _embedder = None
            _unavailable = _embedder is None
    return _embedder


def available() -> bool:
    return _model() is not None


def _vectors(texts: list[str]) -> Optional[list[list[float]]]:
    model = _model()
    if model is None:
        return None
    missing = [t for t in dict.fromkeys(texts) if t not in _cache]
    if missing:
        try:
            fresh = model.embed_documents(missing)
        except Exception:
            return None
        with _lock:
            if len(_cache) + len(fresh) > _MAX_CACHED:
                _cache.clear()
            _cache.update(zip(missing, fresh))
    return [_cache[t] for t in texts]


def similarities(query: str, texts: list[str]) -> Optional[list[float]]:
    """Cosine similarity of ``query`` to each text (vectors are normalised), or None without the model."""
    if not texts or not str(query or "").strip():
        return None
    vectors = _vectors([str(query)] + [str(t) for t in texts])
    if vectors is None:
        return None
    q = vectors[0]
    return [sum(a * b for a, b in zip(q, v)) for v in vectors[1:]]


def reset() -> None:
    """Tests: forget the model and cache."""
    global _embedder, _unavailable
    with _lock:
        _embedder, _unavailable = None, False
        _cache.clear()
