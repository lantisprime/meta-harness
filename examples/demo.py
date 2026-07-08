"""End-to-end demo: every layer of the meta-harness exercised in one run.

    .venv/bin/python examples/demo.py            # run the full demo
    .venv/bin/python examples/demo.py --serve    # ...then serve the WebUI

Acts:
 1. Identity      — challenge-response registration of three workers + orchestrator
 2. Enrichment    — PAL tool-offload lifts the small tier on arithmetic
 3. Routing       — cheapest-capable routing; verified failure escalates the tier
 4. Workflow      — durable journaled run with a HITL gate, resumed from disk
 5. Learning      — failure clusters curate the playbook (delta updates)
 6. Eval gate     — pass^k paired comparison, go/no-go verdict
 7. Provenance    — the whole session's action chain verifies end to end
"""
from __future__ import annotations

import argparse
import asyncio
import tempfile
from pathlib import Path

from metaharness.core.budget import Budget
from metaharness.core.types import Task, TaskType, Tier, Verdict
from metaharness.evals import compare_suites, run_suite
from metaharness.harness import MockLLMWorker, SchemaGuard, SelfConsistency, ToolOffload
from metaharness.identity import KeyPair
from metaharness.routing import CapabilityMatrix
from metaharness.web import HarnessState, create_app
from metaharness.workflows import RunStatus, WorkflowEngine, load_workflow

RULE = "─" * 72

TRIAGE_YAML = """
name: incident-triage
steps:
  - id: classify
    task_type: classify
    objective: Classify the incident severity from the report.
    inputs: {text: "$context.report", labels: [low, high]}
    success_check: {equals: high}
  - id: impact
    task_type: arithmetic
    objective: Estimate affected user-hours (users x hours).
    depends_on: [classify]
    inputs: {expression: "1250 * 3"}
    success_check: {equals: 3750}
  - id: summarize
    task_type: summarize
    objective: Summarize for the on-call engineer.
    depends_on: [classify, impact]
    inputs: {severity: "$steps.classify.output", user_hours: "$steps.impact.output"}
    success_check: {contains: summary}
  - id: page
    task_type: transform
    objective: Draft the page to send to the on-call engineer.
    depends_on: [summarize]
    hitl: true
    success_check: {contains: page}
"""


def act(n: int, title: str) -> None:
    print(f"\n{RULE}\nACT {n} — {title}\n{RULE}")


async def main(serve: bool) -> None:
    journal_dir = Path(tempfile.mkdtemp(prefix="metaharness-demo-"))
    state = HarnessState(budget=Budget(max_cost_usd=5.0))

    # ---- Act 1: identity ------------------------------------------------------
    act(1, "Identity: signed challenge-response registration")
    keys = {tier: KeyPair.generate() for tier in Tier}
    runners = {
        Tier.SMALL: SelfConsistency(
            ToolOffload(MockLLMWorker("w-small", Tier.SMALL, keypair=keys[Tier.SMALL], seed=7)),
            k=3,
        ),
        Tier.MID: MockLLMWorker("w-mid", Tier.MID, keypair=keys[Tier.MID], seed=8),
        Tier.FRONTIER: MockLLMWorker("w-frontier", Tier.FRONTIER, keypair=keys[Tier.FRONTIER], seed=9),
    }
    for tier, runner in runners.items():
        state.register_worker(runner, keys[tier], tiers=[tier.value])
        record = state.registry.get(runner.worker_id)
        print(f"  admitted {runner.worker_id:12s} key {record.public_key_b64[:20]}…")
    state.wire(runners, journal_dir=journal_dir)

    # an impostor with an unregistered key is rejected at registration
    impostor, real_key = KeyPair.generate(), KeyPair.generate()
    challenge = state.registry.begin_registration("w-impostor")
    from metaharness.identity import RegistryError, registration_payload
    payload = registration_payload("w-impostor", real_key.public_b64(), challenge.nonce)
    try:
        state.registry.complete_registration("w-impostor", real_key.public_b64(), impostor.sign(payload))
    except RegistryError as exc:
        print(f"  impostor rejected: {exc}")

    # ---- Act 2: enrichment ----------------------------------------------------
    act(2, "Enrichment: PAL tool-offload on the small tier")
    tasks = [
        Task(task_type=TaskType.ARITHMETIC, objective=f"compute {i}*17+{i}",
             inputs={"expression": f"{i}*17+{i}"}, success_check={"equals": i * 17 + i})
        for i in range(12)
    ]
    direct = MockLLMWorker("bare-small", Tier.SMALL, seed=7)
    direct_hits = sum([
        (await direct.run(t)).output == t.success_check["equals"] for t in tasks
    ])
    enriched_hits = 0
    for t in tasks:
        result = await runners[Tier.SMALL].run(t)
        enriched_hits += result.output == t.success_check["equals"]
    print(f"  bare small model:      {direct_hits}/12 correct")
    print(f"  offload+consistency:   {enriched_hits}/12 correct")

    # ---- Act 3: routing + escalation -----------------------------------------
    act(3, "Routing: cheapest capable tier; verified failure escalates")
    easy = Task(task_type=TaskType.CLASSIFY, objective="easy classify",
                inputs={"labels": ["a", "b"]}, success_check={"equals": "a"})
    hard = Task(task_type=TaskType.PLANNING, objective="multi-step plan",
                success_check={"equals": "plan-v2"}, max_attempts=4)
    for name, task in [("easy/classify", easy), ("hard/planning", hard)]:
        outcome = await state.executor.execute(task)
        path = " → ".join(a.result.tier.value for a in outcome.attempts)
        print(f"  {name:14s} verdict={outcome.final_verdict.value:10s} tiers: {path} "
              f"(escalations={outcome.escalations}, cost=${outcome.total_cost_usd:.4f})")
        state.learning.observe(outcome)

    # a degraded small tier: correct answers require climbing the cascade
    from metaharness.core.executor import TaskExecutor
    from metaharness.routing import Router
    degraded = Router({
        Tier.SMALL: MockLLMWorker("deg-small", Tier.SMALL, seed=3,
                                  skills={TaskType.CLASSIFY: 0.0}),
        Tier.FRONTIER: MockLLMWorker("deg-front", Tier.FRONTIER, seed=4,
                                     skills={TaskType.CLASSIFY: 1.0}),
    })
    outcome = await TaskExecutor(degraded).execute(easy.model_copy(deep=True))
    path = " → ".join(a.result.tier.value for a in outcome.attempts)
    print(f"  degraded-small verdict={outcome.final_verdict.value:10s} tiers: {path} "
          f"(escalations={outcome.escalations}) — failure was verified, so the router climbed")

    # ---- Act 4: durable workflow with HITL, resumed from disk ------------------
    act(4, "Workflow: journaled run, HITL gate, crash, resume from journal")
    spec = load_workflow(TRIAGE_YAML)
    run_state = state.engine.start(spec, context={"report": "db-1 disk full, checkout failing"})
    run_state = await state.engine.advance(run_state.run_id)
    print(f"  run {run_state.run_id}: {run_state.status.value} at gate {run_state.awaiting!r}")
    print(f"  completed steps: {list(run_state.completed)}")
    journal_path = journal_dir / f"{run_state.run_id}.jsonl"

    # simulate a crash: rebuild the engine purely from the journal file
    engine2, resumed = WorkflowEngine.resume(journal_path, state.executor)
    print(f"  resumed from {journal_path.name}: {len(resumed.completed)} steps intact")
    engine2.approve(resumed.run_id, "page")
    final = await engine2.advance(resumed.run_id)
    print(f"  after approval: {final.status.value}"
          f" — page step verdict {final.completed['page'].verdict.value}")
    assert final.status == RunStatus.COMPLETED
    state.engine._runs[resumed.run_id] = engine2._runs[resumed.run_id]  # show in UI

    # ---- Act 5: learning loop ---------------------------------------------------
    act(5, "Learning: failure clusters curate the playbook (deltas only)")
    schema = {"type": "object", "required": ["label", "confidence"],
              "properties": {"label": {"type": "string"}, "confidence": {"type": "number"}}}
    sloppy = MockLLMWorker("w-mid", Tier.MID, keypair=keys[Tier.MID], seed=8,
                           skills={TaskType.EXTRACT: 0.0})
    sloppy_executor = TaskExecutor(
        Router({Tier.MID: sloppy}, matrix=state.matrix),
        registry=state.registry, provenance=state.provenance,
        orchestrator_keypair=state.orchestrator_keypair,
        playbook_hints=state.learning.hints_for,
    )
    for i in range(3):
        task = Task(task_type=TaskType.EXTRACT, objective=f"extract fields #{i}",
                    output_schema=schema, success_check={"equals": {"label": "x", "confidence": 1.0}},
                    max_attempts=1)
        outcome = await sloppy_executor.execute(task)
        state.learning.observe(outcome)
    deltas = state.learning.curate()
    for d in deltas:
        print(f"  Δ {d}")
    print(f"  playbook now has {len(state.playbook.bullets())} active bullet(s); "
          f"failure clusters: {state.learning.stats.as_dict()}")

    # ---- Act 6: eval gate --------------------------------------------------------
    act(6, "Eval gate: pass^3 paired comparison, go/no-go")
    eval_tasks = [
        Task(task_type=TaskType.CLASSIFY, objective=f"classify #{i}",
             inputs={"labels": ["a", "b"]}, success_check={"equals": "a"})
        for i in range(6)
    ] + [
        Task(task_type=TaskType.REASONING, objective=f"reason #{i}",
             success_check={"equals": f"answer-{i}"})
        for i in range(6)
    ]
    incumbent = await run_suite(
        MockLLMWorker("inc", Tier.FRONTIER, model="incumbent-frontier", seed=1),
        eval_tasks, k=3, matrix=state.matrix)
    candidate = await run_suite(
        MockLLMWorker("cand", Tier.MID, model="candidate-mid", seed=2),
        eval_tasks, k=3, matrix=state.matrix)
    report = compare_suites(incumbent, candidate)
    print(f"  overall pass^3: incumbent {report.overall_incumbent:.2f} "
          f"vs candidate {report.overall_candidate:.2f}")
    for delta in report.deltas:
        print(f"    {delta.task_type:10s} {delta.incumbent:.2f} → {delta.candidate:.2f}")
    print(f"  paired: {report.wins}W/{report.losses}L/{report.ties}T p={report.p_value:.4f}")
    print(f"  VERDICT: {'GO' if report.go else 'NO-GO'} — {report.reasons[0]}")

    # ---- Act 7: provenance -------------------------------------------------------
    act(7, "Provenance: verify the whole session's signed chain")
    check = state.provenance.verify_chain(
        lambda wid: (r.public_key_b64 if (r := state.registry.get(wid)) else None))
    print(f"  {len(state.provenance)} entries, chain ok={check.ok} ({check.reason})")
    print(f"  head hash {state.provenance.head_hash()[:32]}…")
    assert check.ok

    # tampering is detected immediately
    if state.provenance.entries():
        state.provenance._entries[1].detail["tampered"] = True
        broken = state.provenance.verify_chain(
            lambda wid: (r.public_key_b64 if (r := state.registry.get(wid)) else None))
        print(f"  after tampering with entry #1: ok={broken.ok} → {broken.reason!r} at #{broken.problem_index}")
        del state.provenance._entries[1].detail["tampered"]

    print(f"\n{RULE}\nDemo complete. Journal dir: {journal_dir}\n{RULE}")

    if serve:
        import uvicorn
        print("\nServing WebUI at http://127.0.0.1:8321 (Ctrl-C to stop)")
        config = uvicorn.Config(create_app(state), host="127.0.0.1", port=8321, log_level="warning")
        await uvicorn.Server(config).serve()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", action="store_true", help="serve the WebUI after the demo")
    args = parser.parse_args()
    asyncio.run(main(serve=args.serve))
