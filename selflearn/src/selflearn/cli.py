"""selflearn CLI: exercise the pipeline with no host harness at all.

    selflearn gather  file:///path/to/docs --workdir W --out sources.json
    selflearn distill sources.json --pack fastapi --topic lifespan \
        --store S --endpoint http://127.0.0.1:1234/v1 --model qwen3
    selflearn seed-kb  memory/knowledge_base --pack meta-research --store S
    selflearn seed-yt  distilled/some-lecture --pack lectures --store S
    selflearn list     --store S
    selflearn retrieve "how do lifespan handlers work" --packs fastapi --store S
    selflearn next     --store S        # prioritized next-best-action advice
    selflearn doctor   --store S --fix  # diagnose and repair the store
    selflearn wizard                    # interactive, wizard-driven front door

Distilled entries land as candidates (quarantined ones flagged); the gates
that publish them arrive with M4/M5 — the CLI says so rather than
pretending.
"""
from __future__ import annotations

import argparse
import json
import os
import re
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
from selflearn.ports import JsonlProvenance, OpenAICompatEmbedding
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
        return _extract_json(content)


def _extract_json(content: str) -> dict:
    """Parse model output into JSON, tolerating hybrid-thinking <think>
    blocks and code fences anywhere in the text (review finding: edge-only
    fence stripping re-introduced the think-text parse bug metaharness
    already fixed in harness/local.py; qwen3-class models emit both)."""
    text = re.sub(r"<think>.*?</think>", " ", content, flags=re.S)
    text = re.sub(r"<think>.*", " ", text, flags=re.S)   # unterminated block
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.S)
    if fenced:
        text = fenced.group(1)
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            return json.loads(text[start:end + 1])
        raise


def _search_backend(args):
    """Selection: explicit --search-backend choice > explicit flags
    (--brave-key, then --searxng) > BRAVE_API_KEY env > DuckDuckGo default.

    Explicit flags ALWAYS beat the environment (review finding: an exported
    BRAVE_API_KEY silently overrode a user's --searxng choice, sending
    queries to a third party they had deliberately avoided).
    """
    from selflearn.acquisition import AcquisitionError

    choice = getattr(args, "search_backend", "auto")
    brave_flag = getattr(args, "brave_key", "")
    searxng_flag = getattr(args, "searxng", "")
    env_key = os.environ.get("BRAVE_API_KEY", "")
    if choice == "wikipedia":
        return WikipediaBackend()
    if choice == "ddg":
        return DuckDuckGoBackend()
    if choice == "brave":
        if not (brave_flag or env_key):
            raise AcquisitionError("--search-backend brave needs --brave-key "
                                   "or BRAVE_API_KEY")
        return BraveBackend(brave_flag or env_key)
    if choice == "searxng":
        if not searxng_flag:
            raise AcquisitionError("--search-backend searxng needs --searxng URL")
        return SearxngBackend(searxng_flag)
    # auto: explicit flags first, environment after, keyless default last
    if brave_flag:
        return BraveBackend(brave_flag)
    if searxng_flag:
        return SearxngBackend(searxng_flag)
    if env_key:
        return BraveBackend(env_key)
    return DuckDuckGoBackend()


def _embedder(args):
    if getattr(args, "embedding_endpoint", ""):
        return OpenAICompatEmbedding(args.embedding_endpoint,
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
        # quarantine is its own display state (review fix: it was collapsed
        # into generic REJECTED, contradicting the pipeline's reporting)
        if stored.cand.quarantined:
            print(f"  [QUARANTINED] {stored.cand.id} — "
                  f"{stored.cand.quarantine_reason}; release with "
                  f"'selflearn release {stored.cand.id} --store {args.store} "
                  f"--reason ... --by you@example.com'")
            continue
        report = verifier.verify(stored.cand)
        state = "ELIGIBLE" if report.ok else "REJECTED"
        ok_n += report.ok
        detail = report.basis[0] if report.ok else report.rejected[0]
        print(f"  [{state}] {stored.cand.id} — {detail}")
    print(f"{ok_n}/{len(candidates)} eligible; publish each with "
          f"'selflearn approve <id> --store {args.store}'")
    return 0


def cmd_release(args) -> int:
    store = PackStore(Path(args.store))
    store.release_quarantine(args.entry_id, reason=args.reason, released_by=args.by)
    print(f"released {args.entry_id} from quarantine (journaled; it is a "
          "normal candidate again and must pass verification to publish)")
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


def cmd_deprecate(args) -> int:
    store = PackStore(Path(args.store))
    store.deprecate(args.entry_id, reason=args.reason)
    print(f"deprecated {args.entry_id} (probes retired; reversible with "
          f"'selflearn restore {args.entry_id} --store {args.store} "
          "--reason ...')")
    return 0


def cmd_restore(args) -> int:
    store = PackStore(Path(args.store))
    store.restore(args.entry_id, reason=args.reason)
    print(f"restored {args.entry_id} to published (probes un-retired)")
    return 0


def cmd_next(args) -> int:
    from selflearn.advisor import render_suggestions, suggest_actions

    try:
        store = PackStore(Path(args.store))
    except Exception as exc:
        print(f"store failed to load: {exc}", file=sys.stderr)
        print(f"diagnose and repair with: selflearn doctor "
              f"--store {args.store} --fix", file=sys.stderr)
        return 2      # a broken store is an error, per the exit contract
    print(f"next best actions for {args.store}:")
    print(render_suggestions(suggest_actions(store)))
    return 0


def cmd_doctor(args) -> int:
    from selflearn.doctor import run_doctor

    report = run_doctor(Path(args.store), fix=args.fix)
    print(report.render())
    return 0 if report.ok else 1


def cmd_wizard(args) -> int:
    from selflearn.wizard import run_wizard

    return run_wizard(runner=main, store=args.store)


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

    # Shared flag groups (review fix: copy-pasted argparse blocks had
    # already drifted — lost help texts). One declaration per flag.
    store_p = argparse.ArgumentParser(add_help=False)
    store_p.add_argument("--store", required=True,
                         help="knowledge store root directory")
    search_p = argparse.ArgumentParser(add_help=False)
    search_p.add_argument("--search-backend", default="auto",
                          choices=["auto", "ddg", "wikipedia", "searxng",
                                   "brave"],
                          help="auto = explicit flags, then BRAVE_API_KEY, "
                               "then DuckDuckGo (keyless default); explicit "
                               "choices force one backend")
    search_p.add_argument("--brave-key", default="",
                          help="Brave Search API key (also read from "
                               "BRAVE_API_KEY; explicit flags beat the env)")
    search_p.add_argument("--searxng", default="",
                          help="self-hosted SearXNG instance base url "
                               "(free, no key)")
    search_p.add_argument("--embedding-endpoint", default="",
                          help="OpenAI-compatible base url for SEMANTIC "
                               "passage ranking (keyword fallback without it)")
    search_p.add_argument("--embedding-model", default="")
    net_p = argparse.ArgumentParser(add_help=False)
    net_p.add_argument("--tier", default="",
                       help="operator-asserted tier hint for local sources")
    net_p.add_argument("--no-network", action="store_true",
                       help="offline: file:// refs only")
    model_p = argparse.ArgumentParser(add_help=False)
    model_p.add_argument("--endpoint", required=True,
                         help="OpenAI-compatible base url, e.g. "
                              "http://127.0.0.1:1234/v1")
    model_p.add_argument("--model", required=True)
    judge_p = argparse.ArgumentParser(add_help=False)
    judge_p.add_argument("--judge-endpoint", default="",
                         help="optional second endpoint for the "
                              "knowledge-judge role")
    judge_p.add_argument("--judge-model", default="")
    key_p = argparse.ArgumentParser(add_help=False)
    key_p.add_argument("--api-key", default="")

    p = sub.add_parser("gather", parents=[search_p, net_p, key_p],
                       help="acquire refs into source documents")
    p.add_argument("refs", nargs="+")
    p.add_argument("--workdir", required=True)
    p.add_argument("--out", default="sources.json")
    p.set_defaults(fn=cmd_gather)

    p = sub.add_parser("acquire",
                       parents=[store_p, search_p, net_p, model_p, judge_p,
                                key_p],
                       help="full pipeline: gather → distill → verify → hold")
    p.add_argument("refs", nargs="+")
    p.add_argument("--pack", required=True)
    p.add_argument("--topic", required=True)
    p.add_argument("--workdir", required=True)
    p.set_defaults(fn=cmd_acquire)

    p = sub.add_parser("verify", parents=[store_p, judge_p, key_p],
                       help="verify a pack's candidates (strict mode)")
    p.add_argument("--pack", required=True)
    p.set_defaults(fn=cmd_verify)

    p = sub.add_parser("approve", parents=[store_p, judge_p, key_p],
                       help="human approval: re-verify then publish one entry")
    p.add_argument("entry_id")
    p.add_argument("--approved-by", default="human")
    p.set_defaults(fn=cmd_approve)

    p = sub.add_parser("release", parents=[store_p],
                       help="journaled human release of a quarantined entry")
    p.add_argument("entry_id")
    p.add_argument("--reason", required=True)
    p.add_argument("--by", required=True,
                   help="identity of the releasing human")
    p.set_defaults(fn=cmd_release)

    p = sub.add_parser("distill", parents=[store_p, model_p, key_p],
                       help="distill gathered sources into candidates")
    p.add_argument("sources")
    p.add_argument("--pack", required=True)
    p.add_argument("--topic", required=True)
    p.set_defaults(fn=cmd_distill)

    p = sub.add_parser("seed-kb", parents=[store_p],
                       help="bulk-seed a knowledge-base directory")
    p.add_argument("dir")
    p.add_argument("--pack", required=True)
    p.add_argument("--publish", action="store_true")
    p.set_defaults(fn=cmd_seed_kb)

    p = sub.add_parser("seed-yt", parents=[store_p],
                       help="bulk-seed a yt-distill lecture folder")
    p.add_argument("dir")
    p.add_argument("--pack", required=True)
    p.add_argument("--publish", action="store_true")
    p.set_defaults(fn=cmd_seed_yt)

    p = sub.add_parser("list", parents=[store_p],
                       help="show packs, entries, suites, coverage")
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("retrieve", parents=[store_p],
                       help="test retrieval (keyword-degraded mode)")
    p.add_argument("query")
    p.add_argument("--packs", nargs="+", required=True)
    p.add_argument("-k", type=int, default=3)
    p.set_defaults(fn=cmd_retrieve)

    p = sub.add_parser("deprecate", parents=[store_p],
                       help="pull a published entry out of retrieval "
                            "(reversible)")
    p.add_argument("entry_id")
    p.add_argument("--reason", required=True)
    p.set_defaults(fn=cmd_deprecate)

    p = sub.add_parser("restore", parents=[store_p],
                       help="restore a deprecated entry to published")
    p.add_argument("entry_id")
    p.add_argument("--reason", required=True)
    p.set_defaults(fn=cmd_restore)

    p = sub.add_parser("next", parents=[store_p],
                       help="suggest the next best action for this store")
    p.set_defaults(fn=cmd_next)

    p = sub.add_parser("doctor", parents=[store_p],
                       help="diagnose store issues; --fix repairs them")
    p.add_argument("--fix", action="store_true",
                   help="apply repairs (default: report only)")
    p.set_defaults(fn=cmd_doctor)

    p = sub.add_parser("wizard",
                       help="interactive wizard: walks every workflow "
                            "step by step")
    p.add_argument("--store", default="",
                   help="knowledge store to start with (asked "
                        "interactively when omitted)")
    p.set_defaults(fn=cmd_wizard)

    args = parser.parse_args(argv)
    try:
        return args.fn(args)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
