"""Budgets and loop guardrails.

Most agent failures are harness-design failures (MAST): runaway loops, no plateau
detection, no termination check. This module is the single place those guards live,
so the orchestrator can't accidentally skip one.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


class BudgetExceeded(Exception):
    """Raised when a run hits a hard ceiling. Callers stop and report state."""


@dataclass
class Budget:
    """A hard ceiling on a run. `total_*` is the pool; `spent_*` accumulates.

    The token target is a *hard* ceiling, not advisory — once spent reaches total,
    further work raises BudgetExceeded rather than silently overrunning.

    A cap-less Budget (every ``max_*`` left None) is a pure ACCUMULATOR: it tallies
    spend and never raises. That is deliberate — the web harness always wires one so
    accounting is on by default (F1, panel 2026-07-09), and real caps are opt-in.
    """

    max_cost_usd: Optional[float] = None
    max_tokens: Optional[int] = None
    max_wall_s: Optional[float] = None
    spent_cost_usd: float = 0.0
    spent_tokens: int = 0
    spent_wall_s: float = 0.0

    def charge(self, cost_usd: float = 0.0, tokens: int = 0, wall_s: float = 0.0) -> None:
        self.spent_cost_usd += cost_usd
        self.spent_tokens += tokens
        self.spent_wall_s += wall_s
        self.check()

    def check(self) -> None:
        # cap-less means unbounded: with every max_* None, nothing here fires, so
        # a default Budget accumulates forever without ever raising.
        if self.max_cost_usd is not None and self.spent_cost_usd > self.max_cost_usd:
            raise BudgetExceeded(
                f"cost {self.spent_cost_usd:.4f} > cap {self.max_cost_usd:.4f}"
            )
        if self.max_tokens is not None and self.spent_tokens > self.max_tokens:
            raise BudgetExceeded(
                f"tokens {self.spent_tokens} > cap {self.max_tokens}"
            )
        # wall-clock here is the SUM of charged worker + verifier latency, not
        # elapsed session time — deterministic and already inclusive of
        # SchemaGuard retry latency (enrichment.py folds retries into the final
        # result before it is charged). Orchestration overhead between calls is
        # not counted; see core/executor.py and friends for the charge sites.
        if self.max_wall_s is not None and self.spent_wall_s > self.max_wall_s:
            raise BudgetExceeded(
                f"wall time {self.spent_wall_s:.2f}s > cap {self.max_wall_s:.2f}s"
            )

    def remaining_cost(self) -> float:
        if self.max_cost_usd is None:
            return float("inf")
        return max(0.0, self.max_cost_usd - self.spent_cost_usd)


@dataclass
class PlateauDetector:
    """Detects when iterating is no longer paying off.

    Research consensus: reflection gains die after 1-2 rounds. We stop early when
    scores stop improving. Heuristic (from the guardrails literature): if the recent
    average is both low and not improving over the earlier window, escalate/stop
    rather than burn more iterations.
    """

    window: int = 3
    min_delta: float = 0.02          # improvement smaller than this = no progress
    scores: list[float] = field(default_factory=list)

    def record(self, score: float) -> None:
        self.scores.append(score)

    def plateaued(self) -> bool:
        if len(self.scores) < self.window + 1:
            return False
        recent = self.scores[-self.window:]
        prior = self.scores[-(self.window + 1)]
        best_recent = max(recent)
        return (best_recent - prior) < self.min_delta

    def regressing(self) -> bool:
        """Recent scores are trending down — a signal to escalate the tier."""
        if len(self.scores) < 2:
            return False
        return self.scores[-1] < self.scores[-2]


def action_signature(result_output: object) -> str:
    """A cheap fingerprint of an output, for step-repetition detection.

    MAST flags 'repeating steps' as a top failure mode; detect it by deduping action
    signatures across the trajectory rather than only counting iterations.
    """
    import hashlib
    import json

    try:
        payload = json.dumps(result_output, sort_keys=True, default=str)
    except (TypeError, ValueError):
        payload = str(result_output)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]
