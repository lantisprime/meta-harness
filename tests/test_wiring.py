"""Wiring sweep: every cross-component connection, exercised through the fully
wired HarnessState object graph — the same graph `metaharness serve` builds.

Motivation (2026-07-08): two gaps shipped because unit tests exercised components
in isolation with typed mock outputs: (1) LearningLoop.observe was never wired
into the server path, so failure clusters stayed empty forever; (2) the verifier
compared a worker's text answer "3767" to the number 3767 and failed all tiers.
These tests drive the wired graph with TEXT-answering workers (like real LLMs)
and assert on every downstream surface a run is supposed to touch.
"""
from __future__ import annotations

import httpx
import pytest

from metaharness.core.budget import Budget
from metaharness.core.types import Task, TaskType, Tier
from metaharness.harness import ScriptedWorker
from metaharness.identity import KeyPair
from metaharness.web import HarnessState, create_app
from metaharness.workflows import RunStatus, load_workflow

CHECKABLE_YAML = """
name: wiring-sweep
steps:
  - id: sentiment
    task_type: classify
    objective: Classify the sentiment.
    inputs: {review: "$context.review"}
    success_check: {one_of: [positive, negative]}
  - id: impact
    task_type: arithmetic
    objective: Compute the number.
    inputs: {expression: "1250 * 3 + 17"}
    success_check: {equals: 3767}
  - id: hopeless
    task_type: extract
    objective: Extract the fields.
    depends_on: [sentiment, impact]
    success_check: {equals: "unreachable"}
    max_attempts: 2
"""


def text_llm_worker(worker_id: str, keypair: KeyPair, tier: Tier) -> ScriptedWorker:
    """Behaves like a real LLM harness: always answers in TEXT."""

    def handler(task: Task) -> str:
        if task.task_type == TaskType.CLASSIFY:
            return "positive"
        if task.task_type == TaskType.ARITHMETIC:
            return "3767"        # text, like a real completion
        return "some wrong text"  # never matches the 'hopeless' check

    return ScriptedWorker(worker_id, handler, tier=tier, model=f"text-{tier.value}", keypair=keypair)


@pytest.fixture
def wired(tmp_path) -> HarnessState:
    state = HarnessState(budget=Budget(max_cost_usd=10.0))
    runners = {}
    for tier in (Tier.SMALL, Tier.FRONTIER):
        kp = KeyPair.generate()
        runner = text_llm_worker(f"w-{tier.value}", kp, tier)
        state.register_worker(runner, kp, tiers=[tier.value])
        runners[tier] = runner
    state.wire(runners, journal_dir=tmp_path)
    return state


async def test_full_graph_every_surface_updates(wired, tmp_path):
    state = wired
    spec = load_workflow(CHECKABLE_YAML)
    run = state.engine.start(spec, context={"review": "loved it"})
    run = await state.engine.advance(run.run_id)

    # 1. engine: checkable steps passed with TEXT answers; hopeless step failed the run
    assert run.completed["sentiment"].verdict.value == "pass"
    assert run.completed["impact"].verdict.value == "pass"      # "3767" == 3767
    assert run.status == RunStatus.FAILED and run.failed_step == "hopeless"

    # 2. router matrix: verified outcomes recorded per model
    matrix = state.matrix.as_dict()
    assert matrix["text-small"]["classify"]["samples"] >= 1
    assert matrix["text-small"]["arithmetic"]["pass_rate"] == 1.0
    # escalation reached the frontier worker on the failing step
    assert "text-frontier" in matrix

    # 3. learning loop: failures clustered (this was the missing wire)
    clusters = state.learning.stats.as_dict()
    assert clusters.get("extract"), f"failure clusters empty: {clusters}"

    # 4. provenance: chain intact and carries task lifecycle under orchestrator key
    check = state.provenance.verify_chain(
        lambda wid: (r.public_key_b64 if (r := state.registry.get(wid)) else None))
    assert check.ok, check.reason
    actions = [e.action for e in state.provenance.entries()]
    assert "task.started" in actions and "task.finished" in actions

    # 5. budget: charged (scripted workers cost 0 usd but the wire must not crash;
    #    charge path executed means spent_tokens accumulated)
    assert state.budget.spent_tokens >= 0

    # 6. journal: durable file exists and replays to the same terminal state
    journal_path = tmp_path / f"{run.run_id}.jsonl"
    assert journal_path.exists()
    from metaharness.workflows import WorkflowEngine
    _, resumed = WorkflowEngine.resume(journal_path, state.executor)
    assert resumed.status == RunStatus.FAILED
    assert resumed.completed["sentiment"].output == "positive"


async def test_playbook_credit_flows_through_wired_graph(wired):
    """Bullets injected by the wired playbook_hints get credited on outcomes."""
    state = wired
    bullet = state.playbook.add("Answer with one word.", task_type=TaskType.CLASSIFY)
    task = Task(task_type=TaskType.CLASSIFY, objective="classify",
                inputs={}, success_check={"one_of": ["positive", "negative"]})
    outcome = await state.executor.execute(task)
    assert outcome.final_verdict.value == "pass"
    assert state.playbook.get(bullet.id).helpful == 1


async def test_http_surface_reflects_same_state(wired):
    """The API serves the exact objects the run updated — no parallel state."""
    app = create_app(wired)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://t") as client:
        await client.post("/api/runs", json={"workflow_yaml": CHECKABLE_YAML,
                                             "context": {"review": "x"}})
        matrix = (await client.get("/api/matrix")).json()
        assert matrix["text-small"]["classify"]["samples"] >= 1
        failures = (await client.get("/api/failures")).json()
        assert failures.get("extract")
        prov = (await client.get("/api/provenance")).json()
        assert prov["chain"]["ok"] and prov["total"] > 0
        spans = (await client.get("/api/spans")).json()
        assert any(s["name"] == "task.execute" for s in spans)
        runs = (await client.get("/api/runs")).json()
        assert runs and runs[-1]["failed_step"] == "hopeless"


async def test_slow_loop_runs_and_persists_through_wired_graph(tmp_path):
    """Regression (2026-07-08 audit): curate() was never called in the live
    path and the playbook was never saved — the harness could observe failures
    forever without ever writing a lesson down, and forgot everything on
    restart."""
    playbook_path = tmp_path / "playbook.json"
    state = HarnessState()
    kp = KeyPair.generate()
    wrong = ScriptedWorker("w", lambda t: "wrong text", tier=Tier.SMALL,
                           model="text-small", keypair=kp)
    state.register_worker(wrong, kp, tiers=["small"])
    state.wire({Tier.SMALL: wrong}, journal_dir=tmp_path)
    state.enable_playbook_persistence(playbook_path)

    for i in range(3):  # three verified failures -> cluster crosses min_cluster
        task = Task(task_type=TaskType.EXTRACT, objective=f"extract #{i}",
                    success_check={"equals": "never"}, max_attempts=1)
        await state.executor.execute(task)

    # slow loop fired by itself: cluster became a bullet, no manual curate()
    bullets = state.playbook.bullets()
    assert bullets, "auto-curation did not run"
    assert bullets[0].origin.startswith("curation:disobey_task_spec")
    assert state.learning.last_deltas

    # and it survives a restart
    assert playbook_path.exists()
    state2 = HarnessState()
    state2.wire({Tier.SMALL: wrong})
    state2.enable_playbook_persistence(playbook_path)
    restored = state2.playbook.bullets()
    assert len(restored) == len(bullets)
    assert restored[0].text == bullets[0].text
    # and the restored bullets flow into new tasks as hints
    hints = state2.learning.hints_for(Task(task_type=TaskType.EXTRACT, objective="x"))
    assert any("verified against a precise expected result" in h for h in hints)


async def test_all_learned_state_survives_restart(tmp_path):
    """User request (2026-07-08): persist the details — matrix samples and
    failure tallies must survive restarts, not just the playbook."""
    def build(persist_dir):
        state = HarnessState()
        kp = KeyPair.generate()
        wrong = ScriptedWorker("w", lambda t: "wrong", tier=Tier.SMALL,
                               model="text-small", keypair=kp)
        state.register_worker(wrong, kp, tiers=["small"])
        state.wire({Tier.SMALL: wrong}, journal_dir=tmp_path / "journals")
        state.enable_persistence(persist_dir)
        return state

    persist_dir = tmp_path / "learn"
    state = build(persist_dir)
    for i in range(2):
        await state.executor.execute(Task(
            task_type=TaskType.EXTRACT, objective=f"e{i}",
            success_check={"equals": "never"}, max_attempts=1))
    assert state.matrix.samples("text-small", TaskType.EXTRACT) == 2
    assert state.learning.stats.count("extract", __import__("metaharness.core.types", fromlist=["MASTMode"]).MASTMode.DISOBEY_TASK_SPEC) == 2

    # "restart": a brand-new state loads everything back
    state2 = build(persist_dir)
    assert state2.matrix.samples("text-small", TaskType.EXTRACT) == 2
    assert state2.matrix.pass_rate("text-small", TaskType.EXTRACT, prior=0.5) < 0.5
    assert state2.learning.stats.as_dict()["extract"]["disobey_task_spec"] == 2
    # and new observations continue accumulating on top of the restored counts
    await state2.executor.execute(Task(
        task_type=TaskType.EXTRACT, objective="e9",
        success_check={"equals": "never"}, max_attempts=1))
    assert state2.matrix.samples("text-small", TaskType.EXTRACT) == 3
