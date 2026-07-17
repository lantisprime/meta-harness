"""Semantic web search: concrete backends + embedding-ranked passages."""
import hashlib
import json
import math
import re

import pytest

from selflearn.acquisition import (
    AcquireContext,
    AcquisitionError,
    BraveBackend,
    SearxngBackend,
    rank_passages,
)
from selflearn.acquisition.plugins import WebPlugin
from selflearn.contracts import SourceRef


class HashEmbedder:
    embedder_id = "hash-v1"

    def embed(self, texts):
        out = []
        for t in texts:
            v = [0.0] * 64
            for tok in re.findall(r"[a-z0-9]{3,}", t.lower()):
                v[int(hashlib.md5(tok.encode()).hexdigest(), 16) % 64] += 1.0
            n = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append(tuple(x / n for x in v))
        return out


def test_rank_passages_semantic_mode_uses_embeddings():
    chunks = (
        "pasta cooking methods and boiling water tips for the home chef",
        "the lifespan context manager governs startup shutdown lifecycle "
        "hooks replacing the deprecated on_event handlers",
    )
    ranked = rank_passages("fastapi lifespan startup shutdown handlers",
                           chunks, embedder=HashEmbedder())
    assert ranked[0].startswith("the lifespan")


def test_searxng_backend_parses_results():
    def transport(url, headers):
        assert "format=json" in url and "q=fastapi" in url
        return json.dumps({"results": [
            {"url": "https://a.example.org/1"},
            {"url": "https://b.example.net/2"},
            {"title": "no url"},
        ]}).encode()

    backend = SearxngBackend("https://searx.local", transport=transport)
    assert backend.search("fastapi lifespan", 2) == [
        "https://a.example.org/1", "https://b.example.net/2"]


def test_searxng_requires_base_url_and_loud_on_garbage():
    with pytest.raises(AcquisitionError, match="base_url"):
        SearxngBackend("")
    backend = SearxngBackend("https://searx.local",
                             transport=lambda u, h: b"<html>not json")
    with pytest.raises(AcquisitionError, match="unparseable"):
        backend.search("q", 3)


def test_brave_backend_sends_token_and_parses():
    seen = {}

    def transport(url, headers):
        seen.update(headers)
        return json.dumps({"web": {"results": [
            {"url": "https://docs.example.org/x"}]}}).encode()

    backend = BraveBackend("secret-token", transport=transport)
    assert backend.search("fastapi", 5) == ["https://docs.example.org/x"]
    assert seen["X-Subscription-Token"] == "secret-token"
    with pytest.raises(AcquisitionError, match="API key"):
        BraveBackend("")


def test_web_plugin_search_end_to_end_with_semantic_ranking(tmp_path):
    page = (b"<html><body><p>pasta cooking methods and boiling water tips "
            b"for the home chef today</p><p>the lifespan context manager "
            b"governs startup shutdown lifecycle hooks replacing deprecated "
            b"on_event handlers</p></body></html>")

    class Backend:
        def search(self, query, max_results):
            return ["https://a.example.org/page"]

    class Fetcher:
        def fetch(self, url):
            return page

    ctx = AcquireContext(workdir=tmp_path / "w", fetcher=Fetcher(),
                         min_fetch_interval_s=0.0)
    plugin = WebPlugin(backend=Backend(), embedder=HashEmbedder())
    docs = plugin.acquire(
        SourceRef(uri="search:fastapi lifespan startup handlers"), ctx)
    assert "lifespan context manager" in docs[0].chunks[0]
