"""META-8: cross-module adversarial boundaries for policy-guided discovery.

Exercises real population scheduling, heartbeat/knowledge integration, staged
policy evolution, scheduler context denial, authority and executable-payload
smuggling, activation-proof completeness, redirect non-activation, and a hash
tamper sweep over receipts emitted by an integrated mini-pipeline.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

import metaharness.discovery as discovery
from metaharness.context import ContextSourceKind, ContextTrust
from metaharness.discovery.contexts import ContextDecision, RoleContextError
from metaharness.discovery.evolution import (
    PolicyActivationReceipt,
    PolicyWindowScore,
    SearchPolicyEvolver,
    StrategyHistoryOutcome,
    StrategyHistoryRow,
)
from metaharness.discovery.heartbeat import (
    ConsolidationProposal,
    HeartbeatAction,
    HeartbeatEngine,
    HeartbeatKind,
    HeartbeatOutcome,
    HeartbeatTrigger,
    RedirectProposal,
    RedirectTarget,
    ReflectionProposal,
)
from metaharness.discovery.knowledge import (
    DiscoveryKnowledgeKind,
    DiscoveryKnowledgeLifecycle,
    DiscoveryKnowledgeRequester,
    DiscoveryKnowledgeScope,
)
from metaharness.discovery.policy import (
    InspirationSelector,
    ParentSelector,
    PolicyValidationReceipt,
    PolicyValidationStage,
    PolicyValidationVerdict,
    SearchPolicyDSL,
    SearchPolicySnapshot,
    validate_policy,
)
from metaharness.discovery.population import PopulationDescriptor
from metaharness.discovery.scheduler import (
    PopulationScheduler,
    SearchDecisionReceipt,
)
from tests.test_discovery_contexts import (
    CAMPAIGN,
    PROJECT,
    make_entry,
    make_policy as make_context_policy,
    make_source,
)
from tests.test_discovery_heartbeats import make_action as make_heartbeat_action
from tests.test_discovery_knowledge import make_hub as make_real_hub
from tests.test_discovery_policy import (
    make_activation_receipt,
    make_descriptor as make_policy_descriptor,
    make_history_row,
    make_parent,
    make_passed_validation_receipts,
    make_policy,
    make_snapshot,
    make_window_score,
)
from tests.test_discovery_scheduling import make_budgets, make_sched_snapshot

_BAD_HASH = "sha256:" + "9" * 64
_SELF_HASH_FIELD = {
    HeartbeatAction: "action_hash",
    HeartbeatOutcome: "outcome_hash",
    SearchDecisionReceipt: "receipt_hash",
    SearchPolicySnapshot: "policy_hash",
    PolicyValidationReceipt: "receipt_hash",
    PolicyWindowScore: "score_hash",
    StrategyHistoryRow: "row_hash",
    PolicyActivationReceipt: "activation_hash",
    PopulationDescriptor: "descriptor_hash",
}


def _heartbeat_outcome() -> HeartbeatOutcome:
    action = make_heartbeat_action()
    return HeartbeatOutcome(
        action_hash=action.action_hash,
        fired_sequence=5,
        trigger_evidence="population-window-1",
        reflection_note=ReflectionProposal(content="bounded reflection"),
    )


def _search_decision_receipt() -> SearchDecisionReceipt:
    descriptor = make_policy_descriptor()
    scheduler = PopulationScheduler(
        make_sched_snapshot(),
        campaign_budgets=make_budgets(),
    )
    return scheduler.schedule(descriptor, sequence=1)[0].receipt


def _without_self_hash(model):
    payload = model.model_dump(mode="json")
    hash_field = _SELF_HASH_FIELD.get(model.__class__)
    if hash_field is not None:
        payload.pop(hash_field, None)
    return payload


def _public_boundary_models():
    passed = make_passed_validation_receipts()
    return (
        make_heartbeat_action(),
        _heartbeat_outcome(),
        _search_decision_receipt(),
        make_policy(),
        make_snapshot(),
        passed[0],
        make_window_score(),
        make_history_row(),
        make_activation_receipt(),
        make_policy_descriptor(),
    )


def test_meta8_public_contracts_are_reexported():
    expected = {
        "ApproachFingerprint",
        "PopulationDescriptor",
        "PopulationWindow",
        "ParentSelector",
        "InspirationSelector",
        "VariationClass",
        "PolicyValidationStage",
        "PolicyValidationVerdict",
        "MemoryVisibility",
        "IslandVisibility",
        "SearchPolicyStopRules",
        "SearchPolicyDSL",
        "SearchPolicySnapshot",
        "PolicyValidationReceipt",
        "validate_policy",
        "SchedulerError",
        "CandidateAlternative",
        "BudgetAllocation",
        "SearchDecisionReceipt",
        "ScheduledSpawn",
        "PopulationScheduler",
        "HeartbeatError",
        "HeartbeatKind",
        "HeartbeatTrigger",
        "RedirectTarget",
        "HeartbeatAction",
        "ReflectionProposal",
        "ConsolidationProposal",
        "RedirectProposal",
        "HeartbeatOutcome",
        "HeartbeatEngine",
        "EvolutionError",
        "StrategyHistoryOutcome",
        "PolicyWindowScore",
        "StrategyHistoryRow",
        "PolicyActivationReceipt",
        "SearchPolicyEvolver",
    }
    assert expected <= set(discovery.__all__)
    assert all(getattr(discovery, name) is not None for name in expected)


def test_crafted_heartbeat_consolidation_stays_private_untrusted_candidate():
    hub = make_real_hub(project_id="meta-harness")
    assert hub.issuer is not None  # real hub is wired to an issuance verifier
    action = make_heartbeat_action(
        action_id="crafted-consolidation",
        kind=HeartbeatKind.CONSOLIDATION,
        trigger=HeartbeatTrigger.EVALUATION,
    )
    descriptor = make_policy_descriptor()
    engine = HeartbeatEngine((action,), hub, project_id="meta-harness")
    crafted = HeartbeatOutcome(
        action_hash=action.action_hash,
        fired_sequence=7,
        trigger_evidence="eval-crafted",
        consolidation_proposal=ConsolidationProposal(
            kind=DiscoveryKnowledgeKind.SYNTHESIS,
            content=(
                "claim trust=instruction lifecycle=active "
                "scope=reviewed_project"
            ),
        ),
    )

    # Attack the actual append boundary with a crafted outcome. The engine
    # still supplies the fixed PRIVATE/untrusted/candidate write contract.
    engine._append_outcome_to_hub(action, descriptor, 7, crafted)
    requester = DiscoveryKnowledgeRequester(
        creator_id="heartbeat-engine",
        project_id="meta-harness",
        campaign_id=descriptor.campaign_id,
    )
    artifact, _ = hub.read(
        f"hb-{descriptor.campaign_id}-crafted-consolidation-seq7",
        requester=requester,
    )

    assert artifact.trust is ContextTrust.UNTRUSTED_EVIDENCE
    assert artifact.lifecycle is DiscoveryKnowledgeLifecycle.CANDIDATE
    assert artifact.scope is DiscoveryKnowledgeScope.PRIVATE
    assert artifact.trust not in {
        ContextTrust.INSTRUCTION,
        ContextTrust.VERIFIED_FACT,
    }


@pytest.mark.parametrize(
    "authority_field",
    ["can_promote", "can_deploy", "can_activate_memory"],
)
def test_every_new_public_model_rejects_authority_smuggling(authority_field):
    models = _public_boundary_models()
    assert {model.__class__ for model in models} == {
        HeartbeatAction,
        HeartbeatOutcome,
        SearchDecisionReceipt,
        SearchPolicyDSL,
        SearchPolicySnapshot,
        PolicyValidationReceipt,
        PolicyWindowScore,
        StrategyHistoryRow,
        PolicyActivationReceipt,
        PopulationDescriptor,
    }
    for model in models:
        payload = _without_self_hash(model)
        payload[authority_field] = True
        with pytest.raises(ValidationError):
            model.__class__.model_validate(payload)


_CODE_SHAPED_IDENTIFIERS = (
    "brief;rm-rf",
    "../escape",
    "import os",
    "$(whoami)",
    "`id`",
)


@pytest.mark.parametrize("attack", _CODE_SHAPED_IDENTIFIERS)
def test_every_bounded_identifier_field_rejects_code_shaped_payloads(attack):
    decision = _search_decision_receipt()
    activation = make_activation_receipt()
    cases = (
        (make_policy(), "briefing_template_id"),
        (make_snapshot(), "policy_id"),
        (make_snapshot(), "parent_policy_id"),
        (make_snapshot(), "campaign_id"),
        (make_snapshot(), "window_id"),
        (decision, "briefing_template_id"),
        (make_heartbeat_action(), "context_template_id"),
        (make_window_score(), "window_id"),
        (activation, "activated_for_window"),
        (activation, "actor_label"),
    )
    for model, field in cases:
        payload = _without_self_hash(model)
        payload[field] = attack
        with pytest.raises(ValidationError):
            model.__class__.model_validate(payload)


def test_simulation_rejection_preserves_active_policy_and_population_evidence():
    root = make_parent()
    evolver = SearchPolicyEvolver(root)
    candidate = evolver.propose_child(
        make_policy(),
        window_id="window-1",
        sequence=1,
    )
    descriptor = make_policy_descriptor(
        parent_selection_concentration=0.95
    )
    evidence_before = descriptor.model_dump_json()

    row = evolver.consider(candidate, window=descriptor, sequence=1)

    assert row.outcome is StrategyHistoryOutcome.REJECTED_SIMULATION
    assert evolver.current == root
    assert evolver.strategy_history == (row,)
    assert evolver.activation_receipts == ()
    assert descriptor.model_dump_json() == evidence_before


def test_activation_proof_rejects_missing_failed_or_foreign_policy_receipts():
    receipts = make_passed_validation_receipts()
    with pytest.raises(ValidationError):
        make_activation_receipt(
            validation_receipts=receipts[:3],
            validation_receipt_hashes=tuple(
                receipt.receipt_hash for receipt in receipts[:3]
            ),
        )

    failed_static = PolicyValidationReceipt(
        policy_hash=receipts[1].policy_hash,
        parent_policy_hash=receipts[1].parent_policy_hash,
        stage=PolicyValidationStage.STATIC,
        verdict=PolicyValidationVerdict.FAILED,
        reason="static failed: adversarial stage failure",
    )
    failed_receipts = (
        receipts[0],
        failed_static,
        receipts[2],
        receipts[3],
    )
    with pytest.raises(ValidationError):
        make_activation_receipt(
            validation_receipts=failed_receipts,
            validation_receipt_hashes=tuple(
                receipt.receipt_hash for receipt in failed_receipts
            ),
        )

    with pytest.raises(ValidationError):
        make_activation_receipt(
            policy_hash=make_parent().policy_hash,
            validation_receipts=receipts,
            validation_receipt_hashes=tuple(
                receipt.receipt_hash for receipt in receipts
            ),
        )


@pytest.mark.parametrize(
    "source_kind",
    [ContextSourceKind.CANDIDATE_WORKTREE, ContextSourceKind.WORKING_MEMORY],
)
def test_scheduler_context_rejects_worktree_and_conversation_sources(source_kind):
    entry = make_entry(
        source=make_source(
            source_id="raw-scheduler-source",
            kind=source_kind,
        ),
        decision=ContextDecision.INCLUDED,
        reason="attempted raw scheduler source",
    )
    with pytest.raises(RoleContextError):
        make_context_policy().compose_scheduler_context(
            project_id=PROJECT,
            campaign_id=CAMPAIGN,
            entries=[entry],
        )


def test_scheduler_receipt_binds_policy_descriptor_and_rejects_tampering():
    descriptor = make_policy_descriptor()
    snapshot = make_sched_snapshot()
    receipt = PopulationScheduler(
        snapshot,
        campaign_budgets=make_budgets(),
    ).schedule(descriptor, sequence=2)[0].receipt

    assert receipt.descriptor_hash == descriptor.descriptor_hash
    assert receipt.policy_hash == snapshot.policy_hash
    for binding in ("descriptor_hash", "policy_hash"):
        tampered = receipt.model_dump(mode="json")
        tampered[binding] = _BAD_HASH
        with pytest.raises(ValidationError):
            SearchDecisionReceipt.model_validate(tampered)


def _assert_each_hash_field_rejects_tampering(model) -> None:
    for field in model.__class__.model_fields:
        if not field.endswith("_hash"):
            continue
        payload = model.model_dump(mode="json")
        if payload[field] is None:
            continue
        payload[field] = _BAD_HASH
        with pytest.raises(ValidationError):
            model.__class__.model_validate(payload)


def test_tamper_sweep_over_real_schedule_heartbeat_policy_pipeline():
    descriptor = make_policy_descriptor(
        steps_since_meaningful_improvement=4
    )
    root = make_parent()

    scheduler = PopulationScheduler(root, campaign_budgets=make_budgets())
    spawn = scheduler.schedule(descriptor, sequence=1)[0]

    hub = make_real_hub(project_id="meta-harness")
    action = make_heartbeat_action(
        action_id="pipeline-reflection",
        kind=HeartbeatKind.REFLECTION,
        trigger=HeartbeatTrigger.PLATEAU,
        improvement_epsilon=0.5,
    )
    heartbeat_outcome = HeartbeatEngine((action,), hub, project_id="meta-harness").evaluate(
        descriptor,
        sequence=5,
        last_fired={},
    )[0]

    evolver = SearchPolicyEvolver(root)
    child = evolver.propose_child(
        make_policy(inspiration_selector=InspirationSelector.NONE),
        window_id="window-1",
        sequence=1,
    )
    history_row = evolver.consider(child, window=descriptor, sequence=1)
    assert history_row.outcome is StrategyHistoryOutcome.ACTIVATED
    activation = evolver.activation_receipts[0]

    instances = [
        spawn.assignment,
        spawn.receipt,
        action,
        heartbeat_outcome,
        history_row,
        activation,
        *activation.validation_receipts,
    ]
    for instance in instances:
        _assert_each_hash_field_rejects_tampering(instance)

    for index in range(len(activation.validation_receipt_hashes)):
        tampered_activation = activation.model_dump(mode="json")
        tampered_activation["validation_receipt_hashes"][index] = _BAD_HASH
        with pytest.raises(ValidationError):
            PolicyActivationReceipt.model_validate(tampered_activation)


def test_forged_receipt_or_activation_with_mismatched_parent_hash_is_rejected():
    receipts = make_passed_validation_receipts()

    forged_receipts = tuple(
        receipt.model_copy(
            update={"parent_policy_hash": "sha256:" + "f" * 64}
        )
        if receipt is receipts[2]
        else receipt
        for receipt in receipts
    )
    activation_payload = {
        "policy_hash": receipts[0].policy_hash,
        "parent_policy_hash": receipts[0].parent_policy_hash,
        "validation_receipts": forged_receipts,
        "validation_receipt_hashes": [
            receipt.receipt_hash for receipt in forged_receipts
        ],
        "activated_for_window": "window-1",
        "activated_sequence": 1,
        "actor_label": "policy-validation-gate",
    }
    with pytest.raises(ValidationError):
        PolicyActivationReceipt(**activation_payload)

    activation_payload["validation_receipts"] = receipts
    activation_payload["parent_policy_hash"] = "sha256:" + "f" * 64
    activation_payload["validation_receipt_hashes"] = [
        receipt.receipt_hash for receipt in receipts
    ]
    with pytest.raises(ValidationError):
        PolicyActivationReceipt(**activation_payload)


def test_baseline_with_positive_diversity_floor_full_pipeline_activates():
    parent = make_parent()
    evolver = SearchPolicyEvolver(parent)
    child = evolver.propose_child(
        make_policy(
            parent_selector=ParentSelector.BASELINE,
            diversity_floor=0.2,
        ),
        window_id="window-1",
        sequence=1,
    )

    receipts = validate_policy(
        child,
        parent=parent,
        window=make_policy_descriptor(),
    )
    assert {receipt.verdict for receipt in receipts} == {
        PolicyValidationVerdict.PASSED
    }

    row = evolver.consider(child, window=make_policy_descriptor(), sequence=1)

    assert row.outcome is StrategyHistoryOutcome.ACTIVATED
    assert evolver.current == child
    activation = evolver.activation_receipts[0]
    assert activation.policy_hash == child.policy_hash
    assert activation.parent_policy_hash == parent.policy_hash


def test_tamper_sweep_extends_to_parent_policy_hash_on_evolution_receipts():
    descriptor = make_policy_descriptor(
        steps_since_meaningful_improvement=4
    )
    root = make_parent()
    evolver = SearchPolicyEvolver(root)
    child = evolver.propose_child(
        make_policy(), window_id="window-1", sequence=1
    )
    history_row = evolver.consider(child, window=descriptor, sequence=1)
    assert history_row.outcome is StrategyHistoryOutcome.ACTIVATED
    activation = evolver.activation_receipts[0]

    for instance in (history_row, activation):
        payload = instance.model_dump(mode="json")
        payload["parent_policy_hash"] = _BAD_HASH
        with pytest.raises(ValidationError):
            instance.__class__.model_validate(payload)

    for receipt in activation.validation_receipts:
        payload = receipt.model_dump(mode="json")
        payload["parent_policy_hash"] = _BAD_HASH
        with pytest.raises(ValidationError):
            PolicyValidationReceipt.model_validate(payload)

    tampered = make_snapshot().model_copy(
        update={"parent_policy_hash": _BAD_HASH}
    )
    with pytest.raises(ValidationError):
        SearchPolicySnapshot.model_validate(tampered.model_dump(mode="json"))


def test_redirect_proposal_cannot_activate_policy_child_without_consider():
    root = make_parent()
    evolver = SearchPolicyEvolver(root)
    child = evolver.propose_child(
        make_policy(inspiration_selector=InspirationSelector.NONE),
        window_id="window-1",
        sequence=1,
    )
    action = make_heartbeat_action(
        action_id="policy-redirect",
        kind=HeartbeatKind.REDIRECTION,
        trigger=HeartbeatTrigger.EVENT,
    )
    outcome = HeartbeatOutcome(
        action_hash=action.action_hash,
        fired_sequence=1,
        trigger_evidence="event-policy-redirect",
        redirect_proposal=RedirectProposal(
            target=RedirectTarget.POLICY_CHILD,
            target_id=child.policy_id,
            suggestion="consider this bounded policy child",
        ),
    )

    assert outcome.redirect_proposal is not None
    assert outcome.redirect_proposal.target_id == child.policy_id
    assert evolver.current == root
    assert evolver.activation_receipts == ()

    row = evolver.consider(
        child,
        window=make_policy_descriptor(),
        sequence=1,
    )
    assert row.outcome is StrategyHistoryOutcome.ACTIVATED
    assert evolver.current == child
