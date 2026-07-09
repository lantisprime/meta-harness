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


async def test_routing_endpoint_exposes_pools_skills_and_tallies(tmp_path):
    """GET /api/routing lists each tier's pool members in configured order, each
    member's model-slice of the capability matrix, and routed-to counts once
    traffic has landed."""
    from metaharness.core.types import Task

    state = HarnessState()
    kp_a, kp_b = KeyPair.generate(), KeyPair.generate()
    a = MockLLMWorker("mid-a", Tier.MID, model="model-a", keypair=kp_a, seed=1)
    b = MockLLMWorker("mid-b", Tier.MID, model="model-b", keypair=kp_b, seed=2)
    state.register_worker(a, kp_a, tiers=["mid"])
    state.register_worker(b, kp_b, tiers=["mid"])
    state.wire({Tier.MID: [a, b]}, journal_dir=tmp_path)

    # give model-a evidence so it wins the slot, then route deterministic
    # (unverifiable → no ε-exploration) traffic to it
    for _ in range(6):
        state.matrix.record("model-a", TaskType.CLASSIFY, passed=True)
    for _ in range(3):
        state.router.decide(Task(task_type=TaskType.CLASSIFY))

    app = create_app(state)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        routing = (await c.get("/api/routing")).json()

    assert set(routing) == {"mid"}
    mid = routing["mid"]
    # members preserve configured order
    assert [m["worker_id"] for m in mid["members"]] == ["mid-a", "mid-b"]
    member_a = mid["members"][0]
    assert member_a["model"] == "model-a"
    assert member_a["display_name"] == "model-a"  # from the registry record
    # skills = that member's model slice of the matrix
    assert member_a["skills"]["classify"] == {"pass_rate": 1.0, "samples": 6}
    # a benched member with no evidence has an empty slice, never missing
    assert mid["members"][1]["skills"] == {}
    # routed tallies present, landing on the evidence-backed member
    assert mid["routed"]["mid-a"] == 3
    assert "mid-b" not in mid["routed"]


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

        before = wired_state.router.pools[Tier.SMALL][0]
        resp = await c.post("/api/optimization/math/approval", json={"approved": True})
        assert resp.json()["applied_live"] is True
        assert ledger.promoted_params() is not None
        assert wired_state.router.pools[Tier.SMALL][0] is not before
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
    wired_state.router.pools[Tier.SMALL][0] = SelfCritique(wired_state.router.pools[Tier.SMALL][0])
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

    chain, r = [], wired_state.router.pools[Tier.SMALL][0]
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
    chain, r = [], wired_state.router.pools[Tier.SMALL][0]
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


async def test_coverage_extends_suite_with_validated_questions(wired_state, tmp_path):
    """add_coverage end to end: the generator's items are validated (bad
    types and unscoreable items dropped, arithmetic answers RECOMPUTED — the
    generator's math is never trusted) and future searches include them."""
    import json

    from metaharness.core.types import Task, TaskType, WorkerResult
    from metaharness.harness.runner import Runner
    from metaharness.optimization.suites import search_and_holdout

    generated = {"tasks": [
        {"task_type": "arithmetic", "objective": "Compute 19*21. Answer with the number only.",
         "inputs": {"expression": "19*21"}, "success_check": {"equals": 12345}},  # wrong answer on purpose
        {"task_type": "arithmetic", "objective": "Compute nonsense.",
         "inputs": {"expression": "import os"}, "success_check": {"equals": 1}},   # unevaluable -> dropped
        {"task_type": "planning", "objective": "Plan a heist.",
         "inputs": {}, "success_check": {"equals": "x"}},                          # wrong domain -> dropped
        {"task_type": "classify", "objective": "Classify the sentiment...",
         "inputs": {"review": "utterly broken", "labels": ["positive", "negative"]}},  # no equals -> dropped
    ]}

    class Generator(Runner):
        worker_id, tier, model = "gen", Tier.SMALL, "gen"
        keypair = None
        async def run(self, task: Task) -> WorkerResult:
            return WorkerResult(task_id=task.id, worker_id="gen", tier=self.tier,
                                model="gen", output=generated, raw_text=json.dumps(generated))

    wired_state.optimization_root = tmp_path / "optimization"
    wired_state.router.pools[Tier.SMALL][0] = Generator()  # planner_runner picks it up
    app = create_app(wired_state)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post("/api/optimization/math/coverage", json={"n": 4})
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"suite": "math", "added": 1, "total_extras": 1}
        # idempotent-ish: the same generation adds nothing new
        assert (await c.post("/api/optimization/math/coverage", json={"n": 4})).status_code == 502

    extras_file = json.loads((tmp_path / "optimization" / "math" / "extra_tasks.json").read_text())
    assert len(extras_file) == 1
    assert extras_file[0]["success_check"]["equals"] == 399   # recomputed, not 12345

    base_search, base_holdout = search_and_holdout("math")
    search, holdout = search_and_holdout("math", extras_dir=tmp_path / "optimization" / "math")
    assert len(search) + len(holdout) == len(base_search) + len(base_holdout) + 1
    assert any(t.success_check == {"equals": 399} for t in search + holdout)


async def test_coverage_hardens_check_values_and_survives_div_by_zero(wired_state, tmp_path):
    """Check-value hardening on the coverage endpoint, end to end: a negative
    tol, a mixed primary key ({equals}+{one_of}), an over-large tol (float()
    OverflowError), a finite-huge tol (Issue #9, 1e308 over MAX_TOL), a
    recomputed 401-digit int (Issue #9, '10**400') and a div-by-zero recompute
    are each dropped — the overflowing/recomputed/div cases WITHOUT 500ing the
    endpoint — while the one clean {equals, tol} item is added with its
    arithmetic answer recomputed and its valid tol preserved."""
    import json

    from metaharness.core.types import Task, WorkerResult
    from metaharness.harness.runner import Runner

    generated = {"tasks": [
        {"task_type": "arithmetic", "objective": "Compute 2+2. Answer with the number only.",
         "inputs": {"expression": "2+2"}, "success_check": {"equals": 4, "tol": -1}},        # negative tol -> dropped
        {"task_type": "arithmetic", "objective": "Compute 3+3. Answer with the number only.",
         "inputs": {"expression": "3+3"}, "success_check": {"equals": 6, "one_of": [6, 7]}},  # mixed keys -> dropped
        {"task_type": "arithmetic", "objective": "Compute 1/0. Answer with the number only.",
         "inputs": {"expression": "1/0"}, "success_check": {"equals": 1}},                    # recompute raises -> dropped
        {"task_type": "arithmetic", "objective": "Compute 5+5. Answer with the number only.",
         "inputs": {"expression": "5+5"}, "success_check": {"equals": 10, "tol": 10 ** 400}},  # OverflowError tol -> dropped, not 500
        {"task_type": "arithmetic", "objective": "Compute 7+7. Answer with the number only.",
         "inputs": {"expression": "7+7"}, "success_check": {"equals": 14, "tol": 1e308}},      # Issue #9: finite-huge tol -> dropped
        {"task_type": "arithmetic", "objective": "Compute 10**400. Answer with the number only.",
         "inputs": {"expression": "10**400"}, "success_check": {"equals": 1}},                 # Issue #9: recompute -> 401-digit int -> dropped, not 500
        {"task_type": "arithmetic", "objective": "Compute 1e999. Answer with the number only.",
         "inputs": {"expression": "1e999"}, "success_check": {"equals": 1}},                   # Issue #9 panel: recompute -> inf (no raise) -> dropped
        {"task_type": "arithmetic", "objective": "Compute 6*7. Answer with the number only.",
         "inputs": {"expression": "6*7"}, "success_check": {"equals": 0, "tol": 0.5}},        # clean -> added (equals=42)
    ]}

    class Generator(Runner):
        worker_id, tier, model = "gen", Tier.SMALL, "gen"
        keypair = None
        async def run(self, task: Task) -> WorkerResult:
            return WorkerResult(task_id=task.id, worker_id="gen", tier=self.tier,
                                model="gen", output=generated, raw_text=json.dumps(generated))

    wired_state.optimization_root = tmp_path / "optimization"
    wired_state.router.pools[Tier.SMALL][0] = Generator()  # planner_runner picks it up
    app = create_app(wired_state)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post("/api/optimization/math/coverage", json={"n": 4})
        assert resp.status_code == 200, resp.text   # div-by-zero recompute did NOT 500 the endpoint
        assert resp.json() == {"suite": "math", "added": 1, "total_extras": 1}

    extras_file = json.loads((tmp_path / "optimization" / "math" / "extra_tasks.json").read_text())
    assert len(extras_file) == 1
    assert extras_file[0]["success_check"] == {"equals": 42, "tol": 0.5}  # recomputed, valid tol kept


def _claude_proposal_stub(path, delta: dict, parent: str = "c0001") -> str:
    """A fake `claude` CLI that prints a Proposal JSON (wrapped in chatter) in
    claude's --output-format json envelope — no real coding CLI needed."""
    import json
    import stat

    proposal = json.dumps({"hypothesis": "web code-proposer test",
                           "parent": parent, "delta": delta})
    envelope = json.dumps({"result": f"Read the ledger. Final: {proposal}",
                           "total_cost_usd": 0.0})
    path.write_text(f"#!/bin/sh\ncat > /dev/null\ncat <<'OUT'\n{envelope}\nOUT\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


async def test_tuning_code_proposer_kicks_a_run_with_stubbed_detection(wired_state, tmp_path, monkeypatch):
    """POST proposer=code with a stubbed coding-CLI detection: the run is
    accepted (202) and the background search completes — the code proposer is
    wired end to end through the endpoint."""
    import asyncio

    import metaharness.harness as harness_pkg

    stub = _claude_proposal_stub(tmp_path / "claude", {"self_consistency_k": 2})
    monkeypatch.setattr(harness_pkg, "available_clis", lambda: {"claude": stub})

    wired_state.optimization_root = tmp_path / "optimization"
    app = create_app(wired_state)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post("/api/optimization/runs",
                            json={"suite": "math", "rounds": 1, "k": 1, "proposer": "code"})
        assert resp.status_code == 202, resp.text
        for _ in range(200):
            suites = (await c.get("/api/optimization")).json()
            if suites and suites[0]["report"] and not suites[0]["running"]:
                break
            await asyncio.sleep(0.05)
        else:
            raise AssertionError("code-proposer search never finished")
        assert suites[0]["suite"] == "math"


async def test_tuning_code_proposer_without_a_cli_is_a_clean_422(wired_state, tmp_path, monkeypatch):
    """No coding CLI on the host -> a clean 422 with the reason, never a 500."""
    import metaharness.harness as harness_pkg

    monkeypatch.setattr(harness_pkg, "available_clis", lambda: {})
    wired_state.optimization_root = tmp_path / "optimization"
    app = create_app(wired_state)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post("/api/optimization/runs",
                            json={"suite": "math", "proposer": "code"})
        assert resp.status_code == 422
        assert "coding CLI" in resp.json()["detail"]


async def test_optimization_payload_carries_code_ref_for_code_candidates(wired_state, tmp_path):
    """A code-carrying candidate surfaces its canonical code_ref in the
    /api/optimization payload so the dashboard can badge it; knob-only
    candidates omit the key entirely."""
    from metaharness.optimization import CandidateLedger, HarnessParams
    from tests.test_optimization import evaluated_candidate

    wired_state.optimization_root = tmp_path / "optimization"
    ledger = CandidateLedger(tmp_path / "optimization" / "math")
    ledger.record(evaluated_candidate("c0001", 0.4, 100))
    ledger.record(evaluated_candidate(
        "c0002", 0.9, 90, parent="c0001",
        params=HarnessParams(code_ref="candidates/c0002/harness.py", code_hash="deadbeef"),
    ))

    app = create_app(wired_state)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        suites = (await c.get("/api/optimization")).json()
    cands = {x["id"]: x for x in suites[0]["candidates"]}
    assert cands["c0002"]["code_ref"] == "candidates/c0002/harness.py"
    assert "code_ref" not in cands["c0001"]
