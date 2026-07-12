"""Crash-recoverable storage for draft and immutable evaluation suites."""
from __future__ import annotations

import fcntl
import json
import os
import re
import tempfile
import time
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Iterator, Literal, Optional

from metaharness.blueprints.models import ArtifactRef, _validate_slug
from metaharness.evals.models import (
    EvalCaseProposal,
    EvalSuiteCatalogEntry,
    EvalSuiteContent,
    EvalSuiteDraft,
    EvalSuitePublic,
    EvalSuitePublishIntent,
    EvalSuiteVersion,
)


class EvalSuiteStoreError(RuntimeError):
    pass


class EvalSuiteNotFoundError(EvalSuiteStoreError):
    pass


class EvalSuiteAlreadyExistsError(EvalSuiteStoreError):
    pass


class EvalSuiteCorruptionError(EvalSuiteStoreError):
    pass


class EvalSuiteRevisionConflictError(EvalSuiteStoreError):
    def __init__(self, suite_id: str, expected: int, actual: int) -> None:
        self.suite_id = suite_id
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"eval suite draft {suite_id!r} revision conflict: "
            f"expected {expected}, actual {actual}"
        )


class InvalidEvalSuiteRevisionError(EvalSuiteStoreError):
    def __init__(self, value: object) -> None:
        self.value = value
        super().__init__(
            "expected_revision must be a positive integer "
            "(bool, string, and float are invalid)"
        )


class EvalSuiteArchivedError(EvalSuiteStoreError):
    pass


class EvalSuiteStore:
    """Separate catalog/draft/intent/version/proposal trees under a trusted root."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.catalog_root = self.root / "eval-suite-catalog"
        self.drafts_root = self.root / "eval-suite-drafts"
        self.intents_root = self.root / "eval-suite-publish-intents"
        self.versions_root = self.root / "eval-suites"
        self.proposals_root = self.root / "eval-case-proposals"
        self.locks_root = self.root / ".eval-suite-locks"

    @staticmethod
    def _expected_revision(value: object) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise InvalidEvalSuiteRevisionError(value)
        return value

    def _id(self, value: str) -> str:
        return _validate_slug(value, label="eval suite id")

    def _catalog_path(self, suite_id: str) -> Path:
        return self.catalog_root / f"{self._id(suite_id)}.json"

    def _draft_path(self, suite_id: str) -> Path:
        return self.drafts_root / f"{self._id(suite_id)}.json"

    def _intent_path(self, suite_id: str) -> Path:
        return self.intents_root / f"{self._id(suite_id)}.json"

    def _version_path(self, suite_id: str, version: int) -> Path:
        ref = ArtifactRef(id=suite_id, version=version)
        return self.versions_root / ref.id / "versions" / f"{ref.version}.json"

    def _proposal_path(self, proposal_id: str) -> Path:
        return self.proposals_root / f"{_validate_slug(proposal_id)}.json"

    def _safe_existing(self, path: Path) -> Path:
        resolved = path.resolve(strict=False)
        try:
            resolved.relative_to(self.root)
            relative = path.relative_to(self.root)
        except ValueError as exc:
            raise EvalSuiteStoreError(f"eval suite path escapes storage root: {path}") from exc
        cursor = self.root
        for part in relative.parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise EvalSuiteStoreError(f"eval suite path uses a symlink: {cursor}")
        return path

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
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
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

    @contextmanager
    def _lock(self, suite_id: str) -> Iterator[None]:
        suite_id = self._id(suite_id)
        self._ensure_directory(self.locks_root)
        path = self._safe_existing(self.locks_root / f"{suite_id}.lock")
        existed = path.exists()
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        if not existed:
            self._fsync_directory(path.parent)
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
            raise EvalSuiteNotFoundError(f"eval suite record not found: {path.name}") from exc
        except Exception as exc:
            raise EvalSuiteCorruptionError(
                f"invalid {model_type.__name__} record at {path}"
            ) from exc
        if expected_id is not None and getattr(value, "id", None) != expected_id:
            raise EvalSuiteCorruptionError(
                f"record identity mismatch at {path}: expected {expected_id!r}"
            )
        if expected_version is not None and getattr(value, "version", None) != expected_version:
            raise EvalSuiteCorruptionError(
                f"record version mismatch at {path}: expected {expected_version}"
            )
        return value

    @staticmethod
    def _payload(model) -> str:
        return json.dumps(
            model.model_dump(mode="json"),
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
        )

    def _atomic_replace(self, path: Path, model) -> None:
        self._ensure_directory(path.parent)
        self._safe_existing(path)
        fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(self._payload(model))
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
        self._ensure_directory(path.parent)
        self._safe_existing(path)
        fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(self._payload(model))
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(tmp_path, path)
                self._fsync_directory(path.parent)
            except FileExistsError as exc:
                raise EvalSuiteAlreadyExistsError(
                    f"immutable eval suite record already exists: {path}"
                ) from exc
        finally:
            with suppress(OSError):
                self._unlink(tmp_path, missing_ok=True)

    @staticmethod
    def _content_fields(value: EvalSuiteContent) -> dict:
        dumped = value.model_dump(mode="python")
        return {name: dumped[name] for name in EvalSuiteContent.model_fields}

    @staticmethod
    def _require_active(entry: EvalSuiteCatalogEntry) -> None:
        if entry.archived_at is not None:
            raise EvalSuiteArchivedError(
                f"eval suite {entry.id!r} is archived; restore it before changing it"
            )

    # Catalog
    def list(self, *, include_archived: bool = False) -> list[EvalSuiteCatalogEntry]:
        if not self.catalog_root.exists():
            return []
        entries = []
        for path in sorted(self.catalog_root.glob("*.json")):
            try:
                expected_id = self._id(path.stem)
            except ValueError as exc:
                raise EvalSuiteCorruptionError(f"invalid catalog path identity: {path}") from exc
            entries.append(self._read(path, EvalSuiteCatalogEntry, expected_id=expected_id))
        return entries if include_archived else [e for e in entries if e.archived_at is None]

    def get_catalog_entry(self, suite_id: str) -> EvalSuiteCatalogEntry:
        suite_id = self._id(suite_id)
        return self._read(
            self._catalog_path(suite_id), EvalSuiteCatalogEntry, expected_id=suite_id
        )

    def archive(self, suite_id: str, *, at: Optional[float] = None) -> EvalSuiteCatalogEntry:
        with self._lock(suite_id):
            entry = self.get_catalog_entry(suite_id)
            if entry.latest_version is None:
                raise EvalSuiteStoreError("cannot archive an unpublished eval suite")
            entry.archived_at = time.time() if at is None else at
            self._atomic_replace(self._catalog_path(suite_id), entry)
            return entry

    def restore(self, suite_id: str) -> EvalSuiteCatalogEntry:
        with self._lock(suite_id):
            entry = self.get_catalog_entry(suite_id)
            entry.archived_at = None
            self._atomic_replace(self._catalog_path(suite_id), entry)
            return entry

    # Drafts
    def create_draft(
        self,
        suite_id: str,
        content: EvalSuiteContent,
        *,
        owner: str,
        base_version: Optional[int] = None,
        now: Optional[float] = None,
    ) -> EvalSuiteDraft:
        suite_id = self._id(suite_id)
        timestamp = time.time() if now is None else now
        draft = EvalSuiteDraft(
            **self._content_fields(content),
            id=suite_id,
            revision=1,
            base_version=base_version,
            owner=owner,
            created_at=timestamp,
            updated_at=timestamp,
        )
        with self._lock(suite_id):
            if self._intent_path(suite_id).exists():
                raise EvalSuiteStoreError("cannot create a draft while publish is pending")
            catalog_path = self._catalog_path(suite_id)
            if self._draft_path(suite_id).exists():
                raise EvalSuiteAlreadyExistsError(f"draft {suite_id!r} already exists")
            if catalog_path.exists():
                entry = self.get_catalog_entry(suite_id)
                self._require_active(entry)
                if base_version is not None:
                    self.get_version_for_evaluation(suite_id, base_version)
            else:
                if base_version is not None:
                    raise EvalSuiteNotFoundError("new eval suite cannot have a base version")
                entry = EvalSuiteCatalogEntry(
                    id=suite_id, display_name=content.name, latest_version=None
                )
                self._atomic_create(catalog_path, entry)
            self._atomic_create(self._draft_path(suite_id), draft)
            return draft

    def create_draft_from_version(
        self, ref: ArtifactRef, *, owner: str, now: Optional[float] = None
    ) -> EvalSuiteDraft:
        version = self.get_version_for_evaluation(ref.id, ref.version)
        content = EvalSuiteContent.model_validate(self._content_fields(version))
        return self.create_draft(
            ref.id, content, owner=owner, base_version=ref.version, now=now
        )

    def get_draft(self, suite_id: str) -> EvalSuiteDraft:
        suite_id = self._id(suite_id)
        return self._read(self._draft_path(suite_id), EvalSuiteDraft, expected_id=suite_id)

    def update_draft(
        self,
        suite_id: str,
        content: EvalSuiteContent,
        *,
        expected_revision: int,
        now: Optional[float] = None,
    ) -> EvalSuiteDraft:
        suite_id = self._id(suite_id)
        expected_revision = self._expected_revision(expected_revision)
        with self._lock(suite_id):
            if self._intent_path(suite_id).exists():
                raise EvalSuiteStoreError("cannot update a draft while publish is pending")
            self._require_active(self.get_catalog_entry(suite_id))
            current = self.get_draft(suite_id)
            if current.revision != expected_revision:
                raise EvalSuiteRevisionConflictError(
                    suite_id, expected_revision, current.revision
                )
            updated = EvalSuiteDraft(
                **self._content_fields(content),
                id=current.id,
                revision=current.revision + 1,
                base_version=current.base_version,
                owner=current.owner,
                created_at=current.created_at,
                updated_at=time.time() if now is None else now,
            )
            self._atomic_replace(self._draft_path(suite_id), updated)
            return updated

    def delete_draft(self, suite_id: str) -> None:
        suite_id = self._id(suite_id)
        with self._lock(suite_id):
            entry = self.get_catalog_entry(suite_id)
            self._require_active(entry)
            if self._intent_path(suite_id).exists():
                raise EvalSuiteStoreError("cannot delete a draft while publish is pending")
            path = self._draft_path(suite_id)
            if path.exists():
                self._read(path, EvalSuiteDraft, expected_id=suite_id)
                self._unlink(path)
            elif entry.latest_version is not None:
                raise EvalSuiteNotFoundError(f"draft {suite_id!r} not found")
            if entry.latest_version is None:
                self._unlink(self._catalog_path(suite_id))

    # Immutable versions
    def publish(
        self,
        suite_id: str,
        *,
        expected_revision: int,
        now: Optional[float] = None,
    ) -> EvalSuitePublic:
        suite_id = self._id(suite_id)
        expected_revision = self._expected_revision(expected_revision)
        with self._lock(suite_id):
            self._require_active(self.get_catalog_entry(suite_id))
            intent_path = self._intent_path(suite_id)
            if intent_path.exists():
                intent = self._read(
                    intent_path, EvalSuitePublishIntent, expected_id=suite_id
                )
                if intent.expected_revision != expected_revision:
                    raise EvalSuiteStoreError("publish intent revision mismatch")
            else:
                draft = self.get_draft(suite_id)
                if draft.revision != expected_revision:
                    raise EvalSuiteRevisionConflictError(
                        suite_id, expected_revision, draft.revision
                    )
                entry = self.get_catalog_entry(suite_id)
                version = EvalSuiteVersion(
                    **self._content_fields(draft),
                    id=suite_id,
                    version=(entry.latest_version or 0) + 1,
                    created_at=time.time() if now is None else now,
                )
                intent = EvalSuitePublishIntent(
                    id=suite_id, expected_revision=expected_revision, version=version
                )
                self._atomic_create(intent_path, intent)
            return self._complete_publish_intent(intent).public()

    def _complete_publish_intent(self, intent: EvalSuitePublishIntent) -> EvalSuiteVersion:
        suite_id = intent.id
        published = intent.version
        catalog = self.get_catalog_entry(suite_id)
        target = published.version
        before = target - 1
        if catalog.latest_version not in (before or None, target):
            raise EvalSuiteStoreError("catalog version does not match publish intent")
        draft_path = self._draft_path(suite_id)
        if draft_path.exists():
            draft = self._read(draft_path, EvalSuiteDraft, expected_id=suite_id)
            if draft.revision != intent.expected_revision:
                raise EvalSuiteStoreError("draft revision does not match publish intent")
            expected = EvalSuiteVersion(
                **self._content_fields(draft),
                id=suite_id,
                version=target,
                created_at=published.created_at,
            )
            if expected.model_dump(mode="json") != published.model_dump(mode="json"):
                raise EvalSuiteStoreError("draft content does not match publish intent")
        path = self._version_path(suite_id, target)
        if path.exists():
            existing = self._read(
                path,
                EvalSuiteVersion,
                expected_id=suite_id,
                expected_version=target,
            )
            if existing.model_dump(mode="json") != published.model_dump(mode="json"):
                raise EvalSuiteStoreError("immutable version conflicts with publish intent")
        else:
            self._atomic_create(path, published)
        if catalog.latest_version != target:
            catalog.latest_version = target
            self._atomic_replace(self._catalog_path(suite_id), catalog)
        if draft_path.exists():
            self._unlink(draft_path)
        self._unlink(self._intent_path(suite_id))
        return published

    def get_version(self, suite_id: str, version: int) -> EvalSuitePublic:
        """Return the safe public projection; it contains no holdout case data."""
        return self.get_version_for_evaluation(suite_id, version).public()

    def get_version_for_evaluation(self, suite_id: str, version: int) -> EvalSuiteVersion:
        """Trusted evaluator-only accessor returning sealed holdout cases."""
        ref = ArtifactRef(id=suite_id, version=version)
        return self._read(
            self._version_path(ref.id, ref.version),
            EvalSuiteVersion,
            expected_id=ref.id,
            expected_version=ref.version,
        )

    def list_versions(self, suite_id: str) -> list[EvalSuitePublic]:
        suite_id = self._id(suite_id)
        directory = self._safe_existing(self.versions_root / suite_id / "versions")
        if not directory.exists():
            return []
        versions: list[EvalSuitePublic] = []
        for path in sorted(directory.iterdir()):
            if re.fullmatch(r"\.[1-9][0-9]*\.json\.[A-Za-z0-9_-]+\.tmp", path.name):
                # A hard crash can strand only this mkstemp shape.  It is not a
                # version and is safe to clean; every other unexpected entry is
                # surfaced as corruption below.
                self._unlink(path)
                continue
            if (
                not path.is_file()
                or path.suffix != ".json"
                or not path.stem.isascii()
                or not path.stem.isdigit()
                or path.stem.startswith("0")
            ):
                raise EvalSuiteCorruptionError(
                    f"noncanonical eval suite version filename: {path.name}"
                )
            number = int(path.stem)
            try:
                ArtifactRef(id=suite_id, version=number)
            except (TypeError, ValueError) as exc:
                raise EvalSuiteCorruptionError(
                    f"invalid eval suite version filename: {path.name}"
                ) from exc
            versions.append(self.get_version(suite_id, number))
        return sorted(versions, key=lambda item: item.version)

    # Proposals remain a separate inert artifact family.
    def create_proposal(self, proposal: EvalCaseProposal) -> EvalCaseProposal:
        # Callers may have mutated nested Pydantic objects after initial
        # construction.  Revalidation is the final intake boundary before any
        # bytes become durable.
        validated = EvalCaseProposal.model_validate(
            proposal.model_dump(mode="python")
        )
        self._atomic_create(self._proposal_path(validated.id), validated)
        return validated

    def get_proposal(self, proposal_id: str) -> EvalCaseProposal:
        proposal_id = _validate_slug(proposal_id)
        return self._read(
            self._proposal_path(proposal_id), EvalCaseProposal, expected_id=proposal_id
        )

    def list_proposals(self) -> list[EvalCaseProposal]:
        if not self.proposals_root.exists():
            return []
        proposals = []
        for path in sorted(self.proposals_root.glob("*.json")):
            try:
                expected_id = _validate_slug(path.stem)
            except ValueError as exc:
                raise EvalSuiteCorruptionError(f"invalid proposal path identity: {path}") from exc
            proposals.append(
                self._read(path, EvalCaseProposal, expected_id=expected_id)
            )
        return proposals

    def set_proposal_status(
        self,
        proposal_id: str,
        status: Literal["accepted", "rejected"],
        *,
        now: Optional[float] = None,
    ) -> EvalCaseProposal:
        proposal = self.get_proposal(proposal_id)
        updated = proposal.model_copy(
            update={"status": status, "updated_at": time.time() if now is None else now}
        )
        updated = EvalCaseProposal.model_validate(updated.model_dump(mode="python"))
        self._atomic_replace(self._proposal_path(proposal_id), updated)
        return updated
