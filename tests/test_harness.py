"""Harness layer tests: runner contract, mock workers, sandbox, enrichment stack.

The statistical tests use fixed seeds, so they are deterministic — but the
assertions are about *relative* lift (offload beats direct compute; k=5 voting
beats k=1) rather than magic counts, which is the actual claim being tested.
"""
from __future__ import annotations

import pytest

from metaharness.core.types import Task, TaskType, Tier
from metaharness.harness import (
    MockLLMWorker,
    SandboxError,
    SchemaGuard,
    ScriptedWorker,
    SelfConsistency,
    ToolOffload,
    check_schema,
    eval_arithmetic,
    verify_result,
)
from metaharness.identity import KeyPair, WorkerRegistry, registration_payload


def arithmetic_task(expression: str, answer: float) -> Task:
    return Task(
        task_type=TaskType.ARITHMETIC,
        objective=f"Compute {expression}",
        inputs={"expression": expression},
        success_check={"equals": answer},
    )


def classify_task(text: str, labels: list[str], answer: str) -> Task:
    return Task(
        task_type=TaskType.CLASSIFY,
        objective=f"Classify: {text}",
        inputs={"text": text, "labels": labels},
        success_check={"equals": answer},
    )


# -- sandbox ---------------------------------------------------------------------


def test_eval_arithmetic_correct():
    assert eval_arithmetic("2+3*4") == 14
    assert eval_arithmetic("(17 - 5) / 4") == 3.0
    assert eval_arithmetic("2**10 % 7") == 2
    assert eval_arithmetic("-3 + +5") == 2


@pytest.mark.parametrize(
    "bad",
    [
        "__import__('os').system('true')",
        "open('/etc/hosts')",
        "x + 1",
        "[1,2][0]",
        "'a' * 3",
        "(lambda: 1)()",
        "1 if True else 2",
        "9**9**9",
    ],
)
def test_eval_arithmetic_rejects_non_arithmetic(bad):
    with pytest.raises(SandboxError):
        eval_arithmetic(bad)


# -- mock worker -----------------------------------------------------------------


async def test_high_skill_worker_answers_correctly():
    worker = MockLLMWorker("w1", Tier.FRONTIER, seed=1)
    task = classify_task("great product", ["positive", "negative"], "positive")
    hits = 0
    for _ in range(20):
        result = await worker.run(task)
        hits += result.output == "positive"
    assert hits >= 18  # frontier classify skill is 0.99


async def test_low_skill_worker_is_unreliable_but_not_useless():
    worker = MockLLMWorker("w1", Tier.SMALL, seed=7)
    hits = 0
    for i in range(30):
        result = await worker.run(arithmetic_task(f"{i}+{i+1}", 2 * i + 1))
        hits += result.output == 2 * i + 1
    assert 5 < hits < 30  # small-tier arithmetic skill is 0.55


async def test_worker_result_carries_cost_tokens_and_signature():
    kp = KeyPair.generate()
    worker = MockLLMWorker("w1", Tier.MID, keypair=kp, seed=3)
    result = await worker.run(classify_task("x", ["a", "b"], "a"))
    assert result.tokens_in > 0 and result.tokens_out > 0 and result.cost_usd > 0
    assert result.latency_s >= 0
    assert result.signature_b64

    registry = WorkerRegistry()
    challenge = registry.begin_registration("w1")
    payload = registration_payload("w1", kp.public_b64(), challenge.nonce)
    registry.complete_registration("w1", kp.public_b64(), kp.sign(payload))
    assert verify_result(result, registry)
    result.output = "b"  # altered after signing
    assert not verify_result(result, registry)


async def test_scripted_worker_and_error_capture():
    ok = ScriptedWorker("s1", lambda task: {"echo": task.inputs["x"]})
    result = await ok.run(Task(inputs={"x": 42}))
    assert result.output == {"echo": 42} and result.error is None

    def boom(task):
        raise RuntimeError("worker fell over")

    bad = ScriptedWorker("s2", boom)
    result = await bad.run(Task())
    assert result.error and "worker fell over" in result.error


# -- enrichment: tool offload ------------------------------------------------------


async def test_tool_offload_lifts_small_model_arithmetic():
    direct = MockLLMWorker("w1", Tier.SMALL, seed=11)
    offloaded = ToolOffload(MockLLMWorker("w1", Tier.SMALL, seed=11))
    tasks = [arithmetic_task(f"{i} * 7 + {i+2}", i * 7 + i + 2) for i in range(30)]
    direct_hits = sum([(await direct.run(t)).output == t.success_check["equals"] for t in tasks])
    offload_hits = sum(
        [(await offloaded.run(t)).output == t.success_check["equals"] for t in tasks]
    )
    assert offload_hits > direct_hits
    assert offload_hits >= 24  # transcription skill 0.55+0.35=0.90


async def test_tool_offload_records_tool_call_and_resigns():
    kp = KeyPair.generate()
    worker = ToolOffload(MockLLMWorker("w1", Tier.FRONTIER, keypair=kp, seed=2))
    result = await worker.run(arithmetic_task("6*7", 42))
    assert result.output == 42
    assert result.tool_calls and result.tool_calls[0]["tool"] == "python.eval_arithmetic"

    registry = WorkerRegistry()
    challenge = registry.begin_registration("w1")
    payload = registration_payload("w1", kp.public_b64(), challenge.nonce)
    registry.complete_registration("w1", kp.public_b64(), kp.sign(payload))
    assert verify_result(result, registry)


async def test_tool_offload_passthrough_for_non_arithmetic():
    worker = ToolOffload(MockLLMWorker("w1", Tier.FRONTIER, seed=2))
    task = classify_task("x", ["a", "b"], "a")
    result = await worker.run(task)
    assert not result.tool_calls


async def test_tool_offload_sandbox_error_becomes_result_error():
    bad_program = ScriptedWorker("s1", lambda task: {"program": "__import__('os')"})
    worker = ToolOffload(bad_program)
    result = await worker.run(arithmetic_task("1+1", 2))
    assert result.error and "tool_offload" in result.error


# -- enrichment: self-consistency ---------------------------------------------------


async def test_self_consistency_beats_single_sample():
    single = MockLLMWorker("w1", Tier.MID, seed=5)
    voted = SelfConsistency(MockLLMWorker("w1", Tier.MID, seed=5), k=5)
    tasks = [arithmetic_task(f"{i}+{i}", 2 * i) for i in range(25)]
    single_hits = sum([(await single.run(t)).output == t.success_check["equals"] for t in tasks])
    voted_hits = sum([(await voted.run(t)).output == t.success_check["equals"] for t in tasks])
    assert voted_hits > single_hits


async def test_self_consistency_aggregates_cost():
    voted = SelfConsistency(MockLLMWorker("w1", Tier.SMALL, seed=5), k=3)
    single = MockLLMWorker("w1", Tier.SMALL, seed=5)
    task = classify_task("x", ["a", "b"], "a")
    r_single = await single.run(task)
    r_voted = await voted.run(task)
    assert r_voted.cost_usd > r_single.cost_usd * 2  # ~3x samples ~3x cost


# -- enrichment: schema guard --------------------------------------------------------


def test_check_schema_reports_violations():
    schema = {
        "type": "object",
        "required": ["label", "confidence"],
        "properties": {"label": {"type": "string"}, "confidence": {"type": "number"}},
    }
    assert check_schema({"label": "a", "confidence": 0.9}, schema) == []
    assert "missing required key 'confidence'" in check_schema({"label": "a"}, schema)[0]
    problems = check_schema({"label": 3, "confidence": True}, schema)
    assert len(problems) == 2
    assert check_schema("not a dict", schema)


async def test_schema_guard_retry_then_error():
    calls = {"n": 0}

    def flaky(task):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"label": "a"}  # missing confidence
        return {"label": "a", "confidence": 0.9}

    schema = {
        "type": "object",
        "required": ["label", "confidence"],
        "properties": {"label": {"type": "string"}, "confidence": {"type": "number"}},
    }
    guard = SchemaGuard(ScriptedWorker("s1", flaky))
    task = Task(objective="classify", output_schema=schema)
    result = await guard.run(task)
    assert result.error is None and calls["n"] == 2
    assert result.output["confidence"] == 0.9

    always_bad = SchemaGuard(ScriptedWorker("s2", lambda t: {"nope": 1}))
    result = await always_bad.run(task)
    assert result.error and result.error.startswith("schema:")


# -- composition ---------------------------------------------------------------------


async def test_enrichment_stack_composes():
    kp = KeyPair.generate()
    stack = SelfConsistency(ToolOffload(MockLLMWorker("w1", Tier.SMALL, keypair=kp, seed=9)), k=3)
    task = arithmetic_task("12*12 - 44", 100)
    result = await stack.run(task)
    assert result.output == 100
    assert result.worker_id == "w1" and stack.tier == Tier.SMALL


# -- enrichment: self-critique -------------------------------------------------------


async def test_self_critique_runs_draft_critique_revise():
    from metaharness.harness import SelfCritique

    calls = []

    def handler(task: Task):
        calls.append(task)
        if len(calls) == 1:
            return "draft plan v1"
        if len(calls) == 2:
            return "- missing deployment step\n- no testing phase"
        return "revised plan v2 with deployment and testing"

    kp = KeyPair.generate()
    worker = SelfCritique(ScriptedWorker("w1", handler, keypair=kp), rounds=1)
    task = Task(task_type=TaskType.PLANNING, objective="plan a web app")
    result = await worker.run(task)

    assert len(calls) == 3
    # critique saw the draft as an external artifact
    assert calls[1].inputs["draft"] == "draft plan v1"
    assert "NO_ISSUES" in calls[1].objective
    # reviser got draft + critique as inputs with external framing
    assert calls[2].inputs["previous_draft"] == "draft plan v1"
    assert "missing deployment" in calls[2].inputs["reviewer_critique"]
    assert any("reviewer" in b for b in calls[2].boundaries)
    assert result.output == "revised plan v2 with deployment and testing"
    assert result.task_id == task.id
    assert result.signature_b64  # re-signed after rewrite


async def test_self_critique_early_exit_on_no_issues():
    from metaharness.harness import SelfCritique

    calls = []

    def handler(task: Task):
        calls.append(task)
        return "perfect draft" if len(calls) == 1 else "NO_ISSUES"

    worker = SelfCritique(ScriptedWorker("w1", handler), rounds=3)
    result = await worker.run(Task(task_type=TaskType.REASONING, objective="reason"))
    assert len(calls) == 2  # draft + one critique, no revisions
    assert result.output == "perfect draft"


async def test_self_critique_skips_checkable_tasks():
    from metaharness.harness import SelfCritique

    calls = []
    worker = SelfCritique(ScriptedWorker("w1", lambda t: (calls.append(t) or "a")))
    await worker.run(Task(task_type=TaskType.CLASSIFY, objective="x",
                          success_check={"equals": "a"}))
    await worker.run(Task(task_type=TaskType.PLANNING, objective="x",
                          output_schema={"required": ["steps"]}))
    assert len(calls) == 2  # one inner call each, no critique traffic


async def test_self_critique_aggregates_cost():
    from metaharness.harness import SelfCritique

    n = {"i": 0}

    def handler(task: Task):
        n["i"] += 1
        return {1: "draft", 2: "- vague"}.get(n["i"], "revised")

    worker = SelfCritique(ScriptedWorker("w1", handler))
    result = await worker.run(Task(task_type=TaskType.GENERAL, objective="do a thing"))
    # tokens from draft + critique + revision all counted
    single = await ScriptedWorker("w2", lambda t: "revised").run(
        Task(task_type=TaskType.GENERAL, objective="do a thing"))
    assert result.tokens_out > single.tokens_out
