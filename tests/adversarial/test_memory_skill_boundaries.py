"""META-5: strict xfail tests for genuinely absent future memory-skill contracts.

Every test in this module is expected to fail today (``xfail(strict=True)``)
and is tagged with a stable requirement ID from
tests/fixtures/meta5/corpus.json (suite=test_memory_skill_boundaries,
status=absent). If a future change happens to satisfy one of these without
also updating the test, strict xfail turns that into a hard failure (XPASS)
so the gap can't silently close without review.

None of these tests assert that today's code is *broken* — src/metaharness
has no memory-skill subsystem yet, so there is nothing to fix here. They
document the contract that subsystem must satisfy once it exists, anchored
to the concrete context-contract primitives it will sit on top of wherever
that's expressible today, and to a plain absent-module probe otherwise.
"""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from metaharness.context import (
    ContextEnvelope,
    ContextManifest,
    ContextSourceKind,
    ContextTrust,
    ContextVersionBindings,
    Sensitivity,
    content_hash,
)
from metaharness.context.models import canonical_json

from tests.adversarial._meta5_support import cases_for, make_scope, make_section, make_source

ABSENT_CASES = {case["id"]: case for case in cases_for("test_memory_skill_boundaries", status="absent")}


def _versions(**changes):
    values = {
        "model_portfolio_version": "portfolio:1",
        "harness_version": "harness:1",
        "evaluator_version": "evaluator:1",
        "evidence_snapshot_version": "evidence:1",
        "candidate_version": "candidate:1",
    }
    values.update(changes)
    return ContextVersionBindings(**values)


def test_all_absent_cases_have_a_stable_requirement_id_and_a_test_below():
    covered_requirement_ids = {
        "META5-MEM-001",
        "META5-MEM-002",
        "META5-MEM-003",
        "META5-MEM-004",
        "META5-MEM-005",
        "META5-MEM-006",
        "META5-MEM-007",
        "META5-MEM-008",
        "META5-MEM-009",
        "META5-MEM-010",
        "META5-MEM-011",
        "META5-MEM-012",
        "META5-MEM-013",
        "META5-MEM-014",
        "META5-MEM-015",
    }
    corpus_requirement_ids = {case["requirement_id"] for case in ABSENT_CASES.values()}
    assert corpus_requirement_ids == covered_requirement_ids


@pytest.mark.xfail(
    strict=True,
    raises=ModuleNotFoundError,
    reason="META5-MEM-001: memory records must be append-only (tombstone/supersede, "
    "not destructive rewrite); no metaharness.memory module exists yet",
)
def test_committed_memory_record_cannot_be_rewritten_in_place():
    import metaharness.memory as memory  # not yet implemented

    store = memory.EpisodicMemoryStore()
    record = store.commit(kind="episodic_memory", content="original observation")
    with pytest.raises(memory.ImmutableRecordError):
        store.overwrite(record.id, content="rewritten observation")


@pytest.mark.xfail(
    strict=True,
    raises=pytest.fail.Exception,
    reason="META5-MEM-002: source_id/artifact_ref values must be rejected when they "
    "encode filesystem path traversal; no artifact resolver enforces this yet",
)
def test_source_id_and_artifact_ref_reject_path_traversal_sequences():
    assert ABSENT_CASES["traversal-source-id-path-escape"]["requirement_id"] == "META5-MEM-002"
    with pytest.raises(ValidationError):
        make_source(source_id="../../etc/passwd")
    with pytest.raises(ValidationError):
        make_source(
            source_id="artifact-with-escape",
            fetchable=True,
            artifact_ref="../../../secrets/credentials.json",
            content_hash=None,
            high_water_mark="event-seq:1",
        )


@pytest.mark.xfail(
    strict=True,
    raises=pytest.fail.Exception,
    reason="META5-MEM-003: a context envelope must refuse to bind sections whose "
    "sources carry different project/run scopes; ContextEnvelope has no "
    "scope-consistency validator today",
)
def test_envelope_rejects_sections_drawn_from_incompatible_scopes():
    source_a = make_source(
        source_id="project-a-instructions",
        scope=make_scope(project_id="project-a"),
    )
    source_b = make_source(
        source_id="project-b-instructions",
        scope=make_scope(project_id="project-b"),
    )
    section_a = make_section(source=source_a, stable_id="a", ordering_priority=0)
    section_b = make_section(source=source_b, stable_id="b", ordering_priority=1)

    def build():
        values = {
            "schema_version": 1,
            "policy_version": "v1",
            "model_id": "m",
            "versions": _versions(),
            "sections": (section_a, section_b),
        }
        hash_material = {
            **values,
            "versions": values["versions"].model_dump(mode="json"),
            "sections": [section_a.model_dump(mode="json"), section_b.model_dump(mode="json")],
        }
        return ContextEnvelope(**values, content_hash=content_hash(hash_material))

    with pytest.raises(ValidationError):
        build()


@pytest.mark.xfail(
    strict=True,
    raises=ModuleNotFoundError,
    reason="META5-MEM-004: every mutation of a committed memory record must produce "
    "a receipt analogous to CompressionReceipt; no memory store exists yet "
    "to enforce unreceipted-mutation rejection",
)
def test_memory_mutation_without_a_receipt_is_rejected():
    import metaharness.memory as memory  # not yet implemented

    store = memory.SemanticMemoryStore()
    record = store.commit(kind="semantic_memory", content="fact")
    with pytest.raises(memory.UnreceiptedMutationError):
        store.mutate(record.id, content="revised fact", receipt=None)


@pytest.mark.xfail(
    strict=True,
    raises=ModuleNotFoundError,
    reason="META5-MEM-005: a memory mutation must be durably committed before any "
    "observability event describing it is emitted; no commit-then-log "
    "ordering contract exists yet",
)
def test_memory_write_is_committed_before_it_is_logged():
    import metaharness.memory.audit as audit  # not yet implemented

    events = []
    audit.bind_sink(lambda kind, payload: events.append((kind, payload)))
    store = audit.CommitOrderedMemoryStore()
    store.commit(kind="working_memory", content="draft")
    assert events, "expected a post-commit audit event"
    assert events[0][1]["commit_state"] == "committed"


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="META5-MEM-006: lossy compression receipts must carry a bounded "
    "fidelity/information-loss estimate; CompressionReceipt has no such field",
)
def test_lossy_compression_receipt_carries_a_fidelity_bound():
    from metaharness.context import CompressionAction, CompressionReceipt

    receipt = CompressionReceipt(
        stable_id="s",
        action=CompressionAction.STRUCTURED_SUMMARY,
        before_hash="sha256:" + "1" * 64,
        after_hash="sha256:" + "2" * 64,
        original_tokens=1000,
        final_tokens=100,
        reason="structured summary for budget",
    )
    assert hasattr(receipt, "fidelity_loss_estimate")
    assert 0.0 <= receipt.fidelity_loss_estimate <= 1.0


@pytest.mark.xfail(
    strict=True,
    raises=ModuleNotFoundError,
    reason="META5-MEM-007: memory records need an activation lifecycle "
    "(active/dormant/tombstoned) so retrieval can exclude tombstoned records "
    "without destroying evidence; no such type exists yet",
)
def test_memory_record_supports_activation_and_tombstone_states():
    import metaharness.memory as memory  # not yet implemented

    record = memory.MemoryRecord(kind="procedural_memory", content="how to run tests")
    assert record.activation_state == memory.ActivationState.ACTIVE
    tombstoned = record.tombstone(reason="superseded")
    assert tombstoned.activation_state == memory.ActivationState.TOMBSTONED
    assert tombstoned.content == record.content


@pytest.mark.xfail(
    strict=True,
    raises=ModuleNotFoundError,
    reason="META5-MEM-008: specialist sub-agents need a typed, bounded task-action "
    "contract distinct from a general worker's free-form tool calls; no "
    "specialist task-action type exists yet",
)
def test_specialist_task_action_is_bounded_and_scope_checked():
    import metaharness.memory.skills as skills  # not yet implemented

    action = skills.SpecialistTaskAction(
        specialist_id="test-writer",
        action="write_test",
        scope=make_scope(project_id="meta-harness"),
    )
    with pytest.raises(skills.UnauthorizedTaskActionError):
        action.authorize(allowed_actions={"read_only"})


@pytest.mark.xfail(
    strict=True,
    raises=ModuleNotFoundError,
    reason="META5-MEM-009: harness optimization needs a typed training-target "
    "schema mapping recorded task actions to labeled outcomes; no such "
    "schema exists yet",
)
def test_task_action_training_target_schema_exists_and_validates():
    import metaharness.memory.training as training  # not yet implemented

    target = training.TaskActionTrainingTarget(
        task_id="task-1",
        action="write_test",
        outcome_label="pass",
    )
    assert target.outcome_label in {"pass", "fail"}


@pytest.mark.xfail(
    strict=True,
    raises=pytest.fail.Exception,
    reason="META5-MEM-010: a memory-skill assembler must refuse to bind a "
    "high-water-mark-tracked (live) section and a content-hash-pinned "
    "(immutable) section to the same lineage without an explicit "
    "reconciliation marker; ContextEnvelope has no such cross-section check",
)
def test_envelope_rejects_confounded_live_and_pinned_sections_for_same_lineage():
    pinned_source = make_source(
        source_id="lineage-42",
        content_hash="sha256:" + "1" * 64,
        high_water_mark=None,
    )
    live_source = make_source(
        source_id="lineage-42",
        content_hash=None,
        high_water_mark="event-seq:7",
        kind=ContextSourceKind.LIVE_RUN_STATE,
        trust=ContextTrust.UNTRUSTED_EVIDENCE,
    )
    pinned_section = make_section(source=pinned_source, stable_id="pinned", ordering_priority=0)
    live_section = make_section(
        source=live_source,
        stable_id="live",
        ordering_priority=1,
        source_hash=content_hash("live-material"),
        trust=live_source.trust,
        sensitivity=live_source.sensitivity,
    )

    def build():
        values = {
            "schema_version": 1,
            "policy_version": "v1",
            "model_id": "m",
            "versions": _versions(),
            "sections": (pinned_section, live_section),
        }
        hash_material = {
            **values,
            "versions": values["versions"].model_dump(mode="json"),
            "sections": [pinned_section.model_dump(mode="json"), live_section.model_dump(mode="json")],
        }
        return ContextEnvelope(**values, content_hash=content_hash(hash_material))

    with pytest.raises(ValidationError):
        build()


@pytest.mark.xfail(
    strict=True,
    raises=ModuleNotFoundError,
    reason="META5-MEM-011: a promotion gate must refuse to promote a candidate "
    "whose supporting evidence comes from repeatedly re-evaluating the same "
    "search set; no promotion-gate module exists yet",
)
def test_promotion_gate_rejects_repeated_search_set_evidence():
    import metaharness.memory.promotion as promotion  # not yet implemented

    gate = promotion.PromotionGate()
    evidence = promotion.Evidence(
        search_set_id="search-1",
        evaluation_count=5,
        held_out_evaluation_count=0,
    )
    with pytest.raises(promotion.SearchSetLeakageError):
        gate.decide(evidence)


@pytest.mark.xfail(
    strict=True,
    raises=pytest.fail.Exception,
    reason="META5-MEM-012: reusing a memory/evidence snapshot pair across an "
    "incompatible harness_version bump must be rejected; "
    "ContextVersionBindings only validates candidate lineage today",
)
def test_version_bindings_reject_incompatible_snapshot_reuse_across_harness_bump():
    shared_axes = {
        "memory_snapshot_version": "memory:9",
        "evidence_snapshot_version": "evidence:9",
        "model_portfolio_version": "portfolio:1",
        "evaluator_version": "evaluator:1",
        "candidate_version": "candidate:1",
    }
    ContextVersionBindings(harness_version="harness:1", **shared_axes)
    with pytest.raises(ValidationError):
        # No cross-axis compatibility check exists: reusing the same
        # memory/evidence snapshot pair across a breaking harness_version
        # bump constructs successfully today, which is the gap this
        # requirement closes.
        ContextVersionBindings(
            harness_version="harness:2-breaking-schema-change",
            **shared_axes,
        )


@pytest.mark.xfail(
    strict=True,
    raises=ModuleNotFoundError,
    reason="META5-MEM-013: a memory-skill subsystem must expose a health signal so "
    "repeated assembly failures trip a circuit breaker instead of always "
    "silently falling back to the legacy fitter; no health/circuit-breaker "
    "module exists yet",
)
def test_repeated_shadow_assembly_failures_trip_an_unhealthy_circuit_breaker():
    import metaharness.memory.health as health  # not yet implemented

    breaker = health.MemorySkillCircuitBreaker(failure_threshold=3)
    for _ in range(5):
        breaker.record_failure()
    assert breaker.is_healthy() is False
    with pytest.raises(health.CircuitOpenError):
        breaker.require_healthy()


@pytest.mark.xfail(
    strict=True,
    raises=pytest.fail.Exception,
    reason="META5-MEM-014: every fetchable section's artifact_ref must appear in "
    "the manifest's artifact_refs list; ContextManifest has no such "
    "completeness validator today",
)
def test_manifest_artifact_refs_must_cover_every_fetchable_section():
    fetchable_source = make_source(
        source_id="fetchable-artifact",
        kind=ContextSourceKind.IMMUTABLE_ARTIFACT,
        trust=ContextTrust.UNTRUSTED_EVIDENCE,
        sensitivity=Sensitivity.INTERNAL,
        fetchable=True,
        artifact_ref="artifact:evidence-42",
    )
    section = make_section(
        source=fetchable_source,
        trust=fetchable_source.trust,
        sensitivity=fetchable_source.sensitivity,
        stable_id="fetchable-section",
        ordering_priority=0,
    )
    envelope_values = {
        "schema_version": 1,
        "policy_version": "v1",
        "model_id": "m",
        "versions": _versions(),
        "sections": (section,),
    }
    hash_material = {
        **envelope_values,
        "versions": envelope_values["versions"].model_dump(mode="json"),
        "sections": [section.model_dump(mode="json")],
    }
    envelope = ContextEnvelope(**envelope_values, content_hash=content_hash(hash_material))
    redacted_envelope_dict = json.loads(envelope.model_dump_json())
    redacted_envelope_json = canonical_json(redacted_envelope_dict)

    manifest_values = {
        "schema_version": 1,
        "policy_version": "v1",
        "model_id": "m",
        "versions": _versions(),
        "envelope_hash": envelope.content_hash,
        "redacted_envelope_hash": content_hash(redacted_envelope_dict),
        "redacted_envelope_json": redacted_envelope_json,
        "entries": (),
        "compression_receipts": (),
        "source_candidates_considered": (),
        "visibility_decisions": (),
        "deliberate_omissions": (),
        "artifact_refs": (),  # missing fetchable_source.artifact_ref
        "budget_used_tokens": 0,
        "budget_limit_tokens": 100,
        "redaction_count": 0,
    }
    serializable = {
        **manifest_values,
        "versions": manifest_values["versions"].model_dump(mode="json"),
        "entries": [],
        "compression_receipts": [],
    }

    with pytest.raises(ValidationError):
        ContextManifest(**manifest_values, manifest_hash=content_hash(serializable))


@pytest.mark.xfail(
    strict=True,
    raises=pytest.fail.Exception,
    reason="META5-MEM-015: evaluator receipts are evidence, never instructions; "
    "ContextSourceRef has no source-kind trust ceiling today",
)
def test_evaluator_receipt_cannot_self_promote_to_instruction_authority():
    assert ABSENT_CASES["evaluator-receipt-instruction-self-promotion"]["requirement_id"] == "META5-MEM-015"
    with pytest.raises(ValidationError):
        make_source(
            source_id="evaluator-verdict",
            kind=ContextSourceKind.EVALUATOR_RECEIPT,
            trust=ContextTrust.INSTRUCTION,
            sensitivity=Sensitivity.INTERNAL,
        )
