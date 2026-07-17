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
    DuckDuckGoBackend,
    SearxngBackend,
    WikipediaBackend,
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


DDG_HTML = (
    '<div><a class="result__a" href="//duckduckgo.com/l/?uddg='
    'https%3A%2F%2Ffastapi.tiangolo.com%2Fadvanced%2Fevents%2F&amp;rut=x">'
    'FastAPI events</a>'
    '<a class="result__a" href="https://direct.example.org/page">direct</a>'
    '<a class="result__a" href="//duckduckgo.com/l/?uddg='
    'https%3A%2F%2Ffastapi.tiangolo.com%2Fadvanced%2Fevents%2F">dupe</a></div>'
).encode()


def test_duckduckgo_backend_decodes_redirects_and_dedupes():
    backend = DuckDuckGoBackend(transport=lambda u, h: DDG_HTML)
    urls = backend.search("fastapi lifespan", 5)
    assert urls == ["https://fastapi.tiangolo.com/advanced/events/",
                    "https://direct.example.org/page"]


def test_duckduckgo_no_results_is_loud():
    backend = DuckDuckGoBackend(transport=lambda u, h: b"<html>captcha</html>")
    with pytest.raises(AcquisitionError, match="no parseable results"):
        backend.search("anything", 3)


def test_wikipedia_backend_builds_article_urls():
    def transport(url, headers):
        assert "srsearch=" in url and "list=search" in url
        return json.dumps({"query": {"search": [
            {"title": "FastAPI"}, {"title": "Web framework"}]}}).encode()

    backend = WikipediaBackend(transport=transport)
    assert backend.search("fastapi", 2) == [
        "https://en.wikipedia.org/wiki/FastAPI",
        "https://en.wikipedia.org/wiki/Web_framework"]


def test_cli_backend_selection(monkeypatch):
    from argparse import Namespace
    from selflearn.cli import _search_backend

    monkeypatch.delenv("BRAVE_API_KEY", raising=False)

    def ns(**kw):
        base = dict(search_backend="auto", brave_key="", searxng="")
        base.update(kw)
        return Namespace(**base)

    # zero-config default: DuckDuckGo, no subscription needed
    assert isinstance(_search_backend(ns()), DuckDuckGoBackend)
    # explicit choices
    assert isinstance(_search_backend(ns(search_backend="wikipedia")),
                      WikipediaBackend)
    assert isinstance(_search_backend(ns(search_backend="ddg",
                                         brave_key="ignored")),
                      DuckDuckGoBackend)
    # auto honors brave key (flag or env) then searxng
    b = _search_backend(ns(brave_key="flag-key"))
    assert isinstance(b, BraveBackend) and b.api_key == "flag-key"
    monkeypatch.setenv("BRAVE_API_KEY", "env-key")
    assert isinstance(_search_backend(ns()), BraveBackend)
    monkeypatch.delenv("BRAVE_API_KEY")
    assert isinstance(_search_backend(ns(searxng="https://sx.local")),
                      SearxngBackend)


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
