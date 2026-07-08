"""Rubric judge: LLM evaluation of UNVERIFIED step outputs."""
from __future__ import annotations

import json

import pytest

from metaharness.core import TaskExecutor
from metaharness.core.types import Task, TaskType, Tier, Verdict
from metaharness.evals.judge import make_judge
from metaharness.harness import ScriptedWorker
from metaharness.identity import KeyPair
from metaharness.routing.router import Router


def _judge_worker(handler):
    return ScriptedWorker("judge-w", handler, tier=Tier.FRONTIER,
                          keypair=KeyPair.generate())


async def test_judge_grades_against_the_contract():
    seen = []

    def handler(task):
        seen.append(task)
        return {"pass": False, "reason": "the summary ignores the outage window"}

    judge = make_judge(_judge_worker(handler))
    task = Task(objective="Summarize the incident.", boundaries=["mention the window"])
    from metaharness.core.types import WorkerResult
    result = WorkerResult(task_id=task.id, worker_id="w", tier=Tier.SMALL,
                          model="m", output="a vague summary")
    verification = await judge(task, result)
    assert verification.verdict is Verdict.FAIL
    assert verification.scorer == "judge"
    assert "outage window" in verification.detail
    # fresh-context grading task: contract + artifact, external-role framing
    prompt = seen[0].objective
    assert "Summarize the incident." in prompt and "a vague summary" in prompt
    assert "You did not produce it" in prompt
    assert seen[0].output_schema is not None


async def test_unparseable_judgment_degrades_to_unverified():
    judge = make_judge(_judge_worker(lambda t: "hmm, looks fine I guess"))
    from metaharness.core.types import WorkerResult
    task = Task(objective="do something")
    result = WorkerResult(task_id=task.id, worker_id="w", tier=Tier.SMALL,
                          model="m", output="output")
    verification = await judge(task, result)
    assert verification.verdict is Verdict.UNVERIFIED  # honest uncertainty


async def test_executor_judge_gates_unverified_and_drives_retry():
    """First attempt judged FAIL -> reflection/retry; second passes. The step
    only completes with an output the judge accepted — dependents never see
    the rejected draft."""
    attempts = []

    def worker_handler(task):
        attempts.append(task)
        return f"draft #{len(attempts)}"

    verdicts = iter([{"pass": False, "reason": "draft 1 is empty fluff"},
                     {"pass": True, "reason": "draft 2 does the job"}])

    def judge_handler(task):
        return next(verdicts)

    worker = ScriptedWorker("w", worker_handler, keypair=KeyPair.generate())
    executor = TaskExecutor(Router({Tier.SMALL: worker}),
                            judge=make_judge(_judge_worker(judge_handler)))
    outcome = await executor.execute(Task(objective="write the summary",
                                          max_attempts=3))
    assert outcome.final_verdict is Verdict.PASS
    assert outcome.final_output == "draft #2"
    assert len(outcome.attempts) == 2
    assert outcome.attempts[0].verification.scorer == "judge"
    assert outcome.attempts[0].verification.verdict is Verdict.FAIL


async def test_deterministic_checks_bypass_the_judge():
    calls = []

    def judge_handler(task):
        calls.append(task)
        return {"pass": True, "reason": "x"}

    worker = ScriptedWorker("w", lambda t: "high", keypair=KeyPair.generate())
    executor = TaskExecutor(Router({Tier.SMALL: worker}),
                            judge=make_judge(_judge_worker(judge_handler)))
    outcome = await executor.execute(Task(
        objective="classify", success_check={"one_of": ["low", "high"]}))
    assert outcome.final_verdict is Verdict.PASS
    assert calls == []  # deterministic verdict — judge never consulted


async def test_planning_tasks_are_exempt_from_judging():
    calls = []

    def judge_handler(task):
        calls.append(task)
        return {"pass": False, "reason": "no"}

    worker = ScriptedWorker("w", lambda t: {"name": "p", "steps": []},
                            keypair=KeyPair.generate())
    executor = TaskExecutor(Router({Tier.SMALL: worker}),
                            judge=make_judge(_judge_worker(judge_handler)))
    outcome = await executor.execute(Task(objective="plan it",
                                          task_type=TaskType.PLANNING))
    assert outcome.final_verdict is Verdict.UNVERIFIED
    assert calls == []
