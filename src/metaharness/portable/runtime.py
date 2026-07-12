"""Portable CLI runtime: wire a minimal HarnessState for exact-blueprint runs."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Optional

from metaharness.config import CONFIG_PATH, HarnessConfig
from metaharness.core.types import Task, TaskType, Tier
from metaharness.factory import build_agent_runner
from metaharness.harness import ScriptedWorker
from metaharness.harness.runner import Runner
from metaharness.identity import KeyPair
from metaharness.routing.router import Router
from metaharness.tools import default_registry, load_mcp_tools, mcp_config_fingerprint
from metaharness.web.state import HarnessState

_RUN_ID_RE = re.compile(r"^run_[0-9a-f]{12}$")


class PortableRuntimeError(RuntimeError):
    """The local runtime cannot be wired without inventing capabilities."""


def shim_workers_enabled(explicit: bool = False) -> bool:
    """Return true only for an explicit test-only shim opt-in."""
    return explicit or os.environ.get("METAHARNESS_SHIM_WORKERS", "").strip() == "1"


def validate_run_id(run_id: str) -> str:
    if not _RUN_ID_RE.fullmatch(run_id):
        raise ValueError(f"invalid run id: {run_id!r}")
    return run_id


def journal_path_for(run_id: str, journal_dir: Path) -> Path:
    validate_run_id(run_id)
    path = journal_dir / f"{run_id}.jsonl"
    resolved = path.resolve()
    resolved_dir = journal_dir.resolve()
    if not str(resolved).startswith(str(resolved_dir) + os.sep) and resolved != resolved_dir:
        raise ValueError("journal path escapes the journal directory")
    return path


def _expected_output(task: Task) -> Any:
    """Deterministic shim: produce an output that satisfies the step contract."""
    check = task.success_check or {}
    if "equals" in check:
        return check["equals"]
    if "contains" in check:
        return f"{check['contains']} answer"
    if "one_of" in check and check["one_of"]:
        return check["one_of"][0]
    schema = task.output_schema
    if schema:
        kind = schema.get("type")
        if kind == "object" or "properties" in schema or "required" in schema:
            props = schema.get("properties", {})
            required = schema.get("required", list(props))
            return {k: _expected_output(Task(success_check=props.get(k))) for k in required}
        if kind == "array":
            return []
        if kind == "boolean":
            return True
        if kind in ("number", "integer"):
            return 0
        if kind == "string":
            return "answer"
    if task.task_type == TaskType.ARITHMETIC:
        return task.inputs.get("expression", "0")
    return {"answer": task.objective[:60]}


def _shim_runner(tier: Tier) -> Runner:
    def handler(task: Task) -> Any:
        return _expected_output(task)

    return ScriptedWorker(
        worker_id=f"shim-{tier.value}",
        handler=handler,
        tier=tier,
        model=f"shim-{tier.value}",
        keypair=KeyPair.generate(),
    )


def build_portable_state(
    *,
    journal_dir: Path,
    workspace_root: Path,
    config_path: Optional[Path] = None,
    shim: bool = False,
) -> HarnessState:
    """Wire a state that can execute one exact blueprint without the WebUI."""
    state = HarnessState()
    state.config_path = config_path or CONFIG_PATH
    state.config = HarnessConfig.load(state.config_path)
    state.hydrate_secret_bindings()
    state.tools = default_registry(workspace_root)

    runners: dict[Tier, list[Runner]] = {}
    if shim:
        for tier in Tier:
            runner = _shim_runner(tier)
            kp = runner.keypair
            state.register_worker(runner, kp, tiers=[tier.value])
            runners[tier] = [runner]
    else:
        for agent in state.config.agents:
            if not agent.enabled:
                continue
            kp = KeyPair.generate()
            runner = build_agent_runner(agent, state.config, keypair=kp)
            state.register_worker(
                runner,
                kp,
                tiers=[agent.tier],
                task_types=agent.task_types or None,
                roles=agent.roles or None,
                capabilities=agent.capabilities or None,
                host=agent.cli,
            )
            runners.setdefault(Tier(agent.tier), []).append(runner)
        # Production runtime is intentionally honest: missing configured agents
        # remain missing and readiness reports the resulting assignment gaps.
        # Deterministic fake workers exist only behind the explicit ``shim``
        # switch used by isolated tests.

    if not runners:
        raise PortableRuntimeError(
            "portable runtime requires at least one enabled configured agent; "
            "deterministic shim workers must be explicitly enabled for tests"
        )

    state.wire(runners, journal_dir=journal_dir)

    def _tool_available(name: str) -> bool:
        tool = state.tools.get(name)
        if tool is None:
            return False
        if not tool.source.startswith("mcp:"):
            return True
        server_name = tool.source[len("mcp:"):]
        server = state.config.mcp_servers.get(server_name)
        status = state.mcp_load_status.get(server_name)
        return bool(
            server is not None and server.enabled and status is not None
            and status.get("status") == "loaded"
            and status.get("fingerprint") == mcp_config_fingerprint(server)
        )

    if state.engine is not None:
        state.engine.tool_requires_approval = lambda name: "." in name
        state.engine.tool_available = _tool_available

    return state


async def refresh_portable_capabilities(state: HarnessState) -> None:
    """Load configured MCP tools and refresh their exact config fingerprints."""
    state.hydrate_secret_bindings()
    if not state.config.mcp_servers:
        return
    try:
        report = await load_mcp_tools(state.tools, state.config)
    except RuntimeError as exc:
        report = {
            name: {
                "ok": False, "status": "load_failed", "tools": 0,
                "detail": str(exc), "fingerprint": mcp_config_fingerprint(server),
            }
            for name, server in state.config.mcp_servers.items()
        }
    state.mcp_load_status.update(report)


async def prepare_portable_blueprint_run(
    state: HarnessState, blueprint: Any, request_context: dict[str, Any]
) -> tuple[Any, Any]:
    """Shared exact-run preparation for portable CLI and package service."""
    from metaharness.blueprints.readiness import (
        prepare_blueprint_run,
        resolve_blueprint_workflow,
    )

    await refresh_portable_capabilities(state)
    readiness = prepare_blueprint_run(
        blueprint,
        request_context,
        tools=state.tools,
        mcp_servers=state.config.mcp_servers,
        mcp_load_status=state.mcp_load_status,
        router=state.router,
        secret_bindings=state.secret_bindings,
    )
    workflow = (
        resolve_blueprint_workflow(blueprint, readiness.normalized_context)
        if readiness.ready else None
    )
    return readiness, workflow
