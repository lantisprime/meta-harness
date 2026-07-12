"""Filesystem persistence for mutable Blueprint drafts and immutable versions."""
from __future__ import annotations

import fcntl
import json
import os
import tempfile
import time
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Iterator, Optional

from metaharness.blueprints.models import (
    ArtifactRef,
    BlueprintCatalogEntry,
    BlueprintContent,
    BlueprintDraft,
    BlueprintPublishIntent,
    BlueprintVersion,
    _validate_slug,
)


class BlueprintStoreError(RuntimeError):
    """Base class for typed Blueprint persistence failures."""


class BlueprintNotFoundError(BlueprintStoreError):
    pass


class BlueprintAlreadyExistsError(BlueprintStoreError):
    pass


class BlueprintCorruptionError(BlueprintStoreError):
    pass


class RevisionConflictError(BlueprintStoreError):
    def __init__(self, blueprint_id: str, expected: int, actual: int) -> None:
        self.blueprint_id = blueprint_id
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"draft {blueprint_id!r} revision conflict: expected {expected}, actual {actual}"
        )


class InvalidRevisionError(BlueprintStoreError):
    def __init__(self, value: object) -> None:
        self.value = value
        super().__init__(
            "expected_revision must be a positive integer (bool, string, and float are invalid)"
        )


class BlueprintArchivedError(BlueprintStoreError):
    def __init__(self, blueprint_id: str) -> None:
        super().__init__(
            f"blueprint {blueprint_id!r} is archived; restore it before changing it"
        )


class BlueprintStore:
    """A supplied-root store with separate catalog, draft, intent, and version trees.

    ``root`` is trusted against hostile same-user replacement while an operation
    is in flight. Generated descendants reject symlinks and traversal, but POSIX
    cannot make a path hierarchy race-proof against an owner concurrently
    renaming directories without stronger OS isolation.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.catalog_root = self.root / "blueprint-catalog"
        self.drafts_root = self.root / "blueprint-drafts"
        self.intents_root = self.root / "blueprint-publish-intents"
        self.versions_root = self.root / "blueprints"
        self.locks_root = self.root / ".blueprint-locks"

    def _id(self, blueprint_id: str) -> str:
        return _validate_slug(blueprint_id)

    @staticmethod
    def _expected_revision(value: object) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise InvalidRevisionError(value)
        return value

    def _catalog_path(self, blueprint_id: str) -> Path:
        return self.catalog_root / f"{self._id(blueprint_id)}.json"

    def _draft_path(self, blueprint_id: str) -> Path:
        return self.drafts_root / f"{self._id(blueprint_id)}.json"

    def _version_path(self, blueprint_id: str, version: int) -> Path:
        ref = ArtifactRef(id=blueprint_id, version=version)
        return self.versions_root / ref.id / "versions" / f"{ref.version}.json"

    def _intent_path(self, blueprint_id: str) -> Path:
        return self.intents_root / f"{self._id(blueprint_id)}.json"

    def _safe_existing(self, path: Path) -> Path:
        """Reject a symlinked catalog component or a path outside the supplied root."""
        resolved = path.resolve(strict=False)
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise BlueprintStoreError(f"blueprint path escapes storage root: {path}") from exc
        cursor = self.root
        try:
            relative = path.relative_to(self.root)
        except ValueError as exc:
            raise BlueprintStoreError(f"blueprint path escapes storage root: {path}") from exc
        for part in relative.parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise BlueprintStoreError(f"blueprint path uses a symlink: {cursor}")
        return path

    @contextmanager
    def _lock(self, blueprint_id: str) -> Iterator[None]:
        blueprint_id = self._id(blueprint_id)
        self._ensure_directory(self.locks_root)
        lock_path = self._safe_existing(self.locks_root / f"{blueprint_id}.lock")
        lock_existed = lock_path.exists()
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        if not lock_existed:
            self._fsync_directory(self.locks_root)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _read(
        self,
        path: Path,
        model_type,
        *,
        expected_id: Optional[str] = None,
        expected_version: Optional[int] = None,
    ):
        path = self._safe_existing(path)
        try:
            value = model_type.model_validate_json(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise BlueprintNotFoundError(f"blueprint record not found: {path.name}") from exc
        except Exception as exc:
            raise BlueprintCorruptionError(
                f"invalid {model_type.__name__} record at {path}"
            ) from exc
        if expected_id is not None and getattr(value, "id", None) != expected_id:
            raise BlueprintCorruptionError(
                f"record identity mismatch at {path}: expected id {expected_id!r}, "
                f"found {getattr(value, 'id', None)!r}"
            )
        if (
            expected_version is not None
            and getattr(value, "version", None) != expected_version
        ):
            raise BlueprintCorruptionError(
                f"record identity mismatch at {path}: expected version "
                f"{expected_version}, found {getattr(value, 'version', None)!r}"
            )
        return value

    def _ensure_directory(self, path: Path) -> None:
        self._safe_existing(path)
        missing: list[Path] = []
        cursor = path
        while cursor != self.root and not cursor.exists():
            missing.append(cursor)
            cursor = cursor.parent
        path.mkdir(parents=True, exist_ok=True)
        for created in reversed(missing):
            self._fsync_directory(created.parent)

    def _fsync_directory(self, path: Path) -> None:
        path = self._safe_existing(path)
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        fd = os.open(path, flags)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _unlink(self, path: Path, *, missing_ok: bool = False) -> None:
        path = self._safe_existing(path)
        try:
            path.unlink()
        except FileNotFoundError:
            if not missing_ok:
                raise
            return
        self._fsync_directory(path.parent)

    def _atomic_replace(self, path: Path, model) -> None:
        # Check the unresolved parent before mkdir: if an intermediate component
        # is a symlink, creating missing descendants could otherwise write
        # outside the supplied root before the later target check rejects it.
        self._ensure_directory(path.parent)
        self._safe_existing(path)
        payload = json.dumps(
            model.model_dump(mode="json"), indent=2, sort_keys=True, ensure_ascii=False
        )
        fd, tmp_name = tempfile.mkstemp(
            dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
        )
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, path)
            self._fsync_directory(path.parent)
        except BaseException:
            with suppress(OSError):
                os.close(fd)
            with suppress(OSError):
                self._unlink(tmp_path, missing_ok=True)
            raise

    def _atomic_create(self, path: Path, model) -> None:
        """Atomically create a version without any operation that can overwrite it."""
        self._ensure_directory(path.parent)
        self._safe_existing(path)
        payload = json.dumps(
            model.model_dump(mode="json"), indent=2, sort_keys=True, ensure_ascii=False
        )
        fd, tmp_name = tempfile.mkstemp(
            dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
        )
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(tmp_path, path)
                self._fsync_directory(path.parent)
            except FileExistsError as exc:
                raise BlueprintAlreadyExistsError(
                    f"immutable blueprint version already exists: {path}"
                ) from exc
        finally:
            with suppress(OSError):
                self._unlink(tmp_path, missing_ok=True)

    @staticmethod
    def _content_fields(value: BlueprintContent) -> dict:
        dumped = value.model_dump(mode="python")
        return {name: dumped[name] for name in BlueprintContent.model_fields}

    def _matching_existing_version(
        self,
        path: Path,
        *,
        blueprint_id: str,
        version: int,
        intended: BlueprintVersion,
    ) -> BlueprintVersion:
        """Load an interrupted publish snapshot and prove it is this draft.

        The publish intent already carries the machine-assigned timestamp, so
        every field participates in the normalized comparison.
        """
        try:
            existing = self._read(
                path,
                BlueprintVersion,
                expected_id=blueprint_id,
                expected_version=version,
            )
        except BlueprintCorruptionError as exc:
            raise BlueprintStoreError(
                f"immutable blueprint version {blueprint_id!r} v{version} "
                "is corrupt; refusing publish reconciliation"
            ) from exc
        if existing.model_dump(mode="json") != intended.model_dump(mode="json"):
            raise BlueprintStoreError(
                f"immutable blueprint version {blueprint_id!r} v{version} "
                "does not match the draft; refusing publish reconciliation"
            )
        return existing

    # -- catalog -----------------------------------------------------------------

    def list(self, *, include_archived: bool = False) -> list[BlueprintCatalogEntry]:
        if not self.catalog_root.exists():
            return []
        entries = []
        for path in sorted(self.catalog_root.glob("*.json")):
            try:
                expected_id = self._id(path.stem)
            except ValueError as exc:
                raise BlueprintCorruptionError(
                    f"invalid catalog path identity: {path}"
                ) from exc
            entries.append(
                self._read(path, BlueprintCatalogEntry, expected_id=expected_id)
            )
        return entries if include_archived else [e for e in entries if e.archived_at is None]

    def get_catalog_entry(self, blueprint_id: str) -> BlueprintCatalogEntry:
        blueprint_id = self._id(blueprint_id)
        return self._read(
            self._catalog_path(blueprint_id),
            BlueprintCatalogEntry,
            expected_id=blueprint_id,
        )

    def set_display_name(self, blueprint_id: str, display_name: str) -> BlueprintCatalogEntry:
        with self._lock(blueprint_id):
            entry = self.get_catalog_entry(blueprint_id)
            self._require_active(entry)
            entry.display_name = display_name
            entry = BlueprintCatalogEntry.model_validate(entry.model_dump())
            self._atomic_replace(self._catalog_path(blueprint_id), entry)
            return entry

    def archive(self, blueprint_id: str, *, at: Optional[float] = None) -> BlueprintCatalogEntry:
        with self._lock(blueprint_id):
            entry = self.get_catalog_entry(blueprint_id)
            if entry.latest_version is None:
                raise BlueprintStoreError(
                    f"cannot archive unpublished blueprint {blueprint_id!r}"
                )
            if entry.archived_at is not None:
                return entry
            entry.archived_at = time.time() if at is None else at
            self._atomic_replace(self._catalog_path(blueprint_id), entry)
            return entry

    def restore(self, blueprint_id: str) -> BlueprintCatalogEntry:
        with self._lock(blueprint_id):
            entry = self.get_catalog_entry(blueprint_id)
            entry.archived_at = None
            self._atomic_replace(self._catalog_path(blueprint_id), entry)
            return entry

    @staticmethod
    def _require_active(entry: BlueprintCatalogEntry) -> None:
        if entry.archived_at is not None:
            raise BlueprintArchivedError(entry.id)

    def require_active(self, blueprint_id: str) -> BlueprintCatalogEntry:
        """Prove an owned blueprint is active while holding its artifact lock."""
        with self._lock(blueprint_id):
            entry = self.get_catalog_entry(blueprint_id)
            self._require_active(entry)
            return entry

    # -- drafts ------------------------------------------------------------------

    def create_draft(
        self,
        blueprint_id: str,
        content: BlueprintContent,
        *,
        owner: str,
        base_version: Optional[int] = None,
        now: Optional[float] = None,
    ) -> BlueprintDraft:
        if content.source is not None:
            raise BlueprintStoreError(
                "create_draft cannot accept source lineage; use fork or "
                "create_draft_from_version"
            )
        return self._create_draft(
            blueprint_id,
            content,
            owner=owner,
            base_version=base_version,
            now=now,
            allow_source=False,
            require_new_catalog=False,
        )

    def _create_draft(
        self,
        blueprint_id: str,
        content: BlueprintContent,
        *,
        owner: str,
        base_version: Optional[int] = None,
        now: Optional[float] = None,
        allow_source: bool,
        require_new_catalog: bool = False,
    ) -> BlueprintDraft:
        blueprint_id = self._id(blueprint_id)
        if content.source is not None and not allow_source:
            raise BlueprintStoreError("unverified source lineage is not allowed")
        timestamp = time.time() if now is None else now
        # Validate the complete record before creating catalog state. A bad owner
        # or content contract must not leave a catalog-only orphan behind.
        draft = BlueprintDraft(
            **self._content_fields(content),
            id=blueprint_id,
            revision=1,
            base_version=base_version,
            owner=owner,
            created_at=timestamp,
            updated_at=timestamp,
        )
        with self._lock(blueprint_id):
            if self._intent_path(blueprint_id).exists():
                raise BlueprintStoreError(
                    f"cannot create draft {blueprint_id!r} while publish is pending"
                )
            catalog_path = self._catalog_path(blueprint_id)
            existing_entry = None
            if catalog_path.exists():
                existing_entry = self._read(
                    catalog_path, BlueprintCatalogEntry, expected_id=blueprint_id
                )
                self._require_active(existing_entry)
            if require_new_catalog and (
                catalog_path.exists() or self._draft_path(blueprint_id).exists()
            ):
                raise BlueprintAlreadyExistsError(
                    f"fork target {blueprint_id!r} already exists"
                )
            if self._draft_path(blueprint_id).exists():
                raise BlueprintAlreadyExistsError(f"draft {blueprint_id!r} already exists")
            if catalog_path.exists():
                entry = existing_entry
                if base_version is not None:
                    self.get_version(blueprint_id, base_version)
            else:
                if base_version is not None:
                    raise BlueprintNotFoundError(
                        f"cannot base new blueprint {blueprint_id!r} on its missing version"
                    )
                entry = BlueprintCatalogEntry(
                    id=blueprint_id, display_name=content.name, latest_version=None
                )
                self._atomic_create(catalog_path, entry)
            self._atomic_create(self._draft_path(blueprint_id), draft)
            return draft

    def create_draft_from_version(
        self, ref: ArtifactRef, *, owner: str, now: Optional[float] = None
    ) -> BlueprintDraft:
        version = self.get_version(ref.id, ref.version)
        content = BlueprintContent.model_validate(self._content_fields(version))
        return self._create_draft(
            ref.id,
            content,
            owner=owner,
            base_version=ref.version,
            now=now,
            allow_source=True,
            require_new_catalog=False,
        )

    def get_draft(self, blueprint_id: str) -> BlueprintDraft:
        blueprint_id = self._id(blueprint_id)
        return self._read(
            self._draft_path(blueprint_id), BlueprintDraft, expected_id=blueprint_id
        )

    def update_draft(
        self,
        blueprint_id: str,
        content: BlueprintContent,
        *,
        expected_revision: int,
        now: Optional[float] = None,
    ) -> BlueprintDraft:
        blueprint_id = self._id(blueprint_id)
        expected_revision = self._expected_revision(expected_revision)
        with self._lock(blueprint_id):
            if self._intent_path(blueprint_id).exists():
                raise BlueprintStoreError(
                    f"cannot update draft {blueprint_id!r} while publish is pending"
                )
            self._require_active(self.get_catalog_entry(blueprint_id))
            current = self.get_draft(blueprint_id)
            if current.revision != expected_revision:
                raise RevisionConflictError(
                    blueprint_id, expected_revision, current.revision
                )
            content_fields = self._content_fields(content)
            # Fork provenance is machine-owned lineage, not editable content.
            # Keeping it across revisions prevents a full-form draft save from
            # silently laundering a fork into an apparently original artifact.
            content_fields["source"] = current.source
            updated = BlueprintDraft(
                **content_fields,
                id=current.id,
                revision=current.revision + 1,
                base_version=current.base_version,
                owner=current.owner,
                created_at=current.created_at,
                updated_at=time.time() if now is None else now,
            )
            self._atomic_replace(self._draft_path(blueprint_id), updated)
            return updated

    def delete_draft(self, blueprint_id: str) -> None:
        blueprint_id = self._id(blueprint_id)
        with self._lock(blueprint_id):
            # Catalog is preflighted before destructive mutation. A missing or
            # corrupt catalog must leave an existing draft untouched.
            entry = self.get_catalog_entry(blueprint_id)
            self._require_active(entry)
            intent_path = self._safe_existing(self._intent_path(blueprint_id))
            if intent_path.exists():
                raise BlueprintStoreError(
                    f"cannot delete draft {blueprint_id!r} while publish is pending"
                )
            path = self._safe_existing(self._draft_path(blueprint_id))
            if path.exists():
                self._read(path, BlueprintDraft, expected_id=blueprint_id)
                self._unlink(path)
            elif entry.latest_version is not None:
                raise BlueprintNotFoundError(f"draft {blueprint_id!r} not found")
            # Recovery after a crash between draft unlink and unpublished
            # catalog unlink: the second call finishes catalog cleanup.
            if entry.latest_version is None:
                self._unlink(self._catalog_path(blueprint_id))

    # -- immutable versions ------------------------------------------------------

    def publish(
        self,
        blueprint_id: str,
        *,
        expected_revision: int,
        now: Optional[float] = None,
    ) -> BlueprintVersion:
        blueprint_id = self._id(blueprint_id)
        expected_revision = self._expected_revision(expected_revision)
        with self._lock(blueprint_id):
            self._require_active(self.get_catalog_entry(blueprint_id))
            intent_path = self._intent_path(blueprint_id)
            if intent_path.exists():
                intent = self._read(
                    intent_path,
                    BlueprintPublishIntent,
                    expected_id=blueprint_id,
                )
                if intent.expected_revision != expected_revision:
                    raise BlueprintStoreError(
                        f"publish intent revision mismatch for {blueprint_id!r}"
                    )
            else:
                draft = self.get_draft(blueprint_id)
                if draft.revision != expected_revision:
                    raise RevisionConflictError(
                        blueprint_id, expected_revision, draft.revision
                    )
                entry = self.get_catalog_entry(blueprint_id)
                published = BlueprintVersion(
                    **self._content_fields(draft),
                    id=blueprint_id,
                    version=(entry.latest_version or 0) + 1,
                    published_at=time.time() if now is None else now,
                )
                intent = BlueprintPublishIntent(
                    id=blueprint_id,
                    expected_revision=expected_revision,
                    version=published,
                )
                # Intent is the first durable transaction mutation. Every later
                # retry completes exactly this normalized snapshot and target.
                self._atomic_create(intent_path, intent)
            return self._complete_publish_intent(intent)

    def _complete_publish_intent(
        self, intent: BlueprintPublishIntent
    ) -> BlueprintVersion:
        blueprint_id = intent.id
        published = intent.version
        if published.id != blueprint_id:
            raise BlueprintCorruptionError("publish intent/version identity mismatch")

        catalog = self.get_catalog_entry(blueprint_id)
        target = published.version
        before = target - 1
        if catalog.latest_version not in (before or None, target):
            raise BlueprintStoreError(
                f"catalog version mismatch for publish intent {blueprint_id!r} "
                f"v{target}: found {catalog.latest_version!r}"
            )

        draft_path = self._safe_existing(self._draft_path(blueprint_id))
        if draft_path.exists():
            draft = self._read(
                draft_path, BlueprintDraft, expected_id=blueprint_id
            )
            if draft.revision != intent.expected_revision:
                raise BlueprintStoreError(
                    f"draft revision does not match publish intent for {blueprint_id!r}"
                )
            intended_from_draft = BlueprintVersion(
                **self._content_fields(draft),
                id=blueprint_id,
                version=target,
                published_at=published.published_at,
            )
            if (
                intended_from_draft.model_dump(mode="json")
                != published.model_dump(mode="json")
            ):
                raise BlueprintStoreError(
                    f"draft content does not match publish intent for {blueprint_id!r}"
                )

        version_path = self._version_path(blueprint_id, target)
        if version_path.exists():
            self._matching_existing_version(
                version_path,
                blueprint_id=blueprint_id,
                version=target,
                intended=published,
            )
        else:
            self._atomic_create(version_path, published)

        if catalog.latest_version != target:
            catalog.latest_version = target
            self._atomic_replace(self._catalog_path(blueprint_id), catalog)
        if draft_path.exists():
            self._unlink(draft_path)
        self._unlink(self._intent_path(blueprint_id))
        return published

    def get_version(self, blueprint_id: str, version: int) -> BlueprintVersion:
        ref = ArtifactRef(id=blueprint_id, version=version)
        return self._read(
            self._version_path(ref.id, ref.version),
            BlueprintVersion,
            expected_id=ref.id,
            expected_version=ref.version,
        )

    def get_active_version(self, ref: ArtifactRef) -> BlueprintVersion:
        """Capture an exact owned snapshot only while its catalog is active.

        The active check and immutable read share the source artifact lock, so
        archive and run/fork intake have a single linearization order.
        """
        with self._lock(ref.id):
            self._require_active(self.get_catalog_entry(ref.id))
            return self.get_version(ref.id, ref.version)

    def list_versions(self, blueprint_id: str) -> list[BlueprintVersion]:
        blueprint_id = self._id(blueprint_id)
        directory = self._safe_existing(
            self.versions_root / blueprint_id / "versions"
        )
        if not directory.exists():
            return []
        versions: list[BlueprintVersion] = []
        for path in directory.glob("*.json"):
            try:
                number = int(path.stem)
                ArtifactRef(id=blueprint_id, version=number)
            except (TypeError, ValueError):
                continue
            versions.append(
                self._read(
                    path,
                    BlueprintVersion,
                    expected_id=blueprint_id,
                    expected_version=number,
                )
            )
        return sorted(versions, key=lambda item: item.version)

    def fork(
        self,
        source: ArtifactRef,
        new_id: str,
        *,
        owner: str,
        display_name: Optional[str] = None,
        now: Optional[float] = None,
    ) -> BlueprintDraft:
        original = self.get_version(source.id, source.version)
        content_data = self._content_fields(original)
        content_data["source"] = source
        if display_name is not None:
            content_data["name"] = display_name
        content = BlueprintContent.model_validate(content_data)
        return self._create_draft(
            new_id,
            content,
            owner=owner,
            now=now,
            allow_source=True,
            require_new_catalog=True,
        )

    def fork_snapshot(
        self,
        source: BlueprintVersion,
        new_id: str,
        *,
        owner: str,
        display_name: Optional[str] = None,
        now: Optional[float] = None,
    ) -> BlueprintDraft:
        """Fork an exact snapshot resolved by a trusted external catalog.

        The snapshot's own exact reference becomes machine-owned lineage.  The
        normal draft creation path supplies locking, path validation, and the
        requirement that the target catalog identity is new.
        """
        source = BlueprintVersion.model_validate(source.model_dump(mode="json"))
        content_data = self._content_fields(source)
        content_data["source"] = source.ref
        if display_name is not None:
            content_data["name"] = display_name
        content = BlueprintContent.model_validate(content_data)
        return self._create_draft(
            new_id,
            content,
            owner=owner,
            now=now,
            allow_source=True,
            require_new_catalog=True,
        )
