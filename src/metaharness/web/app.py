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

from metaharness.config import PROVIDER_CATALOG, AgentConfig, MCPServerConfig, is_masked
from metaharness.core.types import Task, TaskType, Tier
from metaharness.factory import build_agent_runner
from metaharness.harness.coding import CLI_KEY_HINTS, available_clis, list_cli_models
from metaharness.harness.subscription import SUBSCRIPTION_CLIS, SubscriptionWorker, subscription_status
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
    workflow_type: str = ""              # named template ("" = free-form planner)


class AddWorkerRequest(BaseModel):
    worker_id: str
    tier: Tier
    kind: str = "openai_compat"          # openai_compat | coding_cli | mock
    base_url: str = ""                    # direct endpoint (openai_compat)
    provider: str = ""                    # or: configured-provider reference
    api_key: str = ""                     # inline key — stored under the provider
    model: str = ""
    system_prompt: str = ""
    task_types: list[str] = []
    temperature: float = 0.2
    thinking: Optional[bool] = None
    max_tokens: Optional[int] = 4000
    cli: str = ""                         # coding_cli: pi | codex | opencode | claude
    persist: bool = True                  # survive restarts via config.json


class ProbeRequest(BaseModel):
    """POST body so API keys never appear in a URL query string."""
    base_url: str = ""
    provider: str = ""                    # stored-provider ref (uses its key)
    api_key: str = ""                     # inline pre-save key; masked echo -> stored


class TestWorkerRequest(BaseModel):
    """One live call proving a candidate agent's endpoint+key+model work,
    BEFORE anything is saved or registered — the wizard's 'Test' button."""
    kind: str = "openai_compat"
    provider: str = ""
    base_url: str = ""
    api_key: str = ""                     # masked echo → stored key is used
    model: str = ""
    system_prompt: str = ""
    cli: str = ""


def create_app(state: HarnessState) -> FastAPI:
    app = FastAPI(title="metaharness", version="0.1.0")
    app.state.harness = state

    @app.on_event("startup")
    async def _load_mcp_tools() -> None:
        """Mirror every enabled configured MCP server's tools into the registry.
        A server that fails to connect is reported loudly and skipped — its
        tools are absent, never silently stubbed."""
        if not state.config.mcp_servers:
            return
        from metaharness.tools import load_mcp_tools
        try:
            report = await load_mcp_tools(state.tools, state.config)
        except RuntimeError as exc:  # mcp package missing
            print(f"  MCP: {exc}")
            return
        for name, entry in report.items():
            status = f"{entry['tools']} tool(s)" if entry.get("ok") else f"FAILED: {entry['detail']}"
            print(f"  MCP {name}: {status}")

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

    async def _plan(req: GoalRequest, context: dict[str, Any]):
        """Template type -> deterministic spine (no LLM); else free-form planner."""
        if req.workflow_type:
            from metaharness.workflows.templates import get_template
            template = get_template(req.workflow_type)
            if template is None:
                raise HTTPException(422, f"unknown workflow_type {req.workflow_type!r}")
            return template.instantiate(req.goal), f"template:{template.id}"
        return await plan_workflow(req.goal, state.planner_runner(), context,
                                   tools=state.tools)

    @app.post("/api/workflows/validate")
    async def validate_workflow(req: StartRunRequest) -> dict[str, Any]:
        """Validate a hand-written or hand-edited workflow WITHOUT running it:
        the plan editor's save/apply path. Returns the normalized workflow
        plus its YAML form (for the raw editor); a bad spec 422s with the
        validator's exact complaint."""
        import yaml

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
        data = spec.model_dump(mode="json")
        return {"workflow": data,
                "yaml": yaml.safe_dump(data, sort_keys=False, allow_unicode=True)}

    @app.post("/api/plans")
    async def preview_plan(req: GoalRequest) -> dict[str, Any]:
        """Plan a workflow from a goal WITHOUT starting it — the wizard's review
        step. The confirmed plan comes back via POST /api/runs {workflow: ...}."""
        if state.engine is None:
            raise HTTPException(503, "engine not wired")
        if not req.goal.strip():
            raise HTTPException(422, "goal is empty")
        context = {"goal": req.goal, **req.context}
        spec, source = await _plan(req, context)
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
        spec, source = await _plan(req, context)
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

    def _agent_config_from(req: AddWorkerRequest) -> AgentConfig:
        """Normalize an add-worker request into a durable AgentConfig. An
        inline api_key is stored under a provider entry (keys live in exactly
        one place), auto-named after the worker when no ref was given."""
        provider = req.provider
        if req.api_key and not is_masked(req.api_key):
            provider = provider or f"{req.worker_id}-endpoint"
            patch: dict[str, Any] = {"api_key": req.api_key}
            if req.base_url:
                patch["base_url"] = req.base_url
            state.config.apply_provider_update(provider, patch)
        return AgentConfig(
            worker_id=req.worker_id, kind=req.kind, tier=req.tier.value,
            provider=provider, base_url="" if provider else req.base_url,
            model=req.model, system_prompt=req.system_prompt,
            task_types=req.task_types, temperature=req.temperature,
            max_tokens=req.max_tokens, thinking=req.thinking, cli=req.cli,
        )

    @app.post("/api/workers", status_code=201)
    async def add_worker(req: AddWorkerRequest) -> dict[str, Any]:
        """Configure a new agent: build the runner, admit its identity through
        the registration ceremony, point the tier's routing slot at it, and
        (by default) persist the definition so it survives restarts."""
        if req.worker_id in ("orchestrator", "config-test"):
            raise HTTPException(422, f"worker id {req.worker_id!r} is reserved")
        agent = _agent_config_from(req)
        if req.kind == "openai_compat":
            base_url, api_key = state.config.resolve_endpoint(agent)
            if not base_url or not req.model:
                raise HTTPException(422, "openai_compat workers need base_url/provider and model")
            models = await probe_endpoint(base_url, api_key=api_key)
            if models is None and not api_key:
                raise HTTPException(422, f"no OpenAI-compatible endpoint at {base_url}")
            if models is not None and req.model not in models:
                raise HTTPException(
                    422, f"model {req.model!r} not served there; found: {models[:10]}")
        elif req.kind == "coding_cli":
            if req.cli not in available_clis():
                raise HTTPException(
                    422, f"coding CLI {req.cli!r} not found on PATH "
                         f"(installed: {sorted(available_clis()) or 'none'})")
        elif req.kind == "subscription_cli":
            status = subscription_status().get(req.cli)
            if status is None or not status["installed"]:
                raise HTTPException(
                    422, f"subscription CLI {req.cli!r} not installed "
                         f"(known: {sorted(SUBSCRIPTION_CLIS)})")
        try:
            runner = build_agent_runner(agent, state.config)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        try:
            state.add_worker(runner, req.tier)
        except (RegistryError, RuntimeError, ValueError) as exc:
            raise HTTPException(409, str(exc))
        if req.persist:
            state.config.upsert_agent(agent)
            state.save_config()
        return state.registry.get(req.worker_id).model_dump(mode="json")

    @app.delete("/api/workers/{worker_id}")
    async def remove_worker(worker_id: str) -> dict[str, Any]:
        """Retire a worker: deactivate its identity, free its routing slot,
        and drop its durable definition. The provenance it signed stays."""
        record = state.registry.get(worker_id)
        if record is None:
            raise HTTPException(404, f"unknown worker {worker_id}")
        if worker_id == "orchestrator":
            raise HTTPException(422, "the orchestrator cannot retire itself")
        state.registry.deactivate(worker_id)
        if state.router is not None:
            for tier, runner in list(state.router.runners.items()):
                if runner.worker_id == worker_id:
                    del state.router.runners[tier]
        removed = state.config.remove_agent(worker_id)
        if removed:
            state.save_config()
        return {"worker_id": worker_id, "deactivated": True, "config_removed": removed}

    @app.post("/api/test_worker")
    async def test_worker(req: TestWorkerRequest) -> dict[str, Any]:
        """Live one-shot check for the wizard, run before anything persists."""
        import time
        import uuid

        if req.kind == "coding_cli":
            path = available_clis().get(req.cli)
            return {"ok": path is not None,
                    "detail": path or f"'{req.cli}' not found on PATH"}
        if req.kind == "subscription_cli":
            status = subscription_status().get(req.cli)
            if status is None or not status["installed"]:
                return {"ok": False, "detail": f"'{req.cli}' not installed"}
            candidate = SubscriptionWorker("config-test", cli=req.cli,
                                           model=req.model, timeout_s=90.0)
            probe_task = Task(id=f"test-{uuid.uuid4().hex[:8]}",
                              task_type=TaskType.GENERAL,
                              objective="Reply with exactly: OK")
            started = time.perf_counter()
            result = await candidate.run(probe_task)
            return {"ok": result.error is None,
                    "latency_ms": round((time.perf_counter() - started) * 1000),
                    "error": result.error,
                    "reply": (result.raw_text or "")[:200]}
        if req.kind == "mock":
            return {"ok": True, "detail": "mock workers always answer"}
        base_url, api_key = req.base_url, req.api_key
        if req.provider and req.provider in state.config.providers:
            provider = state.config.providers[req.provider]
            base_url = base_url or provider.base_url
            if not api_key or is_masked(api_key):
                api_key = provider.plain_key()
        if not base_url or not req.model:
            raise HTTPException(422, "test needs base_url/provider and model")
        candidate = OpenAICompatWorker(
            "config-test", base_url=base_url, model=req.model, api_key=api_key,
            system_prompt=req.system_prompt, max_tokens=32, timeout_s=30.0,
        )
        probe_task = Task(id=f"test-{uuid.uuid4().hex[:8]}", task_type=TaskType.GENERAL,
                          objective="Reply with exactly: OK")
        started = time.perf_counter()
        result = await candidate.run(probe_task)
        latency_ms = round((time.perf_counter() - started) * 1000)
        return {"ok": result.error is None, "latency_ms": latency_ms,
                "model": req.model, "error": result.error,
                "reply": (result.raw_text or "")[:200]}

    # -- durable configuration ------------------------------------------------------

    @app.get("/api/config")
    async def get_config() -> dict[str, Any]:
        """Everything the Settings view needs; keys always masked."""
        return {
            **state.config.public_dict(),
            "catalog": PROVIDER_CATALOG,
            "coding_clis": available_clis(),
            "cli_key_hints": CLI_KEY_HINTS,
            "subscriptions": subscription_status(),
        }

    @app.post("/api/config/providers")
    async def update_provider(patch: dict[str, Any]) -> dict[str, Any]:
        pid = str(patch.pop("id", "")).strip()
        if not pid:
            raise HTTPException(422, "provider patch needs an id")
        state.config.apply_provider_update(pid, patch)
        state.save_config()
        return state.config.public_dict()["providers"][pid]

    @app.delete("/api/config/providers/{pid}")
    async def delete_provider(pid: str) -> dict[str, Any]:
        if pid not in state.config.providers:
            raise HTTPException(404, f"unknown provider {pid}")
        dependents = [a.worker_id for a in state.config.agents if a.provider == pid]
        if dependents:
            raise HTTPException(409, f"agents still use provider {pid!r}: {dependents}")
        del state.config.providers[pid]
        state.save_config()
        return {"deleted": pid}

    @app.post("/api/config/mcp")
    async def update_mcp_server(spec: dict[str, Any]) -> dict[str, Any]:
        try:
            server = MCPServerConfig.model_validate(spec)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        state.config.mcp_servers[server.name] = server
        state.save_config()
        return server.model_dump()

    @app.delete("/api/config/mcp/{name}")
    async def delete_mcp_server(name: str) -> dict[str, Any]:
        if name not in state.config.mcp_servers:
            raise HTTPException(404, f"unknown MCP server {name}")
        del state.config.mcp_servers[name]
        state.save_config()
        return {"deleted": name}

    @app.get("/api/probe")
    async def probe(base_url: str) -> dict[str, Any]:
        models = await probe_endpoint(base_url)
        return {"reachable": models is not None, "models": models or []}

    @app.post("/api/cli_models")
    async def cli_models(body: dict[str, Any]) -> dict[str, Any]:
        """Models a coding/subscription CLI can use — asked from the CLI
        itself when it supports listing (pi, opencode), static aliases
        otherwise. Feeds the agent wizard's model picker."""
        cli = str(body.get("cli", ""))
        try:
            models = await list_cli_models(cli)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        return {"cli": cli, "models": models}

    @app.post("/api/probe")
    async def probe_models(req: ProbeRequest) -> dict[str, Any]:
        """List the models a provider actually serves — feeds the wizards'
        model pickers so 'default model' is a choice, not a guess."""
        base_url, api_key = req.base_url, req.api_key
        if req.provider and req.provider in state.config.providers:
            stored = state.config.providers[req.provider]
            base_url = base_url or stored.base_url
            if not api_key or is_masked(api_key):
                api_key = stored.plain_key()
        if not base_url:
            raise HTTPException(422, "probe needs base_url or a configured provider")
        models = await probe_endpoint(base_url, api_key=api_key, timeout_s=8.0)
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

    @app.get("/api/workflow-types")
    async def workflow_types() -> list[dict[str, Any]]:
        from metaharness.workflows.templates import list_templates
        return list_templates()

    @app.get("/api/tools")
    async def tools() -> list[dict[str, Any]]:
        return [
            {"name": t.name, "description": t.description, "source": t.source}
            for t in state.tools.all()
        ]

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
