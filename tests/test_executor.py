"""TaskExecutor tests: escalation on verified failure, authenticity rejection,
budget ceilings, plateau stop, capability matrix learning, provenance trail."""
from __future__ import annotations

from metaharness.core import Budget, Task, TaskExecutor, TaskType, Tier, Verdict
from metaharness.harness import MockLLMWorker, ScriptedWorker
from metaharness.identity import KeyPair, ProvenanceLog, WorkerRegistry, registration_payload
from metaharness.routing import CapabilityMatrix, Router


def register(registry: WorkerRegistry, worker_id: str, kp: KeyPair):
    challenge = registry.begin_registration(worker_id)
    payload = registration_payload(worker_id, kp.public_b64(), challenge.nonce)
    registry.complete_registration(worker_id, kp.public_b64(), kp.sign(payload))


def classify_task(answer: str = "positive", **kw) -> Task:
    return Task(
        task_type=TaskType.CLASSIFY,
        objective="Classify sentiment",
        inputs={"text": "great", "labels": ["positive", "negative"]},
        success_check={"equals": answer},
        **kw,
    )


async def test_pass_on_first_attempt():
    router = Router({Tier.SMALL: MockLLMWorker("w", Tier.SMALL, seed=1,
                                               skills={TaskType.CLASSIFY: 1.0})})
    executor = TaskExecutor(router)
    outcome = await executor.execute(classify_task())
    assert outcome.final_verdict == Verdict.PASS
    assert len(outcome.attempts) == 1 and outcome.escalations == 0
    assert outcome.final_output == "positive"


async def test_escalates_on_verified_failure():
    always_wrong = MockLLMWorker("w-small", Tier.SMALL, seed=1,
                                 skills={TaskType.CLASSIFY: 0.0})
    always_right = MockLLMWorker("w-front", Tier.FRONTIER, seed=2,
                                 skills={TaskType.CLASSIFY: 1.0})
    router = Router({Tier.SMALL: always_wrong, Tier.FRONTIER: always_right})
    executor = TaskExecutor(router)
    outcome = await executor.execute(classify_task())
    assert outcome.final_verdict == Verdict.PASS
    assert outcome.escalations >= 1
    tiers = [a.result.tier for a in outcome.attempts]
    assert tiers[0] == Tier.SMALL and tiers[-1] == Tier.FRONTIER


async def test_matrix_learns_from_outcomes():
    matrix = CapabilityMatrix()
    router = Router(
        {Tier.SMALL: MockLLMWorker("w", Tier.SMALL, seed=3,
                                   skills={TaskType.CLASSIFY: 0.0}),
         Tier.MID: MockLLMWorker("w2", Tier.MID, seed=4,
                                 skills={TaskType.CLASSIFY: 1.0})},
        matrix=matrix,
    )
    executor = TaskExecutor(router)
    await executor.execute(classify_task())
    assert matrix.samples("mock-small", TaskType.CLASSIFY) >= 1
    assert matrix.samples("mock-mid", TaskType.CLASSIFY) >= 1


async def test_authenticity_unsigned_result_rejected():
    """A worker whose results aren't signed under a registered key never passes,
    even when its answers are correct."""
    registry = WorkerRegistry()
    kp_registered = KeyPair.generate()
    register(registry, "w-signed", kp_registered)
    # runner presents worker_id "w-signed" but has no key: results are unsigned
    unsigned = ScriptedWorker("w-signed", lambda t: "positive")
    router = Router({Tier.SMALL: unsigned})
    executor = TaskExecutor(router, registry=registry)
    outcome = await executor.execute(classify_task(max_attempts=2))
    assert outcome.final_verdict == Verdict.FAIL
    assert all(a.verification.scorer == "authenticity" for a in outcome.attempts)


async def test_authenticity_wrong_key_rejected():
    registry = WorkerRegistry()
    register(registry, "w1", KeyPair.generate())      # registered key
    impostor_key = KeyPair.generate()                  # different key signs results
    runner = ScriptedWorker("w1", lambda t: "positive", keypair=impostor_key)
    executor = TaskExecutor(Router({Tier.SMALL: runner}), registry=registry)
    outcome = await executor.execute(classify_task(max_attempts=1))
    assert outcome.final_verdict == Verdict.FAIL
    assert outcome.attempts[0].verification.scorer == "authenticity"


async def test_signed_registered_worker_passes_authenticity():
    registry = WorkerRegistry()
    kp = KeyPair.generate()
    register(registry, "w1", kp)
    runner = MockLLMWorker("w1", Tier.SMALL, keypair=kp, seed=1,
                           skills={TaskType.CLASSIFY: 1.0})
    executor = TaskExecutor(Router({Tier.SMALL: runner}), registry=registry)
    outcome = await executor.execute(classify_task())
    assert outcome.final_verdict == Verdict.PASS


async def test_budget_hard_stop():
    runner = MockLLMWorker("w", Tier.SMALL, seed=1, skills={TaskType.CLASSIFY: 0.0})
    budget = Budget(max_tokens=50)  # one attempt blows through this
    executor = TaskExecutor(Router({Tier.SMALL: runner}), budget=budget)
    outcome = await executor.execute(classify_task(max_attempts=5))
    assert outcome.final_verdict != Verdict.PASS
    assert len(outcome.attempts) < 5
    assert outcome.attempts[-1].verification.failure_mode is not None
    assert outcome.attempts[-1].verification.scorer == "budget"


async def test_unverified_stops_iteration():
    """No checkable signal → one attempt, UNVERIFIED; iterating would be vibes."""
    runner = ScriptedWorker("w", lambda t: {"essay": "..."})
    executor = TaskExecutor(Router({Tier.SMALL: runner}))
    task = Task(task_type=TaskType.SUMMARIZE, objective="write", max_attempts=5)
    outcome = await executor.execute(task)
    assert outcome.final_verdict == Verdict.UNVERIFIED
    assert len(outcome.attempts) == 1


async def test_reflection_feeds_next_attempt():
    seen_boundaries: list[list[str]] = []

    def handler(task: Task):
        seen_boundaries.append(list(task.boundaries))
        return "negative"  # always wrong

    runner = ScriptedWorker("w", handler)
    executor = TaskExecutor(
        Router({Tier.SMALL: runner}),
        reflector=lambda task, attempt: f"attempt {attempt.n} returned {attempt.result.output!r}; that is wrong",
    )
    await executor.execute(classify_task(max_attempts=3))
    assert seen_boundaries[0] == []
    assert any("wrong" in b for b in seen_boundaries[1])
    # repetition notice also appears once the same wrong answer recurs
    assert any("different approach" in b.lower() for b in seen_boundaries[2])


async def test_provenance_trail_written_and_verifiable():
    orch_kp = KeyPair.generate()
    registry = WorkerRegistry()
    register(registry, "orchestrator", orch_kp)
    provenance = ProvenanceLog()
    runner = MockLLMWorker("w", Tier.SMALL, seed=1, skills={TaskType.CLASSIFY: 1.0})
    executor = TaskExecutor(
        Router({Tier.SMALL: runner}),
        provenance=provenance,
        orchestrator_keypair=orch_kp,
    )
    await executor.execute(classify_task())
    kinds = [e.action for e in provenance.entries()]
    assert kinds[0] == "task.started" and kinds[-1] == "task.finished"
    assert "task.attempt" in kinds
    check = provenance.verify_chain(
        lambda wid: registry.get(wid).public_key_b64 if registry.get(wid) else None
    )
    assert check.ok, check.reason


async def test_observer_receives_every_outcome():
    """Regression (2026-07-08): LearningLoop.observe was never wired into the
    server path, so WebUI failure clusters stayed empty forever. The executor
    now notifies an observer for each finished task."""
    from metaharness.correction import LearningLoop, Playbook
    from metaharness.core.types import MASTMode

    loop = LearningLoop(Playbook())
    always_wrong = MockLLMWorker("w", Tier.SMALL, seed=1,
                                 skills={TaskType.CLASSIFY: 0.0})
    executor = TaskExecutor(Router({Tier.SMALL: always_wrong}),
                            playbook_hints=loop.hints_for, observer=loop.observe)
    await executor.execute(classify_task(max_attempts=2))
    assert loop.stats.count("classify", MASTMode.DISOBEY_TASK_SPEC) >= 1


async def test_broken_observer_never_fails_the_task():
    def bad_observer(outcome):
        raise RuntimeError("observer exploded")

    runner = MockLLMWorker("w", Tier.SMALL, seed=1, skills={TaskType.CLASSIFY: 1.0})
    executor = TaskExecutor(Router({Tier.SMALL: runner}), observer=bad_observer)
    outcome = await executor.execute(classify_task())
    assert outcome.final_verdict == Verdict.PASS
