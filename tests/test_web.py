"""WebUI/API tests: real ASGI requests against a fully wired HarnessState —
start a run over HTTP, pause at the HITL gate, approve over HTTP, watch it
complete; provenance chain verified through the endpoint."""
from __future__ import annotations

import httpx
import pytest

from metaharness.core.types import TaskType, Tier
from metaharness.harness import MockLLMWorker
from metaharness.identity import KeyPair
from metaharness.web import HarnessState, create_app

WORKFLOW_YAML = """
name: http-triage
steps:
  - id: classify
    task_type: classify
    objective: Classify the ticket severity.
    inputs: {labels: [low, high]}
    success_check: {equals: high}
  - id: notify
    task_type: transform
    objective: Draft the page.
    depends_on: [classify]
    hitl: true
    success_check: {contains: attempt}
"""


@pytest.fixture
def wired_state(tmp_path) -> HarnessState:
    state = HarnessState()
    kp = KeyPair.generate()
    perfect = {t: 1.0 for t in TaskType}
    runner = MockLLMWorker("w-small", Tier.SMALL, keypair=kp, seed=1, skills=perfect)
    state.register_worker(runner, kp, tiers=["small"])
    state.wire({Tier.SMALL: runner}, journal_dir=tmp_path)
    return state


@pytest.fixture
async def client(wired_state):
    app = create_app(wired_state)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_dashboard_served(client):
    resp = await client.get("/")
    assert resp.status_code == 200
    assert "metaharness" in resp.text and "Capability matrix" in resp.text


async def test_full_run_over_http(client):
    # start → runs until the HITL gate
    resp = await client.post("/api/runs", json={"workflow_yaml": WORKFLOW_YAML, "context": {}})
    assert resp.status_code == 200, resp.text
    run = resp.json()
    assert run["status"] == "awaiting_approval" and run["awaiting"] == "notify"
    run_id = run["run_id"]

    # runs list + detail expose state and journal
    runs = (await client.get("/api/runs")).json()
    assert any(r["run_id"] == run_id for r in runs)
    detail = (await client.get(f"/api/runs/{run_id}")).json()
    assert any(e["kind"] == "hitl.requested" for e in detail["journal"])

    # approving the wrong step conflicts
    conflict = await client.post(f"/api/runs/{run_id}/approval",
                                 json={"step_id": "classify", "approved": True})
    assert conflict.status_code == 409

    # approve the right step → run completes
    resp = await client.post(f"/api/runs/{run_id}/approval",
                             json={"step_id": "notify", "approved": True})
    assert resp.status_code == 200
    assert resp.json()["status"] == "completed"


async def test_rejection_over_http(client):
    run = (await client.post("/api/runs", json={"workflow_yaml": WORKFLOW_YAML})).json()
    resp = await client.post(f"/api/runs/{run['run_id']}/approval",
                             json={"step_id": "notify", "approved": False})
    assert resp.json()["status"] == "failed"


async def test_invalid_workflow_yaml_422(client):
    bad = WORKFLOW_YAML.replace("depends_on: [classify]", "depends_on: [ghost]")
    resp = await client.post("/api/runs", json={"workflow_yaml": bad})
    assert resp.status_code == 422


async def test_workers_provenance_matrix_endpoints(client):
    await client.post("/api/runs", json={"workflow_yaml": WORKFLOW_YAML})

    workers = (await client.get("/api/workers")).json()
    ids = {w["worker_id"] for w in workers}
    assert {"orchestrator", "w-small"} <= ids

    prov = (await client.get("/api/provenance")).json()
    assert prov["chain"]["ok"] and prov["total"] > 0
    assert prov["entries"][0]["actor_id"] == "orchestrator"

    matrix = (await client.get("/api/matrix")).json()
    assert matrix["mock-small"]["classify"]["samples"] >= 1

    spans = (await client.get("/api/spans")).json()
    assert any(s["name"] == "task.execute" for s in spans)

    assert (await client.get("/api/failures")).status_code == 200
    assert (await client.get("/api/playbook")).status_code == 200


async def test_unknown_run_404(client):
    assert (await client.get("/api/runs/run_nope")).status_code == 404
