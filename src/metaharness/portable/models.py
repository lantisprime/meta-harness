"""Strict, versioned models for portable Harness Blueprint packages."""
from __future__ import annotations

import re
from typing import Literal, Optional

from pydantic import Field, field_validator, model_validator

from metaharness.blueprints.models import ArtifactRef, BlueprintVersion, StrictModel


PortableTarget = Literal[
    "local", "codex", "claude-code", "pi", "opencode",
    "oci", "aws-apprunner", "gcp-cloud-run-service", "gcp-cloud-run-job",
    "azure-container-app",
]
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_GENERATOR_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")


class FileDigest(StrictModel):
    path: str
    sha256: str
    size: int = Field(ge=0, strict=True)

    @field_validator("path")
    @classmethod
    def _path_is_canonical(cls, value: str) -> str:
        if not value or value.startswith(("/", "\\")) or "\\" in value:
            raise ValueError("package file paths must be relative POSIX paths")
        parts = value.split("/")
        if any(part in {"", ".", ".."} for part in parts):
            raise ValueError("package file paths must be normalized")
        return value

    @field_validator("sha256")
    @classmethod
    def _digest_is_sha256(cls, value: str) -> str:
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("sha256 must be 64 lowercase hexadecimal characters")
        return value


class PortableRequirements(StrictModel):
    agent_roles: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    tool_ids: list[str] = Field(default_factory=list)
    mcp_servers: list[str] = Field(default_factory=list)
    secret_bindings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _requirements_are_sorted_sets(self) -> "PortableRequirements":
        for name in type(self).model_fields:
            values = getattr(self, name)
            if values != sorted(set(values)):
                raise ValueError(f"{name} must be sorted and unique")
        return self


class OCIPackageOptions(StrictModel):
    runtime_image: str


class AWSAppRunnerOptions(StrictModel):
    image: str
    service_name: str
    ecr_access_role_arn: str
    instance_role_arn: str
    remote_journal_ref: str
    secret_refs: dict[str, str] = Field(default_factory=dict)
    public: bool = False


class GCPCloudRunServiceOptions(StrictModel):
    image: str
    name: str
    service_account: str
    secret_refs: dict[str, str] = Field(default_factory=dict)
    storage_refs: dict[str, str] = Field(default_factory=dict)
    public: bool = False


class GCPCloudRunJobOptions(StrictModel):
    image: str
    name: str
    service_account: str
    context_secret_ref: str
    secret_refs: dict[str, str] = Field(default_factory=dict)
    storage_refs: dict[str, str] = Field(default_factory=dict)


class AzureContainerAppOptions(StrictModel):
    image: str
    name: str
    location: str
    environment_id: str
    identity_resource_id: str
    secret_refs: dict[str, str] = Field(default_factory=dict)
    storage_refs: dict[str, str] = Field(default_factory=dict)
    public: bool = False


class PortableDeploymentOptions(StrictModel):
    oci: Optional[OCIPackageOptions] = None
    aws_apprunner: Optional[AWSAppRunnerOptions] = Field(
        default=None, alias="aws-apprunner"
    )
    gcp_cloud_run_service: Optional[GCPCloudRunServiceOptions] = Field(
        default=None, alias="gcp-cloud-run-service"
    )
    gcp_cloud_run_job: Optional[GCPCloudRunJobOptions] = Field(
        default=None, alias="gcp-cloud-run-job"
    )
    azure_container_app: Optional[AzureContainerAppOptions] = Field(
        default=None, alias="azure-container-app"
    )

    def configured_targets(self) -> set[str]:
        return {
            alias
            for name, field in type(self).model_fields.items()
            if getattr(self, name) is not None
            for alias in [str(field.alias or name)]
        }


class DeploymentReadiness(StrictModel):
    target: Literal[
        "oci", "aws-apprunner", "gcp-cloud-run-service",
        "gcp-cloud-run-job", "azure-container-app",
    ]
    status: Literal["ready", "blocked"]
    deployable: bool
    reason: str
    missing_commands: list[str] = Field(default_factory=list)

    @field_validator("missing_commands")
    @classmethod
    def _commands_are_canonical(cls, values: list[str]) -> list[str]:
        if values != sorted(set(values)):
            raise ValueError("missing_commands must be a sorted set")
        return values

    @model_validator(mode="after")
    def _status_is_truthful(self) -> "DeploymentReadiness":
        if self.deployable != (self.status == "ready"):
            raise ValueError("deployment status and deployable flag must agree")
        if self.status == "ready" and self.missing_commands:
            raise ValueError("a ready deployment cannot have missing commands")
        if not self.reason.strip():
            raise ValueError("deployment readiness requires a reason")
        return self


class PortableManifest(StrictModel):
    """Integrity index for the first portable package schema."""

    schema_version: Literal[1] = 1
    generator_version: str
    generated_at: int = Field(ge=0, strict=True)
    blueprint_ref: ArtifactRef
    blueprint_digest: str
    workflow_digest: str
    eval_refs: list[ArtifactRef] = Field(default_factory=list)
    targets: list[PortableTarget]
    requirements: PortableRequirements = Field(default_factory=PortableRequirements)
    deployment_options: Optional[PortableDeploymentOptions] = Field(
        default=None, exclude_if=lambda value: value is None
    )
    deployments: list[DeploymentReadiness] = Field(
        default_factory=list, exclude_if=lambda value: not value
    )
    deployment_digest: Optional[str] = Field(
        default=None, exclude_if=lambda value: value is None
    )
    files: list[FileDigest]
    content_digest: str

    @field_validator("generator_version")
    @classmethod
    def _generator_is_named(cls, value: str) -> str:
        credential_prefixes = ("gho_", "ghp_", "github_pat_", "sk-", "xoxb-", "xoxp-")
        if not _GENERATOR_VERSION_RE.fullmatch(value) or value.casefold().startswith(
            credential_prefixes
        ):
            raise ValueError("generator_version must be a safe version identifier")
        return value

    @field_validator(
        "blueprint_digest", "workflow_digest", "content_digest", "deployment_digest"
    )
    @classmethod
    def _digest_is_sha256(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        if not _SHA256_RE.fullmatch(value):
            raise ValueError("digest must be 64 lowercase hexadecimal characters")
        return value

    @model_validator(mode="after")
    def _collections_are_canonical(self) -> "PortableManifest":
        targets = list(self.targets)
        if targets != sorted(set(targets)):
            raise ValueError("targets must be sorted and unique")
        eval_keys = [(ref.id, ref.version) for ref in self.eval_refs]
        if eval_keys != sorted(set(eval_keys)):
            raise ValueError("eval_refs must be sorted and unique")
        paths = [item.path for item in self.files]
        if paths != sorted(set(paths)):
            raise ValueError("files must be sorted with unique paths")
        deployment_targets = {
            target for target in targets
            if target in {
                "oci", "aws-apprunner", "gcp-cloud-run-service",
                "gcp-cloud-run-job", "azure-container-app",
            }
        }
        configured = (
            self.deployment_options.configured_targets()
            if self.deployment_options is not None else set()
        )
        readiness_targets = {item.target for item in self.deployments}
        if configured != deployment_targets or readiness_targets != deployment_targets:
            raise ValueError(
                "deployment targets, options, and readiness records must match exactly"
            )
        if [item.target for item in self.deployments] != sorted(readiness_targets):
            raise ValueError("deployments must be sorted with unique targets")
        if bool(deployment_targets) != (self.deployment_digest is not None):
            raise ValueError(
                "deployment_digest must be present exactly when deployment targets exist"
            )
        return self


class LoadedPortablePackage(StrictModel):
    manifest: PortableManifest
    blueprint: BlueprintVersion
    workflow: dict
