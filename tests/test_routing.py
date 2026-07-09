"""Router tests: cheapest-capable choice, evidence shifting routes, tier hints,
escalation order, budget-aware filtering."""
from __future__ import annotations

import pytest

from metaharness.core.budget import Budget
from metaharness.core.types import Task, TaskType, Tier
from metaharness.harness import MockLLMWorker
from metaharness.routing import DEFAULT_PRIORS, CapabilityMatrix, Router, RoutingDecision


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


def test_matrix_persist_is_best_effort_on_disk_error(tmp_path, monkeypatch):
    """GLM F3 (2026-07-09): a sync, unwrapped write_text on every observation
    crashed runs whenever the disk erred. save() is now best-effort — a failing
    write records last_persist_error and never raises, in-memory stats keep
    updating, and a later successful write clears the error."""
    import pathlib

    path = tmp_path / "matrix.json"
    matrix = CapabilityMatrix(persist_path=path, persist_min_interval_s=0.0)

    def boom(self, *a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(pathlib.Path, "write_text", boom)
    matrix.record("m", TaskType.CLASSIFY, passed=True)  # must NOT raise
    assert matrix.samples("m", TaskType.CLASSIFY) == 1  # in-memory stat still updated
    assert matrix.last_persist_error is not None and "disk full" in matrix.last_persist_error
    assert matrix.health()["last_persist_error"] == matrix.last_persist_error

    monkeypatch.undo()  # disk recovers
    matrix.record("m", TaskType.CLASSIFY, passed=False)
    assert matrix.last_persist_error is None  # a good write clears the flag
    assert path.exists()


def test_matrix_debounces_writes_and_flush_forces_pending(tmp_path, monkeypatch):
    """A large debounce interval coalesces a burst of observations into a single
    write; flush() forces the deferred write out durably (e.g. on shutdown)."""
    import pathlib

    writes: list[int] = []
    real = pathlib.Path.write_text

    def counting(self, data, *a, **k):
        writes.append(1)
        return real(self, data, *a, **k)

    monkeypatch.setattr(pathlib.Path, "write_text", counting)
    path = tmp_path / "matrix.json"
    matrix = CapabilityMatrix(persist_path=path, persist_min_interval_s=1000.0)
    for _ in range(5):
        matrix.record("m", TaskType.CLASSIFY, passed=True)
    assert len(writes) == 1  # only the first observation wrote; the rest debounced

    matrix.flush()
    assert len(writes) == 2  # flush forced the pending burst out
    assert CapabilityMatrix.load(path).samples("m", TaskType.CLASSIFY) == 5

    matrix.flush()  # nothing pending -> no-op
    assert len(writes) == 2


def test_matrix_load_tolerates_torn_file(tmp_path):
    """probe reviews 2026-07-09 (GLM/deepseek): a torn/corrupt matrix.json must not
    crash load() — JSONDecodeError is a ValueError, not an OSError. Start empty,
    surface the error via health(), never raise."""
    path = tmp_path / "matrix.json"
    path.write_text('{"mock-small": {"classi', encoding="utf-8")   # truncated mid-write
    matrix = CapabilityMatrix.load(path)                           # must not raise
    assert matrix.samples("mock-small", TaskType.CLASSIFY) == 0    # started empty
    assert "load failed" in (matrix.health()["last_persist_error"] or "")


def test_matrix_failed_write_recovers_on_next_record_without_debounce_suppression(
    tmp_path, monkeypatch
):
    """probe reviews 2026-07-09 (kimi): a failed write must NOT advance the debounce
    clock, so the very next observation retries immediately instead of being
    suppressed for a full interval. The write is also atomic (temp + os.replace)."""
    import pathlib

    path = tmp_path / "matrix.json"
    matrix = CapabilityMatrix(persist_path=path, persist_min_interval_s=1000.0)

    calls = {"n": 0}
    real = pathlib.Path.write_text

    def flaky(self, data, *a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("disk full")                  # first write fails
        return real(self, data, *a, **k)

    monkeypatch.setattr(pathlib.Path, "write_text", flaky)
    matrix.record("m", TaskType.CLASSIFY, passed=True)  # attempt 1 fails
    assert not path.exists()
    assert matrix.last_persist_error is not None
    # the NEXT record retries immediately despite the huge debounce interval,
    # because the failed write never advanced the last-write clock
    matrix.record("m", TaskType.CLASSIFY, passed=False)
    assert path.exists()
    assert matrix.last_persist_error is None            # recovered
    assert CapabilityMatrix.load(path).samples("m", TaskType.CLASSIFY) == 2


# -- per-tier pools: within-tier member selection ----------------------------------


class _StubRng:
    """A random() that always returns a fixed value — makes ε-exploration
    deterministic (0.0 forces it, 1.0 forbids it)."""

    def __init__(self, value: float) -> None:
        self.value = value

    def random(self) -> float:
        return self.value


def mid_pool_router(matrix: CapabilityMatrix | None = None, **kw) -> Router:
    """One MID tier with two members, distinct model strings so the matrix can
    hold separate evidence for each. Configured order: member-a then member-b."""
    return Router(
        {Tier.MID: [
            MockLLMWorker("mid-a", Tier.MID, model="model-a"),
            MockLLMWorker("mid-b", Tier.MID, model="model-b"),
        ]},
        matrix=matrix, **kw,
    )


def verifiable(**kw) -> Task:
    return Task(task_type=TaskType.CLASSIFY, success_check={"equals": "x"}, **kw)


def test_best_member_wins_the_slot_on_evidence():
    """Two members share a tier; observed pass rate decides which one serves."""
    matrix = CapabilityMatrix(smoothing=2.0)
    for _ in range(20):
        matrix.record("model-b", TaskType.CLASSIFY, passed=True)
        matrix.record("model-a", TaskType.CLASSIFY, passed=False)
    router = mid_pool_router(matrix=matrix)
    decision = router.decide(Task(task_type=TaskType.CLASSIFY))
    assert decision.tier == Tier.MID and decision.worker_id == "mid-b"
    assert decision.explored is False


def test_cold_start_picks_configured_first_member():
    """No evidence -> both members tie on the prior -> earliest configured wins."""
    router = mid_pool_router()
    decision = router.decide(Task(task_type=TaskType.CLASSIFY))
    assert decision.worker_id == "mid-a"


def test_exploration_picks_least_sampled_non_best_on_verifiable_task():
    """ε fires on a verifiable task: route to the benched, least-sampled member
    so it earns evidence instead of the incumbent taking every task."""
    matrix = CapabilityMatrix(smoothing=2.0)
    for _ in range(30):  # mid-a is the well-sampled incumbent/best
        matrix.record("model-a", TaskType.CLASSIFY, passed=True)
    router = mid_pool_router(matrix=matrix, explore_rate=0.1, rng=_StubRng(0.0))
    decision = router.decide(verifiable())
    assert decision.worker_id == "mid-b" and decision.explored is True
    assert "exploring mid-b" in decision.reason


def test_exploration_never_fires_on_unverifiable_task():
    """No checkable signal -> no evidence to earn -> never explore, even with
    rng forcing it."""
    matrix = CapabilityMatrix(smoothing=2.0)
    for _ in range(30):
        matrix.record("model-a", TaskType.CLASSIFY, passed=True)
    router = mid_pool_router(matrix=matrix, explore_rate=0.1, rng=_StubRng(0.0))
    decision = router.decide(Task(task_type=TaskType.CLASSIFY))  # no success_check
    assert decision.worker_id == "mid-a" and decision.explored is False


def test_exploration_never_fires_on_schema_only_task():
    """An output_schema alone can produce FAIL or UNVERIFIED but never PASS
    (deterministic verifier), so exploring there would only bank downside
    evidence against the benched member — the gate must not fire."""
    router = mid_pool_router(explore_rate=0.1, rng=_StubRng(0.0))
    task = Task(task_type=TaskType.CLASSIFY,
                output_schema={"type": "object"})  # no success_check
    decision = router.decide(task)
    assert decision.worker_id == "mid-a" and decision.explored is False


def test_duplicate_member_object_never_breaks_exploration():
    """The same runner OBJECT pooled twice leaves no 'other' member to explore;
    decide() must fall back to the best pick instead of min() over nothing."""
    w = MockLLMWorker("mid-a", Tier.MID, model="model-a")
    router = Router({Tier.MID: [w, w]}, explore_rate=1.0, rng=_StubRng(0.0))
    decision = router.decide(verifiable())
    assert decision.worker_id == "mid-a" and decision.explored is False


def test_explored_decision_reports_served_members_rate():
    """The tier is chosen on its best member's rate, but the decision must
    describe the member actually served — an explored decision carries the
    benched member's own (lower-confidence) rate and samples."""
    matrix = CapabilityMatrix(smoothing=2.0)
    for _ in range(30):
        matrix.record("model-a", TaskType.CLASSIFY, passed=True)
    router = mid_pool_router(matrix=matrix, explore_rate=0.1, rng=_StubRng(0.0))
    decision = router.decide(verifiable())
    prior = DEFAULT_PRIORS[Tier.MID][TaskType.CLASSIFY]
    assert decision.explored and decision.worker_id == "mid-b"
    assert decision.expected_pass_rate == matrix.pass_rate("model-b", TaskType.CLASSIFY, prior=prior)
    assert decision.expected_pass_rate != matrix.pass_rate("model-a", TaskType.CLASSIFY, prior=prior)
    assert "samples=0" in decision.reason  # the SERVED member's samples, not the best's


def test_unexplored_decision_reports_best_members_rate():
    """Without exploration the served member IS the best one, so the reported
    rate coincides with the tier ceiling used for tier selection."""
    matrix = CapabilityMatrix(smoothing=2.0)
    for _ in range(30):
        matrix.record("model-a", TaskType.CLASSIFY, passed=True)
    router = mid_pool_router(matrix=matrix, rng=_StubRng(1.0))
    decision = router.decide(verifiable())
    prior = DEFAULT_PRIORS[Tier.MID][TaskType.CLASSIFY]
    assert decision.worker_id == "mid-a" and not decision.explored
    assert decision.expected_pass_rate == matrix.pass_rate("model-a", TaskType.CLASSIFY, prior=prior)
    assert decision.expected_pass_rate == router.expected_pass_rate(Tier.MID, TaskType.CLASSIFY)


def test_runner_for_resolves_decided_member():
    router = mid_pool_router()
    decision = router.decide(Task(task_type=TaskType.CLASSIFY))
    assert router.runner_for(decision).worker_id == decision.worker_id
    # an unknown worker_id falls back to the tier pool's first member
    decision.worker_id = "ghost"
    assert router.runner_for(decision).worker_id == "mid-a"


def test_runner_for_empty_tier_raises():
    """A decision naming a tier no pool serves is a wiring error, not an
    IndexError deep in the executor."""
    router = mid_pool_router()
    decision = RoutingDecision(tier=Tier.FRONTIER, worker_id="ghost", model="",
                               expected_pass_rate=0.0, reason="")
    with pytest.raises(ValueError, match="no pool serves tier frontier"):
        router.runner_for(decision)


def test_next_tier_skips_empty_pool():
    router = Router({
        Tier.SMALL: [MockLLMWorker("w-small", Tier.SMALL)],
        Tier.MID: [],  # empty pool is dropped at construction
        Tier.FRONTIER: [MockLLMWorker("w-front", Tier.FRONTIER)],
    })
    assert Tier.MID not in router.pools
    assert router.next_tier(Tier.SMALL) == Tier.FRONTIER


def test_route_evidence_counts_increment_on_decide():
    router = mid_pool_router()
    router.decide(Task(task_type=TaskType.CLASSIFY))
    router.decide(Task(task_type=TaskType.CLASSIFY))
    assert router.route_evidence() == {"mid": {"mid-a": 2}}
