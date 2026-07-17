"""The deterministic acquisition spine: gather → distill → verify → hold.

This is the standalone equivalent of the host's ``knowledge_acquisition``
workflow template: plain journaled code calling one module per stage, no
LLM in control of the lifecycle. Intra-phase fan-out (M4 decision): items
run sequentially with a per-item provenance event each, so a resumed or
partial run can tell exactly which items completed — engine-level
parallelism can replace the loop later without changing the contract.

M4 runs strict: verified entries are *held* as candidates awaiting explicit
human approval (``approve_entry``); nothing auto-publishes until the M5
eval gate exists.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

from selflearn.acquisition import AcquireContext, PluginRegistry
from selflearn.contracts import CandidateEntry, SourceRef
from selflearn.distillation import Distiller
from selflearn.ports import ProvenancePort
from selflearn.store.packstore import PackStore, StoreError
from selflearn.verification import Verifier


@dataclass
class AcquisitionReport:
    pack: str
    topic: str
    gathered: int = 0
    distilled: int = 0
    quarantined: list[str] = field(default_factory=list)
    verified: list[str] = field(default_factory=list)
    rejected: dict[str, list[str]] = field(default_factory=dict)
    held_for_approval: list[str] = field(default_factory=list)
    skipped_existing: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (f"pack={self.pack} topic={self.topic}: {self.gathered} docs → "
                f"{self.distilled} entries ({len(self.quarantined)} "
                f"quarantined), {len(self.verified)} verified and held for "
                f"approval, {len(self.rejected)} rejected, "
                f"{len(self.skipped_existing)} already known")


def run_acquisition(
    refs: Iterable[SourceRef],
    pack: str,
    topic: str,
    *,
    registry: PluginRegistry,
    ctx: AcquireContext,
    distiller: Distiller,
    verifier: Verifier,
    store: PackStore,
    provenance: Optional[ProvenancePort] = None,
) -> AcquisitionReport:
    report = AcquisitionReport(pack=pack, topic=topic)

    def event(payload: dict) -> None:
        if provenance is not None:
            provenance.append(payload)

    event({"event": "acquisition.started", "pack": pack, "topic": topic})
    store.claim_topics(pack, [topic])

    docs = registry.gather(list(refs), ctx)          # loud on any failure
    report.gathered = len(docs)

    entries = distiller.distill(docs, pack, topic)   # SchemaGuard, screen
    report.distilled = len(entries)
    source_excerpts = "\n\n".join(
        chunk for doc in docs for chunk in (doc.chunks or doc.blocks))

    for entry in entries:                            # sequential, journaled
        try:
            store.add_candidate(entry)
        except StoreError:
            report.skipped_existing.append(entry.id)
            event({"event": "acquisition.item.skipped", "entry": entry.id,
                   "reason": "already known"})
            continue
        if entry.quarantined:
            report.quarantined.append(entry.id)
            event({"event": "acquisition.item.quarantined", "entry": entry.id,
                   "reason": entry.quarantine_reason})
            continue
        vreport = verifier.verify(entry, source_excerpts=source_excerpts)
        if vreport.ok:
            report.verified.append(entry.id)
            report.held_for_approval.append(entry.id)
            event({"event": "acquisition.item.verified", "entry": entry.id,
                   "basis": vreport.basis,
                   "held": "strict mode: awaiting human approval"})
        else:
            report.rejected[entry.id] = vreport.rejected
            event({"event": "acquisition.item.rejected", "entry": entry.id,
                   "reasons": vreport.rejected})

    event({"event": "acquisition.finished", "pack": pack,
           "summary": report.summary()})
    return report


def approve_entry(store: PackStore, verifier: Verifier, entry_id: str,
                  source_excerpts: str = "",
                  approved_by: str = "human") -> None:
    """Strict-mode publish: re-verify, then publish with the human approval
    recorded in the decision basis. Loud if verification no longer passes."""
    stored = store.get(entry_id)
    vreport = verifier.verify(stored.cand, source_excerpts=source_excerpts)
    if not vreport.ok:
        raise StoreError(
            f"{entry_id} no longer passes verification; refusing approval: "
            f"{vreport.rejected}")
    decision = verifier.decide(stored.cand, vreport)
    decision = type(decision)(
        entry_id=decision.entry_id, publish=True,
        basis=decision.basis + (f"strict-mode approval by {approved_by}",),
        identity_basis=decision.identity_basis, strict_mode=True)
    store.publish(entry_id, decision)
