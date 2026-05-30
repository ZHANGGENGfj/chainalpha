from __future__ import annotations

import math
from collections import Counter, defaultdict

from chainalpha.schemas import Chunk
from chainalpha.utils.text import tokenize


class HybridIndex:
    """
    Lightweight local hybrid index.
    It is intentionally dependency-light for an MVP:
    - BM25-like keyword score
    - TF-IDF cosine score
    """

    def __init__(self, chunks: list[Chunk]) -> None:
        self.chunks = chunks
        self.doc_tokens: list[list[str]] = []
        self.doc_tf: list[Counter[str]] = []
        self.df: Counter[str] = Counter()
        self.idf: dict[str, float] = {}
        self.avg_len = 1.0
        self._build()

    def _build(self) -> None:
        lengths = []
        for chunk in self.chunks:
            tokens = tokenize(" ".join([chunk.title, chunk.heading or "", chunk.text, " ".join(chunk.tags), " ".join(chunk.wikilinks)]))
            self.doc_tokens.append(tokens)
            tf = Counter(tokens)
            self.doc_tf.append(tf)
            lengths.append(len(tokens))
            for tok in tf:
                self.df[tok] += 1

        n = max(len(self.chunks), 1)
        self.avg_len = sum(lengths) / max(len(lengths), 1)
        self.idf = {tok: math.log(1 + (n - df + 0.5) / (df + 0.5)) for tok, df in self.df.items()}

    def _bm25(self, q_tokens: list[str], idx: int, k1: float = 1.5, b: float = 0.75) -> float:
        tf = self.doc_tf[idx]
        dl = max(len(self.doc_tokens[idx]), 1)
        score = 0.0
        for tok in q_tokens:
            if tok not in tf:
                continue
            idf = self.idf.get(tok, 0.0)
            f = tf[tok]
            denom = f + k1 * (1 - b + b * dl / self.avg_len)
            score += idf * (f * (k1 + 1)) / max(denom, 1e-9)
        return score

    def _tfidf_cosine(self, q_tokens: list[str], idx: int) -> float:
        q_tf = Counter(q_tokens)
        d_tf = self.doc_tf[idx]

        common = set(q_tf) & set(d_tf)
        numerator = sum((q_tf[t] * self.idf.get(t, 0.0)) * (d_tf[t] * self.idf.get(t, 0.0)) for t in common)
        q_norm = math.sqrt(sum((f * self.idf.get(t, 0.0)) ** 2 for t, f in q_tf.items()))
        d_norm = math.sqrt(sum((f * self.idf.get(t, 0.0)) ** 2 for t, f in d_tf.items()))
        if q_norm == 0 or d_norm == 0:
            return 0.0
        return numerator / (q_norm * d_norm)

    def search(self, query: str, top_k: int = 20) -> list[Chunk]:
        q_tokens = tokenize(query)
        if not q_tokens:
            return []

        scored: list[tuple[float, Chunk]] = []
        for i, chunk in enumerate(self.chunks):
            bm25 = self._bm25(q_tokens, i)
            cosine = self._tfidf_cosine(q_tokens, i)
            tag_bonus = 0.15 if any(tok in " ".join(chunk.tags).lower() for tok in q_tokens) else 0.0
            link_bonus = 0.10 if any(tok in " ".join(chunk.wikilinks).lower() for tok in q_tokens) else 0.0
            score = 0.65 * bm25 + 3.0 * cosine + tag_bonus + link_bonus
            if score > 0:
                c = Chunk(**{**chunk.__dict__})
                c.score = round(float(score), 6)
                scored.append((score, c))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [c for _, c in scored[:top_k]]
