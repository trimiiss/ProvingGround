"""A tiny BM25 knowledge base built from the company facts."""

from __future__ import annotations

import hashlib
import math
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache

from provingground import textutil

from .company import DISTRACTOR_DOCS, FACTS_BY_KB


@dataclass(frozen=True)
class Document:
    id: str
    title: str
    text: str


class KnowledgeBase:
    def __init__(self, docs: list[Document], k1: float = 1.4, b: float = 0.75) -> None:
        self.docs = docs
        self.k1, self.b = k1, b
        self._tokens = [Counter(textutil.content_words(f"{d.title} {d.text}")) for d in docs]
        self._len = [sum(t.values()) for t in self._tokens]
        self._avg = sum(self._len) / max(1, len(self._len))
        df: Counter[str] = Counter()
        for tokens in self._tokens:
            df.update(tokens.keys())
        n = len(docs)
        self._idf = {w: math.log(1 + (n - c + 0.5) / (c + 0.5)) for w, c in df.items()}

    @property
    def version(self) -> str:
        return hashlib.sha256("\n".join(f"{d.id}:{d.text}" for d in self.docs).encode()).hexdigest()[:12]

    def search(self, query: str, k: int = 3) -> list[tuple[Document, float]]:
        terms = textutil.content_words(query)
        scored = []
        for i, tokens in enumerate(self._tokens):
            score = 0.0
            for term in terms:
                tf = tokens.get(term, 0)
                if tf:
                    denom = tf + self.k1 * (1 - self.b + self.b * self._len[i] / self._avg)
                    score += self._idf.get(term, 0.0) * tf * (self.k1 + 1) / denom
            if score > 0:
                scored.append((score, i))
        scored.sort(key=lambda x: (-x[0], x[1]))
        return [(self.docs[i], s) for s, i in scored[:k]]


@lru_cache(maxsize=8)
def knowledge_base(name: str) -> KnowledgeBase:
    facts = FACTS_BY_KB.get(name)
    if facts is None:
        raise ValueError(f"unknown knowledge base {name!r}")
    docs = [Document(f.id, f.title, f.text) for f in facts]
    docs += [Document(doc_id, title, text) for doc_id, title, text in DISTRACTOR_DOCS.get(name, [])]
    return KnowledgeBase(docs)
