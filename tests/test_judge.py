"""Rubric judge: LLM evaluation of UNVERIFIED step outputs."""
from __future__ import annotations

import json

import pytest

from metaharness.core import TaskExecutor
from metaharness.core.types import Task, TaskType, Tier, Verdict, WorkerResult
from metaharness.evals.judge import make_judge
from metaharness.harness import ScriptedWorker
from metaharness.harness.runner import Runner
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


async def test_judge_receives_harness_execution_receipt_as_ground_truth():
    seen = []

    def handler(task):
        seen.append(task)
        return {"pass": True, "reason": "the sandboxed tests passed"}

    task = Task(
        objective="Verify every acceptance criterion.",
        inputs={
            "harness_execution_evidence": (
                "Harness-owned execution receipt (not worker-authored):\n"
                "command: python -m pytest -q\nstatus: passed\noutput:\n3 passed"
            )
        },
    )
    result = WorkerResult(
        task_id=task.id, worker_id="w", tier=Tier.SMALL, model="m",
        output={"all_met": True, "criteria": []},
    )

    verification = await make_judge(_judge_worker(handler))(task, result)

    assert verification.verdict is Verdict.PASS
    prompt = seen[0].objective
    assert "command: python -m pytest -q" in prompt
    assert "3 passed" in prompt
    assert "harness evidence" in prompt.lower()


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


async def test_judge_charges_its_own_runner_spend_to_the_shared_budget():
    """Issue #5 item 3b: the judge's own worker call runs a real runner whose
    tokens/cost/latency were previously invisible to the budget. make_judge's
    optional `budget` param must charge them (charge-always) after the
    runner call, on top of whatever the main attempt already charged."""
    from metaharness.core.budget import Budget

    class Billed(Runner):
        worker_id, tier, model = "judge-w", Tier.FRONTIER, "judge-w"
        async def run(self, task):
            return WorkerResult(task_id=task.id, worker_id="judge-w", tier=self.tier,
                                model="judge-w", output={"pass": True, "reason": "ok"},
                                tokens_in=50, tokens_out=20, cost_usd=0.05, latency_s=1.5)

    budget = Budget(max_tokens=1_000_000, max_cost_usd=1000.0)
    judge = make_judge(Billed(), budget=budget)
    task = Task(objective="do something")
    result = WorkerResult(task_id=task.id, worker_id="w", tier=Tier.SMALL,
                          model="m", output="output")
    verification = await judge(task, result)
    assert verification.verdict is Verdict.PASS
    assert budget.spent_tokens == 70
    assert budget.spent_cost_usd == pytest.approx(0.05)
    assert budget.spent_wall_s == pytest.approx(1.5)


async def test_judge_budget_exceeded_surfaces_as_budget_stop_not_judge_error():
    """The judge's charge blowing the cap must stop the run as a budget event
    (task.budget_exceeded), NEVER be swallowed into a judge.error note — a
    budget stop must not be laundered into 'the judge broke'."""
    from metaharness.core.budget import Budget

    class Billed(Runner):
        worker_id, tier, model = "judge-w", Tier.FRONTIER, "judge-w"
        async def run(self, task):
            return WorkerResult(task_id=task.id, worker_id="judge-w", tier=self.tier,
                                model="judge-w", output={"pass": True, "reason": "ok"},
                                tokens_in=1000, tokens_out=0)

    budget = Budget(max_tokens=5)  # the judge's own charge blows this cap
    from metaharness.identity import ProvenanceLog

    orch_kp = KeyPair.generate()
    provenance = ProvenanceLog()
    executor = TaskExecutor(
        Router({Tier.SMALL: ScriptedWorker("w", lambda t: "some output")}),
        provenance=provenance, orchestrator_keypair=orch_kp, budget=budget,
        judge=make_judge(Billed(), budget=budget),
    )
    outcome = await executor.execute(Task(objective="do a thing"))

    assert outcome.final_verdict == Verdict.FAIL
    assert outcome.attempts[-1].verification.scorer == "budget"
    kinds = [e.action for e in provenance.entries()]
    assert "task.budget_exceeded" in kinds
    assert "judge.error" not in kinds


async def test_over_budget_before_judge_slot_never_invokes_the_judge():
    """The charge-site BudgetExceeded (before the judge slot) must stop the run
    without ever consulting the judge — asserting the judge runner's call count
    stays zero (not just that its verdict is absent)."""
    calls = []

    class Billed(Runner):
        worker_id, tier, model = "judge-w", Tier.FRONTIER, "judge-w"
        async def run(self, task):
            calls.append(task)
            return WorkerResult(task_id=task.id, worker_id="judge-w", tier=self.tier,
                                model="judge-w", output={"pass": True, "reason": "ok"})

    from metaharness.core.budget import Budget

    class MainWorker(Runner):
        worker_id, tier, model = "w", Tier.SMALL, "w"
        async def run(self, task):
            return WorkerResult(task_id=task.id, worker_id="w", tier=self.tier,
                                model="w", output={"essay": "..."},
                                tokens_in=30, tokens_out=10)  # blows the cap below

    budget = Budget(max_tokens=5)  # the main attempt's own charge blows this cap
    executor = TaskExecutor(
        Router({Tier.SMALL: MainWorker()}), budget=budget, judge=make_judge(Billed()),
    )
    outcome = await executor.execute(Task(objective="write", task_type=TaskType.SUMMARIZE))
    assert outcome.attempts[-1].verification.scorer == "budget"
    assert calls == []  # judge never invoked


async def test_broken_judge_is_recorded_not_silent():
    """Bug: a judge that raised was swallowed by a bare except — the task kept
    its UNVERIFIED verdict with zero trace of why. The executor now records a
    judge.error provenance entry (task still never fails because of the judge)."""
    from metaharness.identity import ProvenanceLog

    async def exploding_judge(task, result):
        raise RuntimeError("judge runner unreachable")

    orch_kp = KeyPair.generate()
    provenance = ProvenanceLog()
    executor = TaskExecutor(
        Router({Tier.SMALL: ScriptedWorker("w", lambda t: "some output")}),
        provenance=provenance,
        orchestrator_keypair=orch_kp,
        judge=exploding_judge,
    )
    outcome = await executor.execute(Task(objective="do a thing"))

    assert outcome.final_verdict is Verdict.UNVERIFIED  # judge break ≠ task fail
    errors = [e for e in provenance.entries() if e.action == "judge.error"]
    assert len(errors) == 1
    assert "judge runner unreachable" in errors[0].detail["error"]
