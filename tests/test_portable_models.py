from __future__ import annotations

import pytest
from pydantic import ValidationError

from metaharness.blueprints.models import ArtifactRef
from metaharness.portable.models import FileDigest, PortableManifest, PortableRequirements


ZERO = "0" * 64


def manifest(**changes):
    values = {
        "generator_version": "test-1",
        "generated_at": 0,
        "blueprint_ref": {"id": "demo", "version": 1},
        "blueprint_digest": ZERO,
        "workflow_digest": ZERO,
        "targets": ["local"],
        "files": [FileDigest(path="harness.json", sha256=ZERO, size=1)],
        "content_digest": ZERO,
    }
    values.update(changes)
    return PortableManifest(**values)


def test_manifest_is_strict_v1_with_exact_reference():
    value = manifest()
    assert value.schema_version == 1
    assert value.blueprint_ref == ArtifactRef(id="demo", version=1)
    with pytest.raises(ValidationError):
        manifest(schema_version=2)
    with pytest.raises(ValidationError):
        PortableManifest.model_validate({**value.model_dump(), "surprise": True})


@pytest.mark.parametrize("value", ["", "../generator", "name/version", "bad version", "gho_abcdefghijklmnopqrstuvwxyz"])
def test_manifest_rejects_unsafe_generator_versions(value):
    with pytest.raises(ValidationError, match="safe version"):
        manifest(generator_version=value)


@pytest.mark.parametrize("target", ["docker", "aws", "shell"])
def test_manifest_rejects_targets_outside_closed_vocabulary(target):
    with pytest.raises(ValidationError):
        manifest(targets=[target])


def test_manifest_requires_canonical_unique_collections():
    with pytest.raises(ValidationError, match="targets"):
        manifest(targets=["pi", "local"])
    with pytest.raises(ValidationError, match="eval_refs"):
        manifest(eval_refs=[{"id": "z", "version": 1}, {"id": "a", "version": 1}])
    with pytest.raises(ValidationError, match="files"):
        manifest(
            files=[
                FileDigest(path="workflow.json", sha256=ZERO, size=1),
                FileDigest(path="harness.json", sha256=ZERO, size=1),
            ]
        )


def test_file_digest_rejects_unsafe_paths_and_bad_hashes():
    for path in ("/tmp/x", "../x", "a/../x", "a\\x", "a//x"):
        with pytest.raises(ValidationError):
            FileDigest(path=path, sha256=ZERO, size=1)
    with pytest.raises(ValidationError):
        FileDigest(path="x", sha256="ABC", size=1)


def test_requirements_are_sorted_unique_sets():
    assert PortableRequirements(tool_ids=["a", "b"]).tool_ids == ["a", "b"]
    with pytest.raises(ValidationError):
        PortableRequirements(tool_ids=["b", "a"])
    with pytest.raises(ValidationError):
        PortableRequirements(secret_bindings=["same", "same"])
