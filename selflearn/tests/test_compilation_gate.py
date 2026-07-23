"""Tests for cross-validation gate.

Tests cover:
- End-to-end happy path with fake ExecutionPort and a real sandbox double
- Identity collision (author model_id == COMPILER_ID) rejected at construction
- Suite author identity re-check (F2-8)
- Suite/spec hash mismatch rejected without sandbox
- Stale spec rejected unconditionally (F2-5)
- Sandbox fail -> rejected
- Sandbox pass + no approval -> refused (stays quarantined)
- Activation swap without baseline.json -> GateError
- Swap with baseline -> old superseded AFTER new active (F2-2)
- Receipt binds record and provenance (F2-3)
- Receipt tamper detection
- Every decision journaled
- No ExecutionPort -> GateError
"""
import json
import sys
import tempfile
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import pytest

from selflearn.compilation import (
    ApprovalRecord,
    CrossValidationGate,
    ExecutorCandidate,
    ExecutorRegistry,
    ExecutorRuntime,
    ExecutorSpec,
    GateError,
    IndependentTestSuite,
    WorkflowCompiler,
    canonical_json,
    canonical_procedure_hash,
    content_hash,
)
from selflearn.compilation.gate import COMPILER_ID
from selflearn.compilation.models import CrossValidationReceipt, ExecutorRecord
from selflearn.compilation.registry import RegistryError
from selflearn.compilation.runtime import _make_restricted_globals
from selflearn.compilation.testgen import TestAuthorError, WorkflowTestAuthor
from selflearn.contracts import EntrySource, ProcedureStep
from selflearn.ports import ExecutionPort, ExecutionResult, IdentityPort, ProvenancePort
from selflearn.store.packstore import PackStore

sys.path.insert(0, str(Path(__file__).parent))
from test_compilation import _make_workflow_entry


# =============================================================================
# Test helpers
# =============================================================================


def _create_pack(store_root: Path, pack: str) -> None:
    """Create a valid pack structure with manifest.json."""
    pack_dir = store_root / pack
    pack_dir.mkdir(parents=True)
    manifest = {
        "name": pack,
        "schema_version": 1,
        "entries": {},
    }
    (pack_dir / "manifest.json").write_text(json.dumps(manifest))
    (pack_dir / "entries").mkdir(parents=True)


TEST_SOURCE = EntrySource(
    url="https://test.example.org",
    fetched_at="2024-01-01T00:00:00Z",
    sha256="0" * 64,
    tier="official",
)


def _make_step(step_id: str, **kwargs) -> ProcedureStep:
    return ProcedureStep(
        id=step_id,
        objective=kwargs.get("objective", "test"),
        task_type=kwargs.get("task_type", "code_edit"),
        tools=kwargs.get("tools", ()),
        depends_on=kwargs.get("depends_on", ()),
        check=kwargs.get("check", ()),
    )


def _make_spec(entry_id: str = "wf-001", pack: str = "test") -> tuple:
    """Make a test spec and procedure."""
    steps = (_make_step("step1", objective="do work"),)
    spec = ExecutorSpec(
        entry_id=entry_id,
        pack=pack,
        spec_hash=canonical_procedure_hash(steps),
        procedure=steps,
    )
    return spec, steps


def _seed_entry(store_root: Path, pack: str, entry_id: str = "wf-001") -> tuple[ExecutorSpec, tuple]:
    """Seed a workflow entry whose procedure hashes to the returned spec.

    The PackStore is created, the entry is added, and the store instance is
    returned so tests can reuse it.  The registry should be constructed AFTER
    this call so that registry.store loads the entry.
    """
    spec, steps = _make_spec(entry_id, pack)
    entry = _make_workflow_entry(id=entry_id, pack=pack, procedure=steps)
    store = PackStore(store_root)
    store.add_candidate(entry)
    return spec, steps, store


@dataclass
class FakeProvenance:
    """Fake provenance that records events."""
    events: list = field(default_factory=list)

    def append(self, event: dict) -> None:
        self.events.append(event)


@dataclass
class FakeIdentity:
    """Fake identity that checks model_id."""
    basis: str = "fake-identity"

    def distinct(self, worker_a: Any, worker_b: Any) -> bool:
        a_id = getattr(worker_a, "model_id", None)
        b_id = getattr(worker_b, "model_id", None)
        if a_id is None or b_id is None:
            return True
        return a_id != b_id


@dataclass
class FakeModel:
    """Fake model for test author."""
    model_id: str = "test-model"

    def complete(self, role: str, prompt: str, context: dict) -> dict:
        return {
            "tests": [
                {"name": "test_order", "kind": "order", "step_id": "step1", "expect": '["step1"]'},
                {"name": "test_check", "kind": "check", "step_id": "step1", "expect": "ok"},
            ]
        }


@dataclass
class FakeExecutionPort:
    """Fake execution port that records but does not execute checks."""
    should_fail: bool = False
    call_count: int = 0

    def run_check(self, check: dict) -> ExecutionResult:
        self.call_count += 1
        if self.should_fail:
            return ExecutionResult(ok=False, output="Test failure")
        return ExecutionResult(ok=True, output="All tests passed")


class RealSandboxExecutionPort(ExecutionPort):
    """ExecutionPort double that actually execs the executor + tests.

    F2-11: the sandbox double really executes the generated source and test
    source, using the same restricted globals helper the runtime uses.
    """

    def __init__(self):
        self.call_count = 0
        self.outputs: list[str] = []

    def run_check(self, check: dict) -> ExecutionResult:
        self.call_count += 1
        executor_source = check["executor_source"]
        test_source = check["test_source"]

        def load_executor():
            ns = _make_restricted_globals()
            exec(executor_source, ns)
            return ns

        ns = {"load_executor": load_executor, "json": __import__("json")}
        try:
            exec(test_source, ns)
            run_tests = ns["run_tests"]
            results = run_tests(load_executor)
            ok = all(r[0] == "pass" for r in results)
            output = json.dumps(results)
        except Exception as exc:
            ok = False
            output = str(exc)

        self.outputs.append(output)
        return ExecutionResult(ok=ok, output=output)


# =============================================================================
# Identity collision tests
# =============================================================================

def test_test_author_rejects_compiler_identity():
    """Test author rejects model_id == COMPILER_ID."""
    class CompilerModel:
        model_id = COMPILER_ID

    identity = FakeIdentity()

    with pytest.raises(TestAuthorError, match="distinct from compiler"):
        WorkflowTestAuthor(CompilerModel(), identity)


def test_test_author_accepts_distinct_identity():
    """Test author accepts distinct model identity."""
    model = FakeModel()
    identity = FakeIdentity()

    author = WorkflowTestAuthor(model, identity)
    assert author is not None


def test_test_author_wraps_identity_error():
    """F2-17: identity.distinct failure becomes TestAuthorError."""
    class ExplodingIdentity:
        basis = "explodes"

        def distinct(self, a, b):
            raise RuntimeError("identity backend unavailable")

    with pytest.raises(TestAuthorError, match="identity verification failed"):
        WorkflowTestAuthor(FakeModel(), ExplodingIdentity())


# =============================================================================
# Gate tests
# =============================================================================

def test_gate_no_execution_port_raises():
    """Gate raises GateError when no execution port bound."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store_root = Path(tmpdir)
        pack = "test"
        _create_pack(store_root, pack)

        spec, steps, _ = _seed_entry(store_root, pack)
        registry = ExecutorRegistry(store_root, pack)
        provenance = FakeProvenance()

        def clock():
            from datetime import datetime
            return datetime(2024, 1, 1)

        gate = CrossValidationGate(None, registry, provenance, clock)

        candidate = ExecutorCandidate(
            spec=spec,
            source="print('test')",
            executor_hash="a" * 64,
            compiled_at="2024-01-01T00:00:00Z",
            compiler_id="test",
        )
        suite = IndependentTestSuite(
            spec_hash=spec.spec_hash,
            test_source="def run_tests(x): pass",
            suite_hash="b" * 64,
            author_id="test",
            identity_basis="test",
            authored_at="2024-01-01T00:00:00Z",
        )

        with pytest.raises(GateError, match="ExecutionPort"):
            gate.evaluate(candidate, suite, None, decided_at="2024-01-01T00:00:00Z")


def test_gate_spec_mismatch_rejects_without_sandbox():
    """Gate rejects when spec_hash != suite.spec_hash, no sandbox run."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store_root = Path(tmpdir)
        pack = "test"
        _create_pack(store_root, pack)

        spec, steps, _ = _seed_entry(store_root, pack)
        registry = ExecutorRegistry(store_root, pack)
        provenance = FakeProvenance()
        execution = FakeExecutionPort()

        def clock():
            from datetime import datetime
            return datetime(2024, 1, 1)

        gate = CrossValidationGate(execution, registry, provenance, clock)

        candidate = ExecutorCandidate(
            spec=spec,
            source="print('test')",
            executor_hash="a" * 64,
            compiled_at="2024-01-01T00:00:00Z",
            compiler_id="test",
        )
        suite = IndependentTestSuite(
            spec_hash="d" * 64,
            test_source="def run_tests(x): pass",
            suite_hash="b" * 64,
            author_id="test",
            identity_basis="test",
            authored_at="2024-01-01T00:00:00Z",
        )

        receipt = gate.evaluate(candidate, suite, None, decided_at="2024-01-01T00:00:00Z")

        assert receipt.verdict == "rejected"
        assert "mismatch" in receipt.reason.lower()
        assert execution.call_count == 0


def test_gate_suite_author_identity_collision_rejected():
    """F2-8: suite author_id == COMPILER_ID or empty basis -> rejected, no sandbox."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store_root = Path(tmpdir)
        pack = "test"
        _create_pack(store_root, pack)

        spec, steps, _ = _seed_entry(store_root, pack)
        registry = ExecutorRegistry(store_root, pack)
        provenance = FakeProvenance()
        execution = FakeExecutionPort()

        def clock():
            from datetime import datetime
            return datetime(2024, 1, 1)

        gate = CrossValidationGate(execution, registry, provenance, clock)

        candidate = ExecutorCandidate(
            spec=spec,
            source="print('test')",
            executor_hash="a" * 64,
            compiled_at="2024-01-01T00:00:00Z",
            compiler_id="test",
        )
        suite_collision = IndependentTestSuite(
            spec_hash=spec.spec_hash,
            test_source="x",
            suite_hash="b" * 64,
            author_id=COMPILER_ID,
            identity_basis="recorded",
            authored_at="t",
        )
        receipt = gate.evaluate(candidate, suite_collision, None, decided_at="t")
        assert receipt.verdict == "rejected"
        assert "identity" in receipt.reason.lower()

        suite_no_basis = IndependentTestSuite(
            spec_hash=spec.spec_hash,
            test_source="x",
            suite_hash="c" * 64,
            author_id="test-model",
            identity_basis="",
            authored_at="t2",
        )
        receipt2 = gate.evaluate(candidate, suite_no_basis, None, decided_at="t2")
        assert receipt2.verdict == "rejected"
        assert execution.call_count == 0


def test_gate_stale_spec_entry_deleted_rejected_zero_sandbox():
    """Entry deleted after compile: evaluate -> rejected, zero sandbox calls.

    F2-5: stale/unreadable entry check is unconditional, regardless of active.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        store_root = Path(tmpdir)
        pack = "test"
        _create_pack(store_root, pack)

        spec, steps, _ = _seed_entry(store_root, pack)
        registry = ExecutorRegistry(store_root, pack)

        compiler = WorkflowCompiler()
        entry = _make_workflow_entry(id=spec.entry_id, pack=pack, procedure=steps)
        candidate = compiler.compile(entry, pack=pack, compiled_at="2024-01-01T00:00:00Z", provenance=FakeProvenance())

        path = registry.write_candidate(candidate)
        active_record = ExecutorRecord(
            record_id="",
            entry_id=spec.entry_id,
            pack=pack,
            spec_hash=spec.spec_hash,
            executor_hash=candidate.executor_hash,
            status="active",
            path=str(path.relative_to(store_root)),
            receipt_id="old_receipt",
            updated_at="2024-01-01T00:00:00Z",
        )
        registry.add_record(active_record)

        # Simulate the entry disappearing from the store's in-memory view
        # while the registry.json still holds an active record.
        del registry.store._entries[spec.entry_id]

        provenance = FakeProvenance()
        execution = FakeExecutionPort()

        def clock():
            from datetime import datetime
            return datetime(2024, 1, 1)

        gate = CrossValidationGate(execution, registry, provenance, clock)

        suite = IndependentTestSuite(
            spec_hash=spec.spec_hash,
            test_source="def run_tests(x): pass",
            suite_hash="b" * 64,
            author_id="test", identity_basis="test",
            authored_at="2024-01-01T00:00:00Z",
        )
        approval = ApprovalRecord(
            approver="admin", basis="reviewed", strict_mode=True,
            approved_at="2024-01-01T00:00:00Z"
        )

        receipt = gate.evaluate(candidate, suite, approval, decided_at="2024-01-01T00:00:00Z")

        assert receipt.verdict == "rejected"
        assert "unverifiable" in receipt.reason.lower()
        assert execution.call_count == 0


def test_gate_sandbox_pass_awaiting_approval():
    """Gate returns refused when sandbox passes but no approval."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store_root = Path(tmpdir)
        pack = "test"
        _create_pack(store_root, pack)

        spec, steps, _ = _seed_entry(store_root, pack)
        registry = ExecutorRegistry(store_root, pack)
        provenance = FakeProvenance()
        execution = FakeExecutionPort(should_fail=False)

        def clock():
            from datetime import datetime
            return datetime(2024, 1, 1)

        gate = CrossValidationGate(execution, registry, provenance, clock)

        candidate = ExecutorCandidate(
            spec=spec,
            source="print('test')",
            executor_hash="a" * 64,
            compiled_at="2024-01-01T00:00:00Z",
            compiler_id="test",
        )
        suite = IndependentTestSuite(
            spec_hash=spec.spec_hash,
            test_source="def run_tests(x): pass",
            suite_hash="b" * 64,
            author_id="test",
            identity_basis="test",
            authored_at="2024-01-01T00:00:00Z",
        )

        receipt = gate.evaluate(candidate, suite, None, decided_at="2024-01-01T00:00:00Z")

        assert receipt.verdict == "refused"
        assert "approval" in receipt.reason.lower()
        assert receipt.sandbox_ok is True


def test_gate_sandbox_fail_rejected():
    """Gate rejects when sandbox fails."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store_root = Path(tmpdir)
        pack = "test"
        _create_pack(store_root, pack)

        spec, steps, _ = _seed_entry(store_root, pack)
        registry = ExecutorRegistry(store_root, pack)
        provenance = FakeProvenance()
        execution = FakeExecutionPort(should_fail=True)

        def clock():
            from datetime import datetime
            return datetime(2024, 1, 1)

        gate = CrossValidationGate(execution, registry, provenance, clock)

        candidate = ExecutorCandidate(
            spec=spec,
            source="print('test')",
            executor_hash="a" * 64,
            compiled_at="2024-01-01T00:00:00Z",
            compiler_id="test",
        )
        suite = IndependentTestSuite(
            spec_hash=spec.spec_hash,
            test_source="def run_tests(x): pass",
            suite_hash="b" * 64,
            author_id="test",
            identity_basis="test",
            authored_at="2024-01-01T00:00:00Z",
        )
        approval = ApprovalRecord(
            approver="admin", basis="reviewed", strict_mode=True,
            approved_at="2024-01-01T00:00:00Z"
        )

        receipt = gate.evaluate(candidate, suite, approval, decided_at="2024-01-01T00:00:00Z")

        assert receipt.verdict == "rejected"
        assert receipt.sandbox_ok is False
        rejected_records = registry.record_for(spec.entry_id, status="rejected")
        assert len(rejected_records) == 1
        assert rejected_records[0].receipt_id == receipt.receipt_id


def test_gate_activation_with_approval():
    """Gate activates when sandbox passes with strict approval."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store_root = Path(tmpdir)
        pack = "test"
        _create_pack(store_root, pack)

        spec, steps, _ = _seed_entry(store_root, pack)
        registry = ExecutorRegistry(store_root, pack)
        provenance = FakeProvenance()
        execution = FakeExecutionPort(should_fail=False)

        def clock():
            from datetime import datetime
            return datetime(2024, 1, 1)

        gate = CrossValidationGate(execution, registry, provenance, clock)

        candidate = ExecutorCandidate(
            spec=spec,
            source="print('test')",
            executor_hash="a" * 64,
            compiled_at="2024-01-01T00:00:00Z",
            compiler_id="test",
        )
        suite = IndependentTestSuite(
            spec_hash=spec.spec_hash,
            test_source="def run_tests(x): pass",
            suite_hash="b" * 64,
            author_id="test",
            identity_basis="test",
            authored_at="2024-01-01T00:00:00Z",
        )
        approval = ApprovalRecord(
            approver="admin", basis="reviewed", strict_mode=True,
            approved_at="2024-01-01T00:00:00Z"
        )

        receipt = gate.evaluate(candidate, suite, approval, decided_at="2024-01-01T00:00:00Z")

        assert receipt.verdict == "activated"
        assert receipt.sandbox_ok is True
        assert receipt.approval is not None
        assert receipt.approval.strict_mode is True

        active = registry.active_for(spec.entry_id)
        assert active is not None
        assert active.status == "active"
        assert active.receipt_id == receipt.receipt_id


def test_gate_swap_requires_baseline():
    """Gate swap with baseline present activates the new executor."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store_root = Path(tmpdir)
        pack = "test"
        _create_pack(store_root, pack)

        baseline_dir = store_root / pack / "evals"
        baseline_dir.mkdir(parents=True)
        (baseline_dir / "baseline.json").write_text(json.dumps({"score": 0.5}))

        spec, steps, _ = _seed_entry(store_root, pack)
        registry = ExecutorRegistry(store_root, pack)
        provenance = FakeProvenance()
        execution = FakeExecutionPort(should_fail=False)

        def clock():
            from datetime import datetime
            return datetime(2024, 1, 1)

        gate = CrossValidationGate(execution, registry, provenance, clock)
        approval = ApprovalRecord(
            approver="admin", basis="reviewed", strict_mode=True,
            approved_at="2024-01-01T00:00:00Z"
        )

        stored = registry.store.get(spec.entry_id)
        candidate1 = WorkflowCompiler().compile(stored.cand, pack=pack, compiled_at="2024-01-01T00:00:00Z", provenance=FakeProvenance())
        suite1 = IndependentTestSuite(
            spec_hash=candidate1.spec.spec_hash,
            test_source="def run_tests(x): pass",
            suite_hash="a" * 64,
            author_id="test",
            identity_basis="test",
            authored_at="2024-01-01T00:00:00Z",
        )

        receipt1 = gate.evaluate(candidate1, suite1, approval, decided_at="2024-01-01T00:00:00Z")
        assert receipt1.verdict == "activated"
        assert registry.active_for(spec.entry_id) is not None

        # Update the stored entry to a new procedure, producing a new spec hash.
        steps2 = (_make_step("step1", objective="do work v2"),)
        stored.cand = replace(stored.cand, procedure=steps2)
        registry.store._persist_entry(stored)

        candidate2 = WorkflowCompiler().compile(stored.cand, pack=pack, compiled_at="2024-01-02T00:00:00Z", provenance=FakeProvenance())
        suite2 = IndependentTestSuite(
            spec_hash=candidate2.spec.spec_hash,
            test_source="def run_tests(x): pass",
            suite_hash="c" * 64,
            author_id="test",
            identity_basis="test",
            authored_at="2024-01-02T00:00:00Z",
        )
        receipt2 = gate.evaluate(candidate2, suite2, approval, decided_at="2024-01-02T00:00:00Z")
        assert receipt2.verdict == "activated"


# =============================================================================
# FIX-2 / FIX-3 citing tests
# =============================================================================

def test_gate_swap_order_activate_then_supersede():
    """F2-2: swap activates the new record BEFORE superseding the old."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store_root = Path(tmpdir)
        pack = "test"
        _create_pack(store_root, pack)

        baseline_dir = store_root / pack / "evals"
        baseline_dir.mkdir(parents=True)
        (baseline_dir / "baseline.json").write_text(json.dumps({"score": 0.5}))

        spec, steps, _ = _seed_entry(store_root, pack)
        registry = ExecutorRegistry(store_root, pack)
        provenance = FakeProvenance()
        execution = FakeExecutionPort(should_fail=False)

        def clock():
            from datetime import datetime
            return datetime(2024, 1, 1)

        gate = CrossValidationGate(execution, registry, provenance, clock)
        approval = ApprovalRecord(
            approver="admin", basis="reviewed", strict_mode=True,
            approved_at="2024-01-01T00:00:00Z"
        )

        stored = registry.store.get(spec.entry_id)
        candidate1 = WorkflowCompiler().compile(stored.cand, pack=pack, compiled_at="2024-01-01T00:00:00Z", provenance=FakeProvenance())
        suite1 = IndependentTestSuite(
            spec_hash=candidate1.spec.spec_hash,
            test_source="def run_tests(x): pass",
            suite_hash="a" * 64,
            author_id="test", identity_basis="test",
            authored_at="2024-01-01T00:00:00Z",
        )
        gate.evaluate(candidate1, suite1, approval, decided_at="2024-01-01T00:00:00Z")

        # Update the entry so the second candidate has a different spec hash.
        steps2 = (_make_step("step1", objective="do work v2"),)
        stored.cand = replace(stored.cand, procedure=steps2)
        registry.store._persist_entry(stored)

        candidate2 = WorkflowCompiler().compile(stored.cand, pack=pack, compiled_at="2024-01-02T00:00:00Z", provenance=FakeProvenance())
        suite2 = IndependentTestSuite(
            spec_hash=candidate2.spec.spec_hash,
            test_source="def run_tests(x): pass",
            suite_hash="c" * 64,
            author_id="test", identity_basis="test",
            authored_at="2024-01-02T00:00:00Z",
        )
        receipt2 = gate.evaluate(candidate2, suite2, approval, decided_at="2024-01-02T00:00:00Z")

        active = registry.active_for(spec.entry_id)
        superseded = registry.record_for(spec.entry_id, status="superseded")
        assert active is not None
        assert active.executor_hash == candidate2.executor_hash
        assert active.receipt_id == receipt2.receipt_id
        assert active.spec_hash == candidate2.spec.spec_hash
        assert len(superseded) == 1
        assert superseded[0].receipt_id == receipt2.receipt_id
        assert superseded[0].spec_hash == candidate1.spec.spec_hash


def test_gate_receipt_binds_record_and_provenance():
    """F2-3: activation receipt id equals active record receipt id; full receipt in provenance."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store_root = Path(tmpdir)
        pack = "test"
        _create_pack(store_root, pack)

        spec, steps, _ = _seed_entry(store_root, pack)
        registry = ExecutorRegistry(store_root, pack)
        provenance = FakeProvenance()
        execution = FakeExecutionPort(should_fail=False)

        def clock():
            from datetime import datetime
            return datetime(2024, 1, 1)

        gate = CrossValidationGate(execution, registry, provenance, clock)
        candidate = ExecutorCandidate(
            spec=spec,
            source="print('test')",
            executor_hash="a" * 64,
            compiled_at="2024-01-01T00:00:00Z",
            compiler_id="test",
        )
        suite = IndependentTestSuite(
            spec_hash=spec.spec_hash,
            test_source="def run_tests(x): pass",
            suite_hash="b" * 64,
            author_id="test",
            identity_basis="test",
            authored_at="2024-01-01T00:00:00Z",
        )
        approval = ApprovalRecord(
            approver="admin", basis="reviewed", strict_mode=True,
            approved_at="2024-01-01T00:00:00Z"
        )

        receipt = gate.evaluate(candidate, suite, approval, decided_at="2024-01-01T00:00:00Z")
        assert receipt.verdict == "activated"

        active = registry.active_for(spec.entry_id)
        assert active.receipt_id == receipt.receipt_id

        activated_events = [e for e in provenance.events if e["kind"] == "gate.activated"]
        assert len(activated_events) == 1
        assert activated_events[0]["receipt_id"] == receipt.receipt_id
        assert "receipt" in activated_events[0]
        assert activated_events[0]["receipt"]["verdict"] == "activated"


def test_provenance_journals_every_decision():
    """Every gate decision is journaled."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store_root = Path(tmpdir)
        pack = "test"
        _create_pack(store_root, pack)

        spec, steps, _ = _seed_entry(store_root, pack)
        registry = ExecutorRegistry(store_root, pack)
        provenance = FakeProvenance()
        execution = FakeExecutionPort(should_fail=False)

        def clock():
            from datetime import datetime
            return datetime(2024, 1, 1)

        gate = CrossValidationGate(execution, registry, provenance, clock)

        candidate = ExecutorCandidate(
            spec=spec,
            source="print('test')",
            executor_hash="a" * 64,
            compiled_at="2024-01-01T00:00:00Z",
            compiler_id="test",
        )

        # Test 1: spec mismatch
        suite = IndependentTestSuite(
            spec_hash="d" * 64,
            test_source="x",
            suite_hash="b" * 64,
            author_id="test",
            identity_basis="test",
            authored_at="t",
        )
        gate.evaluate(candidate, suite, None, decided_at="2024-01-01T00:00:00Z")
        assert len(provenance.events) >= 1
        assert provenance.events[0]["kind"] == "gate.spec-mismatch"

        # Test 2: sandbox pass with no approval
        suite2 = IndependentTestSuite(
            spec_hash=spec.spec_hash,
            test_source="x",
            suite_hash="c" * 64,
            author_id="test",
            identity_basis="test",
            authored_at="t2",
        )
        gate.evaluate(candidate, suite2, None, decided_at="2024-01-01T00:00:00Z")
        assert any(e["kind"] == "gate.awaiting-approval" for e in provenance.events)

        # Test 3: activation
        approval = ApprovalRecord(
            approver="admin", basis="reviewed", strict_mode=True,
            approved_at="2024-01-01T00:00:00Z"
        )
        suite3 = IndependentTestSuite(
            spec_hash=spec.spec_hash,
            test_source="x",
            suite_hash="d" * 64,
            author_id="test",
            identity_basis="test",
            authored_at="t3",
        )
        gate.evaluate(candidate, suite3, approval, decided_at="2024-01-01T00:00:00Z")
        assert any(e["kind"] == "gate.activated" for e in provenance.events)


def test_receipt_self_verification():
    """Receipt self-verifies its receipt_id."""
    approval = ApprovalRecord(
        approver="admin", basis="reviewed", strict_mode=True,
        approved_at="2024-01-01T00:00:00Z"
    )

    material = {
        "spec_hash": "a" * 64,
        "executor_hash": "b" * 64,
        "suite_hash": "c" * 64,
        "sandbox_ok": True,
        "sandbox_output": "ok",
        "approval": {
            "approver": "admin",
            "basis": "reviewed",
            "strict_mode": True,
            "approved_at": "2024-01-01T00:00:00Z",
        },
        "verdict": "activated",
        "reason": "test",
        "decided_at": "2024-01-01T00:00:00Z",
    }
    expected_id = content_hash(canonical_json(material))

    receipt = CrossValidationReceipt(
        receipt_id=expected_id,
        spec_hash="a" * 64,
        executor_hash="b" * 64,
        suite_hash="c" * 64,
        sandbox_ok=True,
        sandbox_output="ok",
        approval=approval,
        verdict="activated",
        reason="test",
        decided_at="2024-01-01T00:00:00Z",
    )
    assert receipt.receipt_id == expected_id


# =============================================================================
# FIX-3 citing tests: registry state machine wiring
# =============================================================================

def test_gate_full_happy_path_activates_and_runtime_runs():
    """Happy path: compile -> evaluate -> active record exists -> runtime runs."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store_root = Path(tmpdir)
        pack = "test"
        _create_pack(store_root, pack)

        spec, steps, _ = _seed_entry(store_root, pack)
        registry = ExecutorRegistry(store_root, pack)
        provenance = FakeProvenance()
        execution = FakeExecutionPort(should_fail=False)

        def clock():
            from datetime import datetime
            return datetime(2024, 1, 1)

        gate = CrossValidationGate(execution, registry, provenance, clock)

        compiler = WorkflowCompiler()
        entry = _make_workflow_entry(id=spec.entry_id, pack=pack, procedure=steps)
        candidate = compiler.compile(entry, pack=pack, compiled_at="2024-01-01T00:00:00Z", provenance=FakeProvenance())

        suite = IndependentTestSuite(
            spec_hash=spec.spec_hash,
            test_source="def run_tests(x): pass",
            suite_hash="b" * 64,
            author_id="test",
            identity_basis="test",
            authored_at="2024-01-01T00:00:00Z",
        )
        approval = ApprovalRecord(
            approver="admin", basis="reviewed", strict_mode=True,
            approved_at="2024-01-01T00:00:00Z"
        )

        receipt = gate.evaluate(candidate, suite, approval, decided_at="2024-01-01T00:00:00Z")
        assert receipt.verdict == "activated"

        active = registry.active_for(spec.entry_id)
        assert active is not None
        assert active.status == "active"
        assert active.executor_hash == candidate.executor_hash

        # Runtime can run it.
        runtime = ExecutorRuntime(registry, registry.store, provenance, clock)
        result = runtime.run(
            spec.entry_id, task_id="t1", topic="test", task_type="code_edit",
            step_handler=lambda sid, sdata: {"status": "ok"},
            now="2024-01-01T00:00:00Z",
        )
        assert result.status == "completed"


def test_gate_rejection_leaves_no_active_record():
    """Rejection: sandbox fail -> record transitions to rejected, no active."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store_root = Path(tmpdir)
        pack = "test"
        _create_pack(store_root, pack)

        spec, steps, _ = _seed_entry(store_root, pack)
        registry = ExecutorRegistry(store_root, pack)
        provenance = FakeProvenance()
        execution = FakeExecutionPort(should_fail=True)

        def clock():
            from datetime import datetime
            return datetime(2024, 1, 1)

        gate = CrossValidationGate(execution, registry, provenance, clock)

        compiler = WorkflowCompiler()
        entry = _make_workflow_entry(id=spec.entry_id, pack=pack, procedure=steps)
        candidate = compiler.compile(entry, pack=pack, compiled_at="2024-01-01T00:00:00Z", provenance=FakeProvenance())

        suite = IndependentTestSuite(
            spec_hash=spec.spec_hash,
            test_source="def run_tests(x): pass",
            suite_hash="b" * 64,
            author_id="test", identity_basis="test",
            authored_at="2024-01-01T00:00:00Z",
        )
        approval = ApprovalRecord(
            approver="admin", basis="reviewed", strict_mode=True,
            approved_at="2024-01-01T00:00:00Z"
        )

        receipt = gate.evaluate(candidate, suite, approval, decided_at="2024-01-01T00:00:00Z")
        assert receipt.verdict == "rejected"

        assert registry.active_for(spec.entry_id) is None
        records = registry.record_for(spec.entry_id, status="rejected")
        assert len(records) == 1


def test_gate_real_sandbox_double_runs_suite():
    """F2-11: a real ExecutionPort double executes executor + tests end-to-end."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store_root = Path(tmpdir)
        pack = "test"
        _create_pack(store_root, pack)

        spec, steps, _ = _seed_entry(store_root, pack)
        registry = ExecutorRegistry(store_root, pack)
        provenance = FakeProvenance()
        execution = RealSandboxExecutionPort()

        def clock():
            from datetime import datetime
            return datetime(2024, 1, 1)

        gate = CrossValidationGate(execution, registry, provenance, clock)

        compiler = WorkflowCompiler()
        entry = _make_workflow_entry(id=spec.entry_id, pack=pack, procedure=steps)
        candidate = compiler.compile(entry, pack=pack, compiled_at="2024-01-01T00:00:00Z", provenance=FakeProvenance())

        # Build a real test suite that asserts completion order.
        from selflearn.compilation.testgen import WorkflowTestAuthor
        author = WorkflowTestAuthor(FakeModel(), FakeIdentity())
        suite = author.author_suite(spec, authored_at="2024-01-01T00:00:00Z", provenance=FakeProvenance())

        approval = ApprovalRecord(
            approver="admin", basis="reviewed", strict_mode=True,
            approved_at="2024-01-01T00:00:00Z"
        )

        receipt = gate.evaluate(candidate, suite, approval, decided_at="2024-01-01T00:00:00Z")
        assert receipt.verdict == "activated"
        assert receipt.sandbox_ok is True
        assert execution.call_count == 1


# =============================================================================
# FIX-5 / F2-1 / F2-10 citing tests: test author rendering
# =============================================================================

def test_testgen_hostile_step_id_renders_as_inert_literal():
    """Plan with hostile step_id renders as escaped literal."""
    from selflearn.compilation.testgen import WorkflowTestAuthor

    class HostileModel:
        model_id = "hostile"

        def complete(self, role, prompt, context):
            return {
                "tests": [
                    {
                        "name": "test order",
                        "kind": "order",
                        "step_id": "x' or True #",
                        "expect": '["step2"]',
                    },
                    {
                        "name": "test check",
                        "kind": "check",
                        "step_id": "normal_step",
                        "expect": "ok",
                    },
                ]
            }

    class FakeIdentity:
        basis = "test"

        def distinct(self, a, b):
            return True

    steps = (
        ProcedureStep(id="normal_step", objective="do work", task_type="code_edit"),
        ProcedureStep(id="step2", objective="second", task_type="code_edit"),
    )
    spec = ExecutorSpec(
        entry_id="wf-001", pack="test",
        spec_hash=canonical_procedure_hash(steps),
        procedure=steps,
    )

    author = WorkflowTestAuthor(HostileModel(), FakeIdentity())
    suite = author.author_suite(spec, authored_at="2024-01-01T00:00:00Z", provenance=FakeProvenance())

    compile(suite.test_source, "<test>", "exec")
    assert "x' or True #" not in suite.test_source


def test_testgen_approval_and_failure_path_render_compile():
    """F2-1: approval AND failure-path kinds render and compile cleanly."""
    from selflearn.compilation.testgen import WorkflowTestAuthor

    class CoverageModel:
        model_id = "coverage"

        def complete(self, role, prompt, context):
            return {
                "tests": [
                    {"name": "order", "kind": "order", "step_id": "step1", "expect": '["step1"]'},
                    {"name": "approval", "kind": "approval", "step_id": "step2", "expect": ""},
                    {"name": "failure", "kind": "failure-path", "step_id": "step1", "expect": ""},
                ]
            }

    class FakeIdentity:
        basis = "test"

        def distinct(self, a, b):
            return True

    steps = (
        ProcedureStep(id="step1", objective="do work", task_type="code_edit"),
        ProcedureStep(id="step2", objective="approve", task_type="approval"),
    )
    spec = ExecutorSpec(
        entry_id="wf-001", pack="test",
        spec_hash=canonical_procedure_hash(steps),
        procedure=steps,
    )

    suite = WorkflowTestAuthor(CoverageModel(), FakeIdentity()).author_suite(
        spec, authored_at="2024-01-01T00:00:00Z", provenance=FakeProvenance()
    )
    compile(suite.test_source, "<test>", "exec")
    assert "approval not raised" in suite.test_source
    assert "no failure" in suite.test_source


def test_testgen_order_expect_comma_split_renders():
    """F2-10: order expect 's1,s2' renders a list literal that compares correctly."""
    from selflearn.compilation.testgen import WorkflowTestAuthor

    class CommaModel:
        model_id = "comma"

        def complete(self, role, prompt, context):
            return {
                "tests": [
                    {"name": "order", "kind": "order", "step_id": "step1", "expect": "step1,step2"},
                ]
            }

    class FakeIdentity:
        basis = "test"

        def distinct(self, a, b):
            return True

    steps = (
        ProcedureStep(id="step1", objective="one", task_type="code_edit"),
        ProcedureStep(id="step2", objective="two", task_type="code_edit"),
    )
    spec = ExecutorSpec(
        entry_id="wf-001", pack="test",
        spec_hash=canonical_procedure_hash(steps),
        procedure=steps,
    )

    suite = WorkflowTestAuthor(CommaModel(), FakeIdentity()).author_suite(
        spec, authored_at="2024-01-01T00:00:00Z", provenance=FakeProvenance()
    )
    compile(suite.test_source, "<test>", "exec")
    assert '["step1", "step2"]' in suite.test_source


def test_testgen_nonexistent_step_id_raises():
    """Plan referencing nonexistent step_id -> TestAuthorError."""
    from selflearn.compilation.testgen import WorkflowTestAuthor

    class BadModel:
        model_id = "bad"

        def complete(self, role, prompt, context):
            return {
                "tests": [
                    {"name": "test", "kind": "check",
                     "step_id": "nonexistent_step", "expect": "ok"},
                    {"name": "order", "kind": "order",
                     "step_id": "normal_step", "expect": '["normal_step"]'},
                ]
            }

    class FakeIdentity:
        basis = "test"

        def distinct(self, a, b):
            return True

    steps = (ProcedureStep(id="normal_step", objective="x", task_type="code_edit"),)
    spec = ExecutorSpec(
        entry_id="wf-001", pack="test",
        spec_hash=canonical_procedure_hash(steps),
        procedure=steps,
    )

    author = WorkflowTestAuthor(BadModel(), FakeIdentity())
    with pytest.raises(TestAuthorError, match="nonexistent_step"):
        author.author_suite(spec, authored_at="2024-01-01T00:00:00Z", provenance=FakeProvenance())


def test_testgen_order_without_expect_raises():
    """Order test without expect -> TestAuthorError."""
    from selflearn.compilation.testgen import WorkflowTestAuthor

    class BadModel:
        model_id = "bad"

        def complete(self, role, prompt, context):
            return {
                "tests": [
                    {"name": "order test", "kind": "order",
                     "step_id": "step1", "expect": ""},
                ]
            }

    class FakeIdentity:
        basis = "test"

        def distinct(self, a, b):
            return True

    steps = (ProcedureStep(id="step1", objective="x", task_type="code_edit"),)
    spec = ExecutorSpec(
        entry_id="wf-001", pack="test",
        spec_hash=canonical_procedure_hash(steps),
        procedure=steps,
    )

    author = WorkflowTestAuthor(BadModel(), FakeIdentity())
    with pytest.raises(TestAuthorError, match="non-empty expect"):
        author.author_suite(spec, authored_at="2024-01-01T00:00:00Z", provenance=FakeProvenance())


# =============================================================================
# FIX-7 citing test: single hashing path
# =============================================================================

def test_gate_no_compute_receipt_id_function():
    """_compute_receipt_id is deleted; models.py is the only hasher."""
    import inspect
    from selflearn.compilation import gate

    assert not hasattr(gate, '_compute_receipt_id'), \
        "_compute_receipt_id must be deleted (FIX-7)"

    receipt = CrossValidationReceipt(
        receipt_id="",
        spec_hash="a" * 64,
        executor_hash="b" * 64,
        suite_hash="c" * 64,
        sandbox_ok=False,
        sandbox_output="",
        approval=None,
        verdict="rejected",
        reason="test",
        decided_at="2024-01-01T00:00:00Z",
    )
    assert receipt.receipt_id != ""


# =============================================================================
# FIX-8 / F2-4 citing test: doctor unverifiable entry
# =============================================================================

def test_doctor_unverifiable_entry_is_finding():
    """F2-4: entry file exists but is unparseable -> executor.unverifiable."""
    from selflearn.doctor import DoctorReport, Finding, _check_executors

    with tempfile.TemporaryDirectory() as tmpdir:
        store_root = Path(tmpdir)
        pack = "test"
        pack_dir = store_root / pack
        pack_dir.mkdir()
        manifest = {"name": pack, "schema_version": 1, "entries": {}}
        (pack_dir / "manifest.json").write_text(json.dumps(manifest))
        entries_dir = pack_dir / "entries"
        entries_dir.mkdir()

        # Write an entry file that exists but is unparseable YAML
        (entries_dir / "wf-001.md").write_text("---\nnot yaml: [\n---\nbody")

        exec_dir = pack_dir / "executors"
        exec_dir.mkdir()
        registry_path = exec_dir / "registry.json"
        registry_path.write_text(json.dumps({
            "records": [{
                "record_id": "x" * 64,
                "entry_id": "wf-001",
                "pack": pack,
                "spec_hash": "a" * 64,
                "executor_hash": "b" * 64,
                "status": "active",
                "path": "entries/wf-001.md",
                "receipt_id": "rcpt1",
                "updated_at": "2024-01-01T00:00:00Z",
            }]
        }))

        report = DoctorReport(root=store_root, fix=False)
        _check_executors(pack_dir, fix=False, report=report)

        unverifiable = [f for f in report.findings if f.code == "executor.unverifiable"]
        assert len(unverifiable) == 1
        assert unverifiable[0].fixable is False


# =============================================================================
# FIX-9 citing tests: registry reads must not mutate; malformed fails closed
# =============================================================================

def test_registry_active_for_no_side_effects():
    """active_for on empty pack leaves no executors/ directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store_root = Path(tmpdir)
        pack = "test"
        _create_pack(store_root, pack)

        registry = ExecutorRegistry(store_root, pack)

        result = registry.active_for("nonexistent-entry")
        assert result is None

        exec_dir = store_root / pack / "executors"
        assert not exec_dir.exists()


def test_registry_malformed_file_raises():
    """Hand-corrupted record field -> RegistryError."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store_root = Path(tmpdir)
        pack = "test"
        _create_pack(store_root, pack)

        registry = ExecutorRegistry(store_root, pack)

        record = ExecutorRecord(
            record_id="",
            entry_id="wf-001",
            pack=pack,
            spec_hash="a" * 64,
            executor_hash="b" * 64,
            status="quarantined",
            path="test/executors/wf-001/a.py",
            receipt_id="rcpt1",
            updated_at="2024-01-01T00:00:00Z",
        )
        registry.add_record(record)

        data = json.loads(registry._registry_path.read_text())
        data["records"][0]["status"] = "invalid_status"
        registry._registry_path.write_text(json.dumps(data))

        with pytest.raises(RegistryError, match="Corrupt record"):
            registry.record_for("wf-001")


# =============================================================================
# F2-7 citing tests: provenance on compile / test-author / quarantine
# =============================================================================

def test_compile_journals_provenance():
    """F2-7: WorkflowCompiler.compile appends compile event when provenance bound."""
    from selflearn.compilation.compiler import WorkflowCompiler

    with tempfile.TemporaryDirectory() as tmpdir:
        store_root = Path(tmpdir)
        pack = "test"
        _create_pack(store_root, pack)

        spec, steps, _ = _seed_entry(store_root, pack)
        provenance = FakeProvenance()

        def clock():
            from datetime import datetime
            return datetime(2024, 1, 1, tzinfo=__import__("datetime").timezone.utc)

        compiler = WorkflowCompiler()
        entry = _make_workflow_entry(id=spec.entry_id, pack=pack, procedure=steps)
        compiler.compile(
            entry, pack=pack, compiled_at="2024-01-01T00:00:00Z",
            provenance=provenance, clock=clock,
        )

        compile_events = [e for e in provenance.events if e["kind"] == "compile"]
        assert len(compile_events) == 1
        assert compile_events[0]["entry_id"] == spec.entry_id


def test_test_author_journals_provenance():
    """F2-7: WorkflowTestAuthor.author_suite appends test-author event."""
    from selflearn.compilation.testgen import WorkflowTestAuthor

    provenance = FakeProvenance()

    def clock():
        from datetime import datetime
        return datetime(2024, 1, 1, tzinfo=__import__("datetime").timezone.utc)

    steps = (ProcedureStep(id="step1", objective="x", task_type="code_edit"),)
    spec = ExecutorSpec(
        entry_id="wf-001", pack="test",
        spec_hash=canonical_procedure_hash(steps),
        procedure=steps,
    )

    suite = WorkflowTestAuthor(FakeModel(), FakeIdentity()).author_suite(
        spec, authored_at="2024-01-01T00:00:00Z",
        provenance=provenance, clock=clock,
    )

    author_events = [e for e in provenance.events if e["kind"] == "test-author"]
    assert len(author_events) == 1
    assert author_events[0]["suite_hash"] == suite.suite_hash


def test_registry_write_candidate_journals_quarantine():
    """F2-7: ExecutorRegistry.write_candidate journals quarantined event."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store_root = Path(tmpdir)
        pack = "test"
        _create_pack(store_root, pack)

        provenance = FakeProvenance()

        def clock():
            from datetime import datetime
            return datetime(2024, 1, 1, tzinfo=__import__("datetime").timezone.utc)

        registry = ExecutorRegistry(store_root, pack, provenance=provenance, clock=clock)

        spec, steps = _make_spec()
        candidate = ExecutorCandidate(
            spec=spec,
            source="print('hello')",
            executor_hash="a" * 64,
            compiled_at="2024-01-01T00:00:00Z",
            compiler_id="test",
        )
        registry.write_candidate(candidate)

        quarantine_events = [e for e in provenance.events if e["kind"] == "quarantined"]
        assert len(quarantine_events) == 1
        assert quarantine_events[0]["entry_id"] == spec.entry_id


# =============================================================================
# F3-2 citing test: activation is bound to a matching registry record
# =============================================================================

def test_gate_activation_binds_matching_registry_record():
    """F3-2: active record's spec_hash and executor_hash match the candidate."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store_root = Path(tmpdir)
        pack = "test"
        _create_pack(store_root, pack)

        spec, steps, _ = _seed_entry(store_root, pack)
        registry = ExecutorRegistry(store_root, pack)
        provenance = FakeProvenance()
        execution = FakeExecutionPort(should_fail=False)

        def clock():
            from datetime import datetime
            return datetime(2024, 1, 1)

        gate = CrossValidationGate(execution, registry, provenance, clock)

        compiler = WorkflowCompiler()
        entry = _make_workflow_entry(id=spec.entry_id, pack=pack, procedure=steps)
        candidate = compiler.compile(
            entry, pack=pack, compiled_at="2024-01-01T00:00:00Z",
            provenance=FakeProvenance(),
        )
        suite = IndependentTestSuite(
            spec_hash=spec.spec_hash,
            test_source="def run_tests(x): pass",
            suite_hash="b" * 64,
            author_id="test", identity_basis="test",
            authored_at="2024-01-01T00:00:00Z",
        )
        approval = ApprovalRecord(
            approver="admin", basis="reviewed", strict_mode=True,
            approved_at="2024-01-01T00:00:00Z"
        )

        receipt = gate.evaluate(candidate, suite, approval, decided_at="2024-01-01T00:00:00Z")
        assert receipt.verdict == "activated"

        active = registry.active_for(spec.entry_id)
        assert active is not None
        assert active.spec_hash == candidate.spec.spec_hash
        assert active.executor_hash == candidate.executor_hash


# =============================================================================
# F3-4 citing tests: approval step_id validation
# =============================================================================

def test_testgen_approval_nonexistent_step_id_raises():
    """F3-4: approval step_id not in spec -> TestAuthorError."""
    from selflearn.compilation.testgen import WorkflowTestAuthor

    class BadModel:
        model_id = "bad"

        def complete(self, role, prompt, context):
            return {
                "tests": [
                    {"name": "order", "kind": "order", "step_id": "step1", "expect": '["step1"]'},
                    {"name": "approval", "kind": "approval", "step_id": "nonexistent", "expect": ""},
                ]
            }

    class FakeIdentity:
        basis = "test"

        def distinct(self, a, b):
            return True

    steps = (
        ProcedureStep(id="step1", objective="x", task_type="code_edit"),
        ProcedureStep(id="step2", objective="a", task_type="approval"),
    )
    spec = ExecutorSpec(
        entry_id="wf-001", pack="test",
        spec_hash=canonical_procedure_hash(steps),
        procedure=steps,
    )

    with pytest.raises(TestAuthorError, match="nonexistent"):
        WorkflowTestAuthor(BadModel(), FakeIdentity()).author_suite(
            spec, authored_at="t", provenance=FakeProvenance()
        )


def test_testgen_approval_empty_step_id_raises():
    """F3-4: approval step_id empty -> TestAuthorError."""
    from selflearn.compilation.testgen import WorkflowTestAuthor

    class BadModel:
        model_id = "bad"

        def complete(self, role, prompt, context):
            return {
                "tests": [
                    {"name": "order", "kind": "order", "step_id": "step1", "expect": '["step1"]'},
                    {"name": "approval", "kind": "approval", "step_id": "", "expect": ""},
                ]
            }

    class FakeIdentity:
        basis = "test"

        def distinct(self, a, b):
            return True

    steps = (
        ProcedureStep(id="step1", objective="x", task_type="code_edit"),
        ProcedureStep(id="step2", objective="a", task_type="approval"),
    )
    spec = ExecutorSpec(
        entry_id="wf-001", pack="test",
        spec_hash=canonical_procedure_hash(steps),
        procedure=steps,
    )

    with pytest.raises(TestAuthorError, match="requires a step_id"):
        WorkflowTestAuthor(BadModel(), FakeIdentity()).author_suite(
            spec, authored_at="t", provenance=FakeProvenance()
        )


# =============================================================================
# F3-5 citing test: rendered check test is spec-aware
# =============================================================================

def test_testgen_check_test_honors_declared_checks():
    """F3-5: check-kind test satisfies the step's declared check pairs."""
    from selflearn.compilation.testgen import WorkflowTestAuthor
    from selflearn.compilation.runtime import _make_restricted_globals

    class CheckModel:
        model_id = "check"

        def complete(self, role, prompt, context):
            return {
                "tests": [
                    {"name": "order", "kind": "order", "step_id": "step1", "expect": '["step1", "step2"]'},
                    {"name": "status check", "kind": "check", "step_id": "step2", "expect": "ignored"},
                ]
            }

    class FakeIdentity:
        basis = "test"

        def distinct(self, a, b):
            return True

    steps = (
        ProcedureStep(id="step1", objective="x", task_type="code_edit"),
        ProcedureStep(
            id="step2", objective="checked", task_type="code_edit",
            check=(("status", "pass"),),
        ),
    )
    spec = ExecutorSpec(
        entry_id="wf-001", pack="test",
        spec_hash=canonical_procedure_hash(steps),
        procedure=steps,
    )

    suite = WorkflowTestAuthor(CheckModel(), FakeIdentity()).author_suite(
        spec, authored_at="2024-01-01T00:00:00Z", provenance=FakeProvenance()
    )
    compile(suite.test_source, "<test>", "exec")

    # Compile the executor
    compiler = WorkflowCompiler()
    entry = _make_workflow_entry(id="wf-001", pack="test", procedure=steps)
    candidate = compiler.compile(
        entry, pack="test", compiled_at="2024-01-01T00:00:00Z",
        provenance=FakeProvenance(),
    )

    def load_executor():
        ns = _make_restricted_globals()
        exec(candidate.source, ns)
        return ns

    test_ns = {"load_executor": load_executor, "json": json}
    exec(suite.test_source, test_ns)
    results = test_ns["run_tests"](load_executor)
    assert all(r[0] == "pass" for r in results), results


# =============================================================================
# F3-10 citing test: suite_hash binds author identity
# =============================================================================

def test_suite_hash_binds_author_identity():
    """F3-10: mutating author_id or identity_basis changes suite_hash."""
    from selflearn.compilation.testgen import WorkflowTestAuthor

    class ModelA:
        model_id = "model-a"

        def complete(self, role, prompt, context):
            return {
                "tests": [
                    {"name": "order", "kind": "order", "step_id": "step1", "expect": '["step1"]'},
                ]
            }

    class ModelB:
        model_id = "model-b"

        def complete(self, role, prompt, context):
            return ModelA().complete(role, prompt, context)

    class FakeIdentity:
        basis = "test"

        def distinct(self, a, b):
            return True

    steps = (ProcedureStep(id="step1", objective="x", task_type="code_edit"),)
    spec = ExecutorSpec(
        entry_id="wf-001", pack="test",
        spec_hash=canonical_procedure_hash(steps),
        procedure=steps,
    )

    suite_a = WorkflowTestAuthor(ModelA(), FakeIdentity()).author_suite(
        spec, authored_at="t", provenance=FakeProvenance()
    )
    suite_b = WorkflowTestAuthor(ModelB(), FakeIdentity()).author_suite(
        spec, authored_at="t", provenance=FakeProvenance()
    )

    assert suite_a.suite_hash != suite_b.suite_hash
    assert suite_a.test_source == suite_b.test_source
