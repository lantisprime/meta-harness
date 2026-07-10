"""Self-correction tests: playbook deltas + persistence, grounded reflection,
MAST classification, and the two-speed learning loop end to end."""
from __future__ import annotations

from metaharness.core.types import (
    Attempt,
    MASTMode,
    Task,
    TaskOutcome,
    TaskType,
    VerificationResult,
    Verdict,
    WorkerResult,
)
from metaharness.core import TaskExecutor, Tier
from metaharness.correction import (
    FailureStats,
    LearningLoop,
    Playbook,
    classify_failure,
    grounded_reflector,
)
from metaharness.harness import MockLLMWorker
from metaharness.routing import Router


def attempt(verdict: Verdict, *, output="x", error=None, mode=None, detail="",
            timed_out=False) -> Attempt:
    return Attempt(
        n=1,
        result=WorkerResult(
            task_id="t", worker_id="w", tier=Tier.SMALL, model="m",
            output=output, error=error, timed_out=timed_out,
        ),
        verification=VerificationResult(
            verdict=verdict, score=0.0 if verdict == Verdict.FAIL else 1.0,
            failure_mode=mode, detail=detail,
        ),
    )


def failed_outcome(task_type: TaskType, mode: MASTMode = None) -> TaskOutcome:
    task = Task(task_type=task_type, objective="o", success_check={"equals": 1})
    return TaskOutcome(
        task=task,
        attempts=[attempt(Verdict.FAIL, mode=mode)],
        final_verdict=Verdict.FAIL,
    )


# -- playbook ----------------------------------------------------------------------


def test_playbook_delta_operations_and_scoping():
    pb = Playbook()
    general = pb.add("Always re-read the objective.")
    classify = pb.add("Pick exactly one label.", task_type=TaskType.CLASSIFY)
    summar = pb.add("Lead with the outcome.", task_type=TaskType.SUMMARIZE)

    task = Task(task_type=TaskType.CLASSIFY)
    texts = pb.hints_for(task)
    assert general.text in texts and classify.text in texts and summar.text not in texts

    pb.amend(classify.id, "Pick exactly one label from the provided list.")
    assert "provided list" in pb.get(classify.id).text

    pb.deprecate(general.id)
    assert general.text not in pb.hints_for(task)


def test_playbook_scoring_orders_hints():
    pb = Playbook()
    weak = pb.add("weak advice", task_type=TaskType.CLASSIFY)
    strong = pb.add("strong advice", task_type=TaskType.CLASSIFY)
    for _ in range(5):
        pb.mark(strong.id, helpful=True)
        pb.mark(weak.id, helpful=False)
    hints = pb.hints_for(Task(task_type=TaskType.CLASSIFY))
    assert hints[0] == "strong advice"


def test_playbook_persistence_roundtrip(tmp_path):
    pb = Playbook()
    bullet = pb.add("persist me", task_type=TaskType.EXTRACT)
    pb.mark(bullet.id, helpful=True)
    path = tmp_path / "playbook.json"
    pb.save(path)
    loaded = Playbook.load(path)
    restored = loaded.get(bullet.id)
    assert restored.text == "persist me" and restored.helpful == 1
    assert restored.task_type == TaskType.EXTRACT


# -- reflexion ---------------------------------------------------------------------


def test_reflector_grounded_in_signal_without_leaking_answer():
    task = Task(task_type=TaskType.CLASSIFY, success_check={"equals": "high"})
    a = attempt(Verdict.FAIL, output="low", detail="expected 'high', got 'low'")
    reflection = grounded_reflector(task, a)
    assert reflection is not None
    assert "low" in reflection          # names what the worker did
    assert "high" not in reflection     # never leaks the expected answer


def test_reflector_reveals_spec_level_requirements():
    task = Task(success_check={"contains": "summary"})
    a = attempt(Verdict.FAIL, output="blah")
    assert "summary" in grounded_reflector(task, a)

    task2 = Task(success_check={"one_of": ["low", "high"]})
    a2 = attempt(Verdict.FAIL, output="mid")
    reflection = grounded_reflector(task2, a2)
    assert "low" in reflection and "high" in reflection


def test_reflector_schema_and_pass_and_budget():
    task = Task()
    schema_fail = attempt(Verdict.FAIL, mode=MASTMode.SCHEMA_INVALID, detail="missing key 'label'")
    assert "schema" in grounded_reflector(task, schema_fail).lower()
    assert grounded_reflector(task, attempt(Verdict.PASS)) is None
    assert grounded_reflector(task, attempt(Verdict.FAIL, mode=MASTMode.BUDGET_EXCEEDED)) is None


def test_reflector_timeout_gives_retry_actionable_advice():
    """issue #2 (panel, GLM P2): TIMEOUT advice must fit what a RETRY can do —
    take the most direct path — never 'raise the timeout', which is a config
    action outside the worker's control."""
    reflection = grounded_reflector(Task(), attempt(Verdict.FAIL, mode=MASTMode.TIMEOUT))
    assert "ran out of time" in reflection and "direct path" in reflection
    assert "timeout" not in reflection.lower()  # no un-actionable config advice


# -- MAST ---------------------------------------------------------------------------


def test_classify_failure_rules():
    task = Task(success_check={"equals": 1})
    assert classify_failure(task, attempt(Verdict.FAIL, mode=MASTMode.SCHEMA_INVALID)) == MASTMode.SCHEMA_INVALID
    assert classify_failure(task, attempt(Verdict.FAIL, error="boom")) == MASTMode.TOOL_ERROR
    assert classify_failure(task, attempt(Verdict.FAIL)) == MASTMode.DISOBEY_TASK_SPEC
    assert classify_failure(task, attempt(Verdict.UNVERIFIED)) == MASTMode.NO_VERIFICATION
    # issue #2 (panel, GLM P2): a timed-out attempt with no verifier label must
    # classify as TIMEOUT, checked before the generic error->TOOL_ERROR fallback
    assert classify_failure(
        task, attempt(Verdict.FAIL, error="x: timed out after 1s", timed_out=True)
    ) == MASTMode.TIMEOUT


def test_failure_stats_clusters():
    stats = FailureStats()
    for _ in range(4):
        stats.observe(failed_outcome(TaskType.EXTRACT, MASTMode.SCHEMA_INVALID))
    stats.observe(failed_outcome(TaskType.CLASSIFY, MASTMode.DISOBEY_TASK_SPEC))
    top = stats.top_clusters(1)[0]
    assert top == ("extract", MASTMode.SCHEMA_INVALID, 4)
    assert stats.as_dict()["classify"]["disobey_task_spec"] == 1


# -- two-speed loop -------------------------------------------------------------------


def test_curation_adds_bullet_for_big_cluster_once():
    loop = LearningLoop(Playbook(), min_cluster=3)
    for _ in range(3):
        loop.observe(failed_outcome(TaskType.EXTRACT, MASTMode.SCHEMA_INVALID))
    deltas = loop.curate()
    assert len(deltas) == 1 and deltas[0].startswith("add")
    bullets = loop.playbook.bullets()
    assert len(bullets) == 1 and bullets[0].task_type == TaskType.EXTRACT

    # curating again does not duplicate
    assert loop.curate() == []
    assert len(loop.playbook.bullets()) == 1


def test_small_cluster_earns_no_bullet():
    loop = LearningLoop(Playbook(), min_cluster=3)
    loop.observe(failed_outcome(TaskType.EXTRACT, MASTMode.SCHEMA_INVALID))
    assert loop.curate() == []


def test_curation_covers_timeout_clusters():
    """issue #2 (panel, GLM P2): a TIMEOUT failure cluster earns a playbook
    bullet like any other curated mode — the template must exist."""
    loop = LearningLoop(Playbook(), min_cluster=3)
    for _ in range(3):
        loop.observe(failed_outcome(TaskType.CODE_EDIT, MASTMode.TIMEOUT))
    deltas = loop.curate()
    assert len(deltas) == 1 and deltas[0].startswith("add")
    bullets = loop.playbook.bullets()
    assert len(bullets) == 1 and bullets[0].task_type == TaskType.CODE_EDIT
    assert "out of time" in bullets[0].text


def test_bullet_effectiveness_and_deprecation():
    pb = Playbook()
    bad = pb.add("misleading advice", task_type=TaskType.CLASSIFY)
    loop = LearningLoop(pb, deprecate_after=4)
    for _ in range(5):
        task = Task(task_type=TaskType.CLASSIFY, success_check={"equals": 1})
        loop.hints_for(task)
        outcome = TaskOutcome(task=task, attempts=[attempt(Verdict.FAIL)], final_verdict=Verdict.FAIL)
        loop.observe(outcome)
    deltas = loop.curate()
    assert any(d.startswith("deprecate") for d in deltas)
    assert not pb.get(bad.id).active


async def test_learning_loop_wired_into_executor():
    """Full integration: hints flow into attempts, outcomes flow back into stats."""
    pb = Playbook()
    pb.add("Pick exactly one of the provided labels.", task_type=TaskType.CLASSIFY)
    loop = LearningLoop(pb)
    seen_boundaries: list[str] = []

    class SpyWorker(MockLLMWorker):
        async def _execute(self, task):
            seen_boundaries.extend(task.boundaries)
            return await super()._execute(task)

    runner = SpyWorker("w", Tier.SMALL, seed=1, skills={TaskType.CLASSIFY: 1.0})
    executor = TaskExecutor(
        Router({Tier.SMALL: runner}),
        reflector=grounded_reflector,
        playbook_hints=loop.hints_for,
    )
    task = Task(task_type=TaskType.CLASSIFY, objective="classify",
                inputs={"labels": ["a", "b"]}, success_check={"equals": "a"})
    outcome = await executor.execute(task)
    loop.observe(outcome)

    assert any("provided labels" in b for b in seen_boundaries)
    assert pb.bullets()[0].helpful == 1  # bullet credited for the pass
