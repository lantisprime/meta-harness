"""Built-in source plugins: local, web (semantic search), arxiv, pdf, youtube.

Every plugin emits the same normalized ``SourceDocument`` envelope with full
provenance. Network access only through ``ctx.fetch`` (rate-limited,
size-capped); artifacts only through ``ctx.artifact_path`` (jailed).
"""
from __future__ import annotations

import gzip
import hashlib
import io
import re
import shutil
import subprocess
import tarfile
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Optional, Protocol, runtime_checkable

from selflearn.acquisition.context import AcquireContext, AcquisitionError
from selflearn.contracts import Provenance, SourceDocument, SourceRef

MAX_CHUNK_CHARS = 1600


def _sha(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode()
    return hashlib.sha256(data).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _chunk(text: str, max_chars: int = MAX_CHUNK_CHARS) -> tuple[str, ...]:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    buf = ""
    for p in paragraphs:
        if len(buf) + len(p) + 2 > max_chars and buf:
            chunks.append(buf.strip())
            buf = ""
        buf += p + "\n\n"
    if buf.strip():
        chunks.append(buf.strip())
    return tuple(chunks)


# ---------------------------------------------------------------------------
# local: drop-in .md/.json/.txt files and yt-distill distilled folders
# ---------------------------------------------------------------------------

class LocalPlugin:
    id = "local"
    version = "0.1"
    requires: tuple[str, ...] = ()

    def can_handle(self, ref: SourceRef) -> bool:
        return ref.uri.startswith("file://")

    def acquire(self, ref: SourceRef, ctx: AcquireContext) -> list[SourceDocument]:
        path = Path(ref.uri[len("file://"):])
        if not path.exists():
            raise AcquisitionError(f"local path {path} does not exist")
        if path.is_dir():
            files = sorted(p for p in path.rglob("*")
                           if p.suffix in (".md", ".json", ".txt", ".jsonl")
                           and p.is_file())
            if not files:
                raise AcquisitionError(f"{path} contains no importable files")
        else:
            files = [path]
        docs = []
        for f in files:
            text = f.read_text(errors="replace")
            if not text.strip():
                raise AcquisitionError(f"{f} is empty")
            if f.name == "chunks.jsonl":
                docs.append(self._ytdistill_chunks(ref, f, text))
                continue
            docs.append(SourceDocument(
                ref=ref, blocks=(text,), chunks=_chunk(text), assets=(),
                provenance=Provenance(url=f"file://{f}", fetched_at=_now(),
                                      sha256=_sha(text), plugin=self.id,
                                      plugin_version=self.version),
                tier=ref.hint if ref.hint in ("official", "primary",
                                              "community") else "unknown"))
        return docs

    def _ytdistill_chunks(self, ref: SourceRef, f: Path, text: str) -> SourceDocument:
        from selflearn.acquisition.ytdistill import parse_chunks

        parsed = parse_chunks(text, default_url=f"file://{f}")
        chunks = [r.text for r in parsed.chunks]
        if not chunks:
            raise AcquisitionError(f"{f} contains no transcript chunks")
        url = parsed.chunks[0].source_url
        return SourceDocument(
            ref=ref, blocks=("\n".join(chunks),), chunks=tuple(chunks), assets=(),
            provenance=Provenance(url=url, fetched_at=_now(), sha256=_sha(text),
                                  plugin=self.id, plugin_version=self.version,
                                  locator=parsed.span_locator),
            tier="primary")


# ---------------------------------------------------------------------------
# web: semantic search path (decision 2) + plain page fetch
# ---------------------------------------------------------------------------

@runtime_checkable
class SearchBackend(Protocol):
    """Pluggable backend: Brave API, SearXNG, or an MCP search server."""

    def search(self, query: str, max_results: int) -> list[str]:
        """Return candidate URLs for a natural-language question."""
        ...


class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "nav", "header", "footer", "noscript"}

    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip_depth:
            self._skip_depth -= 1
        if tag in ("p", "div", "li", "h1", "h2", "h3", "br", "tr"):
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip_depth and data.strip():
            self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    text = "".join(parser.parts)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def rank_passages(query: str, chunks: tuple[str, ...], top: int = 8,
                  embedder=None) -> tuple[str, ...]:
    """Question-ranked passages, not raw order — the web-RAG step.

    Reuses the retrieval module's scorer (decision 2 parity for real, not
    just by claim — review finding: an inlined zip dot-product silently
    truncated dimension mismatches where retrieval.cosine scores them 0.0).
    """
    from selflearn.retrieval.retriever import _words, cosine

    if not chunks:
        return chunks
    if embedder is not None:
        vectors = embedder.embed([query, *chunks])
        qv, cvs = vectors[0], vectors[1:]
        scored = [(cosine(qv, cv), c) for cv, c in zip(cvs, chunks)]
    else:
        qwords = _words(query)
        scored = []
        for c in chunks:
            cwords = _words(c)
            scored.append((len(qwords & cwords) / (len(cwords) ** 0.5 or 1.0), c))
    ranked = [c for s, c in sorted(scored, key=lambda t: -t[0]) if s > 0]
    return tuple(ranked[:top]) or chunks[:top]


class WebPlugin:
    id = "web"
    version = "0.1"
    requires: tuple[str, ...] = ()

    def __init__(self, backend: Optional[SearchBackend] = None,
                 max_results: int = 4, embedder=None):
        self.backend = backend
        self.max_results = max_results
        self.embedder = embedder

    def can_handle(self, ref: SourceRef) -> bool:
        return (ref.uri.startswith(("http://", "https://"))
                or ref.uri.startswith("search:"))

    def acquire(self, ref: SourceRef, ctx: AcquireContext) -> list[SourceDocument]:
        if ref.uri.startswith("search:"):
            if self.backend is None:
                raise AcquisitionError(
                    "search ref needs a SearchBackend (Brave, SearXNG, or an "
                    "MCP search server) — none configured")
            query = ref.uri[len("search:"):].strip()
            urls = self.backend.search(query, self.max_results)
            if not urls:
                raise AcquisitionError(f"search backend returned no results "
                                       f"for {query!r}")
            return [self._fetch_page(ref, url, ctx, query) for url in urls]
        return [self._fetch_page(ref, ref.uri, ctx, ref.hint)]

    def _fetch_page(self, ref: SourceRef, url: str, ctx: AcquireContext,
                    query: str = "") -> SourceDocument:
        raw = ctx.fetch(url)
        text = html_to_text(raw.decode(errors="replace"))
        if not text:
            raise AcquisitionError(f"{url!r} yielded no extractable text")
        chunks = _chunk(text)
        if query:
            chunks = rank_passages(query, chunks, embedder=self.embedder)
        return SourceDocument(
            ref=ref, blocks=(text[:6000],), chunks=chunks, assets=(),
            provenance=Provenance(url=url, fetched_at=_now(), sha256=_sha(raw),
                                  plugin=self.id, plugin_version=self.version),
            tier=ctx.policy.tier_for(url))


# ---------------------------------------------------------------------------
# arxiv: LaTeX source tarball fast path (decision 5), pdf fallback
# ---------------------------------------------------------------------------

_ARXIV = re.compile(r"arxiv\.org/(?:abs|e-print)/(?P<id>\d{4}\.\d{4,5})")


class ArxivPlugin:
    id = "arxiv"
    version = "0.1"
    requires: tuple[str, ...] = ()

    def can_handle(self, ref: SourceRef) -> bool:
        # abs/e-print only: arxiv.org/pdf/... routes to the pdf plugin, so
        # the prescribed fallback is actually reachable (review finding)
        return bool(_ARXIV.search(ref.uri))

    def acquire(self, ref: SourceRef, ctx: AcquireContext) -> list[SourceDocument]:
        arxiv_id = _ARXIV.search(ref.uri).group("id")
        url = f"https://arxiv.org/e-print/{arxiv_id}"
        raw = ctx.fetch(url)
        text = self._latex_text(raw)
        if not text:
            raise AcquisitionError(
                f"arxiv {arxiv_id}: no LaTeX source extractable; fall back to "
                f"the pdf plugin with https://arxiv.org/pdf/{arxiv_id}")
        return [SourceDocument(
            ref=ref, blocks=(text[:8000],), chunks=_chunk(text), assets=(),
            provenance=Provenance(url=url, fetched_at=_now(), sha256=_sha(raw),
                                  plugin=self.id, plugin_version=self.version,
                                  locator=f"arxiv:{arxiv_id}"),
            tier="official")]

    @staticmethod
    def _latex_text(raw: bytes) -> str:
        texts: list[str] = []
        try:
            tf = tarfile.open(fileobj=io.BytesIO(raw), mode="r:*")
            texts = [tf.extractfile(m).read().decode(errors="replace")
                     for m in tf.getmembers() if m.name.endswith(".tex")]
        except tarfile.TarError:
            # single-file submissions are served as plain gzip of one .tex,
            # not a tarball (review finding, reproduced with ReadError)
            try:
                texts = [gzip.decompress(raw).decode(errors="replace")]
            except (OSError, gzip.BadGzipFile):
                return ""
        parts: list[str] = []
        for tex in texts:
            tex = re.sub(r"(?<!\\)%.*", "", tex)                # comments
            # exact equations and captions are the point of the fast path
            for cap in re.findall(r"\\caption\{([^{}]+)\}", tex):
                parts.append(f"[figure caption] {cap}")
            for eq in re.findall(r"\\begin\{equation\}(.+?)\\end\{equation\}",
                                 tex, re.S):
                parts.append(f"[equation] {eq.strip()}")
            prose = re.sub(r"\\[a-zA-Z]+(\[[^\]]*\])?(\{[^{}]*\})?", " ", tex)
            parts.append(re.sub(r"\s+", " ", prose).strip())
        return "\n\n".join(p for p in parts if p)


# ---------------------------------------------------------------------------
# pdf: text layer via pypdf (rich figure/vision path arrives with M4)
# ---------------------------------------------------------------------------

class PdfPlugin:
    id = "pdf"
    version = "0.1"
    requires: tuple[str, ...] = ("pypdf",)

    def can_handle(self, ref: SourceRef) -> bool:
        uri = ref.uri.lower().split("?")[0]
        # arxiv.org/pdf/<id> serves a PDF without the extension — this is
        # the fallback target the arxiv plugin prescribes (review finding)
        return uri.endswith(".pdf") or "arxiv.org/pdf/" in uri

    def acquire(self, ref: SourceRef, ctx: AcquireContext) -> list[SourceDocument]:
        try:
            from pypdf import PdfReader
        except ImportError:
            raise AcquisitionError(
                "pdf plugin requires pypdf — install selflearn[pdf]")
        if ref.uri.startswith("file://"):
            raw = Path(ref.uri[len("file://"):]).read_bytes()
            url = ref.uri
        else:
            raw = ctx.fetch(ref.uri)
            url = ref.uri
        reader = PdfReader(io.BytesIO(raw))
        pages = [(i + 1, (page.extract_text() or "").strip())
                 for i, page in enumerate(reader.pages)]
        text = "\n\n".join(t for _, t in pages if t)
        if not text:
            raise AcquisitionError(
                f"{ref.uri!r}: no text layer extractable (scanned PDF?) — "
                "loud failure per decision 5, no OCR fallback")
        first_page = next((n for n, t in pages if t), 1)
        return [SourceDocument(
            ref=ref, blocks=(text[:8000],), chunks=_chunk(text), assets=(),
            provenance=Provenance(url=url, fetched_at=_now(), sha256=_sha(raw),
                                  plugin=self.id, plugin_version=self.version,
                                  locator=f"p{first_page}-{len(pages)}"),
            tier=ctx.policy.tier_for(url) if url.startswith("http") else "unknown")]


# ---------------------------------------------------------------------------
# youtube: drive yt-distill, or read an already-distilled folder
# ---------------------------------------------------------------------------

_YOUTUBE = re.compile(r"(youtube\.com/watch|youtu\.be/)")


class YoutubePlugin:
    id = "youtube"
    version = "0.1"
    requires: tuple[str, ...] = ("yt-distill",)

    def can_handle(self, ref: SourceRef) -> bool:
        return bool(_YOUTUBE.search(ref.uri))

    def acquire(self, ref: SourceRef, ctx: AcquireContext) -> list[SourceDocument]:
        exe = shutil.which("yt-distill")
        if exe is None:
            raise AcquisitionError(
                "youtube plugin requires the yt-distill CLI on PATH "
                "(lantisprime/youtube-distiller); alternatively pass the "
                "distilled folder via a file:// ref to the local plugin")
        outdir = ctx.artifact_path("ytdistill")
        proc = subprocess.run(
            [exe, "analyze", ref.uri, "--transcript-format", "none"],
            cwd=outdir, capture_output=True, text=True, timeout=1800)
        if proc.returncode != 0:
            raise AcquisitionError(
                f"yt-distill failed for {ref.uri!r}: {proc.stderr[-400:]}")
        produced = sorted((outdir / "distilled").glob("*/chunks.jsonl"))
        if not produced:
            raise AcquisitionError(f"yt-distill produced no chunks.jsonl "
                                   f"under {outdir}")
        local = LocalPlugin()
        docs: list[SourceDocument] = []
        for chunks_file in produced:
            docs.extend(local.acquire(
                SourceRef(uri=f"file://{chunks_file}"), ctx))
        return docs


def builtin_plugins(search_backend: Optional[SearchBackend] = None,
                    embedder=None) -> list:
    """Registry order matters: specific handlers before the generic web one."""
    return [LocalPlugin(), ArxivPlugin(), PdfPlugin(), YoutubePlugin(),
            WebPlugin(backend=search_backend, embedder=embedder)]
