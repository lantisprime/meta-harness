from __future__ import annotations

import json

import pytest
import yaml

from metaharness.portable.cloud import (
    JOB_ARGV,
    SERVICE_ARGV,
    aws_apprunner_service,
    azure_container_app,
    gcp_cloud_run_job,
    gcp_cloud_run_service,
    render_yaml,
)
from metaharness.portable.container import (
    JOURNAL_PATH,
    WORKSPACE_PATH,
    build_oci_context,
    require_digest_image,
)


IMAGE = "registry.example/metaharness/runtime@sha256:" + "a" * 64
ECR_IMAGE = "123456789012.dkr.ecr.us-east-1.amazonaws.com/metaharness/runtime@sha256:" + "a" * 64
META = {"package_digest": "sha256:" + "b" * 64, "blueprint_ref": "example@7"}
STORAGE = {"workspace": "workspace-bucket", "journal": "journal-bucket"}
AZURE_STORAGE = {"workspace": "workspace-storage", "journal": "journal-storage"}
SUBSCRIPTION = "123e4567-e89b-42d3-a456-426614174000"
AZURE_ENV = f"/subscriptions/{SUBSCRIPTION}/resourceGroups/rg/providers/Microsoft.App/managedEnvironments/env"
AZURE_IDENTITY = f"/subscriptions/{SUBSCRIPTION}/resourceGroups/rg/providers/Microsoft.ManagedIdentity/userAssignedIdentities/runtime"


@pytest.mark.parametrize(
    "image",
    [
        "repo/image:latest", "repo/image:v1", "repo/image",
        "repo/image@sha256:ABC", "--platform=linux/amd64", "registry/repo#x@sha256:" + "a" * 64,
        "registry/${IMAGE}@sha256:" + "a" * 64,
        "Registry/repo@sha256:" + "a" * 64,
        "registry/repo@sha256:" + "a" * 64,
        "bad..registry/repo@sha256:" + "a" * 64,
        "registry.example:99999/repo@sha256:" + "a" * 64, "",
    ],
)
def test_runtime_images_must_use_immutable_lowercase_digest(image):
    with pytest.raises(ValueError, match="IMAGE@sha256"):
        require_digest_image(image)


def test_oci_context_is_non_root_fixed_and_contains_no_install_or_blueprint_commands():
    files = build_oci_context(IMAGE)
    assert files == build_oci_context(IMAGE)
    dockerfile = files["container/Dockerfile"].decode()
    contract = json.loads(files["container/container.json"])
    assert dockerfile.startswith(f"FROM {IMAGE}\n")
    assert "USER 65532:65532" in dockerfile
    assert "ENTRYPOINT [" in dockerfile and "HEALTHCHECK" in dockerfile
    assert "RUN " not in dockerfile and "pip install" not in dockerfile
    assert "blueprint run" not in dockerfile
    assert contract["writable_paths"] == [JOURNAL_PATH, WORKSPACE_PATH]
    assert contract["entrypoint"] == SERVICE_ARGV
    assert contract["healthcheck"][-1].endswith("/health")
    assert contract["runtime_readiness"]["status"] == "blocked"
    assert contract["runtime_readiness"]["deployable"] is False
    assert files["container/workspace/.keep"] == b""
    assert files["container/journal/.keep"] == b""
    assert f"COPY --chown=65532:65532 container/workspace/ {WORKSPACE_PATH}/" in dockerfile
    assert f"COPY --chown=65532:65532 container/journal/ {JOURNAL_PATH}/" in dockerfile


def test_aws_apprunner_is_private_by_default_and_uses_only_refs():
    doc = aws_apprunner_service(
        image=ECR_IMAGE, service_name="harness", remote_journal_ref="s3://journal-bucket/runs", **META,
        ecr_access_role_arn="arn:aws:iam::123456789012:role/ecr",
        instance_role_arn="arn:aws:iam::123456789012:role/runtime",
        secret_refs={"API_TOKEN": "arn:aws:secretsmanager:us-east-1:123456789012:secret:token"},
    )
    props = doc["Resources"]["HarnessService"]["Properties"]
    assert props["NetworkConfiguration"]["IngressConfiguration"] == {
        "IsPubliclyAccessible": False
    }
    image_config = props["SourceConfiguration"]["ImageRepository"]["ImageConfiguration"]
    assert image_config["RuntimeEnvironmentSecrets"] == {
        "API_TOKEN": "arn:aws:secretsmanager:us-east-1:123456789012:secret:token"
    }
    assert props["SourceConfiguration"]["ImageRepository"]["ImageIdentifier"] == ECR_IMAGE
    assert "StartCommand" not in image_config
    assert image_config["RuntimeEnvironmentVariables"]["METAHARNESS_REMOTE_JOURNAL_REF"] == "s3://journal-bucket/runs"
    assert "METAHARNESS_JOURNAL_DIR" not in image_config["RuntimeEnvironmentVariables"]
    assert doc["Metadata"]["MetaharnessDurability"]["LocalFilesystem"] == "ephemeral"
    assert "secret-value" not in render_yaml(doc).decode()


def test_aws_public_ingress_requires_explicit_opt_in():
    doc = aws_apprunner_service(
        image=ECR_IMAGE, service_name="public", remote_journal_ref="s3://journal-bucket", public=True, **META,
        ecr_access_role_arn="arn:aws:iam::123456789012:role/ecr",
        instance_role_arn="arn:aws:iam::123456789012:role/runtime",
    )
    props = doc["Resources"]["HarnessService"]["Properties"]
    assert props["NetworkConfiguration"]["IngressConfiguration"] == {
        "IsPubliclyAccessible": True
    }


def test_gcp_service_and_job_keep_mode_specific_fields_separate():
    service = gcp_cloud_run_service(
        image=IMAGE, name="harness", service_account="runtime@example.iam.gserviceaccount.com",
        secret_refs={"API_TOKEN": "api-token"}, storage_refs=STORAGE,
        **META,
    )
    assert service["kind"] == "Service"
    assert service["metadata"]["annotations"]["run.googleapis.com/ingress"] == "internal"
    assert "x-metaharness-access" not in service
    service_container = service["spec"]["template"]["spec"]["containers"][0]
    assert service_container["command"] + service_container["args"] == SERVICE_ARGV
    assert service_container["ports"] == [{"containerPort": 8000}]
    assert service_container["startupProbe"]["httpGet"] == {
        "path": "/health", "port": 8000,
    }
    assert service_container["livenessProbe"]["httpGet"] == {
        "path": "/health", "port": 8000,
    }
    assert service_container["volumeMounts"] == [
        {"name": "workspace", "mountPath": WORKSPACE_PATH},
        {"name": "journal", "mountPath": JOURNAL_PATH},
    ]

    job = gcp_cloud_run_job(
        image=IMAGE, name="harness-job",
        service_account="runtime@example.iam.gserviceaccount.com",
        context_secret_ref="run-context", secret_refs={"API_TOKEN": "api-token"},
        storage_refs=STORAGE, **META,
    )
    assert job["kind"] == "Job"
    job_container = job["spec"]["template"]["spec"]["template"]["spec"]["containers"][0]
    assert job_container["command"] + job_container["args"] == JOB_ARGV
    assert "ports" not in job_container
    assert "ingress" not in render_yaml(job).decode().lower()
    assert {"name": "run-context", "mountPath": "/run/metaharness"} in job_container["volumeMounts"]
    volumes = job["spec"]["template"]["spec"]["template"]["spec"]["volumes"]
    assert {"name": "run-context", "secret": {
        "secretName": "run-context",
        "items": [{"key": "latest", "path": "context.json"}],
    }} in volumes
    assert JOB_ARGV[JOB_ARGV.index("--context-file") + 1] == "/run/metaharness/context.json"
    assert JOB_ARGV[3] == "/opt/metaharness/package"


def test_gcp_public_access_requires_explicit_opt_in():
    service = gcp_cloud_run_service(
        image=IMAGE, name="public", service_account="runtime@example.iam.gserviceaccount.com",
        storage_refs=STORAGE, public=True, **META,
    )
    assert service["metadata"]["annotations"]["run.googleapis.com/ingress"] == "all"
    assert service["metadata"]["annotations"]["run.googleapis.com/invoker-iam-disabled"] == "true"


def test_azure_container_app_private_identity_refs_and_no_secret_values():
    identity = AZURE_IDENTITY
    doc = azure_container_app(
        image=IMAGE, name="harness", location="eastus",
        environment_id=AZURE_ENV,
        identity_resource_id=identity,
        secret_refs={"api-token": "https://vault.vault.azure.net/secrets/api-token"},
        storage_refs=AZURE_STORAGE, **META,
    )
    resource = doc["resources"][0]
    assert resource["identity"]["userAssignedIdentities"] == {identity: {}}
    assert resource["properties"]["configuration"]["ingress"]["external"] is False
    assert resource["properties"]["configuration"]["secrets"] == [{
        "name": "api-token",
        "keyVaultUrl": "https://vault.vault.azure.net/secrets/api-token",
        "identity": identity,
    }]
    container = resource["properties"]["template"]["containers"][0]
    assert container["command"] == SERVICE_ARGV
    assert [probe["httpGet"]["path"] for probe in container["probes"]] == [
        "/health", "/health",
    ]
    assert container["env"] == [{"name": "api-token", "secretRef": "api-token"}]
    assert container["volumeMounts"] == [
        {"volumeName": "workspace", "mountPath": WORKSPACE_PATH},
        {"volumeName": "journal", "mountPath": JOURNAL_PATH},
    ]


def test_azure_public_ingress_requires_explicit_opt_in():
    doc = azure_container_app(
        image=IMAGE, name="public", location="eastus",
        environment_id=AZURE_ENV, identity_resource_id=AZURE_IDENTITY,
        storage_refs=AZURE_STORAGE, public=True, **META,
    )
    assert doc["resources"][0]["properties"]["configuration"]["ingress"]["external"] is True


def test_cloud_yaml_is_stable_and_parseable():
    doc = gcp_cloud_run_job(
        image=IMAGE, name="job", service_account="runtime@example.iam.gserviceaccount.com",
        context_secret_ref="run-context", storage_refs=STORAGE, **META,
    )
    first = render_yaml(doc)
    assert first == render_yaml(doc)
    assert yaml.safe_load(first) == doc


@pytest.mark.parametrize(
    ("changes", "match"),
    [
        ({"package_digest": "latest"}, "package_digest"),
        ({"package_digest": "sha256:" + "A" * 64}, "package_digest"),
        ({"blueprint_ref": "example@latest"}, "blueprint_ref"),
        ({"blueprint_ref": "../example@1"}, "blueprint_ref"),
        ({"blueprint_ref": "example@01"}, "blueprint_ref"),
        ({"blueprint_ref": ("a" * 81) + "@1"}, "blueprint_ref"),
    ],
)
def test_deployment_metadata_requires_exact_safe_references(changes, match):
    values = {**META, **changes}
    with pytest.raises(ValueError, match=match):
        gcp_cloud_run_job(
            image=IMAGE, name="job",
            service_account="runtime@example.iam.gserviceaccount.com",
            context_secret_ref="run-context", storage_refs=STORAGE, **values,
        )


def test_provider_reference_fields_reject_literal_values_and_newlines():
    with pytest.raises(ValueError, match="AWS secret"):
        aws_apprunner_service(
            image=ECR_IMAGE, service_name="harness", remote_journal_ref="s3://journal-bucket", **META,
            ecr_access_role_arn="arn:aws:iam::123456789012:role/ecr",
            instance_role_arn="arn:aws:iam::123456789012:role/runtime",
            secret_refs={"API_TOKEN": "literal-secret-value"},
        )
    with pytest.raises(ValueError, match="service_account"):
        gcp_cloud_run_service(
            image=IMAGE, name="harness", service_account="token-value\nnext",
            storage_refs=STORAGE, **META,
        )
    with pytest.raises(ValueError, match="GCP secret"):
        gcp_cloud_run_job(
            image=IMAGE, name="job",
            service_account="runtime@example.iam.gserviceaccount.com",
            context_secret_ref="run-context",
            secret_refs={"API_TOKEN": "Bearer literal token"}, storage_refs=STORAGE, **META,
        )
    with pytest.raises(ValueError, match="Azure secret"):
        azure_container_app(
            image=IMAGE, name="harness", location="eastus",
            environment_id=AZURE_ENV, identity_resource_id=AZURE_IDENTITY,
            secret_refs={"api-token": "literal-secret-value"},
            storage_refs=AZURE_STORAGE, **META,
        )


@pytest.mark.parametrize("public", ["true", 1, None])
def test_public_opt_in_must_be_an_explicit_boolean(public):
    with pytest.raises(ValueError, match="explicit boolean"):
        gcp_cloud_run_service(
            image=IMAGE, name="harness",
            service_account="runtime@example.iam.gserviceaccount.com",
            storage_refs=STORAGE, public=public, **META,
        )


def test_aws_requires_private_ecr_and_remote_journal_references():
    common = {
        "service_name": "harness",
        "ecr_access_role_arn": "arn:aws:iam::123456789012:role/ecr",
        "instance_role_arn": "arn:aws:iam::123456789012:role/runtime",
        **META,
    }
    with pytest.raises(ValueError, match="AWS ECR image"):
        aws_apprunner_service(
            image=IMAGE, remote_journal_ref="s3://journal-bucket", **common,
        )
    with pytest.raises(ValueError, match="remote_journal_ref"):
        aws_apprunner_service(
            image=ECR_IMAGE, remote_journal_ref="literal-journal-value", **common,
        )
    with pytest.raises(ValueError, match="service_name"):
        aws_apprunner_service(
            image=ECR_IMAGE, service_name="bad name",
            remote_journal_ref="s3://journal-bucket",
            ecr_access_role_arn=common["ecr_access_role_arn"],
            instance_role_arn=common["instance_role_arn"], **META,
        )


def test_gcp_service_requires_both_durable_storage_bindings():
    common = {
        "image": IMAGE, "name": "harness",
        "service_account": "runtime@example.iam.gserviceaccount.com", **META,
    }
    with pytest.raises(ValueError, match="requires workspace and journal"):
        gcp_cloud_run_service(**common)
    with pytest.raises(ValueError, match="requires workspace and journal"):
        gcp_cloud_run_service(storage_refs={"workspace": "workspace-bucket"}, **common)
    with pytest.raises(ValueError, match="binding names"):
        gcp_cloud_run_service(
            storage_refs={**STORAGE, "cache": "cache-bucket"}, **common,
        )


def test_gcp_job_requires_real_context_secret_and_provider_names():
    common = {
        "image": IMAGE, "service_account": "runtime@example.iam.gserviceaccount.com",
        "context_secret_ref": "run-context", "storage_refs": STORAGE, **META,
    }
    with pytest.raises(ValueError, match="context_secret_ref"):
        gcp_cloud_run_job(name="job", context_secret_ref="Bearer secret value", **{
            key: value for key, value in common.items() if key != "context_secret_ref"
        })
    with pytest.raises(ValueError, match="name"):
        gcp_cloud_run_job(name="Bad_Name", **common)


def test_gcp_jobs_require_durable_storage_and_same_project_bare_secret_names():
    common = {
        "image": IMAGE, "name": "job",
        "service_account": "runtime@example.iam.gserviceaccount.com", **META,
    }
    with pytest.raises(ValueError, match="requires workspace and journal"):
        gcp_cloud_run_job(context_secret_ref="run-context", **common)
    with pytest.raises(ValueError, match="context_secret_ref"):
        gcp_cloud_run_job(
            context_secret_ref="projects/example/secrets/run-context",
            storage_refs=STORAGE, **common,
        )
    with pytest.raises(ValueError, match="GCP secret"):
        gcp_cloud_run_job(
            context_secret_ref="run-context",
            secret_refs={"API_TOKEN": "projects/example/secrets/api-token"},
            storage_refs=STORAGE, **common,
        )
    document = gcp_cloud_run_job(
        context_secret_ref="run-context",
        secret_refs={"API_TOKEN": "api-token"}, storage_refs=STORAGE, **common,
    )
    container = document["spec"]["template"]["spec"]["template"]["spec"]["containers"][0]
    assert {"name": "workspace", "mountPath": WORKSPACE_PATH} in container["volumeMounts"]
    assert {"name": "journal", "mountPath": JOURNAL_PATH} in container["volumeMounts"]


@pytest.mark.parametrize(
    "remote_journal_ref",
    ["s3://a", "s3://a..b", "s3://192.168.1.1", "s3://bad.-bucket", "s3://bad-.bucket"],
)
def test_aws_remote_journal_rejects_invalid_s3_buckets(remote_journal_ref):
    with pytest.raises(ValueError, match="S3 bucket"):
        aws_apprunner_service(
            image=ECR_IMAGE, service_name="harness",
            remote_journal_ref=remote_journal_ref,
            ecr_access_role_arn="arn:aws:iam::123456789012:role/ecr",
            instance_role_arn="arn:aws:iam::123456789012:role/runtime", **META,
        )


def test_azure_requires_guid_resource_ids_location_name_and_durable_storage():
    common = {
        "image": IMAGE, "name": "harness", "location": "eastus",
        "environment_id": AZURE_ENV, "identity_resource_id": AZURE_IDENTITY, **META,
    }
    with pytest.raises(ValueError, match="requires workspace and journal"):
        azure_container_app(**common)
    with pytest.raises(ValueError, match="environment_id"):
        azure_container_app(
            environment_id="/subscriptions/1/resourceGroups/rg/providers/Microsoft.App/managedEnvironments/env",
            storage_refs=AZURE_STORAGE,
            **{key: value for key, value in common.items() if key != "environment_id"},
        )
    with pytest.raises(ValueError, match="location"):
        azure_container_app(
            location="East US", storage_refs=AZURE_STORAGE,
            **{key: value for key, value in common.items() if key != "location"},
        )
    with pytest.raises(ValueError, match="name"):
        azure_container_app(
            name="bad--name", storage_refs=AZURE_STORAGE,
            **{key: value for key, value in common.items() if key != "name"},
        )
