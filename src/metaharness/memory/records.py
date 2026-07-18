"""Frozen memory records, activation/lifecycle enums, and mutation receipts.

MemoryRecord is the durable unit: append-only, frozen, hash-self-verifying.
ActivationState and LifecycleState are independent axes — lifecycle tracks
candidate/active/superseded/.../tombstoned (progression); activation tracks
active/dormant/tombstoned (retrieval visibility). Tombstone() returns a NEW
record (evidence-preserving) instead of mutating in place; that satisfies
META5-MEM-007 without ever destroying evidence.
"""
from __future__ import annotations

import itertools
from enum import Enum
from typing import Any, Literal

from pydantic import Field, model_validator

from metaharness.context import ContextScope, Sensitivity
from metaharness.context.models import (
    SHA256_PATTERN,
    FrozenModel,
    canonical_json,
    content_hash,
)


class MemoryKind(str, Enum):
    """The four closed memory kinds, aligned string-for-string with
    metaharness.context.ContextSourceKind values for the matching kinds."""

    WORKING_MEMORY = "working_memory"
    EPISODIC_MEMORY = "episodic_memory"
    SEMANTIC_MEMORY = "semantic_memory"
    PROCEDURAL_MEMORY = "procedural_memory"


class ActivationState(str, Enum):
    """Retrieval-visibility axis. ACTIVE is selectable; DORMANT is hidden but
    not destroyed; TOMBSTONED is hidden AND tagged as evidence-preserving for
    forensic / audit recovery."""

    ACTIVE = "active"
    DORMANT = "dormant"
    TOMBSTONED = "tombstoned"


class LifecycleState(str, Enum):
    """Promotion axis: candidate -> active -> {superseded, rejected, expired,
    tombstoned}. A record can be ACTIVE+ACTIVE, ACTIVE+TOMBSTONED, etc.; the
    two axes are orthogonal so the same record can be in active lifecycle but
    tombstoned activation (frozen evidence)."""

    CANDIDATE = "candidate"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"
    EXPIRED = "expired"
    TOMBSTONED = "tombstoned"


_id_counter = itertools.count(0)


def _generate_record_id() -> str:
    """Deterministic per-process record id; no wall-clock, no randomness.
    The store overrides this when it owns the id namespace."""

    return f"mem-{next(_id_counter):08x}"


def normalize_text(value: str) -> str:
    """Lowercase, collapse whitespace, strip punctuation-only edges. Stable
    canonical form so the FTS5 lexical index in MemoryStore can search."""

    return " ".join(value.lower().split())


class MemoryRecord(FrozenModel):
    """Append-only memory unit. Frozen + extra='forbid' so attempts to mutate
    in place raise — durable rewrite is impossible; only supersede / tombstone
    are allowed (both produce a NEW record)."""

    schema_version: Literal[1] = 1
    id: str = Field(default_factory=_generate_record_id)
    kind: MemoryKind
    scope: ContextScope = Field(default_factory=lambda: ContextScope(project_id="meta-harness"))
    content: str = Field(min_length=1)
    normalized_content: str = Field(default="")
    source_refs: tuple[str, ...] = ()
    observed_at: int = Field(default=0, ge=0)
    valid_from: int | None = None
    valid_until: int | None = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    lifecycle_state: LifecycleState = LifecycleState.ACTIVE
    activation_state: ActivationState = ActivationState.ACTIVE
    supersedes: tuple[str, ...] = ()
    superseded_by: str | None = None
    sensitivity: Sensitivity = Sensitivity.INTERNAL
    creator_id: str = "anonymous"
    usage_count: int = Field(default=0, ge=0)
    last_accessed_at: int | None = None
    creation_seq: int = Field(default=0, ge=0)
    tombstone_reason: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _derive_normalized_content(cls, data: Any) -> Any:
        """Fill in normalized_content from content when not provided. Called
        before field-level validation so a record that omits
        normalized_content (e.g. constructed directly in tests) is still
        indexed by the FTS5 lexical index."""

        if isinstance(data, dict):
            content = data.get("content", "")
            nc = data.get("normalized_content")
            if not nc and content:
                data["normalized_content"] = normalize_text(content)
        return data

    def tombstone(self, *, reason: str) -> "MemoryRecord":
        """Return a NEW record with activation_state=TOMBSTONED and
        lifecycle_state=TOMBSTONED. Content is preserved (evidence, not
        destruction). The original record is unchanged (frozen)."""

        return MemoryRecord(
            id=f"{self.id}-tombstone",
            kind=self.kind,
            scope=self.scope,
            content=self.content,
            normalized_content=self.normalized_content,
            source_refs=self.source_refs,
            observed_at=self.observed_at,
            valid_from=self.valid_from,
            valid_until=self.valid_until,
            confidence=self.confidence,
            lifecycle_state=LifecycleState.TOMBSTONED,
            activation_state=ActivationState.TOMBSTONED,
            supersedes=self.supersedes,
            superseded_by=self.superseded_by,
            sensitivity=self.sensitivity,
            creator_id=self.creator_id,
            usage_count=self.usage_count,
            last_accessed_at=self.last_accessed_at,
            creation_seq=self.creation_seq + 1,
            tombstone_reason=reason,
        )


class MemoryMutationReceipt(FrozenModel):
    """Receipt analogous to context.CompressionReceipt: binds a mutation to
    before/after content hashes + lifecycle transition + actor. Self-verifying
    via content_hash (recompute from exclude={'content_hash'}; raise on
    mismatch). META5-MEM-004: every receipted mutation emits one of these,
    persisted beside the superseding record."""

    schema_version: Literal[1] = 1
    mutation_id: str = Field(min_length=1)
    target_record_id: str = Field(min_length=1)
    supersede_record_id: str = Field(min_length=1)
    before_content_hash: str = Field(pattern=SHA256_PATTERN)
    after_content_hash: str = Field(pattern=SHA256_PATTERN)
    before_lifecycle: LifecycleState
    after_lifecycle: LifecycleState
    actor_id: str = Field(min_length=1)
    observed_at: int = Field(ge=0)
    mutation_reason: str = Field(default="receipted supersede", min_length=1)
    content_hash: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="wrap")
    @classmethod
    def _validate_content_hash(cls, data: Any, handler: Any) -> "MemoryMutationReceipt":
        """FIX-9: build the fully-defaulted model, then derive the content_hash
        from its model_dump. This matches the broker's _self_verifying_model
        pattern so omitting any defaulted field (mutation_reason) produces a
        valid construction instead of a hash mismatch.

        When the caller supplies an explicit content_hash, the post-construction
        recompute must agree, otherwise the value is rejected (tamper check).
        """

        if isinstance(data, dict) and "content_hash" in data and data["content_hash"]:
            model = handler(data)
            material = model.model_dump(mode="json", exclude={"content_hash"})
            if model.content_hash != content_hash(material):
                raise ValueError("memory mutation receipt content_hash mismatch")
            return model
        if isinstance(data, dict):
            data = {**data, "content_hash": "sha256:" + "0" * 64}
        model = handler(data)
        material = model.model_dump(mode="json", exclude={"content_hash"})
        object.__setattr__(model, "content_hash", content_hash(material))
        return model
