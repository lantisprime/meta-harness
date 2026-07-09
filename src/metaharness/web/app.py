"""FastAPI app: JSON API + single-page dashboard over the whole harness.

Everything the dashboard shows is the harness's real state — live spans from the
OTel store, the actual provenance chain (verified on every request), the real
capability matrix the router routes with, the playbook the learning loop curates.
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, Response
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


class TuneRequest(BaseModel):
    suite: str = "mixed"
    rounds: int = 6
    k: int = 3
    proposer: str = "rule"   # 'rule' | 'llm' — llm reads raw traces with the frontier agent


class ApprovalDecision(BaseModel):
    approved: bool = True


class CoverageRequest(BaseModel):
    n: int = 6


class AdviseRequest(BaseModel):
    page: str                 # 'tuning' | 'goal'
    subject: str = ""         # candidate id, or the user's raw goal text
    suite: str = ""


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

    @app.on_event("shutdown")
    async def _flush_persistent_state() -> None:
        """Force any debounced capability-matrix observations out to disk on a
        clean shutdown — the routing evidence earned in the run's final second
        must survive the restart, not sit unpersisted behind the debounce."""
        state.matrix.flush()

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

    _tuning_running: set[str] = set()

    def _tuning_base(runner):
        """The runner underneath the TUNING layer only. Params-built wrappers
        record their base as `_tuning_base` when applied, so re-applying params
        replaces exactly that layer — user-selected wrappers (e.g. serve
        --critique) underneath are preserved, and stacks never pile up."""
        return getattr(runner, "_tuning_base", runner)

    def _tuning_suite_dirs() -> list:
        """Real ledger directories under the optimization root — symlinks that
        resolve outside the root are refused (GET must never read, and later
        writes must never land, outside the harness's own store)."""
        from pathlib import Path

        if state.optimization_root is None:
            return []
        root = Path(state.optimization_root)
        if not root.is_dir():
            return []
        resolved_root = root.resolve()
        dirs = []
        for p in sorted(root.iterdir()):
            if not p.is_dir():
                continue
            resolved = p.resolve()
            if resolved != resolved_root and resolved.is_relative_to(resolved_root):
                dirs.append(p)
        return dirs

    @app.get("/api/optimization")
    async def optimization() -> list[dict[str, Any]]:
        """Harness-tuning ledgers, one entry per suite — read fresh from disk
        every poll so a CLI-run search shows up live. Strictly read-only."""
        from metaharness.optimization import CandidateLedger
        from metaharness.optimization.findings import derive_findings

        import json as _json
        from pathlib import Path

        active_suite = None
        if state.optimization_root is not None:
            active_path = Path(state.optimization_root) / "active.json"
            if active_path.is_file():
                active_suite = _json.loads(active_path.read_text(encoding="utf-8")).get("suite")

        out: list[dict[str, Any]] = []
        listed = {d.name: d for d in _tuning_suite_dirs()}
        # a just-started search must show up IMMEDIATELY, even before its
        # first candidate (minutes of real inference) creates the suite dir
        for name in sorted(set(listed) | _tuning_running):
            suite_dir = listed.get(name)
            if suite_dir is None:
                out.append({"suite": name, "running": True, "active": False,
                            "candidates": [], "frontier": [], "promoted": None,
                            "pending": None, "report": None, "findings": []})
                continue
            ledger = CandidateLedger(suite_dir)
            if not ledger.candidates() and name not in _tuning_running:
                continue
            frontier = [c.id for c in ledger.frontier()]
            report = ledger.load_report()
            candidates = []
            for c in ledger.candidates():
                entry: dict[str, Any] = {
                    "id": c.id, "parent": c.parent, "status": c.status,
                    "hypothesis": c.hypothesis, "rejected_reason": c.rejected_reason,
                    "frontier": c.id in frontier, "created_at": c.created_at,
                }
                if c.scores is not None:
                    entry["scores"] = {**c.scores.model_dump(),
                                       "tokens_total": c.scores.tokens_total}
                # code-space candidates carry a frozen artifact — surface its
                # canonical path so the dashboard can badge it (knob-only
                # candidates omit the key entirely).
                if c.params is not None and c.params.code_ref:
                    entry["code_ref"] = c.params.code_ref
                candidates.append(entry)
            out.append({
                "suite": suite_dir.name,
                "running": suite_dir.name in _tuning_running,
                "active": suite_dir.name == active_suite,
                "candidates": candidates,
                "frontier": frontier,
                "promoted": ledger.promoted_info(),
                "pending": ledger.pending_info(),
                "report": report,
                "findings": derive_findings(ledger, report),
            })
        return out

    @app.post("/api/optimization/runs", status_code=202)
    async def start_tuning(req: TuneRequest) -> dict[str, Any]:
        """Kick off a harness-tuning search in the background against the wired
        small-tier worker. Gate-passing winners park as pending promotions —
        the WebUI never auto-rewires the live harness."""
        from pathlib import Path

        from metaharness.optimization import (
            CandidateLedger,
            HarnessOptimizer,
            LLMProposer,
            RuleProposer,
            search_and_holdout,
        )

        if state.optimization_root is None or state.router is None:
            raise HTTPException(409, "harness not wired for tuning")
        if req.suite in _tuning_running:
            raise HTTPException(409, f"a search is already running for {req.suite!r}")
        try:
            search, holdout = search_and_holdout(
                req.suite, extras_dir=Path(state.optimization_root) / req.suite
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        if req.proposer == "llm":
            # the frontier agent does the counterfactual diagnosis over raw
            # traces — the paper-shaped proposer, incl. prompt-directive ideas
            proposer = LLMProposer(state.planner_runner(), budget=state.budget)
        elif req.proposer == "rule":
            proposer = RuleProposer()
        elif req.proposer == "code":
            # a real coding agent greps the raw ledger and stages code/knob
            # deltas; it authenticates itself via its own CLI. Requires a coding
            # CLI on the server host — a clean 422 (not a 500) when none exists.
            from metaharness.harness import (
                CLI_ADAPTERS,
                CodingAgentWorker,
                available_clis,
            )
            from metaharness.optimization import CodeProposer

            clis = available_clis()
            if not clis:
                raise HTTPException(
                    422,
                    "proposer 'code' needs a coding CLI on the server host; none "
                    f"found (supported: {', '.join(sorted(CLI_ADAPTERS))})",
                )
            cli_name, cli_path = next(iter(clis.items()))
            proposer = CodeProposer(
                CodingAgentWorker("opt-code-proposer", cli=cli_name, binary=cli_path),
                budget=state.budget,
            )
        else:
            raise HTTPException(422, f"unknown proposer {req.proposer!r}")
        small = state.router.pool(Tier.SMALL)
        wired = small[0] if small else next(iter(state.router.pools.values()))[0]
        target = _tuning_base(wired)
        ledger = CandidateLedger(Path(state.optimization_root) / req.suite)
        seed = ledger.promoted_params()  # tune from what's live, not from scratch
        optimizer = HarnessOptimizer(
            lambda: target, proposer, search, holdout, ledger,
            k=req.k, seed_params=seed, auto_promote=False, budget=state.budget,
        )
        _tuning_running.add(req.suite)

        async def _run() -> None:
            try:
                await optimizer.optimize(rounds=req.rounds)
            except Exception as exc:  # a crashed search must be loud in the card
                ledger.save_report({
                    "rounds_run": 0, "stopped": "error", "seed_id": "", "best_id": "",
                    "frontier": [], "gate": None, "promoted": False, "pending": None,
                    "notes": [f"search crashed: {type(exc).__name__}: {exc}"],
                })
            finally:
                _tuning_running.discard(req.suite)

        asyncio.get_running_loop().create_task(_run())
        return {"suite": req.suite, "status": "running", "target": target.worker_id}

    @app.post("/api/optimization/{suite}/coverage")
    async def extend_suite(suite: str, req: CoverageRequest) -> dict[str, Any]:
        """Grow a suite with frontier-agent-generated questions. Every item
        must be scoreable; arithmetic answers are recomputed exactly, never
        trusted from the generator. A mislabeled non-math item biases both
        sides of the paired gate equally, so comparisons stay fair."""
        import json as _json
        from pathlib import Path

        from metaharness.core.types import Task, TaskType
        from metaharness.harness.enrichment import SchemaGuard
        from metaharness.harness.sandbox import SandboxError, eval_arithmetic
        from metaharness.optimization.suites import (
            load_extras,
            save_extras,
            search_and_holdout,
        )

        if state.optimization_root is None:
            raise HTTPException(409, "harness not wired for tuning")
        try:
            builtin_search, _ = search_and_holdout(suite)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        try:
            runner = state.planner_runner()
        except RuntimeError as exc:
            raise HTTPException(409, str(exc))

        allowed_types = {t.task_type for t in builtin_search}
        examples = [t.model_dump(include={"task_type", "objective", "inputs", "success_check"})
                    for t in builtin_search[:3]]
        task = Task(
            task_type=TaskType.GENERAL,
            objective=(
                f"Write {req.n} NEW evaluation questions in the same JSON shape as the "
                "examples in the inputs — same domains, HARDER difficulty, fresh content "
                "(never repeat an example). Every item must have task_type, objective, "
                "inputs, and success_check with an exact 'equals' answer. Return JSON: "
                '{"tasks": [...]}.'
            ),
            inputs={"examples": _json.dumps(examples)},
            output_schema={"type": "object", "required": ["tasks"],
                           "properties": {"tasks": {"type": "array"}}},
        )
        result = await SchemaGuard(runner).run(task)
        if result.error:
            raise HTTPException(502, f"question generation failed: {result.error}")

        suite_dir = Path(state.optimization_root) / suite
        existing = load_extras(suite_dir)
        seen = {(t.objective, _json.dumps(t.inputs, sort_keys=True, default=str))
                for t in [*builtin_search, *existing]}
        added: list[Task] = []
        for raw in (result.output or {}).get("tasks", []):
            try:
                candidate = Task.model_validate(raw)
            except Exception:
                continue
            check = candidate.success_check or {}
            if candidate.task_type not in allowed_types or "equals" not in check:
                continue
            if candidate.task_type == TaskType.ARITHMETIC:
                expr = candidate.inputs.get("expression")
                try:
                    check["equals"] = eval_arithmetic(str(expr))  # never trust the generator's math
                except SandboxError:
                    continue
            key = (candidate.objective, _json.dumps(candidate.inputs, sort_keys=True, default=str))
            if key in seen:
                continue
            seen.add(key)
            added.append(candidate)
        if not added:
            raise HTTPException(502, "the generator produced no usable questions — try again")
        save_extras(suite_dir, [*existing, *added])
        return {"suite": suite, "added": len(added), "total_extras": len(existing) + len(added)}

    @app.post("/api/optimization/{suite}/approval")
    async def resolve_tuning_approval(suite: str, req: ApprovalDecision) -> dict[str, Any]:
        """Human gate on a pending promotion. Approve → promoted.json + the
        live small-tier runner is rewrapped immediately; reject → cleared."""
        from metaharness.optimization import CandidateLedger, HarnessParams

        suite_dir = next((d for d in _tuning_suite_dirs() if d.name == suite), None)
        if suite_dir is None:
            raise HTTPException(404, f"unknown suite {suite!r}")
        ledger = CandidateLedger(suite_dir)
        pending = ledger.pending_info()
        if pending is None:
            raise HTTPException(409, "no promotion is awaiting approval")
        ledger.clear_pending()
        if not req.approved:
            return {"suite": suite, "approved": False, "candidate": pending["candidate"]}
        ledger.promote(pending["candidate"])
        # durable pointer so the NEXT serve boot replays this exact approval,
        # whatever the suite — not just 'mixed'
        import json as _json
        from pathlib import Path

        from metaharness.core.types import now
        Path(state.optimization_root).mkdir(parents=True, exist_ok=True)
        (Path(state.optimization_root) / "active.json").write_text(
            _json.dumps({"suite": suite, "candidate": pending["candidate"],
                         "params": pending["params"], "approved_at": now()}, indent=1),
            encoding="utf-8",
        )
        applied = False
        if state.router is not None and state.router.pool(Tier.SMALL):
            params = HarnessParams.model_validate(pending["params"])
            small = state.router.pools[Tier.SMALL]
            base = _tuning_base(small[0])
            # code-backed params resolve their artifact under the suite ledger
            # root (== suite_dir), the same dir this approval's params live in.
            wrapped = params.build(base, ledger_root=suite_dir)
            wrapped._tuning_base = base
            small[0] = wrapped
            applied = True
        return {"suite": suite, "approved": True,
                "candidate": pending["candidate"], "applied_live": applied}

    @app.post("/api/advise")
    async def advise_endpoint(req: AdviseRequest) -> dict[str, Any]:
        """AI companion: an advisory read of verified state, through the
        harness's own most capable runner. See web/advisor.py for the rules."""
        from metaharness.optimization import CandidateLedger
        from metaharness.web.advisor import AdvisorError, advise

        try:
            runner = state.planner_runner()
        except RuntimeError as exc:
            raise HTTPException(409, str(exc))

        if req.page == "goal":
            question = "the goal they are about to run — rewrite it into a sharp delegation contract"
            context = {
                "user_goal": req.subject,
                "hint": "propose action prefill_goal with params {goal, context, "
                        "workflow_type}; the goal must name a checkable done-signal",
                "workflow_types": ["", "software_engineering", "incident_response"],
            }
        elif req.page == "tuning":
            suite_dir = next((d for d in _tuning_suite_dirs() if d.name == req.suite), None)
            if suite_dir is None:
                raise HTTPException(404, f"unknown suite {req.suite!r}")
            ledger = CandidateLedger(suite_dir)
            cand = ledger.get(req.subject)
            if cand is None:
                raise HTTPException(404, f"unknown candidate {req.subject!r}")
            question = f"tuning experiment {cand.id} on the {req.suite} suite — explain the result"
            context = {
                "candidate": cand.model_dump(),
                "on_frontier": cand.id in {c.id for c in ledger.frontier()},
                "raw_failure_traces": ledger.failure_traces(cand.id, limit=4),
                "report": ledger.load_report(),
            }
        else:
            raise HTTPException(422, f"unknown advise page {req.page!r}")
        try:
            return await advise(runner, question, context, budget=state.budget)
        except AdvisorError as exc:
            raise HTTPException(502, str(exc))

    @app.get("/api/runs")
    async def runs() -> list[dict[str, Any]]:
        if state.engine is None:
            return []
        out = []
        for r in state.engine.runs():
            rec = r.model_dump(mode="json")
            # The journal is the durable clock: first entry = run started,
            # last entry = most recent activity.
            try:
                entries = state.engine.journal(r.run_id).entries()
            except KeyError:
                entries = []
            rec["started_at"] = entries[0].at if entries else None
            rec["updated_at"] = entries[-1].at if entries else None
            out.append(rec)
        return out

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

    @app.get("/api/runs/{run_id}/package")
    async def run_package(run_id: str) -> Response:
        """Everything the run produced, as one zip: manifest, workflow spec,
        journal, per-step outputs, and the files changed under each step's
        recorded workspace root (capped; omissions listed in the manifest).
        Works for failed runs too — a failure package is a bug report."""
        from metaharness.workflows.package import build_package_bytes

        try:
            spec = state.engine._runs[run_id][0]
            run_state = state.engine.state(run_id)
            journal = state.engine.journal(run_id)
        except (KeyError, AttributeError):
            raise HTTPException(404, f"unknown run {run_id}")
        payload = build_package_bytes(spec, run_state, journal.entries())
        return Response(
            content=payload,
            media_type="application/zip",
            headers={"Content-Disposition":
                     f'attachment; filename="{run_id}-package.zip"'},
        )

    def _advance_in_background(run_id: str) -> None:
        async def _run() -> None:
            try:
                await state.engine.advance(run_id)
            except Exception as exc:  # never crashes the app — but journal it,
                # or the run sits in "running" forever with no trail
                try:
                    state.engine.journal(run_id).append(
                        "run.advance_error", run_id,
                        payload={"error": f"{type(exc).__name__}: {exc}"[:300]},
                    )
                except Exception:
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
            return template.instantiate(req.goal), f"template:{template.id}", None
        return await plan_workflow(req.goal, state.planner_runner(), context,
                                   tools=state.tools)

    @app.post("/api/runs/{run_id}/followup")
    async def plan_run_followup(run_id: str) -> dict[str, Any]:
        """Rework mechanism for a finished run (e.g. review said NO-SHIP):
        the frontier planner reads what actually happened and proposes a
        remediation workflow. Returned for HUMAN review in the plan editor —
        it never starts on its own, and its gated steps keep their gates."""
        from metaharness.workflows.planner import plan_followup, summarize_run

        try:
            run_state = state.engine.state(run_id)
            spec = self_spec = state.engine._runs[run_id][0]
        except (KeyError, AttributeError):
            raise HTTPException(404, f"unknown run {run_id}")
        if run_state.status.value not in ("completed", "failed"):
            raise HTTPException(409, "follow-up planning needs a finished run")
        goal = str(run_state.context.get("goal") or run_state.workflow)
        followup, source, fallback_reason = await plan_followup(
            goal, spec, run_state, state.planner_runner(),
            context=run_state.context, tools=state.tools)
        state.provenance.append(
            "orchestrator", "workflow.followup_planned",
            {"source_run": run_id, "plan_source": source,
             "fallback_reason": fallback_reason,
             "workflow": followup.name, "steps": [s.id for s in followup.steps]},
            keypair=state.orchestrator_keypair,
        )
        return {"workflow": followup.model_dump(mode="json"),
                "plan_source": source,
                "fallback_reason": fallback_reason,
                "prior_summary": summarize_run(self_spec, run_state)}

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
        spec, source, fallback_reason = await _plan(req, context)
        return {"workflow": spec.model_dump(mode="json"), "plan_source": source,
                "fallback_reason": fallback_reason}

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
        spec, source, fallback_reason = await _plan(req, context)
        state.provenance.append(
            "orchestrator", "workflow.planned",
            {"goal": req.goal[:300], "source": source, "workflow": spec.name,
             "fallback_reason": fallback_reason,
             "steps": [s.id for s in spec.steps]},
            keypair=state.orchestrator_keypair,
        )
        run_state = state.engine.start(spec, context=context)
        run_state = await state.engine.advance(run_state.run_id)
        return {
            "run": run_state.model_dump(mode="json"),
            "plan_source": source,
            "fallback_reason": fallback_reason,
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
        """Retire a worker: deactivate its identity, remove it from every tier
        pool that holds it (a tier keeps serving while other members remain, and
        its key drops only when the pool empties), and drop its durable
        definition. The provenance it signed stays."""
        record = state.registry.get(worker_id)
        if record is None:
            raise HTTPException(404, f"unknown worker {worker_id}")
        if worker_id == "orchestrator":
            raise HTTPException(422, "the orchestrator cannot retire itself")
        state.registry.deactivate(worker_id)
        if state.router is not None:
            for tier, members in list(state.router.pools.items()):
                if not any(m.worker_id == worker_id for m in members):
                    continue
                remaining = [m for m in members if m.worker_id != worker_id]
                if remaining:
                    state.router.pools[tier] = remaining
                else:
                    del state.router.pools[tier]
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

    @app.get("/api/routing")
    async def routing() -> dict[str, Any]:
        """Per-tier pool membership plus routed-to tallies, so the UI can show
        who serves each tier and where traffic is actually landing."""
        if state.router is None:
            return {}
        matrix = state.matrix.as_dict()
        evidence = state.router.route_evidence()
        out: dict[str, Any] = {}
        for tier, members in state.router.pools.items():
            out[tier.value] = {
                "members": [
                    {
                        "worker_id": m.worker_id,
                        "model": m.model,
                        "display_name": (
                            r.display_name if (r := state.registry.get(m.worker_id))
                            else m.worker_id
                        ),
                        "skills": matrix.get(m.model, {}),
                    }
                    for m in members
                ],
                "routed": evidence.get(tier.value, {}),
            }
        return out

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
