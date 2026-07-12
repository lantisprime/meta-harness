"""Built-in Blueprint seeds and the read-only catalog projection."""
from __future__ import annotations

import pytest

from metaharness.blueprints.builtins import (
    BUILTIN_GOLDEN_DIGESTS,
    BuiltinBlueprintRegistry,
    DuplicateBuiltinRefError,
    builtin_digests,
    get_builtin_version,
    list_builtin_versions,
)
from metaharness.blueprints.catalog import (
    BlueprintCatalog,
    BlueprintCatalogConflictError,
    BlueprintForkTargetError,
)
from metaharness.blueprints.models import ArtifactRef, BlueprintContent
from metaharness.blueprints.store import (
    BlueprintAlreadyExistsError,
    BlueprintNotFoundError,
    BlueprintStore,
)


def _content(name: str = "Owned") -> BlueprintContent:
    return BlueprintContent.model_validate(
        {"name": name, "workflow": {"name": "owned", "steps": []}}
    )


def test_builtin_versions_are_exact_goal_parameterized_v1_snapshots():
    versions = list_builtin_versions()
    assert {(item.id, item.version) for item in versions} == {
        ("software-engineering", 1),
        ("research", 1),
    }
    for item in versions:
        assert item.ref == ArtifactRef(id=item.id, version=1)
        assert [(spec.name, spec.required, spec.schema) for spec in item.inputs] == [
            ("goal", True, {"type": "string", "minLength": 1})
        ]
        assert item.default_context == {}
        assert all(step.inputs["goal"] == "$context.goal" for step in item.workflow.steps)


def test_builtin_reads_are_isolated_from_caller_mutation():
    first = get_builtin_version("research", 1)
    assert first is not None
    first.name = "mutated"
    second = get_builtin_version("research", 1)
    assert second is not None
    assert second.name == "Research & report"
    assert get_builtin_version("research", 2) is None


def test_registry_retains_exact_history_and_derives_latest_by_max_version():
    v1 = get_builtin_version("research", 1)
    assert v1 is not None
    version2 = v1.__class__.model_validate(
        {**v1.model_dump(), "name": "Research v2", "version": 2, "published_at": 10}
    )
    registry = BuiltinBlueprintRegistry([version2, v1])

    assert [item.version for item in registry.versions("research")] == [1, 2]
    assert registry.latest("research") == version2
    assert registry.get("research", 1) == v1
    fetched = registry.get("research", 2)
    assert fetched is not None
    fetched.name = "caller mutation"
    assert registry.get("research", 2) == version2

    with pytest.raises(DuplicateBuiltinRefError, match="research@1"):
        BuiltinBlueprintRegistry([v1, v1])


def test_builtin_content_has_explicit_golden_digest_guard():
    assert builtin_digests() == BUILTIN_GOLDEN_DIGESTS


def test_catalog_unions_builtin_owned_and_fork_without_mutating_store(tmp_path):
    store = BlueprintStore(tmp_path)
    draft = store.create_draft("owned", _content(), owner="user", now=1)
    published = store.publish("owned", expected_revision=draft.revision, now=2)
    source = published.ref
    catalog = BlueprintCatalog(store)
    catalog.fork(source, new_id="my-research", owner="user", now=3)
    before = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }

    items = {item.id: item for item in catalog.list()}
    assert set(items) == {"software-engineering", "research", "owned", "my-research"}
    assert items["research"].origin == "builtin"
    assert items["research"].supported_actions == (
        "run", "edit", "fork", "versions"
    )
    assert items["research"].edit_mode == "fork"
    assert items["research"].stage_count == 3
    assert items["research"].tool_ids == (
        "grep", "list_files", "read_file", "web_fetch"
    )
    assert items["owned"].origin == "owned"
    assert items["owned"].supported_actions == (
        "run", "edit", "fork", "versions", "archive"
    )
    assert items["owned"].edit_mode == "in_place"
    assert items["my-research"].origin == "fork"
    assert items["my-research"].source == source
    after = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_catalog_exact_detail_versions_and_archive_actions(tmp_path):
    store = BlueprintStore(tmp_path)
    draft = store.create_draft("owned", _content(), owner="user", now=1)
    published = store.publish("owned", expected_revision=draft.revision, now=2)
    store.archive("owned", at=3)
    catalog = BlueprintCatalog(store)

    assert "owned" not in {item.id for item in catalog.list()}
    archived = {item.id: item for item in catalog.list(include_archived=True)}["owned"]
    assert archived.archived is True
    assert archived.edit_mode is None
    assert archived.supported_actions == ("versions", "restore")
    assert catalog.get("owned") == archived
    assert catalog.get_version(published.ref) == published
    assert catalog.list_versions("owned") == [published]
    assert catalog.list_versions("research") == [get_builtin_version("research", 1)]
    with pytest.raises(BlueprintNotFoundError, match="built-in blueprint version not found"):
        catalog.get_version(ArtifactRef(id="research", version=2))


def test_catalog_persists_builtin_fork_with_exact_lineage_and_isolated_source(tmp_path):
    catalog = BlueprintCatalog(BlueprintStore(tmp_path))
    source_before = catalog.get_version(ArtifactRef(id="research", version=1))
    draft = catalog.fork(
        ArtifactRef(id="research", version=1),
        new_id="my-research",
        owner="user",
        display_name="My research",
        now=4,
    )

    assert draft.id == "my-research"
    assert draft.source == ArtifactRef(id="research", version=1)
    assert draft.name == "My research"
    assert draft.workflow == source_before.workflow
    draft.workflow.name = "mutated draft"
    assert catalog.get_version(ArtifactRef(id="research", version=1)) == source_before


def test_store_fork_snapshot_reuses_new_identity_guards_and_copies_source(tmp_path):
    store = BlueprintStore(tmp_path)
    source = get_builtin_version("research", 1)
    assert source is not None
    draft = store.fork_snapshot(source, "external-fork", owner="user", now=1)
    source.workflow.name = "mutated after persistence"

    persisted = store.get_draft("external-fork")
    assert persisted == draft
    assert persisted.source == ArtifactRef(id="research", version=1)
    assert persisted.workflow.name == "research"
    with pytest.raises(BlueprintAlreadyExistsError):
        store.fork_snapshot(source, "external-fork", owner="user", now=2)
    assert store.get_draft("external-fork") == persisted


def test_catalog_fork_rejects_every_reserved_builtin_target(tmp_path):
    catalog = BlueprintCatalog(BlueprintStore(tmp_path))
    with pytest.raises(BlueprintForkTargetError, match="reserved built-in"):
        catalog.fork(
            ArtifactRef(id="research", version=1),
            new_id="research",
            owner="user",
        )
    with pytest.raises(BlueprintForkTargetError, match="reserved built-in"):
        catalog.fork(
            ArtifactRef(id="research", version=1),
            new_id="software-engineering",
            owner="user",
        )


def test_catalog_forks_owned_exact_version_not_latest(tmp_path):
    store = BlueprintStore(tmp_path)
    first_draft = store.create_draft("source", _content("Version one"), owner="user")
    version1 = store.publish("source", expected_revision=first_draft.revision, now=1)
    second_draft = store.create_draft_from_version(
        version1.ref, owner="user", now=2
    )
    store.update_draft(
        "source", _content("Version two"), expected_revision=second_draft.revision, now=3
    )
    store.publish("source", expected_revision=second_draft.revision + 1, now=4)

    fork = BlueprintCatalog(store).fork(
        version1.ref, new_id="fork-v1", owner="user", now=5
    )
    assert fork.name == "Version one"
    assert fork.source == version1.ref


def test_owned_fork_linearizes_when_archive_happens_after_active_snapshot(
    tmp_path, monkeypatch
):
    store = BlueprintStore(tmp_path)
    draft = store.create_draft("source", _content("captured"), owner="user")
    version = store.publish("source", expected_revision=draft.revision, now=1)
    original = store.get_active_version

    def archive_after_capture(ref):
        snapshot = original(ref)
        store.archive(ref.id, at=2)
        return snapshot

    monkeypatch.setattr(store, "get_active_version", archive_after_capture)
    fork = BlueprintCatalog(store).fork(
        version.ref, new_id="captured-fork", owner="user", now=3
    )

    assert fork.name == "captured"
    assert fork.source == version.ref
    assert store.get_catalog_entry("source").archived_at == 2


def test_reserved_builtin_id_collision_fails_closed(tmp_path):
    store = BlueprintStore(tmp_path)
    store.create_draft("research", _content(), owner="user")
    catalog = BlueprintCatalog(store)
    with pytest.raises(BlueprintCatalogConflictError, match="reserved built-in ids"):
        catalog.list()
    with pytest.raises(BlueprintCatalogConflictError, match="reserved built-in ids"):
        catalog.get("research")
