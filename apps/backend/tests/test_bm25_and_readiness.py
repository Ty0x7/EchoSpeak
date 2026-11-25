"""Hybrid document search without rank_bm25, and readiness that says what is really missing (25 Nov 2026)."""
from __future__ import annotations

import math

from agent.bm25 import BM25Okapi


def test_scores_match_the_okapi_formula():
    corpus = [["cat", "sat", "mat"], ["dog", "sat", "log"], ["cat", "cat", "hat"]]
    bm = BM25Okapi(corpus)
    avgdl = 3.0
    # "dog" appears in one of three documents: idf = ln((3-1+0.5)/(1+0.5)).
    idf = math.log(2.5) - math.log(1.5)
    expected = idf * (1 * 2.5) / (1 + 1.5 * (1 - 0.75 + 0.75 * 3 / avgdl))
    scores = bm.get_scores(["dog"])
    assert scores[0] == 0 and scores[2] == 0 and abs(scores[1] - expected) < 1e-9
    ranked = bm.get_scores(["cat"])
    assert ranked[2] > ranked[0] > ranked[1] == 0        # more occurrences rank higher
    assert BM25Okapi([]).get_scores(["x"]) == []


def test_the_document_store_uses_hybrid_search_without_the_package():
    from agent import document_store

    assert document_store.BM25Okapi is not None


def test_readiness_names_what_to_do_when_no_search_model_is_installed(monkeypatch):
    from agent import startup_readiness as sr

    monkeypatch.setattr(sr, "_search_model_installed", lambda: False)
    assert "Search by meaning" in sr._SEARCH_OFF and "Settings" in sr._SEARCH_OFF
    monkeypatch.setattr(sr, "_search_model_installed", lambda: True)
    import sys
    import threading
    import types

    fake = types.SimpleNamespace(_MEMORY_INSTANCES_GUARD=threading.Lock(), _MEMORY_INSTANCES={})
    saved = sys.modules.get("agent.memory")
    sys.modules["agent.memory"] = fake          # loaded, but no conversation has started memory yet
    try:
        status = sr._embeddings()
        assert status["ready"] and "loads with the first conversation" in status["detail"]
        monkeypatch.setattr(sr, "_search_model_installed", lambda: False)
        off = sr._embeddings()
        assert off["degraded"] and "Search by meaning" in off["detail"]
    finally:
        if saved is not None:
            sys.modules["agent.memory"] = saved
        else:
            sys.modules.pop("agent.memory", None)


def test_the_default_openai_provider_without_a_key_is_not_a_search_model(monkeypatch, tmp_path):
    from agent import embeddings, startup_readiness as sr
    from config import config

    monkeypatch.setattr(config.openai, "api_key", "")
    monkeypatch.setattr(embeddings, "local_status", lambda: {"installed": False})
    assert sr._search_model_installed() is False
    monkeypatch.setattr(config.openai, "api_key", "sk-test")
    monkeypatch.setattr(config.embedding, "provider", "openai")
    assert sr._search_model_installed() is True
