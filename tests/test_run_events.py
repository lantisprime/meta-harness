"""Canonical run-event durability, replay, and compatibility contracts."""
from __future__ import annotations

import asyncio
import json
import os

import pytest

from metaharness.core import Task, TaskExecutor, Tier, Verdict
from metaharness.core.types import TaskOutcome
from metaharness.harness import ScriptedWorker
from metaharness.routing import Router
from metaharness.workflows import Journal, RunStatus, WorkflowEngine, WorkflowSpec


def _executor() -> TaskExecutor:
    return TaskExecutor(Router({Tier.SMALL: ScriptedWorker("worker-a", lambda _task: "ok")}))


def _spec(*, hitl: bool = False) -> WorkflowSpec:
    return WorkflowSpec.model_validate({
        "name": "event-test",
        "steps": [{
            "id": "work", "objective": "return ok", "hitl": hitl,
            "success_check": {"equals": "ok"},
        }],
    })


async def test_new_run_writes_only_canonical_events_and_projects_legacy(tmp_path):
    engine = WorkflowEngine(_executor(), journal_dir=tmp_path)
    run = engine.start(_spec())
    run = await engine.advance(run.run_id)
    journal = engine.journal(run.run_id)

    events = journal.events()
    assert [event.seq for event in events] == list(range(len(events)))
    assert len({event.run_id for event in events}) == 1
    assert len({event.snapshot_digest for event in events}) == 1
    kinds = [event.kind for event in events]
    assert kinds == [
        "run.started", "step.ready", "step.started", "attempt.assigned",
        "attempt.started", "verification.started", "verification.completed",
        "step.completed", "run.completed",
    ]
    assigned = next(event for event in events if event.kind == "attempt.assigned")
    assert assigned.attempt_id == "work-attempt-1"
    assert assigned.payload == {
        "n": 1, "worker_id": "worker-a", "model": "scripted",
        "tier": "small", "requested_role": None,
        "requested_capabilities": [], "requested_worker_id": None,
    }

    raw_kinds = [json.loads(line)["kind"]
                 for line in (tmp_path / f"{run.run_id}.jsonl").read_text().splitlines()]
    assert raw_kinds == kinds
    assert "step.attempt" not in raw_kinds
    assert "run.finished" not in raw_kinds
    assert [entry.kind for entry in journal.entries()] == [
        "run.started", "step.started", "step.attempt", "step.completed",
        "run.finished",
    ]


async def test_approval_resolution_is_idempotent_and_opposite_conflicts(tmp_path):
    engine = WorkflowEngine(_executor(), journal_dir=tmp_path)
    run = engine.start(_spec(hitl=True))
    run = await engine.advance(run.run_id)
    assert run.status is RunStatus.AWAITING_APPROVAL

    engine.approve(run.run_id, "work")
    engine.approve(run.run_id, "work")
    assert len(engine.journal(run.run_id).events("approval.resolved")) == 1
    with pytest.raises(ValueError, match="already approved"):
        engine.reject(run.run_id, "work")


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda lines: [lines[0].replace('"seq": 0', '"seq": 0, "seq": 0')],
         "duplicate JSON key"),
        (lambda lines: [lines[0].replace('"at":', '"at": NaN, "old_at":', 1)],
         "non-finite JSON number"),
        (lambda lines: [lines[0].replace('"seq": 0', '"seq": 2')],
         "sequence gap"),
        (lambda lines: [lines[0], lines[1].replace(lines[1].split('"run_id": "')[1].split('"')[0], "other")],
         "mixed run IDs"),
        (lambda lines: [lines[0], lines[1].replace(lines[1].split('"snapshot_digest": "')[1].split('"')[0], "0" * 64)],
         "snapshot digest changed"),
    ],
)
async def test_load_rejects_corrupt_canonical_streams(tmp_path, mutate, message):
    engine = WorkflowEngine(_executor(), journal_dir=tmp_path)
    run = engine.start(_spec())
    await engine.advance(run.run_id)
    path = tmp_path / f"{run.run_id}.jsonl"
    changed = mutate(path.read_text().splitlines())
    path.write_text("\n".join(changed) + "\n")
    with pytest.raises(ValueError, match=message):
        Journal.load(path)


async def test_load_rejects_truncated_and_symlink_paths(tmp_path):
    engine = WorkflowEngine(_executor(), journal_dir=tmp_path)
    run = engine.start(_spec())
    path = tmp_path / f"{run.run_id}.jsonl"
    path.write_bytes(path.read_bytes()[:-1])
    with pytest.raises(ValueError, match="truncated"):
        Journal.load(path)

    target = tmp_path / "target.jsonl"
    target.write_text("")
    link = tmp_path / "link.jsonl"
    os.symlink(target, link)
    with pytest.raises(ValueError, match="non-symlink"):
        Journal.load(link)


async def test_two_resumed_engines_run_completed_step_at_most_once(tmp_path):
    calls = 0

    class SlowExecutor:
        async def execute(self, task: Task, *, event_sink=None) -> TaskOutcome:
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.05)
            return TaskOutcome(
                task=task, final_verdict=Verdict.PASS, final_output="ok"
            )

    first = WorkflowEngine(SlowExecutor(), journal_dir=tmp_path)
    run = first.start(_spec())
    path = tmp_path / f"{run.run_id}.jsonl"
    left, _ = WorkflowEngine.resume(path, SlowExecutor())
    right, _ = WorkflowEngine.resume(path, SlowExecutor())

    left_state, right_state = await asyncio.gather(
        left.advance(run.run_id), right.advance(run.run_id)
    )
    assert calls == 1
    assert left_state.status is RunStatus.COMPLETED
    assert right_state.status is RunStatus.COMPLETED
    loaded = Journal.load(path)
    assert len(loaded.events("step.completed")) == 1
    assert len(loaded.events("run.completed")) == 1


async def test_resume_reconciles_step_failed_without_run_terminal(tmp_path, monkeypatch):
    engine = WorkflowEngine(_executor(), journal_dir=tmp_path)
    spec = WorkflowSpec.model_validate({
        "name": "fail", "steps": [{
            "id": "work", "objective": "return bad", "max_attempts": 1,
            "success_check": {"equals": "never"},
        }],
    })
    run = engine.start(spec)
    journal = engine.journal(run.run_id)
    real_append = journal.append_event

    def crash_before_run_terminal(kind, *args, **kwargs):
        if kind == "run.failed":
            raise OSError("injected crash")
        return real_append(kind, *args, **kwargs)

    monkeypatch.setattr(journal, "append_event", crash_before_run_terminal)
    with pytest.raises(OSError, match="injected"):
        await engine.advance(run.run_id)

    resumed, state = WorkflowEngine.resume(tmp_path / f"{run.run_id}.jsonl", _executor())
    assert state.status is RunStatus.FAILED
    await resumed.advance(run.run_id)
    await resumed.advance(run.run_id)
    assert len(resumed.journal(run.run_id).events("run.failed")) == 1


async def test_cross_process_inspect_and_approval_refresh_stale_state(tmp_path):
    first = WorkflowEngine(_executor(), journal_dir=tmp_path)
    run = first.start(_spec(hitl=True))
    path = tmp_path / f"{run.run_id}.jsonl"
    second, stale = WorkflowEngine.resume(path, _executor())
    assert stale.status is RunStatus.RUNNING

    await first.advance(run.run_id)
    _spec_copy, fresh, events, _entries = await second.inspect(run.run_id)
    assert fresh.status is RunStatus.AWAITING_APPROVAL
    assert any(event.kind == "approval.required" for event in events)
    await second.resolve_hitl(run.run_id, "work", approved=True)
    final = await second.advance(run.run_id)
    assert final.status is RunStatus.COMPLETED


async def test_legacy_journal_resumes_and_continues_without_mixing_formats(tmp_path):
    path = tmp_path / "run_legacy.jsonl"
    spec = _spec()
    legacy = Journal(path=path)
    legacy.append("run.started", "run_legacy", payload={
        "workflow": spec.model_dump(mode="json"), "context": {},
    })

    engine, state = WorkflowEngine.resume(path, _executor())
    state = await engine.advance(state.run_id)
    assert state.status is RunStatus.COMPLETED
    raw = [json.loads(line) for line in path.read_text().splitlines()]
    assert all("schema_version" not in record for record in raw)
    assert [record["seq"] for record in raw] == list(range(len(raw)))
    assert any(record["kind"] == "step.completed" for record in raw)
