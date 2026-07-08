"""Workflow spine tests: DSL validation, reference resolution, engine execution,
HITL gates, and the load-bearing one — kill the engine mid-run and resume from
the journal with completed steps intact."""
from __future__ import annotations

import pytest

from metaharness.core import Task, TaskExecutor, TaskType, Tier, Verdict
from metaharness.harness import MockLLMWorker, ScriptedWorker
from metaharness.routing import Router
from metaharness.workflows import (
    Journal,
    RunStatus,
    WorkflowEngine,
    WorkflowSpec,
    load_workflow,
    resolve_reference,
)

TRIAGE_YAML = """
name: triage
steps:
  - id: classify
    task_type: classify
    objective: Classify the ticket severity.
    inputs: {text: "$context.ticket", labels: [low, high]}
    success_check: {equals: high}
  - id: summarize
    task_type: summarize
    objective: Summarize for the on-call engineer.
    depends_on: [classify]
    inputs: {severity: "$steps.classify.output"}
    success_check: {contains: summary}
  - id: notify
    task_type: transform
    objective: Draft the page to send.
    depends_on: [summarize]
    hitl: true
    success_check: {contains: page}
"""


def perfect_executor() -> TaskExecutor:
    """Workers that always produce the expected answer."""

    def handler(task: Task):
        if task.task_type == TaskType.CLASSIFY:
            return "high"
        if task.task_type == TaskType.SUMMARIZE:
            return "summary: disk full on db-1"
        return "page: db-1 disk full, severity high"

    return TaskExecutor(Router({Tier.SMALL: ScriptedWorker("w", handler)}))


# -- DSL ---------------------------------------------------------------------------


def test_load_workflow_and_topo_order():
    spec = load_workflow(TRIAGE_YAML)
    assert spec.name == "triage"
    assert [s.id for s in spec.topological_order()] == ["classify", "summarize", "notify"]


def test_dsl_rejects_duplicate_ids_unknown_deps_and_cycles():
    with pytest.raises(ValueError, match="duplicate"):
        WorkflowSpec.model_validate(
            {"name": "x", "steps": [{"id": "a", "objective": "o"}, {"id": "a", "objective": "o"}]}
        )
    with pytest.raises(ValueError, match="unknown steps"):
        WorkflowSpec.model_validate(
            {"name": "x", "steps": [{"id": "a", "objective": "o", "depends_on": ["ghost"]}]}
        )
    with pytest.raises(ValueError, match="cycle"):
        WorkflowSpec.model_validate(
            {"name": "x", "steps": [
                {"id": "a", "objective": "o", "depends_on": ["b"]},
                {"id": "b", "objective": "o", "depends_on": ["a"]},
            ]}
        )


def test_resolve_reference():
    context = {"ticket": "disk full"}
    outputs = {"classify": {"label": "high", "confidence": 0.9}}
    assert resolve_reference("$context.ticket", context, outputs) == "disk full"
    assert resolve_reference("$steps.classify.output.label", context, outputs) == "high"
    assert resolve_reference("$steps.classify.output", context, outputs) == outputs["classify"]
    assert resolve_reference({"x": "$context.ticket", "y": [1, "$steps.classify.output.label"]},
                             context, outputs) == {"x": "disk full", "y": [1, "high"]}
    assert resolve_reference("plain", context, outputs) == "plain"
    with pytest.raises(ValueError):
        resolve_reference("$steps.ghost.output", context, outputs)
    with pytest.raises(ValueError):
        resolve_reference("$context.missing", context, outputs)


# -- engine -------------------------------------------------------------------------


async def test_run_to_completion_with_hitl(tmp_path):
    engine = WorkflowEngine(perfect_executor(), journal_dir=tmp_path)
    spec = load_workflow(TRIAGE_YAML)
    state = engine.start(spec, context={"ticket": "db-1 disk full"})
    state = await engine.advance(state.run_id)

    assert state.status == RunStatus.AWAITING_APPROVAL and state.awaiting == "notify"
    assert set(state.completed) == {"classify", "summarize"}
    assert state.completed["classify"].output == "high"

    engine.approve(state.run_id, "notify")
    state = await engine.advance(state.run_id)
    assert state.status == RunStatus.COMPLETED
    assert set(state.completed) == {"classify", "summarize", "notify"}

    kinds = [e.kind for e in engine.journal(state.run_id).entries()]
    assert kinds[0] == "run.started" and kinds[-1] == "run.finished"
    assert "hitl.requested" in kinds and "hitl.resolved" in kinds


async def test_hitl_rejection_fails_run(tmp_path):
    engine = WorkflowEngine(perfect_executor(), journal_dir=tmp_path)
    state = engine.start(load_workflow(TRIAGE_YAML), context={"ticket": "x"})
    state = await engine.advance(state.run_id)
    engine.reject(state.run_id, "notify")
    state = await engine.advance(state.run_id)
    assert state.status == RunStatus.FAILED and state.failed_step == "notify"


async def test_failed_step_fails_run_fast(tmp_path):
    always_wrong = MockLLMWorker("w", Tier.SMALL, seed=1,
                                 skills={t: 0.0 for t in TaskType})
    engine = WorkflowEngine(
        TaskExecutor(Router({Tier.SMALL: always_wrong})), journal_dir=tmp_path
    )
    state = engine.start(load_workflow(TRIAGE_YAML), context={"ticket": "x"})
    state = await engine.advance(state.run_id)
    assert state.status == RunStatus.FAILED
    assert state.failed_step == "classify"
    assert "summarize" not in state.completed  # never ran


async def test_resume_after_crash_skips_completed_steps(tmp_path):
    """Durability: run half the workflow, throw the engine away, resume from the
    journal file — completed steps keep their outputs and don't re-execute."""
    calls: list[str] = []

    def counting_handler(task: Task):
        calls.append(task.task_type.value)
        if task.task_type == TaskType.CLASSIFY:
            return "high"
        if task.task_type == TaskType.SUMMARIZE:
            return "summary: ..."
        return "page: ..."

    executor = TaskExecutor(Router({Tier.SMALL: ScriptedWorker("w", counting_handler)}))
    engine = WorkflowEngine(executor, journal_dir=tmp_path)
    state = engine.start(load_workflow(TRIAGE_YAML), context={"ticket": "x"})
    state = await engine.advance(state.run_id)  # pauses at HITL gate
    assert state.status == RunStatus.AWAITING_APPROVAL
    assert calls == ["classify", "summarize"]
    journal_path = tmp_path / f"{state.run_id}.jsonl"
    assert journal_path.exists()

    del engine  # "crash"

    engine2, resumed = WorkflowEngine.resume(journal_path, executor)
    assert resumed.completed["classify"].output == "high"
    engine2.approve(resumed.run_id, "notify")
    final = await engine2.advance(resumed.run_id)
    assert final.status == RunStatus.COMPLETED
    # classify/summarize did NOT re-run after resume
    assert calls == ["classify", "summarize", "transform"]


async def test_resume_remembers_prior_approval(tmp_path):
    executor = perfect_executor()
    engine = WorkflowEngine(executor, journal_dir=tmp_path)
    state = engine.start(load_workflow(TRIAGE_YAML), context={"ticket": "x"})
    state = await engine.advance(state.run_id)
    engine.approve(state.run_id, "notify")  # approve, then crash before advancing
    journal_path = tmp_path / f"{state.run_id}.jsonl"

    engine2, resumed = WorkflowEngine.resume(journal_path, executor)
    final = await engine2.advance(resumed.run_id)
    assert final.status == RunStatus.COMPLETED  # no second approval needed


async def test_unresolvable_reference_fails_run_visibly(tmp_path):
    """Regression (run_a3d567c20a20, 2026-07-08): a plan step referencing a
    missing context key raised inside advance(); the background task swallowed
    it and the run sat in 'running' forever with only run.started journaled.
    A bad reference must FAIL the run with the reason journaled."""
    spec = WorkflowSpec.model_validate({
        "name": "bad-ref",
        "steps": [{"id": "s1", "objective": "o", "inputs": {"goal": "$context.goal"}}],
    })
    engine = WorkflowEngine(perfect_executor(), journal_dir=tmp_path)
    state = engine.start(spec, context={})  # no 'goal' key
    state = await engine.advance(state.run_id)
    assert state.status == RunStatus.FAILED
    assert state.failed_step == "s1"
    journal = engine.journal(state.run_id)
    failed = journal.entries("step.failed")
    assert failed and "goal" in failed[0].payload["reason"]
    finished = journal.entries("run.finished")
    assert finished and finished[0].payload["status"] == "failed"


async def test_crashing_executor_fails_run_visibly(tmp_path):
    """Same rule for an executor that raises: journaled failure, never stuck."""
    class Boom:
        async def execute(self, task):
            raise RuntimeError("executor exploded")

    spec = WorkflowSpec.model_validate(
        {"name": "boom", "steps": [{"id": "s1", "objective": "o"}]})
    engine = WorkflowEngine(Boom(), journal_dir=tmp_path)
    state = engine.start(spec, context={})
    state = await engine.advance(state.run_id)
    assert state.status == RunStatus.FAILED
    assert "exploded" in engine.journal(state.run_id).entries("step.failed")[0].payload["reason"]


def test_journal_load_roundtrip(tmp_path):
    path = tmp_path / "j.jsonl"
    journal = Journal(path=path)
    journal.append("run.started", "run_1", payload={"a": 1})
    journal.append("step.started", "run_1", step_id="s1")
    loaded = Journal.load(path)
    assert len(loaded) == 2
    assert loaded.entries("step.started")[0].step_id == "s1"
    # appends continue into the same file with correct seq
    loaded.append("step.completed", "run_1", step_id="s1")
    reloaded = Journal.load(path)
    assert [e.seq for e in reloaded.entries()] == [0, 1, 2]


# -- conditional steps: when clauses -------------------------------------------


def _executor(scripted: dict[str, str]) -> TaskExecutor:
    """Workers answering by objective keyword: {'classify': 'low', ...}."""

    def handler(task: Task):
        objective = task.objective.lower()
        for key, answer in scripted.items():
            if key in objective:
                return answer
        return "unscripted"

    return TaskExecutor(Router({Tier.SMALL: ScriptedWorker("w", handler)}))


def _branchy_spec():
    from metaharness.workflows.dsl import WorkflowSpec
    return WorkflowSpec.model_validate({
        "name": "branchy",
        "steps": [
            {"id": "classify", "task_type": "classify",
             "objective": "Classify severity as exactly one of: low, high.",
             "inputs": {"labels": ["low", "high"]},
             "success_check": {"one_of": ["low", "high"]}},
            {"id": "page", "objective": "Draft the page.",
             "when": {"step": "classify", "equals": "high"}},
            {"id": "archive", "objective": "Archive the ticket.",
             "when": {"step": "classify", "equals": "low"}},
            {"id": "notify-page", "objective": "Notify about the page.",
             "depends_on": ["page"]},
        ],
    })


def test_when_validation_and_auto_dependency():
    from metaharness.workflows.dsl import WorkflowSpec
    spec = _branchy_spec()
    # the condition source becomes an explicit dependency automatically
    assert "classify" in spec.step("page").depends_on

    with pytest.raises(ValueError, match="unknown step"):
        WorkflowSpec.model_validate({"name": "w", "steps": [
            {"id": "a", "objective": "x", "when": {"step": "ghost", "equals": 1}}]})
    with pytest.raises(ValueError, match="cannot reference itself"):
        WorkflowSpec.model_validate({"name": "w", "steps": [
            {"id": "a", "objective": "x", "when": {"step": "a", "equals": 1}}]})
    with pytest.raises(ValueError, match="exactly one of"):
        WorkflowSpec.model_validate({"name": "w", "steps": [
            {"id": "a", "objective": "x"},
            {"id": "b", "objective": "y", "when": {"step": "a"}}]})


def test_when_predicate_semantics():
    from metaharness.workflows.dsl import when_satisfied
    outs = {"c": "high", "n": 12, "d": {"k": "v"}}
    assert when_satisfied({"step": "c", "equals": "high"}, outs)
    assert when_satisfied({"step": "c", "equals": "high", "negate": True}, outs) is False
    assert when_satisfied({"step": "n", "equals": 12}, outs)
    assert when_satisfied({"step": "n", "equals": "12"}, outs)  # string form matches
    assert when_satisfied({"step": "c", "one_of": ["low", "high"]}, outs)
    assert when_satisfied({"step": "d", "contains": "k"}, outs)
    assert when_satisfied({"step": "missing", "equals": 1}, outs) is False


async def test_branch_not_taken_is_skipped_and_cascades(tmp_path):
    """classify says 'low' -> page skipped (condition), notify-page skipped
    (cascade), archive runs; the run COMPLETES and every skip is journaled."""
    executor = _executor({"classify": "low", "archive": "archived",
                          "notify": "notified", "page": "PAGE"})
    engine = WorkflowEngine(executor, journal_dir=tmp_path)
    run = engine.start(_branchy_spec(), context={})
    run = await engine.advance(run.run_id)

    assert run.status is RunStatus.COMPLETED
    assert set(run.completed) == {"classify", "archive"}
    assert run.skipped == {
        "page": "condition not met: if classify equals high",
        "notify-page": "dependency 'page' was skipped",
    }
    kinds = [e.kind for e in engine.journal(run.run_id).entries()]
    assert kinds.count("step.skipped") == 2

    # durability: the rebuilt run remembers what was skipped and stays complete
    engine2, resumed = WorkflowEngine.resume(
        tmp_path / f"{run.run_id}.jsonl", executor)
    assert resumed.skipped == run.skipped
    assert (await engine2.advance(resumed.run_id)).status is RunStatus.COMPLETED


async def test_branch_taken_runs_and_gate_not_asked_for_skipped(tmp_path):
    """classify says 'high' -> page runs (even though archive is gated: a
    skipped step's HITL gate must never park the run)."""
    from metaharness.workflows.dsl import WorkflowSpec
    spec = WorkflowSpec.model_validate({
        "name": "gated-branch",
        "steps": [
            {"id": "classify", "task_type": "classify",
             "objective": "Classify severity as exactly one of: low, high.",
             "inputs": {"labels": ["low", "high"]},
             "success_check": {"one_of": ["low", "high"]}},
            {"id": "archive", "objective": "Archive.", "hitl": True,
             "when": {"step": "classify", "equals": "low"}},
            {"id": "page", "objective": "Page.",
             "when": {"step": "classify", "equals": "high"}},
        ],
    })
    executor = _executor({"classify": "high", "page": "PAGED",
                          "archive": "ARCHIVED"})
    engine = WorkflowEngine(executor, journal_dir=tmp_path)
    run = engine.start(spec, context={})
    run = await engine.advance(run.run_id)
    assert run.status is RunStatus.COMPLETED  # never parked at archive's gate
    assert "page" in run.completed
    assert "archive" in run.skipped
