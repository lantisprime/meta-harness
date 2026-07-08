"""Planner + goal-launch + agent-config tests.

The planner path is exercised with a scripted worker emitting real plans (valid,
invalid, and garbage), the HTTP layer end to end, and — when a local endpoint is
up — a genuine LLM planning a genuine multi-step workflow.
"""
from __future__ import annotations

import httpx
import pytest

from metaharness.core.types import Task, TaskType, Tier
from metaharness.harness import MockLLMWorker, OpenAICompatWorker, ScriptedWorker, probe_endpoint
from metaharness.web import HarnessState, create_app
from metaharness.workflows import WorkflowSpec, plan_workflow
from metaharness.workflows.planner import fallback_spec

OLLAMA = "http://localhost:11434/v1"

GOOD_PLAN = {
    "name": "triage",
    "steps": [
        {"id": "classify", "task_type": "classify", "objective": "Classify severity.",
         "inputs": {"labels": ["low", "high"]}, "success_check": {"one_of": ["low", "high"]}},
        {"id": "summarize", "task_type": "summarize", "objective": "Summarize for on-call.",
         "depends_on": ["classify"]},
        {"id": "page", "task_type": "transform", "objective": "Draft the page.",
         "depends_on": ["summarize"], "hitl": True},
    ],
}


async def test_planner_accepts_valid_plan():
    planner = ScriptedWorker("p", lambda t: dict(GOOD_PLAN))
    spec, source = await plan_workflow("triage the incident", planner)
    assert source == "planner"
    assert [s.id for s in spec.steps] == ["classify", "summarize", "page"]
    assert spec.steps[2].hitl


async def test_planner_extracts_plan_from_prose():
    text = "Here is the plan:\n" + str(GOOD_PLAN).replace("'", '"').replace("True", "true")
    planner = ScriptedWorker("p", lambda t: text)
    spec, source = await plan_workflow("triage", planner)
    assert source == "planner" and len(spec.steps) == 3


@pytest.mark.parametrize("bad_output", [
    "no json here at all",
    {"name": "x", "steps": [{"id": "a", "objective": "o", "depends_on": ["ghost"]}]},  # invalid dep
    {"name": "x", "steps": [{"id": "a", "objective": "o", "task_type": "not-a-type"}]},
    {"nope": True},
])
async def test_planner_falls_back_on_bad_plans(bad_output):
    planner = ScriptedWorker("p", lambda t: bad_output)
    spec, source = await plan_workflow("summarize the report", planner)
    assert source == "fallback"
    assert len(spec.steps) == 1 and spec.steps[0].task_type == TaskType.GENERAL
    assert spec.steps[0].objective == "summarize the report"


def test_fallback_spec_slug():
    spec = fallback_spec("Fix the DB!! Now.")
    assert spec.name == "fix-the-db-now"


# -- HTTP layer -----------------------------------------------------------------


@pytest.fixture
async def client(tmp_path):
    state = HarnessState()
    from metaharness.identity import KeyPair
    kp1, kp2 = KeyPair.generate(), KeyPair.generate()
    perfect = {t: 1.0 for t in TaskType}
    small = MockLLMWorker("w-small", Tier.SMALL, keypair=kp1, seed=1, skills=perfect)

    class PlanningWorker(ScriptedWorker):
        pass

    planner = PlanningWorker(
        "w-frontier",
        lambda t: dict(GOOD_PLAN) if t.task_type == TaskType.PLANNING else "high",
        tier=Tier.FRONTIER, keypair=kp2,
    )
    state.register_worker(small, kp1, tiers=["small"])
    state.register_worker(planner, kp2, tiers=["frontier"])
    state.wire({Tier.SMALL: small, Tier.FRONTIER: planner}, journal_dir=tmp_path)
    app = create_app(state)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as c:
        yield c


async def test_goal_endpoint_plans_and_runs(client):
    resp = await client.post("/api/goals", json={"goal": "triage the incident", "context": {}})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["plan_source"] == "planner"
    assert [s["id"] for s in data["workflow"]["steps"]] == ["classify", "summarize", "page"]
    # ran up to the HITL gate the planner set on the outward-facing step
    assert data["run"]["status"] == "awaiting_approval"
    assert data["run"]["awaiting"] == "page"

    # plan recorded in provenance
    prov = (await client.get("/api/provenance")).json()
    assert any(e["action"] == "workflow.planned" for e in prov["entries"])
    assert prov["chain"]["ok"]


async def test_goal_endpoint_rejects_empty(client):
    assert (await client.post("/api/goals", json={"goal": "  "})).status_code == 422


async def test_add_mock_worker_and_duplicate_conflict(client):
    resp = await client.post("/api/workers", json={
        "worker_id": "extra-mid", "tier": "mid", "kind": "mock"})
    assert resp.status_code == 201
    assert resp.json()["worker_id"] == "extra-mid"
    workers = (await client.get("/api/workers")).json()
    assert any(w["worker_id"] == "extra-mid" for w in workers)

    dup = await client.post("/api/workers", json={
        "worker_id": "extra-mid", "tier": "mid", "kind": "mock"})
    assert dup.status_code == 409


async def test_add_openai_worker_validates_endpoint(client):
    resp = await client.post("/api/workers", json={
        "worker_id": "ghost", "tier": "small", "kind": "openai_compat",
        "base_url": "http://localhost:59999/v1", "model": "whatever"})
    assert resp.status_code == 422


# -- live: a real local model plans a real workflow --------------------------------


async def test_live_local_model_plans_workflow():
    models = await probe_endpoint(OLLAMA, timeout_s=1.5)
    if not models:
        pytest.skip("no local OpenAI-compatible endpoint at :11434")
    planner = OpenAICompatWorker(
        "ollama-planner", base_url=OLLAMA, model=models[0], tier=Tier.FRONTIER,
        temperature=0.0, max_tokens=4000,
    )
    spec, source = await plan_workflow(
        "Read the customer complaint in context, classify its urgency as low or high, "
        "summarize it for support, and draft a reply email for human approval.",
        planner, context={"complaint": "my order arrived broken twice"},
    )
    assert isinstance(spec, WorkflowSpec)
    print(f"\n  plan source: {source}; steps: {[s.id for s in spec.steps]}")
    if source == "planner":
        assert len(spec.steps) >= 2
        assert spec.topological_order()  # acyclic, valid deps


# -- derived checks (regression: run_ef22d875cfa3, 2026-07-08) ----------------------


async def test_planner_derives_one_of_from_objective():
    """Planners state the constraint in the objective but omit success_check;
    the harness derives it deterministically."""
    plan = {"name": "x", "steps": [
        {"id": "c", "task_type": "classify",
         "objective": "Classify the urgency as exactly one of: low, high.",
         "inputs": {}},
    ]}
    planner = ScriptedWorker("p", lambda t: plan)
    spec, source = await plan_workflow("classify it", planner)
    assert source == "planner"
    assert spec.steps[0].success_check == {"one_of": ["low", "high"]}


async def test_planner_derives_arithmetic_equals_from_expression():
    """Ground truth for arithmetic comes from the harness's own sandbox."""
    plan = {"name": "x", "steps": [
        {"id": "a", "task_type": "arithmetic",
         "objective": "Compute the user hours.",
         "inputs": {"expression": "340 * 6"}},
    ]}
    planner = ScriptedWorker("p", lambda t: plan)
    spec, _ = await plan_workflow("compute", planner)
    assert spec.steps[0].success_check == {"equals": 2040}


async def test_derived_checks_never_overwrite_planner_checks():
    plan = {"name": "x", "steps": [
        {"id": "c", "task_type": "classify",
         "objective": "Classify as exactly one of: a, b.",
         "success_check": {"one_of": ["a", "b", "c"]}, "inputs": {}},
    ]}
    planner = ScriptedWorker("p", lambda t: plan)
    spec, _ = await plan_workflow("classify", planner)
    assert spec.steps[0].success_check == {"one_of": ["a", "b", "c"]}


def test_one_of_verification_is_case_insensitive():
    from metaharness.core.types import WorkerResult, Verdict
    from metaharness.evals import verify_output

    task = Task(task_type=TaskType.CLASSIFY, objective="o",
                success_check={"one_of": ["low", "high"]})
    result = WorkerResult(task_id="t", worker_id="w", tier=Tier.SMALL,
                          model="m", output=" High ")
    assert verify_output(task, result).verdict == Verdict.PASS


async def test_planner_derives_equals_from_exact_literal():
    """Regression (run_616a9d313246): 'Extract the exact word X' steps must be
    checkable — quoted and colon phrasings both derive an equals check."""
    for objective in [
        "Extract the exact word 'BLUE-HORIZON-7734' from the provided text.",
        'Find exactly the phrase "BLUE-HORIZON-7734" in the document.',
        "Return exactly the word: BLUE-HORIZON-7734",
    ]:
        plan = {"name": "x", "steps": [
            {"id": "e", "task_type": "extract", "objective": objective, "inputs": {}}]}
        planner = ScriptedWorker("p", lambda t, pl=plan: pl)
        spec, _ = await plan_workflow("extract", planner)
        assert spec.steps[0].success_check == {"equals": "BLUE-HORIZON-7734"}, objective


async def test_no_literal_no_derived_check():
    plan = {"name": "x", "steps": [
        {"id": "e", "task_type": "extract",
         "objective": "Extract the exact date from the text.", "inputs": {}}]}
    planner = ScriptedWorker("p", lambda t: plan)
    spec, _ = await plan_workflow("extract", planner)
    assert spec.steps[0].success_check is None
