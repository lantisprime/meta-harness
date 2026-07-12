"""Validate and load a portable Blueprint package without extracting it."""
from __future__ import annotations

import json

from pydantic import ValidationError

from metaharness.blueprints.models import BlueprintVersion
from metaharness.portable.archive import read_safe_zip
from metaharness.portable.builder import requirements_for_blueprint
from metaharness.portable.deployment import deployment_layout
from metaharness.portable.integrity import (
    PortableIntegrityError,
    assert_blueprint_input_defaults_safe,
    assert_reference_values_safe,
    assert_secret_safe,
    canonical_json_bytes,
    package_content_digest,
    sha256_hex,
)
from metaharness.portable.launchers import launcher_layout
from metaharness.portable.models import LoadedPortablePackage, PortableManifest


def _json_document(raw: bytes, *, name: str) -> object:
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PortableIntegrityError(f"{name} is not valid UTF-8 JSON") from exc


def load_portable_package(payload: bytes) -> LoadedPortablePackage:
    files = read_safe_zip(payload)
    required = {"manifest.json", "harness.json", "workflow.json"}
    missing_required = required - set(files)
    if missing_required:
        raise PortableIntegrityError(f"package is missing files: {sorted(missing_required)}")
    manifest_document = _json_document(files["manifest.json"], name="manifest.json")
    try:
        manifest = PortableManifest.model_validate(manifest_document)
    except ValidationError as exc:
        raise PortableIntegrityError("manifest.json does not match schema v1") from exc
    manifest_secret_projection = manifest.model_dump(mode="json")
    manifest_secret_projection.pop("deployment_options", None)
    assert_secret_safe(manifest_secret_projection, location="manifest")
    if manifest.deployment_options is not None:
        assert_reference_values_safe(
            manifest.deployment_options.model_dump(mode="json"),
            location="manifest.deployment_options",
        )
    if canonical_json_bytes(manifest_document) != files["manifest.json"]:
        raise PortableIntegrityError("manifest.json is not canonical JSON")
    declared = {entry.path for entry in manifest.files}
    actual_artifacts = set(files) - {"manifest.json"}
    missing = declared - actual_artifacts
    extra = actual_artifacts - declared
    if missing or extra:
        raise PortableIntegrityError(
            f"package contents differ from manifest (missing={sorted(missing)}, extra={sorted(extra)})"
        )
    for entry in manifest.files:
        raw = files[entry.path]
        if len(raw) != entry.size or sha256_hex(raw) != entry.sha256:
            raise PortableIntegrityError(f"digest mismatch for {entry.path}")
    expected_launcher_files: dict[str, bytes] = {}
    launcher_targets = {"codex", "claude-code", "pi", "opencode"}
    for target in manifest.targets:
        if target in launcher_targets:
            expected_launcher_files.update(launcher_layout(target))
    actual_launcher_paths = {
        path for path in actual_artifacts if path.startswith("launchers/")
    }
    if actual_launcher_paths != set(expected_launcher_files):
        raise PortableIntegrityError("launcher layout differs from selected targets")
    for path, expected in expected_launcher_files.items():
        if files[path] != expected:
            raise PortableIntegrityError(f"launcher layout tampered: {path}")

    deployment_paths = {
        path for path in actual_artifacts
        if path.startswith("container/") or path.startswith("cloud/")
    }
    core_files = [
        entry for entry in manifest.files if entry.path not in deployment_paths
    ]
    expected_deployment_digest = (
        package_content_digest(
            core_files,
            blueprint_ref=manifest.blueprint_ref.model_dump(mode="json"),
            eval_refs=[ref.model_dump(mode="json") for ref in manifest.eval_refs],
            targets=list(manifest.targets),
            requirements=manifest.requirements.model_dump(mode="json"),
            generator_version=manifest.generator_version,
            schema_version=manifest.schema_version,
            deployment_options=manifest.deployment_options.model_dump(mode="json"),
            deployments=[item.model_dump(mode="json") for item in manifest.deployments],
        )
        if manifest.deployment_options is not None else None
    )
    if manifest.deployment_digest != expected_deployment_digest:
        raise PortableIntegrityError("manifest deployment_digest mismatch")
    try:
        expected_deployment_files = (
            deployment_layout(
                targets=manifest.targets,
                options=manifest.deployment_options,
                blueprint_ref=manifest.blueprint_ref,
                deployment_digest=expected_deployment_digest,
            )
            if manifest.deployment_options is not None else {}
        )
    except ValueError as exc:
        raise PortableIntegrityError(f"invalid deployment options: {exc}") from exc
    if deployment_paths != set(expected_deployment_files):
        raise PortableIntegrityError("deployment layout differs from selected targets")
    for path, expected in expected_deployment_files.items():
        if files[path] != expected:
            raise PortableIntegrityError(f"deployment layout tampered: {path}")
    expected_content = package_content_digest(
        manifest.files,
        blueprint_ref=manifest.blueprint_ref.model_dump(mode="json"),
        eval_refs=[ref.model_dump(mode="json") for ref in manifest.eval_refs],
        targets=list(manifest.targets),
        requirements=manifest.requirements.model_dump(mode="json"),
        generator_version=manifest.generator_version,
        schema_version=manifest.schema_version,
        deployment_options=(
            manifest.deployment_options.model_dump(mode="json")
            if manifest.deployment_options is not None else None
        ),
        deployments=[item.model_dump(mode="json") for item in manifest.deployments],
        deployment_digest=manifest.deployment_digest,
    )
    if expected_content != manifest.content_digest:
        raise PortableIntegrityError("manifest content_digest mismatch")

    harness_document = _json_document(files["harness.json"], name="harness.json")
    workflow_document = _json_document(files["workflow.json"], name="workflow.json")
    assert_secret_safe(harness_document)
    try:
        blueprint = BlueprintVersion.model_validate(harness_document)
    except ValidationError as exc:
        raise PortableIntegrityError("harness.json is not an exact BlueprintVersion") from exc
    assert_blueprint_input_defaults_safe(blueprint)
    expected_requirements = requirements_for_blueprint(blueprint)
    if manifest.requirements != expected_requirements:
        raise PortableIntegrityError("manifest requirements differ from blueprint projection")
    if blueprint.ref != manifest.blueprint_ref:
        raise PortableIntegrityError("harness identity differs from manifest blueprint_ref")
    if sha256_hex(files["harness.json"]) != manifest.blueprint_digest:
        raise PortableIntegrityError("manifest blueprint_digest mismatch")
    expected_workflow = blueprint.workflow.model_dump(mode="json")
    if workflow_document != expected_workflow:
        raise PortableIntegrityError("workflow.json is not derived from harness.json")
    if canonical_json_bytes(workflow_document) != files["workflow.json"]:
        raise PortableIntegrityError("workflow.json is not canonical JSON")
    if canonical_json_bytes(harness_document) != files["harness.json"]:
        raise PortableIntegrityError("harness.json is not canonical JSON")
    if sha256_hex(files["workflow.json"]) != manifest.workflow_digest:
        raise PortableIntegrityError("manifest workflow_digest mismatch")
    return LoadedPortablePackage(
        manifest=manifest,
        blueprint=blueprint,
        workflow=workflow_document,
    )
