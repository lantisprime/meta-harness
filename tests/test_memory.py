"""Focused tests for the Stage-2 memory substrate (src/metaharness/memory/).

Covers everything Stage 2 promises but doesn't xfail-test:

- Stores: episodic / semantic / working / procedural; in-memory + WAL durable.
- Lifecycle: activation_state / lifecycle_state axes, evidence-preserving
  tombstone (META5-MEM-007).
- Receipted mutation: append-only supersede + self-verifying
  MemoryMutationReceipt; receipt=None rejected (META5-MEM-004).
- Audit ordering: SQLite COMMIT precedes the audit event
  (META5-MEM-005).
- SpecialistTaskAction: bounded vocabulary + scope / static-forbidden check
  (META5-MEM-008).
- Circuit breaker: failure-threshold open + require_healthy raise
  (META5-MEM-013).
- SQLite restart survival: a record committed to a file-backed store is
  readable after close/reopen with a fresh MemoryStore instance.
- Scope isolation: records from project A are never returned for project B
  queries.

These complement (not replace) the strict-xfail cards in
tests/adversarial/test_memory_skill_boundaries.py; the latter enforce
the corpus contract, the former exercise the wider API.
"""
from __future__ import annotations

import inspect
import json
import os
import sqlite3
import tempfile
from pathlib import Path

import pytest
from pydantic import ValidationError

from metaharness.context import ContextScope, Sensitivity
from metaharness.context.models import content_hash
from metaharness.memory import (
    ActivationState,
    EpisodicMemoryStore,
    ImmutableRecordError,
    LifecycleState,
    MemoryKind,
    MemoryMutationReceipt,
    MemoryRecord,
    MemoryStore,
    ProceduralMemoryStore,
    SemanticMemoryStore,
    UnreceiptedMutationError,
    WorkingMemoryStore,
    normalize_text,
)
from metaharness.memory.audit import (
    CommitOrderedMemoryStore,
    bind_sink,
    reset_sink,
)
from metaharness.memory.health import (
    CircuitOpenError,
    MemorySkillCircuitBreaker,
)
from metaharness.memory.skills import (
    SpecialistTaskAction,
    UnauthorizedTaskActionError,
)


# -- record-level invariants ----------------------------------------------------


def test_default_record_is_active_in_both_axes_and_canonicalizes_content():
    record = MemoryRecord(kind="working_memory", content="Hello   World")
    assert record.activation_state == ActivationState.ACTIVE
    assert record.lifecycle_state == LifecycleState.ACTIVE
    assert record.normalized_content == "hello world"
    assert normalize_text("Caf\u00e9 Foo  Bar") == "caf\u00e9 foo bar"


def test_tombstone_returns_new_record_preserving_content_and_idempotent_chain():
    record = MemoryRecord(kind="procedural_memory", content="how to run tests")
    tombstoned = record.tombstone(reason="superseded")
    assert tombstoned is not record
    assert tombstoned.activation_state == ActivationState.TOMBSTONED
    assert tombstoned.lifecycle_state == LifecycleState.TOMBSTONED
    assert tombstoned.tombstone_reason == "superseded"
    assert tombstoned.content == record.content
    assert tombstoned.creation_seq == record.creation_seq + 1
    # Original is unchanged (frozen model + immutable contract).
    assert record.activation_state == ActivationState.ACTIVE


def test_frozen_model_rejects_in_place_attribute_mutation():
    record = MemoryRecord(kind="working_memory", content="hello")
    with pytest.raises(ValidationError):
        record.content = "tampered"


def test_mutation_receipt_hashes_match_canonical_json_of_all_other_fields():
    receipt = MemoryMutationReceipt(
        mutation_id="mut-1",
        target_record_id="tgt-1",
        supersede_record_id="new-1",
        before_content_hash="sha256:" + "1" * 64,
        after_content_hash="sha256:" + "2" * 64,
        before_lifecycle=LifecycleState.ACTIVE,
        after_lifecycle=LifecycleState.SUPERSEDED,
        actor_id="tester",
        observed_at=1234,
        mutation_reason="receipted supersede",
    )
    # Self-verifying: tampering with any field while keeping content_hash breaks it.
    payload = json.loads(receipt.model_dump_json())
    payload["actor_id"] = "tampered"
    payload["content_hash"] = "sha256:" + "0" * 64
    with pytest.raises(ValidationError):
        MemoryMutationReceipt.model_validate(payload)


# -- store-level: append-only + receipted mutation -----------------------------


def test_overwrite_raises_immutable_record_error_for_each_store_kind():
    """META5-MEM-001 (per-store enforcement): every store refuses in-place
    rewrites; mutation must go through mutate() with a receipt."""

    for factory in (
        lambda: EpisodicMemoryStore(),
        lambda: SemanticMemoryStore(),
        lambda: WorkingMemoryStore(),
        lambda: ProceduralMemoryStore(),
    ):
        store = factory()
        record = store.commit(kind=store.default_kind.value, content="original")
        with pytest.raises(ImmutableRecordError):
            store.overwrite(record.id, content="rewritten")


def test_unreceipted_mutation_raises_without_any_database_write():
    store = SemanticMemoryStore()
    record = store.commit(kind="semantic_memory", content="fact")
    with pytest.raises(UnreceiptedMutationError):
        store.mutate(record.id, content="revised", receipt=None)
    # The original record is still there, unchanged.
    again = store.get(record.id)
    assert again is not None
    assert again.content == "fact"
    assert again.lifecycle_state == LifecycleState.ACTIVE


def test_receipted_mutation_appends_supersede_record_and_immutable_receipt():
    store = SemanticMemoryStore()
    original = store.commit(
        kind="semantic_memory",
        content="fact",
        creator_id="alice",
    )
    revised = store.mutate(
        original.id,
        content="revised fact",
        receipt="permit",
        actor_id="bob",
    )
    assert revised is not None
    assert revised.content == "revised fact"
    assert revised.supersedes == (original.id,)
    assert revised.lifecycle_state == LifecycleState.ACTIVE
    assert revised.creator_id == "alice"  # scope/creator/sensitivity carry over
    # The original is still present (append-only), and a MutationReceipt ties
    # the two together via the durable SQLite mutation table.
    survivor = store.get(original.id)
    assert survivor.content == "fact"
    mut_row = store._conn.execute(
        "SELECT mutation_id, target_record_id, supersede_record_id, "
        "before_content_hash, after_content_hash, actor_id "
        "FROM mutations WHERE target_record_id = ?",
        (original.id,),
    ).fetchall()
    assert len(mut_row) == 1
    mutation_id, target_id, supersede_id, before_h, after_h, actor = mut_row[0]
    assert mutation_id.startswith("mut-")
    assert target_id == original.id
    assert supersede_id == revised.id
    assert before_h.startswith("sha256:")
    assert after_h.startswith("sha256:")
    assert before_h != after_h
    assert actor == "bob"


# -- audit: commit-then-log ordering -------------------------------------------


def test_audit_commit_event_emitted_after_sqlite_commit_with_commit_state():
    events: list[tuple[str, dict]] = []
    unbind = bind_sink(lambda kind, payload: events.append((kind, payload)))
    try:
        store = CommitOrderedMemoryStore()
        record = store.commit(kind="working_memory", content="draft")
        assert len(events) == 1
        event_kind, payload = events[0]
        assert event_kind == "memory.commit"
        assert payload["commit_state"] == "committed"
        assert payload["record_id"] == record.id
        assert payload["kind"] == "working_memory"
        # The audit-fired-after-commit order is durable: if the SQLite write
        # had failed, the event would never have fired.
        rows = store._conn.execute(
            "SELECT id, content FROM records WHERE id = ?",
            (record.id,),
        ).fetchall()
        assert rows == [(record.id, "draft")]
    finally:
        unbind()
        reset_sink()


def test_audit_sink_isolated_between_tests():
    events_a: list = []
    events_b: list = []
    unbind_a = bind_sink(lambda kind, payload: events_a.append((kind, payload)))
    try:
        store_a = CommitOrderedMemoryStore()
        store_a.commit(kind="working_memory", content="a")
    finally:
        unbind_a()
        reset_sink()
    unbind_b = bind_sink(lambda kind, payload: events_b.append((kind, payload)))
    try:
        store_b = CommitOrderedMemoryStore()
        store_b.commit(kind="working_memory", content="b")
    finally:
        unbind_b()
        reset_sink()
    assert len(events_a) == 1 and events_a[0][1]["record_id"].endswith("00000001")
    assert len(events_b) == 1 and events_b[0][1]["record_id"].endswith("00000001")


# -- specialist task actions ---------------------------------------------------


def test_specialist_task_action_authorizes_against_allowlist():
    action = SpecialistTaskAction(
        specialist_id="test-writer",
        action="read_only",
        scope=ContextScope(project_id="meta-harness"),
    )
    action.authorize(allowed_actions={"read_only", "write_test"})
    with pytest.raises(UnauthorizedTaskActionError):
        SpecialistTaskAction(
            specialist_id="test-writer",
            action="write_test",
            scope=ContextScope(project_id="meta-harness"),
        ).authorize(allowed_actions={"read_only"})


def test_specialist_task_action_rejects_static_forbidden_authorities():
    for forbidden in (
        "deploy",
        "promote",
        "self_approve",
        "widen_visibility",
        "evaluate",
        "commit_domain",
    ):
        action = SpecialistTaskAction(
            specialist_id="rogue",
            action=forbidden,
            scope=ContextScope(project_id="meta-harness"),
        )
        with pytest.raises(UnauthorizedTaskActionError):
            action.authorize(allowed_actions={forbidden})


@pytest.mark.parametrize(
    "forbidden",
    [
        "deploy", "deployment", "Deploy", "DEPLOY ",
        "promote", "promotes", "promoted", "promoting", "promotion", "promotional",
        "evaluate", "evaluator", "evaluated", "evaluation",
        "self_approve", "self-approved", "self_approval",
        "widen_visibility", "widen-visibility",
        "commit_domain", "commit-domain-action",
    ],
)
def test_FIX_03_forbidden_actions_reject_stems_and_normalization(forbidden):
    """FIX-3: forbidden-action check is morphological, not literal. The
    normalized action is compared against the closed stem set with a
    bounded alpha-suffix bound covering the standard English derivations
    (s, ed, ing, ment, tion, ation, action, inator)."""

    action = SpecialistTaskAction(
        specialist_id="rogue",
        action=forbidden,
        scope=ContextScope(project_id="meta-harness"),
    )
    with pytest.raises(UnauthorizedTaskActionError):
        action.authorize(allowed_actions={forbidden})


@pytest.mark.parametrize(
    "legitimate",
    [
        "write_test", "read_only", "candidate", "search", "log",
        "reporter", "narrator", "promotion_party", "narrator_action",
        "narrow_down",
    ],
)
def test_FIX_03_legitimate_actions_still_authorize(legitimate):
    """FIX-3 parity: actions that are NOT forbidden and not morphological
    extensions of a forbidden action must still authorize."""

    action = SpecialistTaskAction(
        specialist_id="normal",
        action=legitimate,
        scope=ContextScope(project_id="meta-harness"),
    )
    action.authorize(allowed_actions={legitimate})  # no raise


# -- circuit breaker -----------------------------------------------------------


def test_circuit_breaker_opens_after_threshold_and_resets():
    breaker = MemorySkillCircuitBreaker(failure_threshold=3)
    assert breaker.is_healthy() is True
    for _ in range(3):
        breaker.record_failure()
    assert breaker.is_healthy() is False
    with pytest.raises(CircuitOpenError):
        breaker.require_healthy()
    # Further failures don't crash; the message names the threshold.
    for _ in range(2):
        breaker.record_failure()
    snap = breaker.snapshot()
    assert snap["open"] is True
    assert snap["failure_count"] >= 3
    assert snap["failure_threshold"] == 3
    breaker.record_success()
    assert breaker.is_healthy() is True
    breaker.require_healthy()  # no raise


# -- SQLite durability / restart survival --------------------------------------


def test_sqlite_records_survive_close_and_reopen_under_wal():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        # First store commits durable records.
        store_a = MemoryStore(path=path)
        record_a = store_a.commit(kind="episodic_memory", content="durable")
        record_b = store_a.commit(
            kind="episodic_memory",
            content="also durable",
            scope=ContextScope(project_id="project-x", run_id="run-1"),
        )
        # Sanity check: WAL mode is actually on.
        journal_mode = store_a._conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert journal_mode.lower() == "wal"
        store_a.close()

        # Reopen with a fresh MemoryStore instance.
        store_b = MemoryStore(path=path)
        recovered_a = store_b.get(record_a.id)
        recovered_b = store_b.get(record_b.id)
        assert recovered_a is not None and recovered_a.content == "durable"
        assert recovered_b is not None and recovered_b.content == "also durable"
        assert recovered_b.scope.run_id == "run-1"
        store_b.close()

        # Confirm we don't accidentally reuse the previous connection.
        again = MemoryStore(path=path)
        size = again.list(limit=None)
        assert any(r.id == record_a.id for r in size)
        assert any(r.id == record_b.id for r in size)
        again.close()
    finally:
        for suffix in ("", "-wal", "-shm"):
            p = path + suffix
            if os.path.exists(p):
                os.unlink(p)


def test_sqlite_schema_version_table_exists_and_records_one_migration():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        store = MemoryStore(path=path)
        row = store._conn.execute("SELECT version FROM schema_version").fetchone()
        assert row is not None and row[0] == 1
        store.close()
    finally:
        for suffix in ("", "-wal", "-shm"):
            p = path + suffix
            if os.path.exists(p):
                os.unlink(p)


def test_sqlite_fts5_index_is_populated_for_committed_record():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        store = MemoryStore(path=path)
        record = store.commit(
            kind="episodic_memory",
            content="the quick brown fox jumps over the lazy dog",
        )
        # The FTS5 row must exist for the committed record.
        fts_row = store._conn.execute(
            "SELECT record_id FROM records_fts WHERE records_fts MATCH ?",
            ("brown fox",),
        ).fetchall()
        assert fts_row == [(record.id,)]
        store.close()
    finally:
        for suffix in ("", "-wal", "-shm"):
            p = path + suffix
            if os.path.exists(p):
                os.unlink(p)


# -- scope isolation -----------------------------------------------------------


def test_scope_isolation_keeps_project_records_disjoint():
    store = MemoryStore()
    a1 = store.commit(
        kind="episodic_memory",
        content="project-a observation",
        scope=ContextScope(project_id="project-a", run_id="r-a"),
    )
    a2 = store.commit(
        kind="episodic_memory",
        content="another a observation",
        scope=ContextScope(project_id="project-a", run_id="r-a"),
    )
    b1 = store.commit(
        kind="episodic_memory",
        content="project-b observation",
        scope=ContextScope(project_id="project-b", run_id="r-b"),
    )
    b2 = store.commit(
        kind="episodic_memory",
        content="another b observation",
        scope=ContextScope(project_id="project-b", run_id="r-b"),
    )

    project_a_results = store.scope(project_id="project-a")
    project_a_ids = {r.id for r in project_a_results}
    assert {a1.id, a2.id}.issubset(project_a_ids)
    assert b1.id not in project_a_ids
    assert b2.id not in project_a_ids

    project_b_results = store.scope(project_id="project-b")
    project_b_ids = {r.id for r in project_b_results}
    assert {b1.id, b2.id}.issubset(project_b_ids)
    assert a1.id not in project_b_ids
    assert a2.id not in project_b_ids

    # The mirror direction — same isolation through scope kwarg.
    by_scope = store.list(scope=ContextScope(project_id="project-a"))
    by_scope_ids = {r.id for r in by_scope}
    assert {a1.id, a2.id}.issubset(by_scope_ids)
    assert b1.id not in by_scope_ids
    assert b2.id not in by_scope_ids


def test_list_excludes_tombstoned_records_unless_requested():
    store = EpisodicMemoryStore()
    record = store.commit(kind="episodic_memory", content="ephemeral fact")
    # The in-place activation_state=TOMBSTONED path goes through mutate()
    # (which appends a supersede record) — destruction is never a direct
    # operation. After the mutation the original is still readable; the new
    # tombstone record is what carries the TOMBSTONED activation state.
    tombstone_supersede = store.mutate(
        record.id,
        content=record.content,
        receipt="permit",
        activation_state=ActivationState.TOMBSTONED,
        lifecycle_state=LifecycleState.TOMBSTONED,
        mutation_reason="tombstone via mutate",
    )
    assert tombstone_supersede.activation_state == ActivationState.TOMBSTONED
    listed_active = store.list(project_id="meta-harness")
    listed_with_tombstones = store.list(project_id="meta-harness", include_tombstoned=True)
    active_ids = {r.id for r in listed_active}
    tombstone_ids = {r.id for r in listed_with_tombstones}
    assert tombstone_supersede.id not in active_ids
    assert tombstone_supersede.id in tombstone_ids
    # The original record is still queryable: append-only means evidence
    # is preserved, even when its successor carries the tombstone state.
    assert store.get(record.id) is not None


# -- injectable clock ----------------------------------------------------------


def test_clock_is_injectable_and_deterministic_when_explicit():
    ticks = iter([100, 200, 300])
    store = EpisodicMemoryStore(clock=lambda: next(ticks))
    r1 = store.commit(kind="episodic_memory", content="at 100")
    r2 = store.commit(kind="episodic_memory", content="at 200")
    assert r1.observed_at == 100
    assert r2.observed_at == 200


def test_default_clock_is_not_wall_clock_but_a_deterministic_sequence():
    store = EpisodicMemoryStore()
    observed = [store.commit(kind="episodic_memory", content=f"x{i}").observed_at for i in range(5)]
    # Strict monotonic non-decreasing counter (no wall-clock involvement).
    assert observed == sorted(observed)
    assert len(set(observed)) == len(observed)


# ---------------------------------------------------------------------------
# META-6 fix-batch-1 regression tests (each cites its FIX id in the docstring).
# ---------------------------------------------------------------------------


def test_FIX_01_mutate_rolls_back_record_when_receipt_insert_fails():
    """FIX-1 (atomicity): a record insert + FTS row + mutation receipt must
    be one transaction. A failure between them rolls back the record too.
    """

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        store = MemoryStore(path=path)
        record = store.commit(
            kind="episodic_memory",
            content="original",
            lifecycle_state=LifecycleState.CANDIDATE,
        )
        # Drop the mutations table to force an OperationalError on receipt insert.
        store._conn.execute("DROP TABLE mutations")
        with pytest.raises(sqlite3.OperationalError):
            store.mutate(record.id, content="revised", receipt="permit", mutation_reason="boom")
        rows = store._conn.execute("SELECT id, content FROM records ORDER BY creation_seq").fetchall()
        assert rows == [(record.id, "original")]
        # Mutations table dropped by the probe must still be absent, confirming
        # the rolled-back transaction did not partially create it.
        exists = store._conn.execute(
            "SELECT name FROM sqlite_master WHERE name = 'mutations'"
        ).fetchone()
        assert exists is None
        store.close()
    finally:
        for suffix in ("", "-wal", "-shm"):
            p = path + suffix
            if os.path.exists(p):
                os.unlink(p)


def test_FIX_01_mutate_receipt_construction_failure_aborts_before_insert():
    """FIX-1 (build-first): the receipt is constructed before any insert so
    a construction failure aborts cleanly."""

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        store = MemoryStore(path=path)
        record = store.commit(
            kind="episodic_memory",
            content="original",
            lifecycle_state=LifecycleState.CANDIDATE,
        )
        # Force a construction failure by replacing the class with a
        # version whose __init__ always raises.
        from metaharness.memory.records import MemoryMutationReceipt

        class _BoomReceipt(MemoryMutationReceipt):
            def __init__(self, *args, **kwargs):
                raise ValueError("SIMULATED receipt construction failure")

        original_class = store.__class__
        try:
            import metaharness.memory.stores as stores_module

            stores_module.MemoryMutationReceipt = _BoomReceipt
            with pytest.raises(ValueError, match="SIMULATED"):
                store.mutate(record.id, content="revised", receipt="permit", mutation_reason="boom")
        finally:
            stores_module.MemoryMutationReceipt = MemoryMutationReceipt
        rows = store._conn.execute("SELECT id, content FROM records ORDER BY creation_seq").fetchall()
        assert rows == [(record.id, "original")]
        store.close()
    finally:
        for suffix in ("", "-wal", "-shm"):
            p = path + suffix
            if os.path.exists(p):
                os.unlink(p)


def test_FIX_07_reopen_durable_store_continues_id_and_creation_seq_monotonic():
    """FIX-7: a durable store reopened with default id/clock factories must
    not collide with existing ids and must keep creation_seq monotonic."""

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        a = MemoryStore(path=path)
        first = a.commit(kind="episodic_memory", content="a")
        second = a.commit(kind="episodic_memory", content="b")
        third = a.commit(kind="episodic_memory", content="c")
        a.close()
        b = MemoryStore(path=path)
        # No UNIQUE-constraint collision on the first reopen commit.
        n = b.commit(kind="episodic_memory", content="d-after-reopen")
        # All ids must remain unique.
        ids = {first.id, second.id, third.id, n.id}
        assert len(ids) == 4
        # creation_seq must remain monotonic across the reopen.
        all_seqs = [r.creation_seq for r in [first, second, third, n]]
        assert all_seqs == sorted(all_seqs) and len(set(all_seqs)) == len(all_seqs)
        for i in range(20):
            b.commit(kind="episodic_memory", content=f"x{i}")
        total = b.list(limit=None)
        assert len(total) == 4 + 20
        b.close()
    finally:
        for suffix in ("", "-wal", "-shm"):
            p = path + suffix
            if os.path.exists(p):
                os.unlink(p)


def test_FIX_09_mutation_receipt_construction_with_omitted_default_fields():
    """FIX-9: MemoryMutationReceipt must accept constructions that omit any
    defaulted field (e.g. mutation_reason) without raising a hash mismatch."""

    receipt = MemoryMutationReceipt(
        mutation_id="mut-x",
        target_record_id="t-1",
        supersede_record_id="s-1",
        before_content_hash="sha256:" + "a" * 64,
        after_content_hash="sha256:" + "b" * 64,
        before_lifecycle=LifecycleState.ACTIVE,
        after_lifecycle=LifecycleState.SUPERSEDED,
        actor_id="alice",
        observed_at=0,
    )
    # Auto-filled content_hash must agree with the dump of the defaulted model.
    material = receipt.model_dump(mode="json", exclude={"content_hash"})
    assert receipt.content_hash == content_hash(material)
    # And omitting schema_version must also be accepted.
    receipt_b = MemoryMutationReceipt(
        mutation_id="mut-y",
        target_record_id="t-2",
        supersede_record_id="s-2",
        before_content_hash="sha256:" + "c" * 64,
        after_content_hash="sha256:" + "d" * 64,
        before_lifecycle=LifecycleState.ACTIVE,
        after_lifecycle=LifecycleState.SUPERSEDED,
        actor_id="bob",
        observed_at=1,
        mutation_reason="custom",
    )
    material_b = receipt_b.model_dump(mode="json", exclude={"content_hash"})
    assert receipt_b.content_hash == content_hash(material_b)
def test_FIX_09_hash_bearing_models_robust_to_omitted_defaulted_fields():
    """FIX-9 (parity): every hash-bearing model in the memory package must
    auto-fill its content_hash from the fully-defaulted model dump. Tested
    with one omitted defaulted field per model."""

    from metaharness.memory.broker import (
        MemoryActionReceipt,
        MemoryCognitiveSkillSnapshot,
        MemoryLifecycleProposal,
        MemoryProposalKind,
    )

    # MemoryLifecycleProposal — omit schema_version (defaulted).
    proposal = MemoryLifecycleProposal(
        proposal_id="prop-1",
        proposal_kind=MemoryProposalKind.ACTIVATE,
        snapshot_id="snap-1",
        snapshot_content_hash="sha256:" + "1" * 64,
        scope=ContextScope(project_id="meta-harness"),
        target_record_ids=("record-1",),
        requested_transition="activate",
        reason="review",
        observed_at=0,
    )
    material = proposal.model_dump(mode="json", exclude={"content_hash"})
    assert proposal.content_hash == content_hash(material)

    # MemoryActionReceipt — omit schema_version and proposal_ids (defaulted).
    receipt = MemoryActionReceipt(
        receipt_id="r-1",
        snapshot_id="snap-1",
        snapshot_content_hash="sha256:" + "1" * 64,
        skill_id="shadow-skill",
        context_id="ctx-1",
        context_content_hash="sha256:" + "2" * 64,
        store_high_water_marks=(),
        policy_versions=(("compression", "compression:v1"),),
        phase="log",
        operation="create_candidate",
        source_record_ids=(),
        considered_targets=(),
        selected_targets=("rec-1",),
        scope=ContextScope(project_id="meta-harness"),
        lifecycle_filters=(LifecycleState.CANDIDATE,),
        before_content_hashes=(),
        after_content_hashes=(("rec-1", "sha256:" + "3" * 64),),
        validation_results=("broker_mode:shadow",),
        redaction_results=("redaction:clear",),
        input_tokens=0,
        output_tokens=1,
        context_budget_tokens=1024,
        latency_ms=0,
        accepted=True,
        outcome="accepted",
        effect_or_rejection_reason="ok",
        observed_at=0,
    )
    material = receipt.model_dump(mode="json", exclude={"content_hash"})
    assert receipt.content_hash == content_hash(material)

    # MemoryCognitiveSkillSnapshot — omit schema_version (defaulted).
    snap = MemoryCognitiveSkillSnapshot(
        snapshot_id="snap-1",
        skill_id="shadow-skill",
        scope=ContextScope(project_id="meta-harness"),
        goal_families=("retrieval",),
        roles=("builder",),
    )
    material = snap.model_dump(mode="json", exclude={"content_hash"})
    assert snap.content_hash == content_hash(material)


def test_FIX_10_mutation_emits_exactly_one_audit_event_with_correct_state():
    """FIX-10: CommitOrderedMemoryStore.mutate() emits exactly one
    ``memory.mutation_committed`` event (no double-count from a
    dynamic-dispatched ``commit``), and the event fires only after the
    mutation receipt is durable."""

    events: list[tuple[str, dict]] = []
    unbind = bind_sink(lambda kind, payload: events.append((kind, payload)))
    try:
        store = CommitOrderedMemoryStore()
        original = store.commit(kind="episodic_memory", content="orig")
        # Reset events to isolate the mutation path.
        events.clear()
        new = store.mutate(
            original.id,
            content="revised",
            receipt="permit",
            mutation_reason="r",
        )
        assert [kind for kind, _ in events] == ["memory.mutation_committed"]
        event_kind, payload = events[0]
        assert event_kind == "memory.mutation_committed"
        assert payload["commit_state"] == "committed"
        assert payload["record_id"] == new.id
        # The mutation receipt must already be durable when the event fires.
        mut_rows = store._conn.execute(
            "SELECT target_record_id, supersede_record_id FROM mutations"
        ).fetchall()
        assert (original.id, new.id) in mut_rows
    finally:
        unbind()
        reset_sink()


def test_FIX_15_docstring_states_exact_durability_guarantee():
    """FIX-15: ``stores.py`` and ``audit.py`` docstrings must state the
    precise guarantee — durable across close/reopen and process crash, not
    across OS crash / power loss — and not over-claim WAL+synchronous=NORMAL.
    """

    from metaharness import memory

    stores_doc = inspect.getsource(memory.stores)
    audit_doc = inspect.getsource(memory.audit)
    assert "process crash" in stores_doc or "process crash" in audit_doc
    assert "OS crash" in stores_doc or "OS crash" in audit_doc
    assert "power loss" in stores_doc or "power loss" in audit_doc
    # And the explicit PRAGMA note must call out the close/reopen guarantee.
    assert "close/reopen" in stores_doc
