"""Deterministic integration of OCI and cloud deployment artifacts."""
from __future__ import annotations

import json
from collections.abc import Iterable

from metaharness.blueprints.models import ArtifactRef
from metaharness.portable.cloud import (
    aws_apprunner_service,
    azure_container_app,
    gcp_cloud_run_job,
    gcp_cloud_run_service,
)
from metaharness.portable.container import build_oci_context
from metaharness.portable.integrity import canonical_json_bytes
from metaharness.portable.models import (
    DeploymentReadiness,
    PortableDeploymentOptions,
)


DEPLOYMENT_TARGETS = frozenset({
    "oci", "aws-apprunner", "gcp-cloud-run-service",
    "gcp-cloud-run-job", "azure-container-app",
})


def deployment_readiness(targets: Iterable[str]) -> list[DeploymentReadiness]:
    records = []
    for target in sorted(set(targets) & DEPLOYMENT_TARGETS):
        records.append(DeploymentReadiness(
            target=target,
            status="blocked",
            deployable=False,
            reason=(
                "runtime commands and health probes are generated, but an enabled "
                "agent configuration must be supplied by the deployment environment"
            ),
        ))
    return records


def deployment_layout(
    *,
    targets: Iterable[str],
    options: PortableDeploymentOptions,
    blueprint_ref: ArtifactRef,
    deployment_digest: str,
) -> dict[str, bytes]:
    """Render exact generator-owned deployment files from validated options."""
    selected = set(targets) & DEPLOYMENT_TARGETS
    configured = options.configured_targets()
    if selected != configured:
        raise ValueError(
            "deployment targets require exactly one matching options object each"
        )
    package_digest = f"sha256:{deployment_digest}"
    exact_ref = f"{blueprint_ref.id}@{blueprint_ref.version}"
    files: dict[str, bytes] = {}

    if options.oci is not None:
        files.update(build_oci_context(options.oci.runtime_image))

    if options.aws_apprunner is not None:
        document = aws_apprunner_service(
            **options.aws_apprunner.model_dump(mode="python"),
            package_digest=package_digest, blueprint_ref=exact_ref,
        )
        files["cloud/aws-apprunner.json"] = canonical_json_bytes(document)

    if options.gcp_cloud_run_service is not None:
        document = gcp_cloud_run_service(
            **options.gcp_cloud_run_service.model_dump(mode="python"),
            package_digest=package_digest, blueprint_ref=exact_ref,
        )
        files["cloud/gcp-cloud-run-service.json"] = canonical_json_bytes(document)

    if options.gcp_cloud_run_job is not None:
        document = gcp_cloud_run_job(
            **options.gcp_cloud_run_job.model_dump(mode="python"),
            package_digest=package_digest, blueprint_ref=exact_ref,
        )
        files["cloud/gcp-cloud-run-job.json"] = canonical_json_bytes(document)

    if options.azure_container_app is not None:
        document = azure_container_app(
            **options.azure_container_app.model_dump(mode="python"),
            package_digest=package_digest, blueprint_ref=exact_ref,
        )
        files["cloud/azure-container-app.json"] = canonical_json_bytes(document)

    return files
