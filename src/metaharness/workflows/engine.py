"""WorkflowEngine: run a WorkflowSpec through the TaskExecutor, durably.

Discipline: journal first, act second. Every transition is appended to the
journal before the engine performs it, so a crashed run resumes from its journal
with completed steps intact. HITL steps pause the run (`awaiting_approval`) until
`approve`/`reject` is called — the pause survives restarts too, because it's a
journal entry like everything else.
"""
from __future__ import annotations

import asyncio
import functools
import hashlib
import inspect
import json
import os
import re
import tempfile
import time
import uuid
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional

from pydantic import BaseModel, Field

from metaharness.core.executor import TaskExecutor
from metaharness.core.types import Verdict
from metaharness.observability.tracing import tracer
from metaharness.workflows.dsl import (
    WorkflowSpec,
    describe_when,
    resolve_reference,
    resolve_text_references,
    when_satisfied,
)
from metaharness.workflows.journal import Journal

# FIX-1 (codex#1): a step boundary whose TEMPLATE references a prior step's
# OUTPUT ($steps.<id>.output...) resolves worker-generated text; that is
# untrusted-derived, not a caller-authored instruction contract. Detect it on
# the unresolved template so it can be routed into Task.advice, not boundaries.
_STEPS_OUTPUT_REF_RE = re.compile(r"\$steps\.[A-Za-z0-9_-]+\.output")


class RunStatus(str, Enum):
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"


class RunArchiveConflict(ValueError):
    """The requested archive transition is invalid for the run's current state."""


class StepRecord(BaseModel):
    step_id: str
    verdict: Verdict
    output: Any = None
    attempts: int = 0
    cost_usd: float = 0.0
    # where this step's file side-effects landed (recorded by the runner);
    # "" for pure text-work — run packaging reads this, never guesses roots
    workspace_root: str = ""


def _canonical_json_bytes(value: Any) -> bytes:
    """Deterministic JSON serialization for digest computation."""
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def _snapshot_digest(spec: WorkflowSpec, blueprint_snapshot: Optional[dict[str, Any]]) -> str:
    """SHA-256 digest of the canonical run snapshot.

    Saved-harness runs commit to both the immutable authored BlueprintVersion
    and the exact security-normalized workflow that executed.  Ad-hoc legacy
    runs retain their workflow-only identity.
    """
    workflow = spec.model_dump(mode="json")
    source = (
        {"blueprint_snapshot": blueprint_snapshot, "effective_workflow": workflow}
        if blueprint_snapshot is not None else workflow
    )
    return hashlib.sha256(_canonical_json_bytes(source)).hexdigest()


class RunState(BaseModel):
    run_id: str
    workflow: str
    status: RunStatus = RunStatus.RUNNING
    completed: dict[str, StepRecord] = Field(default_factory=dict)
    awaiting: Optional[str] = None          # step id paused at a HITL gate
    approved: set[str] = Field(default_factory=set)
    rejected: set[str] = Field(default_factory=set)
    failed_step: Optional[str] = None
    skipped: dict[str, str] = Field(default_factory=dict)  # step id -> reason
    context: dict[str, Any] = Field(default_factory=dict)
    blueprint_ref: Optional[dict[str, Any]] = None
    blueprint_snapshot: Optional[dict[str, Any]] = None
    snapshot_digest: Optional[str] = None
    archived_at: Optional[float] = None

    model_config = {"arbitrary_types_allowed": True}


class WorkflowEngine:
    def __init__(self, executor: TaskExecutor, journal_dir: Optional[str | Path] = None,
                 tool_requires_approval: Optional[Callable[[str], bool]] = None,
                 tool_available: Optional[Callable[[str], bool]] = None) -> None:
        self.executor = executor
        self.journal_dir = Path(journal_dir) if journal_dir else None
        self.tool_requires_approval = tool_requires_approval or (lambda _name: False)
        self.tool_available = tool_available or (lambda _name: True)
        self._runs: dict[str, tuple[WorkflowSpec, RunState, Journal]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._archived_at: dict[str, float] = {}

    def _lock_for(self, run_id: str) -> asyncio.Lock:
        return self._locks.setdefault(run_id, asyncio.Lock())

    # -- lifecycle -----------------------------------------------------------------

    def start(
        self,
        spec: WorkflowSpec,
        context: Optional[dict[str, Any]] = None,
        *,
        blueprint_ref: Optional[dict[str, Any]] = None,
        blueprint_snapshot: Optional[dict[str, Any]] = None,
    ) -> RunState:
        if (blueprint_ref is None) != (blueprint_snapshot is None):
            raise ValueError(
                "blueprint_ref and blueprint_snapshot must both be present or both absent"
            )
        if blueprint_snapshot is not None:
            from metaharness.blueprints.models import ArtifactRef, BlueprintVersion
            bp = BlueprintVersion.model_validate(blueprint_snapshot)
            ref = ArtifactRef.model_validate(blueprint_ref)
            if bp.id != ref.id or bp.version != ref.version:
                raise ValueError(
                    f"blueprint snapshot identity {bp.id!r} v{bp.version} "
                    f"does not match ref {ref.id!r} v{ref.version}"
                )
        run_id = f"run_{uuid.uuid4().hex[:12]}"
        journal_path = self.journal_dir / f"{run_id}.jsonl" if self.journal_dir else None
        journal = Journal(path=journal_path, canonical=True)
        digest = _snapshot_digest(spec, blueprint_snapshot)
        state = RunState(
            run_id=run_id,
            workflow=spec.name,
            context=context or {},
            blueprint_ref=blueprint_ref,
            blueprint_snapshot=blueprint_snapshot,
            snapshot_digest=digest,
        )
        payload: dict[str, Any] = {
            "workflow": spec.model_dump(mode="json"),
            "context": state.context,
            "snapshot_digest": digest,
        }
        if state.blueprint_ref is not None:
            payload["blueprint_ref"] = state.blueprint_ref
        if state.blueprint_snapshot is not None:
            payload["blueprint_snapshot"] = state.blueprint_snapshot
        journal.initialize(run_id, payload, snapshot_digest=digest)
        self._runs[run_id] = (spec, state, journal)
        return state

    def state(self, run_id: str) -> RunState:
        return self._runs[run_id][1]

    def journal(self, run_id: str) -> Journal:
        return self._runs[run_id][2]

    def runs(self) -> list[RunState]:
        return [state for _, state, _ in self._runs.values()]

    @staticmethod
    def _archive_path(journal: Journal) -> Optional[Path]:
        return (
            journal._path.with_name(journal._path.stem + ".archive.json")
            if journal._path is not None
            else None
        )

    @classmethod
    def _read_archive_metadata(cls, journal: Journal) -> Optional[float]:
        path = cls._archive_path(journal)
        if path is None or not path.exists():
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        archived_at = value.get("archived_at") if isinstance(value, dict) else None
        if not isinstance(archived_at, (int, float)) or isinstance(archived_at, bool):
            raise ValueError("invalid run archive metadata")
        return float(archived_at)

    @classmethod
    def _write_archive_metadata(
        cls, journal: Journal, archived_at: Optional[float]
    ) -> None:
        path = cls._archive_path(journal)
        if path is None:
            return
        if archived_at is None:
            path.unlink(missing_ok=True)
            dir_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
        )
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump({"archived_at": archived_at}, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(tmp_path, 0o600)
            os.replace(tmp_path, path)
            dir_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        finally:
            tmp_path.unlink(missing_ok=True)

    async def archive(self, run_id: str) -> RunState:
        """Hide a terminal run from active listings without changing its journal."""
        if run_id not in self._runs:
            raise KeyError(run_id)
        async with self._lock_for(run_id):
            _, state, journal = self._runs[run_id]
            await asyncio.to_thread(functools.partial(journal.acquire, refresh=True))
            try:
                self._refresh_state(run_id)
                if state.status not in {RunStatus.COMPLETED, RunStatus.FAILED}:
                    raise RunArchiveConflict("only completed or failed runs can be archived")
                if state.archived_at is not None:
                    raise RunArchiveConflict("run is already archived")
                archived_at = time.time()
                await asyncio.to_thread(
                    self._write_archive_metadata, journal, archived_at
                )
                self._archived_at[run_id] = archived_at
                state.archived_at = archived_at
                return state.model_copy(deep=True)
            finally:
                journal.release()

    async def restore(self, run_id: str) -> RunState:
        """Return an archived run to active listings without changing its journal."""
        if run_id not in self._runs:
            raise KeyError(run_id)
        async with self._lock_for(run_id):
            _, state, journal = self._runs[run_id]
            await asyncio.to_thread(functools.partial(journal.acquire, refresh=True))
            try:
                self._refresh_state(run_id)
                if state.archived_at is None:
                    raise RunArchiveConflict("run is not archived")
                await asyncio.to_thread(self._write_archive_metadata, journal, None)
                self._archived_at.pop(run_id, None)
                state.archived_at = None
                return state.model_copy(deep=True)
            finally:
                journal.release()

    # -- execution -----------------------------------------------------------------

    async def advance(self, run_id: str) -> RunState:
        """Execute ready steps in dependency order until the run completes, fails,
        or pauses at a HITL gate. Call again after `approve` to continue.
        Serialized per run — a second concurrent advance waits, then no-ops on
        the steps the first one already completed."""
        async with self._lock_for(run_id):
            _, state, journal = self._runs[run_id]
            await asyncio.to_thread(
                functools.partial(journal.acquire, refresh=True)
            )
            try:
                self._refresh_state(run_id)
                return await self._advance_locked(run_id)
            finally:
                journal.release()

    async def _advance_locked(self, run_id: str) -> RunState:
        spec, state, journal = self._runs[run_id]
        if state.status == RunStatus.COMPLETED:
            return state
        if state.status == RunStatus.FAILED:
            # A crash can land after the durable step.failed transition but
            # before run.failed.  Replay derives FAILED from the former; finish
            # the canonical stream exactly once instead of returning forever
            # with no run terminal.
            if not journal.entries("run.finished"):
                journal.append_event(
                    "run.failed", run_id,
                    payload={
                        "status": "failed",
                        "failed_step": state.failed_step,
                        "reason": (
                            f"step {state.failed_step} failed"
                            if state.failed_step else "run failed"
                        ),
                    },
                )
            return state
        if state.status == RunStatus.AWAITING_APPROVAL:
            return state
        state.status = RunStatus.RUNNING
        state.awaiting = None

        with tracer().start_as_current_span("workflow.advance") as span:
            span.set_attribute("run.id", run_id)
            span.set_attribute("workflow.name", spec.name)

            for step in spec.topological_order():
                if step.id in state.rejected:
                    state.status = RunStatus.FAILED
                    state.failed_step = step.id
                    journal.append_event(
                        "run.failed", run_id,
                        payload={"status": "failed", "reason": f"step {step.id} rejected",
                                 "failed_step": step.id},
                    )
                    return state
                if step.id in state.completed:
                    if (step.hitl and step.hitl_timing == "after"
                            and step.id not in state.approved):
                        state.status = RunStatus.AWAITING_APPROVAL
                        state.awaiting = step.id
                        journal.append_event("approval.required", run_id, step_id=step.id)
                        return state
                    continue
                if step.id in state.skipped:
                    continue
                skipped_deps = [d for d in step.depends_on if d in state.skipped]
                if skipped_deps:
                    # cascade: a step whose dependency never ran cannot run either
                    reason = f"dependency {skipped_deps[0]!r} was skipped"
                    state.skipped[step.id] = reason
                    journal.append_event("step.skipped", run_id, step_id=step.id,
                                         payload={"reason": reason})
                    continue
                unmet = [d for d in step.depends_on if d not in state.completed]
                if unmet:  # safety net; topological order + fail-fast make this unreachable
                    state.status = RunStatus.FAILED
                    state.failed_step = step.id
                    journal.append_event(
                        "run.failed", run_id,
                        payload={"status": "failed", "failed_step": step.id,
                                 "reason": f"step {step.id} blocked by unmet deps {unmet}"},
                    )
                    return state

                if step.when is not None:
                    outputs = {sid: rec.output for sid, rec in state.completed.items()}
                    if not when_satisfied(step.when, outputs):
                        # the branch not taken is journaled, never silent — and it
                        # is decided BEFORE any HITL gate: nobody approves a step
                        # that was never going to run
                        reason = f"condition not met: {describe_when(step.when)}"
                        state.skipped[step.id] = reason
                        journal.append_event("step.skipped", run_id, step_id=step.id,
                                             payload={"reason": reason})
                        continue

                journal.append_event(
                    "step.ready", run_id, step_id=step.id,
                    payload={
                        "role": step.role,
                        "required_capabilities": list(step.required_capabilities),
                        "worker_id": step.worker_id,
                    },
                )

                tool_gate = any(self.tool_requires_approval(name) for name in step.tools)
                if (
                    ((step.hitl and step.hitl_timing == "before") or tool_gate)
                    and step.id not in state.approved
                ):
                    state.status = RunStatus.AWAITING_APPROVAL
                    state.awaiting = step.id
                    journal.append_event("approval.required", run_id, step_id=step.id)
                    return state

                # Preserve the existing safety UX: an external/MCP step first
                # parks at its mandatory human gate even when currently unloaded.
                # Once approved (or for ungated built-ins), recheck immediately
                # before task construction so a stale capability is never dropped.
                missing_tools = [name for name in step.tools if not self.tool_available(name)]
                if missing_tools:
                    reason = "required tools became unavailable: " + ", ".join(missing_tools)
                    state.status = RunStatus.FAILED
                    state.failed_step = step.id
                    journal.append_event(
                        "step.failed", run_id, step_id=step.id,
                        payload={"reason": reason, "missing_tools": missing_tools},
                    )
                    journal.append_event(
                        "run.failed", run_id,
                        payload={"status": "failed", "failed_step": step.id,
                                 "reason": reason},
                    )
                    return state

                try:
                    outputs = {sid: rec.output for sid, rec in state.completed.items()}
                    resolved = resolve_reference(step.inputs, state.context, outputs)
                    resolved_objective = resolve_text_references(
                        step.objective, state.context, outputs
                    )
                    # FIX-1: split boundaries by TEMPLATE provenance. A boundary
                    # that pulls a prior step's OUTPUT is untrusted-derived text
                    # and must land in Task.advice, never in the caller-authored
                    # boundaries the worker declares as RESPONSE_CONTRACT/
                    # INSTRUCTION. Boundaries with only $context.* refs or no refs
                    # stay boundaries.
                    resolved_boundaries: list[str] = []
                    step_output_advice: list[str] = []
                    for boundary in step.boundaries:
                        resolved_boundary = resolve_text_references(
                            boundary, state.context, outputs
                        )
                        if _STEPS_OUTPUT_REF_RE.search(boundary):
                            step_output_advice.append(resolved_boundary)
                        else:
                            resolved_boundaries.append(resolved_boundary)
                    task = step.to_task(
                        resolved,
                        objective=resolved_objective,
                        boundaries=resolved_boundaries,
                    )
                    if step_output_advice:
                        task.advice = list(task.advice) + step_output_advice
                except ValueError as exc:
                    # a bad input reference must FAIL the run visibly — a stuck
                    # "running" state with no journal trail is the worst outcome
                    state.status = RunStatus.FAILED
                    state.failed_step = step.id
                    journal.append_event("step.failed", run_id, step_id=step.id,
                                         payload={"reason": str(exc)})
                    journal.append_event(
                        "run.failed", run_id,
                        payload={"status": "failed", "failed_step": step.id,
                                 "reason": f"step {step.id}: {exc}"},
                    )
                    return state
                journal.append_event(
                    "step.started", run_id, step_id=step.id,
                    payload={"task_id": task.id},
                )

                try:
                    prior_attempts = sum(
                        event.kind == "attempt.assigned" and event.step_id == step.id
                        for event in journal.events()
                    )
                    def event_sink(kind: str, payload: dict[str, Any]) -> None:
                        attempt = payload.get("n")
                        journal.append_event(
                            kind, run_id, step_id=step.id, payload=payload,
                            attempt_id=(
                                f"{step.id}-attempt-{prior_attempts + int(attempt)}"
                                if attempt is not None else None
                            ),
                        )

                    params = inspect.signature(self.executor.execute).parameters
                    if "event_sink" in params:
                        outcome = await self.executor.execute(task, event_sink=event_sink)
                    else:  # compatibility with small test/custom executors
                        outcome = await self.executor.execute(task)
                except Exception as exc:  # noqa: BLE001 — same visibility rule
                    state.status = RunStatus.FAILED
                    state.failed_step = step.id
                    journal.append_event(
                        "step.failed", run_id, step_id=step.id,
                        payload={"reason": f"{type(exc).__name__}: {exc}"},
                    )
                    journal.append_event(
                        "run.failed", run_id,
                        payload={"status": "failed", "failed_step": step.id,
                                 "reason": f"step {step.id} crashed: {exc}"},
                    )
                    return state
                record = StepRecord(
                    step_id=step.id,
                    verdict=outcome.final_verdict,
                    output=outcome.final_output,
                    attempts=len(outcome.attempts),
                    cost_usd=outcome.total_cost_usd,
                    workspace_root=(outcome.attempts[-1].result.workspace_root
                                    if outcome.attempts else ""),
                )
                if outcome.final_verdict == Verdict.FAIL:
                    journal.append_event(
                        "step.failed", run_id, step_id=step.id,
                        payload=record.model_dump(mode="json"),
                    )
                    state.status = RunStatus.FAILED
                    state.failed_step = step.id
                    journal.append_event(
                        "run.failed", run_id,
                        payload={"status": "failed", "failed_step": step.id,
                                 "reason": f"step {step.id} failed after {record.attempts} attempts"},
                    )
                    return state
                state.completed[step.id] = record
                journal.append_event(
                    "step.completed", run_id, step_id=step.id,
                    payload=record.model_dump(mode="json"),
                )
                if (step.hitl and step.hitl_timing == "after"
                        and step.id not in state.approved):
                    state.status = RunStatus.AWAITING_APPROVAL
                    state.awaiting = step.id
                    journal.append_event("approval.required", run_id, step_id=step.id)
                    return state

            state.status = RunStatus.COMPLETED
            journal.append_event("run.completed", run_id, payload={"status": "completed"})
            return state

    # -- HITL ------------------------------------------------------------------------

    def approve(self, run_id: str, step_id: str) -> None:
        self._resolve_hitl(run_id, step_id, approved=True)

    def reject(self, run_id: str, step_id: str) -> None:
        self._resolve_hitl(run_id, step_id, approved=False)

    def _resolve_hitl(self, run_id: str, step_id: str, *, approved: bool) -> None:
        _, state, journal = self._runs[run_id]
        with journal.transaction(refresh=True):
            self._refresh_state(run_id)
            decided = state.approved if approved else state.rejected
            opposite = state.rejected if approved else state.approved
            if step_id in decided:
                return  # idempotent retry of the same human decision
            if step_id in opposite:
                prior = "rejected" if approved else "approved"
                raise ValueError(
                    f"cannot {'approve' if approved else 'reject'} {step_id!r}; "
                    f"it was already {prior}"
                )
            if state.status != RunStatus.AWAITING_APPROVAL or state.awaiting != step_id:
                if state.awaiting:
                    detail = f"; awaiting approval on {state.awaiting!r}"
                else:
                    detail = "; no approval is pending"
                raise ValueError(f"cannot resolve HITL step {step_id!r}{detail}")
            journal.append_event(
                "approval.resolved", run_id, step_id=step_id,
                payload={"approved": approved},
            )
            decided.add(step_id)
            state.awaiting = None
            state.status = RunStatus.RUNNING

    async def resolve_hitl(self, run_id: str, step_id: str, *, approved: bool) -> RunState:
        """Cross-process-safe, non-event-loop-blocking approval resolution."""
        async with self._lock_for(run_id):
            _, state, journal = self._runs[run_id]
            await asyncio.to_thread(
                functools.partial(journal.acquire, refresh=True)
            )
            try:
                self._refresh_state(run_id)
                self._resolve_hitl(run_id, step_id, approved=approved)
                return state
            finally:
                journal.release()

    async def inspect(
        self, run_id: str
    ) -> tuple[WorkflowSpec, RunState, list[Any], list[Any]]:
        """Return one consistent state + canonical/legacy journal snapshot.

        An advance in this process already owns the journal and updates these
        objects synchronously between awaits, so it can be sampled live without
        recursively waiting on its own run lock.  Otherwise refresh under the
        cross-process sidecar lock.
        """
        spec, state, journal = self._runs[run_id]
        if journal._lock_fd is not None:  # live writer in this process
            return (
                spec.model_copy(deep=True), state.model_copy(deep=True),
                list(journal.events()), list(journal.entries()),
            )
        async with self._lock_for(run_id):
            await asyncio.to_thread(
                functools.partial(journal.acquire, refresh=True)
            )
            try:
                self._refresh_state(run_id)
                return (
                    spec.model_copy(deep=True), state.model_copy(deep=True),
                    list(journal.events()), list(journal.entries()),
                )
            finally:
                journal.release()

    async def fail(self, run_id: str, reason: str) -> RunState:
        """Durably terminate a run after an unexpected outer advance failure."""
        async with self._lock_for(run_id):
            _, state, journal = self._runs[run_id]
            await asyncio.to_thread(
                functools.partial(journal.acquire, refresh=True)
            )
            try:
                self._refresh_state(run_id)
                if state.status == RunStatus.COMPLETED:
                    return state
                if not journal.entries("run.finished"):
                    journal.append_event(
                        "run.failed", run_id,
                        payload={
                            "status": "failed", "failed_step": state.failed_step,
                            "reason": reason,
                        },
                    )
                state.status = RunStatus.FAILED
                return state
            finally:
                journal.release()

    # -- durability --------------------------------------------------------------------

    @classmethod
    def resume(cls, journal_path: str | Path, executor: TaskExecutor) -> tuple["WorkflowEngine", RunState]:
        """Rebuild a run from its journal: completed steps keep their outputs,
        HITL approvals are remembered, and `advance` continues from the first
        unfinished step.

        Completed steps are at-most-once because their terminal event is fsynced
        before the lock is released. An attempt interrupted before its step
        terminal is intentionally re-executed after a crash; workers that cause
        external side effects must provide their own idempotency key.
        """
        journal = Journal.load(journal_path)
        started = journal.entries("run.started")
        if not started:
            raise ValueError(f"journal {journal_path} has no run.started entry")
        head = started[0]
        spec = WorkflowSpec.model_validate(head.payload["workflow"])
        bp_ref = head.payload.get("blueprint_ref")
        bp_snapshot = head.payload.get("blueprint_snapshot")
        if (bp_ref is None) != (bp_snapshot is None):
            raise ValueError(
                "journal blueprint_ref and blueprint_snapshot must both be present or both absent"
            )
        if bp_snapshot is not None:
            from metaharness.blueprints.models import ArtifactRef, BlueprintVersion
            bp = BlueprintVersion.model_validate(bp_snapshot)
            ref = ArtifactRef.model_validate(bp_ref)
            if bp.id != ref.id or bp.version != ref.version:
                raise ValueError(
                    f"journal blueprint snapshot identity {bp.id!r} v{bp.version} "
                    f"does not match ref {ref.id!r} v{ref.version}"
                )
        digest = head.payload.get("snapshot_digest")
        expected_digest = _snapshot_digest(spec, bp_snapshot)
        if digest is not None and digest != expected_digest:
            raise ValueError(
                f"snapshot digest mismatch: journal {digest}, computed {expected_digest}"
            )
        state = cls._state_from_entries(
            spec, journal, digest=digest if digest is not None else expected_digest,
        )
        engine = cls(executor, journal_dir=Path(journal_path).parent)
        engine._runs[state.run_id] = (spec, state, journal)
        return engine, state

    @staticmethod
    def _state_from_entries(
        spec: WorkflowSpec, journal: Journal, *, digest: Optional[str] = None
    ) -> RunState:
        entries = journal.entries()
        started = [entry for entry in entries if entry.kind == "run.started"]
        if not started:
            raise ValueError("journal has no run.started entry")
        head = started[0]
        state = RunState(
            run_id=head.run_id,
            workflow=spec.name,
            context=head.payload.get("context", {}),
            blueprint_ref=head.payload.get("blueprint_ref"),
            blueprint_snapshot=head.payload.get("blueprint_snapshot"),
            snapshot_digest=digest or head.payload.get("snapshot_digest"),
        )
        for entry in entries:
            if entry.kind == "step.completed" and entry.step_id:
                state.completed[entry.step_id] = StepRecord.model_validate(entry.payload)
            elif entry.kind == "step.failed" and entry.step_id:
                state.failed_step = entry.step_id
                state.status = RunStatus.FAILED
            elif entry.kind == "step.skipped" and entry.step_id:
                state.skipped[entry.step_id] = entry.payload.get("reason", "")
            elif entry.kind == "hitl.requested" and entry.step_id:
                state.status = RunStatus.AWAITING_APPROVAL
                state.awaiting = entry.step_id
            elif entry.kind == "hitl.resolved" and entry.step_id:
                target = state.approved if entry.payload.get("approved") else state.rejected
                target.add(entry.step_id)
                if state.awaiting == entry.step_id:
                    state.awaiting = None
                    state.status = RunStatus.RUNNING
            elif entry.kind == "run.finished":
                state.status = (
                    RunStatus.COMPLETED
                    if entry.payload.get("status") == "completed"
                    else RunStatus.FAILED
                )
                if entry.payload.get("failed_step"):
                    state.failed_step = entry.payload["failed_step"]
        return state

    def _refresh_state(self, run_id: str) -> None:
        """Refresh an adopted run after acquiring its cross-process lock."""
        spec, state, journal = self._runs[run_id]
        fresh = self._state_from_entries(
            spec, journal, digest=state.snapshot_digest
        )
        if self._archive_path(journal) is not None:
            fresh.archived_at = self._read_archive_metadata(journal)
            if fresh.archived_at is None:
                self._archived_at.pop(run_id, None)
            else:
                self._archived_at[run_id] = fresh.archived_at
        else:
            fresh.archived_at = self._archived_at.get(run_id)
        for field in RunState.model_fields:
            setattr(state, field, getattr(fresh, field))

    def adopt(self, journal_path: str | Path) -> RunState:
        """Load one journaled run into THIS engine (resume() builds a new one)."""
        other, state = WorkflowEngine.resume(journal_path, self.executor)
        self._runs[state.run_id] = other._runs[state.run_id]
        archived_at = self._read_archive_metadata(self._runs[state.run_id][2])
        if archived_at is not None:
            self._archived_at[state.run_id] = archived_at
            state.archived_at = archived_at
        return state

    def adopt_all(self, journal_dir: str | Path) -> list[RunState]:
        """Rehydrate every journaled run in a directory — call at boot so run
        history survives restarts. Unreadable journals are skipped, not fatal."""
        adopted = []
        for path in sorted(Path(journal_dir).glob("run_*.jsonl")):
            try:
                adopted.append(self.adopt(path))
            except (ValueError, KeyError, OSError):
                continue
        return adopted
