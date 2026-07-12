"""Immutable, versioned Harness Blueprint seeds shipped with meta-harness.

Built-ins are deliberately materialized as complete ``BlueprintVersion``
snapshots.  They are not inserted into a user's ``BlueprintStore`` and callers
always receive a fresh model, so an in-process edit cannot mutate the catalog.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from typing import Final

from metaharness.blueprints.models import BlueprintVersion, InputSpec
from metaharness.workflows.templates import RESEARCH, SOFTWARE_ENGINEERING, WorkflowTemplate


def canonical_blueprint_bytes(version: BlueprintVersion) -> bytes:
    """Return the stable bytes used by built-in golden digest guards."""
    payload = version.model_dump(mode="json", by_alias=True)
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def blueprint_digest(version: BlueprintVersion) -> str:
    return hashlib.sha256(canonical_blueprint_bytes(version)).hexdigest()


def _seed(
    *, blueprint_id: str, name: str, template: WorkflowTemplate
) -> BlueprintVersion:
    # The immutable seed holds context references. Run intake materializes them
    # through resolve_blueprint_workflow(), producing a goal-specific WorkflowSpec
    # and digest without mutating or republishing this authored v1 snapshot.
    workflow = template.instantiate("$context.goal")
    workflow.name = blueprint_id
    return BlueprintVersion(
        id=blueprint_id,
        version=1,
        published_at=0.0,
        name=name,
        description=template.description,
        workflow=workflow,
        inputs=[
            InputSpec(
                name="goal",
                schema={"type": "string", "minLength": 1},
                required=True,
            )
        ],
    )


_SEEDS = (
    _seed(
        blueprint_id="software-engineering",
        name="Software engineering",
        template=SOFTWARE_ENGINEERING,
    ),
    _seed(blueprint_id="research", name="Research & report", template=RESEARCH),
)


class DuplicateBuiltinRefError(ValueError):
    pass


class BuiltinBlueprintRegistry:
    """Append-only-shaped, exact-reference registry for shipped snapshots."""

    def __init__(self, versions: Iterable[BlueprintVersion]) -> None:
        records: dict[tuple[str, int], str] = {}
        for version in versions:
            key = (version.id, version.version)
            if key in records:
                raise DuplicateBuiltinRefError(
                    f"duplicate built-in blueprint ref: {version.id}@{version.version}"
                )
            records[key] = canonical_blueprint_bytes(version).decode("utf-8")
        self._records = records

    def ids(self) -> tuple[str, ...]:
        return tuple(sorted({blueprint_id for blueprint_id, _ in self._records}))

    def get(self, blueprint_id: str, version: int) -> BlueprintVersion | None:
        raw = self._records.get((blueprint_id, version))
        return BlueprintVersion.model_validate_json(raw) if raw is not None else None

    def versions(self, blueprint_id: str) -> list[BlueprintVersion]:
        return [
            BlueprintVersion.model_validate_json(raw)
            for (item_id, _), raw in sorted(
                self._records.items(), key=lambda item: item[0][1]
            )
            if item_id == blueprint_id
        ]

    def latest(self, blueprint_id: str) -> BlueprintVersion | None:
        versions = self.versions(blueprint_id)
        return versions[-1] if versions else None

    def all_versions(self) -> list[BlueprintVersion]:
        return [
            BlueprintVersion.model_validate_json(raw)
            for _, raw in sorted(self._records.items())
        ]


# Serialized once at import and parsed on every read. This prevents Pydantic's
# mutable models from becoming shared catalog state, while the exact tuple key
# keeps old versions addressable after a later version ships.
BUILTIN_REGISTRY: Final = BuiltinBlueprintRegistry(_SEEDS)

# These literals are intentionally updated only with a built-in version bump.
# Tests compare the calculated digest to this map, making accidental seed drift
# visible in review.
BUILTIN_GOLDEN_DIGESTS: Final[dict[str, str]] = {
    "software-engineering@1": "4ef3b2a6a33fddb4a4d108d072c39397fdcc255fb82253b4a18dc0e5ee583fd7",
    "research@1": "fd0df52d173131e5ccdadec3d47ea1eb2e623bc01231525c4407fe119242914d",
}


def list_builtin_versions() -> list[BlueprintVersion]:
    return BUILTIN_REGISTRY.all_versions()


def list_latest_builtin_versions() -> list[BlueprintVersion]:
    return [
        latest
        for blueprint_id in BUILTIN_REGISTRY.ids()
        if (latest := BUILTIN_REGISTRY.latest(blueprint_id)) is not None
    ]


def get_builtin_version(blueprint_id: str, version: int) -> BlueprintVersion | None:
    return BUILTIN_REGISTRY.get(blueprint_id, version)


def get_latest_builtin_version(blueprint_id: str) -> BlueprintVersion | None:
    return BUILTIN_REGISTRY.latest(blueprint_id)


def is_builtin_id(blueprint_id: str) -> bool:
    return blueprint_id in BUILTIN_REGISTRY.ids()


def builtin_digests() -> dict[str, str]:
    return {
        f"{item.id}@{item.version}": blueprint_digest(item)
        for item in list_builtin_versions()
    }
