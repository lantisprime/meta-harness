"""Wizard-path API tests: plan preview without running, run-from-reviewed-plan,
background (wait=false) start + poll until the HITL gate, non-blocking approval."""
from __future__ import annotations

import asyncio

import httpx
import pytest

from metaharness.core.types import Task, TaskType, Tier
from metaharness.harness import MockLLMWorker, ScriptedWorker
from metaharness.identity import KeyPair
from metaharness.web import HarnessState, create_app

GOOD_PLAN = {
    "name": "triage",
    "steps": [
        {"id": "classify", "task_type": "classify", "objective": "Classify severity.",
         "inputs": {"labels": ["low", "high"]}, "success_check": {"one_of": ["low", "high"]}},
        {"id": "page", "task_type": "transform", "objective": "Draft the page.",
         "depends_on": ["classify"], "hitl": True},
    ],
}


@pytest.fixture
async def client(tmp_path):
    state = HarnessState()
    kp1, kp2 = KeyPair.generate(), KeyPair.generate()
    perfect = {t: 1.0 for t in TaskType}
    small = MockLLMWorker("w-small", Tier.SMALL, keypair=kp1, seed=1, skills=perfect)
    planner = ScriptedWorker(
        "w-frontier",
        lambda t: dict(GOOD_PLAN) if t.task_type == TaskType.PLANNING else "high",
        tier=Tier.FRONTIER, keypair=kp2,
    )
    state.register_worker(small, kp1, tiers=["small"])
    state.register_worker(planner, kp2, tiers=["frontier"])
    state.wire({Tier.SMALL: small, Tier.FRONTIER: planner}, journal_dir=tmp_path)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(state)),
                                 base_url="http://test") as c:
        yield c


async def test_plan_preview_does_not_start_a_run(client):
    resp = await client.post("/api/plans", json={"goal": "triage the incident"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["plan_source"] == "planner"
    assert [s["id"] for s in data["workflow"]["steps"]] == ["classify", "page"]
    assert (await client.get("/api/runs")).json() == []  # nothing ran


async def test_run_from_reviewed_plan_background_then_approve(client):
    plan = (await client.post("/api/plans", json={"goal": "triage"})).json()["workflow"]

    # start without waiting — response returns immediately as running
    resp = await client.post("/api/runs", json={"workflow": plan, "context": {}, "wait": False})
    assert resp.status_code == 200
    run_id = resp.json()["run_id"]

    # poll until the background advance parks at the HITL gate
    for _ in range(50):
        detail = (await client.get(f"/api/runs/{run_id}")).json()
        if detail["state"]["status"] == "awaiting_approval":
            break
        await asyncio.sleep(0.05)
    assert detail["state"]["status"] == "awaiting_approval"
    assert detail["state"]["awaiting"] == "page"
    assert detail["state"]["completed"]["classify"]["verdict"] == "pass"

    # non-blocking approval, then poll to completion
    resp = await client.post(f"/api/runs/{run_id}/approval",
                             json={"step_id": "page", "approved": True, "wait": False})
    assert resp.status_code == 200
    for _ in range(50):
        detail = (await client.get(f"/api/runs/{run_id}")).json()
        if detail["state"]["status"] == "completed":
            break
        await asyncio.sleep(0.05)
    assert detail["state"]["status"] == "completed"


async def test_start_run_requires_some_workflow(client):
    resp = await client.post("/api/runs", json={"context": {}})
    assert resp.status_code == 422


# -- Issue #10: source-side check-value gating at the API intake boundary ---------

HAZARDOUS_CHECKS = [
    {"equals": 1e999},                    # JSON inf via a raw Python float
    {"equals": 10 ** 400},                 # overflows float()
    {"tol": 1e308},                        # finite but above MAX_TOL
    {"one_of": [1, 1e999]},                # non-finite member
]


async def _post_raw_json(client, url, body):
    """httpx's own `json=` kwarg refuses to serialize inf/nan (allow_nan=False,
    for spec-strict outbound bodies) — but the stdlib json module (default
    allow_nan=True) happily emits `Infinity`, exactly what a hand-crafted API
    body could send. Encode with the stdlib and post the raw bytes so the
    hazard actually reaches the endpoint under test."""
    import json

    return await client.post(
        url, content=json.dumps(body), headers={"content-type": "application/json"})


@pytest.mark.parametrize("bad_check", HAZARDOUS_CHECKS)
async def test_validate_workflow_rejects_value_hazard_checks(client, bad_check):
    workflow = {"name": "x", "steps": [
        {"id": "hazard-step", "objective": "o", "success_check": bad_check}]}
    resp = await _post_raw_json(client, "/api/workflows/validate", {"workflow": workflow})
    assert resp.status_code == 422
    assert "hazard-step" in resp.text


@pytest.mark.parametrize("bad_check", HAZARDOUS_CHECKS)
async def test_start_run_rejects_value_hazard_checks(client, bad_check):
    workflow = {"name": "x", "steps": [
        {"id": "hazard-step", "objective": "o", "success_check": bad_check}]}
    resp = await _post_raw_json(client, "/api/runs", {"workflow": workflow, "context": {}})
    assert resp.status_code == 422
    assert "hazard-step" in resp.text


async def test_validate_workflow_still_accepts_good_plan(client):
    """The gate is value-level only — GOOD_PLAN's benign one_of check must keep
    validating cleanly (no dashboard/vocabulary regressions)."""
    resp = await client.post("/api/workflows/validate", json={"workflow": GOOD_PLAN})
    assert resp.status_code == 200
    assert resp.json()["workflow"]["steps"][0]["success_check"] == {"one_of": ["low", "high"]}


async def test_start_run_still_accepts_good_plan(client):
    resp = await client.post("/api/runs", json={
        "workflow": GOOD_PLAN, "context": {}, "wait": False})
    assert resp.status_code == 200
    assert resp.json()["run_id"]
