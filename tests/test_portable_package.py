from __future__ import annotations

import io
import json
import zipfile

import pytest

from metaharness.blueprints.models import ArtifactRef, BlueprintVersion
from metaharness.portable import (
    PortableIntegrityError,
    build_portable_package,
    load_portable_package,
)
from metaharness.portable.integrity import (
    canonical_json_bytes,
    digest_files,
    package_content_digest,
)
from metaharness.portable.models import FileDigest, PortableRequirements


def blueprint() -> BlueprintVersion:
    return BlueprintVersion.model_validate(
        {
            "id": "portable-demo",
            "version": 3,
            "published_at": 123.0,
            "name": "Portable demo",
            "description": "A reproducible example.",
            "workflow": {
                "name": "demo-flow",
                "steps": [
                    {
                        "id": "inspect",
                        "objective": "Inspect the supplied workspace.",
                        "tools": ["read_file", "mcp__search__query"],
                        "inputs": {"mail-token": {"binding": "mail-oauth"}},
                    }
                ],
            },
            "inputs": [
                {
                    "name": "mail-token",
                    "schema": {"type": "string"},
                    "secret": True,
                    "default": {"binding": "mail-oauth"},
                }
            ],
            "eval_suites": [{"id": "portable-evals", "version": 2}],
        }
    )


def rewrite_zip(payload: bytes, transform) -> bytes:
    with zipfile.ZipFile(io.BytesIO(payload)) as source:
        files = {name: source.read(name) for name in source.namelist()}
    transform(files)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as target:
        for name, data in files.items():
            target.writestr(name, data)
    return output.getvalue()


def reindex_manifest(files: dict[str, bytes]) -> None:
    """Model an attacker who rewrites every unhashed integrity field coherently."""
    manifest = json.loads(files["manifest.json"])
    artifacts = {path: raw for path, raw in files.items() if path != "manifest.json"}
    digests = digest_files(artifacts)
    manifest["files"] = [item.model_dump(mode="json") for item in digests]
    manifest["content_digest"] = package_content_digest(
        digests,
        blueprint_ref=manifest["blueprint_ref"],
        eval_refs=manifest["eval_refs"],
        targets=manifest["targets"],
        requirements=manifest["requirements"],
        generator_version=manifest["generator_version"],
    )
    files["manifest.json"] = canonical_json_bytes(manifest)


def test_build_and_load_exact_blueprint_package(monkeypatch):
    monkeypatch.delenv("SOURCE_DATE_EPOCH", raising=False)
    payload = build_portable_package(blueprint(), targets=["pi", "local"])
    loaded = load_portable_package(payload)
    assert loaded.blueprint.ref == ArtifactRef(id="portable-demo", version=3)
    assert loaded.workflow == loaded.blueprint.workflow.model_dump(mode="json")
    assert loaded.manifest.targets == ["local", "pi"]
    assert loaded.manifest.eval_refs == [ArtifactRef(id="portable-evals", version=2)]
    assert loaded.manifest.requirements.tool_ids == ["mcp__search__query", "read_file"]
    assert loaded.manifest.requirements.mcp_servers == ["search"]
    assert loaded.manifest.requirements.secret_bindings == ["mail-oauth"]


def test_build_is_byte_identical_and_honors_source_date_epoch(monkeypatch):
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1700000000")
    first = build_portable_package(blueprint(), targets=["local", "codex"])
    second = build_portable_package(blueprint(), targets=["codex", "local"])
    assert first == second
    with zipfile.ZipFile(io.BytesIO(first)) as archive:
        assert archive.namelist() == sorted(archive.namelist())
        assert {item.date_time for item in archive.infolist()} == {
            (2023, 11, 14, 22, 13, 20)
        }
        manifest = json.loads(archive.read("manifest.json"))
    assert manifest["generated_at"] == 1700000000


def test_selected_launcher_layouts_are_included_declared_and_digested():
    payload = build_portable_package(blueprint(), targets=["pi", "codex"])
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = set(archive.namelist())
        manifest = json.loads(archive.read("manifest.json"))
    expected = {
        "launchers/codex/INSTRUCTIONS.md",
        "launchers/codex/launcher.json",
        "launchers/pi/INSTRUCTIONS.md",
        "launchers/pi/launcher.json",
    }
    assert expected <= names
    assert not any(path.startswith("launchers/claude-code/") for path in names)
    assert not any(path.startswith("launchers/opencode/") for path in names)
    declared = {item["path"] for item in manifest["files"]}
    assert expected <= declared
    assert all(item["sha256"] and item["size"] > 0 for item in manifest["files"])


def test_local_target_adds_no_launcher_layout():
    payload = build_portable_package(blueprint(), targets=["local"])
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        assert not any(name.startswith("launchers/") for name in archive.namelist())


def test_deployment_manifest_does_not_overclaim_runtime_readiness():
    image = "registry.example/runtime@sha256:" + "a" * 64
    payload = build_portable_package(
        blueprint(),
        targets=["oci"],
        deployment_options={"oci": {"runtime_image": image}},
    )
    deployment = load_portable_package(payload).manifest.deployments[0]
    assert deployment.target == "oci"
    assert deployment.status == "blocked"
    assert deployment.deployable is False
    assert "agent configuration" in deployment.reason


def _deployment_case(provider: str, secret_ref: str) -> tuple[str, dict]:
    image = "registry.example/runtime@sha256:" + "a" * 64
    if provider == "gcp-context":
        return "gcp-cloud-run-job", {
            "gcp-cloud-run-job": {
                "image": image, "name": "job",
                "service_account": "runtime@example.iam.gserviceaccount.com",
                "context_secret_ref": secret_ref,
                "storage_refs": {"workspace": "workspace-bucket", "journal": "journal-bucket"},
            }
        }
    if provider == "gcp-secret":
        target, options = _deployment_case("gcp-context", "run-context")
        options[target]["secret_refs"] = {"API_TOKEN": secret_ref}
        return target, options
    if provider == "aws":
        return "aws-apprunner", {"aws-apprunner": {
            "image": "123456789012.dkr.ecr.us-east-1.amazonaws.com/runtime@sha256:" + "a" * 64,
            "service_name": "harness",
            "ecr_access_role_arn": "arn:aws:iam::123456789012:role/ecr",
            "instance_role_arn": "arn:aws:iam::123456789012:role/runtime",
            "remote_journal_ref": "s3://journal-bucket/runs",
            "secret_refs": {"API_TOKEN": secret_ref},
        }}
    return "azure-container-app", {"azure-container-app": {
        "image": image, "name": "harness", "location": "eastus",
        "environment_id": "/subscriptions/123e4567-e89b-42d3-a456-426614174000/resourceGroups/rg/providers/Microsoft.App/managedEnvironments/env",
        "identity_resource_id": "/subscriptions/123e4567-e89b-42d3-a456-426614174000/resourceGroups/rg/providers/Microsoft.ManagedIdentity/userAssignedIdentities/runtime",
        "storage_refs": {"workspace": "workspace-storage", "journal": "journal-storage"},
        "secret_refs": {"api-token": secret_ref},
    }}


@pytest.mark.parametrize("provider,secret_ref", [
    ("gcp-context", "sk-live-abcdefghijk"),
    ("gcp-secret", "sk-test-abcdefghijk"),
    ("aws", "arn:aws:secretsmanager:us-east-1:123456789012:secret:sk-live-abcdefghijk"),
    ("azure", "https://vault.vault.azure.net/secrets/sk-live-abcdefghijk"),
])
def test_build_and_load_reject_credential_shaped_deployment_references(
    provider, secret_ref,
):
    target, unsafe = _deployment_case(provider, secret_ref)
    with pytest.raises(PortableIntegrityError, match="credential material"):
        build_portable_package(
            blueprint(), targets=[target], deployment_options=unsafe
        )

    _, safe = _deployment_case(provider, {
        "gcp-context": "run-context",
        "gcp-secret": "api-token",
        "aws": "arn:aws:secretsmanager:us-east-1:123456789012:secret:api-token",
        "azure": "https://vault.vault.azure.net/secrets/api-token",
    }[provider])
    payload = build_portable_package(
        blueprint(), targets=[target], deployment_options=safe, generated_at=1
    )

    def poison_manifest(files):
        manifest = json.loads(files["manifest.json"])
        options = manifest["deployment_options"][target]
        if provider == "gcp-context":
            options["context_secret_ref"] = secret_ref
        else:
            key = next(iter(options["secret_refs"]))
            options["secret_refs"][key] = secret_ref
        files["manifest.json"] = canonical_json_bytes(manifest)

    with pytest.raises(PortableIntegrityError, match="credential material"):
        load_portable_package(rewrite_zip(payload, poison_manifest))


def test_deployment_digest_is_reproducible_advertised_and_option_sensitive():
    target, private = _deployment_case("gcp-context", "run-context")
    public = json.loads(json.dumps(private))
    # Job has no ingress option; use a secret-reference change as deployment identity.
    public[target]["context_secret_ref"] = "other-run-context"
    first = build_portable_package(
        blueprint(), targets=[target], deployment_options=private, generated_at=1
    )
    again = build_portable_package(
        blueprint(), targets=[target], deployment_options=private, generated_at=1
    )
    changed = build_portable_package(
        blueprint(), targets=[target], deployment_options=public, generated_at=1
    )
    assert first == again
    first_loaded = load_portable_package(first)
    changed_loaded = load_portable_package(changed)
    assert first_loaded.manifest.deployment_digest != changed_loaded.manifest.deployment_digest
    with zipfile.ZipFile(io.BytesIO(first)) as archive:
        descriptor = json.loads(archive.read("cloud/gcp-cloud-run-job.json"))
    advertised = descriptor["metadata"]["annotations"]["metaharness.io/package-digest"]
    assert advertised == f"sha256:{first_loaded.manifest.deployment_digest}"


def test_launcher_target_order_does_not_change_package_bytes(monkeypatch):
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1700000000")
    first = build_portable_package(
        blueprint(), targets=["opencode", "codex", "pi", "claude-code"]
    )
    second = build_portable_package(
        blueprint(), targets=["claude-code", "pi", "codex", "opencode"]
    )
    assert first == second


@pytest.mark.parametrize(
    "change, message",
    [
        (lambda files: files.pop("launchers/codex/launcher.json"), "differ from manifest"),
        (
            lambda files: files.__setitem__(
                "launchers/codex/launcher.json",
                files["launchers/codex/launcher.json"] + b" ",
            ),
            "digest mismatch",
        ),
        (
            lambda files: files.__setitem__("launchers/pi/launcher.json", b"{}"),
            "differ from manifest",
        ),
    ],
)
def test_loader_rejects_removed_tampered_or_unselected_launcher_files(change, message):
    payload = build_portable_package(blueprint(), targets=["codex"])
    with pytest.raises(PortableIntegrityError, match=message):
        load_portable_package(rewrite_zip(payload, change))


@pytest.mark.parametrize(
    "change, message",
    [
        (lambda files: files.pop("launchers/codex/launcher.json"), "layout differs"),
        (
            lambda files: files.__setitem__(
                "launchers/codex/launcher.json",
                files["launchers/codex/launcher.json"] + b" ",
            ),
            "layout tampered",
        ),
        (
            lambda files: files.__setitem__("launchers/pi/launcher.json", b"{}"),
            "layout differs",
        ),
    ],
)
def test_launcher_layout_is_authoritative_even_after_manifest_reindex(change, message):
    def attack(files):
        change(files)
        reindex_manifest(files)

    payload = build_portable_package(blueprint(), targets=["codex"])
    with pytest.raises(PortableIntegrityError, match=message):
        load_portable_package(rewrite_zip(payload, attack))


def test_workflow_is_derived_only_from_blueprint():
    payload = build_portable_package(blueprint())

    def alter(files):
        workflow = json.loads(files["workflow.json"])
        workflow["name"] = "substituted"
        files["workflow.json"] = json.dumps(workflow).encode()

    with pytest.raises(PortableIntegrityError, match="digest mismatch"):
        load_portable_package(rewrite_zip(payload, alter))


@pytest.mark.parametrize("change, message", [
    (lambda files: files.pop("workflow.json"), "missing files"),
    (lambda files: files.__setitem__("surprise.txt", b"x"), "differ from manifest"),
    (lambda files: files.__setitem__("harness.json", files["harness.json"] + b" "), "digest mismatch"),
])
def test_loader_rejects_missing_extra_and_digest_mismatch(change, message):
    with pytest.raises(PortableIntegrityError, match=message):
        load_portable_package(rewrite_zip(build_portable_package(blueprint()), change))


def test_builder_rejects_unknown_target_and_does_not_accept_paths_or_commands():
    with pytest.raises(ValueError, match="unsupported"):
        build_portable_package(blueprint(), targets=["../../launcher"])
    assert "path" not in build_portable_package.__code__.co_varnames
    assert "command" not in build_portable_package.__code__.co_varnames


def test_manifest_requirement_tampering_is_covered_by_content_digest():
    payload = build_portable_package(blueprint())

    def alter(files):
        manifest = json.loads(files["manifest.json"])
        manifest["requirements"]["tool_ids"] = []
        requirements = PortableRequirements.model_validate(manifest["requirements"])
        digests = [FileDigest.model_validate(item) for item in manifest["files"]]
        manifest["content_digest"] = package_content_digest(
            digests,
            blueprint_ref=manifest["blueprint_ref"],
            eval_refs=manifest["eval_refs"],
            targets=manifest["targets"],
            requirements=requirements.model_dump(mode="json"),
            generator_version=manifest["generator_version"],
        )
        files["manifest.json"] = json.dumps(
            manifest, sort_keys=True, separators=(",", ":")
        ).encode()

    with pytest.raises(PortableIntegrityError, match="requirements differ"):
        load_portable_package(rewrite_zip(payload, alter))


@pytest.mark.parametrize(
    "name",
    [
        "api_key",
        "client-secret",
        "access_token",
        "refresh-token",
        "password",
        "private_key",
        "authToken",
        "credentials",
    ],
)
@pytest.mark.parametrize("literal", ["ordinary-literal", 7, True])
def test_sensitive_input_name_rejects_scalar_default_even_when_not_marked_secret(name, literal):
    value = blueprint().model_dump(mode="json")
    value["inputs"] = [
        {
            "name": name,
            "schema": {"type": "string"},
            "secret": False,
            "default": literal,
        }
    ]
    unsafe = BlueprintVersion.model_validate(value)
    with pytest.raises(PortableIntegrityError, match="literal default"):
        build_portable_package(unsafe)


def test_sensitive_input_name_allows_exact_logical_binding_when_not_marked_secret():
    value = blueprint().model_dump(mode="json")
    value["inputs"] = [
        {
            "name": "oauth_token",
            "schema": {"type": "string"},
            "secret": False,
            "default": {"binding": "local-oauth"},
        }
    ]
    build_portable_package(BlueprintVersion.model_validate(value))


@pytest.mark.parametrize(
    "wrapped",
    [
        {"value": "literal"},
        {"binding": "local-oauth", "value": "literal"},
        [{"binding": "local-oauth"}],
        ["literal"],
    ],
)
def test_sensitive_input_name_rejects_wrapped_defaults(wrapped):
    value = blueprint().model_dump(mode="json")
    value["inputs"] = [
        {
            "name": "client_secret",
            "schema": {"type": "string"},
            "secret": False,
            "default": wrapped,
        }
    ]
    unsafe = BlueprintVersion.model_validate(value)
    with pytest.raises(PortableIntegrityError, match="exact SecretBindingRef"):
        build_portable_package(unsafe)
