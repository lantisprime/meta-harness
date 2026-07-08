"""Eval gating for model swaps: pass^k, paired comparison, go/no-go.

The question this module answers: "can the candidate model replace the incumbent
on this workload?" — answered with reliability math, not vibes:

- **pass^k** (all k of k attempts pass) is the reliability metric. Agents retry
  and compound, so a model that's right 80% of the time is wrong somewhere in
  almost every 5-step workflow. pass@1 flatters; pass^k tells the truth.
- **Paired comparison** on identical tasks, with an exact sign test — the same
  task list goes to both models, so task difficulty cancels out.
- **Per-task-type gating**: a candidate that wins on average but regresses badly
  on one task type fails the gate; the router routes by task type, so a hidden
  per-type regression is a production incident waiting.

Every verified eval attempt also feeds the capability matrix, so routing priors
improve as a side effect of gating.
"""
from __future__ import annotations

import math
from typing import Optional

from pydantic import BaseModel, Field

from metaharness.core.types import Task, Verdict
from metaharness.evals.verifiers import verify_output
from metaharness.harness.runner import Runner
from metaharness.observability.tracing import tracer
from metaharness.routing.router import CapabilityMatrix


class EvalTaskResult(BaseModel):
    task_id: str
    task_type: str
    passes: list[bool]

    @property
    def pass_all(self) -> bool:
        return bool(self.passes) and all(self.passes)

    @property
    def pass_rate(self) -> float:
        return sum(self.passes) / len(self.passes) if self.passes else 0.0


class TypeSummary(BaseModel):
    tasks: int
    pass_hat_k: float          # fraction of tasks passing all k attempts
    pass_at_1: float           # mean single-attempt pass rate


class SuiteResult(BaseModel):
    model: str
    k: int
    results: list[EvalTaskResult] = Field(default_factory=list)

    def by_type(self) -> dict[str, TypeSummary]:
        grouped: dict[str, list[EvalTaskResult]] = {}
        for r in self.results:
            grouped.setdefault(r.task_type, []).append(r)
        return {
            t: TypeSummary(
                tasks=len(rs),
                pass_hat_k=sum(r.pass_all for r in rs) / len(rs),
                pass_at_1=sum(r.pass_rate for r in rs) / len(rs),
            )
            for t, rs in sorted(grouped.items())
        }

    def overall_pass_hat_k(self) -> float:
        if not self.results:
            return 0.0
        return sum(r.pass_all for r in self.results) / len(self.results)


async def run_suite(
    runner: Runner,
    tasks: list[Task],
    k: int = 4,
    matrix: Optional[CapabilityMatrix] = None,
) -> SuiteResult:
    """Run every task k times through the runner and verify deterministically.
    Tasks without a checkable success signal are rejected up front — an eval you
    can't score isn't an eval."""
    unscoreable = [t.id for t in tasks if not t.success_check and not t.output_schema]
    if unscoreable:
        raise ValueError(f"tasks with no checkable signal: {unscoreable}")
    suite = SuiteResult(model=runner.model, k=k)
    with tracer().start_as_current_span("eval.suite") as span:
        span.set_attribute("eval.model", runner.model)
        span.set_attribute("eval.k", k)
        span.set_attribute("eval.tasks", len(tasks))
        for task in tasks:
            passes: list[bool] = []
            for _ in range(k):
                result = await runner.run(task)
                verdict = verify_output(task, result).verdict
                passed = verdict == Verdict.PASS
                passes.append(passed)
                if matrix is not None and verdict in (Verdict.PASS, Verdict.FAIL):
                    matrix.record(runner.model, task.task_type, passed)
            suite.results.append(
                EvalTaskResult(task_id=task.id, task_type=task.task_type.value, passes=passes)
            )
        span.set_attribute("eval.pass_hat_k", suite.overall_pass_hat_k())
    return suite


def sign_test_p(wins: int, losses: int) -> float:
    """Exact two-sided sign test p-value: probability of a split at least this
    lopsided under 'both models are equal' (ties excluded)."""
    n = wins + losses
    if n == 0:
        return 1.0
    extreme = max(wins, losses)
    tail = sum(math.comb(n, i) for i in range(extreme, n + 1)) / 2**n
    return min(1.0, 2 * tail)


class TypeDelta(BaseModel):
    task_type: str
    incumbent: float
    candidate: float

    @property
    def delta(self) -> float:
        return self.candidate - self.incumbent


class GateReport(BaseModel):
    incumbent_model: str
    candidate_model: str
    k: int
    go: bool
    reasons: list[str] = Field(default_factory=list)
    deltas: list[TypeDelta] = Field(default_factory=list)
    wins: int = 0
    losses: int = 0
    ties: int = 0
    p_value: float = 1.0
    overall_incumbent: float = 0.0
    overall_candidate: float = 0.0


def compare_suites(
    incumbent: SuiteResult,
    candidate: SuiteResult,
    max_type_regression: float = 0.05,
    min_tasks_per_type: int = 3,
) -> GateReport:
    """Go/no-go verdict for swapping incumbent → candidate.

    Gate rules (all must hold for GO):
    1. No task type regresses in pass^k by more than `max_type_regression`.
    2. Overall pass^k does not regress.
    3. If the paired sign test says the candidate is significantly WORSE, no-go
       regardless of aggregates.
    Thin per-type samples are called out rather than silently trusted.
    """
    if incumbent.k != candidate.k:
        raise ValueError("suites must use the same k for a fair comparison")
    inc_by_task = {r.task_id: r for r in incumbent.results}
    cand_by_task = {r.task_id: r for r in candidate.results}
    if set(inc_by_task) != set(cand_by_task):
        raise ValueError("suites must cover the identical task list (paired design)")

    report = GateReport(
        incumbent_model=incumbent.model,
        candidate_model=candidate.model,
        k=incumbent.k,
        go=True,
        overall_incumbent=incumbent.overall_pass_hat_k(),
        overall_candidate=candidate.overall_pass_hat_k(),
    )

    # paired per-task comparison on pass^k
    for task_id, inc in inc_by_task.items():
        cand = cand_by_task[task_id]
        if cand.pass_all == inc.pass_all:
            report.ties += 1
        elif cand.pass_all:
            report.wins += 1
        else:
            report.losses += 1
    report.p_value = sign_test_p(report.wins, report.losses)

    inc_types = incumbent.by_type()
    cand_types = candidate.by_type()
    for task_type, inc_summary in inc_types.items():
        cand_summary = cand_types[task_type]
        delta = TypeDelta(
            task_type=task_type,
            incumbent=inc_summary.pass_hat_k,
            candidate=cand_summary.pass_hat_k,
        )
        report.deltas.append(delta)
        if inc_summary.tasks < min_tasks_per_type:
            report.reasons.append(
                f"{task_type}: only {inc_summary.tasks} tasks — too thin to trust; add coverage"
            )
        if delta.delta < -max_type_regression:
            report.go = False
            report.reasons.append(
                f"{task_type}: pass^{report.k} regressed "
                f"{inc_summary.pass_hat_k:.2f} → {cand_summary.pass_hat_k:.2f} "
                f"(limit {max_type_regression:.2f})"
            )

    if report.overall_candidate < report.overall_incumbent:
        report.go = False
        report.reasons.append(
            f"overall pass^{report.k} regressed "
            f"{report.overall_incumbent:.2f} → {report.overall_candidate:.2f}"
        )
    if report.losses > report.wins and report.p_value < 0.05:
        report.go = False
        report.reasons.append(
            f"paired sign test: candidate significantly worse "
            f"({report.wins}W/{report.losses}L/{report.ties}T, p={report.p_value:.4f})"
        )
    if report.go and not report.reasons:
        report.reasons.append("no regressions; candidate is at least as reliable per type and overall")
    return report
