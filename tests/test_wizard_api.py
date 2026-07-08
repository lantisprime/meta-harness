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
