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

import logging
import random
import time
from typing import Optional, Union

from pydantic import BaseModel

from metaharness.core.budget import Budget
from metaharness.core.types import Task, TaskType, Tier
from metaharness.harness.runner import Runner
from metaharness.identity.registry import WorkerRecord, WorkerRegistry
from metaharness.observability.tracing import tracer
from metaharness.routing.eligibility import (
    EligibilityResult,
    WorkerProfile,
    worker_eligibility,
)

_log = logging.getLogger(__name__)

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

    With `persist_path` set, observations are written through to disk — routing
    evidence is expensive to earn and must survive restarts. Persistence is
    best-effort and debounced: a disk error is recorded (never crashes the run)
    and per-observation rewrites coalesce within `persist_min_interval_s`; call
    `flush()` (e.g. on shutdown) to force any pending write out durably."""

    def __init__(
        self, smoothing: float = 4.0, persist_path=None,
        persist_min_interval_s: float = 1.0,
    ) -> None:
        self._stats: dict[tuple[str, TaskType], list[int]] = {}
        self.smoothing = smoothing
        self.persist_path = persist_path
        self.persist_min_interval_s = persist_min_interval_s
        # populated when the most recent write failed; a later success clears it.
        # `last_persist_error` carries a timestamp for display; `_last_persist_error_msg`
        # is the bare error string, compared for log-dedup (probe reviews 2026-07-09,
        # kimi: parsing a timestamped string with endswith is fragile).
        self.last_persist_error: Optional[str] = None
        self._last_persist_error_msg: Optional[str] = None
        # a torn/corrupt file at load time is a separate signal from a write
        # failure — it must survive the successful initial rewrite so health()
        # still reports it (probe reviews 2026-07-09, GLM/M2.7).
        self.last_load_error: Optional[str] = None
        self._dirty = False
        self._last_write: Optional[float] = None  # monotonic clock of last SUCCESSFUL write

    def record(self, model: str, task_type: TaskType, passed: bool) -> None:
        cell = self._stats.setdefault((model, task_type), [0, 0])
        cell[0] += int(passed)
        cell[1] += 1
        self._dirty = True
        # debounce: persist the first observation immediately (never written yet),
        # then coalesce a burst; interval 0 restores write-every-observation
        if self.persist_path is not None and (
            self._last_write is None
            or time.monotonic() - self._last_write >= self.persist_min_interval_s
        ):
            self.save(self.persist_path)

    def flush(self, force: bool = False) -> None:
        """Force any pending observation to disk now. Best-effort like save();
        callers use it where durability matters (shutdown). `force=True` writes
        even when nothing is dirty — used at initial wiring so matrix.json exists
        from that moment (probe reviews 2026-07-09, deepseek: the plain flush was a
        no-op on a fresh/loaded matrix, so the claimed initial durable write never
        happened)."""
        if self.persist_path is not None and (self._dirty or force):
            self.save(self.persist_path)

    def save(self, path) -> None:
        import json
        import os
        import tempfile
        from pathlib import Path

        data: dict[str, dict[str, list[int]]] = {}
        for (model, task_type), cell in self._stats.items():
            data.setdefault(model, {})[task_type.value] = list(cell)
        payload = json.dumps(data, indent=1, sort_keys=True)
        target = Path(path)
        # atomic write: a sibling temp file renamed over the target, so a crash
        # mid-write never leaves a torn matrix.json that would crash the next
        # load() (probe reviews 2026-07-09, GLM/kimi: write_text truncates in place).
        tmp_path: Optional[Path] = None
        try:
            fd, tmp_name = tempfile.mkstemp(
                dir=str(target.parent), prefix=".matrix.", suffix=".tmp"
            )
            os.close(fd)
            tmp_path = Path(tmp_name)
            tmp_path.write_text(payload, encoding="utf-8")
            os.replace(tmp_path, target)
        except OSError as exc:
            if tmp_path is not None:
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass
            # never crash a run on a persistence failure; warn once per distinct
            # error string (compared on the bare msg, not a timestamped prefix)
            msg = str(exc)
            if self._last_persist_error_msg != msg:
                _log.warning("capability matrix could not persist to %s: %s", target, exc)
            self._last_persist_error_msg = msg
            from metaharness.core.types import now
            self.last_persist_error = f"{now():.0f}: {msg}"
            # a failed write leaves _dirty set and does NOT advance _last_write, so
            # the next observation retries immediately instead of being suppressed
            # for a whole debounce interval (probe reviews 2026-07-09, kimi).
            return
        self._last_write = time.monotonic()
        self.last_persist_error = None
        self._last_persist_error_msg = None
        self._dirty = False

    def health(self) -> dict[str, Optional[str]]:
        """Persistence health for the dashboard/API: the last write/load error
        (with its timestamp) or None when the most recent write succeeded. A torn
        load at boot is reported even after the successful initial rewrite."""
        return {"last_persist_error": self.last_load_error or self.last_persist_error}

    @classmethod
    def load(cls, path, smoothing: float = 4.0) -> "CapabilityMatrix":
        import json
        from pathlib import Path

        matrix = cls(smoothing=smoothing)
        try:
            parsed = json.loads(Path(path).read_text(encoding="utf-8"))
            for model, cells in parsed.items():
                for task_type, (passed, total) in cells.items():
                    matrix._stats[(model, TaskType(task_type))] = [int(passed), int(total)]
        except (OSError, ValueError, TypeError) as exc:
            # a corrupt/torn matrix.json must not crash enable_persistence at boot
            # — start empty and surface the error via health(), never raise
            # (probe reviews 2026-07-09, GLM: JSONDecodeError is a ValueError, not
            # an OSError). Any partial stats gathered before the error are dropped.
            matrix._stats.clear()
            _log.warning("capability matrix could not load from %s: %s; starting empty",
                         path, exc)
            from metaharness.core.types import now
            matrix.last_load_error = f"{now():.0f}: load failed: {exc}"
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
    explored: bool = False


class Router:
    """Cheapest-capable routing with evidence-informed escalation.

    Each tier holds a POOL of runners (configured order preserved). Tier
    selection/escalation is unchanged; within the chosen tier, decide() picks the
    pool member with the best matrix pass rate for the task type. On verifiable
    tasks, ε-exploration occasionally routes to a benched member so it earns the
    evidence that would let it win the slot on merit."""

    def __init__(
        self,
        runners: dict[Tier, Union[Runner, list[Runner]]],
        matrix: Optional[CapabilityMatrix] = None,
        threshold: float = 0.7,
        priors: Optional[dict[Tier, dict[TaskType, float]]] = None,
        explore_rate: float = 0.1,
        rng: Optional[random.Random] = None,
        registry: Optional[WorkerRegistry] = None,
    ) -> None:
        # normalize each value to a list; drop empty pools so `tier in self.pools`
        # always means "serves traffic" (next_tier/decide rely on that invariant)
        self.pools: dict[Tier, list[Runner]] = {}
        for tier, value in runners.items():
            members = list(value) if isinstance(value, list) else [value]
            members = [m for m in members if m is not None]
            if members:
                self.pools[tier] = members
        if not self.pools:
            raise ValueError("router needs at least one runner")
        self.matrix = matrix or CapabilityMatrix()
        self.threshold = threshold
        self.priors = priors or DEFAULT_PRIORS
        self.explore_rate = explore_rate
        self.rng = rng or random.Random()
        self.registry = registry
        # in-memory routed-to evidence, keyed (tier, worker_id) — feeds the UI
        self.route_counts: dict[tuple[Tier, str], int] = {}

    def pool(self, tier: Tier) -> list[Runner]:
        return self.pools.get(tier, [])

    def _profile(self, runner: Runner, tier: Tier) -> WorkerProfile:
        record: Optional[WorkerRecord] = (
            self.registry.get(runner.worker_id) if self.registry is not None else None
        )
        # Standalone Router users historically have no registry; preserve that
        # unrestricted mode. Once a registry is supplied, an absent record is a
        # missing/inactive identity and must not be routed.
        if record is None:
            return WorkerProfile(
                worker_id=runner.worker_id,
                tier=tier,
                active=self.registry is None,
                host=str(getattr(runner, "cli", "") or ""),
            )
        return WorkerProfile(
            worker_id=runner.worker_id,
            tier=tier,
            active=record.active,
            roles=tuple(record.roles),
            capabilities=tuple(record.capabilities),
            task_types=tuple(record.task_types),
            tiers=tuple(record.tiers),
            host=record.host or str(getattr(runner, "cli", "") or ""),
        )

    def eligibility(self, runner: Runner, tier: Tier, task: Task) -> EligibilityResult:
        return worker_eligibility(task, self._profile(runner, tier))

    def eligible_members(self, tier: Tier, task: Task) -> list[Runner]:
        """Pool members accepted by the shared readiness/routing predicate."""
        return [
            member for member in self.pools.get(tier, [])
            if self.eligibility(member, tier, task).eligible
        ]

    def expected_pass_rate(self, tier: Tier, task_type: TaskType | Task) -> float:
        """The tier's ceiling: the best pass rate any member can offer — that is
        the member decide() would route to (absent exploration)."""
        task = task_type if isinstance(task_type, Task) else Task(task_type=task_type)
        members = self.eligible_members(tier, task)
        if not members:
            return 0.0
        task_type = task.task_type
        prior = self.priors.get(tier, {}).get(task_type, 0.5)
        return max(self.matrix.pass_rate(m.model, task_type, prior=prior) for m in members)

    def pick_member(self, tier: Tier, task: Task) -> tuple[Runner, bool]:
        """Choose which pool member serves this task. Best = argmax pass rate for
        the task type (tie → earliest configured). With >1 member on a verifiable
        task, ε of the time route instead to the least-sampled other member so it
        earns evidence (returns explored=True)."""
        members = self.eligible_members(tier, task)
        if not members:
            raise ValueError(f"no eligible runner serves tier {tier.value}")
        prior = self.priors.get(tier, {}).get(task.task_type, 0.5)
        best = max(members, key=lambda m: self.matrix.pass_rate(m.model, task.task_type, prior=prior))
        # success_check only: the deterministic verifier can PASS solely through
        # a success_check branch — an output_schema alone yields FAIL or
        # UNVERIFIED, so exploring there banks downside evidence and no upside
        verifiable = bool(task.success_check)
        if len(members) > 1 and verifiable and self.rng.random() < self.explore_rate:
            others = [m for m in members if m is not best]
            if not others:  # same runner object pooled twice: nothing to explore
                return best, False
            explore = min(others, key=lambda m: self.matrix.samples(m.model, task.task_type))
            return explore, True
        return best, False

    def runner_for(self, decision: RoutingDecision) -> Runner:
        """Resolve the exact decided member; never silently substitute another."""
        members = self.pools.get(decision.tier, [])
        for member in members:
            if member.worker_id == decision.worker_id:
                return member
        raise ValueError(
            f"decided worker {decision.worker_id!r} is no longer available in "
            f"tier {decision.tier.value}"
        )

    def next_tier(self, current: Tier) -> Optional[Tier]:
        """The next tier up whose pool actually serves, or None at the top."""
        idx = TIER_ORDER.index(current)
        for tier in TIER_ORDER[idx + 1:]:
            if self.pool(tier):
                return tier
        return None

    def route_evidence(self) -> dict[str, dict[str, int]]:
        """{tier: {worker_id: times routed}} — in-memory routing tallies."""
        out: dict[str, dict[str, int]] = {}
        for (tier, worker_id), count in self.route_counts.items():
            out.setdefault(tier.value, {})[worker_id] = count
        return out

    def _build_decision(
        self, task: Task, tier: Tier, span, cleared: bool
    ) -> RoutingDecision:
        # the tier was selected on its BEST member's rate; the decision reports
        # the SERVED member's own rate/samples — under exploration they differ
        member, explored = self.pick_member(tier, task)
        prior = self.priors.get(tier, {}).get(task.task_type, 0.5)
        rate = self.matrix.pass_rate(member.model, task.task_type, prior=prior)
        samples = self.matrix.samples(member.model, task.task_type)
        if cleared:
            reason = f"cheapest tier clearing threshold {self.threshold} on its best member"
        else:
            reason = f"no tier cleared threshold {self.threshold}; using most capable available"
        if explored:
            reason += (f"; exploring {member.worker_id} "
                       f"(expected {rate:.2f}, samples={samples}) to earn evidence")
        else:
            reason += f" (expected {rate:.2f}, samples={samples})"
        span.set_attribute("router.tier", tier.value)
        span.set_attribute("router.expected_pass_rate", rate)
        span.set_attribute("router.explored", explored)
        key = (tier, member.worker_id)
        self.route_counts[key] = self.route_counts.get(key, 0) + 1
        return RoutingDecision(
            tier=tier,
            worker_id=member.worker_id,
            model=member.model,
            expected_pass_rate=rate,
            reason=reason,
            explored=explored,
        )

    def decide(
        self,
        task: Task,
        exclude: Optional[set[Tier]] = None,
        budget: Optional[Budget] = None,
    ) -> RoutingDecision:
        """Pick the cheapest available tier whose expected pass rate clears the
        threshold; fall back to the most capable affordable tier otherwise. Then
        pick which member of that tier serves the task."""
        exclude = exclude or set()
        if task.worker_id:
            matches = [
                (tier, member)
                for tier, members in self.pools.items()
                for member in members
                if member.worker_id == task.worker_id and tier not in exclude
            ]
            if not matches:
                raise ValueError(f"pinned worker {task.worker_id!r} is not available")
            eligible = [
                (tier, member)
                for tier, member in matches
                if self.eligibility(member, tier, task).eligible
            ]
            if not eligible:
                tier, member = matches[0]
                eligibility = self.eligibility(member, tier, task)
                raise ValueError(
                    f"pinned worker {task.worker_id!r} is not eligible: "
                    f"{eligibility.code}: {eligibility.detail}"
                )
            if len(eligible) > 1:
                raise ValueError(
                    f"pinned worker {task.worker_id!r} appears in multiple eligible pools"
                )
            tier, member = eligible[0]
            with tracer().start_as_current_span("router.decide") as span:
                span.set_attribute("task.id", task.id)
                span.set_attribute("task.type", task.task_type.value)
                prior = self.priors.get(tier, {}).get(task.task_type, 0.5)
                rate = self.matrix.pass_rate(member.model, task.task_type, prior=prior)
                key = (tier, member.worker_id)
                self.route_counts[key] = self.route_counts.get(key, 0) + 1
                return RoutingDecision(
                    tier=tier,
                    worker_id=member.worker_id,
                    model=member.model,
                    expected_pass_rate=rate,
                    reason=f"hard-pinned worker {member.worker_id}",
                )
        floor_idx = TIER_ORDER.index(task.tier_hint) if task.tier_hint else 0
        candidates = [
            t for t in TIER_ORDER[floor_idx:]
            if t in self.pools and t not in exclude and self.eligible_members(t, task)
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
                # tier selection keys on the tier's ceiling (best member's rate)
                if self.expected_pass_rate(tier, task) >= self.threshold:
                    return self._build_decision(task, tier, span, cleared=True)
            # nothing clears the bar — send the most capable candidate
            return self._build_decision(task, candidates[-1], span, cleared=False)
