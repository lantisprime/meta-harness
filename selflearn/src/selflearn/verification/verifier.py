"""Verification module (M4 scope): the external gates before any publish.

Deterministic checks first — quarantine status, structural citations, the
corroboration rule (independence = distinct registrable domains, simulation
finding 2; vision-extracted content needs corroboration even when an
official source is present) — then skill ``check:`` execution through the
sandboxed ExecutionPort, then the ``knowledge-judge`` rubric fallback for
claim-support when a judge model is bound.

M4 publishes in **strict mode only**: a positive decision means *eligible*,
and an explicit human approval performs the publish. The eval gate that
enables auto-publish lands in M5 (evalgen + second-model probe validation).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from selflearn.contracts import CandidateEntry, PublishDecision
from selflearn.ports import ExecutionPort, ModelPort

JUDGE_ROLE = "knowledge-judge"

JUDGE_PROMPT = (
    "Judge whether every claim of the entry is supported by the source "
    'excerpts. Return JSON {"supported": true|false, "unsupported_claims": '
    "[...]}. Judge support strictly from the excerpts; outside knowledge "
    "does not count."
)

M4_IDENTITY_BASIS = ("n/a (M4 strict mode: no probe validation ran; "
                     "second-model checks land in M5)")


class VerificationError(RuntimeError):
    """Loud failure: missing capability (e.g. no sandbox for an executable
    check) — distinct from a *rejection*, which is a normal verdict."""


@dataclass(frozen=True)
class CorroborationRule:
    """The deterministic reputability/corroboration policy, per pack."""

    min_independent_domains: int = 2
    official_suffices: bool = True
    # vision-extracted content: lower evidence class, official shortcut off
    vision_min_independent_domains: int = 2


@dataclass
class VerificationReport:
    entry_id: str
    ok: bool
    basis: list[str] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)


class Verifier:
    def __init__(self, rule: CorroborationRule = CorroborationRule(),
                 execution: Optional[ExecutionPort] = None,
                 judge: Optional[ModelPort] = None):
        self.rule = rule
        self.execution = execution
        self.judge = judge

    # ------------------------------------------------------------------

    def verify(self, entry: CandidateEntry,
               source_excerpts: str = "") -> VerificationReport:
        report = VerificationReport(entry_id=entry.id, ok=True)

        if entry.quarantined:
            report.ok = False
            report.rejected.append(
                f"quarantined ({entry.quarantine_reason}); requires human "
                "review, no gate can clear it")
            return report

        self._check_citations(entry, report)
        self._check_corroboration(entry, report)
        self._check_skill(entry, report)
        self._check_judge(entry, report, source_excerpts)
        return report

    def decide(self, entry: CandidateEntry,
               report: VerificationReport) -> PublishDecision:
        basis = tuple(report.basis) if report.ok else tuple(report.rejected)
        return PublishDecision(
            entry_id=entry.id, publish=report.ok, basis=basis,
            identity_basis=M4_IDENTITY_BASIS, strict_mode=True)

    # ------------------------------------------------------------------

    def _check_citations(self, entry: CandidateEntry,
                         report: VerificationReport) -> None:
        missing = [s.url for s in entry.sources if not s.sha256]
        if missing:
            report.ok = False
            report.rejected.append(
                f"citations incomplete: sources without content hash: {missing}")
        else:
            report.basis.append(
                f"citations: {len(entry.sources)} hashed source(s)")

    def _check_corroboration(self, entry: CandidateEntry,
                             report: VerificationReport) -> None:
        domains = entry.independent_domains()
        tiers = {s.tier for s in entry.sources}
        is_vision = entry.extraction == "vision"
        if is_vision:
            needed = self.rule.vision_min_independent_domains
            if len(domains) >= needed:
                report.basis.append(
                    f"corroboration (vision class): {len(domains)} "
                    f"independent domains")
            else:
                report.ok = False
                report.rejected.append(
                    f"vision-extracted content needs ≥{needed} independent "
                    f"domains (official alone insufficient); got "
                    f"{sorted(domains)}")
            return
        if self.rule.official_suffices and "official" in tiers:
            report.basis.append("corroboration: official-tier source")
            return
        if len(domains) >= self.rule.min_independent_domains:
            report.basis.append(
                f"corroboration: {len(domains)} independent domains")
            return
        if "unknown" in tiers and len(tiers) == 1:
            report.ok = False
            report.rejected.append(
                "all sources are unknown-tier; unknown domains cannot be "
                "sole support for a published entry")
            return
        report.ok = False
        report.rejected.append(
            f"insufficient corroboration: need ≥"
            f"{self.rule.min_independent_domains} independent domains or one "
            f"official source; got {sorted(domains)} (tiers {sorted(tiers)})")

    def _check_skill(self, entry: CandidateEntry,
                     report: VerificationReport) -> None:
        if entry.kind != "skill" or not entry.skill_check:
            if entry.kind == "skill":
                report.basis.append(
                    "skill has check: none — verified like facts, lower "
                    "evidence class visible")
            return
        if self.execution is None:
            raise VerificationError(
                f"{entry.id}: skill declares an executable check but no "
                "ExecutionPort is bound — refusing to skip it silently")
        result = self.execution.run_check(dict(entry.skill_check))
        if result.ok:
            report.basis.append("skill check executed: PASS (sandboxed)")
        else:
            report.ok = False
            report.rejected.append(
                f"skill check executed: FAIL — {result.output[:200]}")

    def _check_judge(self, entry: CandidateEntry, report: VerificationReport,
                     source_excerpts: str) -> None:
        if self.judge is None:
            report.basis.append(
                "no judge bound: deterministic checks only (rubric fallback "
                "unavailable)")
            return
        if not source_excerpts:
            report.basis.append("judge skipped: no source excerpts supplied")
            return
        verdict = self.judge.complete(JUDGE_ROLE, JUDGE_PROMPT, {
            "claims": list(entry.claims), "body": entry.body,
            "source_excerpts": source_excerpts[:20000]})
        if verdict.get("supported") is True:
            report.basis.append(
                f"judge ({getattr(self.judge, 'model_id', '?')}): claims "
                "supported by excerpts")
        else:
            report.ok = False
            unsupported = verdict.get("unsupported_claims", [])
            report.rejected.append(
                f"judge: claims not supported by excerpts: {unsupported}")
