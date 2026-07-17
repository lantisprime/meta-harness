"""selflearn CLI: exercise the pipeline with no host harness at all.

    selflearn gather  file:///path/to/docs --workdir W --out sources.json
    selflearn distill sources.json --pack fastapi --topic lifespan \
        --store S --endpoint http://127.0.0.1:1234/v1 --model qwen3
    selflearn seed-kb  memory/knowledge_base --pack meta-research --store S
    selflearn seed-yt  distilled/some-lecture --pack lectures --store S
    selflearn list     --store S
    selflearn retrieve "how do lifespan handlers work" --packs fastapi --store S

Distilled entries land as candidates (quarantined ones flagged); the gates
that publish them arrive with M4/M5 — the CLI says so rather than
pretending.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from dataclasses import asdict
from pathlib import Path

from selflearn.acquisition import (
    AcquireContext,
    BraveBackend,
    DuckDuckGoBackend,
    PluginRegistry,
    SearxngBackend,
    UrllibFetcher,
    WikipediaBackend,
    builtin_plugins,
)
from selflearn.contracts import SourceRef
from selflearn.distillation import Distiller
from selflearn.ports import JsonlProvenance
from selflearn.retrieval import Retriever, render_injection_block
from selflearn.store import PackStore, seed_knowledge_base, seed_ytdistill


class OpenAICompatChat:
    """Minimal ModelPort over an OpenAI-compatible /chat/completions endpoint."""

    def __init__(self, base_url: str, model: str, api_key: str = "",
                 timeout_s: float = 120.0):
        self.base_url = base_url.rstrip("/")
        self.model_id = model
        self.api_key = api_key
        self.timeout_s = timeout_s

    def complete(self, role: str, prompt: str, context: dict) -> dict:
        body = {
            "model": self.model_id,
            "messages": [
                {"role": "system",
                 "content": f"You are the {role} worker. {prompt} "
                            "Reply with JSON only."},
                {"role": "user", "content": json.dumps(
                    {k: v for k, v in context.items() if k != "sources"}
                    | {"sources": context.get("sources", [])})[:60000]},
            ],
            "temperature": 0.2,
        }
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json",
                     **({"Authorization": f"Bearer {self.api_key}"}
                        if self.api_key else {})})
        with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
            payload = json.loads(resp.read())
        content = payload["choices"][0]["message"]["content"]
        content = content.strip().removeprefix("```json").removeprefix("```")
        content = content.removesuffix("```").strip()
        return json.loads(content)


class OpenAICompatEmbeddingClient:
    """Minimal EmbeddingPort over an OpenAI-compatible /embeddings endpoint."""

    def __init__(self, base_url: str, model: str, api_key: str = "",
                 timeout_s: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout_s = timeout_s
        self.embedder_id = f"openai-compat:{model}"

    def embed(self, texts: list[str]) -> list[tuple[float, ...]]:
        req = urllib.request.Request(
            f"{self.base_url}/embeddings",
            data=json.dumps({"model": self.model, "input": texts}).encode(),
            headers={"Content-Type": "application/json",
                     **({"Authorization": f"Bearer {self.api_key}"}
                        if self.api_key else {})})
        with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
            payload = json.loads(resp.read())
        data = sorted(payload["data"], key=lambda d: d["index"])
        return [tuple(d["embedding"]) for d in data]


def _search_backend(args):
    """Selection: explicit choice > Brave key > SearXNG url > DuckDuckGo.

    DuckDuckGo is the zero-config default — no key, no subscription.
    """
    choice = getattr(args, "search_backend", "auto")
    if choice == "wikipedia":
        return WikipediaBackend()
    if choice == "ddg":
        return DuckDuckGoBackend()
    brave_key = getattr(args, "brave_key", "") or os.environ.get("BRAVE_API_KEY", "")
    if brave_key:
        return BraveBackend(brave_key)
    if getattr(args, "searxng", ""):
        return SearxngBackend(args.searxng)
    return DuckDuckGoBackend()


def _embedder(args):
    if getattr(args, "embedding_endpoint", ""):
        return OpenAICompatEmbeddingClient(args.embedding_endpoint,
                                           args.embedding_model,
                                           args.api_key or "")
    return None


def _doc_to_dict(doc) -> dict:
    return {"ref_uri": doc.ref.uri, "blocks": list(doc.blocks),
            "chunks": list(doc.chunks), "tier": doc.tier,
            "provenance": asdict(doc.provenance)}


def _doc_from_dict(d: dict):
    from selflearn.contracts import Provenance, SourceDocument
    return SourceDocument(
        ref=SourceRef(uri=d["ref_uri"]), blocks=tuple(d["blocks"]),
        chunks=tuple(d["chunks"]), assets=(),
        provenance=Provenance(**d["provenance"]), tier=d["tier"])


def cmd_gather(args) -> int:
    ctx = AcquireContext(workdir=Path(args.workdir))
    if not args.no_network:
        ctx.fetcher = UrllibFetcher()
    registry = PluginRegistry(
        builtin_plugins(search_backend=_search_backend(args),
                        embedder=_embedder(args)),
        provenance=JsonlProvenance(Path(args.workdir) / "provenance.jsonl"))
    docs = registry.gather([SourceRef(uri=r, hint=args.tier or "") for r in args.refs],
                           ctx)
    out = Path(args.out)
    out.write_text(json.dumps([_doc_to_dict(d) for d in docs], indent=1))
    print(f"gathered {len(docs)} documents -> {out}")
    for d in docs:
        print(f"  [{d.tier:9}] {d.provenance.url} "
              f"({len(d.chunks)} chunks, plugin={d.provenance.plugin})")
    return 0


def cmd_distill(args) -> int:
    docs = [_doc_from_dict(d) for d in json.loads(Path(args.sources).read_text())]
    model = OpenAICompatChat(args.endpoint, args.model, args.api_key)
    distiller = Distiller(model)
    entries = distiller.distill(docs, pack=args.pack, topic=args.topic)
    store = PackStore(Path(args.store))
    added = 0
    for entry in entries:
        try:
            store.add_candidate(entry)
            added += 1
        except Exception as exc:
            print(f"  skip {entry.id}: {exc}")
    quarantined = [e for e in entries if e.quarantined]
    print(f"distilled {len(entries)} candidates ({added} new) into pack "
          f"{args.pack!r}; {len(quarantined)} quarantined")
    print("note: entries stay candidates — verification + eval gate (M4/M5) "
          "publish them")
    return 0


def _verifier(args):
    from selflearn.verification import Verifier
    judge = None
    if getattr(args, "judge_endpoint", ""):
        judge = OpenAICompatChat(args.judge_endpoint, args.judge_model,
                                 getattr(args, "api_key", ""))
    return Verifier(judge=judge)


def cmd_acquire(args) -> int:
    """gather → distill → verify → hold (strict mode) in one run."""
    from selflearn.pipeline import run_acquisition

    ctx = AcquireContext(workdir=Path(args.workdir))
    if not args.no_network:
        ctx.fetcher = UrllibFetcher()
    registry = PluginRegistry(
        builtin_plugins(search_backend=_search_backend(args),
                        embedder=_embedder(args)),
        provenance=JsonlProvenance(Path(args.workdir) / "provenance.jsonl"))
    model = OpenAICompatChat(args.endpoint, args.model, args.api_key)
    report = run_acquisition(
        [SourceRef(uri=r, hint=args.tier or "") for r in args.refs],
        pack=args.pack, topic=args.topic, registry=registry, ctx=ctx,
        distiller=Distiller(model), verifier=_verifier(args),
        store=PackStore(Path(args.store)),
        provenance=JsonlProvenance(Path(args.workdir) / "run.jsonl"))
    print(report.summary())
    for eid, reasons in report.rejected.items():
        print(f"  rejected {eid}: {reasons[0]}")
    if report.held_for_approval:
        print("held for approval (strict mode) — publish with:")
        for eid in report.held_for_approval:
            print(f"  selflearn approve {eid} --store {args.store}")
    return 0


def cmd_verify(args) -> int:
    store = PackStore(Path(args.store))
    verifier = _verifier(args)
    candidates = store.entries_for(args.pack, "candidate")
    if not candidates:
        print(f"no candidates in pack {args.pack!r}")
        return 1
    ok_n = 0
    for stored in candidates:
        report = verifier.verify(stored.cand)
        state = "ELIGIBLE" if report.ok else "REJECTED"
        ok_n += report.ok
        detail = report.basis[0] if report.ok else report.rejected[0]
        print(f"  [{state}] {stored.cand.id} — {detail}")
    print(f"{ok_n}/{len(candidates)} eligible; publish each with "
          f"'selflearn approve <id> --store {args.store}'")
    return 0


def cmd_approve(args) -> int:
    from selflearn.pipeline import approve_entry

    store = PackStore(Path(args.store))
    approve_entry(store, _verifier(args), args.entry_id,
                  approved_by=args.approved_by)
    print(f"published {args.entry_id} (strict-mode human approval recorded)")
    return 0


def cmd_seed_kb(args) -> int:
    store = PackStore(Path(args.store))
    ids = seed_knowledge_base(store, Path(args.dir), pack=args.pack,
                              publish=args.publish)
    print(f"seeded {len(ids)} entries into pack {args.pack!r}"
          f"{' (published, pre-gate)' if args.publish else ' (candidates)'}")
    return 0


def cmd_seed_yt(args) -> int:
    store = PackStore(Path(args.store))
    ids = seed_ytdistill(store, Path(args.dir), pack=args.pack,
                         publish=args.publish)
    print(f"seeded {len(ids)} lecture entries into pack {args.pack!r}")
    return 0


def cmd_list(args) -> int:
    store = PackStore(Path(args.store))
    for pack in store.packs():
        entries = store.entries_for(pack)
        by_status: dict[str, int] = {}
        for e in entries:
            by_status[e.status] = by_status.get(e.status, 0) + 1
        cov = store.coverage(pack)
        print(f"{pack}: {len(entries)} entries {by_status}, "
              f"suite={store.suite_size(pack)} probes, "
              f"coverage={sum(1 for v in cov.values() if v == 'covered')}"
              f"/{len(cov)} topics")
    return 0


def cmd_retrieve(args) -> int:
    store = PackStore(Path(args.store))
    retriever = Retriever(store, embedder=None)   # degraded mode, loudly
    results = retriever.retrieve(args.packs, args.query, k=args.k)
    block = render_injection_block(results)
    if block.empty:
        print("no published entries matched")
        return 1
    print(block.text)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="selflearn")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("gather", help="acquire refs into source documents")
    p.add_argument("refs", nargs="+")
    p.add_argument("--workdir", required=True)
    p.add_argument("--out", default="sources.json")
    p.add_argument("--tier", default="")
    p.add_argument("--no-network", action="store_true")
    p.add_argument("--search-backend", default="auto",
                   choices=["auto", "ddg", "wikipedia"],
                   help="auto = Brave if keyed, else SearXNG if given, else "
                        "DuckDuckGo (keyless default); 'wikipedia' uses the "
                        "official MediaWiki API")
    p.add_argument("--brave-key", default="",
                   help="optional Brave Search API key (also read from "
                        "BRAVE_API_KEY)")
    p.add_argument("--searxng", default="",
                   help="self-hosted SearXNG instance base url (free, no key)")
    p.add_argument("--embedding-endpoint", default="",
                   help="OpenAI-compatible base url for SEMANTIC passage "
                        "ranking (keyword fallback without it)")
    p.add_argument("--embedding-model", default="")
    p.add_argument("--api-key", default="")
    p.set_defaults(fn=cmd_gather)

    p = sub.add_parser("acquire",
                       help="full pipeline: gather → distill → verify → hold")
    p.add_argument("refs", nargs="+")
    p.add_argument("--pack", required=True)
    p.add_argument("--topic", required=True)
    p.add_argument("--store", required=True)
    p.add_argument("--workdir", required=True)
    p.add_argument("--endpoint", required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--api-key", default="")
    p.add_argument("--tier", default="")
    p.add_argument("--no-network", action="store_true")
    p.add_argument("--search-backend", default="auto",
                   choices=["auto", "ddg", "wikipedia"])
    p.add_argument("--brave-key", default="")
    p.add_argument("--searxng", default="")
    p.add_argument("--embedding-endpoint", default="")
    p.add_argument("--embedding-model", default="")
    p.add_argument("--judge-endpoint", default="",
                   help="optional second endpoint for the knowledge-judge role")
    p.add_argument("--judge-model", default="")
    p.set_defaults(fn=cmd_acquire)

    p = sub.add_parser("verify", help="verify a pack's candidates (strict mode)")
    p.add_argument("--pack", required=True)
    p.add_argument("--store", required=True)
    p.add_argument("--judge-endpoint", default="")
    p.add_argument("--judge-model", default="")
    p.add_argument("--api-key", default="")
    p.set_defaults(fn=cmd_verify)

    p = sub.add_parser("approve",
                       help="human approval: re-verify then publish one entry")
    p.add_argument("entry_id")
    p.add_argument("--store", required=True)
    p.add_argument("--approved-by", default="human")
    p.add_argument("--judge-endpoint", default="")
    p.add_argument("--judge-model", default="")
    p.add_argument("--api-key", default="")
    p.set_defaults(fn=cmd_approve)

    p = sub.add_parser("distill", help="distill gathered sources into candidates")
    p.add_argument("sources")
    p.add_argument("--pack", required=True)
    p.add_argument("--topic", required=True)
    p.add_argument("--store", required=True)
    p.add_argument("--endpoint", required=True,
                   help="OpenAI-compatible base url, e.g. http://127.0.0.1:1234/v1")
    p.add_argument("--model", required=True)
    p.add_argument("--api-key", default="")
    p.set_defaults(fn=cmd_distill)

    p = sub.add_parser("seed-kb", help="bulk-seed a knowledge-base directory")
    p.add_argument("dir")
    p.add_argument("--pack", required=True)
    p.add_argument("--store", required=True)
    p.add_argument("--publish", action="store_true")
    p.set_defaults(fn=cmd_seed_kb)

    p = sub.add_parser("seed-yt", help="bulk-seed a yt-distill lecture folder")
    p.add_argument("dir")
    p.add_argument("--pack", required=True)
    p.add_argument("--store", required=True)
    p.add_argument("--publish", action="store_true")
    p.set_defaults(fn=cmd_seed_yt)

    p = sub.add_parser("list", help="show packs, entries, suites, coverage")
    p.add_argument("--store", required=True)
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("retrieve", help="test retrieval (keyword-degraded mode)")
    p.add_argument("query")
    p.add_argument("--packs", nargs="+", required=True)
    p.add_argument("--store", required=True)
    p.add_argument("-k", type=int, default=3)
    p.set_defaults(fn=cmd_retrieve)

    args = parser.parse_args(argv)
    try:
        return args.fn(args)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
