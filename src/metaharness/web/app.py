"""FastAPI app: JSON API + single-page dashboard over the whole harness.

Everything the dashboard shows is the harness's real state — live spans from the
OTel store, the actual provenance chain (verified on every request), the real
capability matrix the router routes with, the playbook the learning loop curates.
"""
from __future__ import annotations

import asyncio
from typing import Any, Literal, Optional

import re
from contextlib import contextmanager
from typing import Iterator

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field, ValidationError, model_validator

from metaharness.blueprints import (
    ArtifactRef,
    BlueprintAlreadyExistsError,
    BlueprintContent,
    BlueprintCatalog,
    BlueprintCatalogConflictError,
    BlueprintCorruptionError,
    BlueprintNotFoundError,
    BlueprintStoreError,
    InvalidRevisionError,
    RevisionConflictError,
    is_builtin_id,
    prepare_blueprint_run,
    resolve_blueprint_workflow,
    workflow_assignment_issues,
)
from metaharness.blueprints.models import StrictModel
from metaharness.config import (
    PROVIDER_CATALOG,
    AgentConfig,
    HarnessConfig,
    MCPServerConfig,
    is_masked,
)
from metaharness.core.types import Task, TaskType, Tier
from metaharness.evals.artifact_store import (
    EvalArtifactAlreadyExistsError,
    EvalArtifactCorruptionError,
    EvalArtifactNotFoundError,
    EvalArtifactStoreError,
)
from metaharness.evals.artifacts import EvaluationReportRef, SafeBlueprintPatch
from metaharness.evals.evaluator import (
    EvalReferenceMismatchError,
    EvaluationError,
    ExactSuiteEvaluator,
    SandboxedCaseRunner,
    UnsafeEvalRunnerError,
)
from metaharness.evals.models import EvalSuiteContent
from metaharness.evals.store import (
    EvalSuiteAlreadyExistsError,
    EvalSuiteArchivedError,
    EvalSuiteCorruptionError,
    EvalSuiteNotFoundError,
    EvalSuiteRevisionConflictError,
    EvalSuiteStoreError,
    InvalidEvalSuiteRevisionError,
)
from metaharness.evals.tuning import (
    TuningError,
    apply_tuning_proposal_to_draft,
    create_tuning_proposal,
)
from metaharness.factory import build_agent_runner
from metaharness.harness.coding import CLI_KEY_HINTS, available_clis, list_cli_models
from metaharness.harness.subscription import SUBSCRIPTION_CLIS, SubscriptionWorker, subscription_status
from metaharness.harness.local import OpenAICompatWorker, probe_endpoint
from metaharness.harness.workers import MockLLMWorker
from metaharness.identity.registry import RegistryError
from metaharness.observability.tracing import store
from metaharness.portable import PortableDeploymentOptions, PortableTarget, build_portable_package
from metaharness.web.dashboard import DASHBOARD_HTML
from metaharness.web.state import HarnessState
from metaharness.workflows.dsl import load_workflow
from metaharness.workflows.engine import RunArchiveConflict
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
    page: str                 # 'goal' | 'tuning' | 'routing' | 'failures' | 'playbook'
    subject: str = ""         # candidate id, or the user's raw goal text
    suite: str = ""


class StartRunRequest(BaseModel):
    workflow_yaml: str = ""
    workflow: Optional[dict[str, Any]] = None    # a reviewed plan, as JSON
    blueprint: Optional[ArtifactRef] = None      # exact blueprint version
    context: dict[str, Any] = {}
    wait: bool = True                             # False → run in background, poll


class BlueprintReadinessRequest(BaseModel):
    blueprint: ArtifactRef
    context: dict[str, Any] = Field(default_factory=dict)


class SecretBindingWriteRequest(StrictModel):
    name: str
    value: str


LOCAL_BLUEPRINT_OWNER = "local-user"


class CreateDraftRequest(StrictModel):
    blueprint_id: str
    content: Optional[BlueprintContent] = None
    base_version: Optional[int] = Field(default=None, ge=1, strict=True)

    @model_validator(mode="after")
    def _exactly_one_source(self) -> "CreateDraftRequest":
        if (self.content is None) == (self.base_version is None):
            raise ValueError("provide exactly one of content or base_version")
        return self


class UpdateDraftRequest(StrictModel):
    content: BlueprintContent
    expected_revision: int = Field(ge=1, strict=True)


class PublishRequest(StrictModel):
    expected_revision: int = Field(ge=1, strict=True)


class CreateEvalSuiteRequest(StrictModel):
    suite_id: str
    content: EvalSuiteContent


class UpdateEvalSuiteRequest(StrictModel):
    content: EvalSuiteContent
    expected_revision: int = Field(ge=1, strict=True)


class EvaluateBlueprintRequest(StrictModel):
    report_id: str
    eval_ref: ArtifactRef
    split: Literal["development", "validation"]
    runner: SandboxedCaseRunner


class TuneBlueprintRequest(StrictModel):
    proposal_id: str
    report_refs: list[EvaluationReportRef]
    patches: list[SafeBlueprintPatch]
    rationale: str
    human_approved: bool = False
    expected_revision: Optional[int] = Field(default=None, ge=1, strict=True)


class PortablePackageRequest(StrictModel):
    targets: list[PortableTarget] = Field(default_factory=lambda: ["local"])
    deployment_options: Optional[PortableDeploymentOptions] = None
    generated_at: Optional[int] = Field(default=None, ge=0, strict=True)


class ForkRequest(StrictModel):
    new_id: str
    source_version: int = Field(ge=1, strict=True)
    display_name: str = ""


class MetadataPatchRequest(BaseModel):
    display_name: str


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
    roles: list[str] = []
    capabilities: list[str] = []
    temperature: float = 0.2
    thinking: Optional[bool] = None
    max_tokens: Optional[int] = 4000
    cli: str = ""                         # coding_cli: pi | codex | opencode | claude
    # None = kind default (issue #2); bounds mirror AgentConfig.timeout_s
    # (issue #2 panel, Claude+codex+kimi P2 — gt=0 alone accepts +Infinity)
    timeout_s: Optional[float] = Field(default=None, gt=0, le=86400,
                                       allow_inf_nan=False)
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


_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _validate_slug(value: str, field: str) -> str:
    if not value or len(value) > 80 or not _SLUG_RE.fullmatch(value):
        raise HTTPException(
            422,
            f"{field} must be a lowercase slug of at most 80 characters"
        )
    return value


def _validate_display_name(value: str) -> str:
    if not value or not value.strip():
        raise HTTPException(422, "display name cannot be blank")
    return value


@contextmanager
def _blueprint_api(state: HarnessState) -> Iterator[None]:
    """Centralized error mapping for blueprint catalog endpoints."""
    if state.blueprint_store is None:
        raise HTTPException(503, "blueprint store not enabled")
    if state.blueprint_catalog is None:
        state.blueprint_catalog = BlueprintCatalog(state.blueprint_store)
    try:
        yield
    except BlueprintNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (
        BlueprintAlreadyExistsError,
        BlueprintCatalogConflictError,
        RevisionConflictError,
    ) as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(422, {
            "errors": [
                {key: error[key] for key in ("loc", "msg", "type")}
                for error in exc.errors(
                    include_url=False, include_context=False, include_input=False
                )
            ]
        }) from exc
    except (InvalidRevisionError, ValueError) as exc:
        # These domain errors are produced by trusted server-side validation and
        # never interpolate the submitted payload.
        raise HTTPException(422, str(exc)) from exc
    except BlueprintCorruptionError as exc:
        raise HTTPException(500, str(exc)) from exc
    except BlueprintStoreError as exc:
        raise HTTPException(409, str(exc)) from exc


@contextmanager
def _eval_api(state: HarnessState) -> Iterator[None]:
    """Fail-closed, sanitized mapping for eval and tuning artifact APIs."""
    if (
        state.eval_suite_store is None
        or state.evaluation_report_store is None
        or state.tuning_proposal_store is None
    ):
        raise HTTPException(503, "evaluation persistence is not enabled")
    try:
        yield
    except (EvalSuiteNotFoundError, EvalArtifactNotFoundError) as exc:
        raise HTTPException(404, str(exc)) from exc
    except (
        EvalSuiteAlreadyExistsError,
        EvalSuiteArchivedError,
        EvalSuiteRevisionConflictError,
        EvalArtifactAlreadyExistsError,
    ) as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(422, {
            "errors": [
                {key: error[key] for key in ("loc", "msg", "type")}
                for error in exc.errors(
                    include_url=False, include_context=False, include_input=False
                )
            ]
        }) from exc
    except (
        InvalidEvalSuiteRevisionError,
        EvalReferenceMismatchError,
        UnsafeEvalRunnerError,
        EvaluationError,
        TuningError,
        ValueError,
    ) as exc:
        raise HTTPException(422, str(exc)) from exc
    except (EvalSuiteCorruptionError, EvalArtifactCorruptionError) as exc:
        raise HTTPException(500, str(exc)) from exc
    except (EvalSuiteStoreError, EvalArtifactStoreError) as exc:
        raise HTTPException(409, str(exc)) from exc


def _load_runnable_blueprint_version(state: HarnessState, ref: ArtifactRef) -> dict[str, Any]:
    """Capture a built-in or active-owned exact version for new execution.

    Historical GETs deliberately use the catalog's unrestricted exact resolver;
    readiness and run intake use this active gate before any journal is created.
    """
    if is_builtin_id(ref.id):
        catalog = state.blueprint_catalog or BlueprintCatalog(state.blueprint_store)
        version = catalog.get_version(ref)
    else:
        version = state.blueprint_store.get_active_version(ref)
    return version.model_dump(mode="json")


def create_app(state: HarnessState) -> FastAPI:
    # Config may be assigned after HarnessState construction (serve/portable
    # boot). Hydrate before any readiness request can observe binding state.
    state.hydrate_secret_bindings()
    app = FastAPI(title="metaharness", version="0.1.0")
    app.state.harness = state

    if state.engine is not None:
        def _tool_is_current(name: str) -> bool:
            tool = state.tools.get(name)
            if tool is None:
                return False
            if not tool.source.startswith("mcp:"):
                return True
            from metaharness.tools.mcp import mcp_config_fingerprint

            server_name = tool.source[len("mcp:"):]
            server = state.config.mcp_servers.get(server_name)
            status = state.mcp_load_status.get(server_name)
            return bool(
                server is not None and server.enabled and status is not None
                and status.get("status") == "loaded"
                and status.get("fingerprint") == mcp_config_fingerprint(server)
            )

        state.engine.tool_available = _tool_is_current

    @app.exception_handler(RequestValidationError)
    async def _sanitized_request_validation(_request, exc: RequestValidationError):
        """Never let Pydantic's input/ctx fields reflect credentials or values."""
        errors = [
            {key: error[key] for key in ("loc", "msg", "type")}
            for error in exc.errors()
        ]
        return JSONResponse(status_code=422, content={"detail": errors})

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
        state.mcp_load_status.update(report)
        for name, entry in report.items():
            status = f"{entry['tools']} tool(s)" if entry.get("ok") else f"FAILED: {entry['detail']}"
            print(f"  MCP {name}: {status}")

    @app.on_event("shutdown")
    async def _flush_persistent_state() -> None:
        """Force any debounced capability-matrix observations out to disk on a
        clean shutdown — the routing evidence earned in the run's final second
        must survive the restart, not sit unpersisted behind the debounce.

        F9e (probe reviews 2026-07-09, M2.7): flush() does blocking disk I/O, so it
        runs in a worker thread rather than stalling the event loop during shutdown."""
        await asyncio.to_thread(state.matrix.flush)

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
        from metaharness.harness.sandbox import eval_arithmetic
        from metaharness.optimization.suites import (
            append_extras,
            check_value_ok,
            dedupe_key,
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
        # Issue #7: no `load_extras` here — a stale in-request read would be
        # clobbered by (or itself clobber) a concurrent writer. In-request dedupe
        # only needs to know about the builtins; existing extras + any concurrent
        # writer's additions are handled by append_extras's own locked fresh read.
        seen = {dedupe_key(t.objective, t.inputs) for t in builtin_search}
        candidates: list[Task] = []
        for raw in (result.output or {}).get("tasks", []):
            try:
                candidate = Task.model_validate(raw)
            except Exception:
                continue
            check = candidate.success_check or {}
            # subset gate: at least {equals}, at most {equals, tol}, and value-hardened.
            # (Accepting any check merely *containing* equals let a mixed primary key like
            # {"equals":x,"one_of":[...]} slip through and get silently equals-scored.)
            if (candidate.task_type not in allowed_types
                    or not ({"equals"} <= set(check) <= {"equals", "tol"})
                    or not check_value_ok(check)):
                continue
            if candidate.task_type == TaskType.ARITHMETIC:
                expr = candidate.inputs.get("expression")
                try:
                    recomputed = eval_arithmetic(str(expr))  # never trust the generator's math
                except Exception:  # a div-by-zero / evaluator crash drops the task, never 500s
                    continue
                # panel (kimi): build a fresh check and re-validate BEFORE persisting — don't
                # mutate candidate.success_check in place, mirroring harvest.py's copy pattern so
                # a recomputed bigint/inf can never partially persist.
                new_check = {**check, "equals": recomputed}
                if not check_value_ok(new_check):   # recomputed bigint/inf → drop, never 500
                    continue
                candidate.success_check = new_check
            key = dedupe_key(candidate.objective, candidate.inputs)
            if key in seen:
                continue
            seen.add(key)
            candidates.append(candidate)
        if not candidates:
            raise HTTPException(502, "the generator produced no usable questions — try again")
        # flock + file I/O must not block the event loop; to_thread moves the
        # blocking choke point off the loop rather than adding new blocking work.
        added, total = await asyncio.to_thread(append_extras, suite_dir, candidates)
        if not added:
            raise HTTPException(502, "the generator's questions were all duplicates — try again")
        return {"suite": suite, "added": len(added), "total_extras": total}

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

        def _top_failures() -> list[tuple[str, str, int]]:
            """Top-10 (task_type, mast_mode, count) failure triples, ranked
            deterministically (count desc, then key asc) so identical state always
            produces identical context — shared by the failures and playbook pages."""
            triples = [
                (task_type, mode, count)
                for task_type, modes in state.learning.stats.as_dict().items()
                for mode, count in modes.items()
            ]
            triples.sort(key=lambda t: (-t[2], t[0], t[1]))
            return triples[:10]

        def _suites() -> list[str]:
            """The deterministic list of suites start_tune/add_coverage may target."""
            return [d.name for d in _tuning_suite_dirs()]

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
        elif req.page == "routing":
            # subject unused — this is card-level advice over the whole routing state
            pool_models = {
                m.model for members in state.router.pools.values() for m in members
            }
            question = (
                "the harness's routing state — per-tier model pools and the measured "
                "capability matrix. Identify the highest-leverage routing change (a pool "
                "member that should be re-picked, benched, or promoted), or say the "
                "routing is healthy."
            )
            context = {
                "pools": {
                    tier.value: [{"worker_id": m.worker_id, "model": m.model}
                                 for m in members]
                    for tier, members in state.router.pools.items()
                },
                # the matrix is filtered to models that actually sit in a pool —
                # benched/foreign models are dropped so the context stays
                # proportional to pool size, not to every model ever measured
                "matrix": {
                    model: cells
                    for model, cells in state.matrix.as_dict().items()
                    if model in pool_models
                },
                "routed": state.router.route_evidence(),
                "hint": "navigation-only advice: use open_settings when a pool should be "
                        "re-picked, else none; flag matrix cells with samples < 5 as thin "
                        "evidence, not fact",
            }
        elif req.page == "failures":
            # subject unused
            question = (
                "the failure counts grouped by task type and MAST failure mode. Turn the "
                "top failure cluster into the single highest-leverage fix."
            )
            context = {
                "failures": _top_failures(),
                # the active lessons already on file, so it recommends against what
                # exists rather than duplicating it
                "playbook_active": [
                    {"text": b.text,
                     "task_type": b.task_type.value if b.task_type else None}
                    for b in sorted(state.playbook.bullets(),
                                    key=lambda b: (-b.score(), b.created_at))[:20]
                ],
                "suites": _suites(),
                "hint": "prefer start_tune with params {suite} when a harness-parameter "
                        "experiment could fix the cluster, add_coverage with params {suite} "
                        "when evidence is too thin; suite MUST be one of context.suites — if "
                        "none fits the failing task type, use none and say so in the read",
            }
        elif req.page == "playbook":
            # subject unused. Show the top and bottom of the active playbook plus the
            # most recently retired lessons, so curation advice sees both ends and the
            # recent history (deterministic caps: 20 top + 5 bottom + 5 deprecated).
            # The bullet projection below is volatile-field-free (no id/created_at/
            # updated_at) so identical logical state yields identical context bytes.
            # Ordering must be content-deterministic too (panel P1): curate()
            # deprecates bullets in a tight loop, so updated_at values collide at
            # time.time() resolution — WHICH ones collide varies with scheduling
            # jitter, so any payload order derived from comparing updated_at leaks
            # that jitter into the bytes. updated_at therefore only SELECTS the 5
            # most recently retired; the shown slice is ordered by text. The active
            # sort is jitter-proof as-is: created_at ascending coincides with
            # insertion order, so ties and distinct values order identically.
            active = sorted(state.playbook.bullets(),
                            key=lambda b: (-b.score(), b.created_at))
            deprecated = sorted(
                (b for b in state.playbook.bullets(include_deprecated=True) if not b.active),
                key=lambda b: (-b.updated_at, b.text),
            )[:5]
            deprecated.sort(key=lambda b: b.text)  # presentation order is content-only
            bullets, seen_ids = [], set()
            for b in [*active[:20], *active[-5:], *deprecated]:
                if b.id in seen_ids:  # dedupe when the caps overlap (small playbooks)
                    continue
                seen_ids.add(b.id)  # id used only for in-memory dedup, never in the payload
                bullets.append({
                    "text": b.text,
                    "task_type": b.task_type.value if b.task_type else None,
                    "helpful": b.helpful,
                    "harmful": b.harmful,
                    "active": b.active,
                    "origin": b.origin,
                })
            question = (
                "the learned playbook — lessons with helpful/harmful track records. "
                "Recommend curation: which lessons are earning their place, which look "
                "harmful or stale, and what's missing given the failure stats."
            )
            context = {
                "bullets": bullets,
                "failures": _top_failures(),
                "suites": _suites(),
                "hint": "there is no retire action in the vocabulary; phrase curation "
                        "advice in the read; next_actions only start_tune/add_coverage "
                        "(params {suite}, suite from context.suites) or none",
            }
        else:
            raise HTTPException(422, f"unknown advise page {req.page!r}")
        from metaharness.optimization.suites import SUITE_NAMES

        try:
            return await advise(runner, question, context, budget=state.budget,
                                page=req.page, legal_suites=SUITE_NAMES)
        except AdvisorError as exc:
            raise HTTPException(502, str(exc))

    @app.get("/api/runs")
    async def runs(include_archived: bool = False) -> list[dict[str, Any]]:
        if state.engine is None:
            return []
        out = []
        for r in state.engine.runs():
            try:
                _spec, fresh, events, entries = await state.engine.inspect(r.run_id)
            except KeyError:
                continue
            if fresh.archived_at is not None and not include_archived:
                continue
            rec = fresh.model_dump(mode="json")
            # The journal is the durable clock: first entry = run started,
            # last entry = most recent activity.
            clock = events or entries
            rec["started_at"] = clock[0].at if clock else None
            rec["updated_at"] = clock[-1].at if clock else None
            out.append(rec)
        return out

    async def _set_run_archived(run_id: str, archived: bool) -> dict[str, Any]:
        if state.engine is None:
            raise HTTPException(404, f"unknown run {run_id}")
        try:
            result = await (
                state.engine.archive(run_id)
                if archived
                else state.engine.restore(run_id)
            )
        except (KeyError, AttributeError):
            raise HTTPException(404, f"unknown run {run_id}") from None
        except RunArchiveConflict as exc:
            raise HTTPException(409, str(exc)) from None
        return result.model_dump(mode="json")

    @app.post("/api/runs/{run_id}/archive")
    async def archive_run(run_id: str) -> dict[str, Any]:
        return await _set_run_archived(run_id, True)

    @app.post("/api/runs/{run_id}/restore")
    async def restore_run(run_id: str) -> dict[str, Any]:
        return await _set_run_archived(run_id, False)

    @app.get("/api/runs/{run_id}")
    async def run_detail(run_id: str) -> dict[str, Any]:
        try:
            _spec, run_state, events, entries = await state.engine.inspect(run_id)
        except (KeyError, AttributeError):
            raise HTTPException(404, f"unknown run {run_id}")
        return {
            "state": run_state.model_dump(mode="json"),
            # `journal` remains the historical projection for old clients.
            "journal": [e.model_dump(mode="json") for e in entries],
            "events": [e.model_dump(mode="json") for e in events],
        }

    @app.get("/api/runs/{run_id}/package")
    async def run_package(run_id: str) -> Response:
        """Everything the run produced, as one zip: manifest, workflow spec,
        journal, per-step outputs, and the files changed under each step's
        recorded workspace root (capped; omissions listed in the manifest).
        Works for failed runs too — a failure package is a bug report."""
        from metaharness.workflows.package import build_package_bytes

        try:
            spec, run_state, events, entries = await state.engine.inspect(run_id)
        except (KeyError, AttributeError):
            raise HTTPException(404, f"unknown run {run_id}")
        payload = build_package_bytes(
            spec, run_state, entries,
            canonical_events=events if events and hasattr(events[0], "schema_version") else None,
        )
        return Response(
            content=payload,
            media_type="application/zip",
            headers={"Content-Disposition":
                     f'attachment; filename="{run_id}-package.zip"'},
        )

    # -- blueprint catalog --------------------------------------------------------

    @app.get("/api/blueprints")
    async def list_blueprints(include_archived: bool = False) -> list[dict[str, Any]]:
        with _blueprint_api(state):
            return [
                item.model_dump(mode="json")
                for item in state.blueprint_catalog.list(include_archived=include_archived)
            ]

    @app.post("/api/blueprints/readiness")
    async def blueprint_readiness(req: BlueprintReadinessRequest) -> dict[str, Any]:
        """Preview exact-version readiness without loading tools or starting work."""
        from metaharness.blueprints.models import BlueprintVersion

        with _blueprint_api(state):
            snapshot = _load_runnable_blueprint_version(state, req.blueprint)
        blueprint = BlueprintVersion.model_validate(snapshot)
        result = prepare_blueprint_run(
            blueprint,
            req.context,
            tools=state.tools,
            mcp_servers=state.config.mcp_servers,
            mcp_load_status=state.mcp_load_status,
            router=state.router,
            secret_bindings=state.secret_bindings,
        )
        return result.model_dump(mode="json")

    @app.post("/api/blueprint-drafts", status_code=201)
    async def create_draft(req: CreateDraftRequest) -> dict[str, Any]:
        with _blueprint_api(state):
            _validate_slug(req.blueprint_id, "blueprint_id")
            if is_builtin_id(req.blueprint_id):
                raise BlueprintCatalogConflictError(
                    f"{req.blueprint_id!r} is reserved for a built-in blueprint"
                )
            if req.base_version is not None:
                ref = ArtifactRef(id=req.blueprint_id, version=req.base_version)
                draft = state.blueprint_store.create_draft_from_version(
                    ref, owner=LOCAL_BLUEPRINT_OWNER
                )
            else:
                draft = state.blueprint_store.create_draft(
                    req.blueprint_id, req.content, owner=LOCAL_BLUEPRINT_OWNER
                )
            return draft.model_dump(mode="json")

    @app.get("/api/blueprint-drafts")
    async def list_drafts() -> list[dict[str, Any]]:
        with _blueprint_api(state):
            drafts = []
            for entry in state.blueprint_store.list(include_archived=True):
                try:
                    drafts.append(state.blueprint_store.get_draft(entry.id))
                except BlueprintNotFoundError:
                    continue
            return [draft.model_dump(mode="json") for draft in drafts]

    @app.get("/api/blueprint-drafts/{blueprint_id}")
    async def get_draft(blueprint_id: str) -> dict[str, Any]:
        with _blueprint_api(state):
            _validate_slug(blueprint_id, "blueprint_id")
            return state.blueprint_store.get_draft(blueprint_id).model_dump(mode="json")

    @app.patch("/api/blueprint-drafts/{blueprint_id}")
    async def update_draft(blueprint_id: str, req: UpdateDraftRequest) -> dict[str, Any]:
        with _blueprint_api(state):
            _validate_slug(blueprint_id, "blueprint_id")
            draft = state.blueprint_store.update_draft(
                blueprint_id, req.content, expected_revision=req.expected_revision
            )
            return draft.model_dump(mode="json")

    @app.delete("/api/blueprint-drafts/{blueprint_id}")
    async def delete_draft(blueprint_id: str) -> Response:
        with _blueprint_api(state):
            _validate_slug(blueprint_id, "blueprint_id")
            state.blueprint_store.delete_draft(blueprint_id)
            return Response(status_code=204)

    @app.get("/api/blueprints/{blueprint_id}")
    async def get_blueprint_catalog(blueprint_id: str) -> dict[str, Any]:
        with _blueprint_api(state):
            _validate_slug(blueprint_id, "blueprint_id")
            return state.blueprint_catalog.get(blueprint_id).model_dump(mode="json")

    @app.get("/api/blueprints/{blueprint_id}/versions")
    async def list_blueprint_versions(blueprint_id: str) -> list[dict[str, Any]]:
        with _blueprint_api(state):
            _validate_slug(blueprint_id, "blueprint_id")
            return [
                item.model_dump(mode="json")
                for item in state.blueprint_catalog.list_versions(blueprint_id)
            ]

    @app.get("/api/blueprints/{blueprint_id}/versions/{version}")
    async def get_blueprint_version(blueprint_id: str, version: int) -> dict[str, Any]:
        with _blueprint_api(state):
            _validate_slug(blueprint_id, "blueprint_id")
            ref = ArtifactRef(id=blueprint_id, version=version)
            return state.blueprint_catalog.get_version(ref).model_dump(mode="json")

    @app.post("/api/blueprint-drafts/{blueprint_id}/publish")
    async def publish_blueprint(blueprint_id: str, req: PublishRequest) -> dict[str, Any]:
        with _blueprint_api(state):
            _validate_slug(blueprint_id, "blueprint_id")
            version = state.blueprint_store.publish(
                blueprint_id, expected_revision=req.expected_revision
            )
            return version.model_dump(mode="json")

    @app.post("/api/blueprints/{blueprint_id}/fork")
    async def fork_blueprint(blueprint_id: str, req: ForkRequest) -> dict[str, Any]:
        with _blueprint_api(state):
            _validate_slug(blueprint_id, "blueprint_id")
            _validate_slug(req.new_id, "new_id")
            source = ArtifactRef(id=blueprint_id, version=req.source_version)
            draft = state.blueprint_catalog.fork(
                source, new_id=req.new_id, owner=LOCAL_BLUEPRINT_OWNER,
                display_name=req.display_name or None,
            )
            return draft.model_dump(mode="json")

    @app.patch("/api/blueprints/{blueprint_id}/metadata")
    async def patch_blueprint_metadata(
        blueprint_id: str, req: MetadataPatchRequest
    ) -> dict[str, Any]:
        with _blueprint_api(state):
            _validate_slug(blueprint_id, "blueprint_id")
            _validate_display_name(req.display_name)
            entry = state.blueprint_store.set_display_name(
                blueprint_id, req.display_name
            )
            return entry.model_dump(mode="json")

    @app.post("/api/blueprints/{blueprint_id}/archive")
    async def archive_blueprint(blueprint_id: str) -> dict[str, Any]:
        with _blueprint_api(state):
            _validate_slug(blueprint_id, "blueprint_id")
            return state.blueprint_store.archive(blueprint_id).model_dump(mode="json")

    @app.post("/api/blueprints/{blueprint_id}/restore")
    async def restore_blueprint(blueprint_id: str) -> dict[str, Any]:
        with _blueprint_api(state):
            _validate_slug(blueprint_id, "blueprint_id")
            return state.blueprint_store.restore(blueprint_id).model_dump(mode="json")

    # -- exact evaluation suites, reports, and tuning ---------------------------

    @app.get("/api/eval-suites")
    async def list_eval_suites(include_archived: bool = False) -> list[dict[str, Any]]:
        with _eval_api(state):
            return [
                item.model_dump(mode="json")
                for item in state.eval_suite_store.list(include_archived=include_archived)
            ]

    @app.post("/api/eval-suites", status_code=201)
    async def create_eval_suite(req: CreateEvalSuiteRequest) -> dict[str, Any]:
        with _eval_api(state):
            _validate_slug(req.suite_id, "suite_id")
            draft = state.eval_suite_store.create_draft(
                req.suite_id, req.content, owner=LOCAL_BLUEPRINT_OWNER
            )
            return draft.model_dump(mode="json")

    @app.get("/api/eval-suites/{suite_id}/draft")
    async def get_eval_suite_draft(suite_id: str) -> dict[str, Any]:
        with _eval_api(state):
            _validate_slug(suite_id, "suite_id")
            return state.eval_suite_store.get_draft(suite_id).model_dump(mode="json")

    @app.patch("/api/eval-suites/{suite_id}")
    @app.patch("/api/eval-suites/{suite_id}/draft", include_in_schema=False)
    async def update_eval_suite(
        suite_id: str, req: UpdateEvalSuiteRequest
    ) -> dict[str, Any]:
        with _eval_api(state):
            _validate_slug(suite_id, "suite_id")
            draft = state.eval_suite_store.update_draft(
                suite_id, req.content, expected_revision=req.expected_revision
            )
            return draft.model_dump(mode="json")

    @app.post("/api/eval-suites/{suite_id}/versions")
    async def publish_eval_suite(suite_id: str, req: PublishRequest) -> dict[str, Any]:
        with _eval_api(state):
            _validate_slug(suite_id, "suite_id")
            published = state.eval_suite_store.publish(
                suite_id, expected_revision=req.expected_revision
            )
            # EvalSuitePublic is a deliberately sealed projection.
            return published.model_dump(mode="json")

    @app.get("/api/eval-suites/{suite_id}/versions/{version}")
    async def get_eval_suite_version(suite_id: str, version: int) -> dict[str, Any]:
        with _eval_api(state):
            _validate_slug(suite_id, "suite_id")
            return state.eval_suite_store.get_version(suite_id, version).model_dump(
                mode="json"
            )

    @app.get("/api/evaluation-reports/{report_id}")
    async def get_evaluation_report(report_id: str) -> dict[str, Any]:
        with _eval_api(state):
            _validate_slug(report_id, "report_id")
            return state.evaluation_report_store.get(report_id).model_dump(mode="json")

    @app.post(
        "/api/blueprints/{blueprint_id}/versions/{version}/evaluate",
        status_code=201,
    )
    async def evaluate_blueprint_version(
        blueprint_id: str, version: int, req: EvaluateBlueprintRequest
    ) -> dict[str, Any]:
        with _blueprint_api(state), _eval_api(state):
            _validate_slug(blueprint_id, "blueprint_id")
            if req.runner.sealed_holdout_access:
                raise UnsafeEvalRunnerError(
                    "public evaluation runners cannot request sealed holdout access"
                )
            blueprint_ref = ArtifactRef(id=blueprint_id, version=version)
            blueprint = state.blueprint_catalog.get_version(blueprint_ref)
            if req.eval_ref not in blueprint.eval_suites:
                raise EvalReferenceMismatchError(
                    "eval suite exact ref is not frozen on the blueprint version"
                )
            # Resolve before starting an expensive subprocess and before trusting
            # the descriptor. The evaluator resolves it again at execution time.
            state.eval_suite_store.get_version_for_evaluation(
                req.eval_ref.id, req.eval_ref.version
            )
            try:
                state.evaluation_report_store.get(req.report_id)
            except EvalArtifactNotFoundError:
                pass
            else:
                raise EvalArtifactAlreadyExistsError(
                    f"evaluation report {req.report_id!r} already exists"
                )
            evaluator = ExactSuiteEvaluator(
                state.blueprint_catalog, state.eval_suite_store, req.runner
            )
            report = await asyncio.to_thread(
                evaluator.evaluate,
                report_id=req.report_id,
                blueprint_ref=blueprint_ref,
                eval_ref=req.eval_ref,
                split=req.split,
            )
            return state.evaluation_report_store.create(report).model_dump(mode="json")

    @app.post(
        "/api/blueprints/{blueprint_id}/versions/{version}/tune",
        status_code=201,
    )
    async def tune_blueprint_version(
        blueprint_id: str, version: int, req: TuneBlueprintRequest
    ) -> dict[str, Any]:
        with _blueprint_api(state), _eval_api(state):
            _validate_slug(blueprint_id, "blueprint_id")
            blueprint_ref = ArtifactRef(id=blueprint_id, version=version)
            blueprint = state.blueprint_catalog.get_version(blueprint_ref)
            proposal = create_tuning_proposal(
                proposal_id=req.proposal_id,
                blueprint_ref=blueprint_ref,
                eval_refs=blueprint.eval_suites,
                catalog=state.blueprint_catalog,
                eval_store=state.eval_suite_store,
                report_store=state.evaluation_report_store,
                report_refs=req.report_refs,
                patches=req.patches,
                rationale=req.rationale,
            )
            persisted = state.tuning_proposal_store.create(proposal)
            applied_draft = None
            if req.human_approved is True:
                applied_draft = apply_tuning_proposal_to_draft(
                    persisted,
                    catalog=state.blueprint_catalog,
                    owner=LOCAL_BLUEPRINT_OWNER,
                    base_version=version,
                    expected_revision=req.expected_revision,
                    human_approved=True,
                )
            return {
                "proposal": persisted.model_dump(mode="json"),
                "applied_draft": (
                    applied_draft.model_dump(mode="json")
                    if applied_draft is not None
                    else None
                ),
                "published": False,
            }

    @app.post("/api/blueprints/{blueprint_id}/versions/{version}/package")
    async def package_blueprint_version(
        blueprint_id: str, version: int, req: PortablePackageRequest
    ) -> Response:
        with _blueprint_api(state), _eval_api(state):
            _validate_slug(blueprint_id, "blueprint_id")
            ref = ArtifactRef(id=blueprint_id, version=version)
            blueprint = state.blueprint_catalog.get_version(ref)
            for eval_ref in blueprint.eval_suites:
                state.eval_suite_store.get_version(eval_ref.id, eval_ref.version)
            payload = build_portable_package(
                blueprint,
                targets=req.targets,
                eval_refs=blueprint.eval_suites,
                deployment_options=req.deployment_options,
                generated_at=req.generated_at,
            )
        return Response(
            content=payload,
            media_type="application/zip",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="{blueprint_id}-v{version}-portable.zip"'
                )
            },
        )

    def _advance_in_background(run_id: str) -> None:
        async def _run() -> None:
            try:
                await state.engine.advance(run_id)
            except Exception as exc:  # never crashes the app — but journal it,
                # or the run sits in "running" forever with no trail
                try:
                    await state.engine.fail(
                        run_id,
                        f"{type(exc).__name__}: {exc}"[:300],
                    )
                except Exception:
                    pass
        asyncio.get_running_loop().create_task(_run())

    @app.post("/api/runs/{run_id}/approval")
    async def resolve_approval(run_id: str, req: ApprovalRequest) -> dict[str, Any]:
        try:
            run_state = await state.engine.resolve_hitl(
                run_id, req.step_id, approved=req.approved
            )
        except (KeyError, AttributeError):
            raise HTTPException(404, f"unknown run {run_id}")
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
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
            result = template.instantiate(req.goal), f"template:{template.id}", None
        else:
            result = await plan_workflow(req.goal, state.planner_runner(), context,
                                         tools=state.tools)
        _enforce_mcp_hitl(result[0])
        return result

    def _enforce_mcp_hitl(spec) -> None:
        """MCP annotations are untrusted hints; all MCP calls require approval."""
        for step in spec.steps:
            if any("." in name or ((tool := state.tools.get(name))
                   and tool.source.startswith("mcp:")) for name in step.tools):
                step.hitl = True
                step.hitl_timing = "before"

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
        prior_summary = summarize_run(self_spec, run_state)
        followup_context = dict(run_state.context)
        followup_context.setdefault("prior_run_summary", prior_summary)
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
                "prior_summary": prior_summary,
                "context": followup_context}

    def _value_hazard_problems(spec) -> list[str]:
        """Issue #10 intake-boundary gate: WorkflowSpec.model_validate only
        checks SHAPE — an inf/huge equals-or-tol slips through it untouched and
        every run of that step then burns as UNVERIFIED. Named per step so the
        422 body points straight at the offending check."""
        from metaharness.evals.verifiers import check_value_problems

        return [f"{step.id}: {p}" for step in spec.steps
                for p in check_value_problems(step.success_check)]

    def _resolve_run_source(req: StartRunRequest):
        """Mutually-exclusive run source: blueprint exact version, legacy JSON
        workflow, or legacy YAML workflow. Returns the resolved WorkflowSpec,
        request context, and optional blueprint ref/snapshot."""
        sources = [
            ("blueprint", req.blueprint is not None),
            ("workflow", req.workflow is not None),
            ("workflow_yaml", bool(req.workflow_yaml)),
        ]
        present = [name for name, ok in sources if ok]
        if len(present) != 1:
            raise ValueError(
                f"provide exactly one source; found {len(present)}: {present}"
            )
        source = present[0]
        if source == "blueprint":
            with _blueprint_api(state):
                snapshot = _load_runnable_blueprint_version(state, req.blueprint)
            from metaharness.blueprints.models import BlueprintVersion
            blueprint = BlueprintVersion.model_validate(snapshot)
            spec = blueprint.workflow
            return spec, req.context, req.blueprint, snapshot
        if source == "workflow":
            from metaharness.workflows.dsl import WorkflowSpec
            spec = WorkflowSpec.model_validate(req.workflow)
        else:
            spec = load_workflow(req.workflow_yaml)
        return spec, req.context, None, None

    @app.post("/api/workflows/validate")
    async def validate_workflow(req: StartRunRequest) -> dict[str, Any]:
        """Validate a hand-written or hand-edited workflow WITHOUT running it:
        the plan editor's save/apply path. Returns the normalized workflow
        plus its YAML form (for the raw editor); a bad spec 422s with the
        validator's exact complaint."""
        import yaml

        try:
            spec, _ctx, _ref, _snap = _resolve_run_source(req)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        readiness = None
        if _ref is not None:
            from metaharness.blueprints.models import BlueprintVersion

            blueprint = BlueprintVersion.model_validate(_snap)
            readiness = prepare_blueprint_run(
                blueprint,
                _ctx,
                tools=state.tools,
                mcp_servers=state.config.mcp_servers,
                mcp_load_status=state.mcp_load_status,
                router=state.router,
                secret_bindings=state.secret_bindings,
            )
            if readiness.ready:
                try:
                    spec = resolve_blueprint_workflow(
                        blueprint, readiness.normalized_context
                    )
                except ValueError as exc:
                    raise HTTPException(422, str(exc)) from None
        problems = _value_hazard_problems(spec)
        if problems:
            raise HTTPException(422, "; ".join(problems))
        _enforce_mcp_hitl(spec)
        if _ref is None:
            assignment_issues = workflow_assignment_issues(spec, router=state.router)
            if assignment_issues:
                raise HTTPException(409, detail={
                    "ready": False,
                    "issues": [issue.model_dump(mode="json") for issue in assignment_issues],
                })
        data = spec.model_dump(mode="json")
        response = {"workflow": data,
                    "yaml": yaml.safe_dump(data, sort_keys=False, allow_unicode=True)}
        if readiness is not None:
            response["readiness"] = readiness.model_dump(mode="json")
        return response

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
            spec, context, bp_ref, bp_snapshot = _resolve_run_source(req)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        if bp_ref is not None:
            from metaharness.blueprints.models import BlueprintVersion

            blueprint = BlueprintVersion.model_validate(bp_snapshot)
            # Recheck loaded registry/config truth immediately before creating a
            # run.  Failure therefore allocates no run id and writes no journal.
            readiness = prepare_blueprint_run(
                blueprint,
                context,
                tools=state.tools,
                mcp_servers=state.config.mcp_servers,
                mcp_load_status=state.mcp_load_status,
                router=state.router,
                secret_bindings=state.secret_bindings,
            )
            if not readiness.ready:
                status = (
                    422
                    if all(issue.code == "invalid_input" for issue in readiness.issues)
                    else 409
                )
                raise HTTPException(status, detail=readiness.model_dump(mode="json"))
            context = readiness.normalized_context
            try:
                spec = resolve_blueprint_workflow(blueprint, context)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from None
        problems = _value_hazard_problems(spec)
        if problems:
            raise HTTPException(422, "; ".join(problems))
        _enforce_mcp_hitl(spec)
        if bp_ref is None:
            assignment_issues = workflow_assignment_issues(spec, router=state.router)
            if assignment_issues:
                raise HTTPException(409, detail={
                    "ready": False,
                    "issues": [issue.model_dump(mode="json") for issue in assignment_issues],
                })
        run_state = state.engine.start(
            spec,
            context=context,
            blueprint_ref=bp_ref.model_dump(mode="json") if bp_ref is not None else None,
            blueprint_snapshot=bp_snapshot,
        )
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
            roles=req.roles, capabilities=req.capabilities,
            max_tokens=req.max_tokens, thinking=req.thinking, cli=req.cli,
            # mock has no timeout to apply it to; a direct API caller bypasses
            # the wizard's JS guards, so drop it server-side too (issue #2
            # panel, codex+kimi P2 — the card would display a fake timeout)
            timeout_s=None if req.kind == "mock" else req.timeout_s,
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
            state.add_worker(
                runner, req.tier, task_types=req.task_types,
                roles=req.roles, capabilities=req.capabilities,
            )
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

    @app.post("/api/config/secret-bindings")
    async def update_secret_binding(req: SecretBindingWriteRequest) -> dict[str, Any]:
        """Create/replace one local binding; plaintext is write-only."""
        try:
            state.config.set_secret_binding(req.name, req.value)
            state.secret_bindings.configure(req.name, req.value)
        except ValueError as exc:
            # Validation messages describe shape only and never interpolate value.
            raise HTTPException(422, str(exc)) from None
        state.save_config()
        return {"name": req.name, "configured": True}

    @app.delete("/api/config/secret-bindings/{name}")
    async def delete_secret_binding(name: str) -> dict[str, Any]:
        from metaharness.blueprints.secrets import validate_secret_binding_name

        try:
            binding = validate_secret_binding_name(name)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        if binding not in state.config.secret_bindings:
            raise HTTPException(404, f"unknown secret binding {binding!r}")
        del state.config.secret_bindings[binding]
        state.secret_bindings.remove(binding)
        state.save_config()
        return {"deleted": binding}

    @app.post("/api/config/mcp")
    async def update_mcp_server(spec: dict[str, Any]) -> dict[str, Any]:
        secret_values = [str(spec.get("oauth_token", ""))]
        secret_values.extend(str(value) for value in (spec.get("env") or {}).values())
        if any(value.startswith("enc1:") for value in secret_values):
            raise HTTPException(422, "MCP secrets must be plaintext, not enc1 envelopes")
        current = state.config.mcp_servers.get(str(spec.get("name", "")))
        merged = current.model_dump() if current else {}
        merged.update({key: value for key, value in spec.items() if key not in {"env", "oauth_token"}})
        if "oauth_token" in spec:
            token = spec.get("oauth_token")
            if current is None or (token is not None and not is_masked(str(token)) and token != "set"):
                merged["oauth_token"] = token or ""
        if "env" in spec:
            old_env = current.env if current else {}
            merged["env"] = {
                key: (old_env.get(key, "") if is_masked(str(value)) or value == "set" else value)
                for key, value in (spec.get("env") or {}).items()
            }
        try:
            server = MCPServerConfig.model_validate(merged)
        except ValueError:
            raise HTTPException(422, "invalid MCP server configuration")
        state.tools.unregister_source(f"mcp:{server.name}")
        state.mcp_load_status.pop(server.name, None)
        state.config.mcp_servers[server.name] = server
        state.save_config()
        return state.config.public_dict()["mcp_servers"][server.name]

    @app.post("/api/config/mcp/{name}/load")
    async def load_mcp_server(name: str) -> dict[str, Any]:
        server = state.config.mcp_servers.get(name)
        if server is None:
            raise HTTPException(404, f"unknown MCP server {name}")
        import asyncio
        from metaharness.tools import load_mcp_tools, mcp_config_fingerprint
        one = HarnessConfig(mcp_servers={name: server})
        try:
            async with asyncio.timeout(60):
                report = await load_mcp_tools(state.tools, one)
        except TimeoutError:
            state.mcp_load_status[name] = {
                "ok": False, "status": "load_failed", "tools": 0,
                "detail": "connection timed out after 60s",
                "fingerprint": mcp_config_fingerprint(server),
            }
            return {
                "ok": False, "status": "load_failed",
                "detail": "connection timed out after 60s",
            }
        result = report[name]
        state.mcp_load_status[name] = result
        if not result.get("ok"):
            status = result.get("status", "load_failed")
            detail = {
                "disabled": "server is disabled",
                "zero_tools": "server exposed zero tools",
                "load_failed": "connection failed; check server settings and credentials",
            }.get(status, "connection failed; check server settings and credentials")
            return {"ok": False, "status": status, "detail": detail}
        return {key: value for key, value in result.items() if key != "fingerprint"}

    @app.delete("/api/config/mcp/{name}")
    async def delete_mcp_server(name: str) -> dict[str, Any]:
        if name not in state.config.mcp_servers:
            raise HTTPException(404, f"unknown MCP server {name}")
        del state.config.mcp_servers[name]
        state.tools.unregister_source(f"mcp:{name}")
        state.mcp_load_status.pop(name, None)
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
            {"name": t.name, "description": t.description, "source": t.source,
             "annotations": t.annotations}
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
