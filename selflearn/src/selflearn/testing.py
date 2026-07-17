"""Shared test doubles — one HashEmbedder instead of seven drifting copies
(review finding: six verbatim copies plus a near-copy with a different
tokenizer produced different vector geometry across suites)."""
from __future__ import annotations

import hashlib
import math
import re

_TOKEN = re.compile(r"[a-z0-9]{3,}")


class HashEmbedder:
    """Deterministic EmbeddingPort stand-in with real cosine geometry."""

    def __init__(self, embedder_id: str = "hash-v1", dim: int = 64):
        self.embedder_id = embedder_id
        self.dim = dim

    def embed(self, texts: list[str]) -> list[tuple[float, ...]]:
        out = []
        for text in texts:
            v = [0.0] * self.dim
            for tok in _TOKEN.findall(text.lower()):
                v[int(hashlib.md5(tok.encode()).hexdigest(), 16) % self.dim] += 1.0
            n = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append(tuple(x / n for x in v))
        return out
