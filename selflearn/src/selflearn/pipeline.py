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
from selflearn.contracts import SourceRef
from selflearn.distillation import Distiller
from selflearn.ports import ProvenancePort
from selflearn.store.packstore import PackStore, StoreError
from selflearn.verification import Verifier


@dataclass
class AcquisitionReport:
    pack: str
    topic: str
    mode: str = "strict"
    gathered: int = 0
    distilled: int = 0
    quarantined: list[str] = field(default_factory=list)
    rejected: dict[str, list[str]] = field(default_factory=dict)
    held_for_approval: list[str] = field(default_factory=list)
    published: list[str] = field(default_factory=list)
    skipped_existing: list[str] = field(default_factory=list)

    @property
    def verified(self) -> list[str]:
        """Derived, not stored (review fix: a third counter that must stay
        in sync with the other two is a self-contradiction waiting)."""
        return self.held_for_approval + self.published

    def summary(self) -> str:
        tail = (f"{len(self.published)} auto-published (eval-gated)"
                if self.mode == "auto"
                else f"{len(self.held_for_approval)} held for approval")
        return (f"pack={self.pack} topic={self.topic} [{self.mode}]: "
                f"{self.gathered} docs → {self.distilled} entries "
                f"({len(self.quarantined)} quarantined), "
                f"{len(self.verified)} verified, {tail}, "
                f"{len(self.rejected)} rejected, "
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
    evalgen=None,
    answer_model=None,
) -> AcquisitionReport:
    """``evalgen`` + ``answer_model`` switch the run to **auto mode**
    (decision 3): entries that pass verification AND whose second-model-
    validated probes pass with the entry injected publish without a human,
    under the bootstrap rule. Without them the run is strict: verified
    entries are held for explicit approval."""
    auto = evalgen is not None and answer_model is not None
    report = AcquisitionReport(pack=pack, topic=topic,
                               mode="auto" if auto else "strict")

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
        if not vreport.ok:
            report.rejected[entry.id] = vreport.rejected
            event({"event": "acquisition.item.rejected", "entry": entry.id,
                   "reasons": vreport.rejected})
            continue
        if not auto:
            report.held_for_approval.append(entry.id)
            event({"event": "acquisition.item.verified", "entry": entry.id,
                   "basis": vreport.basis,
                   "held": "strict mode: awaiting human approval"})
            continue
        # auto mode: evalgen -> second-model validation -> eval gate
        from selflearn.verification.suite import eval_gated_decision

        probes = evalgen.generate(entry)
        genreport = evalgen.validate(probes, source_excerpts)
        decision = eval_gated_decision(
            entry, vreport, genreport.validated, answer_model,
            suite_size=store.suite_size(pack),
            identity_basis=evalgen.identity.basis,
            execution=verifier.execution)
        if decision.publish:
            store.publish(entry.id, decision, probes=genreport.validated)
            report.published.append(entry.id)
            event({"event": "acquisition.item.published", "entry": entry.id,
                   "basis": list(decision.basis),
                   "probes": [p.id for p in genreport.validated],
                   "probes_rejected_by_validator": list(genreport.rejected)})
        else:
            report.held_for_approval.append(entry.id)
            event({"event": "acquisition.item.gate_failed", "entry": entry.id,
                   "basis": list(decision.basis),
                   "held": "eval gate not passed: held for human approval"})

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
