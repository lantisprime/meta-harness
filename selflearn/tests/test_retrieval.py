"""Retrieval: semantic ranking, learning prior, budget, degraded mode, indexing."""
import hashlib
import math
import re

import pytest

from selflearn.contracts import CandidateEntry, EntrySource, PublishDecision
from selflearn.retrieval import Retriever, render_injection_block
from selflearn.store import PackStore, StoreError


class HashEmbedder:
    """Deterministic embedding stand-in with real cosine geometry."""

    def __init__(self, embedder_id="hash-v1", dim=64):
        self.embedder_id = embedder_id
        self.dim = dim

    def embed(self, texts):
        out = []
        for t in texts:
            v = [0.0] * self.dim
            for tok in re.findall(r"[a-z0-9]{3,}", t.lower()):
                v[int(hashlib.md5(tok.encode()).hexdigest(), 16) % self.dim] += 1.0
            n = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append(tuple(x / n for x in v))
        return out


SRC = EntrySource(url="https://docs.example.org/x", fetched_at="t",
                  sha256="0" * 64, tier="official")


def publish(store, eid, body, topic="t", pack="fastapi"):
    e = CandidateEntry(id=eid, pack=pack, kind="knowledge", body=body,
                       claims=(body.split(".")[0],), sources=(SRC,), topic=topic)
    store.add_candidate(e)
    store.publish(e.id, PublishDecision(entry_id=e.id, publish=True,
                                        basis=("test",), identity_basis="model-id"))
    return e


@pytest.fixture()
def indexed_store(tmp_path):
    store = PackStore(tmp_path)
    publish(store, "kn-f-lifespan", "FastAPI lifespan context manager replaces "
            "on_event startup shutdown handlers for application lifecycle.")
    publish(store, "kn-f-middleware", "FastAPI middleware wraps requests; use "
            "BaseHTTPMiddleware or pure ASGI middleware for performance.")
    publish(store, "kn-f-deps", "FastAPI dependency injection with Depends "
            "resolves nested dependencies and caches per-request.")
    retriever = Retriever(store, HashEmbedder())
    assert retriever.index("fastapi") == 3
    return store, retriever


def test_semantic_ranking_prefers_relevant_entry(indexed_store):
    _, retriever = indexed_store
    got = retriever.retrieve(["fastapi"], "how do I run startup shutdown "
                             "lifecycle code with the lifespan handler")
    assert got and got[0].entry_id == "kn-f-lifespan"
    assert not got[0].degraded


def test_learning_prior_sinks_harmful_entry(indexed_store):
    store, retriever = indexed_store
    baseline = retriever.retrieve(["fastapi"], "fastapi middleware requests")
    assert baseline[0].entry_id == "kn-f-middleware"
    store.mark("kn-f-middleware", harmful=8.0)
    store.mark("kn-f-deps", helpful=4.0)
    reranked = retriever.retrieve(["fastapi"], "fastapi middleware requests")
    assert reranked[0].entry_id != "kn-f-middleware"


def test_budget_skips_oversized_entries(indexed_store):
    store, retriever = indexed_store
    publish(store, "kn-f-huge", "lifespan " * 500)   # 500 tokens of relevance
    retriever.index("fastapi")
    got = retriever.retrieve(["fastapi"], "lifespan startup", budget_tokens=60)
    assert got, "small relevant entries still fit"
    assert "kn-f-huge" not in [r.entry_id for r in got]


def test_k_cap(indexed_store):
    _, retriever = indexed_store
    got = retriever.retrieve(["fastapi"], "fastapi", k=2)
    assert len(got) <= 2


def test_degraded_mode_is_loud_and_flagged(tmp_path):
    store = PackStore(tmp_path)
    publish(store, "kn-f-lifespan", "lifespan replaces on_event handlers.")
    retriever = Retriever(store, embedder=None)
    assert retriever.degraded
    with pytest.warns(UserWarning, match="WITHOUT an embedding endpoint"):
        got = retriever.retrieve(["fastapi"], "lifespan on_event")
    assert got and got[0].degraded


def test_unindexed_entries_are_lazily_indexed(tmp_path):
    """Review fix: entries published after wiring no longer hard-fail
    retrieval — they get embedded on the fly and the vectors persist."""
    store = PackStore(tmp_path)
    publish(store, "kn-f-a", "lifespan replaces on_event.")
    retriever = Retriever(store, HashEmbedder())
    got = retriever.retrieve(["fastapi"], "lifespan")
    assert got and got[0].entry_id == "kn-f-a"
    assert store.get("kn-f-a").embedder_id == "hash-v1"


def test_embedder_swap_reindexes(indexed_store):
    store, _ = indexed_store
    r2 = Retriever(store, HashEmbedder(embedder_id="hash-v2"))
    assert r2.index("fastapi") == 3          # every vector re-embedded
    assert r2.index("fastapi") == 0          # idempotent
    assert r2.retrieve(["fastapi"], "lifespan startup")


def test_index_without_embedder_is_loud(tmp_path):
    retriever = Retriever(PackStore(tmp_path), embedder=None)
    with pytest.raises(StoreError, match="EmbeddingPort"):
        retriever.index("fastapi")


def test_injection_block_framing(indexed_store):
    _, retriever = indexed_store
    got = retriever.retrieve(["fastapi"], "lifespan startup shutdown")
    block = render_injection_block(got)
    assert "field notes" in block.text
    assert "untrusted advisory" in block.text
    assert "your memories" not in block.text
    assert 'id="kn-f-lifespan"' in block.text
    assert "docs.example.org" in block.text
    assert "kn-f-lifespan" in block.entry_ids
    assert not block.empty


def test_injection_block_truncates_and_empty(indexed_store):
    store, retriever = indexed_store
    publish(store, "kn-f-long", "lifespan " + "word " * 600)
    retriever.index("fastapi")
    got = retriever.retrieve(["fastapi"], "lifespan", budget_tokens=5000)
    block = render_injection_block(got, max_entry_tokens=50)
    assert "…[truncated]" in block.text
    assert render_injection_block([]).empty
