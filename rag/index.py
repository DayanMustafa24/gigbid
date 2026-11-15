"""Unit index: BM25 + dense (in-memory numpy). One class so every variant shares identical scoring code.

Backend note: this is the in-memory backend (unit tests, CI, evals, single-user runs). The pgvector backend for the
deployed API implements the same three methods (dense/bm25/rank) in Phase 6; variants never touch storage directly.
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Hashable, Sequence

import numpy as np

from mcp_servers.portfolio.store import tokenize

from .embeddings import Embedder
from .types import Unit


def rrf(rankings: Sequence[Sequence[Hashable]], k: int = 60, weights: Sequence[float] | None = None) -> dict:
    """Reciprocal rank fusion: score(d) = sum_i w_i / (k + rank_i(d)). Rank starts at 1."""
    out: dict[Hashable, float] = defaultdict(float)
    for i, ranking in enumerate(rankings):
        w = weights[i] if weights else 1.0
        for r, key in enumerate(ranking, start=1):
            out[key] += w / (k + r)
    return dict(out)


class UnitIndex:
    K1, B = 1.5, 0.75

    def __init__(self, units: list[Unit], embedder: Embedder):
        self.units, self.embedder = units, embedder
        toks = [tokenize(u.text) for u in units]
        n = len(units)
        self.avgdl = (sum(len(t) for t in toks) / n) if n else 1.0
        self.dl = np.array([len(t) or 1 for t in toks], dtype=np.float32)
        post: dict[str, list[tuple[int, int]]] = defaultdict(list)
        for i, t in enumerate(toks):
            for term, c in Counter(t).items():
                post[term].append((i, c))
        self.post = post
        self.idf = {t: math.log(1 + (n - len(p) + 0.5) / (len(p) + 0.5)) for t, p in post.items()}
        self.mat = embedder.embed([u.dense_text or u.text for u in units], kind="doc") if n else np.zeros((0, 1))

    def __len__(self) -> int:
        return len(self.units)

    def dense(self, query: str) -> np.ndarray:
        if not self.units:
            return np.zeros(0, dtype=np.float32)
        q = self.embedder.embed([query], kind="query")[0]
        return self.mat @ q

    def bm25(self, query: str) -> np.ndarray:
        s = np.zeros(len(self.units), dtype=np.float32)
        for term in set(tokenize(query)):
            for i, f in self.post.get(term, ()):
                s[i] += self.idf[term] * f * (self.K1 + 1) / (f + self.K1 * (1 - self.B + self.B * self.dl[i] / self.avgdl))
        return s

    def rank(self, scores: np.ndarray, n: int, *, positive_only: bool = False) -> list[int]:
        """Top-n unit indices, best first, ties broken by uid (deterministic)."""
        order = sorted(range(len(scores)), key=lambda i: (-float(scores[i]), self.units[i].uid))
        if positive_only:
            order = [i for i in order if scores[i] > 0]
        return order[:n]
