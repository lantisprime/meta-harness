"""META-8: declarative discovery search-policy contracts and staged validation.

Covers bounded identifiers and numeric controls, immutable self-hashes,
canonical variation weights, fail-closed stage ordering, bounded child
mutation, deterministic simulation, and authority-shaped field rejection.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from metaharness.discovery.policy import (
    InspirationSelector,
    IslandVisibility,
    MemoryVisibility,
    ParentSelector,
    PolicyValidationStage,
    PolicyValidationVerdict,
    SearchPolicyDSL,
    SearchPolicySnapshot,
    SearchPolicyStopRules,
    VariationClass,
    validate_policy,
)
from metaharness.discovery.population import PopulationDescriptor


def make_stop_rules(**overrides) -> SearchPolicyStopRules:
    defaults: dict = dict(
        max_attempts=4,
        max_cost=50.0,
        stagnation_window=3,
    )
    defaults.update(overrides)
    return SearchPolicyStopRules(**defaults)


def make_policy(**overrides) -> SearchPolicyDSL:
    defaults: dict = dict(
        parent_selector=ParentSelector.DIVERSE,
        inspiration_selector=InspirationSelector.DIVERSE,
        explorer_fraction=0.5,
        optimizer_fraction=0.5,
        variation_weights={
            VariationClass.STRUCTURAL: 0.4,
            VariationClass.LOCAL: 0.6,
        },
        briefing_template_id="minimal-brief-v1",
        max_width=2,
        max_depth=2,
        max_concurrency=2,
        diversity_floor=0.2,
        baseline_reseed_interval=10,
        memory_visibility=MemoryVisibility.LINEAGE,
        island_visibility=IslandVisibility.ISOLATED,
        stop_rules=make_stop_rules(),
    )
    defaults.update(overrides)
    return SearchPolicyDSL(**defaults)


def make_snapshot(**overrides) -> SearchPolicySnapshot:
    defaults: dict = dict(
        policy_id="policy-child",
        parent_policy_id="policy-root",
        campaign_id="campaign-1",
        policy=make_policy(),
        window_id="window-1",
        created_sequence=1,
    )
    defaults.update(overrides)
    return SearchPolicySnapshot(**defaults)


def make_parent(**overrides) -> SearchPolicySnapshot:
    defaults: dict = dict(
        policy_id="policy-root",
        parent_policy_id=None,
        campaign_id="campaign-1",
        policy=make_policy(),
        window_id="window-0",
        created_sequence=0,
    )
    defaults.update(overrides)
    return SearchPolicySnapshot(**defaults)


def make_descriptor(**overrides) -> PopulationDescriptor:
    defaults: dict = dict(
        campaign_id="campaign-1",
        window_id="window-1",
        candidate_nodes=[
            {
                "candidate_id": "candidate-1",
                "lineage_id": "lineage-1",
                "approach_descriptor_tokens": ["local"],
                "structure_signature": "python-graph-a",
                "score_tier": "frontier",
            },
            {
                "candidate_id": "candidate-2",
                "lineage_id": "lineage-2",
                "approach_descriptor_tokens": ["structural"],
                "structure_signature": "python-graph-b",
                "score_tier": "promising",
            },
        ],
        parent_edges=[
            ("candidate-1", "baseline-candidate"),
            ("candidate-2", "candidate-1"),
        ],
        best_score=0.9,
        frontier_score=0.85,
        window_score_mean=0.7,
        window_score_variance=0.03,
        approach_diversity=0.7,
        behavioral_diversity=0.6,
        parent_selection_concentration=0.4,
        lineage_depth=2,
        lineage_width=2,
        score_tier_coverage=0.5,
        pareto_coverage=0.5,
        steps_since_meaningful_improvement=1,
        variation_operator_yield={"local": 0.5, "structural": 0.5},
        cross_agent_transfer_count=1,
        memory_use_concentration=0.3,
        evaluator_failure_count=0,
        cost_so_far=10.0,
        latency_stats={"mean_seconds": 2.0},
        remaining_budget={"attempts": 10.0, "cost": 100.0},
    )
    defaults.update(overrides)
    return PopulationDescriptor(**defaults)


@pytest.mark.parametrize(
    "identifier",
    [
        "two words",
        "back`tick",
        "$variable",
        "semi;colon",
        "path/segment",
        "call(arg)",
    ],
)
def test_dsl_rejects_code_or_path_shaped_identifier_strings(identifier):
    with pytest.raises(ValidationError):
        make_policy(briefing_template_id=identifier)


def test_dsl_requires_role_fractions_to_sum_to_one():
    with pytest.raises(ValidationError):
        make_policy(explorer_fraction=0.6, optimizer_fraction=0.3)


def test_dsl_requires_at_least_one_positive_variation_weight():
    with pytest.raises(ValidationError):
        make_policy(
            variation_weights={
                VariationClass.LOCAL: 0.0,
                VariationClass.STRUCTURAL: 0.0,
            }
        )


def test_variation_weight_mapping_has_canonical_order():
    policy_a = make_policy(
        variation_weights={
            VariationClass.STRUCTURAL: 0.4,
            VariationClass.LOCAL: 0.6,
        }
    )
    policy_b = make_policy(
        variation_weights={
            VariationClass.LOCAL: 0.6,
            VariationClass.STRUCTURAL: 0.4,
        }
    )
    assert policy_a == policy_b
    assert policy_a.model_dump_json() == policy_b.model_dump_json()


def test_snapshot_self_hashes_and_rejects_tampering():
    snapshot = make_snapshot()
    assert snapshot.policy_hash.startswith("sha256:")

    tampered = snapshot.model_dump(mode="json")
    tampered["policy_hash"] = "sha256:" + "9" * 64
    with pytest.raises(ValidationError):
        SearchPolicySnapshot(**tampered)


@pytest.mark.parametrize("make", [make_stop_rules, make_policy, make_snapshot])
def test_policy_contracts_reject_authority_shaped_extra_fields(make):
    with pytest.raises(ValidationError):
        make(can_promote=True)


def test_static_failure_stops_before_simulation_and_shadow():
    parent = make_parent()
    candidate = make_snapshot(
        policy=make_policy(
            parent_selector=ParentSelector.ELITE,
            diversity_floor=0.0,
        )
    )

    receipts = validate_policy(candidate, parent=parent, window=make_descriptor())

    assert [receipt.stage for receipt in receipts] == [
        PolicyValidationStage.SCHEMA,
        PolicyValidationStage.STATIC,
    ]
    assert receipts[0].verdict is PolicyValidationVerdict.PASSED
    assert receipts[1].verdict is PolicyValidationVerdict.FAILED


def test_bounded_mutation_rejects_four_changes_and_allows_three():
    parent = make_parent()
    four_change_policy = make_policy(
        inspiration_selector=InspirationSelector.NONE,
        explorer_fraction=0.75,
        optimizer_fraction=0.25,
        max_concurrency=3,
    )
    four_receipts = validate_policy(
        make_snapshot(policy=four_change_policy),
        parent=parent,
        window=make_descriptor(),
    )
    assert [receipt.stage for receipt in four_receipts] == [
        PolicyValidationStage.SCHEMA,
        PolicyValidationStage.STATIC,
    ]
    assert four_receipts[-1].verdict is PolicyValidationVerdict.FAILED

    three_change_policy = make_policy(
        inspiration_selector=InspirationSelector.NONE,
        explorer_fraction=0.75,
        optimizer_fraction=0.25,
    )
    three_receipts = validate_policy(
        make_snapshot(policy=three_change_policy),
        parent=parent,
        window=make_descriptor(),
    )
    assert three_receipts[1].stage is PolicyValidationStage.STATIC
    assert three_receipts[1].verdict is PolicyValidationVerdict.PASSED


def test_simulation_fails_closed_without_a_population_window():
    receipts = validate_policy(make_snapshot(), parent=make_parent(), window=None)

    assert [receipt.stage for receipt in receipts] == [
        PolicyValidationStage.SCHEMA,
        PolicyValidationStage.STATIC,
        PolicyValidationStage.SIMULATION,
    ]
    assert receipts[-1].verdict is PolicyValidationVerdict.FAILED
    assert receipts[-1].descriptor_hash is None


def test_simulation_rejects_excess_parent_concentration():
    descriptor = make_descriptor(parent_selection_concentration=0.95)
    receipts = validate_policy(
        make_snapshot(), parent=make_parent(), window=descriptor
    )

    assert receipts[-1].stage is PolicyValidationStage.SIMULATION
    assert receipts[-1].verdict is PolicyValidationVerdict.FAILED
    assert receipts[-1].descriptor_hash == descriptor.descriptor_hash
    assert all(
        receipt.stage is not PolicyValidationStage.SHADOW for receipt in receipts
    )


@pytest.mark.parametrize(
    "remaining_budget",
    [
        {"attempts": 1.0, "cost": 100.0},
        {"attempts": 10.0, "cost": 1.0},
    ],
)
def test_simulation_rejects_budget_arithmetic_over_remaining_budget(
    remaining_budget,
):
    descriptor = make_descriptor(remaining_budget=remaining_budget)
    receipts = validate_policy(
        make_snapshot(), parent=make_parent(), window=descriptor
    )

    assert receipts[-1].stage is PolicyValidationStage.SIMULATION
    assert receipts[-1].verdict is PolicyValidationVerdict.FAILED


def test_valid_child_receives_all_four_ordered_receipts():
    descriptor = make_descriptor()
    receipts = validate_policy(
        make_snapshot(), parent=make_parent(), window=descriptor
    )

    assert [receipt.stage for receipt in receipts] == list(PolicyValidationStage)
    assert all(
        receipt.verdict is PolicyValidationVerdict.PASSED for receipt in receipts
    )
    assert receipts[0].descriptor_hash is None
    assert receipts[1].descriptor_hash is None
    assert receipts[2].descriptor_hash == descriptor.descriptor_hash
    assert receipts[3].descriptor_hash == descriptor.descriptor_hash
    assert "no activation authority" in receipts[3].reason


def test_validation_receipts_are_deterministic_for_identical_inputs():
    parent = make_parent()
    candidate = make_snapshot()
    descriptor = make_descriptor()

    first = validate_policy(candidate, parent=parent, window=descriptor)
    second = validate_policy(candidate, parent=parent, window=descriptor)

    assert first == second
    assert [receipt.receipt_hash for receipt in first] == [
        receipt.receipt_hash for receipt in second
    ]
    assert [receipt.model_dump_json() for receipt in first] == [
        receipt.model_dump_json() for receipt in second
    ]


def test_schema_stage_rechecks_hash_on_validation_bypassing_copy():
    candidate = make_snapshot()
    stale = candidate.model_copy(
        update={"policy": make_policy(max_width=3)}
    )

    receipts = validate_policy(stale, parent=make_parent(), window=make_descriptor())

    assert len(receipts) == 1
    assert receipts[0].stage is PolicyValidationStage.SCHEMA
    assert receipts[0].verdict is PolicyValidationVerdict.FAILED


# ---------------------------------------------------------------------------
# Search-policy evolution
# ---------------------------------------------------------------------------

import metaharness.discovery.evolution as evolution_module
from metaharness.discovery.evolution import (
    PolicyActivationReceipt,
    PolicyWindowScore,
    SearchPolicyEvolver,
    StrategyHistoryOutcome,
    StrategyHistoryRow,
)
from metaharness.discovery.policy import PolicyValidationReceipt


def make_window_score(**overrides) -> PolicyWindowScore:
    defaults: dict = dict(
        window_id="window-1",
        policy_hash=make_parent().policy_hash,
        descriptor_hash_before="sha256:" + "1" * 64,
        descriptor_hash_after="sha256:" + "2" * 64,
        observed_progress=0.1,
        steps_since_meaningful_improvement=1,
        attempts_run=4,
        cost_spent=5.0,
    )
    defaults.update(overrides)
    return PolicyWindowScore(**defaults)


def make_history_row(**overrides) -> StrategyHistoryRow:
    defaults: dict = dict(
        sequence=1,
        policy_hash=make_snapshot().policy_hash,
        parent_policy_hash=make_parent().policy_hash,
        window_score_hash=make_window_score().score_hash,
        outcome=StrategyHistoryOutcome.ACTIVATED,
        reason="all policy validation stages passed",
    )
    defaults.update(overrides)
    return StrategyHistoryRow(**defaults)


def make_passed_validation_receipts():
    return validate_policy(
        make_snapshot(),
        parent=make_parent(),
        window=make_descriptor(),
    )


def make_activation_receipt(**overrides) -> PolicyActivationReceipt:
    receipts = overrides.pop(
        "validation_receipts", make_passed_validation_receipts()
    )
    defaults: dict = dict(
        policy_hash=receipts[0].policy_hash,
        validation_receipts=receipts,
        validation_receipt_hashes=[
            receipt.receipt_hash for receipt in receipts
        ],
        activated_for_window="window-1",
        activated_sequence=1,
        actor_label="policy-validation-gate",
    )
    defaults.update(overrides)
    return PolicyActivationReceipt(**defaults)


def test_activation_receipt_requires_exactly_four_passed_stage_receipts():
    receipts = make_passed_validation_receipts()
    with pytest.raises(ValidationError):
        make_activation_receipt(
            validation_receipts=receipts[:3],
            validation_receipt_hashes=[
                receipt.receipt_hash for receipt in receipts[:3]
            ],
        )

    failed_static = PolicyValidationReceipt(
        policy_hash=receipts[1].policy_hash,
        stage=PolicyValidationStage.STATIC,
        verdict=PolicyValidationVerdict.FAILED,
        reason="static failed: bounded mutation rejected",
    )
    receipts_with_failure = (
        receipts[0],
        failed_static,
        receipts[2],
        receipts[3],
    )
    with pytest.raises(ValidationError):
        make_activation_receipt(
            validation_receipts=receipts_with_failure,
            validation_receipt_hashes=[
                receipt.receipt_hash for receipt in receipts_with_failure
            ],
        )


@pytest.mark.parametrize(
    ("steps", "progress", "expected"),
    [
        (2, 0.0, False),
        (3, 0.0, True),
        (4, 0.0, True),
        (4, 0.01, False),
    ],
)
def test_stagnation_predicate_boundaries(steps, progress, expected):
    evolver = SearchPolicyEvolver(make_parent())
    score = make_window_score(
        steps_since_meaningful_improvement=steps,
        observed_progress=progress,
    )

    assert evolver.is_stagnant(score, stagnation_window=3) is expected


def test_score_window_is_pure_and_computes_bounded_progress():
    evolver = SearchPolicyEvolver(make_parent())
    before = make_descriptor(
        window_id="window-0",
        best_score=0.5,
        frontier_score=0.4,
        window_score_mean=0.3,
    )
    after = make_descriptor(
        best_score=0.7,
        frontier_score=0.6,
        window_score_mean=0.5,
        steps_since_meaningful_improvement=0,
    )
    state_before = (
        evolver.current,
        evolver.strategy_history,
        evolver.activation_receipts,
    )

    score = evolver.score_window(
        "window-1",
        before,
        after,
        attempts_run=4,
        cost_spent=8.0,
    )

    assert 0.0 < score.observed_progress <= 1.0
    assert score.steps_since_meaningful_improvement == 0
    assert (
        evolver.current,
        evolver.strategy_history,
        evolver.activation_receipts,
    ) == state_before


def test_child_proposal_binds_to_current_policy_parent():
    evolver = SearchPolicyEvolver(make_parent())

    child = evolver.propose_child(
        make_policy(inspiration_selector=InspirationSelector.NONE),
        window_id="window-1",
        sequence=1,
    )

    assert child.parent_policy_id == evolver.current.policy_id
    assert child.campaign_id == evolver.current.campaign_id
    assert child.policy_hash != evolver.current.policy_hash

    # The visible parent reference follows SearchPolicySnapshot's policy-id
    # contract, while deterministic child identity also binds the exact parent
    # hash: equal parent IDs with different immutable parent content must yield
    # different child IDs.
    other_evolver = SearchPolicyEvolver(
        make_parent(policy=make_policy(max_concurrency=3))
    )
    other_child = other_evolver.propose_child(
        make_policy(inspiration_selector=InspirationSelector.NONE),
        window_id="window-1",
        sequence=1,
    )
    assert child.policy_id != other_child.policy_id


@pytest.mark.parametrize(
    ("failure_kind", "expected_outcome"),
    [
        ("schema", StrategyHistoryOutcome.REJECTED_SCHEMA),
        ("static", StrategyHistoryOutcome.REJECTED_STATIC),
        ("simulation", StrategyHistoryOutcome.REJECTED_SIMULATION),
    ],
)
def test_rejected_candidate_preserves_parent_and_appends_only_history(
    failure_kind,
    expected_outcome,
):
    evolver = SearchPolicyEvolver(make_parent())
    candidate = evolver.propose_child(
        make_policy(),
        window_id="window-1",
        sequence=1,
    )
    descriptor = make_descriptor()
    if failure_kind == "schema":
        candidate = candidate.model_copy(
            update={"policy": make_policy(max_width=3)}
        )
    elif failure_kind == "static":
        candidate = evolver.propose_child(
            make_policy(
                parent_selector=ParentSelector.ELITE,
                diversity_floor=0.0,
            ),
            window_id="window-1",
            sequence=1,
        )
    else:
        descriptor = make_descriptor(parent_selection_concentration=0.95)
    parent = evolver.current

    row = evolver.consider(candidate, window=descriptor, sequence=1)

    assert row.outcome is expected_outcome
    assert evolver.current == parent
    assert evolver.strategy_history == (row,)
    assert evolver.activation_receipts == ()


def test_shadow_rejection_preserves_parent_and_records_exact_stage(monkeypatch):
    evolver = SearchPolicyEvolver(make_parent())
    candidate = evolver.propose_child(
        make_policy(), window_id="window-1", sequence=1
    )
    descriptor = make_descriptor()
    passed = validate_policy(candidate, parent=evolver.current, window=descriptor)
    failed_shadow = PolicyValidationReceipt(
        policy_hash=candidate.policy_hash,
        stage=PolicyValidationStage.SHADOW,
        verdict=PolicyValidationVerdict.FAILED,
        reason="shadow failed: grants no activation authority",
        descriptor_hash=descriptor.descriptor_hash,
    )
    monkeypatch.setattr(
        evolution_module,
        "validate_policy",
        lambda candidate, *, parent, window: passed[:3] + (failed_shadow,),
    )
    parent = evolver.current

    row = evolver.consider(candidate, window=descriptor, sequence=1)

    assert row.outcome is StrategyHistoryOutcome.REJECTED_SHADOW
    assert evolver.current == parent
    assert evolver.activation_receipts == ()


def test_activation_then_rollback_to_activated_ancestor():
    evolver = SearchPolicyEvolver(make_parent())
    first_child = evolver.propose_child(
        make_policy(inspiration_selector=InspirationSelector.NONE),
        window_id="window-1",
        sequence=1,
    )
    first_row = evolver.consider(
        first_child, window=make_descriptor(), sequence=1
    )
    assert first_row.outcome is StrategyHistoryOutcome.ACTIVATED

    second_child = evolver.propose_child(
        first_child.policy,
        window_id="window-2",
        sequence=2,
    )
    second_row = evolver.consider(
        second_child,
        window=make_descriptor(window_id="window-2"),
        sequence=2,
    )
    assert second_row.outcome is StrategyHistoryOutcome.ACTIVATED

    with pytest.raises(ValueError):
        evolver.rollback(
            to_policy_hash="sha256:" + "9" * 64,
            sequence=3,
            reason="unknown policy must fail closed",
        )

    rollback_row = evolver.rollback(
        to_policy_hash=first_child.policy_hash,
        sequence=3,
        reason="restore stronger validated ancestor",
    )

    assert rollback_row.outcome is StrategyHistoryOutcome.ROLLED_BACK
    assert evolver.current == first_child
    assert evolver.strategy_history[-1] == rollback_row
    assert len(evolver.activation_receipts) == 2


@pytest.mark.parametrize(
    ("make", "hash_field"),
    [
        (make_window_score, "score_hash"),
        (make_history_row, "row_hash"),
        (make_activation_receipt, "activation_hash"),
    ],
)
def test_evolution_models_self_hash_and_reject_tampering(make, hash_field):
    model = make()
    assert getattr(model, hash_field).startswith("sha256:")

    tampered = model.model_dump(mode="json")
    tampered[hash_field] = "sha256:" + "9" * 64
    with pytest.raises(ValidationError):
        model.__class__(**tampered)


@pytest.mark.parametrize(
    "make",
    [make_window_score, make_history_row, make_activation_receipt],
)
def test_evolution_models_reject_authority_shaped_extra_fields(make):
    with pytest.raises(ValidationError):
        make(can_promote=True)


def test_identical_consider_sequences_have_identical_history_hashes():
    evolver_a = SearchPolicyEvolver(make_parent())
    evolver_b = SearchPolicyEvolver(make_parent())
    candidate_a = evolver_a.propose_child(
        make_policy(inspiration_selector=InspirationSelector.NONE),
        window_id="window-1",
        sequence=1,
    )
    candidate_b = evolver_b.propose_child(
        make_policy(inspiration_selector=InspirationSelector.NONE),
        window_id="window-1",
        sequence=1,
    )

    row_a = evolver_a.consider(
        candidate_a, window=make_descriptor(), sequence=1
    )
    row_b = evolver_b.consider(
        candidate_b, window=make_descriptor(), sequence=1
    )

    assert row_a.row_hash == row_b.row_hash
    assert evolver_a.strategy_history == evolver_b.strategy_history
    assert (
        evolver_a.activation_receipts[0].activation_hash
        == evolver_b.activation_receipts[0].activation_hash
    )


def test_consider_rejects_missing_shadow_receipt_without_activation(monkeypatch):
    evolver = SearchPolicyEvolver(make_parent())
    candidate = evolver.propose_child(
        make_policy(), window_id="window-1", sequence=1
    )
    descriptor = make_descriptor()
    passed = validate_policy(candidate, parent=evolver.current, window=descriptor)
    monkeypatch.setattr(
        evolution_module,
        "validate_policy",
        lambda candidate, *, parent, window: passed[:3],
    )
    parent = evolver.current

    row = evolver.consider(candidate, window=descriptor, sequence=1)

    assert row.outcome is StrategyHistoryOutcome.REJECTED_SHADOW
    assert evolver.current == parent
    assert evolver.activation_receipts == ()
