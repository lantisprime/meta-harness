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


def test_context_manifest_is_a_canonical_attempt_scoped_kind(tmp_path):
    """META-19 (F1): context.manifest is journaled as a canonical kind with an
    attempt id; a durable Journal.load() round-trips it, and it has no legacy
    projection (entries() must skip it, never crash)."""
    from metaharness.workflows.journal import CANONICAL_KINDS

    assert "context.manifest" in CANONICAL_KINDS
    path = tmp_path / "manifest_run.jsonl"
    journal = Journal(path=path, canonical=True)
    journal.initialize("run_m", {"context": {}}, snapshot_digest="a" * 64)
    journal.append_event("step.started", "run_m", step_id="work", payload={"task_id": "t1"})
    event = journal.append_event(
        "context.manifest", "run_m", step_id="work",
        payload={"schema_version": 1, "shadow": False, "task_id": "t1", "round": 0, "n": 1},
        attempt_id="work-attempt-1",
    )
    assert event.kind == "context.manifest"
    assert event.attempt_id == "work-attempt-1"

    reloaded = Journal.load(path)
    manifests = [e for e in reloaded.events() if e.kind == "context.manifest"]
    assert len(manifests) == 1
    assert manifests[0].payload["shadow"] is False
    # no legacy projection — the historical entries() view skips it silently
    assert all(entry.kind != "context.manifest" for entry in reloaded.entries())


async def test_context_manifest_journaled_per_round_end_to_end(tmp_path):
    """META-19 (test 4): a real run — engine -> executor -> worker ->
    Journal.load() — journals one NON-shadow context.manifest per tool round,
    each pinning the exact bytes that round sent, under the attempt id. No
    context.manifest.shadow event exists anymore."""
    import httpx

    from metaharness.context import content_hash
    from metaharness.harness import OpenAICompatWorker

    sent: list[list[dict]] = []

    class Client:
        async def post(self, url, json=None, headers=None):
            sent.append(json["messages"])
            if len(sent) == 1:
                message = {"content": None, "tool_calls": [
                    {"id": "c1", "function": {"name": "probe", "arguments": "{}"}}]}
            else:
                message = {"content": "ok"}
            return httpx.Response(
                200, json={"choices": [{"message": message}]},
                request=httpx.Request("POST", url),
            )

    class Registry:
        workspace_root = ""

        def openai_schemas(self, names):
            return [{"type": "function", "function": {"name": "probe", "parameters": {}}}]

        async def call(self, name, arguments, focus=""):
            return "tool observation"

    worker = OpenAICompatWorker(
        "worker-a", base_url="http://fake/v1", model="m",
        client=Client(), tool_registry=Registry(),
    )
    executor = TaskExecutor(Router({Tier.SMALL: worker}))
    spec = WorkflowSpec.model_validate({
        "name": "tool-run",
        "steps": [{"id": "work", "objective": "use probe", "tools": ["probe"],
                   "success_check": {"equals": "ok"}}],
    })
    engine = WorkflowEngine(executor, journal_dir=tmp_path)
    run = engine.start(spec)
    run = await engine.advance(run.run_id)
    assert run.status is RunStatus.COMPLETED

    journal = Journal.load(tmp_path / f"{run.run_id}.jsonl")
    manifests = [e for e in journal.events() if e.kind == "context.manifest"]
    assert len(manifests) == 2
    assert [e.payload["round"] for e in manifests] == [0, 1]
    assert all(e.payload["shadow"] is False for e in manifests)
    assert all(e.attempt_id == "work-attempt-1" for e in manifests)
    for event, messages in zip(manifests, sent):
        assert event.payload["live_messages_hash"] == content_hash(messages)
    assert not any(e.kind == "context.manifest.shadow" for e in journal.events())
    # the new kind has no legacy projection and never crashes entries()
    assert all(entry.kind != "context.manifest" for entry in journal.entries())


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


def test_fix8_context_manifest_requires_attempt_id():
    """FIX-8 (codex#9): context.manifest is attempt-scoped, so a RunEvent of that
    kind without an attempt_id must be rejected (same rule as attempt.*/tool.*)."""
    from pydantic import ValidationError

    from metaharness.workflows.journal import RunEvent

    common = dict(seq=1, at=0.0, kind="context.manifest", run_id="r",
                  snapshot_digest="a" * 64, step_id="work")
    with pytest.raises(ValidationError):
        RunEvent(**common)  # no attempt_id
    # with the attempt id it validates
    event = RunEvent(**common, attempt_id="work-attempt-1")
    assert event.attempt_id == "work-attempt-1"
