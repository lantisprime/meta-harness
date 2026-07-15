"""Frozen context, source, compression, and shadow-manifest contracts."""
from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SHA256_PATTERN = r"^sha256:[0-9a-f]{64}$"


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def content_hash(value: Any) -> str:
    material = value if isinstance(value, str) else canonical_json(value)
    return "sha256:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ContextSourceKind(str, Enum):
    PROTECTED_INSTRUCTIONS = "protected_instructions"
    GOAL = "goal"
    LIVE_RUN_STATE = "live_run_state"
    IMMUTABLE_ARTIFACT = "immutable_artifact"
    CANDIDATE_WORKTREE = "candidate_worktree"
    PARENT_LINEAGE = "parent_lineage"
    WORKING_MEMORY = "working_memory"
    EPISODIC_MEMORY = "episodic_memory"
    SEMANTIC_MEMORY = "semantic_memory"
    PROCEDURAL_MEMORY = "procedural_memory"
    POPULATION_FINDING = "population_finding"
    TOOL_POLICY_SCHEMA = "tool_policy_schema"
    EVALUATOR_RECEIPT = "evaluator_receipt"
    RESPONSE_CONTRACT = "response_contract"


class ContextTrust(str, Enum):
    INSTRUCTION = "instruction"
    VERIFIED_FACT = "verified_fact"
    UNTRUSTED_EVIDENCE = "untrusted_evidence"
    GENERATED_SUMMARY = "generated_summary"


class Sensitivity(str, Enum):
    PUBLIC = "public"
    INTERNAL = "internal"
    RESTRICTED = "restricted"
    SECRET = "secret"


class ContextSectionType(str, Enum):
    SYSTEM_INSTRUCTIONS = "system_instructions"
    TASK_CONTRACT = "task_contract"
    TOOL_SCHEMAS = "tool_schemas"
    WORKFLOW_STATE = "workflow_state"
    PRIOR_OUTPUTS = "prior_outputs"
    MEMORY = "memory"
    POPULATION_FINDINGS = "population_findings"
    VERIFIER_FEEDBACK = "verifier_feedback"
    RESPONSE_CONTRACT = "response_contract"


class CompressionAction(str, Enum):
    NONE = "none"
    HEAD_TAIL = "head_tail"
    STRUCTURED_SUMMARY = "structured_summary"
    ARTIFACT_REFERENCE = "artifact_reference"
    OMITTED = "omitted"


class ContextVersionBindings(FrozenModel):
    """The immutable evidence/lineage tuple for one context assembly."""

    model_portfolio_version: str = Field(min_length=1)
    harness_version: str = Field(min_length=1)
    evaluator_version: str = Field(min_length=1)
    weight_snapshot_version: str | None = Field(default=None, min_length=1)
    memory_snapshot_version: str | None = Field(default=None, min_length=1)
    evidence_snapshot_version: str = Field(min_length=1)
    candidate_version: str = Field(min_length=1)
    parent_candidate_version: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_lineage(self) -> "ContextVersionBindings":
        if self.parent_candidate_version == self.candidate_version:
            raise ValueError("candidate and parent versions must differ")
        return self


class ContextScope(FrozenModel):
    project_id: str = Field(min_length=1)
    run_id: str | None = Field(default=None, min_length=1)
    task_id: str | None = Field(default=None, min_length=1)
    attempt_id: str | None = Field(default=None, min_length=1)
    lineage_id: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_nesting(self) -> "ContextScope":
        if self.task_id is not None and self.run_id is None:
            raise ValueError("task_id requires run_id")
        if self.attempt_id is not None and self.task_id is None:
            raise ValueError("attempt_id requires task_id")
        return self


class ContextSourceRef(FrozenModel):
    schema_version: Literal[1] = 1
    source_id: str = Field(min_length=1)
    kind: ContextSourceKind
    scope: ContextScope
    trust: ContextTrust
    content_hash: str | None = Field(default=None, pattern=SHA256_PATTERN)
    high_water_mark: str | None = Field(default=None, min_length=1)
    selection_reason: str = Field(min_length=1)
    sensitivity: Sensitivity
    fetchable: bool
    artifact_ref: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_identity_and_fetch(self) -> "ContextSourceRef":
        if (self.content_hash is None) == (self.high_water_mark is None):
            raise ValueError("exactly one of content_hash or high_water_mark is required")
        if self.fetchable != (self.artifact_ref is not None):
            raise ValueError("fetchable sources require exactly one artifact_ref")
        return self


class ContextSection(FrozenModel):
    schema_version: Literal[1] = 1
    section_type: ContextSectionType
    stable_id: str = Field(min_length=1)
    source: ContextSourceRef
    source_hash: str = Field(pattern=SHA256_PATTERN)
    trust: ContextTrust
    content: str
    original_tokens: int = Field(ge=0)
    selected_tokens: int = Field(ge=0)
    compressed_tokens: int = Field(ge=0)
    budget_tokens: int = Field(ge=0)
    ordering_priority: int = Field(ge=0)
    sensitivity: Sensitivity
    redaction_markers: tuple[str, ...] = ()
    compression_action: CompressionAction = CompressionAction.NONE
    omission_reason: str | None = Field(default=None, min_length=1)
    retrieval_score: float | None = None

    @model_validator(mode="after")
    def validate_provenance_and_counts(self) -> "ContextSection":
        if self.source.content_hash is not None and self.source_hash != self.source.content_hash:
            raise ValueError("source_hash must match source.content_hash")
        if self.trust != self.source.trust:
            raise ValueError("section trust must match source trust")
        if self.sensitivity != self.source.sensitivity:
            raise ValueError("section sensitivity must match source sensitivity")
        if self.selected_tokens > self.original_tokens:
            raise ValueError("selected token count cannot exceed original")
        if self.compression_action is CompressionAction.NONE:
            if self.omission_reason is not None:
                raise ValueError("uncompressed sections cannot have an omission reason")
            if self.compressed_tokens != self.selected_tokens:
                raise ValueError("uncompressed token counts must match")
        elif self.omission_reason is None:
            raise ValueError("compression or omission requires a reason")
        if self.compression_action is CompressionAction.OMITTED and self.content:
            raise ValueError("omitted sections cannot retain content")
        if self.compression_action is CompressionAction.OMITTED and self.compressed_tokens != 0:
            raise ValueError("omitted sections must have zero rendered tokens")
        if self.section_type in {
            ContextSectionType.SYSTEM_INSTRUCTIONS,
            ContextSectionType.RESPONSE_CONTRACT,
        } and self.compression_action is CompressionAction.OMITTED:
            raise ValueError("protected edge sections cannot be omitted")
        return self


class ContextEnvelope(FrozenModel):
    schema_version: Literal[1] = 1
    policy_version: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    versions: ContextVersionBindings
    sections: tuple[ContextSection, ...]
    content_hash: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_hash_and_order(self) -> "ContextEnvelope":
        if len({section.stable_id for section in self.sections}) != len(self.sections):
            raise ValueError("section stable_id values must be unique")
        priorities = [section.ordering_priority for section in self.sections]
        if priorities != list(range(len(self.sections))):
            raise ValueError("sections must have unique contiguous priorities")
        material = self.model_dump(mode="json", exclude={"content_hash"})
        if self.content_hash != content_hash(material):
            raise ValueError("envelope content_hash mismatch")
        return self


class CompressionReceipt(FrozenModel):
    schema_version: Literal[1] = 1
    stable_id: str = Field(min_length=1)
    action: CompressionAction
    before_hash: str = Field(pattern=SHA256_PATTERN)
    after_hash: str = Field(pattern=SHA256_PATTERN)
    original_tokens: int = Field(ge=0)
    final_tokens: int = Field(ge=0)
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_change(self) -> "CompressionReceipt":
        changed = self.before_hash != self.after_hash
        if (self.action is CompressionAction.NONE) == changed:
            raise ValueError("compression action must agree with before/after hashes")
        return self


class ContextManifestEntry(FrozenModel):
    stable_id: str = Field(min_length=1)
    surface: Literal["message", "tool_schemas"]
    payload_json: str = Field(min_length=2)
    source_kind: ContextSourceKind
    trust: ContextTrust
    sensitivity: Sensitivity
    source_hash: str = Field(pattern=SHA256_PATTERN)
    selected_hash: str = Field(pattern=SHA256_PATTERN)
    redacted: bool = False

    @model_validator(mode="after")
    def validate_payload(self) -> "ContextManifestEntry":
        try:
            payload = json.loads(self.payload_json)
        except json.JSONDecodeError as exc:
            raise ValueError("payload_json must be canonical JSON") from exc
        if self.payload_json != canonical_json(payload):
            raise ValueError("payload_json must be canonical JSON")
        if self.surface == "message" and not isinstance(payload, dict):
            raise ValueError("message payload must be an object")
        if self.surface == "tool_schemas" and not (
            isinstance(payload, list) and all(isinstance(item, dict) for item in payload)
        ):
            raise ValueError("tool_schemas payload must be an object array")
        if self.selected_hash != content_hash(payload):
            raise ValueError("selected_hash must attest payload_json")
        return self


class ContextManifest(FrozenModel):
    schema_version: Literal[1] = 1
    policy_version: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    versions: ContextVersionBindings
    envelope_hash: str = Field(pattern=SHA256_PATTERN)
    redacted_envelope_hash: str = Field(pattern=SHA256_PATTERN)
    redacted_envelope_json: str = Field(min_length=2)
    entries: tuple[ContextManifestEntry, ...]
    compression_receipts: tuple[CompressionReceipt, ...]
    source_candidates_considered: tuple[str, ...]
    visibility_decisions: tuple[str, ...]
    deliberate_omissions: tuple[str, ...]
    artifact_refs: tuple[str, ...]
    budget_used_tokens: int = Field(ge=0)
    budget_limit_tokens: int = Field(ge=0)
    redaction_count: int = Field(ge=0)
    manifest_hash: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def validate_manifest_hash(self) -> "ContextManifest":
        if len(self.entries) != len(self.compression_receipts):
            raise ValueError("each manifest entry requires a compression receipt")
        if len({entry.stable_id for entry in self.entries}) != len(self.entries):
            raise ValueError("manifest entry stable_id values must be unique")
        if tuple(entry.stable_id for entry in self.entries) != tuple(
            receipt.stable_id for receipt in self.compression_receipts
        ):
            raise ValueError("manifest entries and compression receipts must align")
        material = self.model_dump(mode="json", exclude={"manifest_hash"})
        if self.manifest_hash != content_hash(material):
            raise ValueError("manifest_hash mismatch")
        try:
            redacted_envelope = json.loads(self.redacted_envelope_json)
        except json.JSONDecodeError as exc:
            raise ValueError("redacted_envelope_json must be canonical JSON") from exc
        if self.redacted_envelope_json != canonical_json(redacted_envelope):
            raise ValueError("redacted_envelope_json must be canonical JSON")
        if self.redacted_envelope_hash != content_hash(redacted_envelope):
            raise ValueError("redacted_envelope_hash mismatch")
        envelope = ContextEnvelope.model_validate(redacted_envelope)
        if envelope.content_hash != redacted_envelope.get("content_hash"):
            raise ValueError("redacted envelope content hash mismatch")
        return self

    def reconstruct_messages(self) -> list[dict[str, Any]]:
        return [json.loads(entry.payload_json) for entry in self.entries if entry.surface == "message"]

    def reconstruct_tool_schemas(self) -> list[dict[str, Any]]:
        schemas = [json.loads(entry.payload_json) for entry in self.entries if entry.surface == "tool_schemas"]
        return [schema for group in schemas for schema in group]

    def reconstruct_redacted_envelope(self) -> ContextEnvelope:
        return ContextEnvelope.model_validate_json(self.redacted_envelope_json)
