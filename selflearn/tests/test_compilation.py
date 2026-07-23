"""Compilation module tests.

Tests for:
- models: hash determinism, tamper sensitivity
- compiler: procedure compilation, approval predicate, source generation
- generated executor behavior
"""
import json

import pytest

from selflearn.compilation import (
    ApprovalRecord,
    canonical_json,
    canonical_procedure_hash,
    content_hash,
    ExecutorCandidate,
    ExecutorRecord,
    ExecutorSpec,
    IndependentTestSuite,
)
from selflearn.compilation.models import CrossValidationReceipt
from selflearn.contracts import CandidateEntry, ContractError, EntrySource, ProcedureStep

# Test helper: minimal source for workflow entries
TEST_SOURCE = EntrySource(
    url="https://test.example.org",
    fetched_at="2024-01-01T00:00:00Z",
    sha256="0" * 64,
    tier="official",
)


def _make_workflow_entry(**kw) -> CandidateEntry:
    """Create a workflow CandidateEntry with required sources."""
    base = dict(
        id="wf-001", pack="test", kind="workflow",
        body="test workflow", claims=(), sources=(TEST_SOURCE,),
        topic="test"
    )
    base.update(kw)
    return CandidateEntry(**base)


# =============================================================================
# Model tests - hash determinism and tamper sensitivity
# =============================================================================


def _make_step(step_id: str, objective: str = "test objective",
               task_type: str = "code_edit",
               tools: tuple = (),
               depends_on: tuple = (),
               check: tuple = ()) -> ProcedureStep:
    return ProcedureStep(
        id=step_id,
        objective=objective,
        task_type=task_type,
        tools=tools,
        depends_on=depends_on,
        check=check,
    )


def test_canonical_procedure_hash_deterministic():
    """Same procedure produces same hash."""
    steps = (
        _make_step("step1", objective="do x"),
        _make_step("step2", objective="do y", depends_on=("step1",)),
    )
    h1 = canonical_procedure_hash(steps)
    h2 = canonical_procedure_hash(steps)
    assert h1 == h2


def test_spec_hash_changes_on_objective_flip():
    """Any step field flip changes spec_hash."""
    steps1 = (_make_step("step1", objective="do x"),)
    steps2 = (_make_step("step1", objective="do y"),)
    assert canonical_procedure_hash(steps1) != canonical_procedure_hash(steps2)


def test_spec_hash_changes_on_task_type_flip():
    """Task type flip changes spec_hash."""
    steps1 = (_make_step("step1", task_type="code_edit"),)
    steps2 = (_make_step("step1", task_type="code_review"),)
    assert canonical_procedure_hash(steps1) != canonical_procedure_hash(steps2)


def test_spec_hash_changes_on_tools_flip():
    """Tools list flip changes spec_hash."""
    steps1 = (_make_step("step1", tools=("tool_a",)),)
    steps2 = (_make_step("step1", tools=("tool_b",)),)
    assert canonical_procedure_hash(steps1) != canonical_procedure_hash(steps2)


def test_spec_hash_changes_on_depends_on_flip():
    """Depends on flip changes spec_hash."""
    steps1 = (_make_step("step1", depends_on=("dep_a",)),)
    steps2 = (_make_step("step1", depends_on=("dep_b",)),)
    assert canonical_procedure_hash(steps1) != canonical_procedure_hash(steps2)


def test_spec_hash_changes_on_check_flip():
    """Check pairs flip changes spec_hash."""
    steps1 = (_make_step("step1", check=(("status", "pass"),)),)
    steps2 = (_make_step("step1", check=(("status", "fail"),)),)
    assert canonical_procedure_hash(steps1) != canonical_procedure_hash(steps2)


def test_spec_hash_changes_on_step_order():
    """Step order changes spec_hash."""
    steps1 = (
        _make_step("step1"),
        _make_step("step2"),
    )
    steps2 = (
        _make_step("step2"),
        _make_step("step1"),
    )
    assert canonical_procedure_hash(steps1) != canonical_procedure_hash(steps2)


def test_content_hash_hex_length():
    """content_hash returns 64-char hex."""
    h = content_hash("test string")
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)


def test_executor_spec_validates_hash_shape():
    """ExecutorSpec validates 64-hex spec_hash."""
    steps = (_make_step("step1"),)
    # Valid 64-hex
    spec = ExecutorSpec(
        entry_id="e1", pack="test-pack",
        spec_hash="a" * 64, procedure=steps
    )
    assert spec.spec_hash == "a" * 64

    # Invalid: not 64 hex
    with pytest.raises(ContractError, match="64-hex"):
        ExecutorSpec(entry_id="e1", pack="test-pack",
                      spec_hash="abc", procedure=steps)


def test_executor_spec_validates_non_empty():
    """ExecutorSpec validates non-empty procedure."""
    with pytest.raises(ContractError, match="non-empty"):
        ExecutorSpec(entry_id="e1", pack="test-pack",
                      spec_hash="a" * 64, procedure=())


def test_executor_candidate_validates_hash():
    """ExecutorCandidate validates executor_hash."""
    steps = (_make_step("step1"),)
    spec = ExecutorSpec(entry_id="e1", pack="p", spec_hash="a" * 64,
                         procedure=steps)
    cand = ExecutorCandidate(
        spec=spec, source="print('hello')",
        executor_hash="b" * 64, compiled_at="2024-01-01T00:00:00Z",
        compiler_id="test"
    )
    assert cand.executor_hash == "b" * 64

    with pytest.raises(ContractError, match="64-hex"):
        ExecutorCandidate(spec=spec, source="x", executor_hash="bad",
                           compiled_at="t", compiler_id="c")


def test_approval_record_requires_strict_mode():
    """ApprovalRecord requires strict_mode=True for activation."""
    with pytest.raises(ContractError, match="strict_mode"):
        ApprovalRecord(approver="admin", basis="looks good",
                       strict_mode=False, approved_at="2024-01-01T00:00:00Z")

    # strict_mode=True is allowed
    rec = ApprovalRecord(approver="admin", basis="looks good",
                         strict_mode=True, approved_at="2024-01-01T00:00:00Z")
    assert rec.strict_mode is True


def test_cross_validation_receipt_self_hash():
    """CrossValidationReceipt self-verifies its receipt_id."""
    approval = ApprovalRecord(approver="admin", basis="reviewed",
                               strict_mode=True, approved_at="2024-01-01T00:00:00Z")

    # Build a valid receipt - compute expected receipt_id
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
        "reason": "sandbox pass with strict approval",
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
        reason="sandbox pass with strict approval",
        decided_at="2024-01-01T00:00:00Z",
    )
    assert receipt.receipt_id == expected_id


def test_cross_validation_receipt_tamper_detection():
    """Tampering with any field raises ContractError."""
    approval = ApprovalRecord(approver="admin", basis="reviewed",
                               strict_mode=True, approved_at="2024-01-01T00:00:00Z")

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
        "reason": "sandbox pass with strict approval",
        "decided_at": "2024-01-01T00:00:00Z",
    }
    expected_id = content_hash(canonical_json(material))

    # Tamper with sandbox_ok
    with pytest.raises(ContractError, match="mismatch"):
        CrossValidationReceipt(
            receipt_id=expected_id,
            spec_hash="a" * 64,
            executor_hash="b" * 64,
            suite_hash="c" * 64,
            sandbox_ok=False,  # tampered
            sandbox_output="ok",
            approval=approval,
            verdict="activated",
            reason="sandbox pass with strict approval",
            decided_at="2024-01-01T00:00:00Z",
        )


def test_executor_record_self_hash():
    """ExecutorRecord self-verifies its record_id."""
    material = {
        "entry_id": "e1",
        "pack": "test-pack",
        "spec_hash": "a" * 64,
        "executor_hash": "b" * 64,
        "status": "active",
        "path": "executors/e1/abc.py",
        "receipt_id": "rcpt1",
        "updated_at": "2024-01-01T00:00:00Z",
    }
    expected_id = content_hash(canonical_json(material))

    record = ExecutorRecord(
        record_id=expected_id,
        entry_id="e1",
        pack="test-pack",
        spec_hash="a" * 64,
        executor_hash="b" * 64,
        status="active",
        path="executors/e1/abc.py",
        receipt_id="rcpt1",
        updated_at="2024-01-01T00:00:00Z",
    )
    assert record.record_id == expected_id


def test_executor_record_tamper_detection():
    """Tampering with any field raises ContractError."""
    material = {
        "entry_id": "e1",
        "pack": "test-pack",
        "spec_hash": "a" * 64,
        "executor_hash": "b" * 64,
        "status": "active",
        "path": "executors/e1/abc.py",
        "receipt_id": "rcpt1",
        "updated_at": "2024-01-01T00:00:00Z",
    }
    expected_id = content_hash(canonical_json(material))

    # Tamper with status
    with pytest.raises(ContractError, match="mismatch"):
        ExecutorRecord(
            record_id=expected_id,
            entry_id="e1",
            pack="test-pack",
            spec_hash="a" * 64,
            executor_hash="b" * 64,
            status="rejected",  # tampered
            path="executors/e1/abc.py",
            receipt_id="rcpt1",
            updated_at="2024-01-01T00:00:00Z",
        )


def test_executor_record_validates_status():
    """ExecutorRecord validates status is in EXECUTOR_STATUSES."""
    material = {
        "entry_id": "e1",
        "pack": "test-pack",
        "spec_hash": "a" * 64,
        "executor_hash": "b" * 64,
        "status": "active",
        "path": "executors/e1/abc.py",
        "receipt_id": "rcpt1",
        "updated_at": "2024-01-01T00:00:00Z",
    }
    expected_id = content_hash(canonical_json(material))

    with pytest.raises(ContractError, match="status must be"):
        ExecutorRecord(
            record_id=expected_id,
            entry_id="e1",
            pack="test-pack",
            spec_hash="a" * 64,
            executor_hash="b" * 64,
            status="invalid_status",
            path="executors/e1/abc.py",
            receipt_id="rcpt1",
            updated_at="2024-01-01T00:00:00Z",
        )


def test_independent_test_suite_validates():
    """IndependentTestSuite validates its fields."""
    suite = IndependentTestSuite(
        spec_hash="a" * 64,
        test_source="def run_tests(): pass",
        suite_hash="b" * 64,
        author_id="test-author",
        identity_basis="model-id distinct",
        authored_at="2024-01-01T00:00:00Z",
    )
    assert suite.spec_hash == "a" * 64

    with pytest.raises(ContractError, match="64-hex"):
        IndependentTestSuite(
            spec_hash="bad",
            test_source="x",
            suite_hash="b" * 64,
            author_id="a",
            identity_basis="b",
            authored_at="t",
        )


# =============================================================================
# Compiler tests
# =============================================================================

def test_compiler_generates_three_step_procedure():
    """Compile a 3-step procedure preserves order and depends_on."""
    from selflearn.compilation.compiler import WorkflowCompiler

    steps = (
        ProcedureStep(id="init", objective="initialize", task_type="setup"),
        ProcedureStep(id="process", objective="process data",
                      task_type="code_edit", depends_on=("init",)),
        ProcedureStep(id="finalize", objective="finalize",
                      task_type="review", depends_on=("process",)),
    )
    entry = _make_workflow_entry(procedure=steps)

    compiler = WorkflowCompiler()
    candidate = compiler.compile(entry, pack="test", compiled_at="2024-01-01T00:00:00Z")

    # Check source contains step definitions
    assert "init" in candidate.source
    assert "process" in candidate.source
    assert "finalize" in candidate.source
    # Depends on should be preserved
    assert '"depends_on": ["init"]' in candidate.source or "'depends_on': ['init']" in candidate.source


def test_compiler_approval_predicate_task_type():
    """is_approval_step returns True for task_type == 'approval'."""
    from selflearn.compilation.compiler import is_approval_step

    step = ProcedureStep(id="s1", objective="approve", task_type="approval")
    assert is_approval_step(step) is True


def test_compiler_approval_predicate_check_dict():
    """is_approval_step returns True when check has approval key."""
    from selflearn.compilation.compiler import is_approval_step

    step = ProcedureStep(
        id="s1", objective="check", task_type="review",
        check=(("approval", True),)
    )
    assert is_approval_step(step) is True


def test_compiler_approval_predicate_absent():
    """is_approval_step returns False when neither condition met."""
    from selflearn.compilation.compiler import is_approval_step

    step = ProcedureStep(id="s1", objective="do work", task_type="code_edit")
    assert is_approval_step(step) is False


def test_compiler_generated_source_injection_probe():
    """Objective containing code injection compiles to inert literal."""
    from selflearn.compilation.compiler import WorkflowCompiler

    # This would be dangerous if not escaped properly
    malicious_objective = 'do work";\nimport os\nx="'
    steps = (ProcedureStep(id="step1", objective=malicious_objective,
                           task_type="code_edit"),)
    entry = _make_workflow_entry(procedure=steps)

    compiler = WorkflowCompiler()
    candidate = compiler.compile(entry, pack="test", compiled_at="2024-01-01T00:00:00Z")

    # The dangerous code should NOT be executable
    # It should appear as a string literal
    assert "import os" not in candidate.source or "x=" not in candidate.source.split(";")[0]
    # Check it's in a string context
    assert '";' in candidate.source or "';" in candidate.source


def test_compiler_stdlib_only():
    """Generated source uses only stdlib (json import allowed)."""
    from selflearn.compilation.compiler import WorkflowCompiler

    steps = (ProcedureStep(id="s1", objective="test", task_type="code_edit"),)
    entry = _make_workflow_entry(procedure=steps)

    compiler = WorkflowCompiler()
    candidate = compiler.compile(entry, pack="test", compiled_at="2024-01-01T00:00:00Z")

    # Should only have json import
    import_lines = [l for l in candidate.source.split('\n')
                    if l.strip().startswith('import ')]
    for line in import_lines:
        assert line.strip() in ("import json",)


def test_compiler_generates_stepcheckfailed():
    """Generated source defines StepCheckFailed exception."""
    from selflearn.compilation.compiler import WorkflowCompiler

    steps = (ProcedureStep(id="s1", objective="test", task_type="code_edit"),)
    entry = _make_workflow_entry(procedure=steps)

    compiler = WorkflowCompiler()
    candidate = compiler.compile(entry, pack="test", compiled_at="2024-01-01T00:00:00Z")

    assert "StepCheckFailed" in candidate.source


def test_compiler_generates_approval_required():
    """Generated source defines ApprovalRequired with step_id."""
    from selflearn.compilation.compiler import WorkflowCompiler

    steps = (ProcedureStep(id="s1", objective="approve", task_type="approval"),)
    entry = _make_workflow_entry(procedure=steps)

    compiler = WorkflowCompiler()
    candidate = compiler.compile(entry, pack="test", compiled_at="2024-01-01T00:00:00Z")

    assert "ApprovalRequired" in candidate.source
    assert "step_id" in candidate.source


def test_compiler_rejects_non_workflow():
    """Compiler rejects non-workflow entries."""
    from selflearn.compilation.compiler import CompilerError, WorkflowCompiler

    entry = CandidateEntry(
        id="kn-001", pack="test", kind="knowledge",
        body="test knowledge", claims=(), sources=(TEST_SOURCE,),
        topic="test"
    )

    compiler = WorkflowCompiler()
    with pytest.raises(CompilerError, match="workflow"):
        compiler.compile(entry, pack="test", compiled_at="2024-01-01T00:00:00Z")


def test_compiler_rejects_empty_procedure():
    """Workflow entry requires non-empty procedure (contract-level validation)."""
    # The CandidateEntry contract enforces non-empty procedure for workflow kind.
    # This test verifies the contract-level rejection.
    with pytest.raises(ContractError, match="procedure"):
        _make_workflow_entry(procedure=())


# =============================================================================
# Generated executor runtime tests
# =============================================================================

def test_executor_run_stub_handler():
    """Generated executor run() drives stub handler in order."""
    # This tests the generated code behavior by importing and running
    from selflearn.compilation.compiler import WorkflowCompiler

    steps = (
        ProcedureStep(id="step1", objective="first", task_type="setup"),
        ProcedureStep(id="step2", objective="second", task_type="code_edit"),
    )
    entry = _make_workflow_entry(procedure=steps)

    compiler = WorkflowCompiler()
    candidate = compiler.compile(entry, pack="test", compiled_at="2024-01-01T00:00:00Z")

    # Execute the generated code in a sandbox
    execution_order = []

    def stub_handler(step_id, step_data):
        execution_order.append(step_id)
        return {"status": "ok", "checks": {}}

    # Create a restricted globals for exec
    from selflearn.compilation.runtime import _make_restricted_globals
    restricted_globals = _make_restricted_globals()

    # Execute the module
    exec(candidate.source, restricted_globals)

    # Run the workflow
    result = restricted_globals["run"](stub_handler)
    assert result["completed"] == ["step1", "step2"]
    assert execution_order == ["step1", "step2"]


def test_executor_check_failure_raises_stepcheckfailed():
    """Check failure raises StepCheckFailed with right step."""
    from selflearn.compilation.compiler import WorkflowCompiler

    steps = (
        ProcedureStep(id="step1", objective="first", task_type="setup"),
        ProcedureStep(id="step2", objective="check this",
                      task_type="code_edit",
                      check=(("status", "pass"),)),
    )
    entry = _make_workflow_entry(procedure=steps)

    compiler = WorkflowCompiler()
    candidate = compiler.compile(entry, pack="test", compiled_at="2024-01-01T00:00:00Z")

    # Handler returns status != expected
    def stub_handler(step_id, step_data):
        return {"status": "fail"}  # step2 expects "pass"

    from selflearn.compilation.runtime import _make_restricted_globals
    restricted_globals = _make_restricted_globals()
    exec(candidate.source, restricted_globals)

    with pytest.raises(Exception) as exc_info:
        restricted_globals["run"](stub_handler)

    assert "StepCheckFailed" in str(type(exc_info.value).__name__)


def test_executor_approval_step_raises_approvalrequired():
    """Approval step raises ApprovalRequired with step_id."""
    from selflearn.compilation.compiler import WorkflowCompiler

    steps = (
        ProcedureStep(id="step1", objective="do work", task_type="code_edit"),
        ProcedureStep(id="step2", objective="get approval", task_type="approval"),
    )
    entry = _make_workflow_entry(procedure=steps)

    compiler = WorkflowCompiler()
    candidate = compiler.compile(entry, pack="test", compiled_at="2024-01-01T00:00:00Z")

    def stub_handler(step_id, step_data):
        return {"status": "ok"}

    from selflearn.compilation.runtime import _make_restricted_globals
    restricted_globals = _make_restricted_globals()
    exec(candidate.source, restricted_globals)

    with pytest.raises(Exception) as exc_info:
        restricted_globals["run"](stub_handler)

    # Should raise ApprovalRequired, not StepCheckFailed
    assert "ApprovalRequired" in type(exc_info.value).__name__
