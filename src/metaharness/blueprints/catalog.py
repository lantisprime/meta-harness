"""Read-only catalog facade over shipped and user-owned Blueprints."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import Field

from metaharness.blueprints.builtins import (
    get_builtin_version,
    get_latest_builtin_version,
    is_builtin_id,
    list_latest_builtin_versions,
    list_builtin_versions,
)
from metaharness.blueprints.models import (
    ArtifactRef,
    BlueprintCatalogEntry,
    BlueprintDraft,
    BlueprintVersion,
    StrictModel,
)
from metaharness.blueprints.store import BlueprintNotFoundError, BlueprintStore


class BlueprintCatalogConflictError(RuntimeError):
    """Raised when persistent state shadows a reserved built-in identity."""


class BlueprintForkTargetError(ValueError):
    pass


class CatalogBlueprint(StrictModel):
    """API projection; origin and actions never enter persisted records."""

    id: str
    display_name: str
    origin: Literal["builtin", "owned", "fork"]
    archived: bool = False
    latest_version: Optional[int] = Field(default=None, ge=1, strict=True)
    has_draft: bool = False
    source: Optional[ArtifactRef] = None
    edit_mode: Optional[Literal["in_place", "fork"]] = None
    stage_count: int = Field(ge=0)
    tool_ids: tuple[str, ...] = ()
    supported_actions: tuple[str, ...]


_BUILTIN_ACTIONS = ("run", "edit", "fork", "versions")
_OWNED_PUBLISHED_ACTIONS = (
    "run",
    "edit",
    "fork",
    "versions",
    "archive",
)
_OWNED_DRAFT_ACTIONS = ("edit", "publish", "delete_draft")
_ARCHIVED_ACTIONS = ("versions", "restore")


class BlueprintCatalog:
    """Union built-ins and a ``BlueprintStore`` without writing either source."""

    def __init__(self, store: BlueprintStore) -> None:
        self.store = store

    @staticmethod
    def _builtin_item(version: BlueprintVersion) -> CatalogBlueprint:
        return CatalogBlueprint(
            id=version.id,
            display_name=version.name,
            origin="builtin",
            latest_version=version.version,
            edit_mode="fork",
            stage_count=len(version.workflow.steps),
            tool_ids=tuple(
                sorted({tool for step in version.workflow.steps for tool in step.tools})
            ),
            supported_actions=_BUILTIN_ACTIONS,
        )

    def _draft_or_none(self, blueprint_id: str) -> BlueprintDraft | None:
        try:
            return self.store.get_draft(blueprint_id)
        except BlueprintNotFoundError:
            return None

    def _owned_item(self, entry: BlueprintCatalogEntry) -> CatalogBlueprint:
        draft = self._draft_or_none(entry.id)
        latest = (
            self.store.get_version(entry.id, entry.latest_version)
            if entry.latest_version is not None
            else None
        )
        source = draft.source if draft is not None else (latest.source if latest else None)
        content = draft or latest
        archived = entry.archived_at is not None
        if archived:
            actions = _ARCHIVED_ACTIONS
        elif entry.latest_version is not None:
            actions = _OWNED_PUBLISHED_ACTIONS
            if draft is not None:
                actions = (*actions, "publish", "delete_draft")
        else:
            actions = _OWNED_DRAFT_ACTIONS
        return CatalogBlueprint(
            id=entry.id,
            display_name=entry.display_name,
            origin="fork" if source is not None else "owned",
            archived=archived,
            latest_version=entry.latest_version,
            has_draft=draft is not None,
            source=source,
            edit_mode=None if archived else "in_place",
            stage_count=len(content.workflow.steps) if content is not None else 0,
            tool_ids=tuple(
                sorted(
                    {
                        tool
                        for step in (content.workflow.steps if content is not None else [])
                        for tool in step.tools
                    }
                )
            ),
            supported_actions=actions,
        )

    def _owned_entries(self, *, include_archived: bool) -> list[BlueprintCatalogEntry]:
        entries = self.store.list(include_archived=True)
        reserved = {item.id for item in list_builtin_versions()}
        collisions = sorted(reserved & {entry.id for entry in entries})
        if collisions:
            raise BlueprintCatalogConflictError(
                f"persistent blueprints use reserved built-in ids: {collisions}"
            )
        if include_archived:
            return entries
        return [entry for entry in entries if entry.archived_at is None]

    def list(self, *, include_archived: bool = False) -> list[CatalogBlueprint]:
        builtins = [self._builtin_item(item) for item in list_latest_builtin_versions()]
        owned = [
            self._owned_item(entry)
            for entry in self._owned_entries(include_archived=include_archived)
        ]
        return sorted((*builtins, *owned), key=lambda item: item.id)

    def get(self, blueprint_id: str) -> CatalogBlueprint:
        builtin = get_latest_builtin_version(blueprint_id)
        if builtin is not None:
            # Do not silently hide a same-id persistent record.
            self._owned_entries(include_archived=True)
            return self._builtin_item(builtin)
        return self._owned_item(self.store.get_catalog_entry(blueprint_id))

    def get_version(self, ref: ArtifactRef) -> BlueprintVersion:
        builtin = get_builtin_version(ref.id, ref.version)
        if builtin is not None:
            self._owned_entries(include_archived=True)
            return builtin
        if is_builtin_id(ref.id):
            self._owned_entries(include_archived=True)
            raise BlueprintNotFoundError(
                f"built-in blueprint version not found: {ref.id}@{ref.version}"
            )
        return self.store.get_version(ref.id, ref.version)

    def list_versions(self, blueprint_id: str) -> list[BlueprintVersion]:
        if is_builtin_id(blueprint_id):
            self._owned_entries(include_archived=True)
            return [
                item for item in list_builtin_versions() if item.id == blueprint_id
            ]
        return self.store.list_versions(blueprint_id)

    def fork(
        self,
        source: ArtifactRef,
        *,
        new_id: str,
        owner: str,
        display_name: Optional[str] = None,
        now: Optional[float] = None,
    ) -> BlueprintDraft:
        """Resolve an exact source and persist an independent owned draft."""
        if is_builtin_id(new_id):
            raise BlueprintForkTargetError(
                "a fork target cannot use a reserved built-in blueprint id"
            )
        ArtifactRef(id=new_id, version=1)  # validate the target identity now
        snapshot = (
            self.get_version(source)
            if is_builtin_id(source.id)
            else self.store.get_active_version(source)
        )
        return self.store.fork_snapshot(
            snapshot,
            new_id,
            owner=owner,
            display_name=display_name,
            now=now,
        )
