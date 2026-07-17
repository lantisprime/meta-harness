#!/usr/bin/env python3
"""M5 end-to-end demo on AI-course lecture notes — fully offline.

DISCLAIMER: ocw.mit.edu is unreachable from the dev container (network
policy), so the two "lecture notes" sources below are locally authored
stand-ins covering classic MIT-AI-course topics (6.034-style search/games,
6.S191-style neural networks). They are accurate standard material but were
NOT fetched from MIT; source tiers are operator-asserted for the demo. The
four model roles are deterministic rule-based stand-ins that operate on the
real fixture text — swap in real endpoints via the CLI for live runs.

What it demonstrates, end to end:
  gather -> distill -> verify -> evalgen -> second-model probe validation ->
  eval-gated AUTO-publish (bootstrap rule visible) -> retrieval with
  injection -> model qualification (with/without-injection delta).
"""
from __future__ import annotations

import hashlib
import math
import re
import sys
import tempfile
from pathlib import Path

from selflearn import (
    EvalGen,
    PackStore,
    Retriever,
    Verifier,
    qualify_model,
    render_injection_block,
    run_acquisition,
)
from selflearn.acquisition import AcquireContext, PluginRegistry
from selflearn.acquisition.plugins import LocalPlugin
from selflearn.contracts import SourceRef
from selflearn.distillation import Distiller
from selflearn.ports import JsonlProvenance, ModelIdIdentity

SEARCH_NOTES = """\
Uninformed search methods explore a state space without domain knowledge: \
breadth-first search expands the shallowest frontier node first and finds a \
shortest path when all step costs are equal, while depth-first search uses \
far less memory but can descend forever without a depth limit.

A star search orders the frontier by f(n) = g(n) + h(n), the cost so far \
plus a heuristic estimate to the goal, and it is guaranteed to find an \
optimal path when the heuristic is admissible, meaning it never \
overestimates the true remaining cost.

Minimax computes game values by assuming the maximizing player and the \
minimizing player both play optimally down the game tree.

Alpha-beta pruning eliminates branches that cannot influence the final \
minimax decision, and with good move ordering it roughly doubles the \
searchable depth for the same budget.
"""

NEURAL_NOTES = """\
A perceptron computes a weighted sum of its inputs followed by a threshold, \
and a single layer can only separate classes with a linear boundary.

Backpropagation applies the chain rule to propagate the loss gradient from \
the output layer backward through the network, giving each weight its \
partial derivative in one backward pass.

Stochastic gradient descent updates weights using small random mini-batches, \
trading gradient accuracy for far cheaper and more frequent steps.

Overfitting appears when training loss keeps falling while validation loss \
rises, and regularization techniques such as dropout and weight decay narrow \
that gap.
"""

_SENT = re.compile(r"(?<=[.!?])\s+")
_WORD = re.compile(r"[a-z0-9]{3,}")


def sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT.split(text.replace("\n", " ")) if s.strip()]


def overlap(a: str, b: str) -> int:
    return len(set(_WORD.findall(a.lower())) & set(_WORD.findall(b.lower())))


class ExtractiveDistiller:
    """Rule-based stand-in for the knowledge-distiller role: one entry per
    paragraph, claim = its lead sentence (so every claim IS source text)."""
    model_id = "distiller-sim"

    def complete(self, role, prompt, context):
        entries = []
        for chunk in context["source_text"].split("\n\n"):
            sents = sentences(chunk)
            if sents:
                entries.append({"kind": "knowledge", "body": chunk.strip(),
                                "claims": [sents[0]],
                                "topic": context["topic"]})
        return {"entries": entries}


class ProbeAuthorSim:
    model_id = "probe-author-sim"

    def complete(self, role, prompt, context):
        claim = context["claims"][0]
        words = claim.split()
        head = " ".join(words[: max(4, len(words) // 3)])
        return {"probes": [
            {"kind": "recall",
             "question": f"According to the course notes, complete: {head} …?",
             "expected": claim},
            {"kind": "application",
             "question": f"A new problem needs the technique where {head.lower()} — "
                         "what applies and why?",
             "expected": claim},
        ]}


class ValidatorSim:
    """Second model: answers ONLY from the source excerpts it is shown."""
    model_id = "probe-validator-sim"

    def complete(self, role, prompt, context):
        best = max(sentences(context["source_excerpts"]),
                   key=lambda s: overlap(s, context["question"]), default="")
        if overlap(best, context["question"]) < 3:
            return {"answer": "cannot determine from sources"}
        return {"answer": best}


class SpecialistSim:
    """Simulated specialist: competent WITH the injected notes, lost without."""
    model_id = "specialist-sim"

    def complete(self, role, prompt, context):
        block = re.sub(r"<[^>]+>", " ", context.get("knowledge_block", ""))
        if block.strip():
            best = max(sentences(block),
                       key=lambda s: overlap(s, context["question"]), default="")
            return {"answer": best}
        return {"answer": "I am not certain without my notes."}


class IgnorantModel:
    model_id = "ignorant-sim"

    def complete(self, role, prompt, context):
        return {"answer": "no idea"}


class HashEmbedder:
    embedder_id = "hash-v1"

    def embed(self, texts):
        out = []
        for t in texts:
            v = [0.0] * 64
            for tok in _WORD.findall(t.lower()):
                v[int(hashlib.md5(tok.encode()).hexdigest(), 16) % 64] += 1.0
            n = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append(tuple(x / n for x in v))
        return out


def main() -> int:
    print(__doc__.split("What it")[0])
    base = Path(tempfile.mkdtemp(prefix="mit-demo-"))
    (base / "notes").mkdir()
    (base / "notes" / "search-and-games.md").write_text(SEARCH_NOTES)
    (base / "notes" / "neural-networks.md").write_text(NEURAL_NOTES)

    store = PackStore(base / "store")
    evalgen = EvalGen(ProbeAuthorSim(), ValidatorSim(), ModelIdIdentity())
    common = dict(registry=PluginRegistry([LocalPlugin()]),
                  distiller=Distiller(ExtractiveDistiller()),
                  verifier=Verifier(), store=store, evalgen=evalgen,
                  answer_model=SpecialistSim(),
                  provenance=JsonlProvenance(base / "run.jsonl"))

    for name, topic in (("search-and-games", "search-and-games"),
                        ("neural-networks", "neural-networks")):
        report = run_acquisition(
            [SourceRef(uri=f"file://{base}/notes/{name}.md", hint="official")],
            pack="ai-course", topic=topic,
            ctx=AcquireContext(workdir=base / "w" / name), **common)
        print(f"\n=== acquisition: {topic} ===")
        print(report.summary())
        for eid in report.published:
            probes = store.probes_for(eid)
            print(f"  published {eid.split('-')[-1]}: "
                  f"{store.get(eid).cand.claims[0][:70]}…")
            print(f"    probes: {[p.kind for p in probes]} "
                  f"validated_by={probes[0].validated_by}")
    suite = store.suite_size("ai-course")
    print(f"\npack suite: {suite} second-model-validated probes; coverage: "
          f"{store.coverage('ai-course')}")

    import json as _json
    published_events = [
        _json.loads(line)
        for line in (base / "run.jsonl").read_text().splitlines()
        if "item.published" in line]
    print("\ngate basis, first vs later publish (bootstrap rule, finding 1):")
    for ev in (published_events[0], published_events[-1]):
        gate_lines = [b for b in ev["basis"] if "BOOTSTRAP" in b or "suite" in b]
        print(f"  {ev['entry'].split('-')[-1]}: {gate_lines[0]}")

    print("\n=== retrieval + steering ===")
    retriever = Retriever(store, HashEmbedder())
    retriever.index("ai-course")
    q = "how does alpha-beta pruning cut down the game tree search"
    results = retriever.retrieve(["ai-course"], q, k=2)
    block = render_injection_block(results)
    print(f"query: {q!r}")
    for r in results:
        print(f"  -> {r.entry_id.split('-')[-1]} score={r.score:.3f}: "
              f"{r.entry.cand.claims[0][:70]}…")
    answer = SpecialistSim().complete("specialist-answer", "", {
        "question": q, "knowledge_block": block.text})
    print(f"specialist answer (with injection): {answer['answer'][:110]}…")

    print("\n=== model qualification (platform-agnostic serving contract) ===")
    for model in (SpecialistSim(), IgnorantModel()):
        qr = qualify_model(model, store, "ai-course")
        print(f"  {qr.model_id}: with={qr.with_injection:.0%} "
              f"without={qr.without_injection:.0%} delta={qr.delta:+.0%} "
              f"-> {'QUALIFIED' if qr.qualified else 'NOT QUALIFIED'} "
              f"({qr.total_probes} probes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
