"""Built-in probe-suite runner, the eval gate, and model qualification (M5).

The gate that makes auto-publish honest (decision 3): an entry publishes
without a human only when its validated probes pass *with the entry
injected*, on top of the M4 deterministic verification. Cold-start rule
(simulation finding 1): while the pack suite holds fewer than
``BOOTSTRAP_MIN_SUITE`` probes, the pack-level paired comparison is
deferred to promotion and the decision basis says so.

Qualification is the platform-agnostic serving contract: any model that
passes a pack's suite (with injection) is eligible to serve specialists
bound to that pack; the host records the evidence keyed by (model, pack).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence

from selflearn.contracts import CandidateEntry, Probe, PublishDecision
from selflearn.ports import ExecutionPort, ModelPort
from selflearn.store.packstore import PackStore
from selflearn.verification.evalgen import _matches
from selflearn.verification.verifier import VerificationError, VerificationReport

ANSWER_ROLE = "specialist-answer"

ANSWER_PROMPT = (
    "Answer the question. If domain notes are provided, ground your answer "
    "in them. Return JSON {\"answer\": \"...\"}."
)

BOOTSTRAP_MIN_SUITE = 5


@dataclass
class ProbeResult:
    probe_id: str
    kind: str
    passed: bool


@dataclass
class SuiteResult:
    model_id: str
    pack: str
    injected: bool
    results: list[ProbeResult] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total if self.results else 0.0

    def by_kind(self) -> dict[str, tuple[int, int]]:
        out: dict[str, list[int]] = {}
        for r in self.results:
            got = out.setdefault(r.kind, [0, 0])
            got[1] += 1
            got[0] += int(r.passed)
        return {k: (p, n) for k, (p, n) in out.items()}


def run_probe(model: ModelPort, probe: Probe, knowledge_block: str = "",
              execution: Optional[ExecutionPort] = None,
              skill_check: dict | None = None) -> bool:
    if probe.check_kind == "execution":
        if execution is None:
            raise VerificationError(
                f"probe {probe.id} is executable but no ExecutionPort is "
                "bound — refusing to skip")
        return execution.run_check(dict(skill_check or {})).ok
    answer = model.complete(ANSWER_ROLE, ANSWER_PROMPT, {
        "question": probe.question, "knowledge_block": knowledge_block})
    return _matches(probe.expected, str(answer.get("answer", "")))


def run_pack_suite(model: ModelPort, store: PackStore, pack: str,
                   injected: bool,
                   execution: Optional[ExecutionPort] = None) -> SuiteResult:
    """Run every live probe in the pack against ``model``, with or without
    that probe's entry injected — the with/without pair is the honesty
    measurement."""
    result = SuiteResult(model_id=getattr(model, "model_id", "?"), pack=pack,
                         injected=injected)
    for stored in store.entries_for(pack):
        probes = store.probes_for(stored.cand.id)
        if not probes:
            continue
        block = stored.cand.body if injected else ""
        for probe in probes:
            passed = run_probe(model, probe, knowledge_block=block,
                               execution=execution,
                               skill_check=dict(stored.cand.skill_check))
            result.results.append(ProbeResult(probe.id, probe.kind, passed))
    return result


@dataclass(frozen=True)
class QualificationResult:
    model_id: str
    pack: str
    with_injection: float
    without_injection: float
    total_probes: int

    @property
    def delta(self) -> float:
        return self.with_injection - self.without_injection

    @property
    def qualified(self) -> bool:
        """Eligible to serve: uses the pack when given it (non-negative
        delta) and answers most of the suite with it."""
        return self.total_probes > 0 and self.delta >= 0 and \
            self.with_injection >= 0.5


def qualify_model(model: ModelPort, store: PackStore, pack: str,
                  execution: Optional[ExecutionPort] = None) -> QualificationResult:
    with_inj = run_pack_suite(model, store, pack, injected=True,
                              execution=execution)
    without = run_pack_suite(model, store, pack, injected=False,
                             execution=execution)
    return QualificationResult(
        model_id=with_inj.model_id, pack=pack,
        with_injection=with_inj.pass_rate,
        without_injection=without.pass_rate,
        total_probes=with_inj.total)


def eval_gated_decision(
    entry: CandidateEntry,
    vreport: VerificationReport,
    probes: Sequence[Probe],
    answer_model: ModelPort,
    suite_size: int,
    identity_basis: str,
    execution: Optional[ExecutionPort] = None,
    bootstrap_min: int = BOOTSTRAP_MIN_SUITE,
) -> PublishDecision:
    """Decision 3's auto-publish gate, on top of M4 verification."""
    if not vreport.ok:
        return PublishDecision(entry_id=entry.id, publish=False,
                               basis=tuple(vreport.rejected),
                               identity_basis=identity_basis)
    validated = [p for p in probes if p.validated]
    if not validated:
        return PublishDecision(
            entry_id=entry.id, publish=False,
            basis=tuple(vreport.basis) + (
                "eval gate: no second-model-validated probes — cannot "
                "auto-publish; use strict human approval",),
            identity_basis=identity_basis)
    failed = [p.id for p in validated
              if not run_probe(answer_model, p, knowledge_block=entry.body,
                               execution=execution,
                               skill_check=dict(entry.skill_check))]
    if failed:
        return PublishDecision(
            entry_id=entry.id, publish=False,
            basis=tuple(vreport.basis) + (
                f"eval gate: probes fail WITH the entry injected: {failed}",),
            identity_basis=identity_basis)
    basis = list(vreport.basis)
    basis.append(f"eval gate: {len(validated)} validated probes pass with "
                 "entry injected")
    if suite_size < bootstrap_min:
        basis.append(f"BOOTSTRAP: pack suite {suite_size} < {bootstrap_min}; "
                     "paired pack gate deferred to promotion "
                     "(simulation finding 1)")
    else:
        basis.append(f"pack suite at {suite_size} probes; paired go/no-go "
                     "applies at promotion")
    return PublishDecision(entry_id=entry.id, publish=True,
                           basis=tuple(basis), identity_basis=identity_basis,
                           strict_mode=False)
