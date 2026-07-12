"""FastAPI service that serves a loaded portable package.

This is the runtime behind `metaharness serve --package`. It loads and
integrity-checks the package at startup, exposes a health endpoint used by
`metaharness healthcheck`, and keeps the same durable journal/workspace
contract as the CLI run command.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from metaharness.blueprints.models import ArtifactRef, BlueprintVersion
from metaharness.evals.verifiers import check_value_problems
from metaharness.portable.cli import load_blueprint_input, _record_run_workspace
from metaharness.portable.runtime import (
    build_portable_state,
    prepare_portable_blueprint_run,
    shim_workers_enabled,
)

DEFAULT_SERVICE_WORKSPACE = Path("/var/lib/metaharness/workspace")
DEFAULT_SERVICE_JOURNAL = Path("/var/lib/metaharness/journal")


class PackageRunRequest(BaseModel):
    context: dict[str, Any] = Field(default_factory=dict)
    wait: bool = True
    # Compatibility with /api/runs clients. The package service still runs only
    # the embedded exact blueprint and rejects alternate sources.
    blueprint: Optional[ArtifactRef] = None
    workflow: Optional[dict[str, Any]] = None
    workflow_yaml: str = ""


class PackageApprovalRequest(BaseModel):
    step_id: str
    approved: bool = True
    wait: bool = True


def create_package_app(
    package_path: Path,
    *,
    workspace: Path | None = None,
    journal_dir: Path | None = None,
) -> FastAPI:
    loaded = load_blueprint_input(package_path, allow_draft=False)
    blueprint = loaded.blueprint
    if not isinstance(blueprint, BlueprintVersion):
        raise ValueError("package service requires an exact published BlueprintVersion")
    manifest = loaded.manifest

    workspace_root = workspace or DEFAULT_SERVICE_WORKSPACE
    journal_dir = journal_dir or DEFAULT_SERVICE_JOURNAL

    state = build_portable_state(
        journal_dir=journal_dir,
        workspace_root=workspace_root,
        shim=shim_workers_enabled(),
    )

    readiness, resolved_workflow = asyncio.run(
        prepare_portable_blueprint_run(state, blueprint, {})
    )

    app = FastAPI(title="metaharness-package", version="0.1.0")
    app.state.resolved_workflow = resolved_workflow

    def _advance_in_background(run_id: str) -> None:
        async def _run() -> None:
            try:
                await state.engine.advance(run_id)
            except Exception as exc:
                try:
                    await state.engine.fail(
                        run_id,
                        f"{type(exc).__name__}: {exc}"[:300],
                    )
                except Exception:
                    pass

        asyncio.get_running_loop().create_task(_run())

    def _value_hazard_problems(spec: Any) -> list[str]:
        return [
            f"{step.id}: {problem}"
            for step in spec.steps
            for problem in check_value_problems(step.success_check)
        ]

    async def _run_detail(run_id: str) -> dict[str, Any]:
        try:
            _spec, run_state, events, entries = await state.engine.inspect(run_id)
        except (KeyError, AttributeError):
            raise HTTPException(404, f"unknown run {run_id}") from None
        return {
            "state": run_state.model_dump(mode="json"),
            "journal": [entry.model_dump(mode="json") for entry in entries],
            "events": [event.model_dump(mode="json") for event in events],
        }

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "healthy",
            "blueprint_ref": blueprint.ref.model_dump(mode="json"),
            "package_digest": manifest.content_digest,
            "workspace": str(workspace_root),
            "journal_dir": str(journal_dir),
        }

    @app.get("/ready")
    async def ready() -> JSONResponse:
        return JSONResponse(readiness.model_dump(mode="json"))

    @app.get("/api/runs")
    @app.get("/runs", include_in_schema=False)
    async def runs() -> list[dict[str, Any]]:
        out = []
        for run in state.engine.runs():
            try:
                _spec, fresh, events, entries = await state.engine.inspect(run.run_id)
            except KeyError:
                continue
            rec = fresh.model_dump(mode="json")
            clock = events or entries
            rec["started_at"] = clock[0].at if clock else None
            rec["updated_at"] = clock[-1].at if clock else None
            out.append(rec)
        return out

    @app.get("/api/runs/{run_id}")
    @app.get("/runs/{run_id}", include_in_schema=False)
    async def run_detail(run_id: str) -> dict[str, Any]:
        return await _run_detail(run_id)

    @app.post("/api/runs")
    @app.post("/runs", include_in_schema=False)
    async def start_run(req: PackageRunRequest) -> dict[str, Any]:
        if req.workflow is not None or req.workflow_yaml:
            raise HTTPException(
                422,
                "package service runs only the embedded exact blueprint; "
                "workflow and workflow_yaml are not accepted",
            )
        if req.blueprint is not None and req.blueprint != blueprint.ref:
            raise HTTPException(
                409,
                {
                    "detail": "requested blueprint does not match the package",
                    "package_blueprint": blueprint.ref.model_dump(mode="json"),
                },
            )

        run_readiness, run_workflow = await prepare_portable_blueprint_run(
            state, blueprint, req.context
        )
        if not run_readiness.ready:
            status = (
                422
                if all(issue.code == "invalid_input" for issue in run_readiness.issues)
                else 409
            )
            raise HTTPException(status, detail=run_readiness.model_dump(mode="json"))
        assert run_workflow is not None

        problems = _value_hazard_problems(run_workflow)
        if problems:
            raise HTTPException(422, "; ".join(problems))

        run_state = state.engine.start(
            run_workflow,
            context=dict(run_readiness.normalized_context),
            blueprint_ref=blueprint.ref.model_dump(mode="json"),
            blueprint_snapshot=blueprint.model_dump(mode="json"),
        )
        _record_run_workspace(run_state.run_id, journal_dir, workspace_root)

        if req.wait:
            run_state = await state.engine.advance(run_state.run_id)
        else:
            _advance_in_background(run_state.run_id)
        return run_state.model_dump(mode="json")

    @app.post("/api/runs/{run_id}/approval")
    @app.post("/runs/{run_id}/approval", include_in_schema=False)
    async def resolve_approval(
        run_id: str, req: PackageApprovalRequest
    ) -> dict[str, Any]:
        try:
            run_state = await state.engine.resolve_hitl(
                run_id, req.step_id, approved=req.approved
            )
        except (KeyError, AttributeError):
            raise HTTPException(404, f"unknown run {run_id}") from None
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        if req.wait:
            run_state = await state.engine.advance(run_id)
        else:
            _advance_in_background(run_id)
        return run_state.model_dump(mode="json")

    return app
