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
    assert "metaharness" in resp.text and "Who’s good at what" in resp.text


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


async def test_runs_carry_journal_timestamps(client):
    """/api/runs exposes started_at/updated_at derived from the journal so the
    console can show human-relative dates."""
    await client.post("/api/runs", json={"workflow_yaml": WORKFLOW_YAML, "context": {}})
    runs = (await client.get("/api/runs")).json()
    assert runs, "run should be listed"
    rec = runs[-1]
    assert rec["started_at"] is not None and rec["updated_at"] is not None
    assert rec["updated_at"] >= rec["started_at"]

    detail = (await client.get(f"/api/runs/{rec['run_id']}")).json()
    entries = detail["journal"]
    assert rec["started_at"] == entries[0]["at"]
    assert rec["updated_at"] == entries[-1]["at"]


async def test_optimization_endpoint_serves_tuning_ledgers(wired_state, tmp_path):
    """/api/optimization reads harness-tuning ledgers from disk each poll:
    candidates with frontier flags, the persisted report, promoted params, and
    deterministic findings — empty (not an error) when nothing has run."""
    from metaharness.harness.runner import Runner
    from metaharness.core.types import Task, TaskType, WorkerResult
    from metaharness.optimization import CandidateLedger, HarnessOptimizer, RuleProposer, search_and_holdout

    wired_state.optimization_root = tmp_path / "optimization"
    app = create_app(wired_state)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        assert (await c.get("/api/optimization")).json() == []

        class TranscribeOnly(Runner):
            worker_id, tier, model = "t", Tier.SMALL, "t"
            async def run(self, task: Task) -> WorkerResult:
                if task.inputs.get("emit_program") and "expression" in task.inputs:
                    out = {"program": task.inputs["expression"]}
                elif task.task_type == TaskType.ARITHMETIC:
                    out = -1
                else:
                    out = (task.success_check or {}).get("equals", "ok")
                return WorkerResult(task_id=task.id, worker_id="t", tier=Tier.SMALL,
                                    model="t", output=out, raw_text=str(out),
                                    tokens_in=30, tokens_out=10)

        search, holdout = search_and_holdout("math")
        ledger = CandidateLedger(tmp_path / "optimization" / "math")
        await HarnessOptimizer(TranscribeOnly, RuleProposer(), search, holdout,
                               ledger, k=2).optimize(rounds=3)

        suites = (await c.get("/api/optimization")).json()
        assert [s["suite"] for s in suites] == ["math"]
        s = suites[0]
        assert s["promoted"]["candidate"] == s["report"]["best_id"]
        promoted = next(c2 for c2 in s["candidates"] if c2["id"] == s["promoted"]["candidate"])
        assert promoted["frontier"] and promoted["scores"]["tokens_total"] > 0
        assert s["findings"][0]["kind"] == "promotion"
        assert s["report"]["gate"]["go"] is True


async def test_tuning_start_approve_and_hot_swap(wired_state, tmp_path):
    """Full web tuning loop: POST starts a background search against the bare
    small-tier worker; the winner parks as pending; approval promotes it and
    rewires the live router runner; a second decision 409s."""
    import asyncio

    from metaharness.optimization import CandidateLedger

    wired_state.optimization_root = tmp_path / "optimization"
    app = create_app(wired_state)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post("/api/optimization/runs", json={"suite": "math", "rounds": 3, "k": 2})
        assert resp.status_code == 202, resp.text

        for _ in range(200):                       # mock workers finish fast
            suites = (await c.get("/api/optimization")).json()
            if suites and suites[0]["report"] and not suites[0]["running"]:
                break
            await asyncio.sleep(0.05)
        else:
            raise AssertionError("tuning search never finished")

        s = suites[0]
        assert s["suite"] == "math" and s["promoted"] is None
        ledger = CandidateLedger(tmp_path / "optimization" / "math")
        if s["pending"] is None:   # perfect mock may leave nothing to promote
            assert (await c.post("/api/optimization/math/approval", json={"approved": True})).status_code == 409
            return

        before = wired_state.router.runners[Tier.SMALL]
        resp = await c.post("/api/optimization/math/approval", json={"approved": True})
        assert resp.json()["applied_live"] is True
        assert ledger.promoted_params() is not None
        assert wired_state.router.runners[Tier.SMALL] is not before
        assert (await c.post("/api/optimization/math/approval", json={"approved": True})).status_code == 409


async def test_tuning_reject_keeps_current_setup(wired_state, tmp_path):
    from metaharness.optimization import CandidateLedger, HarnessParams
    from tests.test_optimization import evaluated_candidate

    wired_state.optimization_root = tmp_path / "optimization"
    ledger = CandidateLedger(tmp_path / "optimization" / "classify")
    ledger.record(evaluated_candidate("c0001", 0.5, 100))
    ledger.record(evaluated_candidate("c0002", 0.9, 90, parent="c0001",
                                      params=HarnessParams(tool_offload=True)))
    ledger.save_pending("c0002", {"go": True, "overall_incumbent": 0.5, "overall_candidate": 0.9})

    app = create_app(wired_state)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post("/api/optimization/classify/approval", json={"approved": False})
        assert resp.json() == {"suite": "classify", "approved": False, "candidate": "c0002"}
        assert ledger.promoted_params() is None and ledger.pending_info() is None


async def test_optimization_get_is_read_only_and_root_contained(wired_state, tmp_path):
    """Codex slice-1 P1 regression: a plain GET must not create directories in
    empty suite dirs, and symlinks pointing outside the root are refused."""
    root = tmp_path / "optimization"
    (root / "empty-suite").mkdir(parents=True)
    outside = tmp_path / "outside-target"
    outside.mkdir()
    (root / "sneaky").symlink_to(outside)

    wired_state.optimization_root = root
    app = create_app(wired_state)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        assert (await c.get("/api/optimization")).json() == []
    assert list((root / "empty-suite").iterdir()) == []
    assert list(outside.iterdir()) == []
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        assert (await c.post("/api/optimization/sneaky/approval", json={"approved": True})).status_code == 404


async def test_approval_preserves_user_wrappers_and_writes_active(wired_state, tmp_path):
    """Codex slices-2+3 P1s: (a) approving a promotion must not strip
    user-selected wrappers like serve --critique — only the tuning layer is
    replaced; (b) the approval writes active.json so the NEXT boot replays the
    approved suite, not just 'mixed'."""
    import json

    from metaharness.harness import SelfCritique
    from metaharness.harness.enrichment import ToolOffload
    from metaharness.optimization import CandidateLedger, HarnessParams
    from tests.test_optimization import evaluated_candidate

    # simulate `serve --critique`: the live runner is critique-wrapped
    wired_state.router.runners[Tier.SMALL] = SelfCritique(wired_state.router.runners[Tier.SMALL])
    wired_state.optimization_root = tmp_path / "optimization"
    ledger = CandidateLedger(tmp_path / "optimization" / "math")
    ledger.record(evaluated_candidate("c0001", 0.4, 100))
    ledger.record(evaluated_candidate("c0002", 0.9, 90, parent="c0001",
                                      params=HarnessParams(tool_offload=True)))
    ledger.save_pending("c0002", {"go": True, "overall_incumbent": 0.4, "overall_candidate": 0.9})

    app = create_app(wired_state)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        assert (await c.post("/api/optimization/math/approval", json={"approved": True})).json()["applied_live"]

    chain, r = [], wired_state.router.runners[Tier.SMALL]
    while r is not None:
        chain.append(type(r).__name__)
        r = getattr(r, "inner", None)
    assert "ToolOffload" in chain and "SelfCritique" in chain     # both layers live
    assert chain.index("ToolOffload") < chain.index("SelfCritique")  # tuning wraps ON TOP

    active = json.loads((tmp_path / "optimization" / "active.json").read_text())
    assert active["suite"] == "math" and active["candidate"] == "c0002"

    # re-approving another candidate replaces the tuning layer, never stacks it
    ledger.record(evaluated_candidate("c0003", 0.95, 95, parent="c0001",
                                      params=HarnessParams(self_consistency_k=3)))
    ledger.save_pending("c0003", {"go": True, "overall_incumbent": 0.4, "overall_candidate": 0.95})
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        await c.post("/api/optimization/math/approval", json={"approved": True})
    chain, r = [], wired_state.router.runners[Tier.SMALL]
    while r is not None:
        chain.append(type(r).__name__)
        r = getattr(r, "inner", None)
    assert chain.count("SelfCritique") == 1 and "ToolOffload" not in chain
    assert "SelfConsistency" in chain


async def test_tuning_accepts_llm_proposer_and_rejects_unknown(wired_state, tmp_path):
    """The web Tune button can hand proposing to the wired frontier agent —
    the paper-shaped proposer, able to hypothesize prompt directives."""
    import asyncio

    wired_state.optimization_root = tmp_path / "optimization"
    app = create_app(wired_state)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        assert (await c.post("/api/optimization/runs",
                             json={"suite": "math", "proposer": "psychic"})).status_code == 422
        resp = await c.post("/api/optimization/runs",
                            json={"suite": "math", "rounds": 1, "k": 1, "proposer": "llm"})
        assert resp.status_code == 202
        for _ in range(200):
            suites = (await c.get("/api/optimization")).json()
            if suites and not suites[0]["running"]:
                break
            await asyncio.sleep(0.05)
        assert suites[0]["report"] is not None   # the llm-proposer search completed
