"""WorkflowEngine: run a WorkflowSpec through the TaskExecutor, durably.

Discipline: journal first, act second. Every transition is appended to the
journal before the engine performs it, so a crashed run resumes from its journal
with completed steps intact. HITL steps pause the run (`awaiting_approval`) until
`approve`/`reject` is called — the pause survives restarts too, because it's a
journal entry like everything else.
"""
from __future__ import annotations

import asyncio
import uuid
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field

from metaharness.core.executor import TaskExecutor
from metaharness.core.types import Verdict
from metaharness.observability.tracing import tracer
from metaharness.workflows.dsl import (
    WorkflowSpec,
    describe_when,
    resolve_reference,
    when_satisfied,
)
from metaharness.workflows.journal import Journal


class RunStatus(str, Enum):
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"


class StepRecord(BaseModel):
    step_id: str
    verdict: Verdict
    output: Any = None
    attempts: int = 0
    cost_usd: float = 0.0
    # where this step's file side-effects landed (recorded by the runner);
    # "" for pure text-work — run packaging reads this, never guesses roots
    workspace_root: str = ""


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

    model_config = {"arbitrary_types_allowed": True}


class WorkflowEngine:
    def __init__(self, executor: TaskExecutor, journal_dir: Optional[str | Path] = None) -> None:
        self.executor = executor
        self.journal_dir = Path(journal_dir) if journal_dir else None
        self._runs: dict[str, tuple[WorkflowSpec, RunState, Journal]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock_for(self, run_id: str) -> asyncio.Lock:
        return self._locks.setdefault(run_id, asyncio.Lock())

    # -- lifecycle -----------------------------------------------------------------

    def start(self, spec: WorkflowSpec, context: Optional[dict[str, Any]] = None) -> RunState:
        run_id = f"run_{uuid.uuid4().hex[:12]}"
        journal_path = self.journal_dir / f"{run_id}.jsonl" if self.journal_dir else None
        journal = Journal(path=journal_path)
        state = RunState(run_id=run_id, workflow=spec.name, context=context or {})
        journal.append(
            "run.started", run_id,
            payload={"workflow": spec.model_dump(mode="json"), "context": state.context},
        )
        self._runs[run_id] = (spec, state, journal)
        return state

    def state(self, run_id: str) -> RunState:
        return self._runs[run_id][1]

    def journal(self, run_id: str) -> Journal:
        return self._runs[run_id][2]

    def runs(self) -> list[RunState]:
        return [state for _, state, _ in self._runs.values()]

    # -- execution -----------------------------------------------------------------

    async def advance(self, run_id: str) -> RunState:
        """Execute ready steps in dependency order until the run completes, fails,
        or pauses at a HITL gate. Call again after `approve` to continue.
        Serialized per run — a second concurrent advance waits, then no-ops on
        the steps the first one already completed."""
        async with self._lock_for(run_id):
            return await self._advance_locked(run_id)

    async def _advance_locked(self, run_id: str) -> RunState:
        spec, state, journal = self._runs[run_id]
        if state.status in (RunStatus.COMPLETED, RunStatus.FAILED):
            return state
        state.status = RunStatus.RUNNING
        state.awaiting = None

        with tracer().start_as_current_span("workflow.advance") as span:
            span.set_attribute("run.id", run_id)
            span.set_attribute("workflow.name", spec.name)

            for step in spec.topological_order():
                if step.id in state.completed or step.id in state.skipped:
                    continue
                if step.id in state.rejected:
                    state.status = RunStatus.FAILED
                    state.failed_step = step.id
                    journal.append("run.finished", run_id, payload={"status": "failed", "reason": f"step {step.id} rejected"})
                    return state
                skipped_deps = [d for d in step.depends_on if d in state.skipped]
                if skipped_deps:
                    # cascade: a step whose dependency never ran cannot run either
                    reason = f"dependency {skipped_deps[0]!r} was skipped"
                    state.skipped[step.id] = reason
                    journal.append("step.skipped", run_id, step_id=step.id,
                                   payload={"reason": reason})
                    continue
                unmet = [d for d in step.depends_on if d not in state.completed]
                if unmet:  # safety net; topological order + fail-fast make this unreachable
                    state.status = RunStatus.FAILED
                    state.failed_step = step.id
                    journal.append(
                        "run.finished", run_id,
                        payload={"status": "failed", "reason": f"step {step.id} blocked by unmet deps {unmet}"},
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
                        journal.append("step.skipped", run_id, step_id=step.id,
                                       payload={"reason": reason})
                        continue

                if step.hitl and step.id not in state.approved:
                    state.status = RunStatus.AWAITING_APPROVAL
                    state.awaiting = step.id
                    journal.append("hitl.requested", run_id, step_id=step.id)
                    return state

                try:
                    outputs = {sid: rec.output for sid, rec in state.completed.items()}
                    resolved = resolve_reference(step.inputs, state.context, outputs)
                    task = step.to_task(resolved)
                except ValueError as exc:
                    # a bad input reference must FAIL the run visibly — a stuck
                    # "running" state with no journal trail is the worst outcome
                    state.status = RunStatus.FAILED
                    state.failed_step = step.id
                    journal.append("step.failed", run_id, step_id=step.id,
                                   payload={"reason": str(exc)})
                    journal.append("run.finished", run_id,
                                   payload={"status": "failed", "reason": f"step {step.id}: {exc}"})
                    return state
                journal.append("step.started", run_id, step_id=step.id, payload={"task_id": task.id})

                try:
                    outcome = await self.executor.execute(task)
                except Exception as exc:  # noqa: BLE001 — same visibility rule
                    state.status = RunStatus.FAILED
                    state.failed_step = step.id
                    journal.append("step.failed", run_id, step_id=step.id,
                                   payload={"reason": f"{type(exc).__name__}: {exc}"})
                    journal.append("run.finished", run_id,
                                   payload={"status": "failed", "reason": f"step {step.id} crashed: {exc}"})
                    return state
                for att in outcome.attempts:
                    # every attempt is journaled with its verdict + verifier
                    # reason, so "failed after 3 attempts" is diagnosable from
                    # the run journal alone (judge reasons included)
                    journal.append(
                        "step.attempt", run_id, step_id=step.id,
                        payload={
                            "n": att.n,
                            "model": att.result.model,
                            "tier": att.result.tier.value,
                            "verdict": att.verification.verdict.value,
                            "scorer": att.verification.scorer,
                            "detail": att.verification.detail[:300],
                            # issue #2: a timeout is now structurally identifiable
                            # from the journal alone, not just free-text detail
                            "failure_mode": (att.verification.failure_mode.value
                                             if att.verification.failure_mode else None),
                            "latency_s": round(att.result.latency_s, 2),
                            "timed_out": att.result.timed_out,
                        },
                    )
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
                    journal.append(
                        "step.failed", run_id, step_id=step.id,
                        payload=record.model_dump(mode="json"),
                    )
                    state.status = RunStatus.FAILED
                    state.failed_step = step.id
                    journal.append(
                        "run.finished", run_id,
                        payload={"status": "failed", "reason": f"step {step.id} failed after {record.attempts} attempts"},
                    )
                    return state
                state.completed[step.id] = record
                journal.append(
                    "step.completed", run_id, step_id=step.id,
                    payload=record.model_dump(mode="json"),
                )

            state.status = RunStatus.COMPLETED
            journal.append("run.finished", run_id, payload={"status": "completed"})
            return state

    # -- HITL ------------------------------------------------------------------------

    def approve(self, run_id: str, step_id: str) -> None:
        _, state, journal = self._runs[run_id]
        state.approved.add(step_id)
        journal.append("hitl.resolved", run_id, step_id=step_id, payload={"approved": True})

    def reject(self, run_id: str, step_id: str) -> None:
        _, state, journal = self._runs[run_id]
        state.rejected.add(step_id)
        journal.append("hitl.resolved", run_id, step_id=step_id, payload={"approved": False})

    # -- durability --------------------------------------------------------------------

    @classmethod
    def resume(cls, journal_path: str | Path, executor: TaskExecutor) -> tuple["WorkflowEngine", RunState]:
        """Rebuild a run from its journal: completed steps keep their outputs,
        HITL approvals are remembered, and `advance` continues from the first
        unfinished step."""
        journal = Journal.load(journal_path)
        started = journal.entries("run.started")
        if not started:
            raise ValueError(f"journal {journal_path} has no run.started entry")
        head = started[0]
        spec = WorkflowSpec.model_validate(head.payload["workflow"])
        state = RunState(
            run_id=head.run_id, workflow=spec.name, context=head.payload.get("context", {})
        )
        for entry in journal.entries():
            if entry.kind == "step.completed":
                record = StepRecord.model_validate(entry.payload)
                state.completed[entry.step_id] = record
            elif entry.kind == "step.skipped" and entry.step_id:
                state.skipped[entry.step_id] = entry.payload.get("reason", "")
            elif entry.kind == "hitl.resolved" and entry.step_id:
                (state.approved if entry.payload.get("approved") else state.rejected).add(entry.step_id)
            elif entry.kind == "run.finished":
                state.status = (
                    RunStatus.COMPLETED
                    if entry.payload.get("status") == "completed"
                    else RunStatus.FAILED
                )
        engine = cls(executor, journal_dir=Path(journal_path).parent)
        engine._runs[state.run_id] = (spec, state, journal)
        return engine, state

    def adopt(self, journal_path: str | Path) -> RunState:
        """Load one journaled run into THIS engine (resume() builds a new one)."""
        other, state = WorkflowEngine.resume(journal_path, self.executor)
        self._runs[state.run_id] = other._runs[state.run_id]
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
