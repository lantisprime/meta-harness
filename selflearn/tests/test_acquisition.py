"""Acquisition: registry resolution, plugins, rate limiting, reputability."""
import io
import json
import tarfile

import pytest

from selflearn.acquisition import (
    AcquireContext,
    AcquisitionError,
    PluginRegistry,
    ReputabilityPolicy,
    builtin_plugins,
    html_to_text,
    rank_passages,
    registrable_domain,
)
from selflearn.acquisition.plugins import ArxivPlugin, LocalPlugin, WebPlugin
from selflearn.contracts import SourceRef
from selflearn.ports import JsonlProvenance


class FakeFetcher:
    def __init__(self, responses: dict):
        self.responses = responses
        self.calls: list[str] = []

    def fetch(self, url: str) -> bytes:
        self.calls.append(url)
        if url not in self.responses:
            raise AcquisitionError(f"fetch failed for {url!r}: 404")
        return self.responses[url]


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.slept: list[float] = []

    def clock(self):
        return self.now

    def sleep(self, s):
        self.slept.append(s)
        self.now += s


def ctx_with(tmp_path, fetcher=None, **kw):
    fc = FakeClock()
    return AcquireContext(workdir=tmp_path / "work", fetcher=fetcher,
                          clock=fc.clock, sleep=fc.sleep, **kw), fc


# -- registry ---------------------------------------------------------------

def test_unclaimed_ref_is_loud(tmp_path):
    registry = PluginRegistry([LocalPlugin()])
    ctx, _ = ctx_with(tmp_path)
    with pytest.raises(AcquisitionError, match="no plugin claims"):
        registry.gather([SourceRef(uri="gopher://old")], ctx)


def test_first_match_wins_and_provenance_records_plugin(tmp_path):
    f = tmp_path / "note.md"
    f.write_text("Lifespan replaces on_event handlers in FastAPI.")
    prov = JsonlProvenance(tmp_path / "prov.jsonl")
    registry = PluginRegistry(builtin_plugins(), provenance=prov)
    ctx, _ = ctx_with(tmp_path)
    docs = registry.gather([SourceRef(uri=f"file://{f}")], ctx)
    assert docs[0].provenance.plugin == "local"
    events = [json.loads(l) for l in (tmp_path / "prov.jsonl").read_text().splitlines()]
    assert events[0]["plugin"] == "local" and events[0]["plugin_version"]


def test_duplicate_plugin_ids_rejected():
    with pytest.raises(AcquisitionError, match="duplicate"):
        PluginRegistry([LocalPlugin(), LocalPlugin()])


# -- local plugin -----------------------------------------------------------

def test_local_missing_and_empty_are_loud(tmp_path):
    plugin = LocalPlugin()
    ctx, _ = ctx_with(tmp_path)
    with pytest.raises(AcquisitionError, match="does not exist"):
        plugin.acquire(SourceRef(uri=f"file://{tmp_path}/nope.md"), ctx)
    empty = tmp_path / "empty.md"
    empty.write_text("   ")
    with pytest.raises(AcquisitionError, match="is empty"):
        plugin.acquire(SourceRef(uri=f"file://{empty}"), ctx)


def test_local_ytdistill_chunks_old_and_new_schema(tmp_path):
    f = tmp_path / "chunks.jsonl"
    f.write_text("\n".join([
        json.dumps({"record_type": "summary", "text": "sum", "start": 0}),
        json.dumps({"record_type": "transcript_chunk", "text": "chunk one",
                    "source_url": "https://youtu.be/x", "start": 3.0}),
        json.dumps({"id": "old", "text": "old-schema chunk", "start": 9.0}),
    ]))
    docs = LocalPlugin().acquire(SourceRef(uri=f"file://{f}"), ctx_with(tmp_path)[0])
    assert docs[0].chunks == ("chunk one", "old-schema chunk")
    assert docs[0].provenance.url == "https://youtu.be/x"
    assert docs[0].provenance.locator == "t=3s"


# -- web plugin -------------------------------------------------------------

HTML = (b"<html><head><script>evil()</script><style>x{}</style></head><body>"
        b"<nav>menu menu</nav><h1>Lifespan</h1><p>FastAPI lifespan context "
        b"manager replaces on_event handlers.</p><p>Unrelated paragraph about "
        b"cooking pasta at home.</p></body></html>")


def test_web_page_extraction_and_tier(tmp_path):
    fetcher = FakeFetcher({"https://docs.example.org/lifespan": HTML})
    policy = ReputabilityPolicy(official=frozenset({"docs.example.org"}))
    ctx, _ = ctx_with(tmp_path, fetcher=fetcher, policy=policy)
    docs = WebPlugin().acquire(SourceRef(uri="https://docs.example.org/lifespan"), ctx)
    text = docs[0].blocks[0]
    assert "lifespan context" in text.lower()
    assert "evil()" not in text and "menu" not in text
    assert docs[0].tier == "official"


def test_web_search_ref_ranks_passages_and_needs_backend(tmp_path):
    class Backend:
        def search(self, query, max_results):
            return ["https://a.example.org/page"]

    fetcher = FakeFetcher({"https://a.example.org/page": HTML})
    ctx, _ = ctx_with(tmp_path, fetcher=fetcher)
    docs = WebPlugin(backend=Backend()).acquire(
        SourceRef(uri="search:fastapi lifespan handlers"), ctx)
    # question-ranked: the lifespan passage outranks the pasta one
    assert "lifespan" in docs[0].chunks[0].lower()
    with pytest.raises(AcquisitionError, match="SearchBackend"):
        WebPlugin().acquire(SourceRef(uri="search:anything"), ctx)


def test_rate_limit_between_fetches(tmp_path):
    fetcher = FakeFetcher({"https://a.example.org/1": HTML,
                           "https://a.example.org/2": HTML})
    ctx, fc = ctx_with(tmp_path, fetcher=fetcher, min_fetch_interval_s=2.0)
    plugin = WebPlugin()
    plugin.acquire(SourceRef(uri="https://a.example.org/1"), ctx)
    plugin.acquire(SourceRef(uri="https://a.example.org/2"), ctx)
    assert fc.slept and fc.slept[-1] > 0   # second fetch waited


def test_fetch_without_fetcher_is_loud(tmp_path):
    ctx, _ = ctx_with(tmp_path, fetcher=None)
    with pytest.raises(AcquisitionError, match="no fetcher configured"):
        WebPlugin().acquire(SourceRef(uri="https://a.example.org/x"), ctx)


def test_workdir_jail(tmp_path):
    ctx, _ = ctx_with(tmp_path)
    with pytest.raises(AcquisitionError, match="escapes"):
        ctx.artifact_path("../outside.txt")


# -- arxiv plugin -----------------------------------------------------------

def _arxiv_tarball() -> bytes:
    tex = (r"% a comment to strip" "\n"
           r"\section{Intro} The outer loop evolves harness code." "\n"
           r"\begin{equation} a^2 + b^2 = c^2 \end{equation}" "\n"
           r"\caption{Pareto frontier of accuracy vs tokens}" "\n")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        data = tex.encode()
        info = tarfile.TarInfo(name="paper.tex")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def test_arxiv_latex_fast_path(tmp_path):
    fetcher = FakeFetcher({"https://arxiv.org/e-print/2603.28052": _arxiv_tarball()})
    ctx, _ = ctx_with(tmp_path, fetcher=fetcher)
    docs = ArxivPlugin().acquire(
        SourceRef(uri="https://arxiv.org/abs/2603.28052"), ctx)
    text = docs[0].blocks[0]
    assert "[equation] a^2 + b^2 = c^2" in text
    assert "[figure caption] Pareto frontier" in text
    assert "% a comment" not in text
    assert docs[0].tier == "official"
    assert docs[0].provenance.locator == "arxiv:2603.28052"


def test_arxiv_non_tarball_falls_back_loudly(tmp_path):
    fetcher = FakeFetcher({"https://arxiv.org/e-print/2603.28052": b"not a tar"})
    ctx, _ = ctx_with(tmp_path, fetcher=fetcher)
    with pytest.raises(AcquisitionError, match="pdf plugin"):
        ArxivPlugin().acquire(SourceRef(uri="https://arxiv.org/abs/2603.28052"), ctx)


# -- helpers ----------------------------------------------------------------

def test_registrable_domain():
    assert registrable_domain("https://www.Example.org/path") == "example.org"
    assert registrable_domain("docs.example.org") == "docs.example.org"


def test_rank_passages_orders_by_question():
    chunks = ("pasta cooking tips at home", "lifespan handlers replace on_event")
    ranked = rank_passages("fastapi lifespan handlers", chunks)
    assert ranked[0].startswith("lifespan")
