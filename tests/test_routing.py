"""Router tests: cheapest-capable choice, evidence shifting routes, tier hints,
escalation order, budget-aware filtering."""
from __future__ import annotations

import pytest

from metaharness.core.budget import Budget
from metaharness.core.types import Task, TaskType, Tier
from metaharness.harness import MockLLMWorker
from metaharness.routing import CapabilityMatrix, Router


def make_router(threshold: float = 0.7, matrix: CapabilityMatrix | None = None) -> Router:
    runners = {
        Tier.SMALL: MockLLMWorker("w-small", Tier.SMALL, seed=1),
        Tier.MID: MockLLMWorker("w-mid", Tier.MID, seed=2),
        Tier.FRONTIER: MockLLMWorker("w-front", Tier.FRONTIER, seed=3),
    }
    return Router(runners, matrix=matrix, threshold=threshold)


def test_easy_task_routes_small():
    router = make_router()
    decision = router.decide(Task(task_type=TaskType.CLASSIFY))
    assert decision.tier == Tier.SMALL
    assert "cheapest tier" in decision.reason


def test_hard_task_routes_up():
    router = make_router()
    decision = router.decide(Task(task_type=TaskType.PLANNING))
    assert decision.tier == Tier.FRONTIER  # priors: small 0.30, mid 0.65 < 0.7


def test_reasoning_routes_mid():
    router = make_router()
    decision = router.decide(Task(task_type=TaskType.REASONING))
    assert decision.tier == Tier.FRONTIER or decision.tier == Tier.MID
    # priors: small 0.40 < 0.7, mid 0.72 >= 0.7 → mid
    assert decision.tier == Tier.MID


def test_evidence_overrides_optimistic_prior():
    """Observed failures at the small tier push classification traffic up."""
    matrix = CapabilityMatrix(smoothing=2.0)
    for _ in range(20):
        matrix.record("mock-small", TaskType.CLASSIFY, passed=False)
    router = make_router(matrix=matrix)
    decision = router.decide(Task(task_type=TaskType.CLASSIFY))
    assert decision.tier != Tier.SMALL


def test_evidence_earns_downroute():
    """Strong observed performance at small tier keeps hard traffic there."""
    matrix = CapabilityMatrix(smoothing=2.0)
    for _ in range(50):
        matrix.record("mock-small", TaskType.REASONING, passed=True)
    router = make_router(matrix=matrix)
    decision = router.decide(Task(task_type=TaskType.REASONING))
    assert decision.tier == Tier.SMALL


def test_tier_hint_is_a_floor():
    router = make_router()
    decision = router.decide(Task(task_type=TaskType.CLASSIFY, tier_hint=Tier.MID))
    assert decision.tier == Tier.MID


def test_exclusions_and_next_tier():
    router = make_router()
    decision = router.decide(Task(task_type=TaskType.CLASSIFY), exclude={Tier.SMALL})
    assert decision.tier == Tier.MID
    assert router.next_tier(Tier.SMALL) == Tier.MID
    assert router.next_tier(Tier.MID) == Tier.FRONTIER
    assert router.next_tier(Tier.FRONTIER) is None


def test_next_tier_skips_missing_runner():
    router = Router({
        Tier.SMALL: MockLLMWorker("w-small", Tier.SMALL),
        Tier.FRONTIER: MockLLMWorker("w-front", Tier.FRONTIER),
    })
    assert router.next_tier(Tier.SMALL) == Tier.FRONTIER


def test_budget_filters_expensive_tiers():
    router = make_router()
    tight = Budget(max_cost_usd=0.005)  # can afford small (0.001), not mid/frontier
    decision = router.decide(Task(task_type=TaskType.PLANNING), budget=tight)
    assert decision.tier == Tier.SMALL


def test_no_runner_after_exclusions_raises():
    router = Router({Tier.SMALL: MockLLMWorker("w", Tier.SMALL)})
    with pytest.raises(ValueError):
        router.decide(Task(), exclude={Tier.SMALL})


def test_matrix_as_dict_shape():
    matrix = CapabilityMatrix()
    matrix.record("mock-small", TaskType.CLASSIFY, True)
    matrix.record("mock-small", TaskType.CLASSIFY, False)
    d = matrix.as_dict()
    cell = d["mock-small"]["classify"]
    assert cell["samples"] == 2 and cell["pass_rate"] == 0.5
