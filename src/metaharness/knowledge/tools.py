"""Knowledge tools for the knowledge_acquisition workflow phases.

Workers drive acquisition through these four tools; deliberately there is
NO publish tool — strict mode means eligible entries wait for a human
(``selflearn approve`` or the console), so a worker cannot clear the gate
it is being verified by.

State: one AcquisitionSession per built toolset holds the gathered
documents between the gather and distill phases (both phases share the
run's workspace, so one toolset per run).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from metaharness.tools.registry import ToolError, ToolSpec

_STR = {"type": "string"}
_STR_LIST = {"type": "array", "items": {"type": "string"}}


def knowledge_tools(store, workdir: Path, search_backend=None, embedder=None,
                    verifier=None, fetcher: Any = "default") -> list[ToolSpec]:
    """Build the four knowledge ToolSpecs bound to a selflearn store.

    ``store`` is a selflearn PackStore; ``verifier`` a selflearn Verifier
    (default: deterministic-only). ``fetcher="default"`` uses the stdlib
    fetcher; pass None for offline (file:// refs only) or a custom Fetcher.
    """
    from selflearn.acquisition import (
        AcquireContext,
        AcquisitionError,
        PluginRegistry,
        UrllibFetcher,
        builtin_plugins,
    )
    from selflearn.contracts import SourceRef
    from selflearn.distillation import DistillationError, entries_from_specs
    from selflearn.store.packstore import StoreError
    from selflearn.verification import Verifier

    ctx = AcquireContext(
        workdir=Path(workdir),
        fetcher=UrllibFetcher() if fetcher == "default" else fetcher)
    registry = PluginRegistry(
        builtin_plugins(search_backend=search_backend, embedder=embedder))
    verifier = verifier or Verifier()
    session: dict[str, list] = {"docs": []}

    def knowledge_gather(refs: list[str], tier: str = "") -> str:
        try:
            docs = registry.gather(
                [SourceRef(uri=r, hint=tier) for r in refs], ctx)
        except AcquisitionError as exc:
            raise ToolError(str(exc))
        session["docs"].extend(docs)
        lines = [f"{d.provenance.url} [tier={d.tier}] "
                 f"{len(d.chunks)} chunks via {d.provenance.plugin}"
                 for d in docs]
        preview = (docs[0].chunks or docs[0].blocks)[0][:400]
        return (f"acquired {len(docs)} source document(s):\n"
                + "\n".join(lines) + f"\nfirst passage preview:\n{preview}")

    def knowledge_submit_entries(entries: list[dict], pack: str,
                                 topic: str) -> str:
        if not session["docs"]:
            raise ToolError("no gathered sources in this run — call "
                            "knowledge_gather first")
        try:
            built = entries_from_specs(entries, session["docs"], pack, topic)
        except DistillationError as exc:
            raise ToolError(str(exc))
        added, skipped, quarantined = [], [], []
        for entry in built:
            try:
                store.add_candidate(entry)
                (quarantined if entry.quarantined else added).append(entry.id)
            except StoreError:
                skipped.append(entry.id)
        return json.dumps({"added": added, "quarantined": quarantined,
                           "already_known": skipped})

    def knowledge_verify(pack: str) -> str:
        candidates = store.entries_for(pack, "candidate")
        if not candidates:
            raise ToolError(f"pack {pack!r} has no candidate entries")
        excerpts = "\n\n".join(
            chunk for d in session["docs"] for chunk in (d.chunks or d.blocks))
        lines = []
        for stored in candidates:
            report = verifier.verify(stored.cand, source_excerpts=excerpts)
            state = "ELIGIBLE" if report.ok else "REJECTED"
            detail = (report.basis if report.ok else report.rejected)[0]
            lines.append(f"[{state}] {stored.cand.id} — {detail}")
        lines.append("strict mode: eligible entries await HUMAN approval; "
                     "no tool can publish them")
        return "\n".join(lines)

    def knowledge_status(pack: str) -> str:
        entries = store.entries_for(pack)
        by_status: dict[str, int] = {}
        for e in entries:
            by_status[e.status] = by_status.get(e.status, 0) + 1
        cov = store.coverage(pack)
        return json.dumps({"pack": pack, "entries": by_status,
                           "coverage": cov, "suite": store.suite_size(pack)})

    return [
        ToolSpec("knowledge_gather",
                 "Acquire source refs (URLs, file://, arXiv, search:<query>) "
                 "into provenance-stamped documents for this run.",
                 {"type": "object",
                  "properties": {"refs": _STR_LIST, "tier": _STR},
                  "required": ["refs"]},
                 knowledge_gather,
                 keywords=("knowledge", "gather", "acquire", "source",
                           "research", "fetch")),
        ToolSpec("knowledge_submit_entries",
                 "Submit distilled entry specs; schema-guards, screens for "
                 "injection, stores as candidate entries.",
                 {"type": "object",
                  "properties": {"entries": {"type": "array"},
                                 "pack": _STR, "topic": _STR},
                  "required": ["entries", "pack", "topic"]},
                 knowledge_submit_entries,
                 keywords=("knowledge", "distill", "entry", "submit")),
        ToolSpec("knowledge_verify",
                 "Verify a pack's candidate entries (corroboration, "
                 "citations, checks); publishing stays human-gated.",
                 {"type": "object", "properties": {"pack": _STR},
                  "required": ["pack"]},
                 knowledge_verify,
                 keywords=("knowledge", "verify", "corroborate", "gate")),
        ToolSpec("knowledge_status",
                 "Pack status: entry counts by lifecycle state, coverage "
                 "map, eval-suite size.",
                 {"type": "object", "properties": {"pack": _STR},
                  "required": ["pack"]},
                 knowledge_status,
                 keywords=("knowledge", "status", "pack", "coverage")),
    ]
