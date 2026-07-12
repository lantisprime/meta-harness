"""Workflow spine tests: DSL validation, reference resolution, engine execution,
HITL gates, and the load-bearing one — kill the engine mid-run and resume from
the journal with completed steps intact."""
from __future__ import annotations

import stat

import pytest

from metaharness.core import Task, TaskExecutor, TaskType, Tier, Verdict
from metaharness.harness import CodingAgentWorker, MockLLMWorker, ScriptedWorker
from metaharness.routing import Router
from metaharness.workflows import (
    Journal,
    RunArchiveConflict,
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


async def test_engine_resolves_inline_context_and_step_refs_before_execution(tmp_path):
    seen: list[Task] = []

    def handler(task: Task):
        seen.append(task)
        if task.objective.startswith("Gather"):
            return {"facts": "gathered context"}
        return "analysis complete"

    spec = WorkflowSpec.model_validate({
        "name": "inline-refs",
        "steps": [
            {
                "id": "gather",
                "task_type": "general",
                "objective": "Gather source material for $context.goal.",
                "inputs": {"goal": "$context.goal"},
            },
            {
                "id": "analyze",
                "task_type": "reasoning",
                "objective": (
                    "Analyze $context.goal using $steps.gather.output.facts."
                ),
                "boundaries": [
                    "Do not ignore prior output: $steps.gather.output.facts."
                ],
                "depends_on": ["gather"],
                "inputs": {"prior": "$steps.gather.output"},
            },
        ],
    })
    executor = TaskExecutor(Router({Tier.SMALL: ScriptedWorker("w", handler)}))
    engine = WorkflowEngine(executor, journal_dir=tmp_path)

    state = engine.start(spec, context={"goal": "explain MCP setup"})
    state = await engine.advance(state.run_id)

    assert state.status == RunStatus.COMPLETED
    assert len(seen) == 2
    assert seen[0].objective == "Gather source material for explain MCP setup."
    assert seen[1].objective == "Analyze explain MCP setup using gathered context."
    assert seen[1].boundaries == ["Do not ignore prior output: gathered context."]
    assert seen[1].inputs["prior"] == {"facts": "gathered context"}
    assert "$context" not in seen[1].objective
    assert "$steps" not in seen[1].objective


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
    assert resumed.status == RunStatus.AWAITING_APPROVAL
    assert resumed.awaiting == "notify"
    engine2.approve(resumed.run_id, "notify")
    final = await engine2.advance(resumed.run_id)
    assert final.status == RunStatus.COMPLETED
    # classify/summarize did NOT re-run after resume
    assert calls == ["classify", "summarize", "transform"]
    journal = engine2.journal(resumed.run_id)
    assert len(journal.entries("hitl.requested")) == 1
    assert len(journal.entries("hitl.resolved")) == 1


async def test_hitl_resolution_rejects_a_non_pending_or_duplicate_step(tmp_path):
    engine = WorkflowEngine(perfect_executor(), journal_dir=tmp_path)
    state = engine.start(load_workflow(TRIAGE_YAML), context={"ticket": "x"})
    state = await engine.advance(state.run_id)

    with pytest.raises(ValueError, match="awaiting approval on 'notify'"):
        engine.approve(state.run_id, "")
    with pytest.raises(ValueError, match="awaiting approval on 'notify'"):
        engine.reject(state.run_id, "other")

    assert state.status == RunStatus.AWAITING_APPROVAL
    assert state.awaiting == "notify"
    assert engine.journal(state.run_id).entries("hitl.resolved") == []

    engine.approve(state.run_id, "notify")
    engine.approve(state.run_id, "notify")  # same decision is idempotent
    assert len(engine.journal(state.run_id).entries("hitl.resolved")) == 1


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


async def test_no_eligible_route_can_never_complete_a_zero_attempt_step(tmp_path):
    """A routing abort is FAIL, never a successful UNVERIFIED no-op."""
    worker = ScriptedWorker("plain", lambda _task: "unused")
    engine = WorkflowEngine(
        TaskExecutor(Router({Tier.SMALL: worker})), journal_dir=tmp_path
    )
    spec = WorkflowSpec.model_validate({
        "name": "no-route",
        "steps": [{"id": "work", "objective": "work", "role": "missing-role"}],
    })
    run = engine.start(spec)
    run = await engine.advance(run.run_id)

    assert run.status is RunStatus.FAILED
    assert run.failed_step == "work"
    assert "work" not in run.completed
    assert not engine.journal(run.run_id).events("attempt.started")


async def test_required_tool_is_rechecked_immediately_before_stage(tmp_path):
    available = {"needed"}
    engine = WorkflowEngine(
        perfect_executor(), journal_dir=tmp_path,
        tool_available=lambda name: name in available,
    )
    spec = WorkflowSpec.model_validate({
        "name": "tool-drift",
        "steps": [
            {"id": "gate", "objective": "gate", "hitl": True},
            {"id": "use", "objective": "use", "depends_on": ["gate"],
             "tools": ["needed"]},
        ],
    })
    run = engine.start(spec)
    run = await engine.advance(run.run_id)
    assert run.awaiting == "gate"
    available.clear()
    engine.approve(run.run_id, "gate")
    run = await engine.advance(run.run_id)

    assert run.status is RunStatus.FAILED and run.failed_step == "use"
    failed = engine.journal(run.run_id).events("step.failed")[-1]
    assert failed.payload["missing_tools"] == ["needed"]


async def test_resume_at_post_artifact_gate_keeps_output_without_rerunning(tmp_path):
    calls: list[str] = []

    def handler(task: Task):
        calls.append(task.task_type.value)
        return "artifact ready for review"

    executor = TaskExecutor(Router({Tier.SMALL: ScriptedWorker("w", handler)}))
    spec = WorkflowSpec.model_validate({
        "name": "post-artifact",
        "steps": [{"id": "draft", "objective": "Draft it.",
                   "hitl": True, "hitl_timing": "after"}],
    })
    engine = WorkflowEngine(executor, journal_dir=tmp_path)
    run = engine.start(spec)
    run = await engine.advance(run.run_id)
    assert run.status is RunStatus.AWAITING_APPROVAL
    assert run.completed["draft"].output == "artifact ready for review"

    engine2, resumed = WorkflowEngine.resume(
        tmp_path / f"{run.run_id}.jsonl", executor)
    assert resumed.status is RunStatus.AWAITING_APPROVAL
    assert resumed.awaiting == "draft"
    assert resumed.completed["draft"].output == "artifact ready for review"
    engine2.approve(resumed.run_id, "draft")
    final = await engine2.advance(resumed.run_id)

    assert final.status is RunStatus.COMPLETED
    assert calls == ["general"]
    journal = engine2.journal(resumed.run_id)
    assert len(journal.entries("hitl.requested")) == 1
    assert len(journal.entries("hitl.resolved")) == 1


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


# -- per-attempt journaling (v0.4: diagnosability) ------------------------------------


async def test_step_attempts_journaled_with_verdict_and_detail(tmp_path):
    """Bug: a step 'failed after 3 attempts' with no per-attempt trail in the run
    journal — judge reasons lived only in the provenance chain. Every attempt is
    now journaled as step.attempt {n, model, tier, verdict, scorer, detail}."""
    engine = WorkflowEngine(perfect_executor(), journal_dir=tmp_path)
    spec = load_workflow(TRIAGE_YAML)
    state = engine.start(spec, context={"ticket": "db-1 disk full"})
    state = await engine.advance(state.run_id)

    atts = engine.journal(state.run_id).entries("step.attempt")
    assert atts, "completed steps must journal their attempts"
    by_step = {e.step_id for e in atts}
    assert {"classify", "summarize"} <= by_step
    payload = atts[0].payload
    assert {"n", "model", "tier", "verdict", "scorer", "detail"} <= set(payload)
    assert payload["verdict"] == "pass"


async def test_failed_step_attempts_journaled(tmp_path):
    """A failing step journals one step.attempt per retry, with the verifier's
    reason, BEFORE the step.failed entry."""

    def always_wrong(task: Task):
        return "low"  # success_check demands "high"

    executor = TaskExecutor(Router({Tier.SMALL: ScriptedWorker("w", always_wrong)}))
    engine = WorkflowEngine(executor, journal_dir=tmp_path)
    spec = load_workflow(TRIAGE_YAML)
    state = engine.start(spec, context={"ticket": "db-1 disk full"})
    state = await engine.advance(state.run_id)

    assert state.status is RunStatus.FAILED and state.failed_step == "classify"
    entries = engine.journal(state.run_id).entries()
    kinds = [e.kind for e in entries]
    atts = [e for e in entries if e.kind == "step.attempt"]
    assert len(atts) >= 2, "each retry journals its own attempt"
    assert all(e.payload["verdict"] == "fail" for e in atts)
    assert all(e.payload["detail"] for e in atts), "verifier reason is never empty"
    assert kinds.index("step.attempt") < kinds.index("step.failed")


async def test_run_archive_metadata_survives_adopt_and_preserves_journal(tmp_path):
    spec = WorkflowSpec.model_validate({
        "name": "archive-demo",
        "steps": [{"id": "work", "objective": "Do the durable work."}],
    })
    executor = perfect_executor()
    engine = WorkflowEngine(executor, journal_dir=tmp_path)
    running = engine.start(spec)
    with pytest.raises(RunArchiveConflict, match="completed or failed"):
        await engine.archive(running.run_id)
    completed = await engine.advance(running.run_id)
    journal_path = tmp_path / f"{completed.run_id}.jsonl"
    journal_before = journal_path.read_bytes()
    output_before = completed.completed["work"].output

    archived = await engine.archive(completed.run_id)
    assert archived.archived_at is not None
    assert journal_path.read_bytes() == journal_before
    assert (await engine.inspect(completed.run_id))[1].completed["work"].output == output_before
    with pytest.raises(RunArchiveConflict, match="already archived"):
        await engine.archive(completed.run_id)

    restarted = WorkflowEngine(executor, journal_dir=tmp_path)
    adopted = restarted.adopt_all(tmp_path)
    restored_run = next(run for run in adopted if run.run_id == completed.run_id)
    assert restored_run.archived_at == archived.archived_at
    assert restored_run.completed["work"].output == output_before
    restored = await restarted.restore(completed.run_id)
    assert restored.archived_at is None
    with pytest.raises(RunArchiveConflict, match="not archived"):
        await restarted.restore(completed.run_id)

    restarted_again = WorkflowEngine(executor, journal_dir=tmp_path)
    adopted_again = restarted_again.adopt_all(tmp_path)
    assert next(run for run in adopted_again if run.run_id == completed.run_id).archived_at is None


async def test_failed_run_can_be_archived_and_restored(tmp_path):
    def wrong(_task: Task):
        return "wrong"

    executor = TaskExecutor(Router({Tier.SMALL: ScriptedWorker("wrong", wrong)}))
    engine = WorkflowEngine(executor, journal_dir=tmp_path)
    spec = WorkflowSpec.model_validate({
        "name": "failed-archive",
        "steps": [{"id": "work", "objective": "Return right.",
                   "success_check": {"equals": "right"}, "max_attempts": 1}],
    })
    failed = await engine.advance(engine.start(spec).run_id)
    assert failed.status is RunStatus.FAILED
    assert (await engine.archive(failed.run_id)).archived_at is not None
    restored = await engine.restore(failed.run_id)
    assert restored.status is RunStatus.FAILED and restored.archived_at is None


async def test_step_attempt_journal_records_timeout(tmp_path):
    """issue #2: a real coding-CLI timeout is journaled as timed_out=True /
    failure_mode='timeout' with a numeric latency — diagnosable from the run
    journal alone, not just free-text detail."""
    binary = tmp_path / "codex-stub"
    binary.write_text("#!/bin/sh\nsleep 30\n")
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)

    worker = CodingAgentWorker("cc", cli="codex", workspace=tmp_path / "ws",
                               binary=str(binary), timeout_s=0.5)
    executor = TaskExecutor(Router({Tier.SMALL: worker}))
    engine = WorkflowEngine(executor, journal_dir=tmp_path)
    spec = WorkflowSpec.model_validate({
        "name": "timeout-probe",
        "steps": [{"id": "edit", "task_type": "code_edit",
                  "objective": "make the change", "max_attempts": 1}],
    })
    state = engine.start(spec, context={})
    state = await engine.advance(state.run_id)

    atts = engine.journal(state.run_id).entries("step.attempt")
    assert len(atts) == 1
    payload = atts[0].payload
    assert payload["timed_out"] is True
    assert payload["failure_mode"] == "timeout"
    assert isinstance(payload["latency_s"], float) and payload["latency_s"] > 0


async def test_step_attempt_journal_records_non_timeout_tool_error(tmp_path):
    """Guards the verifier branch order (issue #2): a plain execution failure
    must still record timed_out=False / failure_mode='tool_error', not get
    misclassified as a timeout."""

    def boom(task: Task):
        raise RuntimeError("boom")

    executor = TaskExecutor(Router({Tier.SMALL: ScriptedWorker("w", boom)}))
    engine = WorkflowEngine(executor, journal_dir=tmp_path)
    spec = WorkflowSpec.model_validate({
        "name": "tool-error-probe",
        "steps": [{"id": "s", "objective": "do it", "max_attempts": 1}],
    })
    state = engine.start(spec, context={})
    state = await engine.advance(state.run_id)

    atts = engine.journal(state.run_id).entries("step.attempt")
    assert len(atts) == 1
    payload = atts[0].payload
    assert payload["timed_out"] is False
    assert payload["failure_mode"] == "tool_error"


def _valid_bp_snapshot(bp_id: str = "test-bp", version: int = 3, name: str = "Test BP"):
    """A minimal valid BlueprintVersion-shaped snapshot for engine tests."""
    return {
        "schema_version": 1,
        "name": name,
        "description": "",
        "workflow": {
            "name": "wf",
            "steps": [{"id": "s", "objective": "o"}],
        },
        "inputs": [],
        "default_context": {},
        "eval_suites": [],
        "id": bp_id,
        "version": version,
        "published_at": 1.0,
    }


async def test_run_start_embeds_blueprint_ref_and_snapshot(tmp_path):
    """Saved-harness runs record the exact blueprint reference and a full
    snapshot in the RunState and the first journal entry."""
    engine = WorkflowEngine(perfect_executor(), journal_dir=tmp_path)
    spec = load_workflow(TRIAGE_YAML)
    bp_ref = {"id": "test-bp", "version": 3}
    bp_snapshot = _valid_bp_snapshot()
    state = engine.start(
        spec,
        context={"ticket": "x"},
        blueprint_ref=bp_ref,
        blueprint_snapshot=bp_snapshot,
    )
    assert state.blueprint_ref == bp_ref
    assert state.blueprint_snapshot == bp_snapshot
    assert state.snapshot_digest is not None

    started = engine.journal(state.run_id).entries("run.started")[0]
    assert started.payload["blueprint_ref"] == bp_ref
    assert started.payload["blueprint_snapshot"] == bp_snapshot
    assert started.payload["snapshot_digest"] == state.snapshot_digest


async def test_ad_hoc_run_has_null_blueprint_ref_and_snapshot(tmp_path):
    """Legacy/ad-hoc runs keep blueprint fields null and journals unchanged."""
    engine = WorkflowEngine(perfect_executor(), journal_dir=tmp_path)
    spec = load_workflow(TRIAGE_YAML)
    state = engine.start(spec, context={"ticket": "x"})
    assert state.blueprint_ref is None
    assert state.blueprint_snapshot is None
    assert state.snapshot_digest is not None

    started = engine.journal(state.run_id).entries("run.started")[0]
    assert "blueprint_ref" not in started.payload
    assert "blueprint_snapshot" not in started.payload
    assert started.payload["snapshot_digest"] == state.snapshot_digest


async def test_resume_restores_blueprint_ref_and_snapshot(tmp_path):
    """Resuming a saved-harness run restores its exact blueprint ref and
    snapshot from the journal without touching the catalog."""
    engine = WorkflowEngine(perfect_executor(), journal_dir=tmp_path)
    spec = load_workflow(TRIAGE_YAML)
    bp_ref = {"id": "resume-bp", "version": 2}
    bp_snapshot = _valid_bp_snapshot("resume-bp", 2, "Resume BP")
    state = engine.start(
        spec,
        context={"ticket": "x"},
        blueprint_ref=bp_ref,
        blueprint_snapshot=bp_snapshot,
    )
    state = await engine.advance(state.run_id)
    engine.approve(state.run_id, "notify")
    journal_path = tmp_path / f"{state.run_id}.jsonl"

    engine2, resumed = WorkflowEngine.resume(journal_path, perfect_executor())
    assert resumed.blueprint_ref == bp_ref
    assert resumed.blueprint_snapshot == bp_snapshot
    assert resumed.snapshot_digest is not None
    final = await engine2.advance(resumed.run_id)
    assert final.status is RunStatus.COMPLETED


async def test_resume_verifies_present_snapshot_digest(tmp_path):
    """A journal whose embedded snapshot digest does not match the snapshot
    fails resume with a clear error, not silent corruption."""
    engine = WorkflowEngine(perfect_executor(), journal_dir=tmp_path)
    spec = load_workflow(TRIAGE_YAML)
    bp_snapshot = _valid_bp_snapshot("digest-bp", 1, "Digest BP")
    state = engine.start(
        spec,
        context={"ticket": "x"},
        blueprint_ref={"id": "digest-bp", "version": 1},
        blueprint_snapshot=bp_snapshot,
    )
    journal_path = tmp_path / f"{state.run_id}.jsonl"

    # tamper with the digest in the journal file
    import json
    lines = journal_path.read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0])
    first["payload"]["snapshot_digest"] = "0" * 64
    lines[0] = json.dumps(first, sort_keys=True)
    journal_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="snapshot digest mismatch"):
        WorkflowEngine.resume(journal_path, perfect_executor())


async def test_resume_tolerates_old_journals_without_digest(tmp_path):
    """Journals created before snapshot_digest was introduced still resume
    successfully and backfill the computed digest."""
    engine = WorkflowEngine(perfect_executor(), journal_dir=tmp_path)
    spec = load_workflow(TRIAGE_YAML)
    state = engine.start(spec, context={"ticket": "x"})
    state = await engine.advance(state.run_id)
    journal_path = tmp_path / f"{state.run_id}.jsonl"

    # strip the digest as if this were an old journal
    import json
    lines = journal_path.read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0])
    first["payload"].pop("snapshot_digest", None)
    lines[0] = json.dumps(first, sort_keys=True)
    journal_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    engine2, resumed = WorkflowEngine.resume(journal_path, perfect_executor())
    assert resumed.snapshot_digest is not None
    engine2.approve(resumed.run_id, "notify")
    final = await engine2.advance(resumed.run_id)
    assert final.status is RunStatus.COMPLETED


async def test_start_rejects_mismatched_blueprint_provenance(tmp_path):
    """Ref and snapshot must agree on id/version; one-sided values are rejected."""
    engine = WorkflowEngine(perfect_executor(), journal_dir=tmp_path)
    spec = load_workflow(TRIAGE_YAML)
    bp_snapshot = _valid_bp_snapshot("right-bp", 1, "Right BP")

    with pytest.raises(ValueError, match="both be present or both absent"):
        engine.start(spec, blueprint_ref={"id": "right-bp", "version": 1})
    with pytest.raises(ValueError, match="both be present or both absent"):
        engine.start(spec, blueprint_snapshot=bp_snapshot)
    with pytest.raises(ValueError, match="does not match ref"):
        engine.start(
            spec,
            blueprint_ref={"id": "wrong-bp", "version": 1},
            blueprint_snapshot=bp_snapshot,
        )
    with pytest.raises(ValueError, match="does not match ref"):
        engine.start(
            spec,
            blueprint_ref={"id": "right-bp", "version": 2},
            blueprint_snapshot=bp_snapshot,
        )
