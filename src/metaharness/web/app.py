"""FastAPI app: JSON API + single-page dashboard over the whole harness.

Everything the dashboard shows is the harness's real state — live spans from the
OTel store, the actual provenance chain (verified on every request), the real
capability matrix the router routes with, the playbook the learning loop curates.
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from metaharness.core.types import Tier
from metaharness.harness.local import OpenAICompatWorker, probe_endpoint
from metaharness.harness.workers import MockLLMWorker
from metaharness.identity.registry import RegistryError
from metaharness.observability.tracing import store
from metaharness.web.dashboard import DASHBOARD_HTML
from metaharness.web.state import HarnessState
from metaharness.workflows.dsl import load_workflow
from metaharness.workflows.planner import plan_workflow


class ApprovalRequest(BaseModel):
    step_id: str
    approved: bool = True
    wait: bool = True


class StartRunRequest(BaseModel):
    workflow_yaml: str = ""
    workflow: Optional[dict[str, Any]] = None    # a reviewed plan, as JSON
    context: dict[str, Any] = {}
    wait: bool = True                             # False → run in background, poll


class GoalRequest(BaseModel):
    goal: str
    context: dict[str, Any] = {}


class AddWorkerRequest(BaseModel):
    worker_id: str
    tier: Tier
    kind: str = "openai_compat"          # openai_compat | mock
    base_url: str = ""                    # openai_compat only
    model: str = ""
    thinking: Optional[bool] = None
    max_tokens: Optional[int] = 4000


def create_app(state: HarnessState) -> FastAPI:
    app = FastAPI(title="metaharness", version="0.1.0")
    app.state.harness = state

    @app.on_event("startup")
    async def _resume_interrupted_runs() -> None:
        """Runs adopted from journals in RUNNING state were interrupted mid-run
        (crash/restart) — advance them so they finish or fail visibly instead of
        sitting 'running' forever."""
        if state.engine is None:
            return
        for run in state.engine.runs():
            if run.status.value == "running":
                _advance_in_background(run.run_id)

    # -- dashboard ---------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    async def dashboard() -> str:
        return DASHBOARD_HTML

    # -- runs / HITL --------------------------------------------------------------

    @app.get("/api/runs")
    async def runs() -> list[dict[str, Any]]:
        if state.engine is None:
            return []
        return [r.model_dump(mode="json") for r in state.engine.runs()]

    @app.get("/api/runs/{run_id}")
    async def run_detail(run_id: str) -> dict[str, Any]:
        try:
            run_state = state.engine.state(run_id)
            journal = state.engine.journal(run_id)
        except (KeyError, AttributeError):
            raise HTTPException(404, f"unknown run {run_id}")
        return {
            "state": run_state.model_dump(mode="json"),
            "journal": [e.model_dump(mode="json") for e in journal.entries()],
        }

    def _advance_in_background(run_id: str) -> None:
        async def _run() -> None:
            try:
                await state.engine.advance(run_id)
            except Exception:  # surfaced via run state / journal, never crashes the app
                pass
        asyncio.get_running_loop().create_task(_run())

    @app.post("/api/runs/{run_id}/approval")
    async def resolve_approval(run_id: str, req: ApprovalRequest) -> dict[str, Any]:
        try:
            run_state = state.engine.state(run_id)
        except (KeyError, AttributeError):
            raise HTTPException(404, f"unknown run {run_id}")
        if run_state.awaiting != req.step_id:
            raise HTTPException(409, f"run is not awaiting approval on {req.step_id!r}")
        if req.approved:
            state.engine.approve(run_id, req.step_id)
        else:
            state.engine.reject(run_id, req.step_id)
        if req.wait:
            run_state = await state.engine.advance(run_id)
        else:
            _advance_in_background(run_id)
        return run_state.model_dump(mode="json")

    @app.post("/api/plans")
    async def preview_plan(req: GoalRequest) -> dict[str, Any]:
        """Plan a workflow from a goal WITHOUT starting it — the wizard's review
        step. The confirmed plan comes back via POST /api/runs {workflow: ...}."""
        if state.engine is None:
            raise HTTPException(503, "engine not wired")
        if not req.goal.strip():
            raise HTTPException(422, "goal is empty")
        context = {"goal": req.goal, **req.context}
        spec, source = await plan_workflow(req.goal, state.planner_runner(), context)
        return {"workflow": spec.model_dump(mode="json"), "plan_source": source}

    @app.post("/api/runs")
    async def start_run(req: StartRunRequest) -> dict[str, Any]:
        if state.engine is None:
            raise HTTPException(503, "engine not wired")
        try:
            if req.workflow is not None:
                from metaharness.workflows.dsl import WorkflowSpec
                spec = WorkflowSpec.model_validate(req.workflow)
            elif req.workflow_yaml:
                spec = load_workflow(req.workflow_yaml)
            else:
                raise ValueError("provide workflow or workflow_yaml")
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        run_state = state.engine.start(spec, context=req.context)
        if req.wait:
            run_state = await state.engine.advance(run_state.run_id)
        else:
            _advance_in_background(run_state.run_id)
        return run_state.model_dump(mode="json")

    @app.post("/api/goals")
    async def start_from_goal(req: GoalRequest) -> dict[str, Any]:
        """The plan-then-execute entry point: describe the goal; the harness
        plans the workflow with its most capable worker, records the plan in
        provenance, and starts the run."""
        if state.engine is None:
            raise HTTPException(503, "engine not wired")
        if not req.goal.strip():
            raise HTTPException(422, "goal is empty")
        context = {"goal": req.goal, **req.context}
        spec, source = await plan_workflow(req.goal, state.planner_runner(), context)
        state.provenance.append(
            "orchestrator", "workflow.planned",
            {"goal": req.goal[:300], "source": source, "workflow": spec.name,
             "steps": [s.id for s in spec.steps]},
            keypair=state.orchestrator_keypair,
        )
        run_state = state.engine.start(spec, context=context)
        run_state = await state.engine.advance(run_state.run_id)
        return {
            "run": run_state.model_dump(mode="json"),
            "plan_source": source,
            "workflow": spec.model_dump(mode="json"),
        }

    # -- identity / provenance ------------------------------------------------------

    @app.post("/api/workers", status_code=201)
    async def add_worker(req: AddWorkerRequest) -> dict[str, Any]:
        """Configure a new agent: build the runner, admit its identity through
        the registration ceremony, and point the tier's routing slot at it."""
        from metaharness.identity.keys import KeyPair

        keypair = KeyPair.generate()
        if req.kind == "openai_compat":
            if not req.base_url or not req.model:
                raise HTTPException(422, "openai_compat workers need base_url and model")
            models = await probe_endpoint(req.base_url)
            if models is None:
                raise HTTPException(422, f"no OpenAI-compatible endpoint at {req.base_url}")
            if req.model not in models:
                raise HTTPException(422, f"model {req.model!r} not served there; found: {models[:10]}")
            runner = OpenAICompatWorker(
                req.worker_id, base_url=req.base_url, model=req.model, tier=req.tier,
                keypair=keypair, thinking=req.thinking, max_tokens=req.max_tokens,
            )
        elif req.kind == "mock":
            runner = MockLLMWorker(req.worker_id, req.tier, model=req.model or "", keypair=keypair)
        else:
            raise HTTPException(422, f"unknown worker kind {req.kind!r}")
        try:
            state.add_worker(runner, req.tier)
        except (RegistryError, RuntimeError, ValueError) as exc:
            raise HTTPException(409, str(exc))
        return state.registry.get(req.worker_id).model_dump(mode="json")

    @app.get("/api/probe")
    async def probe(base_url: str) -> dict[str, Any]:
        models = await probe_endpoint(base_url)
        return {"reachable": models is not None, "models": models or []}

    @app.get("/api/workers")
    async def workers() -> list[dict[str, Any]]:
        return [w.model_dump(mode="json") for w in state.registry.all()]

    @app.get("/api/provenance")
    async def provenance(limit: int = 100) -> dict[str, Any]:
        check = state.provenance.verify_chain(
            lambda wid: (r.public_key_b64 if (r := state.registry.get(wid)) else None)
        )
        entries = state.provenance.entries()[-limit:]
        return {
            "chain": check.model_dump(mode="json"),
            "head_hash": state.provenance.head_hash(),
            "total": len(state.provenance),
            "entries": [e.model_dump(mode="json") for e in entries],
        }

    # -- routing / learning -----------------------------------------------------------

    @app.get("/api/matrix")
    async def matrix() -> dict[str, Any]:
        return state.matrix.as_dict()

    @app.get("/api/playbook")
    async def playbook() -> list[dict[str, Any]]:
        return [b.model_dump(mode="json") for b in state.playbook.bullets(include_deprecated=True)]

    @app.get("/api/failures")
    async def failures() -> dict[str, Any]:
        return state.learning.stats.as_dict()

    # -- observability -------------------------------------------------------------------

    @app.get("/api/traces")
    async def traces() -> list[str]:
        return store().traces()

    @app.get("/api/spans")
    async def spans(trace_id: Optional[str] = None, limit: int = 200) -> list[dict[str, Any]]:
        items = store().by_trace(trace_id) if trace_id else store().all()
        return [
            {
                "name": s.name,
                "span_id": s.span_id,
                "parent_id": s.parent_id,
                "trace_id": s.trace_id,
                "start_ns": s.start_ns,
                "duration_ms": s.duration_ms,
                "status": s.status,
                "attributes": s.attributes,
                "events": s.events,
            }
            for s in items[-limit:]
        ]

    return app
