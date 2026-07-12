"""Build a secret-free, reproducible portable Blueprint package."""
from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from metaharness.blueprints.models import ArtifactRef, BlueprintVersion
from metaharness.portable.archive import source_date_epoch, write_deterministic_zip
from metaharness.portable.integrity import (
    assert_blueprint_input_defaults_safe,
    assert_reference_values_safe,
    assert_secret_safe,
    canonical_json_bytes,
    digest_files,
    package_content_digest,
    sha256_hex,
)
from metaharness.portable.deployment import (
    DEPLOYMENT_TARGETS,
    deployment_layout,
    deployment_readiness,
)
from metaharness.portable.launchers import launcher_layout
from metaharness.portable.models import (
    PortableDeploymentOptions,
    PortableManifest,
    PortableRequirements,
    PortableTarget,
)


GENERATOR_VERSION = "metaharness-portable-1"
_MCP_DOUBLE_UNDERSCORE = re.compile(r"^mcp__([A-Za-z0-9_-]+)__")
_MCP_COLON = re.compile(r"^mcp:([A-Za-z0-9_-]+):")


def _binding_names(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        if set(value) == {"binding"} and isinstance(value["binding"], str):
            found.add(value["binding"])
        else:
            for child in value.values():
                found.update(_binding_names(child))
    elif isinstance(value, list):
        for child in value:
            found.update(_binding_names(child))
    return found


def requirements_for_blueprint(blueprint: BlueprintVersion) -> PortableRequirements:
    """Project authoritative, reproducible runtime requirements from a blueprint."""
    tool_ids = sorted({tool for step in blueprint.workflow.steps for tool in step.tools})
    mcp_servers: set[str] = set()
    for tool in tool_ids:
        match = _MCP_DOUBLE_UNDERSCORE.match(tool) or _MCP_COLON.match(tool)
        if match:
            mcp_servers.add(match.group(1))
    document = blueprint.model_dump(mode="json")
    return PortableRequirements(
        tool_ids=tool_ids,
        mcp_servers=sorted(mcp_servers),
        secret_bindings=sorted(_binding_names(document)),
    )


def build_portable_package(
    blueprint: BlueprintVersion,
    *,
    targets: Iterable[PortableTarget] = ("local",),
    eval_refs: Iterable[ArtifactRef] | None = None,
    generator_version: str = GENERATOR_VERSION,
    generated_at: int | None = None,
    deployment_options: PortableDeploymentOptions | dict[str, Any] | None = None,
) -> bytes:
    """Return a deterministic ZIP containing an exact immutable Blueprint.

    Paths and executable content are generator-owned constants. Blueprint data
    contributes only canonical JSON documents and requirement metadata.
    """
    target_list = sorted(set(targets))
    if not target_list:
        raise ValueError("at least one portable target is required")
    # Runtime validation matters for callers that bypass static typing.
    launcher_targets = {"codex", "claude-code", "pi", "opencode"}
    allowed = {"local", *launcher_targets, *DEPLOYMENT_TARGETS}
    unsupported = set(target_list) - allowed
    if unsupported:
        raise ValueError(f"unsupported portable targets: {sorted(unsupported)}")
    selected_deployments = set(target_list) & DEPLOYMENT_TARGETS
    options = (
        PortableDeploymentOptions.model_validate(deployment_options)
        if deployment_options is not None else None
    )
    configured = options.configured_targets() if options is not None else set()
    if configured != selected_deployments:
        raise ValueError(
            "deployment targets require exactly one matching options object each"
        )
    if options is not None:
        assert_reference_values_safe(
            options.model_dump(mode="json"), location="deployment_options"
        )
    refs = sorted(
        set((ref.id, ref.version) for ref in (eval_refs if eval_refs is not None else blueprint.eval_suites))
    )
    exact_eval_refs = [ArtifactRef(id=artifact_id, version=version) for artifact_id, version in refs]

    harness_document = blueprint.model_dump(mode="json")
    workflow_document = blueprint.workflow.model_dump(mode="json")
    assert_blueprint_input_defaults_safe(blueprint)
    assert_secret_safe(harness_document)
    files = {
        "harness.json": canonical_json_bytes(harness_document),
        "workflow.json": canonical_json_bytes(workflow_document),
    }
    for target in target_list:
        if target in launcher_targets:
            files.update(launcher_layout(target))
    requirements = requirements_for_blueprint(blueprint)
    core_file_digests = digest_files(files)
    readiness = deployment_readiness(target_list)
    deployment_digest = (
        package_content_digest(
            core_file_digests,
            blueprint_ref=blueprint.ref.model_dump(mode="json"),
            eval_refs=[ref.model_dump(mode="json") for ref in exact_eval_refs],
            targets=target_list,
            requirements=requirements.model_dump(mode="json"),
            generator_version=generator_version,
            deployment_options=options.model_dump(mode="json"),
            deployments=[item.model_dump(mode="json") for item in readiness],
        )
        if selected_deployments else None
    )
    if options is not None:
        assert deployment_digest is not None
        files.update(deployment_layout(
            targets=target_list,
            options=options,
            blueprint_ref=blueprint.ref,
            deployment_digest=deployment_digest,
        ))
    file_digests = digest_files(files)
    content_digest = package_content_digest(
        file_digests,
        blueprint_ref=blueprint.ref.model_dump(mode="json"),
        eval_refs=[ref.model_dump(mode="json") for ref in exact_eval_refs],
        targets=target_list,
        requirements=requirements.model_dump(mode="json"),
        generator_version=generator_version,
        deployment_options=(
            options.model_dump(mode="json") if options is not None else None
        ),
        deployments=[item.model_dump(mode="json") for item in readiness],
        deployment_digest=deployment_digest,
    )
    manifest = PortableManifest(
        generator_version=generator_version,
        generated_at=source_date_epoch(generated_at),
        blueprint_ref=blueprint.ref,
        blueprint_digest=sha256_hex(files["harness.json"]),
        workflow_digest=sha256_hex(files["workflow.json"]),
        eval_refs=exact_eval_refs,
        targets=target_list,
        requirements=requirements,
        deployment_options=options,
        deployments=readiness,
        deployment_digest=deployment_digest,
        files=file_digests,
        content_digest=content_digest,
    )
    manifest_document = manifest.model_dump(mode="json")
    secret_projection = dict(manifest_document)
    # Field-name scanning cannot distinguish `secret_refs` from a secret value;
    # reference values were scanned above before rendering.
    secret_projection.pop("deployment_options", None)
    assert_secret_safe(secret_projection, location="manifest")
    files["manifest.json"] = canonical_json_bytes(manifest_document)
    return write_deterministic_zip(files, epoch=generated_at)
