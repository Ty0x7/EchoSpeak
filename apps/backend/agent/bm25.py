"""Okapi BM25 for hybrid document search, without a third-party package (campaign round 3, 25 November 2026).

Document search defaults to hybrid retrieval (DOC_HYBRID_ENABLED=true) but imported ``rank_bm25``, which is in no
requirements file: every installation silently fell back to vector-only search and startup readiness reported
document retrieval as degraded. The store uses only ``BM25Okapi(corpus).get_scores(query)``, so this provides that,
with rank_bm25's defaults (k1=1.5, b=0.75, epsilon=0.25 floor for negative IDF). The package is still used when it
is installed.
"""
from __future__ import annotations

import math
from collections import Counter
from typing import Sequence


class BM25Okapi:
    def __init__(self, corpus: Sequence[Sequence[str]], k1: float = 1.5, b: float = 0.75, epsilon: float = 0.25) -> None:
        self.k1, self.b, self.epsilon = k1, b, epsilon
        self.doc_freqs = [Counter(doc) for doc in corpus]
        self.doc_len = [len(doc) for doc in corpus]
        self.corpus_size = len(self.doc_len)
        self.avgdl = (sum(self.doc_len) / self.corpus_size) if self.corpus_size else 0.0
        df: Counter = Counter()
        for freqs in self.doc_freqs:
            df.update(freqs.keys())
        self.idf: dict[str, float] = {}
        negative = []
        total = 0.0
        for word, n in df.items():
            value = math.log(self.corpus_size - n + 0.5) - math.log(n + 0.5)
            self.idf[word] = value
            total += value
            if value < 0:
                negative.append(word)
        floor = self.epsilon * (total / len(self.idf)) if self.idf else 0.0
        for word in negative:
            self.idf[word] = floor

    def get_scores(self, query: Sequence[str]) -> list[float]:
        scores = [0.0] * self.corpus_size
        if not self.corpus_size or not self.avgdl:
            return scores
        for word in query:
            idf = self.idf.get(word)
            if idf is None:
                continue
            for i, freqs in enumerate(self.doc_freqs):
                tf = freqs.get(word, 0)
                if tf:
                    norm = self.k1 * (1 - self.b + self.b * self.doc_len[i] / self.avgdl)
                    scores[i] += idf * (tf * (self.k1 + 1)) / (tf + norm)
        return scores
