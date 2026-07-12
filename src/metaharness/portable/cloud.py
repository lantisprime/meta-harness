"""Pure, deterministic cloud descriptor renderers.

These functions generate provider input artifacts only. They never invoke a
provider API or CLI and accept deployment values from the caller, not a
Blueprint.
"""
from __future__ import annotations

from collections.abc import Mapping
import ipaddress
import re
from typing import Any

import yaml

from metaharness.blueprints.models import ArtifactRef
from metaharness.portable.container import JOURNAL_PATH, WORKSPACE_PATH, require_digest_image


SERVICE_ARGV = [
    "metaharness", "serve", "--package", "/opt/metaharness/package",
    "--host", "0.0.0.0", "--port", "8000",
]
JOB_ARGV = [
    "metaharness", "blueprint", "run", "/opt/metaharness/package",
    "--context-file", "/run/metaharness/context.json", "--workspace", WORKSPACE_PATH,
    "--format", "jsonl", "--approval", "stop",
]
_PACKAGE_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_GCP_RESOURCE_NAME = re.compile(r"^[a-z](?:[a-z0-9-]{0,47}[a-z0-9])?$")
_AZURE_APP_NAME = re.compile(r"^[a-z](?:(?:[a-z0-9]|-(?!-)){0,30}[a-z0-9])$")
_AWS_SERVICE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{2,38}[A-Za-z0-9]$")
_AWS_ROLE_ARN = re.compile(
    r"^arn:(?:aws|aws-us-gov|aws-cn):iam::[0-9]{12}:role/[A-Za-z0-9+=,.@_/-]+$"
)
_AWS_SECRET_ARN = re.compile(
    r"^arn:(?:aws|aws-us-gov|aws-cn):secretsmanager:[a-z0-9-]+:[0-9]{12}:secret:[A-Za-z0-9/_+=.@:-]+$"
)
_AWS_ECR_IMAGE = re.compile(
    r"^[0-9]{12}\.dkr\.ecr\.[a-z0-9-]+\.amazonaws\.com(?:\.cn)?/"
    r"[a-z0-9]+(?:[._/-][a-z0-9]+)*@sha256:[0-9a-f]{64}$"
)
_AWS_REMOTE_JOURNAL = re.compile(
    r"^s3://(?P<bucket>[^/]+)(?:/(?P<prefix>[A-Za-z0-9._/-]+))?$"
)
_GCP_SERVICE_ACCOUNT = re.compile(
    r"^[a-z][a-z0-9-]{4,28}[a-z0-9]@[a-z][a-z0-9-]{4,28}[a-z0-9]\.iam\.gserviceaccount\.com$"
)
_GCP_SECRET_REF = re.compile(
    r"^[A-Za-z][A-Za-z0-9_-]{0,254}$"
)
_GCP_BUCKET = re.compile(r"^[a-z0-9][a-z0-9._-]{1,61}[a-z0-9]$")
_AZURE_IDENTITY = re.compile(
    r"^/subscriptions/[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}/resourceGroups/[A-Za-z0-9._()-]+/providers/"
    r"Microsoft\.ManagedIdentity/userAssignedIdentities/[A-Za-z0-9._()-]+$",
    re.IGNORECASE,
)
_AZURE_ENVIRONMENT = re.compile(
    r"^/subscriptions/[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}/resourceGroups/[A-Za-z0-9._()-]+/providers/"
    r"Microsoft\.App/managedEnvironments/[A-Za-z0-9._()-]+$",
    re.IGNORECASE,
)
_AZURE_KEY_VAULT = re.compile(
    r"^https://[a-z0-9-]{3,24}\.vault\.azure\.net/secrets/[A-Za-z0-9-]+(?:/[A-Za-z0-9]+)?$"
)
_AZURE_BINDING = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
_AZURE_LOCATION = re.compile(r"^[a-z][a-z0-9]{1,31}$")


def _required(label: str, value: str) -> str:
    if not value or "\x00" in value:
        raise ValueError(f"{label} must be non-empty")
    return value


def _matches(label: str, value: str, pattern: re.Pattern[str]) -> str:
    _required(label, value)
    if not pattern.fullmatch(value):
        raise ValueError(f"{label} is not a supported provider reference")
    return value


def _refs(
    values: Mapping[str, str] | None,
    *,
    name_pattern: re.Pattern[str],
    reference_pattern: re.Pattern[str],
    label: str,
) -> list[tuple[str, str]]:
    items = []
    for name, reference in sorted((values or {}).items()):
        items.append((
            _matches(f"{label} binding name", name, name_pattern),
            _matches(f"{label} reference", reference, reference_pattern),
        ))
    return items


def _metadata(package_digest: str, blueprint_ref: str) -> dict[str, str]:
    try:
        artifact_id, version_text = blueprint_ref.rsplit("@", 1)
        exact_ref = ArtifactRef(id=artifact_id, version=int(version_text))
        if blueprint_ref != f"{exact_ref.id}@{exact_ref.version}":
            raise ValueError
    except (AttributeError, TypeError, ValueError):
        raise ValueError("blueprint_ref must be a safe slug@positive-version") from None
    return {
        "metaharness.io/blueprint": f"{exact_ref.id}@{exact_ref.version}",
        "metaharness.io/package-digest": _matches(
            "package_digest", package_digest, _PACKAGE_DIGEST
        ),
    }


def _public(value: bool) -> bool:
    if type(value) is not bool:
        raise ValueError("public must be an explicit boolean")
    return value


def _s3_journal_ref(value: str) -> str:
    _required("remote_journal_ref", value)
    match = _AWS_REMOTE_JOURNAL.fullmatch(value)
    if match is None:
        raise ValueError("remote_journal_ref is not a supported S3 reference")
    bucket = match.group("bucket")
    prefix = match.group("prefix")
    bucket_valid = (
        3 <= len(bucket) <= 63
        and re.fullmatch(r"[a-z0-9][a-z0-9.-]*[a-z0-9]", bucket) is not None
        and ".." not in bucket
        and ".-" not in bucket
        and "-." not in bucket
    )
    try:
        ipaddress.ip_address(bucket)
        bucket_is_ip = True
    except ValueError:
        bucket_is_ip = False
    if not bucket_valid or bucket_is_ip:
        raise ValueError("remote_journal_ref has an invalid S3 bucket name")
    if prefix is not None and any(part in {"", ".", ".."} for part in prefix.split("/")):
        raise ValueError("remote_journal_ref has an invalid S3 key prefix")
    return value


def _storage_bindings(
    values: Mapping[str, str] | None,
    *,
    reference_pattern: re.Pattern[str],
    provider: str,
    required: bool,
) -> list[tuple[str, str, str]]:
    supplied = dict(values or {})
    allowed = {"workspace": WORKSPACE_PATH, "journal": JOURNAL_PATH}
    unknown = set(supplied) - set(allowed)
    if unknown:
        raise ValueError(f"{provider} storage binding names must be workspace and journal")
    if required and set(supplied) != set(allowed):
        raise ValueError(f"{provider} service requires workspace and journal storage references")
    if supplied and set(supplied) != set(allowed):
        raise ValueError(f"{provider} storage bindings must provide workspace and journal together")
    return [
        (name, _matches(f"{provider} storage reference", supplied[name], reference_pattern), path)
        for name, path in allowed.items()
        if name in supplied
    ]


def render_yaml(document: Mapping[str, Any]) -> bytes:
    """Serialize a renderer result deterministically."""
    return yaml.safe_dump(dict(document), sort_keys=True).encode()


def aws_apprunner_service(
    *, image: str, service_name: str, package_digest: str, blueprint_ref: str,
    ecr_access_role_arn: str, instance_role_arn: str,
    remote_journal_ref: str, secret_refs: Mapping[str, str] | None = None,
    public: bool = False,
) -> dict[str, Any]:
    """Generate an AWS CloudFormation App Runner service resource."""
    image = require_digest_image(image)
    image = _matches("AWS ECR image", image, _AWS_ECR_IMAGE)
    public = _public(public)
    remote_journal_ref = _s3_journal_ref(remote_journal_ref)
    secrets = {
        name: ref
        for name, ref in _refs(
            secret_refs,
            name_pattern=_ENV_NAME,
            reference_pattern=_AWS_SECRET_ARN,
            label="AWS secret",
        )
    }
    metadata: dict[str, Any] = _metadata(package_digest, blueprint_ref)
    metadata["MetaharnessDurability"] = {
        "LocalFilesystem": "ephemeral",
        "JournalMode": "caller-supplied-remote-reference",
        "JournalReference": remote_journal_ref,
    }
    return {
        "AWSTemplateFormatVersion": "2010-09-09",
        "Metadata": metadata,
        "Resources": {
            "HarnessService": {
                "Type": "AWS::AppRunner::Service",
                "Properties": {
                    "ServiceName": _matches(
                        "service_name", service_name, _AWS_SERVICE_NAME
                    ),
                    "HealthCheckConfiguration": {
                        "Protocol": "HTTP", "Path": "/health",
                        "HealthyThreshold": 1, "UnhealthyThreshold": 3,
                    },
                    "InstanceConfiguration": {
                        "InstanceRoleArn": _matches(
                            "instance_role_arn", instance_role_arn, _AWS_ROLE_ARN
                        ),
                    },
                    "NetworkConfiguration": {
                        "IngressConfiguration": {"IsPubliclyAccessible": bool(public)},
                    },
                    "SourceConfiguration": {
                        "AutoDeploymentsEnabled": False,
                        "AuthenticationConfiguration": {
                            "AccessRoleArn": _matches(
                                "ecr_access_role_arn", ecr_access_role_arn, _AWS_ROLE_ARN
                            ),
                        },
                        "ImageRepository": {
                            "ImageIdentifier": image,
                            "ImageRepositoryType": "ECR",
                            "ImageConfiguration": {
                                "Port": "8000",
                                "RuntimeEnvironmentSecrets": secrets,
                                "RuntimeEnvironmentVariables": {
                                    "METAHARNESS_REMOTE_JOURNAL_REF": remote_journal_ref,
                                    "METAHARNESS_WORKSPACE": WORKSPACE_PATH,
                                },
                            },
                        },
                    },
                },
            },
        },
    }


def gcp_cloud_run_service(
    *, image: str, name: str, package_digest: str, blueprint_ref: str,
    service_account: str, secret_refs: Mapping[str, str] | None = None,
    storage_refs: Mapping[str, str] | None = None, public: bool = False,
) -> dict[str, Any]:
    """Generate a Cloud Run service descriptor; public IAM is explicit metadata only."""
    image = require_digest_image(image)
    public = _public(public)
    env = [
        {"name": key, "valueFrom": {"secretKeyRef": {"name": ref, "key": "latest"}}}
        for key, ref in _refs(
            secret_refs, name_pattern=_ENV_NAME, reference_pattern=_GCP_SECRET_REF,
            label="GCP secret",
        )
    ]
    storage = _storage_bindings(
        storage_refs, reference_pattern=_GCP_BUCKET, provider="GCP", required=True
    )
    volumes = [
        {"name": name, "csi": {"driver": "gcsfuse.run.googleapis.com", "readOnly": False,
                                "volumeAttributes": {"bucketName": ref}}}
        for name, ref, _ in storage
    ]
    annotations = {"run.googleapis.com/ingress": "all" if public else "internal"}
    if public:
        annotations["run.googleapis.com/invoker-iam-disabled"] = "true"
    annotations.update(_metadata(package_digest, blueprint_ref))
    container: dict[str, Any] = {
        "image": image, "command": SERVICE_ARGV[:1], "args": SERVICE_ARGV[1:],
        "env": env, "ports": [{"containerPort": 8000}],
        "startupProbe": {
            "httpGet": {"path": "/health", "port": 8000},
            "failureThreshold": 12,
            "periodSeconds": 5,
        },
        "livenessProbe": {
            "httpGet": {"path": "/health", "port": 8000},
            "periodSeconds": 30,
        },
    }
    if volumes:
        container["volumeMounts"] = [
            {"name": name, "mountPath": path}
            for name, _, path in storage
        ]
    template_spec: dict[str, Any] = {
        "serviceAccountName": _matches(
            "service_account", service_account, _GCP_SERVICE_ACCOUNT
        ),
        "containers": [container],
    }
    if volumes:
        template_spec["volumes"] = volumes
    return {
        "apiVersion": "serving.knative.dev/v1", "kind": "Service",
        "metadata": {
            "name": _matches("name", name, _GCP_RESOURCE_NAME),
            "annotations": annotations,
        },
        "spec": {"template": {"spec": template_spec}},
    }


def gcp_cloud_run_job(
    *, image: str, name: str, package_digest: str, blueprint_ref: str,
    service_account: str, context_secret_ref: str,
    secret_refs: Mapping[str, str] | None = None,
    storage_refs: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Generate a Cloud Run job descriptor without service ingress/port fields."""
    image = require_digest_image(image)
    context_secret_ref = _matches(
        "context_secret_ref", context_secret_ref, _GCP_SECRET_REF
    )
    env = [
        {"name": key, "valueFrom": {"secretKeyRef": {"name": ref, "key": "latest"}}}
        for key, ref in _refs(
            secret_refs, name_pattern=_ENV_NAME, reference_pattern=_GCP_SECRET_REF,
            label="GCP secret",
        )
    ]
    storage = _storage_bindings(
        storage_refs, reference_pattern=_GCP_BUCKET, provider="GCP", required=True
    )
    volumes = [
        {"name": name, "csi": {"driver": "gcsfuse.run.googleapis.com", "readOnly": False,
                                "volumeAttributes": {"bucketName": ref}}}
        for name, ref, _ in storage
    ]
    volumes.append({
        "name": "run-context",
        "secret": {
            "secretName": context_secret_ref,
            "items": [{"key": "latest", "path": "context.json"}],
        },
    })
    container: dict[str, Any] = {
        "image": image, "command": JOB_ARGV[:1], "args": JOB_ARGV[1:], "env": env,
    }
    container["volumeMounts"] = [
        {"name": name, "mountPath": path}
        for name, _, path in storage
    ] + [{"name": "run-context", "mountPath": "/run/metaharness"}]
    execution_spec: dict[str, Any] = {
        "serviceAccountName": _matches(
            "service_account", service_account, _GCP_SERVICE_ACCOUNT
        ),
        "containers": [container], "maxRetries": 0,
    }
    execution_spec["volumes"] = volumes
    return {
        "apiVersion": "run.googleapis.com/v1", "kind": "Job",
        "metadata": {"name": _matches("name", name, _GCP_RESOURCE_NAME),
                     "annotations": _metadata(package_digest, blueprint_ref)},
        "spec": {"template": {"spec": {"template": {"spec": execution_spec}}}},
    }


def azure_container_app(
    *, image: str, name: str, location: str, environment_id: str,
    package_digest: str, blueprint_ref: str, identity_resource_id: str,
    secret_refs: Mapping[str, str] | None = None,
    storage_refs: Mapping[str, str] | None = None, public: bool = False,
) -> dict[str, Any]:
    """Generate an ARM template resource for one Azure Container App service."""
    image = require_digest_image(image)
    public = _public(public)
    identity = _matches("identity_resource_id", identity_resource_id, _AZURE_IDENTITY)
    azure_secrets = _refs(
        secret_refs, name_pattern=_AZURE_BINDING, reference_pattern=_AZURE_KEY_VAULT,
        label="Azure secret",
    )
    secrets = [
        {"name": name, "keyVaultUrl": ref, "identity": identity}
        for name, ref in azure_secrets
    ]
    env = [{"name": name, "secretRef": name} for name, _ in azure_secrets]
    storage = _storage_bindings(
        storage_refs, reference_pattern=_AZURE_BINDING, provider="Azure", required=True
    )
    volumes = [
        {"name": name, "storageType": "AzureFile", "storageName": ref}
        for name, ref, _ in storage
    ]
    labels = _metadata(package_digest, blueprint_ref)
    properties: dict[str, Any] = {
        "managedEnvironmentId": _matches(
            "environment_id", environment_id, _AZURE_ENVIRONMENT
        ),
        "configuration": {
            "activeRevisionsMode": "Single", "secrets": secrets,
            "ingress": {"external": bool(public), "targetPort": 8000,
                        "allowInsecure": False, "transport": "auto"},
        },
        "template": {
            "containers": [{"name": "metaharness", "image": image,
                            "command": SERVICE_ARGV, "env": env,
                            "probes": [
                                {
                                    "type": "Startup",
                                    "httpGet": {
                                        "path": "/health", "port": 8000,
                                        "scheme": "HTTP",
                                    },
                                    "failureThreshold": 12,
                                    "periodSeconds": 5,
                                },
                                {
                                    "type": "Liveness",
                                    "httpGet": {
                                        "path": "/health", "port": 8000,
                                        "scheme": "HTTP",
                                    },
                                    "periodSeconds": 30,
                                },
                            ],
                            "volumeMounts": [
                                {"volumeName": binding, "mountPath": path}
                                for binding, _, path in storage
                            ]}],
            "volumes": volumes,
        },
    }
    return {
        "$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentTemplate.json#",
        "contentVersion": "1.0.0.0",
        "resources": [{
            "type": "Microsoft.App/containerApps", "apiVersion": "2024-03-01",
            "name": _matches("name", name, _AZURE_APP_NAME),
            "location": _matches("location", location, _AZURE_LOCATION),
            "tags": labels,
            "identity": {"type": "UserAssigned", "userAssignedIdentities": {identity: {}}},
            "properties": properties,
        }],
    }
