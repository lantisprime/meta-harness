"""Retrieval module: semantic scoring over pack entries + budgeted selection.

Decision 1 (revised): embedding cosine over stored entry vectors is the
primary signal, multiplied by the learning-marks prior (StoredEntry.score),
with keyword overlap as a cheap prefilter at scale. When no EmbeddingPort is
configured, retrieval degrades to keyword-only — loudly: the retriever
carries ``degraded=True``, every result is flagged, and a warning is
emitted once. Vectors are keyed by embedder id; ``index()`` (re)embeds
anything produced by a different embedder (re-index on swap).
"""
from __future__ import annotations

import math
import re
import warnings
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from selflearn.ports import EmbeddingPort
from selflearn.store.packstore import PackStore, StoredEntry, StoreError

_WORD = re.compile(r"[a-z0-9]{3,}")

# Above this many candidates, keyword overlap prefilters before cosine.
PREFILTER_THRESHOLD = 256


def _words(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


def cosine(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b))


def _keyword_overlap(query_words: set[str], entry: StoredEntry,
                     entry_words: Optional[set[str]] = None) -> float:
    if entry_words is None:
        entry_words = _words(entry.cand.body + " " + " ".join(entry.cand.claims))
    if not query_words or not entry_words:
        return 0.0
    return len(query_words & entry_words) / math.sqrt(len(entry_words))


@dataclass(frozen=True)
class RetrievalResult:
    entry: StoredEntry
    score: float
    degraded: bool = False

    @property
    def entry_id(self) -> str:
        return self.entry.cand.id


class Retriever:
    def __init__(self, store: PackStore, embedder: Optional[EmbeddingPort] = None):
        self.store = store
        self.embedder = embedder
        self.degraded = embedder is None
        self._warned = False
        # bounded caches (review fix: every call re-embedded the query over
        # the network and re-tokenized every entry body)
        self._query_vectors: dict[str, tuple[float, ...]] = {}
        self._entry_words: dict[str, set[str]] = {}

    def _query_vector(self, query: str) -> tuple[float, ...]:
        if query not in self._query_vectors:
            if len(self._query_vectors) >= 128:
                self._query_vectors.pop(next(iter(self._query_vectors)))
            self._query_vectors[query] = self.embedder.embed([query])[0]
        return self._query_vectors[query]

    def _words_for(self, entry: StoredEntry) -> set[str]:
        eid = entry.cand.id
        if eid not in self._entry_words:
            self._entry_words[eid] = _words(
                entry.cand.body + " " + " ".join(entry.cand.claims))
        return self._entry_words[eid]

    # ------------------------------------------------------------------
    # Indexing (re-index on embedder swap)
    # ------------------------------------------------------------------

    def index(self, pack: str) -> int:
        """(Re)embed published entries not produced by the current embedder.
        Returns how many vectors were written. Loud without an embedder."""
        if self.embedder is None:
            raise StoreError("index() requires a configured EmbeddingPort")
        stale = self.store.reindex_needed(pack, self.embedder.embedder_id)
        if not stale:
            return 0
        entries = [self.store.get(eid) for eid in stale]
        vectors = self.embedder.embed(
            [e.cand.body + " " + " ".join(e.cand.claims) for e in entries])
        for entry, vector in zip(entries, vectors):
            self.store.set_vector(entry.cand.id, vector, self.embedder.embedder_id)
        return len(stale)

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    def retrieve(self, packs: list[str], query: str, k: int = 5,
                 budget_tokens: int = 1200,
                 task_type: str = "") -> list[RetrievalResult]:
        """``task_type`` sharpens the learning prior per task type
        (score_for): an entry that helps for code_edit but misleads for
        review ranks differently for each."""
        candidates = [e for pack in packs for e in self.store.published(pack)]
        if not candidates:
            return []
        query_words = _words(query)
        # one clock per call: priors decay consistently with staleness views
        now = datetime.now(timezone.utc)

        if self.degraded:
            if not self._warned:
                warnings.warn(
                    "selflearn retrieval running WITHOUT an embedding endpoint: "
                    "degraded keyword-only scoring (decision 1 wants semantic "
                    "scoring — configure an EmbeddingPort)", stacklevel=2)
                self._warned = True
            scored = [(_keyword_overlap(query_words, e, self._words_for(e))
                       * e.score_for(task_type, now=now), e)
                      for e in candidates]
        else:
            if len(candidates) > PREFILTER_THRESHOLD:
                candidates = sorted(
                    candidates,
                    key=lambda e: -_keyword_overlap(query_words, e),
                )[:PREFILTER_THRESHOLD]
            stale = [e for e in candidates
                     if e.embedder_id != self.embedder.embedder_id]
            if stale:
                # Lazy re-index (review finding: publishing after wiring left
                # unvectored entries that hard-failed every retrieval until a
                # manual index). No silent partial retrieval — we fix the gap
                # by embedding the stale entries now, not by skipping them.
                vectors = self.embedder.embed(
                    [e.cand.body + " " + " ".join(e.cand.claims)
                     for e in stale])
                for entry, vector in zip(stale, vectors):
                    self.store.set_vector(entry.cand.id, vector,
                                          self.embedder.embedder_id)
            qv = self._query_vector(query)
            scored = [(cosine(qv, e.vector) * e.score_for(task_type, now=now), e)
                      for e in candidates]

        ranked = sorted(scored, key=lambda t: -t[0])
        out: list[RetrievalResult] = []
        used_tokens = 0
        for score, entry in ranked:
            if score <= 0.0 or len(out) >= k:
                break
            entry_tokens = len(entry.cand.body.split())
            if used_tokens + entry_tokens > budget_tokens:
                continue    # try a smaller later entry rather than bust budget
            out.append(RetrievalResult(entry=entry, score=score,
                                       degraded=self.degraded))
            used_tokens += entry_tokens
        return out
