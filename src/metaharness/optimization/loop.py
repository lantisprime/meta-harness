"""The outer optimization loop — Meta-Harness (arXiv 2603.28052) over this
repo's own machinery.

Deterministic spine, intelligent steps: the loop itself (seeding, rounds,
validation, evaluation, Pareto bookkeeping, plateau stop, promotion gate) is
plain journaled code; LLM intelligence lives only inside the proposer step.

Per round:
    1. proposer reads the ledger (params, scores, hypotheses, RAW failure
       traces of every prior candidate) and proposes a delta + causal hypothesis
    2. interface validation: the delta must merge into valid HarnessParams and
       must not duplicate a tried configuration — violations are recorded as
       rejected candidates, loudly, and the proposer sees them next round
    3. the candidate stack is composed around a fresh base worker and scored on
       the search suite (pass^k + token cost, raw per-attempt traces kept)
    4. the Pareto frontier updates; a plateau detector stops a stalled search

Promotion reuses the eval gate over the whole Pareto frontier: every frontier
candidate is evaluated on a HELD-OUT suite; those where the paired go/no-go
comparison versus the seed says GO AND that strictly improve a held-out
objective are ranked by held-out (pass^k, tokens) and the winner is promoted —
never search-set numbers, search order, or "no regression" alone (the paper
holds test data out until final frontier evaluation)."""
from __future__ import annotations

from typing import Callable, Optional

from pydantic import BaseModel, Field, ValidationError

from metaharness.core.budget import Budget, BudgetExceeded, PlateauDetector
from metaharness.core.types import Task, Verdict
from metaharness.evals.gate import EvalTaskResult, GateReport, SuiteResult, compare_suites
from metaharness.evals.verifiers import verify_output
from metaharness.harness.runner import Runner
from metaharness.observability.tracing import tracer
from metaharness.optimization.ledger import Candidate, CandidateLedger, CandidateScores
from metaharness.optimization.params import HarnessParams
from metaharness.optimization.proposer import ProposalError, Proposer


class OptimizationReport(BaseModel):
    rounds_run: int = 0
    stopped: str = "rounds"          # rounds | plateau | budget | no-proposal
    seed_id: str = ""
    best_id: str = ""
    frontier: list[str] = Field(default_factory=list)
    gate: Optional[GateReport] = None
    promoted: bool = False
    pending: Optional[str] = None    # gate-passing candidate awaiting human approval
    finished_at: float = 0.0         # epoch seconds; the console shows freshness
    target_model: str = ""           # which model these results describe — swapping
                                     # the tier's model makes old ledgers stale
    notes: list[str] = Field(default_factory=list)


class HarnessOptimizer:
    """Searches harness configurations for a fixed base worker against a
    scoreable task suite. Domain-general: any Task list with checkable
    success signals works — classification, extraction, math, SDLC phases."""

    def __init__(
        self,
        base_factory: Callable[[], Runner],
        proposer: Proposer,
        search_tasks: list[Task],
        holdout_tasks: list[Task],
        ledger: CandidateLedger,
        k: int = 3,
        budget: Optional[Budget] = None,
        lessons: Optional[list[str]] = None,
        seed_params: Optional[HarnessParams] = None,
        auto_promote: bool = True,
    ) -> None:
        for name, tasks in (("search", search_tasks), ("holdout", holdout_tasks)):
            unscoreable = [t.id for t in tasks if not t.success_check and not t.output_schema]
            if unscoreable:
                raise ValueError(f"{name} tasks with no checkable signal: {unscoreable}")
        self.base_factory = base_factory
        self.proposer = proposer
        self.search_tasks = search_tasks
        self.holdout_tasks = holdout_tasks
        self.ledger = ledger
        self.k = k
        self.budget = budget
        self.lessons = lessons
        self.seed_params = seed_params or HarnessParams()
        # auto_promote=False parks a gate-passing winner as a pending promotion
        # for human approval (WebUI); True promotes immediately (CLI default).
        self.auto_promote = auto_promote

    # -- evaluation --------------------------------------------------------------

    async def _evaluate(
        self, label: str, params: HarnessParams, tasks: list[Task]
    ) -> tuple[SuiteResult, CandidateScores, list[dict]]:
        """Score one candidate stack: every task k times, deterministic verify,
        raw per-attempt trace rows. UNVERIFIED never counts as a pass."""
        runner = params.build(self.base_factory(), ledger_root=self.ledger.root)
        suite = SuiteResult(model=label, k=self.k)
        scores = CandidateScores(
            pass_hat_k=0.0, pass_at_1=0.0, tokens_in=0, tokens_out=0,
            cost_usd=0.0, tasks=len(tasks), k=self.k,
        )
        traces: list[dict] = []
        with tracer().start_as_current_span("optimize.evaluate") as span:
            span.set_attribute("optimize.candidate", label)
            span.set_attribute("optimize.tasks", len(tasks))
            for task in tasks:
                passes: list[bool] = []
                for attempt in range(self.k):
                    result = await runner.run(task)
                    verification = verify_output(task, result)
                    passes.append(verification.verdict == Verdict.PASS)
                    scores.tokens_in += result.tokens_in
                    scores.tokens_out += result.tokens_out
                    scores.cost_usd += result.cost_usd
                    traces.append({
                        "task_id": task.id,
                        "task_type": task.task_type.value,
                        "attempt": attempt + 1,
                        "objective": task.objective,
                        "output": result.output,
                        "raw_text": result.raw_text,
                        "error": result.error,
                        "verdict": verification.verdict.value,
                        "detail": verification.detail,
                        "failure_mode": verification.failure_mode.value if verification.failure_mode else None,
                        "scorer": verification.scorer,
                        "tokens_in": result.tokens_in,
                        "tokens_out": result.tokens_out,
                    })
                    if self.budget is not None:
                        self.budget.charge(
                            cost_usd=result.cost_usd,
                            tokens=result.tokens_in + result.tokens_out,
                        )
                suite.results.append(EvalTaskResult(
                    task_id=task.id, task_type=task.task_type.value, passes=passes,
                ))
            scores.pass_hat_k = suite.overall_pass_hat_k()
            scores.pass_at_1 = (
                sum(r.pass_rate for r in suite.results) / len(suite.results)
                if suite.results else 0.0
            )
            span.set_attribute("optimize.pass_hat_k", scores.pass_hat_k)
        return suite, scores, traces

    # -- the loop ---------------------------------------------------------------

    async def optimize(self, rounds: int = 6) -> OptimizationReport:
        """Run the search and persist the final report into the ledger root, so
        the WebUI can render results without holding the process."""
        from metaharness.core.types import now

        report = await self._optimize(rounds)
        report.finished_at = now()
        self.ledger.save_report(report.model_dump(mode="json"))
        return report

    async def _optimize(self, rounds: int) -> OptimizationReport:
        report = OptimizationReport(target_model=self.base_factory().model)

        # seed: the incumbent configuration, evaluated once. A pre-populated
        # ledger (resumed search) keeps its existing seed and history.
        if self.ledger.evaluated():
            seed = self.ledger.evaluated()[0]
        else:
            cid = self.ledger.next_id()
            try:
                _, scores, traces = await self._evaluate(cid, self.seed_params, self.search_tasks)
            except BudgetExceeded as exc:
                report.stopped = "budget"
                report.notes.append(f"budget cannot even cover the seed evaluation: {exc}")
                return report
            seed = self.ledger.record(
                Candidate(id=cid, hypothesis="seed: the incumbent harness configuration",
                          params=self.seed_params, scores=scores),
                traces=traces,
            )
        report.seed_id = seed.id

        detector = PlateauDetector()
        detector.record(self.ledger.best().scores.pass_hat_k)
        for _ in range(rounds):
            try:
                proposal = await self.proposer.propose(self.ledger, self.lessons)
            except ProposalError as exc:
                report.stopped = "no-proposal"
                report.notes.append(f"proposer stopped: {exc}")
                break
            except BudgetExceeded as exc:
                # the proposer's own LLM call exhausted the budget — stop cleanly,
                # mirroring the eval-side budget stop rather than crashing the run
                report.stopped = "budget"
                report.notes.append(f"proposer budget exhausted: {exc}")
                break

            report.rounds_run += 1
            cid = self.ledger.next_id()
            parent = self.ledger.get(proposal.parent)
            if parent is None or parent.params is None:
                self.ledger.record(Candidate(
                    id=cid, parent=proposal.parent, hypothesis=proposal.hypothesis,
                    status="rejected",
                    rejected_reason=f"unknown or unevaluable parent {proposal.parent!r}",
                ))
                continue
            try:
                params = parent.params.with_delta(proposal.delta)
            except ValidationError as exc:
                self.ledger.record(Candidate(
                    id=cid, parent=parent.id, hypothesis=proposal.hypothesis,
                    status="rejected", rejected_reason=f"interface validation failed: {exc}",
                ))
                continue
            duplicate = next(
                (c for c in self.ledger.candidates() if c.params == params), None
            )
            if duplicate is not None:
                self.ledger.record(Candidate(
                    id=cid, parent=parent.id, hypothesis=proposal.hypothesis,
                    status="rejected", params=params,
                    rejected_reason=f"duplicate of already-tried {duplicate.id}",
                ))
                continue

            try:
                _, scores, traces = await self._evaluate(cid, params, self.search_tasks)
            except BudgetExceeded as exc:
                self.ledger.record(Candidate(
                    id=cid, parent=parent.id, hypothesis=proposal.hypothesis,
                    status="rejected", params=params,
                    rejected_reason=f"budget exhausted mid-evaluation: {exc}",
                ))
                report.stopped = "budget"
                report.notes.append(f"stopped: {exc}")
                break
            self.ledger.record(Candidate(
                id=cid, parent=parent.id, hypothesis=proposal.hypothesis,
                params=params, scores=scores,
            ), traces=traces)

            detector.record(self.ledger.best().scores.pass_hat_k)
            if detector.plateaued():
                report.stopped = "plateau"
                report.notes.append("search plateaued: best pass^k stopped improving")
                break

        report.best_id = self.ledger.best().id
        report.frontier = [c.id for c in self.ledger.frontier()]

        # promotion: final evaluation happens over the held-out PARETO FRONTIER
        # (the paper holds the test set out until final frontier evaluation),
        # never on search-set numbers or a greedy champion. Every contender is
        # judged on held-out data; promotable ones (paired gate GO AND a strict
        # held-out improvement — "no regression" alone never promotes) are then
        # ranked by HELD-OUT objectives, so search-set order can't pick the
        # winner.
        contenders = [c for c in self.ledger.frontier() if c.id != seed.id]
        if not contenders:
            report.notes.append("search found nothing better than the seed; nothing to promote")
            return report
        try:
            seed_suite, seed_holdout, _ = await self._evaluate(
                f"{seed.id}(holdout)", seed.params, self.holdout_tasks
            )
            # judge EVERY contender on held-out data first; search-set order
            # must never decide which promotable candidate wins.
            judged: list[tuple[Candidate, GateReport, CandidateScores, bool]] = []
            for contender in contenders:
                cand_suite, cand_holdout, _ = await self._evaluate(
                    f"{contender.id}(holdout)", contender.params, self.holdout_tasks
                )
                gate = compare_suites(seed_suite, cand_suite)
                improves = (
                    cand_holdout.pass_hat_k > seed_holdout.pass_hat_k
                    or (
                        cand_holdout.pass_hat_k == seed_holdout.pass_hat_k
                        and cand_holdout.tokens_total < seed_holdout.tokens_total
                    )
                )
                judged.append((contender, gate, cand_holdout, improves))
                if not (gate.go and improves):
                    reason = "gate said no-go" if not gate.go else (
                        "no strict held-out improvement (equal pass^k, no token win)"
                    )
                    report.notes.append(f"{contender.id} not promoted: {reason}")
        except BudgetExceeded as exc:
            report.stopped = "budget"
            report.notes.append(f"no promotion: budget exhausted during holdout gate: {exc}")
            return report

        promotable = [j for j in judged if j[1].go and j[3]]
        if promotable:
            winner, gate, _, _ = min(
                promotable, key=lambda j: (-j[2].pass_hat_k, j[2].tokens_total)
            )
            report.best_id = winner.id
            report.gate = gate
            report.notes.extend(gate.reasons)
            if self.auto_promote:
                self.ledger.promote(winner.id)
                report.promoted = True
            else:
                self.ledger.save_pending(winner.id, gate.model_dump(mode="json"))
                report.pending = winner.id
                report.notes.append(f"{winner.id} cleared the held-out gate — promotion awaits approval")
        elif judged:
            report.gate = judged[-1][1]
        return report
