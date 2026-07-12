"""Pure input normalization and readiness checks for exact blueprint runs.

This module deliberately performs no loading, network access, journaling, or
execution.  Preview and run intake call the same function so the server remains
the source of truth and a run can recheck immediately before it is created.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Literal, Mapping, Optional, Protocol, TypeVar

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import Field

from metaharness.blueprints.models import (
    ArtifactRef,
    BlueprintVersion,
    SecretBindingRef,
    StrictModel,
)
from metaharness.blueprints.secrets import validate_secret_binding_name
from metaharness.config import MCPServerConfig
from metaharness.core.types import Tier
from metaharness.routing.router import Router
from metaharness.tools.registry import ToolRegistry
from metaharness.workflows.dsl import WorkflowSpec


class ReadinessRepair(StrictModel):
    """A machine-stable repair action with a human-readable label."""

    action: Literal[
        "fix_input",
        "choose_tool",
        "load_mcp",
        "enable_mcp",
        "configure_secret_binding",
        "choose_worker",
        "configure_agent",
    ]
    label: str
    target: Optional[str] = None


class ReadinessIssue(StrictModel):
    """One deterministic reason an exact blueprint is not ready to run."""

    code: Literal[
        "invalid_input",
        "missing_tool",
        "unloaded_mcp",
        "missing_secret_binding",
        "missing_worker",
        "no_eligible_worker",
        "pin_mismatch",
        "unsafe_recursion",
    ]
    severity: Literal["error"] = "error"
    message: str
    input_name: Optional[str] = None
    stage_id: Optional[str] = None
    tool: Optional[str] = None
    server: Optional[str] = None
    worker_id: Optional[str] = None
    eligibility_code: Optional[str] = None
    mcp_state: Optional[Literal[
        "never_loaded", "load_failed", "zero_tools", "disabled", "stale_config"
    ]] = None
    repair: ReadinessRepair


class ReadinessResult(StrictModel):
    """Side-effect-free preparation result returned by preview and run intake."""

    blueprint_ref: ArtifactRef
    ready: bool
    # Execution-only data.  Public model dumps and FastAPI responses must never
    # echo request values or defaults back to callers.
    normalized_context: dict[str, Any] = Field(default_factory=dict, exclude=True)
    issues: list[ReadinessIssue] = Field(default_factory=list)


class SecretBindingLookup(Protocol):
    """Readiness-safe view of the local secret-binding store.

    This deliberately exposes existence only. Resolving a plaintext value while
    normalizing run context would make it far too easy to journal or serialize.
    """

    def is_configured(self, binding: str) -> bool: ...


_T = TypeVar("_T")


class SecretBindingProvider(SecretBindingLookup, Protocol):
    """Last-moment secret delivery contract for an authorized provider/tool.

    Implementations call ``consumer`` with the plaintext and return only the
    consumer's result. Callers never receive a plaintext binding value directly.
    """

    def use(self, binding: str, consumer: Callable[[str], _T]) -> _T: ...


_CONTEXT_REF = re.compile(r"\$context\.([A-Za-z][A-Za-z0-9_-]*(?:\.[A-Za-z0-9_-]+)*)")


def _context_value(reference: str, context: Mapping[str, Any]) -> Any:
    node: Any = context
    for part in reference.split("."):
        if not isinstance(node, Mapping) or part not in node:
            raise ValueError(f"context reference '$context.{reference}' is not defined")
        node = node[part]
    return node


def _render_context_references(value: Any, context: Mapping[str, Any]) -> Any:
    """Resolve context references while retaining runtime ``$steps`` links."""
    if isinstance(value, dict):
        return {
            key: _render_context_references(child, context)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [_render_context_references(child, context) for child in value]
    if not isinstance(value, str):
        return value
    exact = _CONTEXT_REF.fullmatch(value)
    if exact is not None:
        return _context_value(exact.group(1), context)

    def substitute(match: re.Match[str]) -> str:
        resolved = _context_value(match.group(1), context)
        if isinstance(resolved, (dict, list)):
            raise ValueError(
                f"context reference {match.group(0)!r} cannot embed a structured value"
            )
        return str(resolved)

    return _CONTEXT_REF.sub(substitute, value)


def resolve_blueprint_workflow(
    blueprint: BlueprintVersion, normalized_context: Mapping[str, Any]
) -> WorkflowSpec:
    """Build the immutable, fully input-resolved workflow for one exact run.

    The authored ``BlueprintVersion`` remains immutable. Ordinary run inputs are
    materialized into a deep copy so the workflow plus authored snapshot are a
    complete digest input. Secret values can never be rendered because readiness
    excludes declared secrets from ``normalized_context``.
    """
    secret_names = {item.name for item in blueprint.inputs if item.secret}
    leaked = secret_names & set(normalized_context)
    if leaked:
        raise ValueError(
            f"declared secret inputs cannot enter resolved workflow context: {sorted(leaked)}"
        )
    rendered = _render_context_references(
        blueprint.workflow.model_dump(mode="python"), normalized_context
    )
    return WorkflowSpec.model_validate(rendered)


def _input_issue(message: str, name: Optional[str] = None) -> ReadinessIssue:
    return ReadinessIssue(
        code="invalid_input",
        message=message,
        input_name=name,
        repair=ReadinessRepair(
            action="fix_input",
            label="Review the harness inputs and try again.",
            target=name,
        ),
    )


def _exact_secret_ref(value: Any) -> Optional[SecretBindingRef]:
    try:
        marker = SecretBindingRef.model_validate(value)
        validate_secret_binding_name(marker.binding)
        return marker
    except (TypeError, ValueError):
        return None


def _contains_external_ref(value: Any) -> bool:
    """Find schema reference keywords whose string value is not a fragment.

    A property literally named ``$ref`` has a schema object as its value and is
    therefore not confused with the ``$ref`` keyword itself.
    """
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"$ref", "$dynamicRef"} and isinstance(child, str):
                if not child.startswith("#"):
                    return True
                continue
            if _contains_external_ref(child):
                return True
        return False
    if isinstance(value, list):
        return any(_contains_external_ref(child) for child in value)
    return False


def workflow_assignment_issues(
    workflow: WorkflowSpec, *, router: Optional[Router]
) -> list[ReadinessIssue]:
    """Preflight declared stage assignments with live routing truth.

    Unconstrained legacy workflows remain untouched. Blueprint and ad-hoc
    JSON/YAML sources call this same function before any run journal exists.
    """
    if router is None:
        return []
    issues: list[ReadinessIssue] = []
    for step in workflow.steps:
        if not (
            step.worker_id or step.role or step.required_capabilities
            or step.tier_hint is not None
        ):
            continue
        task = step.to_task({})
        candidates = [
            (tier, member)
            for tier in Tier
            for member in router.pool(tier)
            if not task.worker_id or member.worker_id == task.worker_id
        ]
        if task.worker_id and not candidates:
            issues.append(ReadinessIssue(
                code="missing_worker",
                message=f"Pinned worker {task.worker_id!r} is not available.",
                stage_id=step.id,
                worker_id=task.worker_id,
                repair=ReadinessRepair(
                    action="choose_worker",
                    label="Choose an available worker for this stage.",
                    target=task.worker_id,
                ),
            ))
            continue
        decisions = [
            (member, router.eligibility(member, tier, task))
            for tier, member in candidates
        ]
        eligible = [(member, result) for member, result in decisions if result.eligible]
        if (not task.worker_id and eligible) or (task.worker_id and len(eligible) == 1):
            continue
        if task.worker_id and len(eligible) > 1:
            code = "pin_mismatch"
            message = f"Pinned worker {task.worker_id!r} appears in multiple eligible pools."
            worker_id = task.worker_id
            eligibility_code = "duplicate_worker"
        elif task.worker_id:
            result = decisions[0][1]
            if result.code == "inactive":
                code = "missing_worker"
            elif result.code == "unsafe_recursion":
                code = "unsafe_recursion"
            else:
                code = "pin_mismatch"
            message = (
                f"Pinned worker {task.worker_id!r} cannot run this stage: "
                f"{result.detail}."
            )
            worker_id = task.worker_id
            eligibility_code = result.code
        else:
            result_codes = {result.code for _, result in decisions}
            code = (
                "unsafe_recursion"
                if decisions and result_codes == {"unsafe_recursion"}
                else "no_eligible_worker"
            )
            message = "No active worker satisfies this stage's assignment requirements."
            worker_id = None
            eligibility_code = next(iter(sorted(result_codes)), None)
        issues.append(ReadinessIssue(
            code=code,
            message=message,
            stage_id=step.id,
            worker_id=worker_id,
            eligibility_code=eligibility_code,
            repair=ReadinessRepair(
                action="configure_agent",
                label="Configure or choose an eligible agent for this stage.",
                target=worker_id or step.id,
            ),
        ))
    return issues


def prepare_blueprint_run(
    blueprint: BlueprintVersion,
    request_context: Mapping[str, Any],
    *,
    tools: ToolRegistry,
    mcp_servers: Mapping[str, MCPServerConfig],
    mcp_load_status: Optional[Mapping[str, Mapping[str, Any]]] = None,
    router: Optional[Router] = None,
    secret_bindings: Optional[SecretBindingLookup] = None,
) -> ReadinessResult:
    """Normalize declared inputs and check loaded tool truth without effects.

    Secret bindings are checked by logical name only. Their values are never
    resolved here or copied into the returned context, which is the only context
    exact-blueprint runs may journal. In the
    current v1 model, ``default=None`` means no default; explicit null defaults
    require a future schema revision that can distinguish absence from null.
    """
    issues: list[ReadinessIssue] = []
    mcp_load_status = mcp_load_status or {}
    declared = {item.name: item for item in blueprint.inputs}
    secret_names = {item.name for item in blueprint.inputs if item.secret}

    for name in sorted(set(request_context) - set(declared)):
        issues.append(_input_issue(f"Unknown input {name!r}.", name))

    normalized: dict[str, Any] = {}
    for item in blueprint.inputs:
        if not item.secret and item.default is not None:
            normalized[item.name] = item.default
    for name, value in blueprint.default_context.items():
        if name not in secret_names:
            normalized[name] = value

    secret_candidates: dict[str, Any] = {}
    for item in blueprint.inputs:
        if item.secret and item.default is not None:
            secret_candidates[item.name] = item.default
    for name, value in request_context.items():
        if name in secret_names:
            secret_candidates[name] = value
        elif name in declared:
            normalized[name] = value

    for item in blueprint.inputs:
        if _contains_external_ref(item.schema):
            issues.append(_input_issue(
                f"Input {item.name!r} uses unsupported external JSON Schema references.",
                item.name,
            ))
            continue
        try:
            Draft202012Validator.check_schema(item.schema)
        except SchemaError:
            issues.append(_input_issue(
                f"Input {item.name!r} has an invalid JSON Schema.", item.name
            ))
            continue

        if item.secret:
            if item.name not in secret_candidates:
                if item.required:
                    issues.append(_input_issue(
                        f"Required input {item.name!r} is missing.", item.name
                    ))
                continue
            marker = _exact_secret_ref(secret_candidates[item.name])
            if marker is None:
                issues.append(_input_issue(
                    f"Secret input {item.name!r} must use a logical binding reference.",
                    item.name,
                ))
                continue
            if secret_bindings is None or not secret_bindings.is_configured(marker.binding):
                issues.append(ReadinessIssue(
                    code="missing_secret_binding",
                    message=f"Secret binding {marker.binding!r} is not configured.",
                    input_name=item.name,
                    repair=ReadinessRepair(
                        action="configure_secret_binding",
                        label="Configure this secret binding in Settings.",
                        target=marker.binding,
                    ),
                ))
            continue

        if item.name not in normalized:
            if item.required:
                issues.append(_input_issue(
                    f"Required input {item.name!r} is missing.", item.name
                ))
            continue
        try:
            validator = Draft202012Validator(item.schema)
            errors = sorted(
                validator.iter_errors(normalized[item.name]),
                key=lambda error: (
                    tuple(str(part) for part in error.absolute_path), error.message
                ),
            )
        except Exception:  # malformed/resolution failures are input errors, never 500s
            issues.append(_input_issue(
                f"Input {item.name!r} could not be validated safely.", item.name
            ))
            continue
        if errors:
            issues.append(_input_issue(
                f"Input {item.name!r} does not match its JSON Schema.",
                item.name,
            ))

    # Only declared-secret step input locations carry secret semantics.  An
    # ordinary payload shaped like {"binding": "x"} remains ordinary data.
    for step in blueprint.workflow.steps:
        for name in sorted(secret_names & set(step.inputs)):
            marker = _exact_secret_ref(step.inputs[name])
            if marker is None:
                continue
            if secret_bindings is None or not secret_bindings.is_configured(marker.binding):
                issues.append(ReadinessIssue(
                    code="missing_secret_binding",
                    message=f"Secret binding {marker.binding!r} is not configured.",
                    stage_id=step.id,
                    repair=ReadinessRepair(
                        action="configure_secret_binding",
                        label="Configure this secret binding in Settings.",
                        target=marker.binding,
                    ),
                ))

    issues.extend(workflow_assignment_issues(blueprint.workflow, router=router))

    from metaharness.tools.mcp import mcp_config_fingerprint

    def _mcp_unavailable(
        *, step_id: str, tool_name: str, server_name: str, server: MCPServerConfig,
    ) -> Optional[ReadinessIssue]:
        status = mcp_load_status.get(server_name)
        if not server.enabled:
            state = "disabled"
            message = f"MCP server {server_name!r} is disabled."
            action = "enable_mcp"
            label = "Enable this MCP server in Settings."
        elif status is None:
            state = "never_loaded"
            message = f"MCP server {server_name!r} has never been loaded."
            action = "load_mcp"
            label = "Load this MCP server and retry readiness."
        elif status.get("fingerprint") != mcp_config_fingerprint(server):
            state = "stale_config"
            message = f"MCP server {server_name!r} changed since its tools were loaded."
            action = "load_mcp"
            label = "Reload this MCP server after its configuration change."
        elif status.get("status") != "loaded":
            state = str(status.get("status") or "load_failed")
            if state not in {"load_failed", "zero_tools", "disabled"}:
                state = "load_failed"
            message = {
                "load_failed": f"MCP server {server_name!r} failed to load.",
                "zero_tools": f"MCP server {server_name!r} loaded zero tools.",
                "disabled": f"MCP server {server_name!r} is disabled.",
            }[state]
            action = "enable_mcp" if state == "disabled" else "load_mcp"
            label = (
                "Enable this MCP server in Settings."
                if state == "disabled"
                else "Load this MCP server and retry readiness."
            )
        else:
            return None
        return ReadinessIssue(
            code="unloaded_mcp",
            message=message,
            stage_id=step_id,
            tool=tool_name,
            server=server_name,
            mcp_state=state,
            repair=ReadinessRepair(action=action, label=label, target=server_name),
        )

    for step in blueprint.workflow.steps:
        for tool_name in step.tools:
            tool = tools.get(tool_name)
            source_server = (
                tool.source[len("mcp:"):]
                if tool is not None and tool.source.startswith("mcp:") else ""
            )
            server_name = source_server or (
                tool_name.split(".", 1)[0] if "." in tool_name else ""
            )
            server = mcp_servers.get(server_name) if server_name else None
            if source_server and server is None:
                issues.append(ReadinessIssue(
                    code="missing_tool",
                    message=(
                        f"MCP tool {tool_name!r} has no configured server "
                        f"{source_server!r}."
                    ),
                    stage_id=step.id,
                    tool=tool_name,
                    server=source_server,
                    repair=ReadinessRepair(
                        action="choose_tool",
                        label="Choose a tool from a configured MCP server.",
                        target=tool_name,
                    ),
                ))
                continue
            if server is not None:
                unavailable = _mcp_unavailable(
                    step_id=step.id, tool_name=tool_name,
                    server_name=server_name, server=server,
                )
                if unavailable is not None:
                    issues.append(unavailable)
                    continue
            if tool is not None:
                continue
            issues.append(ReadinessIssue(
                code="missing_tool",
                message=f"Tool {tool_name!r} is not available.",
                stage_id=step.id,
                tool=tool_name,
                server=server_name or None,
                repair=ReadinessRepair(
                    action="choose_tool",
                    label="Choose an available tool for this stage.",
                    target=tool_name,
                ),
            ))

    return ReadinessResult(
        blueprint_ref=blueprint.ref,
        ready=not issues,
        normalized_context=normalized,
        issues=issues,
    )
