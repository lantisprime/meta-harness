"""Stage-3 tests for the shadow MemoryActionBroker + deterministic LOG/CONSULT.

These tests are additive and exercise the contract the build spec describes for
Stage 3: vocabulary enforcement, scope guards, immutable self-verifying
receipts, determinism, lifecycle-bypass + domain-action rejection, and the
scaffold-only LOG/CONSULT round-trip. They complement (not replace) the
strict-xfail corpus in ``tests/adversarial/test_memory_skill_boundaries.py``.
"""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from metaharness.context import ContextScope, ContextSourceKind, Sensitivity
from metaharness.context.models import content_hash
from metaharness.memory import (
    ActivationState,
    EpisodicMemoryStore,
    LifecycleState,
    MemoryAction,
    MemoryActionBroker,
    MemoryActionOutcome,
    MemoryActionReceipt,
    MemoryCognitiveSkillSnapshot,
    MemoryKind,
    MemoryOperation,
    MemoryPhase,
    MemoryPhaseContract,
    SemanticMemoryStore,
    WorkingMemoryStore,
    normalize_text,
)
from metaharness.memory.scaffold import (
    consult_memory,
    log_observation,
)


# -- helpers -------------------------------------------------------------------


def _snapshot(**changes):
    base = {
        "snapshot_id": "snap-1",
        "skill_id": "shadow-skill",
        "scope": ContextScope(project_id="meta-harness"),
        "goal_families": ("retrieval",),
        "roles": ("builder",),
    }
    base.update(changes)
    return MemoryCognitiveSkillSnapshot(**base)


def _stores():
    return {
        MemoryKind.WORKING_MEMORY.value: WorkingMemoryStore(clock=lambda: 0),
        MemoryKind.EPISODIC_MEMORY.value: EpisodicMemoryStore(clock=lambda: 0),
        MemoryKind.SEMANTIC_MEMORY.value: SemanticMemoryStore(clock=lambda: 0),
    }


# -- vocabulary and phase contract enforcement ---------------------------------


def test_snapshot_contains_all_phase_contracts_with_disjoint_vocabularies():
    snapshot = _snapshot()
    phases = [contract.phase for contract in snapshot.phase_contracts]
    assert set(phases) == set(MemoryPhase)
    consult_ops = set(snapshot.operations_for(MemoryPhase.CONSULT))
    log_ops = set(snapshot.operations_for(MemoryPhase.LOG))
    maintain_ops = set(snapshot.operations_for(MemoryPhase.MAINTAIN))
    assert consult_ops == {MemoryOperation.SEARCH, MemoryOperation.READ}
    assert log_ops.issubset(maintain_ops)
    assert consult_ops.isdisjoint(log_ops)


def test_snapshot_phase_contract_rejects_duplicate_phase():
    base = _snapshot()
    extra = MemoryPhaseContract(
        phase=MemoryPhase.CONSULT,
        allowed_operations=(MemoryOperation.SEARCH,),
        declaration="duplicate consult",
    )
    contracts = list(base.phase_contracts) + [extra]
    with pytest.raises(ValidationError):
        MemoryCognitiveSkillSnapshot(
            snapshot_id="snap-dup",
            skill_id="shadow-skill",
            scope=ContextScope(project_id="meta-harness"),
            goal_families=("retrieval",),
            roles=("builder",),
            phase_contracts=tuple(contracts),
        )


def test_broker_rejects_out_of_vocabulary_action_with_receipt():
    restricted = MemoryPhaseContract(
        phase=MemoryPhase.CONSULT,
        allowed_operations=(MemoryOperation.READ,),
        declaration="consult restricted",
    )
    log_contract = MemoryPhaseContract(
        phase=MemoryPhase.LOG,
        allowed_operations=(
            MemoryOperation.CREATE_CANDIDATE,
            MemoryOperation.APPEND,
            MemoryOperation.LINK,
        ),
        declaration="log writes",
    )
    maintain_contract = MemoryPhaseContract(
        phase=MemoryPhase.MAINTAIN,
        allowed_operations=tuple(MemoryOperation),
        declaration="maintain all",
    )
    contracts = (log_contract, restricted, maintain_contract)
    snapshot = MemoryCognitiveSkillSnapshot(
        snapshot_id="snap-restricted",
        skill_id="shadow-skill",
        scope=ContextScope(project_id="meta-harness"),
        goal_families=("retrieval",),
        roles=("builder",),
        phase_contracts=contracts,
    )
    broker = MemoryActionBroker(snapshot=snapshot, stores=_stores(), clock=lambda: 0)
    action = MemoryAction(
        operation=MemoryOperation.SEARCH,
        phase=MemoryPhase.CONSULT,
        scope=snapshot.scope,
        payload={"query": "anything"},
    )
    receipt = broker.invoke(action)
    assert receipt.outcome is MemoryActionOutcome.REJECTED
    assert "phase contract" in receipt.effect_or_rejection_reason
    assert "policy:rejected" in receipt.validation_results


def test_broker_rejects_unknown_operation_with_receipt():
    broker = MemoryActionBroker(snapshot=_snapshot(), stores=_stores(), clock=lambda: 0)
    receipt = broker.invoke(
        {
            "operation": "make_coffee",
            "phase": "consult",
            "scope": ContextScope(project_id="meta-harness"),
            "payload": {"query": "x"},
        }
    )
    assert receipt.outcome is MemoryActionOutcome.REJECTED
    assert receipt.operation == "make_coffee"
    assert receipt.accepted is False
    assert "unknown memory operation" in receipt.effect_or_rejection_reason


# -- scope guards --------------------------------------------------------------


def test_broker_rejects_action_outside_snapshot_scope():
    broker = MemoryActionBroker(snapshot=_snapshot(), stores=_stores(), clock=lambda: 0)
    action = MemoryAction(
        operation=MemoryOperation.SEARCH,
        phase=MemoryPhase.CONSULT,
        scope=ContextScope(project_id="other-project"),
        payload={"query": "anything"},
    )
    receipt = broker.invoke(action)
    assert receipt.outcome is MemoryActionOutcome.REJECTED
    assert "scope" in receipt.effect_or_rejection_reason


def test_broker_rejects_path_traversal_in_query():
    broker = MemoryActionBroker(snapshot=_snapshot(), stores=_stores(), clock=lambda: 0)
    action = MemoryAction(
        operation=MemoryOperation.SEARCH,
        phase=MemoryPhase.CONSULT,
        scope=ContextScope(project_id="meta-harness"),
        payload={"query": "../etc/passwd"},
    )
    receipt = broker.invoke(action)
    assert receipt.outcome is MemoryActionOutcome.REJECTED
    assert "traversal" in receipt.effect_or_rejection_reason


def test_broker_rejects_path_traversal_in_payload_nested():
    broker = MemoryActionBroker(snapshot=_snapshot(), stores=_stores(), clock=lambda: 0)
    receipt = broker.invoke(
        MemoryAction(
            operation=MemoryOperation.SEARCH,
            phase=MemoryPhase.CONSULT,
            scope=ContextScope(project_id="meta-harness"),
            payload={"query": "ok", "filters": {"path": "..\\..\\etc"}},
        )
    )
    assert receipt.outcome is MemoryActionOutcome.REJECTED


def test_broker_rejects_cross_scope_record_lookup():
    store_a = EpisodicMemoryStore(clock=lambda: 0)
    record = store_a.commit(
        kind="episodic_memory",
        content="project-a only",
        scope=ContextScope(project_id="project-a", run_id="r1"),
    )
    snapshot = _snapshot(scope=ContextScope(project_id="meta-harness"))
    broker = MemoryActionBroker(snapshot=snapshot, stores={"project-a": store_a}, clock=lambda: 0)
    receipt = broker.invoke(
        MemoryAction(
            operation=MemoryOperation.READ,
            phase=MemoryPhase.CONSULT,
            scope=snapshot.scope,
            payload={"record_ids": [record.id]},
        )
    )
    assert receipt.outcome is MemoryActionOutcome.REJECTED
    assert "cross-scope" in receipt.effect_or_rejection_reason


def test_broker_rejects_writes_to_immutable_evidence_kind():
    broker = MemoryActionBroker(snapshot=_snapshot(), stores=_stores(), clock=lambda: 0)
    receipt = broker.invoke(
        MemoryAction(
            operation=MemoryOperation.CREATE_CANDIDATE,
            phase=MemoryPhase.LOG,
            scope=ContextScope(project_id="meta-harness"),
            payload={
                "kind": ContextSourceKind.IMMUTABLE_ARTIFACT.value,
                "content": "evidence write attempt",
            },
        )
    )
    assert receipt.outcome is MemoryActionOutcome.REJECTED
    assert "immutable evidence" in receipt.effect_or_rejection_reason


def test_broker_rejects_write_to_existing_non_candidate_record():
    snapshot = _snapshot()
    store = EpisodicMemoryStore(clock=lambda: 0)
    record = store.commit(
        kind="episodic_memory",
        content="promoted fact",
        lifecycle_state=LifecycleState.ACTIVE,
        activation_state=ActivationState.ACTIVE,
    )
    broker = MemoryActionBroker(
        snapshot=snapshot,
        stores={MemoryKind.EPISODIC_MEMORY.value: store},
        clock=lambda: 0,
    )
    receipt = broker.invoke(
        MemoryAction(
            operation=MemoryOperation.REVISE_CANDIDATE,
            phase=MemoryPhase.MAINTAIN,
            scope=snapshot.scope,
            payload={
                "target_record_id": record.id,
                "content": "re-write attempt",
            },
        )
    )
    assert receipt.outcome is MemoryActionOutcome.REJECTED
    assert "immutable evidence" in receipt.effect_or_rejection_reason


# -- domain-task action rejection ---------------------------------------------


def test_broker_rejects_domain_task_action_even_within_allowlist():
    log_contract = MemoryPhaseContract(
        phase=MemoryPhase.LOG,
        allowed_operations=(MemoryOperation.CREATE_CANDIDATE,),
        declaration="log only",
    )
    consult_contract = MemoryPhaseContract(
        phase=MemoryPhase.CONSULT,
        allowed_operations=(MemoryOperation.SEARCH,),
        declaration="consult only",
    )
    maintain_contract = MemoryPhaseContract(
        phase=MemoryPhase.MAINTAIN,
        allowed_operations=(MemoryOperation.UPSERT,),
        declaration="maintain only",
    )
    snapshot = MemoryCognitiveSkillSnapshot(
        snapshot_id="snap-deploy-attempt",
        skill_id="shadow-skill",
        scope=ContextScope(project_id="meta-harness"),
        goal_families=("retrieval",),
        roles=("builder",),
        allowed_actions=(
            MemoryOperation.SEARCH,
            MemoryOperation.CREATE_CANDIDATE,
            MemoryOperation.UPSERT,
        ),
        phase_contracts=(log_contract, consult_contract, maintain_contract),
    )
    broker = MemoryActionBroker(snapshot=snapshot, stores=_stores(), clock=lambda: 0)
    receipt = broker.invoke(
        {
            "operation": "deploy",
            "phase": "maintain",
            "scope": snapshot.scope,
            "payload": {"kind": MemoryKind.WORKING_MEMORY.value, "content": "x"},
        }
    )
    assert receipt.outcome is MemoryActionOutcome.REJECTED
    assert "domain task actions" in receipt.effect_or_rejection_reason


def test_broker_rejects_payload_carrying_task_action_marker():
    broker = MemoryActionBroker(snapshot=_snapshot(), stores=_stores(), clock=lambda: 0)
    receipt = broker.invoke(
        MemoryAction(
            operation=MemoryOperation.CREATE_CANDIDATE,
            phase=MemoryPhase.LOG,
            scope=ContextScope(project_id="meta-harness"),
            payload={
                "kind": MemoryKind.WORKING_MEMORY.value,
                "content": "candidate text",
                "task_action": "write_test",
            },
        )
    )
    assert receipt.outcome is MemoryActionOutcome.REJECTED
    assert "domain task actions" in receipt.effect_or_rejection_reason


# -- lifecycle bypass → proposal -----------------------------------------------


def test_broker_converts_lifecycle_bypass_into_reviewable_proposal():
    snapshot = _snapshot()
    store = EpisodicMemoryStore(clock=lambda: 0)
    record = store.commit(
        kind="episodic_memory",
        content="to be activated",
        lifecycle_state=LifecycleState.CANDIDATE,
    )
    broker = MemoryActionBroker(
        snapshot=snapshot,
        stores={MemoryKind.EPISODIC_MEMORY.value: store},
        clock=lambda: 0,
    )
    receipt = broker.invoke(
        MemoryAction(
            operation=MemoryOperation.UPSERT,
            phase=MemoryPhase.MAINTAIN,
            scope=snapshot.scope,
            payload={
                "target_record_id": record.id,
                "content": record.content,
                "lifecycle_state": LifecycleState.ACTIVE.value,
            },
        )
    )
    assert receipt.outcome is MemoryActionOutcome.PROPOSED
    assert receipt.accepted is False
    assert receipt.proposal_ids
    proposal = broker.proposals[-1]
    assert proposal.proposal_kind.value in {"activate", "tombstone", "expiry"}
    assert proposal.target_record_ids == (record.id,)


def test_broker_rejects_direct_tombstone_as_proposal():
    broker = MemoryActionBroker(snapshot=_snapshot(), stores=_stores(), clock=lambda: 0)
    receipt = broker.invoke(
        {
            "operation": "tombstone",
            "phase": "log",
            "scope": ContextScope(project_id="meta-harness"),
            "payload": {"record_ids": ["ghost"]},
        }
    )
    assert receipt.outcome is MemoryActionOutcome.PROPOSED


# -- receipt immutability + self-verifying hash --------------------------------


def test_receipt_content_hash_recomputes_from_other_fields_and_survives_tamper():
    broker = MemoryActionBroker(snapshot=_snapshot(), stores=_stores(), clock=lambda: 0)
    receipt = broker.invoke(
        MemoryAction(
            operation=MemoryOperation.SEARCH,
            phase=MemoryPhase.CONSULT,
            scope=ContextScope(project_id="meta-harness"),
            payload={"query": "nothing-yet"},
        )
    )
    material = receipt.model_dump(mode="json", exclude={"content_hash"})
    assert receipt.content_hash == content_hash(material)
    tampered = json.loads(receipt.model_dump_json())
    tampered["effect_or_rejection_reason"] = "tampered reason"
    tampered["content_hash"] = "sha256:" + "0" * 64
    with pytest.raises(ValidationError):
        MemoryActionReceipt.model_validate(tampered)


def test_receipt_is_frozen_and_rejects_in_place_mutation():
    broker = MemoryActionBroker(snapshot=_snapshot(), stores=_stores(), clock=lambda: 0)
    receipt = broker.invoke(
        MemoryAction(
            operation=MemoryOperation.SEARCH,
            phase=MemoryPhase.CONSULT,
            scope=ContextScope(project_id="meta-harness"),
            payload={"query": "frozen"},
        )
    )
    with pytest.raises(ValidationError):
        receipt.effect_or_rejection_reason = "tampered"  # type: ignore[misc]


def test_receipt_outcome_disagrees_with_accepted_raises():
    snapshot = _snapshot()
    broker = MemoryActionBroker(snapshot=snapshot, stores=_stores(), clock=lambda: 0)
    receipt = broker.invoke(
        MemoryAction(
            operation=MemoryOperation.SEARCH,
            phase=MemoryPhase.CONSULT,
            scope=snapshot.scope,
            payload={"query": "ok"},
        )
    )
    payload = json.loads(receipt.model_dump_json())
    payload["accepted"] = False
    payload["outcome"] = "accepted"
    payload["content_hash"] = "sha256:" + "0" * 64
    with pytest.raises(ValidationError):
        MemoryActionReceipt.model_validate(payload)


def test_receipt_proposed_outcome_requires_proposal_ids():
    snapshot = _snapshot()
    broker = MemoryActionBroker(snapshot=snapshot, stores=_stores(), clock=lambda: 0)
    receipt = broker.invoke(
        MemoryAction(
            operation=MemoryOperation.SEARCH,
            phase=MemoryPhase.CONSULT,
            scope=snapshot.scope,
            payload={"query": "ok"},
        )
    )
    payload = json.loads(receipt.model_dump_json())
    payload["outcome"] = "proposed"
    payload["accepted"] = False
    payload["proposal_ids"] = []
    payload["content_hash"] = "sha256:" + "0" * 64
    with pytest.raises(ValidationError):
        MemoryActionReceipt.model_validate(payload)


# -- determinism ---------------------------------------------------------------


def test_broker_is_deterministic_with_injected_clock_and_factories():
    def _build(observed_seed: int) -> MemoryActionReceipt:
        counter = iter([10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28])
        store = EpisodicMemoryStore(clock=lambda: next(counter))
        store.commit(
            kind="episodic_memory",
            content="alpha one",
            observed_at=10,
            creator_id="skill",
        )
        store.commit(
            kind="episodic_memory",
            content="alpha two",
            observed_at=11,
            creator_id="skill",
        )
        broker = MemoryActionBroker(
            snapshot=_snapshot(),
            stores={MemoryKind.EPISODIC_MEMORY.value: store},
            clock=lambda: next(counter),
            receipt_id_factory=lambda: f"r-{observed_seed}",
            proposal_id_factory=lambda: f"p-{observed_seed}",
        )
        return broker.invoke(
            MemoryAction(
                operation=MemoryOperation.SEARCH,
                phase=MemoryPhase.CONSULT,
                scope=ContextScope(project_id="meta-harness"),
                payload={"query": "alpha", "limit": 5},
            )
        )

    first = _build(1)
    second = _build(1)
    assert first.model_dump() == second.model_dump()
    assert first.content_hash == second.content_hash
    assert first.selected_targets == second.selected_targets
    # Different receipt_id factory values still yield different receipts.
    different = _build(2)
    assert different.content_hash != first.content_hash


def test_ranking_is_stable_across_runs_with_identical_inputs():
    store = EpisodicMemoryStore(clock=lambda: 0)
    # Identical normalized text but different ids; ranking must be deterministic.
    for index in range(5):
        store.commit(
            kind="episodic_memory",
            content=f"deterministic ordering #{index}",
        )
    broker = MemoryActionBroker(snapshot=_snapshot(), stores={"memory": store}, clock=lambda: 0)
    a = broker.invoke(
        MemoryAction(
            operation=MemoryOperation.SEARCH,
            phase=MemoryPhase.CONSULT,
            scope=ContextScope(project_id="meta-harness"),
            payload={"query": "deterministic", "limit": 5},
        )
    )
    b = broker.invoke(
        MemoryAction(
            operation=MemoryOperation.SEARCH,
            phase=MemoryPhase.CONSULT,
            scope=ContextScope(project_id="meta-harness"),
            payload={"query": "deterministic", "limit": 5},
        )
    )
    assert a.selected_targets == b.selected_targets
    assert a.considered_targets == b.considered_targets


# -- redaction + sensitivity ----------------------------------------------------


def test_broker_redaction_rejects_forbidden_payload_keys():
    broker = MemoryActionBroker(snapshot=_snapshot(), stores=_stores(), clock=lambda: 0)
    receipt = broker.invoke(
        MemoryAction(
            operation=MemoryOperation.CREATE_CANDIDATE,
            phase=MemoryPhase.LOG,
            scope=ContextScope(project_id="meta-harness"),
            payload={
                "kind": MemoryKind.WORKING_MEMORY.value,
                "content": "draft",
                "metadata": {"api_key": "secret-key"},
            },
        )
    )
    assert receipt.outcome is MemoryActionOutcome.REJECTED
    assert "redaction violation" in receipt.effect_or_rejection_reason
    assert "redaction:rejected" in receipt.redaction_results


def test_broker_redaction_rejects_sensitivity_outside_policy():
    snapshot = _snapshot(allowed_sensitivities=(Sensitivity.PUBLIC,))
    broker = MemoryActionBroker(snapshot=snapshot, stores=_stores(), clock=lambda: 0)
    receipt = broker.invoke(
        MemoryAction(
            operation=MemoryOperation.CREATE_CANDIDATE,
            phase=MemoryPhase.LOG,
            scope=ContextScope(project_id="meta-harness"),
            payload={
                "kind": MemoryKind.WORKING_MEMORY.value,
                "content": "sensitive",
                "sensitivity": Sensitivity.RESTRICTED.value,
            },
        )
    )
    assert receipt.outcome is MemoryActionOutcome.REJECTED
    assert "sensitivity" in receipt.effect_or_rejection_reason


# -- LOG/CONSULT scaffold round-trip -------------------------------------------


def test_scaffold_log_appends_candidate_and_emits_accepted_receipt():
    broker = MemoryActionBroker(snapshot=_snapshot(), stores=_stores(), clock=lambda: 0)
    receipt = log_observation(
        broker=broker,
        scope=ContextScope(project_id="meta-harness"),
        content="first observation about the regression",
        kind=MemoryKind.WORKING_MEMORY,
        source_record_ids=(),
        confidence=0.9,
    )
    assert receipt.outcome is MemoryActionOutcome.ACCEPTED
    assert receipt.selected_targets
    record_id = receipt.selected_targets[0]
    listed = broker._stores[MemoryKind.WORKING_MEMORY.value].get(record_id)
    assert listed is not None
    assert listed.lifecycle_state is LifecycleState.CANDIDATE
    assert listed.creator_id == "shadow-skill"


def test_scaffold_consult_returns_deterministic_top_results():
    store = EpisodicMemoryStore(clock=lambda: 0)
    for index in range(3):
        store.commit(
            kind="episodic_memory",
            content=f"redteam corpus entry {index} alpha",
        )
    store.commit(
        kind="episodic_memory",
        content="non-matching reference observation",
    )
    broker = MemoryActionBroker(snapshot=_snapshot(), stores={"memory": store}, clock=lambda: 0)
    receipt = consult_memory(
        broker=broker,
        scope=ContextScope(project_id="meta-harness"),
        query="alpha",
        limit=2,
    )
    assert receipt.outcome is MemoryActionOutcome.ACCEPTED
    assert len(receipt.selected_targets) == 2
    assert receipt.considered_targets[:3] == receipt.considered_targets[:3]


def test_scaffold_log_consult_round_trip_is_deterministic():
    def _run():
        store = EpisodicMemoryStore(clock=lambda: 0)
        broker = MemoryActionBroker(snapshot=_snapshot(), stores={"memory": store}, clock=lambda: 0)
        log_receipt = log_observation(
            broker=broker,
            scope=ContextScope(project_id="meta-harness"),
            content="deterministic log round trip",
        )
        consult_receipt = consult_memory(
            broker=broker,
            scope=ContextScope(project_id="meta-harness"),
            query="round trip",
        )
        return log_receipt, consult_receipt

    a_log, a_consult = _run()
    b_log, b_consult = _run()
    assert a_log.content_hash == b_log.content_hash
    assert a_consult.content_hash == b_consult.content_hash
    assert a_log.selected_targets == b_log.selected_targets
    assert a_consult.selected_targets == b_consult.selected_targets


# -- bookkeeping for receipt high-water marks -----------------------------------


def test_receipt_carries_store_high_water_marks_under_scope():
    store = EpisodicMemoryStore(clock=lambda: 0)
    store.commit(kind="episodic_memory", content="observation", creator_id="skill")
    store.commit(
        kind="episodic_memory",
        content="other-project",
        scope=ContextScope(project_id="other-project"),
    )
    broker = MemoryActionBroker(snapshot=_snapshot(), stores={"memory": store}, clock=lambda: 0)
    receipt = broker.invoke(
        MemoryAction(
            operation=MemoryOperation.SEARCH,
            phase=MemoryPhase.CONSULT,
            scope=ContextScope(project_id="meta-harness"),
            payload={"query": "observation"},
        )
    )
    names = {name for name, _ in receipt.store_high_water_marks}
    assert "memory" in names


# -- store helper sanity -------------------------------------------------------


def test_normalize_text_used_by_scaffold_is_stable():
    assert normalize_text("  Hello   WORLD  ") == "hello world"
    assert normalize_text("Hello   WORLD") == normalize_text("HELLO world")
