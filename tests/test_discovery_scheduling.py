"""META-8: frozen population-state contracts for discovery scheduling.

Covers descriptor self-hashing, canonical ordering, authority-shaped extra-field
rejection, numeric range checks, and population-window bounds.
"""
from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from metaharness.discovery.population import (
    ApproachFingerprint,
    PopulationDescriptor,
    PopulationWindow,
)


def make_fingerprint(**overrides) -> ApproachFingerprint:
    defaults: dict = dict(
        candidate_id="candidate-1",
        lineage_id="lineage-1",
        approach_descriptor_tokens=["counterexample", "local-repair"],
        structure_signature="python:src/metaharness/discovery",
        score_tier="frontier",
    )
    defaults.update(overrides)
    return ApproachFingerprint(**defaults)


def make_descriptor(**overrides) -> PopulationDescriptor:
    defaults: dict = dict(
        campaign_id="campaign-1",
        window_id="window-1",
        candidate_nodes=[
            {
                "candidate_id": "candidate-2",
                "lineage_id": "lineage-2",
                "approach_descriptor_tokens": ["structural", "composition"],
                "structure_signature": "python:graph-b",
                "score_tier": "promising",
            },
            make_fingerprint(),
        ],
        parent_edges=[
            ("candidate-2", "candidate-1"),
            ("candidate-1", "baseline-candidate"),
        ],
        best_score=0.90,
        frontier_score=0.85,
        window_score_mean=0.72,
        window_score_variance=0.04,
        approach_diversity=0.75,
        behavioral_diversity=0.60,
        parent_selection_concentration=0.35,
        lineage_depth=3,
        lineage_width=2,
        score_tier_coverage=0.50,
        pareto_coverage=0.40,
        steps_since_meaningful_improvement=2,
        variation_operator_yield={"structural": 0.50, "local": 0.25},
        cross_agent_transfer_count=1,
        memory_use_concentration=0.30,
        evaluator_failure_count=0,
        cost_so_far=12.5,
        latency_stats={"p95_seconds": 4.0, "mean_seconds": 2.5},
        remaining_budget={"wall_seconds": 300.0, "evaluations": 8.0, "attempts": 9.0},
    )
    defaults.update(overrides)
    return PopulationDescriptor(**defaults)


def make_window(**overrides) -> PopulationWindow:
    defaults: dict = dict(
        window_id="window-1",
        start_attempt_sequence=4,
        end_attempt_sequence=9,
        descriptor_hash=make_descriptor().descriptor_hash,
    )
    defaults.update(overrides)
    return PopulationWindow(**defaults)


def test_population_descriptor_self_hash_auto_fills_and_rejects_tampering():
    descriptor = make_descriptor()
    assert descriptor.descriptor_hash.startswith("sha256:")

    tampered = descriptor.model_dump(mode="json")
    tampered["descriptor_hash"] = "sha256:" + "9" * 64
    with pytest.raises(ValidationError):
        PopulationDescriptor(**tampered)


@pytest.mark.parametrize("make", [make_fingerprint, make_descriptor, make_window])
def test_population_contracts_reject_authority_shaped_extra_fields(make):
    with pytest.raises(ValidationError):
        make(can_promote=True)


def test_population_descriptor_canonical_ordering_is_hash_deterministic():
    descriptor_a = make_descriptor()
    raw = descriptor_a.model_dump(mode="json", exclude={"descriptor_hash"})

    raw["candidate_nodes"] = list(reversed(raw["candidate_nodes"]))
    raw["parent_edges"] = list(reversed(raw["parent_edges"]))
    for field in ("variation_operator_yield", "latency_stats", "remaining_budget"):
        raw[field] = list(reversed(raw[field]))
    descriptor_b = PopulationDescriptor(**copy.deepcopy(raw))

    assert descriptor_a.descriptor_hash == descriptor_b.descriptor_hash
    assert descriptor_a.model_dump_json() == descriptor_b.model_dump_json()


@pytest.mark.parametrize(
    "field",
    [
        "lineage_depth",
        "lineage_width",
        "steps_since_meaningful_improvement",
        "cross_agent_transfer_count",
        "evaluator_failure_count",
    ],
)
def test_population_descriptor_rejects_negative_counts(field):
    with pytest.raises(ValidationError):
        make_descriptor(**{field: -1})


@pytest.mark.parametrize(
    "field",
    [
        "best_score",
        "frontier_score",
        "window_score_mean",
        "window_score_variance",
        "cost_so_far",
    ],
)
def test_population_descriptor_rejects_negative_scalar_measures(field):
    with pytest.raises(ValidationError):
        make_descriptor(**{field: -0.01})


@pytest.mark.parametrize(
    "field",
    [
        "approach_diversity",
        "behavioral_diversity",
        "parent_selection_concentration",
        "score_tier_coverage",
        "pareto_coverage",
        "memory_use_concentration",
    ],
)
def test_population_descriptor_rejects_fractions_above_one(field):
    with pytest.raises(ValidationError):
        make_descriptor(**{field: 1.01})


def test_population_descriptor_rejects_out_of_range_mapping_values():
    with pytest.raises(ValidationError):
        make_descriptor(variation_operator_yield={"local": 1.01})
    with pytest.raises(ValidationError):
        make_descriptor(latency_stats={"mean_seconds": -0.01})
    with pytest.raises(ValidationError):
        make_descriptor(remaining_budget={"attempts": -1.0})


@pytest.mark.parametrize(
    ("make", "field"),
    [
        (make_fingerprint, "candidate_id"),
        (make_fingerprint, "lineage_id"),
        (make_descriptor, "campaign_id"),
        (make_descriptor, "window_id"),
        (make_window, "window_id"),
    ],
)
def test_population_contracts_reject_empty_ids(make, field):
    with pytest.raises(ValidationError):
        make(**{field: ""})


def test_population_window_rejects_end_before_start_and_negative_sequences():
    with pytest.raises(ValidationError):
        make_window(start_attempt_sequence=10, end_attempt_sequence=9)
    with pytest.raises(ValidationError):
        make_window(start_attempt_sequence=-1)


def test_fingerprint_tokens_are_sorted_unique_deterministically():
    fingerprint_a = make_fingerprint(
        approach_descriptor_tokens=["structural", "local", "structural"]
    )
    fingerprint_b = make_fingerprint(
        approach_descriptor_tokens=["local", "structural"]
    )

    assert fingerprint_a == fingerprint_b
    assert fingerprint_a.approach_descriptor_tokens == ("local", "structural")


# ---------------------------------------------------------------------------
# META-8 scheduler tests (SearchDecisionReceipt + PopulationScheduler).
# Appended: the population-contract tests above are intentionally untouched.
# ---------------------------------------------------------------------------

from metaharness.discovery.models import DiscoveryBudgets, DiscoveryRole
from metaharness.discovery.policy import (
    InspirationSelector,
    IslandVisibility,
    MemoryVisibility,
    ParentSelector,
    SearchPolicyDSL,
    SearchPolicySnapshot,
    SearchPolicyStopRules,
    VariationClass,
)
from metaharness.discovery.scheduler import (
    BudgetAllocation,
    CandidateAlternative,
    PopulationScheduler,
    ScheduledSpawn,
    SchedulerError,
    SearchDecisionReceipt,
)


def make_budgets(**overrides) -> DiscoveryBudgets:
    defaults: dict = dict(
        max_concurrency=4,
        max_restarts_per_attempt=1,
        max_attempts=100,
        max_evaluations=50,
        max_wall_seconds=3600,
        attempt_timeout_seconds=600,
    )
    defaults.update(overrides)
    return DiscoveryBudgets(**defaults)


def make_dsl(**overrides) -> SearchPolicyDSL:
    defaults: dict = dict(
        parent_selector=ParentSelector.DIVERSE,
        inspiration_selector=InspirationSelector.DIVERSE,
        explorer_fraction=0.5,
        optimizer_fraction=0.5,
        variation_weights={
            VariationClass.LOCAL: 0.6,
            VariationClass.STRUCTURAL: 0.4,
        },
        briefing_template_id="minimal-brief-v1",
        max_width=2,
        max_depth=5,
        max_concurrency=2,
        diversity_floor=0.2,
        baseline_reseed_interval=5,
        memory_visibility=MemoryVisibility.LINEAGE,
        island_visibility=IslandVisibility.ISOLATED,
        stop_rules=SearchPolicyStopRules(
            max_attempts=4, max_cost=50.0, stagnation_window=3
        ),
    )
    defaults.update(overrides)
    return SearchPolicyDSL(**defaults)


def make_sched_snapshot(**overrides) -> SearchPolicySnapshot:
    policy_overrides = overrides.pop("policy_overrides", {})
    defaults: dict = dict(
        policy_id="policy-sched",
        parent_policy_id="policy-root",
        campaign_id="campaign-1",
        policy=make_dsl(**policy_overrides),
        window_id="window-1",
        created_sequence=1,
    )
    defaults.update(overrides)
    return SearchPolicySnapshot(**defaults)


def make_diverse_descriptor(
    *,
    concentration: float = 0.95,
    steps: int = 0,
    remaining_attempts: int = 9,
    remaining_cost: float | None = None,
) -> PopulationDescriptor:
    """Two candidates whose ELITE and UNDEREXPLORED rankings diverge.

    ``cand-elite`` is the frontier-tier elite on lineage ``z-lin`` so ELITE
    ranks it first; ``cand-other`` is promising-tier on lineage ``a-lin`` so
    UNDEREXPLORED (which orders by lineage id) ranks it first instead. This
    lets a diversity-floor-forced choice be distinguished from an elite choice.
    """
    budget: dict = {"attempts": float(remaining_attempts)}
    if remaining_cost is not None:
        budget["cost"] = remaining_cost
    return PopulationDescriptor(
        campaign_id="campaign-1",
        window_id="window-1",
        candidate_nodes=[
            ApproachFingerprint(
                candidate_id="cand-other",
                lineage_id="a-lin",
                approach_descriptor_tokens=["local"],
                structure_signature="python:graph-b",
                score_tier="promising",
            ),
            ApproachFingerprint(
                candidate_id="cand-elite",
                lineage_id="z-lin",
                approach_descriptor_tokens=["structural"],
                structure_signature="python:graph-a",
                score_tier="frontier",
            ),
        ],
        parent_edges=[
            ("cand-elite", "baseline-candidate"),
            ("cand-other", "baseline-candidate"),
        ],
        best_score=0.9,
        frontier_score=0.85,
        window_score_mean=0.7,
        window_score_variance=0.04,
        approach_diversity=0.6,
        behavioral_diversity=0.5,
        parent_selection_concentration=concentration,
        lineage_depth=1,
        lineage_width=2,
        score_tier_coverage=0.5,
        pareto_coverage=0.4,
        steps_since_meaningful_improvement=steps,
        variation_operator_yield={"local": 0.5},
        cross_agent_transfer_count=0,
        memory_use_concentration=0.1,
        evaluator_failure_count=0,
        cost_so_far=1.0,
        latency_stats={"mean_seconds": 1.0},
        remaining_budget=budget,
    )


def _optimizer_spawns(spawns: tuple[ScheduledSpawn, ...]) -> list[ScheduledSpawn]:
    return [s for s in spawns if s.receipt.role is DiscoveryRole.OPTIMIZER]


def _explorer_spawns(spawns: tuple[ScheduledSpawn, ...]) -> list[ScheduledSpawn]:
    return [s for s in spawns if s.receipt.role is DiscoveryRole.EXPLORER]


def test_scheduler_receipt_completeness_records_every_required_field():
    snapshot = make_sched_snapshot()
    scheduler = PopulationScheduler(snapshot, campaign_budgets=make_budgets())
    spawns = scheduler.schedule(make_descriptor(), sequence=1, window_attempts_used=0)

    assert spawns, "scheduler must emit at least one spawn"
    for spawn in spawns:
        receipt = spawn.receipt
        assignment = spawn.assignment
        # bindings
        assert receipt.campaign_id == assignment.campaign_id == "campaign-1"
        assert receipt.descriptor_hash == make_descriptor().descriptor_hash
        assert receipt.policy_hash == snapshot.policy_hash
        # required receipt fields are present and well-typed
        assert receipt.role in (DiscoveryRole.EXPLORER, DiscoveryRole.OPTIMIZER)
        assert isinstance(receipt.variation_class, VariationClass)
        assert receipt.briefing_template_id == "minimal-brief-v1"
        assert isinstance(receipt.budget_allocated, BudgetAllocation)
        assert receipt.budget_allocated.attempts >= 1
        assert receipt.width_allocated >= 1
        assert receipt.concurrency_allocated >= 1
        assert receipt.depth_allocated >= 0
        assert 0.0 <= receipt.expected_information_gain <= 1.0
        assert receipt.reason
        # more than one candidate existed -> alternatives never empty
        assert len(make_descriptor().candidate_nodes) >= 2
        assert receipt.alternatives_considered, (
            "alternatives_considered must never be empty when more than one "
            "candidate existed"
        )
        for alt in receipt.alternatives_considered:
            assert isinstance(alt, CandidateAlternative)
            assert 0.0 <= alt.selector_score <= 1.0
        # parent is recorded on the assignment payload too
        assert assignment.role is receipt.role
        assert assignment.parent_lineage_id == receipt.parent_lineage_id


def test_scheduler_diversity_floor_forces_non_elite_parents_on_concentrated_descriptor():
    snapshot = make_sched_snapshot(
        policy_overrides={
            "parent_selector": ParentSelector.ELITE,
            "diversity_floor": 0.3,  # max concentration 0.7
        }
    )
    scheduler = PopulationScheduler(snapshot, campaign_budgets=make_budgets())

    # Not concentrated (0.3 <= 0.7): ELITE selects the frontier elite.
    relaxed = make_diverse_descriptor(concentration=0.3)
    relaxed_spawns = scheduler.schedule(relaxed, sequence=1, window_attempts_used=0)
    relaxed_opt = _optimizer_spawns(relaxed_spawns)
    assert relaxed_opt, "expected at least one optimizer spawn"
    assert relaxed_opt[0].receipt.parent_candidate_id == "cand-elite"

    # Concentrated (0.95 > 0.7): forced UNDEREXPLORED picks the non-elite.
    concentrated = make_diverse_descriptor(concentration=0.95)
    concentrated_spawns = scheduler.schedule(concentrated, sequence=1, window_attempts_used=0)
    concentrated_opt = _optimizer_spawns(concentrated_spawns)
    assert concentrated_opt, "expected at least one optimizer spawn"
    forced_parent = concentrated_opt[0].receipt.parent_candidate_id
    assert forced_parent == "cand-other", (
        "diversity floor must force a non-elite (UNDEREXPLORED) parent, not "
        f"the elite candidate (got {forced_parent!r})"
    )


def test_scheduler_baseline_reseed_fires_on_the_interval():
    snapshot = make_sched_snapshot(
        policy_overrides={"baseline_reseed_interval": 5}
    )
    scheduler = PopulationScheduler(snapshot, campaign_budgets=make_budgets())

    reseed_at_zero = scheduler.schedule(make_descriptor(), sequence=0, window_attempts_used=0)
    assert any(
        s.receipt.reason.startswith("baseline reseed") for s in reseed_at_zero
    ), "a baseline reseed must fire on a multiple of the interval"

    none_at_one = scheduler.schedule(make_descriptor(), sequence=1, window_attempts_used=0)
    assert not any(
        s.receipt.reason.startswith("baseline reseed") for s in none_at_one
    ), "no baseline reseed should fire off the interval"

    reseed_at_five = scheduler.schedule(make_descriptor(), sequence=5, window_attempts_used=0)
    assert any(
        s.receipt.reason.startswith("baseline reseed") for s in reseed_at_five
    ), "a baseline reseed must fire again on the next multiple of the interval"


def test_scheduler_is_deterministic_for_identical_inputs():
    snapshot = make_sched_snapshot()
    scheduler = PopulationScheduler(snapshot, campaign_budgets=make_budgets())
    descriptor = make_descriptor()

    first = scheduler.schedule(descriptor, sequence=3, window_attempts_used=0)
    second = scheduler.schedule(descriptor, sequence=3, window_attempts_used=0)

    assert len(first) == len(second)
    for a, b in zip(first, second):
        assert a.assignment.assignment_hash == b.assignment.assignment_hash
        assert a.receipt.receipt_hash == b.receipt.receipt_hash
        assert a.model_dump_json() == b.model_dump_json()


def test_scheduler_over_budget_allocation_is_rejected_and_never_over_allocates():
    snapshot = make_sched_snapshot()
    scheduler = PopulationScheduler(snapshot, campaign_budgets=make_budgets())

    # Fully exhausted attempts budget -> scheduling is rejected outright.
    exhausted = make_descriptor(remaining_budget={"attempts": 0.0})
    with pytest.raises(SchedulerError):
        scheduler.schedule(exhausted, sequence=1, window_attempts_used=0)

    # Partial budget caps width to the remaining attempts (never over).
    cap_snapshot = make_sched_snapshot(
        policy_overrides={"max_width": 10, "max_concurrency": 10}
    )
    cap_scheduler = PopulationScheduler(
        cap_snapshot, campaign_budgets=make_budgets(max_concurrency=10)
    )
    partial = make_descriptor(remaining_budget={"attempts": 2.0})
    spawns = cap_scheduler.schedule(partial, sequence=1, window_attempts_used=0)
    assert len(spawns) == 2, "width must be capped to remaining attempts, not exceeded"


def test_scheduler_optimizer_needs_parent_explorer_must_not_have_parent():
    snapshot = make_sched_snapshot()
    scheduler = PopulationScheduler(snapshot, campaign_budgets=make_budgets())
    spawns = scheduler.schedule(make_descriptor(), sequence=1, window_attempts_used=0)

    optimizers = _optimizer_spawns(spawns)
    explorers = _explorer_spawns(spawns)
    assert optimizers and explorers, "expected both roles in one decision"

    for spawn in optimizers:
        assert spawn.assignment.parent_lineage_id is not None
        assert spawn.assignment.parent_attempt_id is not None
        assert spawn.receipt.parent_lineage_id is not None
        assert spawn.receipt.parent_candidate_id is not None
    for spawn in explorers:
        assert spawn.assignment.parent_lineage_id is None
        assert spawn.assignment.parent_attempt_id is None
        assert spawn.receipt.parent_lineage_id is None
        assert spawn.receipt.parent_candidate_id is None

    # The receipt enforces the invariant directly: an optimizer receipt
    # without a parent, and an explorer receipt WITH a parent, are rejected.
    base_kwargs = dict(
        campaign_id="campaign-1",
        sequence=1,
        descriptor_hash=make_descriptor().descriptor_hash,
        policy_hash=snapshot.policy_hash,
        role=DiscoveryRole.OPTIMIZER,
        variation_class=VariationClass.LOCAL,
        briefing_template_id="minimal-brief-v1",
        width_allocated=1,
        depth_allocated=2,
        concurrency_allocated=1,
        budget_allocated=BudgetAllocation(attempts=1, cost=0.0),
        alternatives_considered=(
            CandidateAlternative(candidate_id="c-1", selector_score=1.0),
        ),
        expected_information_gain=0.3,
        reason="optimizer without parent",
    )
    with pytest.raises(ValidationError):
        SearchDecisionReceipt(parent_lineage_id=None, parent_candidate_id=None, **base_kwargs)
    explorer_kwargs = dict(base_kwargs)
    explorer_kwargs["role"] = DiscoveryRole.EXPLORER
    explorer_kwargs["parent_lineage_id"] = "lineage-x"
    explorer_kwargs["parent_candidate_id"] = "candidate-x"
    explorer_kwargs["reason"] = "explorer with parent"
    with pytest.raises(ValidationError):
        SearchDecisionReceipt(**explorer_kwargs)


def test_scheduler_binds_policy_and_descriptor_hash_on_every_receipt():
    snapshot = make_sched_snapshot()
    scheduler = PopulationScheduler(snapshot, campaign_budgets=make_budgets())
    descriptor = make_descriptor()
    spawns = scheduler.schedule(descriptor, sequence=2, window_attempts_used=0)

    assert spawns
    for spawn in spawns:
        assert spawn.receipt.policy_hash == snapshot.policy_hash
        assert spawn.receipt.descriptor_hash == descriptor.descriptor_hash
        assert spawn.receipt.sequence == 2


def test_scheduler_rejects_campaign_mismatch_and_tampered_policy():
    foreign = make_diverse_descriptor()
    foreign = foreign.model_copy(
        update={"campaign_id": "other-campaign"},
    )
    # Re-hash the tampered descriptor so it is internally consistent.
    foreign = PopulationDescriptor(
        **{k: v for k, v in foreign.model_dump(mode="json").items() if k != "descriptor_hash"}
    )
    snapshot = make_sched_snapshot()
    scheduler = PopulationScheduler(snapshot, campaign_budgets=make_budgets())
    with pytest.raises(SchedulerError):
        scheduler.schedule(foreign, sequence=1, window_attempts_used=0)


def test_scheduler_receipt_self_hash_rejects_tampering_and_authority_extras():
    snapshot = make_sched_snapshot()
    scheduler = PopulationScheduler(snapshot, campaign_budgets=make_budgets())
    spawn = scheduler.schedule(make_descriptor(), sequence=1, window_attempts_used=0)[0]

    tampered = spawn.receipt.model_dump(mode="json")
    tampered["receipt_hash"] = "sha256:" + "9" * 64
    with pytest.raises(ValidationError):
        SearchDecisionReceipt(**tampered)

    # No approval/authority field is carried: an authority-shaped extra is
    # rejected exactly the way DiscoveryBoundary rejects can_promote=True.
    with pytest.raises(ValidationError):
        SearchDecisionReceipt(
            campaign_id="campaign-1",
            sequence=1,
            descriptor_hash=make_descriptor().descriptor_hash,
            policy_hash=snapshot.policy_hash,
            role=DiscoveryRole.EXPLORER,
            variation_class=VariationClass.LOCAL,
            briefing_template_id="minimal-brief-v1",
            width_allocated=1,
            depth_allocated=1,
            concurrency_allocated=1,
            budget_allocated=BudgetAllocation(attempts=1, cost=0.0),
            alternatives_considered=(),
            expected_information_gain=0.5,
            reason="authority extra",
            can_promote=True,
        )


# ---------------------------------------------------------------------------
# META-8 fix-round regressions (F1-F6).
# ---------------------------------------------------------------------------


def test_scheduler_f1_reseed_never_overruns_width_with_optimizer_only():
    # optimizer_fraction=1.0 + a due reseed at sequence=0 must emit exactly
    # `width` spawns (the reseed consumes a slot INSIDE width rather than
    # appending an extra spawn on top of it).
    snapshot = make_sched_snapshot(
        policy_overrides={
            "explorer_fraction": 0.0,
            "optimizer_fraction": 1.0,
            "max_width": 2,
            "max_concurrency": 2,
        }
    )
    scheduler = PopulationScheduler(
        snapshot, campaign_budgets=make_budgets(max_concurrency=10)
    )
    spawns = scheduler.schedule(make_descriptor(), sequence=0, window_attempts_used=0)
    assert len(spawns) == 2, "reseed must occupy a slot inside width, not add width+1"
    assert any(s.receipt.reason.startswith("baseline reseed") for s in spawns)


def test_scheduler_f2_stop_rules_max_attempts_caps_batch_size():
    # The policy's own stop_rules.max_attempts must cap the batch even when
    # max_width / max_concurrency / remaining budget would permit more.
    snapshot = make_sched_snapshot(
        policy_overrides={
            "max_width": 10,
            "max_concurrency": 10,
            "stop_rules": SearchPolicyStopRules(
                max_attempts=4, max_cost=50.0, stagnation_window=3
            ),
        }
    )
    scheduler = PopulationScheduler(
        snapshot, campaign_budgets=make_budgets(max_concurrency=10)
    )
    descriptor = make_descriptor(remaining_budget={"attempts": 100.0})
    spawns = scheduler.schedule(descriptor, sequence=1, window_attempts_used=0)
    assert len(spawns) <= 4
    assert len(spawns) == 4


def _make_diverse_nodes(count: int) -> list:
    return [
        ApproachFingerprint(
            candidate_id=f"c-{i}",
            lineage_id=f"lin-{i}",
            approach_descriptor_tokens=["local"],
            structure_signature=f"sig-{i}",
            score_tier="frontier",
        )
        for i in range(count)
    ]


def _batch_parent_concentration(spawns: tuple[ScheduledSpawn, ...]) -> float:
    total = len(spawns)
    if total == 0:
        return 0.0
    counts: dict[str, int] = {}
    for spawn in spawns:
        lineage = spawn.receipt.parent_lineage_id
        if lineage is not None:
            counts[lineage] = counts.get(lineage, 0) + 1
    max_count = max(counts.values()) if counts else 0
    return max_count / total


def test_scheduler_f3_emitted_batch_respects_diversity_floor():
    # 10 candidates on distinct lineages, max_concurrency=2, 50/50 fractions,
    # diversity_floor=0.5 -> emitted batch concentration must be <= 0.5.
    snapshot = make_sched_snapshot(
        policy_overrides={
            "max_width": 2,
            "max_concurrency": 2,
            "diversity_floor": 0.5,
        }
    )
    scheduler = PopulationScheduler(
        snapshot, campaign_budgets=make_budgets(max_concurrency=10)
    )
    descriptor = make_descriptor(
        candidate_nodes=_make_diverse_nodes(10),
        parent_selection_concentration=0.5,
        lineage_depth=1,
    )
    spawns = scheduler.schedule(descriptor, sequence=1, window_attempts_used=0)
    assert len(spawns) == 2
    assert _batch_parent_concentration(spawns) <= 0.5 + 1e-12


def test_scheduler_f3_degrades_single_optimizer_under_high_floor():
    # width=1, optimizer-only: a single optimizer is 100% concentration. With
    # diversity_floor=0.5 (allowed 0.5), 1/1=1.0 > 0.5 -> the F3 loop degrades
    # it to a fresh explorer so the emitted batch respects the floor.
    snapshot = make_sched_snapshot(
        policy_overrides={
            "explorer_fraction": 0.0,
            "optimizer_fraction": 1.0,
            "max_width": 1,
            "max_concurrency": 1,
            "diversity_floor": 0.5,
        }
    )
    scheduler = PopulationScheduler(
        snapshot, campaign_budgets=make_budgets(max_concurrency=10)
    )
    descriptor = make_descriptor(lineage_depth=1)
    spawns = scheduler.schedule(descriptor, sequence=1, window_attempts_used=0)
    assert len(spawns) == 1
    assert spawns[0].receipt.role is DiscoveryRole.EXPLORER
    assert spawns[0].receipt.parent_lineage_id is None
    assert _batch_parent_concentration(spawns) == 0.0


def test_scheduler_f4_scheduled_spawn_rejects_foreign_campaign_pairing():
    # A valid receipt re-paired onto a foreign-campaign assignment must be
    # rejected by the ScheduledSpawn binding validator (F4).
    snapshot_one = make_sched_snapshot()
    scheduler_one = PopulationScheduler(
        snapshot_one, campaign_budgets=make_budgets()
    )
    spawn_one = scheduler_one.schedule(make_descriptor(), sequence=1, window_attempts_used=0)[0]

    snapshot_two = make_sched_snapshot(campaign_id="campaign-2")
    scheduler_two = PopulationScheduler(
        snapshot_two, campaign_budgets=make_budgets()
    )
    desc_two = make_descriptor(campaign_id="campaign-2")
    spawn_two = scheduler_two.schedule(desc_two, sequence=1, window_attempts_used=0)[0]

    with pytest.raises(ValidationError):
        ScheduledSpawn(assignment=spawn_two.assignment, receipt=spawn_one.receipt)

    # Positive control: the scheduler's own spawn passes the binding validator.
    ScheduledSpawn(
        assignment=spawn_one.assignment, receipt=spawn_one.receipt
    )


def test_scheduler_f4_receipt_carries_assignment_hash_binding():
    snapshot = make_sched_snapshot()
    scheduler = PopulationScheduler(snapshot, campaign_budgets=make_budgets())
    spawn = scheduler.schedule(make_descriptor(), sequence=1, window_attempts_used=0)[0]
    # F4: every receipt binds the paired assignment hash.
    assert spawn.receipt.assignment_hash == spawn.assignment.assignment_hash
    assert spawn.receipt.assignment_hash.startswith("sha256:")


def test_scheduler_f5_rejects_stale_hash_descriptor_from_model_copy():
    snapshot = make_sched_snapshot()
    scheduler = PopulationScheduler(snapshot, campaign_budgets=make_budgets())
    descriptor = make_descriptor()
    # model_copy bypasses validation, leaving a stale descriptor_hash relative
    # to the mutated remaining_budget.
    tampered = descriptor.model_copy(
        update={"remaining_budget": (("attempts", 1.0),)}
    )
    with pytest.raises(SchedulerError):
        scheduler.schedule(tampered, sequence=1, window_attempts_used=0)


def test_scheduler_f6_optimizer_build_without_parent_raises_not_assert():
    # The optimizer-needs-parent guard raises SchedulerError (surviving -O)
    # rather than a bare `assert`.
    snapshot = make_sched_snapshot()
    scheduler = PopulationScheduler(snapshot, campaign_budgets=make_budgets())
    with pytest.raises(SchedulerError):
        scheduler._build_spawn(
            role=DiscoveryRole.OPTIMIZER,
            parent=None,
            spawn_index=0,
            sequence=1,
            campaign_id="campaign-1",
            descriptor_hash=make_descriptor().descriptor_hash,
            policy_hash=snapshot.policy_hash,
            width=1,
            depth_allocated=1,
            concurrency=1,
            budget_allocation=BudgetAllocation(attempts=1, cost=0.0),
            alternatives=(),
            variation_class=VariationClass.LOCAL,
            briefing_template_id="minimal-brief-v1",
            expected_gain=0.5,
            reason="optimizer without parent",
        )


# ---------------------------------------------------------------------------
# META-8 final fix-round regressions.
# ---------------------------------------------------------------------------


def test_scheduler_final1_window_attempts_used_enforced_cumulatively():
    # max_attempts=4: two consecutive decisions must not emit 4+4 spawns.
    # The caller-managed window_attempts_used accumulator caps the second
    # decision by what the first already emitted, then fails closed once the
    # window is exhausted.
    snapshot = make_sched_snapshot(
        policy_overrides={
            "max_width": 10,
            "max_concurrency": 10,
            "stop_rules": SearchPolicyStopRules(
                max_attempts=4, max_cost=50.0, stagnation_window=3
            ),
        }
    )
    scheduler = PopulationScheduler(
        snapshot, campaign_budgets=make_budgets(max_concurrency=10)
    )
    descriptor = make_descriptor(remaining_budget={"attempts": 100.0})

    first = scheduler.schedule(
        descriptor, sequence=1, window_attempts_used=0
    )
    assert len(first) <= 4, "first decision must respect max_attempts=4"
    assert len(first) == 4

    # Second decision with the first batch already counted: only 0 attempts
    # remain in the window, so scheduling must fail closed (no over-allocation).
    with pytest.raises(SchedulerError):
        scheduler.schedule(
            descriptor, sequence=2, window_attempts_used=4
        )


def test_scheduler_final1_window_attempts_used_caps_partial_remaining():
    # max_attempts=4 with 3 already used -> only 1 spawn remains in the window.
    snapshot = make_sched_snapshot(
        policy_overrides={
            "max_width": 10,
            "max_concurrency": 10,
            "stop_rules": SearchPolicyStopRules(
                max_attempts=4, max_cost=50.0, stagnation_window=3
            ),
        }
    )
    scheduler = PopulationScheduler(
        snapshot, campaign_budgets=make_budgets(max_concurrency=10)
    )
    descriptor = make_descriptor(remaining_budget={"attempts": 100.0})
    spawns = scheduler.schedule(descriptor, sequence=2, window_attempts_used=3)
    assert len(spawns) == 1


def test_scheduler_final1_rejects_negative_window_attempts_used():
    snapshot = make_sched_snapshot()
    scheduler = PopulationScheduler(snapshot, campaign_budgets=make_budgets())
    with pytest.raises(SchedulerError):
        scheduler.schedule(make_descriptor(), sequence=1, window_attempts_used=-1)
