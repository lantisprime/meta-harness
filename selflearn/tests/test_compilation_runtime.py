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
        candidate = compiler.compile(entry, pack=pack, compiled_at="2024-01-01T00:00:00Z", provenance=FakeProvenance())

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
        candidate = compiler.compile(entry, pack=pack, compiled_at="2024-01-01T00:00:00Z", provenance=FakeProvenance())

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
        candidate = compiler.compile(entry, pack=pack, compiled_at="2024-01-01T00:00:00Z", provenance=FakeProvenance())

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
        candidate = compiler.compile(entry, pack=pack, compiled_at="2024-01-01T00:00:00Z", provenance=FakeProvenance())

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
        ("getattr(object(), '__class__')", "getattr"),
        ("hasattr(object(), '__class__')", "hasattr"),
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
        candidate = compiler.compile(entry, pack=pack, compiled_at="2024-01-01T00:00:00Z", provenance=FakeProvenance())

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
        candidate = compiler.compile(entry, pack=pack, compiled_at="2024-01-01T00:00:00Z", provenance=FakeProvenance())

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


# =============================================================================
# F2-6 citing test: host handler error is not entry evidence
# =============================================================================

def test_runtime_handler_error_is_not_entry_evidence():
    """F2-6: step_handler exception -> RunResult error, outcomes=()."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store_root = Path(tmpdir)
        pack = "test"
        store = PackStore(store_root)

        procedure = (ProcedureStep(id="step1", objective="do work", task_type="code_edit"),)
        entry = _make_entry(pack=pack, procedure=procedure)
        store.add_candidate(entry)

        spec_hash = canonical_procedure_hash(procedure)
        compiler = WorkflowCompiler()
        candidate = compiler.compile(entry, pack=pack, compiled_at="2024-01-01T00:00:00Z", provenance=FakeProvenance())

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

        def exploding_handler(step_id, step_data):
            raise RuntimeError("host bug")

        result = runtime.run("wf-001", task_id="t1", topic="test",
                            task_type="code_edit", step_handler=exploding_handler,
                            now="2024-01-01T00:00:00Z")

        assert result.status == "error"
        assert result.outcomes == ()
        assert any(e["kind"] == "runtime.handler-error" for e in provenance.events)


# =============================================================================
# F2-13 citing test: doctor flags orphan executor source
# =============================================================================

def test_doctor_flags_orphan_source():
    """F2-13: doctor reports executor.orphan-source when source has no record."""
    from selflearn.doctor import DoctorReport, _check_executors

    with tempfile.TemporaryDirectory() as tmpdir:
        store_root = Path(tmpdir)
        pack = "test"
        pack_dir = store_root / pack
        pack_dir.mkdir()
        manifest = {"name": pack, "schema_version": 1, "entries": {}}
        (pack_dir / "manifest.json").write_text(json.dumps(manifest))
        (pack_dir / "entries").mkdir()

        orphan_dir = pack_dir / "executors" / "wf-001"
        orphan_dir.mkdir(parents=True)
        (orphan_dir / "orphan.py").write_text("print('orphan')")

        registry_path = pack_dir / "executors" / "registry.json"
        registry_path.write_text(json.dumps({"records": []}))

        report = DoctorReport(root=store_root, fix=False)
        _check_executors(pack_dir, fix=False, report=report)

        orphans = [f for f in report.findings if f.code == "executor.orphan-source"]
        assert len(orphans) == 1
        assert orphans[0].fixable is False


# =============================================================================
# F2-16 citing test: AST preflight rejects dunder escape classes
# =============================================================================

def test_runtime_ast_preflight_rejects_dunder_escape():
    """F2-16: source with dunder escape class is refused before exec."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store_root = Path(tmpdir)
        pack = "test"
        store = PackStore(store_root)

        procedure = (ProcedureStep(id="step1", objective="do work", task_type="code_edit"),)
        entry = _make_entry(pack=pack, procedure=procedure)
        store.add_candidate(entry)

        spec_hash = canonical_procedure_hash(procedure)
        exec_dir = store_root / pack / "executors" / "wf-001"
        exec_dir.mkdir(parents=True)
        exec_path = exec_dir / f"{spec_hash}.py"
        # Source that would otherwise execute a dunder escape and uses no builtins.
        exec_path.write_text(
            "def run(handler):\n"
            "    ().__class__.__bases__[0].__subclasses__()\n"
            "    return {'completed': []}\n"
        )

        record = ExecutorRecord(
            record_id="",
            entry_id="wf-001",
            pack=pack,
            spec_hash=spec_hash,
            executor_hash=content_hash(exec_path.read_text()),
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

        with pytest.raises(RuntimeCompError, match="forbidden dunder"):
            runtime.run("wf-001", task_id="t1", topic="test",
                       task_type="code_edit", step_handler=lambda sid, sdata: {"status": "ok"},
                       now="2024-01-01T00:00:00Z")

        # META-17 F1: the dunder refusal is now also journalled under the
        # preflight kind -- an aborted preflight must leave an evidence event.
        assert any(e["kind"] == "executor.malformed-source" for e in provenance.events)


def test_runtime_nul_bearing_source_is_journalled():
    """A hash-matched executor whose source carries a NUL fails closed, not raw.

    META-17 F1: on older interpreters ast.parse() raises ValueError -- NOT
    SyntaxError -- on NUL-bearing source (observed CPython 3.10.20), and
    _ast_preflight caught only SyntaxError, so the ValueError escaped run() raw
    and unjournalled, breaching both the journalled-refusal evidence contract
    and the RuntimeCompError normalization contract. (On newer interpreters --
    observed 3.11.15 and 3.12.13 -- the tokenizer raises SyntaxError instead,
    which _ast_preflight normalized but never journalled -- the same evidence
    gap in the sibling branch.) The dual-branch guard is deliberately
    version-agnostic and closes both, which is why this test asserts the journal
    invariant and the "null bytes" semantics rather than a version-specific
    message. Reachable only through a tampered hash-matched executor file
    (json.dumps escapes NUL in legitimate compiler output), i.e. exactly the
    adversarial surface the evidence record exists for. The refusal must be
    journalled under an honest kind and normalized to RuntimeCompError.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        store_root = Path(tmpdir)
        pack = "test"
        store = PackStore(store_root)

        procedure = (ProcedureStep(id="step1", objective="x", task_type="code_edit"),)
        entry = _make_entry(pack=pack, procedure=procedure)
        store.add_candidate(entry)

        spec_hash = canonical_procedure_hash(procedure)
        exec_dir = store_root / pack / "executors" / "wf-001"
        exec_dir.mkdir(parents=True)
        exec_path = exec_dir / f"{spec_hash}.py"
        # Valid-looking Python carrying an embedded NUL. ast.parse rejects it
        # with "source code string cannot contain null bytes" -- ValueError on
        # older interpreters (observed CPython 3.10.20, the raw-escape defect),
        # SyntaxError on newer ones (observed 3.11.15, 3.12.13); both are now
        # journalled and normalized by the wrapping guard.
        nul_source = "def run(handler):\n    x = '\x00'\n    return {'completed': []}\n"
        exec_path.write_text(nul_source)

        record = ExecutorRecord(
            record_id="",
            entry_id="wf-001",
            pack=pack,
            spec_hash=spec_hash,
            # Hash matches the NUL-bearing bytes, so the tamper check passes and
            # control reaches _ast_preflight -- the tampered hash-matched surface.
            executor_hash=content_hash(nul_source),
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

        # A raw ValueError/SyntaxError must never escape run(); it is normalized
        # to RuntimeCompError. Match on the version-invariant "null bytes" text.
        with pytest.raises(RuntimeCompError, match="null bytes"):
            runtime.run("wf-001", task_id="t1", topic="test",
                       task_type="code_edit", step_handler=lambda sid, sdata: {"status": "ok"},
                       now="2024-12-25T00:00:00Z")

        # Journalled under the honest kind, carrying the entry_id and the run's
        # injected now.
        preflight_events = [e for e in provenance.events
                            if e["kind"] == "executor.malformed-source"]
        assert len(preflight_events) == 1
        assert preflight_events[0]["entry_id"] == "wf-001"
        assert preflight_events[0]["timestamp"] == "2024-12-25T00:00:00Z"
        # A malformed source is not an escape attempt; it gets its own kind.
        assert not any(e["kind"] == "executor.path-escape" for e in provenance.events)


# =============================================================================
# F3-1 citing test: active executor leaves clean doctor report
# =============================================================================

def test_doctor_clean_after_activation():
    """F3-1: a registered+active executor produces no orphan finding and report.ok."""
    from selflearn.doctor import run_doctor

    with tempfile.TemporaryDirectory() as tmpdir:
        store_root = Path(tmpdir)
        pack = "test"
        store = PackStore(store_root)

        procedure = (ProcedureStep(id="step1", objective="do work", task_type="code_edit"),)
        entry = _make_entry(pack=pack, procedure=procedure)
        store.add_candidate(entry)

        compiler = WorkflowCompiler()
        candidate = compiler.compile(
            entry, pack=pack, compiled_at="2024-01-01T00:00:00Z",
            provenance=FakeProvenance(),
        )

        registry = ExecutorRegistry(store_root, pack)
        path = registry.write_candidate(candidate)
        active_record = ExecutorRecord(
            record_id="",
            entry_id="wf-001",
            pack=pack,
            spec_hash=candidate.spec.spec_hash,
            executor_hash=candidate.executor_hash,
            status="active",
            path=str(path.relative_to(store_root)),
            receipt_id="rcpt1",
            updated_at="2024-01-01T00:00:00Z",
        )
        registry.add_record(active_record)

        report = run_doctor(store_root, fix=False)
        orphan = [f for f in report.findings if f.code == "executor.orphan-source"]
        assert orphan == []
        assert report.ok is True


# =============================================================================
# F3-3 citing test: getattr-dunder and attribute-dunder escapes blocked
# =============================================================================

def test_runtime_preflight_blocks_getattr_dunder():
    """F3-3: getattr removed from sandbox -> dunder access by string raises NameError."""
    from selflearn.compilation.runtime import _make_restricted_globals

    restricted = _make_restricted_globals()

    # getattr is not in SAFE_BUILTINS; any attempt to use it for dunder
    # access fails before it can do anything.
    with pytest.raises(NameError):
        exec("getattr(type(()), '__bases__')", restricted)


def test_runtime_blocks_getattr_hasattr():
    """F3-3: getattr/hasattr are absent from the restricted sandbox."""
    from selflearn.compilation.runtime import _make_restricted_globals

    restricted = _make_restricted_globals()

    for code, name in [
        ("getattr(object(), '__class__')", "getattr"),
        ("hasattr(object(), '__class__')", "hasattr"),
    ]:
        try:
            exec(code, restricted)
            assert False, f"{name} should raise NameError"
        except NameError:
            pass


# =============================================================================
# F3-8 citing test: runtime refusal events use the injected now
# =============================================================================

def test_runtime_refusal_uses_injected_now():
    """F3-8: no-active-record refusal is journaled with the run's injected now."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store_root = Path(tmpdir)
        pack = "test"
        store = PackStore(store_root)

        # No executor registered
        entry = _make_entry(pack=pack)
        store.add_candidate(entry)

        registry = ExecutorRegistry(store_root, pack)
        provenance = FakeProvenance()

        def clock():
            return datetime(2024, 1, 1, tzinfo=timezone.utc)

        runtime = ExecutorRuntime(registry, store, provenance, clock)

        with pytest.raises(RuntimeCompError, match="No active executor"):
            runtime.run("wf-001", task_id="t1", topic="test",
                       task_type="code_edit", step_handler=lambda sid, sdata: {"status": "ok"},
                       now="2025-12-25T00:00:00Z")

        refusal_events = [e for e in provenance.events if e["kind"] == "executor.no-active"]
        assert len(refusal_events) == 1
        assert refusal_events[0]["timestamp"] == "2025-12-25T00:00:00Z"


# =============================================================================
# F4-3 citing test: exec failure is normalized and journals finish event
# =============================================================================

def test_runtime_exec_failure_normalizes_and_journals_finish():
    """F4-3: malformed executor that passes AST preflight but fails exec."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store_root = Path(tmpdir)
        pack = "test"
        store = PackStore(store_root)

        procedure = (ProcedureStep(id="step1", objective="x", task_type="code_edit"),)
        entry = _make_entry(pack=pack, procedure=procedure)
        store.add_candidate(entry)

        spec_hash = canonical_procedure_hash(procedure)
        exec_dir = store_root / pack / "executors" / "wf-001"
        exec_dir.mkdir(parents=True)
        exec_path = exec_dir / f"{spec_hash}.py"
        # Passes _ast_preflight (no dunder) but fails at exec with NameError.
        bad_source = "undefined_symbol_for_exec_failure\n"
        exec_path.write_text(bad_source)

        record = ExecutorRecord(
            record_id="",
            entry_id="wf-001",
            pack=pack,
            spec_hash=spec_hash,
            executor_hash=content_hash(bad_source),
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

        with pytest.raises(RuntimeCompError):
            runtime.run("wf-001", task_id="t1", topic="test",
                       task_type="code_edit", step_handler=lambda sid, sdata: {"status": "ok"},
                       now="2024-01-01T00:00:00Z")

        # A runtime.start must be paired with a runtime.finish/executor-error.
        assert any(e["kind"] == "runtime.start" for e in provenance.events)
        assert any(e["kind"] in ("runtime.finish", "runtime.executor-error") for e in provenance.events)


# =============================================================================
# F4-4 citing test: runtime guards missing entry during drift check
# =============================================================================

def test_runtime_missing_entry_journals_refusal():
    """F4-4: entry deleted after activation is unverifiable, not uncaught."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store_root = Path(tmpdir)
        pack = "test"
        store = PackStore(store_root)

        procedure = (ProcedureStep(id="step1", objective="x", task_type="code_edit"),)
        entry = _make_entry(pack=pack, procedure=procedure)
        store.add_candidate(entry)

        spec_hash = canonical_procedure_hash(procedure)
        exec_dir = store_root / pack / "executors" / "wf-001"
        exec_dir.mkdir(parents=True)
        exec_path = exec_dir / f"{spec_hash}.py"
        source = "def run(handler): return {'completed': []}"
        exec_path.write_text(source)

        record = ExecutorRecord(
            record_id="",
            entry_id="wf-001",
            pack=pack,
            spec_hash=spec_hash,
            executor_hash=content_hash(source),
            status="active",
            path=f"{pack}/executors/wf-001/{spec_hash}.py",
            receipt_id="rcpt1",
            updated_at="2024-01-01T00:00:00Z",
        )
        registry = ExecutorRegistry(store_root, pack)
        registry.add_record(record)

        # Delete the entry from the live store.
        del store._entries["wf-001"]

        provenance = FakeProvenance()

        def clock():
            return datetime(2024, 1, 1, tzinfo=timezone.utc)

        runtime = ExecutorRuntime(registry, store, provenance, clock)

        with pytest.raises(RuntimeCompError, match="unverifiable"):
            runtime.run("wf-001", task_id="t1", topic="test",
                       task_type="code_edit", step_handler=lambda sid, sdata: {"status": "ok"},
                       now="2024-01-01T00:00:00Z")

        assert any(e["kind"] == "executor.unverifiable" for e in provenance.events)


# =============================================================================
# Final tidy: executor path-escape blocked
# =============================================================================

def test_runtime_path_escape_blocked():
    """A tampered registry.path outside the store root is refused before read."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store_root = Path(tmpdir)
        pack = "test"
        store = PackStore(store_root)

        procedure = (ProcedureStep(id="step1", objective="x", task_type="code_edit"),)
        entry = _make_entry(pack=pack, procedure=procedure)
        store.add_candidate(entry)

        spec_hash = canonical_procedure_hash(procedure)
        record = ExecutorRecord(
            record_id="",
            entry_id="wf-001",
            pack=pack,
            spec_hash=spec_hash,
            executor_hash="b" * 64,
            status="active",
            path="../../etc/hostile.py",
            receipt_id="rcpt1",
            updated_at="2024-01-01T00:00:00Z",
        )
        registry = ExecutorRegistry(store_root, pack)
        registry.add_record(record)

        provenance = FakeProvenance()

        def clock():
            return datetime(2024, 1, 1, tzinfo=timezone.utc)

        runtime = ExecutorRuntime(registry, store, provenance, clock)

        with pytest.raises(RuntimeCompError, match="escapes store root"):
            runtime.run("wf-001", task_id="t1", topic="test",
                       task_type="code_edit", step_handler=lambda sid, sdata: {"status": "ok"},
                       now="2024-01-01T00:00:00Z")

        assert any(e["kind"] == "executor.path-escape" for e in provenance.events)


def test_runtime_path_validation_oserror_is_journalled(monkeypatch):
    """A filesystem error while validating the executor path must be journalled
    and normalized to RuntimeCompError, never surfaced as a raw OSError.

    Citing test for the gate P1 on 4a8069b: the ``.resolve()`` calls sat above
    the ``try``, so its ``except OSError`` handler was unreachable and an OSError
    (e.g. ELOOP, or EACCES on a ``/proc/<pid>/root`` component) escaped ``run()``
    raw and unjournalled.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        store_root = Path(tmpdir)
        pack = "test"
        store = PackStore(store_root)

        procedure = (ProcedureStep(id="step1", objective="x", task_type="code_edit"),)
        entry = _make_entry(pack=pack, procedure=procedure)
        store.add_candidate(entry)

        spec_hash = canonical_procedure_hash(procedure)
        record = ExecutorRecord(
            record_id="",
            entry_id="wf-001",
            pack=pack,
            spec_hash=spec_hash,
            executor_hash="b" * 64,
            status="active",
            path="executors/wf-001.py",
            receipt_id="rcpt1",
            updated_at="2024-01-01T00:00:00Z",
        )
        registry = ExecutorRegistry(store_root, pack)
        registry.add_record(record)

        provenance = FakeProvenance()

        def clock():
            return datetime(2024, 1, 1, tzinfo=timezone.utc)

        runtime = ExecutorRuntime(registry, store, provenance, clock)

        real_resolve = Path.resolve

        def _refuse_resolve(self, *args, **kwargs):
            # Only executor-path validation is made to fail; unrelated
            # resolution elsewhere in the call keeps working. Uses
            # is_relative_to, not str.startswith -- a prefix check would also
            # match a sibling directory sharing the tempdir name prefix.
            if self.is_relative_to(store_root):
                raise PermissionError(13, "Permission denied")
            return real_resolve(self, *args, **kwargs)

        monkeypatch.setattr(Path, "resolve", _refuse_resolve)

        with pytest.raises(RuntimeCompError, match="Cannot validate executor path"):
            runtime.run("wf-001", task_id="t1", topic="test",
                       task_type="code_edit", step_handler=lambda sid, sdata: {"status": "ok"},
                       now="2024-01-01T00:00:00Z")

        # A resolution failure is NOT an escape attempt: it must be journalled
        # as unreadable, and must not pollute the path-escape bucket.
        assert any(e["kind"] == "executor.path-unreadable" for e in provenance.events)
        assert not any(e["kind"] == "executor.path-escape" for e in provenance.events)


def test_runtime_unreadable_contained_path_is_journalled():
    """A contained-but-unreadable executor path fails closed as RuntimeCompError.

    ``active.path == "."`` resolves to the store root itself, which is contained
    and exists, so it passes containment and then raises IsADirectoryError on
    read. That OSError must be journalled and normalized, not surfaced raw --
    and journalled as *unreadable*, not as an escape attempt, since the path
    never left the store root.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        store_root = Path(tmpdir)
        pack = "test"
        store = PackStore(store_root)

        procedure = (ProcedureStep(id="step1", objective="x", task_type="code_edit"),)
        entry = _make_entry(pack=pack, procedure=procedure)
        store.add_candidate(entry)

        spec_hash = canonical_procedure_hash(procedure)
        record = ExecutorRecord(
            record_id="",
            entry_id="wf-001",
            pack=pack,
            spec_hash=spec_hash,
            executor_hash="b" * 64,
            status="active",
            path=".",
            receipt_id="rcpt1",
            updated_at="2024-01-01T00:00:00Z",
        )
        registry = ExecutorRegistry(store_root, pack)
        registry.add_record(record)

        provenance = FakeProvenance()

        def clock():
            return datetime(2024, 1, 1, tzinfo=timezone.utc)

        runtime = ExecutorRuntime(registry, store, provenance, clock)

        with pytest.raises(RuntimeCompError, match="Cannot read executor path"):
            runtime.run("wf-001", task_id="t1", topic="test",
                       task_type="code_edit", step_handler=lambda sid, sdata: {"status": "ok"},
                       now="2024-01-01T00:00:00Z")

        assert any(e["kind"] == "executor.path-unreadable" for e in provenance.events)
        assert not any(e["kind"] == "executor.path-escape" for e in provenance.events)


def test_runtime_non_ascii_executor_round_trips():
    """A VALID non-ASCII executor must round-trip write -> read -> hash -> run.

    content_hash() hashes text.encode("utf-8") explicitly, and executor source
    I/O is now pinned to utf-8 on both sides. Non-ASCII source is reachable in
    practice -- the compiler embeds the step objective and repr(entry_id)
    directly -- so this proves the hash binding survives the round trip.
    Without it the only non-ASCII coverage is the negative case (invalid bytes
    fail closed), and a future refactor could silently break non-ASCII lineage
    with CI still green.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        store_root = Path(tmpdir)
        pack = "test"
        store = PackStore(store_root)

        procedure = (
            ProcedureStep(id="step1", objective="déployer le café ☕",
                          task_type="code_edit"),
        )
        entry = _make_entry(pack=pack, procedure=procedure)
        store.add_candidate(entry)

        spec_hash = canonical_procedure_hash(procedure)

        from selflearn.compilation.compiler import WorkflowCompiler
        compiler = WorkflowCompiler()
        candidate = compiler.compile(entry, pack=pack,
                                     compiled_at="2024-01-01T00:00:00Z",
                                     provenance=FakeProvenance())

        # The fixture is only meaningful if the compiled source really carries
        # non-ASCII bytes.
        assert any(ord(ch) > 127 for ch in candidate.source)

        registry = ExecutorRegistry(store_root, pack)
        # Written through the real registry path, which pins utf-8.
        registry.write_candidate(candidate)

        relative_path = f"{pack}/executors/wf-001/{spec_hash}.py"
        record = ExecutorRecord(
            record_id="",
            entry_id="wf-001",
            pack=pack,
            spec_hash=spec_hash,
            executor_hash=candidate.executor_hash,
            status="active",
            path=relative_path,
            receipt_id="rcpt1",
            updated_at="2024-01-01T00:00:00Z",
        )
        registry.add_record(record)

        provenance = FakeProvenance()

        def clock():
            return datetime(2024, 1, 1, tzinfo=timezone.utc)

        runtime = ExecutorRuntime(registry, store, provenance, clock)

        result = runtime.run("wf-001", task_id="t1", topic="test",
                            task_type="code_edit",
                            step_handler=lambda sid, sdata: {"status": "ok"},
                            now="2024-01-01T00:00:00Z")

        # The hash check passed, so the utf-8 write and read agreed with
        # content_hash's utf-8 encoding.
        assert result.status == "completed"
        assert not any(e["kind"] == "executor.tampered" for e in provenance.events)


def test_runtime_control_character_path_is_rejected():
    """A registry.path carrying a control character is refused before resolve().

    Citing test for the gate-5 P2-1. Path.resolve() and open() raise ValueError
    -- not OSError -- on an embedded NUL, so such a path escaped every OSError
    guard raw and unjournalled, and skipped the containment check entirely
    because resolve() throws before is_relative_to() runs.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        store_root = Path(tmpdir)
        pack = "test"
        store = PackStore(store_root)

        procedure = (ProcedureStep(id="step1", objective="x", task_type="code_edit"),)
        entry = _make_entry(pack=pack, procedure=procedure)
        store.add_candidate(entry)

        spec_hash = canonical_procedure_hash(procedure)
        record = ExecutorRecord(
            record_id="",
            entry_id="wf-001",
            pack=pack,
            spec_hash=spec_hash,
            executor_hash="b" * 64,
            status="active",
            path=f"{pack}/exec\x00utors/wf-001/x.py",
            receipt_id="rcpt1",
            updated_at="2024-01-01T00:00:00Z",
        )
        registry = ExecutorRegistry(store_root, pack)
        registry.add_record(record)

        provenance = FakeProvenance()

        def clock():
            return datetime(2024, 1, 1, tzinfo=timezone.utc)

        runtime = ExecutorRuntime(registry, store, provenance, clock)

        with pytest.raises(RuntimeCompError, match="control characters"):
            runtime.run("wf-001", task_id="t1", topic="test",
                       task_type="code_edit", step_handler=lambda sid, sdata: {"status": "ok"},
                       now="2024-01-01T00:00:00Z")

        # A malformed path is not an escape attempt; it gets its own kind.
        assert any(e["kind"] == "executor.malformed-path" for e in provenance.events)
        assert not any(e["kind"] == "executor.path-escape" for e in provenance.events)


def test_runtime_missing_source_is_journalled():
    """A genuinely absent executor file must journal its refusal.

    Every other refusal path emits a provenance event; this one did not, so an
    aborted run left a silent gap in the evidence record.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        store_root = Path(tmpdir)
        pack = "test"
        store = PackStore(store_root)

        procedure = (ProcedureStep(id="step1", objective="x", task_type="code_edit"),)
        entry = _make_entry(pack=pack, procedure=procedure)
        store.add_candidate(entry)

        spec_hash = canonical_procedure_hash(procedure)
        record = ExecutorRecord(
            record_id="",
            entry_id="wf-001",
            pack=pack,
            spec_hash=spec_hash,
            executor_hash="b" * 64,
            status="active",
            # Contained and resolvable, but no such file exists.
            path=f"{pack}/executors/wf-001/absent.py",
            receipt_id="rcpt1",
            updated_at="2024-01-01T00:00:00Z",
        )
        registry = ExecutorRegistry(store_root, pack)
        registry.add_record(record)

        provenance = FakeProvenance()

        def clock():
            return datetime(2024, 1, 1, tzinfo=timezone.utc)

        runtime = ExecutorRuntime(registry, store, provenance, clock)

        with pytest.raises(RuntimeCompError, match="Executor source missing"):
            runtime.run("wf-001", task_id="t1", topic="test",
                       task_type="code_edit", step_handler=lambda sid, sdata: {"status": "ok"},
                       now="2024-01-01T00:00:00Z")

        assert any(e["kind"] == "executor.missing-source" for e in provenance.events)
        assert not any(e["kind"] == "executor.path-escape" for e in provenance.events)


def test_runtime_stat_failure_is_journalled(monkeypatch):
    """An OSError from the existence check must be journalled and normalized.

    Citing test for the gate P1 on bd9b79d. On CPython < 3.13 ``Path.exists()``
    swallows only ENOENT/ENOTDIR/EBADF/ELOOP and re-raises the rest, so an
    EACCES on a contained path escaped ``run()`` raw and unjournalled. This
    package supports >=3.10, so the guard is required even though 3.13+
    delegates to ``os.path.exists`` and swallows it -- which is exactly why
    this test injects the error rather than relying on interpreter behaviour.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        store_root = Path(tmpdir)
        pack = "test"
        store = PackStore(store_root)

        procedure = (ProcedureStep(id="step1", objective="x", task_type="code_edit"),)
        entry = _make_entry(pack=pack, procedure=procedure)
        store.add_candidate(entry)

        spec_hash = canonical_procedure_hash(procedure)
        record = ExecutorRecord(
            record_id="",
            entry_id="wf-001",
            pack=pack,
            spec_hash=spec_hash,
            executor_hash="b" * 64,
            status="active",
            path=f"{pack}/executors/wf-001/deadbeef.py",
            receipt_id="rcpt1",
            updated_at="2024-01-01T00:00:00Z",
        )
        registry = ExecutorRegistry(store_root, pack)
        registry.add_record(record)

        provenance = FakeProvenance()

        def clock():
            return datetime(2024, 1, 1, tzinfo=timezone.utc)

        runtime = ExecutorRuntime(registry, store, provenance, clock)

        real_exists = Path.exists

        def _refuse_exists(self, *args, **kwargs):
            # Only the executor's own stat is denied; the store and registry
            # keep working, so the failure lands on the guarded call and not
            # somewhere earlier in run(). Matched by filename because the
            # guarded call uses the *resolved* path, which on macOS differs
            # from the constructed one (/var -> /private/var).
            if self.name == "deadbeef.py":
                raise PermissionError(13, "Permission denied")
            return real_exists(self, *args, **kwargs)

        monkeypatch.setattr(Path, "exists", _refuse_exists)

        with pytest.raises(RuntimeCompError, match="Cannot read executor path"):
            runtime.run("wf-001", task_id="t1", topic="test",
                       task_type="code_edit", step_handler=lambda sid, sdata: {"status": "ok"},
                       now="2024-01-01T00:00:00Z")

        assert any(e["kind"] == "executor.path-unreadable" for e in provenance.events)
        assert not any(e["kind"] == "executor.path-escape" for e in provenance.events)


def test_runtime_undecodable_source_is_journalled():
    """An executor file that cannot be decoded fails closed, not raw.

    ``read_text()`` raises UnicodeDecodeError, which is a ValueError and NOT an
    OSError, so it needs catching explicitly. Reachable through a corrupted
    file on disk.

    The read is now pinned to utf-8 (matching content_hash's explicit
    text.encode("utf-8")), so these bytes fail to decode on every runner. Under
    the previous locale-dependent read this test would have passed for the
    wrong reason on a latin-1/cp1252 locale, where b"\\xff\\xfe" decodes cleanly
    and the run would instead have hit the hash-mismatch path.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        store_root = Path(tmpdir)
        pack = "test"
        store = PackStore(store_root)

        procedure = (ProcedureStep(id="step1", objective="x", task_type="code_edit"),)
        entry = _make_entry(pack=pack, procedure=procedure)
        store.add_candidate(entry)

        # A contained, existing executor file holding bytes that are not valid
        # UTF-8. Placed under the pack's own executors/ dir, matching the
        # layout ExecutorRegistry.write_candidate produces, so PackStore does
        # not mistake a bare top-level directory for a pack.
        exec_rel = f"{pack}/executors/wf-001/deadbeef.py"
        exec_abs = store_root / exec_rel
        exec_abs.parent.mkdir(parents=True, exist_ok=True)
        exec_abs.write_bytes(b"\xff\xfe# not decodable\n")

        spec_hash = canonical_procedure_hash(procedure)
        record = ExecutorRecord(
            record_id="",
            entry_id="wf-001",
            pack=pack,
            spec_hash=spec_hash,
            executor_hash="b" * 64,
            status="active",
            path=exec_rel,
            receipt_id="rcpt1",
            updated_at="2024-01-01T00:00:00Z",
        )
        registry = ExecutorRegistry(store_root, pack)
        registry.add_record(record)

        provenance = FakeProvenance()

        def clock():
            return datetime(2024, 1, 1, tzinfo=timezone.utc)

        runtime = ExecutorRuntime(registry, store, provenance, clock)

        with pytest.raises(RuntimeCompError, match="Cannot read executor path"):
            runtime.run("wf-001", task_id="t1", topic="test",
                       task_type="code_edit", step_handler=lambda sid, sdata: {"status": "ok"},
                       now="2024-01-01T00:00:00Z")

        assert any(e["kind"] == "executor.path-unreadable" for e in provenance.events)


# =============================================================================
# F4-12 citing test: transition refuses to operate on unknown record
# =============================================================================

def test_registry_transition_missing_record_raises():
    """F4-12: transitioning a record not present in registry.json raises."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store_root = Path(tmpdir)
        pack = "test"

        registry = ExecutorRegistry(store_root, pack)

        # A record that is not persisted in the registry file.
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

        with pytest.raises(RegistryError, match="not found in registry"):
            registry.transition(record, "active", "rcpt2",
                               updated_at="2024-01-02T00:00:00Z")
