"""Crash-safe immutable persistence for eval reports and tuning proposals."""
from __future__ import annotations

import json
import os
import re
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Generic, TypeVar

from pydantic import BaseModel

from metaharness.blueprints.models import _validate_slug
from metaharness.evals.artifacts import EvaluationReport, TuningProposal


class EvalArtifactStoreError(RuntimeError):
    pass


class EvalArtifactNotFoundError(EvalArtifactStoreError):
    pass


class EvalArtifactAlreadyExistsError(EvalArtifactStoreError):
    pass


class EvalArtifactCorruptionError(EvalArtifactStoreError):
    pass


T = TypeVar("T", bound=BaseModel)


class _ImmutableModelStore(Generic[T]):
    def __init__(self, root: str | Path, directory: str, model_type: type[T]) -> None:
        self.root = Path(root).expanduser().resolve()
        self.directory = self.root / directory
        self.model_type = model_type

    def _path(self, artifact_id: str) -> Path:
        return self.directory / f"{_validate_slug(artifact_id)}.json"

    def _safe(self, path: Path) -> Path:
        try:
            relative = path.relative_to(self.root)
        except ValueError as exc:
            raise EvalArtifactStoreError("eval artifact path escapes storage root") from exc
        cursor = self.root
        for part in relative.parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise EvalArtifactStoreError(f"eval artifact path uses symlink: {cursor}")
        return path

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _ensure_directory(self) -> None:
        self._safe(self.directory)
        missing: list[Path] = []
        cursor = self.directory
        while cursor != self.root and not cursor.exists():
            missing.append(cursor)
            cursor = cursor.parent
        self.directory.mkdir(parents=True, exist_ok=True)
        for created in reversed(missing):
            self._fsync_directory(created.parent)

    def create(self, value: T) -> T:
        validated = self.model_type.model_validate(value.model_dump(mode="python"))
        target = self._safe(self._path(validated.id))
        self._ensure_directory()
        payload = json.dumps(
            validated.model_dump(mode="json"),
            sort_keys=True,
            indent=2,
            ensure_ascii=False,
            allow_nan=False,
        )
        fd, temporary_name = tempfile.mkstemp(
            dir=self.directory, prefix=f".{target.name}.", suffix=".tmp"
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temporary, target)
            except FileExistsError as exc:
                raise EvalArtifactAlreadyExistsError(
                    f"immutable eval artifact already exists: {validated.id}"
                ) from exc
            self._fsync_directory(self.directory)
        finally:
            with suppress(FileNotFoundError):
                temporary.unlink()
        return validated

    def get(self, artifact_id: str) -> T:
        artifact_id = _validate_slug(artifact_id)
        path = self._safe(self._path(artifact_id))
        try:
            if not path.is_file():
                raise EvalArtifactNotFoundError(
                    f"eval artifact not found: {artifact_id}"
                )
            value = self.model_type.model_validate_json(path.read_text(encoding="utf-8"))
        except EvalArtifactNotFoundError:
            raise
        except Exception as exc:
            raise EvalArtifactCorruptionError(
                f"invalid {self.model_type.__name__} record: {artifact_id}"
            ) from exc
        if value.id != artifact_id:
            raise EvalArtifactCorruptionError(
                f"eval artifact identity mismatch: {artifact_id}"
            )
        return value

    def list(self) -> list[T]:
        if not self.directory.exists():
            return []
        self._safe(self.directory)
        values: list[T] = []
        for path in sorted(self.directory.iterdir()):
            if re.fullmatch(
                r"\.[a-z0-9]+(?:-[a-z0-9]+)*\.json\.[A-Za-z0-9_-]+\.tmp",
                path.name,
            ):
                # A hard crash may strand only this mkstemp shape. It is never
                # an artifact and removing it restores a clean immutable index.
                if path.is_symlink() or not path.is_file():
                    raise EvalArtifactCorruptionError(
                        f"unsafe temporary eval artifact entry: {path.name}"
                    )
                path.unlink()
                self._fsync_directory(self.directory)
                continue
            if not path.is_file() or path.suffix != ".json":
                raise EvalArtifactCorruptionError(
                    f"unexpected eval artifact entry: {path.name}"
                )
            try:
                artifact_id = _validate_slug(path.stem)
            except ValueError as exc:
                raise EvalArtifactCorruptionError(
                    f"invalid eval artifact filename: {path.name}"
                ) from exc
            values.append(self.get(artifact_id))
        return values


class EvaluationReportStore(_ImmutableModelStore[EvaluationReport]):
    def __init__(self, root: str | Path) -> None:
        super().__init__(root, "evaluation-reports", EvaluationReport)


class TuningProposalStore(_ImmutableModelStore[TuningProposal]):
    """An append-only store. It intentionally exposes no update/promote method."""

    def __init__(self, root: str | Path) -> None:
        super().__init__(root, "tuning-proposals", TuningProposal)
