"""Blueprint Phase 1 models and immutable filesystem-store contracts."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from metaharness.blueprints import (
    ArtifactRef,
    BlueprintArchivedError,
    BlueprintAlreadyExistsError,
    BlueprintContent,
    BlueprintCorruptionError,
    BlueprintNotFoundError,
    BlueprintStore,
    BlueprintStoreError,
    InvalidRevisionError,
    InputSpec,
    RevisionConflictError,
    SecretBindingRef,
)
from metaharness.workflows.dsl import WorkflowSpec


def content(name: str = "Incident response", *, source=None, inputs=None, context=None):
    return BlueprintContent(
        name=name,
        description="A test harness blueprint.",
        workflow=WorkflowSpec.model_validate({
            "name": "incident-response",
            "steps": [{"id": "triage", "objective": "Triage the incident."}],
        }),
        inputs=inputs or [],
        default_context=context or {},
        source=source,
    )


@pytest.mark.parametrize(
    "value",
    ["", "UPPER", "has space", "../escape", "a/b", "-leading", "trailing-", "a--b"],
)
def test_artifact_ref_rejects_unsafe_ids(value):
    with pytest.raises(ValidationError):
        ArtifactRef(id=value, version=1)


@pytest.mark.parametrize("value", [0, -1, True, "1", 1.5])
def test_artifact_ref_requires_a_strict_positive_integer_version(value):
    with pytest.raises(ValidationError):
        ArtifactRef(id="safe-id", version=value)


def test_blueprint_models_forbid_extra_fields_and_unknown_schema_versions():
    raw = content().model_dump(mode="json")
    raw["surprise"] = True
    with pytest.raises(ValidationError, match="surprise"):
        BlueprintContent.model_validate(raw)

    raw.pop("surprise")
    raw["schema_version"] = 2
    with pytest.raises(ValidationError, match="schema_version"):
        BlueprintContent.model_validate(raw)

    with pytest.raises(ValidationError, match="extra"):
        SecretBindingRef.model_validate({"binding": "api-key", "extra": "literal"})


@pytest.mark.parametrize(
    "workflow",
    [
        {"name": "x", "steps": [], "unknown_workflow_key": True},
        {"name": "x", "steps": [
            {"id": "a", "objective": "o", "unknown_step_key": True}
        ]},
    ],
)
def test_blueprint_boundary_rejects_unknown_workflow_and_step_keys(workflow):
    with pytest.raises(ValidationError, match="unknown"):
        BlueprintContent(name="Strict", workflow=workflow)


def test_secret_inputs_accept_only_binding_refs_and_never_default_context():
    binding = InputSpec(
        name="api_key",
        schema={"type": "string"},
        secret=True,
        default={"binding": "incident-api-key"},
    )
    assert binding.default == SecretBindingRef(binding="incident-api-key")

    with pytest.raises(ValidationError, match="SecretBindingRef"):
        InputSpec(
            name="api_key", schema={"type": "string"}, secret=True, default="plaintext"
        )
    with pytest.raises(ValidationError, match="schemas cannot declare defaults"):
        InputSpec(
            name="api_key",
            schema={"anyOf": [{"type": "string", "default": "plaintext"}]},
            secret=True,
        )
    with pytest.raises(ValidationError, match="default_context"):
        content(inputs=[binding], context={"api_key": "plaintext"})


def test_secret_step_inputs_require_exact_binding_marker_and_serialize_as_reference():
    secret = InputSpec(name="token", schema={"type": "string"}, secret=True)
    raw = {
        "name": "secret-flow",
        "steps": [{
            "id": "call-service",
            "objective": "Call the service.",
            "inputs": {"token": {"binding": "service-token"}},
        }],
    }
    blueprint = BlueprintContent(name="Secret flow", workflow=raw, inputs=[secret])
    assert blueprint.model_dump(mode="json")["workflow"]["steps"][0]["inputs"]["token"] == {
        "binding": "service-token"
    }

    raw["steps"][0]["inputs"]["token"] = "plaintext"
    with pytest.raises(ValidationError, match="never a literal"):
        BlueprintContent(name="Secret flow", workflow=raw, inputs=[secret])
    raw["steps"][0]["inputs"]["token"] = {
        "binding": "service-token", "literal": "smuggled"
    }
    with pytest.raises(ValidationError, match="never a literal"):
        BlueprintContent(name="Secret flow", workflow=raw, inputs=[secret])


def test_secret_schema_allows_property_named_default_but_rejects_default_keyword():
    allowed = InputSpec(
        name="token",
        secret=True,
        schema={"type": "object", "properties": {"default": {"type": "string"}}},
    )
    assert "default" in allowed.schema["properties"]

    with pytest.raises(ValidationError, match="schemas cannot declare defaults"):
        InputSpec(
            name="token",
            secret=True,
            schema={
                "type": "object",
                "properties": {"default": {"type": "string", "default": "literal"}},
            },
        )


def test_duplicate_input_names_are_rejected():
    duplicate = InputSpec(name="goal", schema={"type": "string"})
    with pytest.raises(ValidationError, match="duplicate input names"):
        content(inputs=[duplicate, duplicate])


def test_store_separates_catalog_draft_and_immutable_versions(tmp_path):
    store = BlueprintStore(tmp_path / "state")
    draft = store.create_draft(
        "incident-response", content(), owner="alice", now=10.0
    )

    assert draft.revision == 1
    assert (store.catalog_root / "incident-response.json").is_file()
    assert (store.drafts_root / "incident-response.json").is_file()
    assert store.list()[0].latest_version is None

    version1 = store.publish("incident-response", expected_revision=1, now=20.0)
    assert version1.version == 1
    assert version1.published_at == 20.0
    assert not (store.drafts_root / "incident-response.json").exists()
    version1_path = (
        store.versions_root / "incident-response" / "versions" / "1.json"
    )
    original_bytes = version1_path.read_bytes()

    store.create_draft_from_version(version1.ref, owner="alice", now=30.0)
    updated = store.update_draft(
        "incident-response",
        content("Incident response v2"),
        expected_revision=1,
        now=31.0,
    )
    version2 = store.publish(
        "incident-response", expected_revision=updated.revision, now=40.0
    )

    assert version2.version == 2
    assert store.get_version("incident-response", 1).name == "Incident response"
    assert store.get_version("incident-response", 2).name == "Incident response v2"
    assert version1_path.read_bytes() == original_bytes
    assert [v.version for v in store.list_versions("incident-response")] == [1, 2]
    assert store.get_catalog_entry("incident-response").latest_version == 2


def test_draft_updates_and_publish_are_revision_checked(tmp_path):
    store = BlueprintStore(tmp_path)
    store.create_draft("review-flow", content(), owner="alice")

    with pytest.raises(RevisionConflictError) as caught:
        store.update_draft(
            "review-flow", content("Changed"), expected_revision=9
        )
    assert (caught.value.expected, caught.value.actual) == (9, 1)
    assert store.get_draft("review-flow").name == "Incident response"

    updated = store.update_draft(
        "review-flow", content("Changed"), expected_revision=1
    )
    assert updated.revision == 2
    with pytest.raises(RevisionConflictError):
        store.publish("review-flow", expected_revision=1)
    assert not store.list_versions("review-flow")


@pytest.mark.parametrize("bad", [True, False, "1", 1.0, 0, -1, None])
@pytest.mark.parametrize("operation", ["update", "publish"])
def test_expected_revision_must_be_a_strict_positive_integer(
    tmp_path, bad, operation
):
    store = BlueprintStore(tmp_path)
    store.create_draft("review-flow", content(), owner="alice")

    with pytest.raises(InvalidRevisionError, match="positive integer"):
        if operation == "update":
            store.update_draft(
                "review-flow", content("Changed"), expected_revision=bad
            )
        else:
            store.publish("review-flow", expected_revision=bad)

    assert store.get_draft("review-flow").revision == 1
    assert store.list_versions("review-flow") == []


def test_publish_retry_reconciles_matching_immutable_version_after_catalog_failure(
    tmp_path, monkeypatch
):
    store = BlueprintStore(tmp_path)
    store.create_draft("review-flow", content(), owner="alice")
    catalog_path = store.catalog_root / "review-flow.json"
    version_path = store.versions_root / "review-flow" / "versions" / "1.json"
    real_replace = store._atomic_replace
    failed = False

    def fail_catalog_once(path, model):
        nonlocal failed
        if path == catalog_path and not failed:
            failed = True
            raise OSError("injected catalog write failure")
        return real_replace(path, model)

    monkeypatch.setattr(store, "_atomic_replace", fail_catalog_once)
    with pytest.raises(OSError, match="injected"):
        store.publish("review-flow", expected_revision=1, now=20.0)

    immutable_bytes = version_path.read_bytes()
    assert store.get_catalog_entry("review-flow").latest_version is None
    assert store.get_draft("review-flow").revision == 1

    published = store.publish("review-flow", expected_revision=1, now=999.0)
    assert published.version == 1
    assert published.published_at == 20.0
    assert version_path.read_bytes() == immutable_bytes
    assert store.get_catalog_entry("review-flow").latest_version == 1
    with pytest.raises(BlueprintNotFoundError):
        store.get_draft("review-flow")


def test_publish_retry_finalizes_draft_cleanup_without_creating_duplicate_version(
    tmp_path, monkeypatch
):
    store = BlueprintStore(tmp_path)
    store.create_draft("review-flow", content(), owner="alice")
    draft_path = store.drafts_root / "review-flow.json"
    version1_path = store.versions_root / "review-flow" / "versions" / "1.json"
    version2_path = store.versions_root / "review-flow" / "versions" / "2.json"
    real_unlink = Path.unlink
    failed = False

    def fail_draft_unlink_once(path, *args, **kwargs):
        nonlocal failed
        if path == draft_path and not failed:
            failed = True
            raise OSError("injected draft cleanup failure")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_draft_unlink_once)
    with pytest.raises(OSError, match="cleanup"):
        store.publish("review-flow", expected_revision=1, now=20.0)

    immutable_bytes = version1_path.read_bytes()
    assert store.get_catalog_entry("review-flow").latest_version == 1
    assert store.get_draft("review-flow").revision == 1

    published = store.publish("review-flow", expected_revision=1, now=999.0)
    assert published.version == 1
    assert published.published_at == 20.0
    assert version1_path.read_bytes() == immutable_bytes
    assert not version2_path.exists()
    with pytest.raises(BlueprintNotFoundError):
        store.get_draft("review-flow")


def test_publish_retry_after_draft_removed_finishes_from_durable_intent(
    tmp_path, monkeypatch
):
    store = BlueprintStore(tmp_path)
    store.create_draft("review-flow", content(), owner="alice")
    intent_path = store.intents_root / "review-flow.json"
    version_path = store.versions_root / "review-flow" / "versions" / "1.json"
    real_unlink = store._unlink
    failed = False

    def fail_intent_cleanup_once(path, *args, **kwargs):
        nonlocal failed
        if path == intent_path and not failed:
            failed = True
            raise OSError("injected intent cleanup failure")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(store, "_unlink", fail_intent_cleanup_once)
    with pytest.raises(OSError, match="intent cleanup"):
        store.publish("review-flow", expected_revision=1, now=20.0)

    immutable_bytes = version_path.read_bytes()
    assert not (store.drafts_root / "review-flow.json").exists()
    assert intent_path.exists()
    assert store.get_catalog_entry("review-flow").latest_version == 1

    published = store.publish("review-flow", expected_revision=1, now=999.0)
    assert published.version == 1 and published.published_at == 20.0
    assert version_path.read_bytes() == immutable_bytes
    assert not intent_path.exists()
    assert not (store.versions_root / "review-flow" / "versions" / "2.json").exists()


def test_unchanged_draft_based_on_latest_can_publish_a_new_version(tmp_path):
    store = BlueprintStore(tmp_path)
    store.create_draft("review-flow", content(), owner="alice")
    version1 = store.publish("review-flow", expected_revision=1, now=10.0)
    draft = store.create_draft_from_version(version1.ref, owner="alice", now=20.0)

    version2 = store.publish(
        "review-flow", expected_revision=draft.revision, now=30.0
    )
    assert version2.version == 2
    assert version2.name == version1.name
    assert [item.version for item in store.list_versions("review-flow")] == [1, 2]


def test_changed_draft_based_on_old_version_can_publish_after_latest_advanced(tmp_path):
    store = BlueprintStore(tmp_path)
    store.create_draft("review-flow", content("v1"), owner="alice")
    version1 = store.publish("review-flow", expected_revision=1, now=10.0)
    store.create_draft_from_version(version1.ref, owner="alice")
    changed = store.update_draft("review-flow", content("v2"), expected_revision=1)
    store.publish("review-flow", expected_revision=changed.revision, now=20.0)

    # A draft may intentionally branch from old v1 even though catalog latest is
    # now v2. Explicit intent, not a base/latest heuristic, makes this publish v3.
    old_base = store.create_draft(
        "review-flow", content("changed from old v1"), owner="alice", base_version=1
    )
    version3 = store.publish(
        "review-flow", expected_revision=old_base.revision, now=30.0
    )
    assert version3.version == 3
    assert version3.name == "changed from old v1"


@pytest.mark.parametrize("corrupt", [True, False])
def test_post_catalog_recovery_fails_closed_for_corrupt_or_different_latest(
    tmp_path, monkeypatch, corrupt
):
    store = BlueprintStore(tmp_path)
    store.create_draft("review-flow", content(), owner="alice")
    draft_path = store.drafts_root / "review-flow.json"
    version_path = store.versions_root / "review-flow" / "versions" / "1.json"
    real_unlink = Path.unlink

    def fail_draft_unlink(path, *args, **kwargs):
        if path == draft_path:
            raise OSError("injected draft cleanup failure")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_draft_unlink)
    with pytest.raises(OSError):
        store.publish("review-flow", expected_revision=1, now=20.0)
    monkeypatch.setattr(Path, "unlink", real_unlink)

    if corrupt:
        version_path.write_text("{not-json", encoding="utf-8")
        match = "corrupt"
    else:
        raw = json.loads(version_path.read_text(encoding="utf-8"))
        raw["name"] = "Different latest content"
        version_path.write_text(json.dumps(raw), encoding="utf-8")
        match = "does not match"

    with pytest.raises(BlueprintStoreError, match=match):
        store.publish("review-flow", expected_revision=1)
    assert store.get_catalog_entry("review-flow").latest_version == 1
    assert store.get_draft("review-flow").revision == 1
    assert not (store.versions_root / "review-flow" / "versions" / "2.json").exists()


@pytest.mark.parametrize("damage", ["intent", "draft", "version", "catalog"])
def test_publish_intent_recovery_fails_closed_on_mismatched_state(
    tmp_path, monkeypatch, damage
):
    store = BlueprintStore(tmp_path)
    store.create_draft("review-flow", content(), owner="alice")
    catalog_path = store.catalog_root / "review-flow.json"
    intent_path = store.intents_root / "review-flow.json"
    draft_path = store.drafts_root / "review-flow.json"
    version_path = store.versions_root / "review-flow" / "versions" / "1.json"
    real_replace = store._atomic_replace

    def fail_catalog(path, model):
        if path == catalog_path:
            raise OSError("injected catalog failure")
        return real_replace(path, model)

    monkeypatch.setattr(store, "_atomic_replace", fail_catalog)
    with pytest.raises(OSError):
        store.publish("review-flow", expected_revision=1, now=20.0)
    monkeypatch.setattr(store, "_atomic_replace", real_replace)

    target = {
        "intent": intent_path,
        "draft": draft_path,
        "version": version_path,
        "catalog": catalog_path,
    }[damage]
    raw = json.loads(target.read_text())
    if damage == "intent":
        raw["version"]["name"] = "mismatch"
    elif damage == "draft":
        raw["name"] = "mismatch"
    elif damage == "version":
        raw["name"] = "mismatch"
    else:
        raw["latest_version"] = 99
    target.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(BlueprintStoreError):
        store.publish("review-flow", expected_revision=1)
    assert intent_path.exists()


def test_pending_publish_intent_freezes_all_draft_mutations_until_recovery(
    tmp_path, monkeypatch
):
    store = BlueprintStore(tmp_path)
    store.create_draft("source-flow", content("Source"), owner="alice")
    source = store.publish("source-flow", expected_revision=1)
    store.create_draft("review-flow", content("v1"), owner="bob")
    version1 = store.publish("review-flow", expected_revision=1)
    draft = store.create_draft_from_version(version1.ref, owner="bob")
    updated = store.update_draft(
        "review-flow", content("v2 pending"), expected_revision=draft.revision
    )
    version2_path = store.versions_root / "review-flow" / "versions" / "2.json"
    real_create = store._atomic_create

    def fail_version_create(path, model):
        if path == version2_path:
            raise OSError("injected version create failure")
        return real_create(path, model)

    monkeypatch.setattr(store, "_atomic_create", fail_version_create)
    with pytest.raises(OSError, match="version create"):
        store.publish("review-flow", expected_revision=updated.revision)
    assert (store.intents_root / "review-flow.json").exists()

    with pytest.raises(BlueprintStoreError, match="publish is pending"):
        store.update_draft(
            "review-flow", content("illegal update"), expected_revision=updated.revision
        )
    with pytest.raises(BlueprintStoreError, match="publish is pending"):
        store.create_draft(
            "review-flow", content("illegal replacement"), owner="mallory"
        )
    with pytest.raises(BlueprintStoreError, match="publish is pending"):
        store.create_draft_from_version(version1.ref, owner="mallory")
    with pytest.raises(BlueprintStoreError, match="publish is pending"):
        store.fork(source.ref, "review-flow", owner="mallory")
    assert store.get_draft("review-flow").name == "v2 pending"

    monkeypatch.setattr(store, "_atomic_create", real_create)
    recovered = store.publish("review-flow", expected_revision=updated.revision)
    assert recovered.version == 2 and recovered.name == "v2 pending"
    assert not (store.intents_root / "review-flow.json").exists()
    with pytest.raises(BlueprintNotFoundError):
        store.get_draft("review-flow")


@pytest.mark.parametrize("corrupt", [True, False])
def test_publish_reconciliation_fails_closed_for_corrupt_or_different_version(
    tmp_path, monkeypatch, corrupt
):
    store = BlueprintStore(tmp_path)
    store.create_draft("review-flow", content(), owner="alice")
    catalog_path = store.catalog_root / "review-flow.json"
    version_path = store.versions_root / "review-flow" / "versions" / "1.json"
    real_replace = store._atomic_replace

    def fail_catalog(path, model):
        if path == catalog_path:
            raise OSError("injected catalog write failure")
        return real_replace(path, model)

    monkeypatch.setattr(store, "_atomic_replace", fail_catalog)
    with pytest.raises(OSError):
        store.publish("review-flow", expected_revision=1, now=20.0)
    monkeypatch.setattr(store, "_atomic_replace", real_replace)

    if corrupt:
        version_path.write_text("{not-json", encoding="utf-8")
        match = "corrupt"
    else:
        raw = json.loads(version_path.read_text(encoding="utf-8"))
        raw["name"] = "A different immutable snapshot"
        version_path.write_text(json.dumps(raw), encoding="utf-8")
        match = "does not match"

    with pytest.raises(BlueprintStoreError, match=match):
        store.publish("review-flow", expected_revision=1)
    assert store.get_catalog_entry("review-flow").latest_version is None
    assert store.get_draft("review-flow").revision == 1


def test_archive_restore_and_listing_only_change_catalog_metadata(tmp_path):
    store = BlueprintStore(tmp_path)
    store.create_draft("review-flow", content(), owner="alice")
    published = store.publish("review-flow", expected_revision=1)

    archived = store.archive("review-flow", at=123.0)
    assert archived.archived_at == 123.0
    assert store.list() == []
    assert store.list(include_archived=True)[0].id == "review-flow"
    assert store.get_version("review-flow", published.version) == published
    assert store.archive("review-flow", at=999.0).archived_at == 123.0
    with pytest.raises(BlueprintArchivedError):
        store.get_active_version(published.ref)

    restored = store.restore("review-flow")
    assert restored.archived_at is None
    assert [entry.id for entry in store.list()] == ["review-flow"]
    assert store.get_active_version(published.ref) == published


def test_archived_blueprint_rejects_every_mutation_until_restore(tmp_path):
    store = BlueprintStore(tmp_path)
    draft = store.create_draft("review-flow", content(), owner="alice")
    version = store.publish("review-flow", expected_revision=draft.revision)
    current = store.create_draft_from_version(version.ref, owner="alice")
    store.archive("review-flow", at=123.0)

    blocked = [
        lambda: store.create_draft_from_version(version.ref, owner="alice"),
        lambda: store.update_draft(
            "review-flow", content("changed"), expected_revision=current.revision
        ),
        lambda: store.publish("review-flow", expected_revision=current.revision),
        lambda: store.delete_draft("review-flow"),
        lambda: store.set_display_name("review-flow", "Changed"),
    ]
    for mutate in blocked:
        with pytest.raises(BlueprintArchivedError, match="restore it"):
            mutate()

    store.restore("review-flow")
    updated = store.update_draft(
        "review-flow", content("changed"), expected_revision=current.revision
    )
    assert updated.name == "changed"


def test_unpublished_blueprint_cannot_be_archived(tmp_path):
    store = BlueprintStore(tmp_path)
    store.create_draft("draft-only", content(), owner="alice")
    with pytest.raises(BlueprintStoreError, match="unpublished"):
        store.archive("draft-only")


def test_fork_records_exact_source_and_is_isolated(tmp_path):
    store = BlueprintStore(tmp_path)
    store.create_draft("source-flow", content(), owner="alice")
    source = store.publish("source-flow", expected_revision=1)

    fork = store.fork(
        source.ref,
        "forked-flow",
        owner="bob",
        display_name="Forked flow",
        now=50.0,
    )
    assert fork.source == ArtifactRef(id="source-flow", version=1)
    assert fork.name == "Forked flow"
    assert fork.base_version is None

    changed = store.update_draft(
        "forked-flow", content("Fork changed"), expected_revision=1
    )
    assert changed.source == source.ref
    assert store.get_version("source-flow", 1).name == "Incident response"


@pytest.mark.parametrize("target_state", ["draft", "published"])
def test_fork_requires_a_brand_new_catalog_identity(tmp_path, target_state):
    store = BlueprintStore(tmp_path)
    store.create_draft("source-flow", content("Source"), owner="alice")
    source = store.publish("source-flow", expected_revision=1)
    store.create_draft("target-flow", content("Target"), owner="bob")
    if target_state == "published":
        store.publish("target-flow", expected_revision=1)

    source_before = store.get_version("source-flow", 1).model_dump(mode="json")
    target_catalog_before = store.get_catalog_entry("target-flow").model_dump(mode="json")
    target_versions_before = [
        item.model_dump(mode="json") for item in store.list_versions("target-flow")
    ]

    with pytest.raises(BlueprintAlreadyExistsError, match="fork target"):
        store.fork(source.ref, "target-flow", owner="mallory")

    assert store.get_version("source-flow", 1).model_dump(mode="json") == source_before
    assert store.get_catalog_entry("target-flow").model_dump(mode="json") == target_catalog_before
    assert [
        item.model_dump(mode="json") for item in store.list_versions("target-flow")
    ] == target_versions_before


def test_create_draft_rejects_client_supplied_lineage(tmp_path):
    store = BlueprintStore(tmp_path)
    supplied = content(source=ArtifactRef(id="claimed-source", version=1))
    with pytest.raises(BlueprintStoreError, match="cannot accept source lineage"):
        store.create_draft("new-flow", supplied, owner="mallory")
    assert store.list(include_archived=True) == []


def test_unpublished_draft_can_be_deleted_but_published_history_remains(tmp_path):
    store = BlueprintStore(tmp_path)
    store.create_draft("temporary-flow", content(), owner="alice")
    store.delete_draft("temporary-flow")
    assert store.list(include_archived=True) == []
    with pytest.raises(BlueprintNotFoundError):
        store.get_draft("temporary-flow")

    store.create_draft("kept-flow", content(), owner="alice")
    store.publish("kept-flow", expected_revision=1)
    store.create_draft_from_version(
        ArtifactRef(id="kept-flow", version=1), owner="alice"
    )
    store.delete_draft("kept-flow")
    assert store.get_catalog_entry("kept-flow").latest_version == 1
    assert store.get_version("kept-flow", 1).version == 1


@pytest.mark.parametrize("catalog_state", ["missing", "corrupt"])
def test_delete_draft_preflights_catalog_before_destroying_draft(
    tmp_path, catalog_state
):
    store = BlueprintStore(tmp_path)
    store.create_draft("safe-flow", content(), owner="alice")
    catalog_path = store.catalog_root / "safe-flow.json"
    if catalog_state == "missing":
        catalog_path.unlink()
        error = BlueprintNotFoundError
    else:
        catalog_path.write_text("{not-json", encoding="utf-8")
        error = BlueprintCorruptionError

    with pytest.raises(error):
        store.delete_draft("safe-flow")
    assert (store.drafts_root / "safe-flow.json").is_file()


def test_delete_draft_recovers_after_draft_removed_before_catalog_cleanup(
    tmp_path, monkeypatch
):
    store = BlueprintStore(tmp_path)
    store.create_draft("temporary-flow", content(), owner="alice")
    catalog_path = store.catalog_root / "temporary-flow.json"
    real_unlink = store._unlink
    failed = False

    def fail_catalog_once(path, *args, **kwargs):
        nonlocal failed
        if path == catalog_path and not failed:
            failed = True
            raise OSError("injected catalog cleanup failure")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(store, "_unlink", fail_catalog_once)
    with pytest.raises(OSError, match="catalog cleanup"):
        store.delete_draft("temporary-flow")
    assert not (store.drafts_root / "temporary-flow.json").exists()
    assert catalog_path.exists()

    store.delete_draft("temporary-flow")
    assert not catalog_path.exists()


def test_duplicate_draft_and_missing_records_fail_loudly(tmp_path):
    store = BlueprintStore(tmp_path)
    store.create_draft("one-flow", content(), owner="alice")
    with pytest.raises(BlueprintAlreadyExistsError):
        store.create_draft("one-flow", content(), owner="alice")
    with pytest.raises(BlueprintNotFoundError):
        store.get_version("one-flow", 1)
    with pytest.raises(BlueprintNotFoundError):
        store.get_catalog_entry("missing-flow")


@pytest.mark.parametrize("kind", ["catalog", "draft", "version"])
def test_persisted_record_identity_must_match_requested_path(tmp_path, kind):
    store = BlueprintStore(tmp_path)
    store.create_draft("right-flow", content(), owner="alice")
    if kind == "catalog":
        path = store.catalog_root / "right-flow.json"
        raw = json.loads(path.read_text())
        raw["id"] = "wrong-flow"
        path.write_text(json.dumps(raw), encoding="utf-8")
        read = lambda: store.get_catalog_entry("right-flow")
    elif kind == "draft":
        path = store.drafts_root / "right-flow.json"
        raw = json.loads(path.read_text())
        raw["id"] = "wrong-flow"
        path.write_text(json.dumps(raw), encoding="utf-8")
        read = lambda: store.get_draft("right-flow")
    else:
        store.publish("right-flow", expected_revision=1)
        path = store.versions_root / "right-flow" / "versions" / "1.json"
        raw = json.loads(path.read_text())
        raw["id"] = "wrong-flow"
        path.write_text(json.dumps(raw), encoding="utf-8")
        read = lambda: store.get_version("right-flow", 1)

    with pytest.raises(BlueprintCorruptionError, match="identity mismatch"):
        read()


def test_catalog_and_version_lists_enforce_path_identity(tmp_path):
    store = BlueprintStore(tmp_path)
    store.create_draft("right-flow", content(), owner="alice")
    store.publish("right-flow", expected_revision=1)

    catalog_path = store.catalog_root / "right-flow.json"
    catalog = json.loads(catalog_path.read_text())
    catalog["id"] = "wrong-flow"
    catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
    with pytest.raises(BlueprintCorruptionError, match="identity mismatch"):
        store.list(include_archived=True)

    catalog["id"] = "right-flow"
    catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
    version_path = store.versions_root / "right-flow" / "versions" / "1.json"
    version = json.loads(version_path.read_text())
    version["version"] = 2
    version_path.write_text(json.dumps(version), encoding="utf-8")
    with pytest.raises(BlueprintCorruptionError, match="identity mismatch"):
        store.list_versions("right-flow")


def test_store_rejects_symlink_escape(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "state"
    root.mkdir()
    (root / "blueprint-catalog").symlink_to(outside, target_is_directory=True)

    store = BlueprintStore(root)
    with pytest.raises(BlueprintStoreError, match="symlink"):
        store.create_draft("unsafe-flow", content(), owner="alice")
    assert list(outside.iterdir()) == []


def test_persisted_json_contains_no_secret_literal(tmp_path):
    store = BlueprintStore(tmp_path)
    secret = InputSpec(
        name="token",
        schema={"type": "string"},
        secret=True,
        default=SecretBindingRef(binding="service-token"),
    )
    store.create_draft("safe-flow", content(inputs=[secret]), owner="alice")
    raw = json.loads((store.drafts_root / "safe-flow.json").read_text())
    assert raw["inputs"][0]["default"] == {"binding": "service-token"}
    assert "plaintext" not in json.dumps(raw)


def test_store_fsyncs_parent_directories_for_durable_mutations(tmp_path, monkeypatch):
    store = BlueprintStore(tmp_path)
    fsynced = []
    real_fsync_directory = store._fsync_directory

    def record_fsync(path):
        fsynced.append(path)
        return real_fsync_directory(path)

    monkeypatch.setattr(store, "_fsync_directory", record_fsync)
    store.create_draft("durable-flow", content(), owner="alice")
    store.publish("durable-flow", expected_revision=1)

    assert store.catalog_root in fsynced
    assert store.drafts_root in fsynced
    assert store.intents_root in fsynced
    assert store.versions_root / "durable-flow" / "versions" in fsynced
