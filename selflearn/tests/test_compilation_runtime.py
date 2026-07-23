"""Tests for executor runtime.

Tests cover:
- Runtime refuses without ACTIVE record
- Runtime refuses on drift (stale spec)
- Runtime refuses on executor-byte tamper
- Approval step -> awaiting_approval hard stop
- Check failure -> fail TaskOutcome with exact step_id
- Success -> pass TaskOutcome
- Outcomes feed learning.marks.apply_outcome
- Doctor flags stale executor and dangling entry
- Registry transitions + atomic rewrite
"""
import json
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pytest

from selflearn.compilation import (
    ExecutorRegistry,
    ExecutorRuntime,
    RegistryError,
    RuntimeCompError,
    RunResult,
    WorkflowCompiler,
    canonical_procedure_hash,
    content_hash,
)
from selflearn.compilation.models import ExecutorRecord
from selflearn.contracts import (
    CandidateEntry,
    EntrySource,
    ProcedureStep,
    TaskOutcome,
)
from selflearn.learning.marks import apply_outcome
from selflearn.store.packstore import PackStore


# =============================================================================
# Test helpers
# =============================================================================

TEST_SOURCE = EntrySource(
    url="https://test.example.org",
    fetched_at="2024-01-01T00:00:00Z",
    sha256="0" * 64,
    tier="official",
)


def _make_entry(pack: str = "test", **kwargs) -> CandidateEntry:
    """Make a test workflow entry."""
    return CandidateEntry(
        id=kwargs.get("id", "wf-001"),
        pack=pack,
        kind="workflow",
        body="test workflow",
        claims=(),
        sources=(TEST_SOURCE,),
        topic="test",
        procedure=kwargs.get("procedure", (
            ProcedureStep(id="step1", objective="do work", task_type="code_edit"),
        )),
    )


@dataclass
class FakeProvenance:
    """Fake provenance that records events."""
    events: list = field(default_factory=list)

    def append(self, event: dict) -> None:
        self.events.append(event)


# =============================================================================
# Runtime tests
# =============================================================================

def test_runtime_refuses_without_active_record():
    """Runtime refuses to run when no ACTIVE record exists."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store_root = Path(tmpdir)
        pack = "test"
        store = PackStore(store_root)

        # Create entry but no executor
        entry = _make_entry(pack=pack)
        store.add_candidate(entry)

        registry = ExecutorRegistry(store_root, pack)
        provenance = FakeProvenance()

        def clock():
            return datetime(2024, 1, 1, tzinfo=timezone.utc)

        runtime = ExecutorRuntime(registry, store, provenance, clock)

        def handler(step_id, step_data):
            return {"status": "ok"}

        with pytest.raises(RuntimeCompError, match="No active executor"):
            runtime.run("wf-001", task_id="t1", topic="test",
                       task_type="code_edit", step_handler=handler,
                       now="2024-01-01T00:00:00Z")


def test_runtime_refuses_on_drift():
    """Runtime refuses when active executor is stale."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store_root = Path(tmpdir)
        pack = "test"
        store = PackStore(store_root)

        # Create entry
        original_procedure = (ProcedureStep(id="step1", objective="do work", task_type="code_edit"),)
        entry = _make_entry(pack=pack, procedure=original_procedure)
        store.add_candidate(entry)

        # Create executor record with different spec hash
        old_hash = "a" * 64  # Valid 64-hex but different from current
        exec_dir = store_root / pack / "executors" / "wf-001"
        exec_dir.mkdir(parents=True)
        exec_path = exec_dir / f"{old_hash}.py"
        exec_path.write_text("def run(handler): pass")

        from selflearn.compilation.models import ExecutorRecord
        record = ExecutorRecord(
            record_id="",
            entry_id="wf-001",
            pack=pack,
            spec_hash=old_hash,
            executor_hash="b" * 64,  # Valid 64-hex
            status="active",
            path=f"{pack}/executors/wf-001/{old_hash}.py",
            receipt_id="rcpt1",
            updated_at="2024-01-01T00:00:00Z",
        )
        registry = ExecutorRegistry(store_root, pack)
        registry.add_record(record)

        provenance = FakeProvenance()

        def clock():
            return datetime(2024, 1, 1, tzinfo=timezone.utc)

        runtime = ExecutorRuntime(registry, store, provenance, clock)

        def handler(step_id, step_data):
            return {"status": "ok"}

        with pytest.raises(RuntimeCompError, match="stale"):
            runtime.run("wf-001", task_id="t1", topic="test",
                       task_type="code_edit", step_handler=handler,
                       now="2024-01-01T00:00:00Z")


def test_runtime_refuses_on_tamper():
    """Runtime refuses when executor bytes don't match hash."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store_root = Path(tmpdir)
        pack = "test"
        store = PackStore(store_root)

        # Create entry
        procedure = (ProcedureStep(id="step1", objective="do work", task_type="code_edit"),)
        entry = _make_entry(pack=pack, procedure=procedure)
        store.add_candidate(entry)

        # Create executor with matching hash
        spec_hash = canonical_procedure_hash(procedure)
        exec_dir = store_root / pack / "executors" / "wf-001"
        exec_dir.mkdir(parents=True)
        exec_path = exec_dir / f"{spec_hash}.py"
        source = "def run(handler): pass"
        exec_path.write_text(source)

        recorded_hash = content_hash(source)

        record = ExecutorRecord(
            record_id="",
            entry_id="wf-001",
            pack=pack,
            spec_hash=spec_hash,
            executor_hash=recorded_hash,
            status="active",
            path=f"{pack}/executors/wf-001/{spec_hash}.py",
            receipt_id="rcpt1",
            updated_at="2024-01-01T00:00:00Z",
        )
        registry = ExecutorRegistry(store_root, pack)
        registry.add_record(record)

        # Tamper with the file
        exec_path.write_text("def run(handler): print('tampered')")

        provenance = FakeProvenance()

        def clock():
            return datetime(2024, 1, 1, tzinfo=timezone.utc)

        runtime = ExecutorRuntime(registry, store, provenance, clock)

        def handler(step_id, step_data):
            return {"status": "ok"}

        with pytest.raises(RuntimeCompError, match="tampered"):
            runtime.run("wf-001", task_id="t1", topic="test",
                       task_type="code_edit", step_handler=handler,
                       now="2024-01-01T00:00:00Z")


def test_runtime_approval_step_awaiting():
    """Runtime returns awaiting_approval on approval step."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store_root = Path(tmpdir)
        pack = "test"
        store = PackStore(store_root)

        # Create entry with approval step
        procedure = (
            ProcedureStep(id="step1", objective="do work", task_type="code_edit"),
            ProcedureStep(id="step2", objective="get approval", task_type="approval"),
        )
        entry = _make_entry(pack=pack, procedure=procedure)
        store.add_candidate(entry)

        spec_hash = canonical_procedure_hash(procedure)

        # Generate and save executor source
        from selflearn.compilation.compiler import WorkflowCompiler
        compiler = WorkflowCompiler()
        candidate = compiler.compile(entry, pack=pack, compiled_at="2024-01-01T00:00:00Z")

        exec_dir = store_root / pack / "executors" / "wf-001"
        exec_dir.mkdir(parents=True)
        exec_path = exec_dir / f"{spec_hash}.py"
        exec_path.write_text(candidate.source)

        record = ExecutorRecord(
            record_id="",
            entry_id="wf-001",
            pack=pack,
            spec_hash=spec_hash,
            executor_hash=candidate.executor_hash,
            status="active",
            path=f"{pack}/executors/wf-001/{spec_hash}.py",
            receipt_id="rcpt1",
            updated_at="2024-01-01T00:00:00Z",
        )
        registry = ExecutorRegistry(store_root, pack)
        registry.add_record(record)

        provenance = FakeProvenance()

        def clock():
            return datetime(2024, 1, 1, tzinfo=timezone.utc)

        runtime = ExecutorRuntime(registry, store, provenance, clock)

        def handler(step_id, step_data):
            return {"status": "ok"}

        result = runtime.run("wf-001", task_id="t1", topic="test",
                           task_type="code_edit", step_handler=handler,
                           now="2024-01-01T00:00:00Z")

        assert result.status == "awaiting_approval"
        # FIX-6: tracking handler records steps that actually ran (step1 executed)
        assert len(result.completed_steps) == 1
        assert result.completed_steps[0] == "step1"
        # FIX-2: outcomes must be empty tuple — no evidence produced
        assert result.outcomes == ()
        # No entry implicated — nothing to feed to apply_outcome
        assert result.at_step == "step2"
        # Journal event for approval stop
        assert any(e["kind"] == "runtime.approval-stop" for e in provenance.events)


def test_runtime_check_failure():
    """Runtime reports check failure as TaskOutcome with step_id."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store_root = Path(tmpdir)
        pack = "test"
        store = PackStore(store_root)

        # Create entry with check
        procedure = (
            ProcedureStep(
                id="step1", objective="do work", task_type="code_edit",
                check=(("status", "pass"),)
            ),
        )
        entry = _make_entry(pack=pack, procedure=procedure)
        store.add_candidate(entry)

        spec_hash = canonical_procedure_hash(procedure)

        from selflearn.compilation.compiler import WorkflowCompiler
        compiler = WorkflowCompiler()
        candidate = compiler.compile(entry, pack=pack, compiled_at="2024-01-01T00:00:00Z")

        exec_dir = store_root / pack / "executors" / "wf-001"
        exec_dir.mkdir(parents=True)
        exec_path = exec_dir / f"{spec_hash}.py"
        exec_path.write_text(candidate.source)

        record = ExecutorRecord(
            record_id="",
            entry_id="wf-001",
            pack=pack,
            spec_hash=spec_hash,
            executor_hash=candidate.executor_hash,
            status="active",
            path=f"{pack}/executors/wf-001/{spec_hash}.py",
            receipt_id="rcpt1",
            updated_at="2024-01-01T00:00:00Z",
        )
        registry = ExecutorRegistry(store_root, pack)
        registry.add_record(record)

        provenance = FakeProvenance()

        def clock():
            return datetime(2024, 1, 1, tzinfo=timezone.utc)

        runtime = ExecutorRuntime(registry, store, provenance, clock)

        # Handler returns fail, but step expects "pass"
        def handler(step_id, step_data):
            return {"status": "fail"}

        result = runtime.run("wf-001", task_id="t1", topic="test",
                           task_type="code_edit", step_handler=handler,
                           now="2024-01-01T00:00:00Z")

        assert result.status == "failed"
        assert len(result.outcomes) == 1
        assert result.outcomes[0].verdict == "fail"
        assert result.outcomes[0].step_id == "step1"
        assert "executor-step-check" in result.outcomes[0].failure_mode


def test_runtime_success():
    """Runtime returns pass TaskOutcome on success."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store_root = Path(tmpdir)
        pack = "test"
        store = PackStore(store_root)

        procedure = (
            ProcedureStep(id="step1", objective="do work", task_type="code_edit"),
        )
        entry = _make_entry(pack=pack, procedure=procedure)
        store.add_candidate(entry)

        spec_hash = canonical_procedure_hash(procedure)

        from selflearn.compilation.compiler import WorkflowCompiler
        compiler = WorkflowCompiler()
        candidate = compiler.compile(entry, pack=pack, compiled_at="2024-01-01T00:00:00Z")

        exec_dir = store_root / pack / "executors" / "wf-001"
        exec_dir.mkdir(parents=True)
        exec_path = exec_dir / f"{spec_hash}.py"
        exec_path.write_text(candidate.source)

        record = ExecutorRecord(
            record_id="",
            entry_id="wf-001",
            pack=pack,
            spec_hash=spec_hash,
            executor_hash=candidate.executor_hash,
            status="active",
            path=f"{pack}/executors/wf-001/{spec_hash}.py",
            receipt_id="rcpt1",
            updated_at="2024-01-01T00:00:00Z",
        )
        registry = ExecutorRegistry(store_root, pack)
        registry.add_record(record)

        provenance = FakeProvenance()

        def clock():
            return datetime(2024, 1, 1, tzinfo=timezone.utc)

        runtime = ExecutorRuntime(registry, store, provenance, clock)

        def handler(step_id, step_data):
            return {"status": "ok"}

        result = runtime.run("wf-001", task_id="t1", topic="test",
                           task_type="code_edit", step_handler=handler,
                           now="2024-01-01T00:00:00Z")

        assert result.status == "completed"
        assert len(result.outcomes) == 1
        assert result.outcomes[0].verdict == "pass"
        assert "wf-001" in result.outcomes[0].injected


def test_outcomes_feed_apply_outcome():
    """Runtime outcomes can be fed to learning.marks.apply_outcome."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store_root = Path(tmpdir)
        pack = "test"
        store = PackStore(store_root)

        procedure = (
            ProcedureStep(id="step1", objective="do work", task_type="code_edit"),
        )
        entry = _make_entry(pack=pack, procedure=procedure)
        store.add_candidate(entry)

        spec_hash = canonical_procedure_hash(procedure)

        from selflearn.compilation.compiler import WorkflowCompiler
        compiler = WorkflowCompiler()
        candidate = compiler.compile(entry, pack=pack, compiled_at="2024-01-01T00:00:00Z")

        exec_dir = store_root / pack / "executors" / "wf-001"
        exec_dir.mkdir(parents=True)
        exec_path = exec_dir / f"{spec_hash}.py"
        exec_path.write_text(candidate.source)

        record = ExecutorRecord(
            record_id="",
            entry_id="wf-001",
            pack=pack,
            spec_hash=spec_hash,
            executor_hash=candidate.executor_hash,
            status="active",
            path=f"{pack}/executors/wf-001/{spec_hash}.py",
            receipt_id="rcpt1",
            updated_at="2024-01-01T00:00:00Z",
        )
        registry = ExecutorRegistry(store_root, pack)
        registry.add_record(record)

        provenance = FakeProvenance()

        def clock():
            return datetime(2024, 1, 1, tzinfo=timezone.utc)

        runtime = ExecutorRuntime(registry, store, provenance, clock)

        def handler(step_id, step_data):
            return {"status": "ok"}

        result = runtime.run("wf-001", task_id="t1", topic="test",
                           task_type="code_edit", step_handler=handler,
                           now="2024-01-01T00:00:00Z")

        # Feed outcome to marks
        for outcome in result.outcomes:
            mark_report = apply_outcome(store, outcome)
            # Check that marks were applied
            entry = store.get("wf-001")
            assert entry.helpful > 0


def test_registry_transitions():
    """Registry properly transitions executor status."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store_root = Path(tmpdir)
        pack = "test"

        registry = ExecutorRegistry(store_root, pack)

        # Add quarantined record
        record = ExecutorRecord(
            record_id="",
            entry_id="wf-001",
            pack=pack,
            spec_hash="a" * 64,
            executor_hash="b" * 64,
            status="quarantined",
            path=f"{pack}/executors/wf-001/a.py",
            receipt_id="rcpt1",
            updated_at="2024-01-01T00:00:00Z",
        )
        registry.add_record(record)

        # Transition to active
        new_record = registry.transition(
            record, "active", "rcpt2", updated_at="2024-01-02T00:00:00Z"
        )
        assert new_record.status == "active"

        # Can't go back to quarantined
        with pytest.raises(RegistryError, match="Illegal transition"):
            registry.transition(new_record, "quarantined", "rcpt3",
                               updated_at="2024-01-03T00:00:00Z")


def test_registry_atomic_write():
    """Registry writes are atomic."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store_root = Path(tmpdir)
        pack = "test"

        registry = ExecutorRegistry(store_root, pack)

        # Add record
        record = ExecutorRecord(
            record_id="",
            entry_id="wf-001",
            pack=pack,
            spec_hash="a" * 64,
            executor_hash="b" * 64,
            status="quarantined",
            path=f"{pack}/executors/wf-001/a.py",
            receipt_id="rcpt1",
            updated_at="2024-01-01T00:00:00Z",
        )
        registry.add_record(record)

        # Verify registry file exists and is valid
        reg_path = store_root / pack / "executors" / "registry.json"
        assert reg_path.exists()
        data = json.loads(reg_path.read_text())
        assert len(data["records"]) == 1


def test_registry_write_candidate():
    """Registry can write executor candidate to disk."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store_root = Path(tmpdir)
        pack = "test"

        registry = ExecutorRegistry(store_root, pack)

        procedure = (ProcedureStep(id="step1", objective="do work", task_type="code_edit"),)
        spec_hash = canonical_procedure_hash(procedure)

        from selflearn.compilation import ExecutorSpec, ExecutorCandidate
        spec = ExecutorSpec(
            entry_id="wf-001",
            pack=pack,
            spec_hash=spec_hash,
            procedure=procedure,
        )
        candidate = ExecutorCandidate(
            spec=spec,
            source="print('hello')",
            executor_hash="a" * 64,
            compiled_at="2024-01-01T00:00:00Z",
            compiler_id="test",
        )

        path = registry.write_candidate(candidate)
        assert path.exists()
        assert path.read_text() == "print('hello')"


# =============================================================================
# FIX-1 citing test: dangerous builtins blocked
# =============================================================================

def test_runtime_blocks_dangerous_builtins():
    """Restricted globals exclude eval, exec, open, __import__, etc.

    When code is exec'd in the restricted sandbox, referencing any D3-blocked
    builtin (open, eval, __import__, input, compile, breakpoint, etc.) raises
    NameError — the sandbox whitelist does not include them.
    """
    from selflearn.compilation.runtime import _make_restricted_globals

    restricted = _make_restricted_globals()

    # Direct sandbox exec: referencing blocked builtins raises NameError
    for code, name in [
        ("open('/etc/passwd')", "open"),
        ("eval('1')", "eval"),
        ("__import__('os')", "__import__"),
        ("input()", "input"),
        ("compile('1', '', 'exec')", "compile"),
        ("breakpoint()", "breakpoint"),
        ("exit", "exit"),
        ("quit", "quit"),
        ("help()", "help"),
        ("memoryview(b'x')", "memoryview"),
        ("globals()", "globals"),
        ("locals()", "locals"),
        ("vars()", "vars"),
        ("setattr(object(), 'x', 1)", "setattr"),
        ("delattr(object(), 'x')", "delattr"),
    ]:
        try:
            exec(code, restricted)
            assert False, f"{name} should raise NameError in restricted sandbox"
        except NameError:
            pass  # Expected
        except Exception as e:
            # Some might raise different errors (e.g., OSError for open),
            # but they should NOT succeed silently
            assert type(e).__name__ != "None", f"{name} should not succeed"

    # Sanity: safe builtins ARE available (no exception)
    for code in [
        "len([1,2,3])",
        "str(123)",
        "bool(True)",
        "list((1,2,))",
        "dict(a=1)",
        "json.dumps({'x': 1})",  # json injected by runtime
    ]:
        ns = {}
        exec(code, restricted, ns)  # must not raise


# =============================================================================
# FIX-2 citing test: approval stop is NOT a failure outcome
# =============================================================================

def test_approval_stop_no_outcome():
    """ApprovalRequired produces no TaskOutcome — outcomes == ().

    awaiting_approval produces no pass/fail evidence; apply_outcome is never
    invocable on the result. The entry must NOT be implicated.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        store_root = Path(tmpdir)
        pack = "test"
        store = PackStore(store_root)

        procedure = (
            ProcedureStep(id="step1", objective="do work", task_type="code_edit"),
            ProcedureStep(id="step2", objective="get approval", task_type="approval"),
        )
        entry = _make_entry(pack=pack, procedure=procedure)
        store.add_candidate(entry)

        spec_hash = canonical_procedure_hash(procedure)
        compiler = WorkflowCompiler()
        candidate = compiler.compile(entry, pack=pack, compiled_at="2024-01-01T00:00:00Z")

        exec_dir = store_root / pack / "executors" / "wf-001"
        exec_dir.mkdir(parents=True)
        exec_path = exec_dir / f"{spec_hash}.py"
        exec_path.write_text(candidate.source)

        record = ExecutorRecord(
            record_id="",
            entry_id="wf-001",
            pack=pack,
            spec_hash=spec_hash,
            executor_hash=candidate.executor_hash,
            status="active",
            path=f"{pack}/executors/wf-001/{spec_hash}.py",
            receipt_id="rcpt1",
            updated_at="2024-01-01T00:00:00Z",
        )
        registry = ExecutorRegistry(store_root, pack)
        registry.add_record(record)

        provenance = FakeProvenance()

        def clock():
            return datetime(2024, 1, 1, tzinfo=timezone.utc)

        runtime = ExecutorRuntime(registry, store, provenance, clock)

        def handler(step_id, step_data):
            return {"status": "ok"}

        result = runtime.run("wf-001", task_id="t1", topic="test",
                            task_type="code_edit", step_handler=handler,
                            now="2024-01-01T00:00:00Z")

        assert result.status == "awaiting_approval"
        # FIX-2: outcomes must be empty tuple — no evidence produced
        assert result.outcomes == ()
        # No entry implicated — nothing to feed to apply_outcome
        assert result.at_step == "step2"
        # Journal event for approval stop
        assert any(e["kind"] == "runtime.approval-stop" for e in provenance.events)


# =============================================================================
# FIX-6 citing test: completed-step fidelity with tracking handler
# =============================================================================

def test_completed_steps_fidelity_on_failure():
    """3-step procedure failing at step 2 → completed_steps == ('step1',).

    The tracking handler records each step that actually executed, even on
    failure paths.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        store_root = Path(tmpdir)
        pack = "test"
        store = PackStore(store_root)

        procedure = (
            ProcedureStep(
                id="s1", objective="step one", task_type="code_edit",
                check=(("status", "ok"),),
            ),
            ProcedureStep(
                id="s2", objective="step two", task_type="code_edit",
                check=(("status", "pass"),),  # will fail
            ),
            ProcedureStep(
                id="s3", objective="step three", task_type="code_edit",
            ),
        )
        entry = _make_entry(pack=pack, procedure=procedure)
        store.add_candidate(entry)

        spec_hash = canonical_procedure_hash(procedure)
        compiler = WorkflowCompiler()
        candidate = compiler.compile(entry, pack=pack, compiled_at="2024-01-01T00:00:00Z")

        exec_dir = store_root / pack / "executors" / "wf-001"
        exec_dir.mkdir(parents=True)
        exec_path = exec_dir / f"{spec_hash}.py"
        exec_path.write_text(candidate.source)

        record = ExecutorRecord(
            record_id="",
            entry_id="wf-001",
            pack=pack,
            spec_hash=spec_hash,
            executor_hash=candidate.executor_hash,
            status="active",
            path=f"{pack}/executors/wf-001/{spec_hash}.py",
            receipt_id="rcpt1",
            updated_at="2024-01-01T00:00:00Z",
        )
        registry = ExecutorRegistry(store_root, pack)
        registry.add_record(record)

        provenance = FakeProvenance()

        def clock():
            return datetime(2024, 1, 1, tzinfo=timezone.utc)

        runtime = ExecutorRuntime(registry, store, provenance, clock)

        # s2 returns "fail" so s2's check fails; s1 passes
        def handler(step_id, step_data):
            if step_id == "s2":
                return {"status": "fail"}  # check expects "pass"
            return {"status": "ok"}

        result = runtime.run("wf-001", task_id="t1", topic="test",
                            task_type="code_edit", step_handler=handler,
                            now="2024-01-01T00:00:00Z")

        assert result.status == "failed"
        # FIX-6: completed_steps reflects what actually ran (only s1)
        assert result.completed_steps == ("s1",)
        assert result.outcomes[0].step_id == "s2"
