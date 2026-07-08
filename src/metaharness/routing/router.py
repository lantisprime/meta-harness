"""Model router: send each task to the cheapest tier likely to succeed, and
escalate on a verifiable failure signal — never on vibes.

Two inputs drive the decision:
1. Priors — what tier is *expected* to handle each task type (cold start).
2. The capability matrix — observed pass rates per (model, task type), fed by
   the eval harness and by live verified outcomes. Evidence beats priors as
   samples accumulate.

The cascade discipline (from the routing literature): route down when the
failure signal is checkable, because a wrong answer you can't detect at a cheap
tier silently becomes an expensive downstream failure.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from metaharness.core.budget import Budget
from metaharness.core.types import Task, TaskType, Tier
from metaharness.harness.runner import Runner
from metaharness.observability.tracing import tracer

TIER_ORDER = [Tier.SMALL, Tier.MID, Tier.FRONTIER]

# rough $ per task by tier, for budget-aware filtering (refined by live data)
TIER_EST_COST = {Tier.SMALL: 0.001, Tier.MID: 0.01, Tier.FRONTIER: 0.05}

# cold-start priors: expected pass rate per (tier, task type) before evidence
DEFAULT_PRIORS: dict[Tier, dict[TaskType, float]] = {
    Tier.SMALL: {
        TaskType.CLASSIFY: 0.85, TaskType.EXTRACT: 0.80, TaskType.SUMMARIZE: 0.75,
        TaskType.TRANSFORM: 0.70, TaskType.ARITHMETIC: 0.80,  # arithmetic is tool-offloaded
        TaskType.CODE_EDIT: 0.45, TaskType.REASONING: 0.40, TaskType.PLANNING: 0.30,
        TaskType.GENERAL: 0.55,
    },
    Tier.MID: {
        TaskType.CLASSIFY: 0.95, TaskType.EXTRACT: 0.93, TaskType.SUMMARIZE: 0.90,
        TaskType.TRANSFORM: 0.88, TaskType.ARITHMETIC: 0.92, TaskType.CODE_EDIT: 0.78,
        TaskType.REASONING: 0.72, TaskType.PLANNING: 0.65, TaskType.GENERAL: 0.80,
    },
    Tier.FRONTIER: {
        TaskType.CLASSIFY: 0.99, TaskType.EXTRACT: 0.98, TaskType.SUMMARIZE: 0.97,
        TaskType.TRANSFORM: 0.96, TaskType.ARITHMETIC: 0.97, TaskType.CODE_EDIT: 0.93,
        TaskType.REASONING: 0.92, TaskType.PLANNING: 0.90, TaskType.GENERAL: 0.94,
    },
}


class CapabilityMatrix:
    """Observed pass rates per (model, task type), Laplace-smoothed toward the
    prior so a single unlucky sample doesn't flip routing.

    With `persist_path` set, every observation is written through to disk —
    routing evidence is expensive to earn and must survive restarts."""

    def __init__(self, smoothing: float = 4.0, persist_path=None) -> None:
        self._stats: dict[tuple[str, TaskType], list[int]] = {}
        self.smoothing = smoothing
        self.persist_path = persist_path

    def record(self, model: str, task_type: TaskType, passed: bool) -> None:
        cell = self._stats.setdefault((model, task_type), [0, 0])
        cell[0] += int(passed)
        cell[1] += 1
        if self.persist_path is not None:
            self.save(self.persist_path)

    def save(self, path) -> None:
        import json
        from pathlib import Path

        data: dict[str, dict[str, list[int]]] = {}
        for (model, task_type), cell in self._stats.items():
            data.setdefault(model, {})[task_type.value] = list(cell)
        Path(path).write_text(json.dumps(data, indent=1, sort_keys=True), encoding="utf-8")

    @classmethod
    def load(cls, path, smoothing: float = 4.0) -> "CapabilityMatrix":
        import json
        from pathlib import Path

        matrix = cls(smoothing=smoothing)
        for model, cells in json.loads(Path(path).read_text(encoding="utf-8")).items():
            for task_type, (passed, total) in cells.items():
                matrix._stats[(model, TaskType(task_type))] = [int(passed), int(total)]
        return matrix

    def samples(self, model: str, task_type: TaskType) -> int:
        return self._stats.get((model, task_type), [0, 0])[1]

    def pass_rate(self, model: str, task_type: TaskType, prior: float = 0.5) -> float:
        passed, total = self._stats.get((model, task_type), (0, 0))
        return (passed + self.smoothing * prior) / (total + self.smoothing)

    def as_dict(self) -> dict[str, dict[str, dict[str, float]]]:
        """{model: {task_type: {rate, samples}}} for the WebUI."""
        out: dict[str, dict[str, dict[str, float]]] = {}
        for (model, task_type), (passed, total) in sorted(self._stats.items()):
            out.setdefault(model, {})[task_type.value] = {
                "pass_rate": passed / total if total else 0.0,
                "samples": total,
            }
        return out


class RoutingDecision(BaseModel):
    tier: Tier
    worker_id: str
    model: str
    expected_pass_rate: float
    reason: str


class Router:
    """Cheapest-capable routing with evidence-informed escalation."""

    def __init__(
        self,
        runners: dict[Tier, Runner],
        matrix: Optional[CapabilityMatrix] = None,
        threshold: float = 0.7,
        priors: Optional[dict[Tier, dict[TaskType, float]]] = None,
    ) -> None:
        if not runners:
            raise ValueError("router needs at least one runner")
        self.runners = runners
        self.matrix = matrix or CapabilityMatrix()
        self.threshold = threshold
        self.priors = priors or DEFAULT_PRIORS

    def expected_pass_rate(self, tier: Tier, task_type: TaskType) -> float:
        runner = self.runners.get(tier)
        if runner is None:
            return 0.0
        prior = self.priors.get(tier, {}).get(task_type, 0.5)
        return self.matrix.pass_rate(runner.model, task_type, prior=prior)

    def next_tier(self, current: Tier) -> Optional[Tier]:
        """The next tier up that actually has a runner, or None at the top."""
        idx = TIER_ORDER.index(current)
        for tier in TIER_ORDER[idx + 1:]:
            if tier in self.runners:
                return tier
        return None

    def decide(
        self,
        task: Task,
        exclude: Optional[set[Tier]] = None,
        budget: Optional[Budget] = None,
    ) -> RoutingDecision:
        """Pick the cheapest available tier whose expected pass rate clears the
        threshold; fall back to the most capable affordable tier otherwise."""
        exclude = exclude or set()
        floor_idx = TIER_ORDER.index(task.tier_hint) if task.tier_hint else 0
        candidates = [
            t for t in TIER_ORDER[floor_idx:]
            if t in self.runners and t not in exclude
        ]
        if budget is not None:
            affordable = [t for t in candidates if TIER_EST_COST[t] <= budget.remaining_cost()]
            candidates = affordable or candidates[:1]  # never route to nothing
        if not candidates:
            raise ValueError("no runner available after exclusions")

        with tracer().start_as_current_span("router.decide") as span:
            span.set_attribute("task.id", task.id)
            span.set_attribute("task.type", task.task_type.value)
            for tier in candidates:
                rate = self.expected_pass_rate(tier, task.task_type)
                if rate >= self.threshold:
                    runner = self.runners[tier]
                    span.set_attribute("router.tier", tier.value)
                    span.set_attribute("router.expected_pass_rate", rate)
                    return RoutingDecision(
                        tier=tier,
                        worker_id=runner.worker_id,
                        model=runner.model,
                        expected_pass_rate=rate,
                        reason=f"cheapest tier clearing threshold {self.threshold} "
                               f"(expected {rate:.2f}, samples={self.matrix.samples(runner.model, task.task_type)})",
                    )
            # nothing clears the bar — send the most capable candidate
            tier = candidates[-1]
            runner = self.runners[tier]
            rate = self.expected_pass_rate(tier, task.task_type)
            span.set_attribute("router.tier", tier.value)
            span.set_attribute("router.expected_pass_rate", rate)
            return RoutingDecision(
                tier=tier,
                worker_id=runner.worker_id,
                model=runner.model,
                expected_pass_rate=rate,
                reason=f"no tier cleared threshold {self.threshold}; using most capable available",
            )
