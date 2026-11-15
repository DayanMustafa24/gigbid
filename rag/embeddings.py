"""Embedders, a disk cache, and rerankers behind small Protocols. The model is chosen in agent/config.yaml (rag.*).

kinds:
  hashing  offline, deterministic, no model download. Hashed word + char n-gram vectors. It is a LEXICAL-ish stand-in
           (fuzzy spelling, no synonyms): used for unit tests/CI and as a floor. Results must name it, never call it semantic.
  ollama   real dense model (default nomic-embed-text) served by Ollama. CPU is fine for a corpus this size.
  st       sentence-transformers model (BGE-M3, ...). Needs `uv sync --extra ml`.
"""
from __future__ import annotations

import hashlib
import math
import os
import re
from pathlib import Path
from typing import Any, Protocol

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
_WORD = re.compile(r"[a-z0-9][a-z0-9+#.\-]*")


def _norm(m: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(m, axis=1, keepdims=True)
    n[n == 0] = 1.0
    return (m / n).astype(np.float32)


class Embedder(Protocol):
    name: str
    dim: int

    def embed(self, texts: list[str], *, kind: str = "doc") -> np.ndarray: ...


class HashingEmbedder:
    """Signed feature hashing of word unigrams/bigrams and char 3-grams with sublinear tf. Deterministic across runs."""

    def __init__(self, dim: int = 512):
        self.dim, self.name = dim, f"hashing-{dim}"

    @staticmethod
    def _h(s: str) -> int:
        return int.from_bytes(hashlib.blake2b(s.encode(), digest_size=8).digest(), "little")

    def _vec(self, text: str) -> np.ndarray:
        v = np.zeros(self.dim, dtype=np.float32)
        words = _WORD.findall(text.lower())
        feats: dict[str, float] = {}
        for w in words:
            feats["w:" + w] = feats.get("w:" + w, 0) + 1
            if len(w) >= 4:
                for i in range(len(w) - 2):
                    k = "c:" + w[i : i + 3]
                    feats[k] = feats.get(k, 0) + 0.35
        for a, b in zip(words, words[1:], strict=False):
            feats[f"b:{a}_{b}"] = feats.get(f"b:{a}_{b}", 0) + 0.7
        for k, c in feats.items():
            h = self._h(k)
            v[h % self.dim] += (1 if (h >> 40) & 1 else -1) * (1 + math.log(c))
        return v

    def embed(self, texts: list[str], *, kind: str = "doc") -> np.ndarray:
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        return _norm(np.stack([self._vec(t) for t in texts]))


class OllamaEmbedder:
    def __init__(self, model: str = "nomic-embed-text", base_url: str | None = None, dim: int = 768,
                 query_prefix: str = "search_query: ", doc_prefix: str = "search_document: ", timeout: float = 120.0):
        base = (base_url or os.environ.get("OLLAMA_BASE_URL") or "http://localhost:11434").rstrip("/")
        self.base = base[:-3] if base.endswith("/v1") else base
        self.model, self.dim, self.name = model, dim, f"ollama-{model}"
        self.qp, self.dp, self.timeout = query_prefix, doc_prefix, timeout

    def embed(self, texts: list[str], *, kind: str = "doc") -> np.ndarray:
        import httpx

        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        pre = self.qp if kind == "query" else self.dp
        out: list[list[float]] = []
        for i in range(0, len(texts), 32):
            r = httpx.post(f"{self.base}/api/embed", timeout=self.timeout,
                           json={"model": self.model, "input": [pre + t for t in texts[i : i + 32]]})
            r.raise_for_status()
            out += r.json()["embeddings"]
        m = np.asarray(out, dtype=np.float32)
        self.dim = m.shape[1]
        return _norm(m)


class STEmbedder:
    def __init__(self, model: str = "BAAI/bge-m3", query_prefix: str = "", doc_prefix: str = ""):
        from sentence_transformers import SentenceTransformer  # extra: ml

        self._m = SentenceTransformer(model, trust_remote_code=True)
        self.model, self.qp, self.dp = model, query_prefix, doc_prefix
        self.name, self.dim = f"st-{model.split('/')[-1]}", int(self._m.get_sentence_embedding_dimension())

    def embed(self, texts: list[str], *, kind: str = "doc") -> np.ndarray:
        pre = self.qp if kind == "query" else self.dp
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        return _norm(np.asarray(self._m.encode([pre + t for t in texts], normalize_embeddings=True), dtype=np.float32))


class CachedEmbedder:
    """Wraps any embedder with an in-memory + on-disk cache keyed by sha1(kind|text). Re-indexing only embeds new text."""

    def __init__(self, inner: Embedder, cache_dir: Path | str | None = None):
        self.inner, self.name = inner, inner.name
        self.dir = Path(cache_dir) if cache_dir else None
        self._mem: dict[str, np.ndarray] = {}
        self._dirty = False
        self.hits = self.misses = 0
        if self.dir and self._file.exists():
            with np.load(self._file, allow_pickle=False) as z:
                self._mem = dict(zip(z["keys"].tolist(), z["vecs"], strict=False))

    @property
    def dim(self) -> int:
        return self.inner.dim

    @property
    def _file(self) -> Path:
        assert self.dir is not None
        return self.dir / (re.sub(r"[^A-Za-z0-9._-]", "_", self.name) + ".npz")

    @staticmethod
    def _key(kind: str, text: str) -> str:
        return hashlib.sha1(f"{kind}|{text}".encode()).hexdigest()

    def embed(self, texts: list[str], *, kind: str = "doc") -> np.ndarray:
        keys = [self._key(kind, t) for t in texts]
        todo = [i for i, k in enumerate(keys) if k not in self._mem]
        self.hits += len(texts) - len(todo)
        self.misses += len(todo)
        if todo:
            vecs = self.inner.embed([texts[i] for i in todo], kind=kind)
            for i, v in zip(todo, vecs, strict=False):
                self._mem[keys[i]] = v
            self._dirty = True
        if not texts:
            return np.zeros((0, self.inner.dim), dtype=np.float32)
        return np.stack([self._mem[k] for k in keys])

    def flush(self) -> None:
        if self.dir and self._dirty and self._mem:
            self.dir.mkdir(parents=True, exist_ok=True)
            ks = list(self._mem)
            np.savez_compressed(self._file, keys=np.array(ks), vecs=np.stack([self._mem[k] for k in ks]))
            self._dirty = False


def make_embedder(cfg: dict[str, Any] | None = None, *, cache: bool = True) -> Embedder:
    """cfg = the `rag.embedder` block of config.yaml (kind/model/...). Env RAG_EMBEDDER overrides `kind`."""
    cfg = dict(cfg or {"kind": "hashing"})
    kind = os.environ.get("RAG_EMBEDDER") or cfg.get("kind", "hashing")
    if kind == "hashing":
        emb: Embedder = HashingEmbedder(int(cfg.get("dim", 512)))
    elif kind == "ollama":
        emb = OllamaEmbedder(cfg.get("model", "nomic-embed-text"), dim=int(cfg.get("dim", 768)))
    elif kind == "st":
        emb = STEmbedder(cfg.get("model", "BAAI/bge-m3"))
    else:
        raise ValueError(f"unknown embedder kind {kind!r} (hashing | ollama | st)")
    if cache and kind != "hashing":  # hashing is cheaper to recompute than to load
        return CachedEmbedder(emb, ROOT / cfg.get("cache_dir", "data/processed/emb_cache"))
    return emb


# ------------------------------------------------------------------------------------------- rerankers -------------


class Reranker(Protocol):
    name: str

    def score(self, query: str, passages: list[str]) -> list[float]: ...


class LexicalReranker:
    """Offline stand-in for a cross-encoder: query-term coverage (idf over the candidate set) + bigram overlap.

    It reads query and passage TOGETHER (unlike bi-encoders), which is the property V3 tests, but it is not a neural
    model. Use CrossEncoderReranker (bge-reranker-v2-m3) for the real measurement.
    """

    name = "lexical-coverage"

    def score(self, query: str, passages: list[str]) -> list[float]:
        q = _WORD.findall(query.lower())
        qs = set(q)
        toks = [_WORD.findall(p.lower()) for p in passages]
        n = len(passages) or 1
        df = {t: sum(t in set(p) for p in toks) for t in qs}
        idf = {t: math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5)) for t in qs}
        tot = sum(idf.values()) or 1.0
        qb = set(zip(q, q[1:], strict=False))
        out = []
        for p in toks:
            ps = set(p)
            cov = sum(idf[t] for t in qs if t in ps) / tot
            big = len(qb & set(zip(p, p[1:], strict=False))) / (len(qb) or 1)
            out.append(0.85 * cov + 0.15 * big)
        return out


class CrossEncoderReranker:
    def __init__(self, model: str = "BAAI/bge-reranker-v2-m3", max_length: int = 512):
        from sentence_transformers import CrossEncoder  # extra: ml

        self._m = CrossEncoder(model, max_length=max_length)
        self.name = f"ce-{model.split('/')[-1]}"

    def score(self, query: str, passages: list[str]) -> list[float]:
        if not passages:
            return []
        return [float(s) for s in self._m.predict([(query, p) for p in passages])]


def make_reranker(cfg: dict[str, Any] | None = None) -> Reranker:
    cfg = cfg or {"kind": "lexical"}
    kind = os.environ.get("RAG_RERANKER") or cfg.get("kind", "lexical")
    if kind == "lexical":
        return LexicalReranker()
    if kind == "cross_encoder":
        return CrossEncoderReranker(cfg.get("model", "BAAI/bge-reranker-v2-m3"))
    raise ValueError(f"unknown reranker kind {kind!r} (lexical | cross_encoder)")
