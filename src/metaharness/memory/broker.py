"""Frozen contracts for the shadow memory-action enforcement boundary."""
from __future__ import annotations

import itertools
import re
import sqlite3
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Iterable, Mapping

from pydantic import Field, ValidationError, field_validator, model_validator

from metaharness.context import ContextScope, ContextSourceKind, Sensitivity
from metaharness.context.models import (
    SHA256_PATTERN,
    FrozenModel,
    canonical_json,
    content_hash as calculate_content_hash,
)
from metaharness.memory.records import (
    ActivationState,
    LifecycleState,
    MemoryKind,
    MemoryRecord,
    normalize_text,
)
from metaharness.memory.stores import MemoryStore


class MemoryPhase(str, Enum):
    LOG = "log"
    MAINTAIN = "maintain"
    CONSULT = "consult"


class MemoryOperation(str, Enum):
    SEARCH = "search"
    READ = "read"
    CREATE_CANDIDATE = "create_candidate"
    APPEND = "append"
    UPSERT = "upsert"
    REVISE_CANDIDATE = "revise_candidate"
    LINK = "link"
    COMPRESS_CANDIDATE = "compress_candidate"


class MemoryActionOutcome(str, Enum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    PROPOSED = "proposed"


class MemoryProposalKind(str, Enum):
    ACTIVATE = "activate"
    TOMBSTONE = "tombstone"
    EXPIRY = "expiry"


_ALL_OPERATIONS = tuple(MemoryOperation)
_DEFAULT_PHASE_CONTRACTS: tuple[dict[str, Any], ...] = (
    {
        "phase": MemoryPhase.LOG,
        "allowed_operations": (
            MemoryOperation.CREATE_CANDIDATE,
            MemoryOperation.APPEND,
            MemoryOperation.LINK,
        ),
        "declaration": "post-observation candidate writes only",
    },
    {
        "phase": MemoryPhase.MAINTAIN,
        "allowed_operations": _ALL_OPERATIONS,
        "declaration": "scoped reads and append-only candidate maintenance",
    },
    {
        "phase": MemoryPhase.CONSULT,
        "allowed_operations": (MemoryOperation.SEARCH, MemoryOperation.READ),
        "declaration": "pre-action scoped reads only",
    },
)
_DEFAULT_POLICY_VERSIONS = (
    ("compression", "compression:v1"),
    ("context_budget", "context-budget:v1"),
    ("query", "query:v1"),
    ("ranking", "ranking:v1"),
    ("redaction", "redaction:v1"),
    ("retention", "retention:v1"),
)
_HASH_PLACEHOLDER = "sha256:" + "0" * 64
_SHA256_RE = re.compile(SHA256_PATTERN)


def _self_verifying_model(data: Any, handler: Callable[[Any], Any], mismatch: str):
    supplied = not isinstance(data, dict) or bool(data.get("content_hash"))
    values = data
    if isinstance(data, dict) and not supplied:
        values = dict(data)
        values["content_hash"] = _HASH_PLACEHOLDER
    model = handler(values)
    expected = calculate_content_hash(model.model_dump(mode="json", exclude={"content_hash"}))
    if supplied:
        if model.content_hash != expected:
            raise ValueError(mismatch)
    else:
        object.__setattr__(model, "content_hash", expected)
    return model


class MemoryPhaseContract(FrozenModel):
    phase: MemoryPhase
    allowed_operations: tuple[MemoryOperation, ...] = Field(min_length=1)
    declaration: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_operations(self) -> "MemoryPhaseContract":
        if len(set(self.allowed_operations)) != len(self.allowed_operations):
            raise ValueError("phase-contract operations must be unique")
        return self


class MemoryCognitiveSkillSnapshot(FrozenModel):
    """Immutable policy snapshot binding one shadow skill to a closed scope."""

    schema_version: int = Field(default=1, ge=1)
    snapshot_id: str = Field(min_length=1)
    skill_id: str = Field(min_length=1)
    scope: ContextScope
    goal_families: tuple[str, ...] = Field(min_length=1)
    roles: tuple[str, ...] = Field(min_length=1)
    parent_snapshot_hash: str | None = Field(default=None, pattern=SHA256_PATTERN)
    phase_contracts: tuple[MemoryPhaseContract, ...] = Field(
        default_factory=lambda: tuple(MemoryPhaseContract(**item) for item in _DEFAULT_PHASE_CONTRACTS)
    )
    allowed_actions: tuple[MemoryOperation, ...] = Field(default=_ALL_OPERATIONS, min_length=1)
    query_max_results: int = Field(default=8, ge=1)
    query_min_lexical_score: int = Field(default=1, ge=0)
    query_lifecycle_states: tuple[LifecycleState, ...] = (
        LifecycleState.CANDIDATE,
        LifecycleState.ACTIVE,
    )
    ranking_strategy: str = Field(default="lexical_overlap", min_length=1)
    ranking_stable_keys: tuple[str, ...] = (
        "lexical_score_desc",
        "confidence_desc",
        "creation_seq_asc",
        "record_id_asc",
        "store_name_asc",
    )
    compression_strategy: str = Field(default="deterministic_prefix", min_length=1)
    compression_max_tokens: int = Field(default=256, ge=1)
    retention_max_candidates: int = Field(default=1000, ge=1)
    retention_expiry_observations: int | None = Field(default=None, ge=1)
    context_budget_tokens: int = Field(default=1024, ge=1)
    allowed_sensitivities: tuple[Sensitivity, ...] = (
        Sensitivity.PUBLIC,
        Sensitivity.INTERNAL,
    )
    forbidden_payload_keys: tuple[str, ...] = (
        "api_key",
        "credential",
        "credentials",
        "password",
        "raw_secret",
        "secret",
        "token",
    )
    redaction_marker: str = Field(default="[REDACTED]", min_length=1)
    policy_versions: tuple[tuple[str, str], ...] = _DEFAULT_POLICY_VERSIONS
    deterministic_fallback: str = Field(
        default="reject-with-receipt; consult-empty-on-no-match; no-model-call",
        min_length=1,
    )
    lifecycle_state: LifecycleState = LifecycleState.ACTIVE
    content_hash: str = Field(default="", pattern=SHA256_PATTERN)

    @field_validator("goal_families", "roles")
    @classmethod
    def validate_nonempty_unique_strings(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value.strip() for value in values):
            raise ValueError("goal-family and role entries must be non-empty")
        if len(set(values)) != len(values):
            raise ValueError("goal-family and role entries must be unique")
        return values

    @model_validator(mode="wrap")
    @classmethod
    def validate_snapshot_hash(cls, data: Any, handler: Callable[[Any], Any]):
        return _self_verifying_model(data, handler, "memory skill snapshot content_hash mismatch")

    @model_validator(mode="after")
    def validate_policy_contract(self) -> "MemoryCognitiveSkillSnapshot":
        phases = [contract.phase for contract in self.phase_contracts]
        if len(phases) != len(set(phases)) or set(phases) != set(MemoryPhase):
            raise ValueError("phase_contracts must contain exactly one LOG, MAINTAIN, and CONSULT contract")
        if len(set(self.allowed_actions)) != len(self.allowed_actions):
            raise ValueError("allowed_actions must be unique")
        vocabulary = set(self.allowed_actions)
        for contract in self.phase_contracts:
            if not set(contract.allowed_operations).issubset(vocabulary):
                raise ValueError("phase contract grants an operation outside allowed_actions")
        if len(set(self.query_lifecycle_states)) != len(self.query_lifecycle_states):
            raise ValueError("query_lifecycle_states must be unique")
        if len(set(self.allowed_sensitivities)) != len(self.allowed_sensitivities):
            raise ValueError("allowed_sensitivities must be unique")
        if not self.allowed_sensitivities:
            raise ValueError("allowed_sensitivities must not be empty")
        if len({key for key, _ in self.policy_versions}) != len(self.policy_versions):
            raise ValueError("policy_versions keys must be unique")
        if any(not key or not version for key, version in self.policy_versions):
            raise ValueError("policy_versions entries must be non-empty")
        if self.parent_snapshot_hash == self.content_hash:
            raise ValueError("snapshot cannot name itself as its parent")
        return self

    @property
    def action_vocabulary(self) -> tuple[MemoryOperation, ...]:
        return self.allowed_actions

    def operations_for(self, phase: MemoryPhase) -> tuple[MemoryOperation, ...]:
        return next(contract.allowed_operations for contract in self.phase_contracts if contract.phase is phase)


class MemoryAction(FrozenModel):
    """Typed shadow action. Unknown operations can only enter via broker raw input."""

    schema_version: int = Field(default=1, ge=1)
    operation: MemoryOperation
    phase: MemoryPhase
    scope: ContextScope
    payload: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_json_payload(self) -> "MemoryAction":
        try:
            canonical_json(self.payload)
        except (TypeError, ValueError) as exc:
            raise ValueError("memory action payload must be JSON-serializable") from exc
        return self


class MemoryLifecycleProposal(FrozenModel):
    """Reviewable replacement for direct activation, expiry, or tombstoning."""

    schema_version: int = Field(default=1, ge=1)
    proposal_id: str = Field(min_length=1)
    proposal_kind: MemoryProposalKind
    snapshot_id: str = Field(min_length=1)
    snapshot_content_hash: str = Field(pattern=SHA256_PATTERN)
    scope: ContextScope
    target_record_ids: tuple[str, ...]
    requested_transition: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    observed_at: int = Field(ge=0)
    content_hash: str = Field(default="", pattern=SHA256_PATTERN)

    @model_validator(mode="wrap")
    @classmethod
    def validate_proposal_hash(cls, data: Any, handler: Callable[[Any], Any]):
        return _self_verifying_model(data, handler, "memory lifecycle proposal content_hash mismatch")


class MemoryActionReceipt(FrozenModel):
    """Immutable decision record emitted for every broker invocation."""

    schema_version: int = Field(default=1, ge=1)
    receipt_id: str = Field(min_length=1)
    snapshot_id: str = Field(min_length=1)
    snapshot_content_hash: str = Field(pattern=SHA256_PATTERN)
    skill_id: str = Field(min_length=1)
    context_id: str = Field(min_length=1)
    context_content_hash: str = Field(pattern=SHA256_PATTERN)
    store_high_water_marks: tuple[tuple[str, int], ...]
    policy_versions: tuple[tuple[str, str], ...]
    phase: str = Field(min_length=1)
    operation: str = Field(min_length=1)
    query: str | None = None
    source_record_ids: tuple[str, ...]
    considered_targets: tuple[str, ...]
    selected_targets: tuple[str, ...]
    scope: ContextScope
    lifecycle_filters: tuple[LifecycleState, ...]
    before_content_hashes: tuple[tuple[str, str], ...]
    after_content_hashes: tuple[tuple[str, str], ...]
    validation_results: tuple[str, ...]
    redaction_results: tuple[str, ...]
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    context_budget_tokens: int = Field(ge=0)
    latency_ms: int = Field(ge=0)
    accepted: bool
    outcome: MemoryActionOutcome
    effect_or_rejection_reason: str = Field(min_length=1)
    proposal_ids: tuple[str, ...] = ()
    observed_at: int = Field(ge=0)
    content_hash: str = Field(default="", pattern=SHA256_PATTERN)

    @model_validator(mode="wrap")
    @classmethod
    def validate_receipt_hash(cls, data: Any, handler: Callable[[Any], Any]):
        return _self_verifying_model(data, handler, "memory action receipt content_hash mismatch")

    @model_validator(mode="after")
    def validate_outcome(self) -> "MemoryActionReceipt":
        if self.accepted != (self.outcome is MemoryActionOutcome.ACCEPTED):
            raise ValueError("accepted must agree with receipt outcome")
        if self.outcome is MemoryActionOutcome.PROPOSED and not self.proposal_ids:
            raise ValueError("proposed receipt must identify its reviewable proposal")
        for _, digest in self.before_content_hashes + self.after_content_hashes:
            if not _SHA256_RE.fullmatch(digest):
                raise ValueError("before/after content hashes must be sha256 digests")
        return self


@dataclass(frozen=True)
class _ExecutionEffect:
    reason: str
    considered: tuple[str, ...] = ()
    selected: tuple[str, ...] = ()
    before_hashes: tuple[tuple[str, str], ...] = ()
    after_hashes: tuple[tuple[str, str], ...] = ()
    redaction_results: tuple[str, ...] = ("redaction:clear",)
    output_tokens: int = 0


class _BrokerRejection(Exception):
    def __init__(
        self,
        reason: str,
        *,
        considered: Iterable[str] = (),
        redaction_results: Iterable[str] = ("redaction:clear",),
    ):
        super().__init__(reason)
        self.reason = reason
        self.considered = tuple(considered)
        self.redaction_results = tuple(redaction_results)


class MemoryActionBroker:
    """Shadow-only policy boundary. It never grants domain execution authority."""

    mode = "shadow"

    _LIFECYCLE_OPERATIONS = {
        "activate": MemoryProposalKind.ACTIVATE,
        "activation": MemoryProposalKind.ACTIVATE,
        "delete": MemoryProposalKind.TOMBSTONE,
        "tombstone": MemoryProposalKind.TOMBSTONE,
        "expire": MemoryProposalKind.EXPIRY,
        "expiry": MemoryProposalKind.EXPIRY,
    }
    _DOMAIN_OPERATIONS = {
        "approve",
        "commit_domain",
        "deploy",
        "domain_action",
        "edit_code",
        "evaluate",
        "execute_task",
        "promote",
        "run_command",
        "self_approve",
        "widen_visibility",
        "write_test",
    }
    _DOMAIN_PAYLOAD_KEYS = {
        "domain_action",
        "task_action",
        "task_operation",
    }
    _IMMUTABLE_EVIDENCE_KINDS = {
        ContextSourceKind.IMMUTABLE_ARTIFACT.value,
        ContextSourceKind.EVALUATOR_RECEIPT.value,
        ContextSourceKind.PROTECTED_INSTRUCTIONS.value,
        ContextSourceKind.TOOL_POLICY_SCHEMA.value,
    }
    _WRITE_OPERATIONS = {
        MemoryOperation.CREATE_CANDIDATE,
        MemoryOperation.APPEND,
        MemoryOperation.UPSERT,
        MemoryOperation.REVISE_CANDIDATE,
        MemoryOperation.LINK,
        MemoryOperation.COMPRESS_CANDIDATE,
    }

    def __init__(
        self,
        *,
        snapshot: MemoryCognitiveSkillSnapshot,
        stores: Mapping[str | MemoryKind, MemoryStore] | Iterable[MemoryStore] | MemoryStore,
        clock: Callable[[], int] | None = None,
        receipt_id_factory: Callable[[], str] | None = None,
        proposal_id_factory: Callable[[], str] | None = None,
        receipt_sink: Callable[[MemoryActionReceipt], None] | None = None,
    ):
        self.snapshot = snapshot
        self._stores = self._normalize_stores(stores)
        self._clock_counter = itertools.count(0)
        self._receipt_counter = itertools.count(0)
        self._proposal_counter = itertools.count(0)
        self._clock = clock if clock is not None else lambda: next(self._clock_counter)
        self._receipt_id_factory = (
            receipt_id_factory
            if receipt_id_factory is not None
            else lambda: f"memory-action-{next(self._receipt_counter):08x}"
        )
        self._proposal_id_factory = (
            proposal_id_factory
            if proposal_id_factory is not None
            else lambda: f"memory-proposal-{next(self._proposal_counter):08x}"
        )
        self._receipt_sink = receipt_sink
        self._receipts: list[MemoryActionReceipt] = []
        self._proposals: list[MemoryLifecycleProposal] = []

    @property
    def receipts(self) -> tuple[MemoryActionReceipt, ...]:
        return tuple(self._receipts)

    @property
    def proposals(self) -> tuple[MemoryLifecycleProposal, ...]:
        return tuple(self._proposals)

    def execute(self, action: MemoryAction | Mapping[str, Any], **kwargs: Any) -> MemoryActionReceipt:
        return self.invoke(action, **kwargs)

    def invoke(
        self,
        action: MemoryAction | Mapping[str, Any],
        *,
        context_id: str = "shadow-context",
        context: Mapping[str, Any] | None = None,
        context_hash: str | None = None,
    ) -> MemoryActionReceipt:
        # FIX-2: validate the receipt-critical context_id BEFORE any store
        # write so a construction failure cannot follow a successful write.
        # Coerce empty/whitespace to a placeholder so the rejection receipt
        # itself is constructible (its schema requires min_length=1).
        receipt_context_id = context_id if isinstance(context_id, str) and context_id.strip() else "shadow-context:missing"
        if context_id != receipt_context_id:
            return self._emit_receipt(
                context_id=receipt_context_id,
                context_hash="sha256:" + "0" * 64,
                scope=self.snapshot.scope,
                phase="invalid",
                operation="invalid",
                payload={},
                observed_at=self._safe_clock(),
                input_tokens=0,
                outcome=MemoryActionOutcome.REJECTED,
                reason="context_id must be a non-empty string (receipt-critical input)",
                validation=("broker_mode:shadow", "context_id:rejected"),
            )

        try:
            return self._invoke_locked(
                action,
                context_id=context_id,
                context=context,
                context_hash=context_hash,
            )
        except _BrokerRejection as exc:
            # The structured path already returns receipts; this catch exists
            # for defensive completeness and is a no-op when _invoke_locked
            # has already produced one.
            return self._emit_receipt(
                context_id=context_id,
                context_hash=self._safe_context_hash(
                    context_id=context_id, context=context,
                    context_hash=context_hash, scope=self.snapshot.scope,
                ),
                scope=self.snapshot.scope,
                phase="invalid",
                operation="invalid",
                payload={},
                observed_at=self._safe_clock(),
                input_tokens=0,
                outcome=MemoryActionOutcome.REJECTED,
                reason=f"broker rejection escaped: {exc.reason}",
                validation=("broker_mode:shadow", "policy:rejected"),
                considered=exc.considered,
                redaction_results=exc.redaction_results,
            )
        except Exception as exc:  # FIX-2: catch any escape and emit a receipt.
            return self._emit_receipt(
                context_id=context_id,
                context_hash=self._safe_context_hash(
                    context_id=context_id, context=context,
                    context_hash=context_hash, scope=self.snapshot.scope,
                ),
                scope=self.snapshot.scope,
                phase="invalid",
                operation="invalid",
                payload={},
                observed_at=self._safe_clock(),
                input_tokens=0,
                outcome=MemoryActionOutcome.REJECTED,
                reason=f"operational error: {type(exc).__name__}: {exc}",
                validation=("broker_mode:shadow", "execution:rejected"),
            )

    def _invoke_locked(
        self,
        action: MemoryAction | Mapping[str, Any],
        *,
        context_id: str,
        context: Mapping[str, Any] | None,
        context_hash: str | None,
    ) -> MemoryActionReceipt:
        raw = self._raw_action(action)
        raw_operation_value = self._raw_operation_value(raw)
        phase = str(raw.get("phase", "invalid"))
        scope = self._receipt_scope(raw.get("scope"))
        payload = raw.get("payload") if isinstance(raw.get("payload"), dict) else {}
        observed_at = self._clock()
        # FIX-2 / FIX-8: never let serialization raise. Compute the context
        # hash with a defensive fallback and reject mismatches as a
        # receipted outcome.
        resolved_context_hash, context_hash_rejection = self._safe_resolve_context_hash(
            context_id=context_id,
            context=context,
            context_hash=context_hash,
            scope=scope,
        )
        if context_hash_rejection is not None:
            return self._emit_receipt(
                context_id=context_id,
                context_hash="sha256:" + "0" * 64,
                scope=scope,
                phase=phase,
                operation=raw_operation_value,
                payload=payload,
                observed_at=observed_at,
                input_tokens=0,
                outcome=MemoryActionOutcome.REJECTED,
                reason=context_hash_rejection,
                validation=("broker_mode:shadow", "context_hash:rejected"),
            )
        input_tokens = self._safe_tokens(raw)
        validation = ["broker_mode:shadow"]

        if raw_operation_value in self._DOMAIN_OPERATIONS:
            return self._emit_receipt(
                context_id=context_id,
                context_hash=resolved_context_hash,
                scope=scope,
                phase=phase,
                operation=raw_operation_value,
                payload=payload,
                observed_at=observed_at,
                input_tokens=input_tokens,
                outcome=MemoryActionOutcome.REJECTED,
                reason="domain task actions have no authority through the shadow memory broker",
                validation=validation + ["domain_authority:rejected"],
            )
        if raw_operation_value in self._LIFECYCLE_OPERATIONS:
            return self._propose_lifecycle_action(
                kind=self._LIFECYCLE_OPERATIONS[raw_operation_value],
                requested_transition=raw_operation_value,
                raw=raw,
                context_id=context_id,
                context_hash=resolved_context_hash,
                scope=scope,
                phase=phase,
                operation=raw_operation_value,
                payload=payload,
                observed_at=observed_at,
                input_tokens=input_tokens,
                validation=validation,
            )

        try:
            typed_action = action if isinstance(action, MemoryAction) else MemoryAction.model_validate(raw)
        except ValidationError as exc:
            reason = "unknown memory operation" if raw_operation_value not in {op.value for op in MemoryOperation} else "invalid memory action schema"
            return self._emit_receipt(
                context_id=context_id,
                context_hash=resolved_context_hash,
                scope=scope,
                phase=phase,
                operation=raw_operation_value,
                payload=payload,
                observed_at=observed_at,
                input_tokens=input_tokens,
                outcome=MemoryActionOutcome.REJECTED,
                reason=f"{reason}: {exc.errors()[0]['msg']}",
                validation=validation + ["action_schema:rejected"],
            )

        operation = typed_action.operation.value
        phase = typed_action.phase.value
        scope = typed_action.scope
        payload = typed_action.payload
        validation.append("action_schema:valid")

        rejection = self._validate_action(typed_action)
        if rejection is not None:
            if rejection.reason.startswith("lifecycle bypass:"):
                transition = rejection.reason.split(":", 1)[1].strip()
                return self._propose_lifecycle_action(
                    kind=self._proposal_kind(transition),
                    requested_transition=transition,
                    raw={"operation": transition, "phase": phase, "scope": scope, "payload": payload},
                    context_id=context_id,
                    context_hash=resolved_context_hash,
                    scope=scope,
                    phase=phase,
                    operation=operation,
                    payload=payload,
                    observed_at=observed_at,
                    input_tokens=input_tokens,
                    validation=validation,
                )
            return self._emit_receipt(
                context_id=context_id,
                context_hash=resolved_context_hash,
                scope=scope,
                phase=phase,
                operation=operation,
                payload=payload,
                observed_at=observed_at,
                input_tokens=input_tokens,
                outcome=MemoryActionOutcome.REJECTED,
                reason=rejection.reason,
                validation=validation + ["policy:rejected"],
                considered=rejection.considered,
                redaction_results=rejection.redaction_results,
            )

        validation.extend(("snapshot_scope:valid", "phase_contract:valid", "vocabulary:valid"))
        try:
            effect = self._execute_action(typed_action, observed_at=observed_at)
        except _BrokerRejection as exc:
            return self._emit_receipt(
                context_id=context_id,
                context_hash=resolved_context_hash,
                scope=scope,
                phase=phase,
                operation=operation,
                payload=payload,
                observed_at=observed_at,
                input_tokens=input_tokens,
                outcome=MemoryActionOutcome.REJECTED,
                reason=exc.reason,
                validation=validation + ["execution:rejected"],
                considered=exc.considered,
                redaction_results=exc.redaction_results,
            )

        return self._emit_receipt(
            context_id=context_id,
            context_hash=resolved_context_hash,
            scope=scope,
            phase=phase,
            operation=operation,
            payload=payload,
            observed_at=observed_at,
            input_tokens=input_tokens,
            output_tokens=effect.output_tokens,
            outcome=MemoryActionOutcome.ACCEPTED,
            reason=effect.reason,
            validation=validation + ["execution:accepted"],
            considered=effect.considered,
            selected=effect.selected,
            before_hashes=effect.before_hashes,
            after_hashes=effect.after_hashes,
            redaction_results=effect.redaction_results,
        )

    def _raw_operation_value(self, raw: Mapping[str, Any]) -> str:
        op = raw.get("operation")
        if isinstance(op, MemoryOperation):
            return op.value
        return str(op) if op is not None else "invalid"

    def _safe_clock(self) -> int:
        try:
            return self._clock()
        except Exception:
            return 0

    @staticmethod
    def _safe_tokens(raw: Mapping[str, Any]) -> int:
        try:
            return len(canonical_json(raw).split())
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _safe_resolve_context_hash(
        *,
        context_id: str,
        context: Mapping[str, Any] | None,
        context_hash: str | None,
        scope: ContextScope,
    ) -> tuple[str, str | None]:
        """FIX-8: when both context and context_hash are supplied, verify the
        hash matches the canonical-JSON(context). Returns (hash, rejection_or_None).
        FIX-2: never let serialization raise; if context is not JSON-safe,
        return a zero hash and a rejection message."""

        if context_hash is not None and not _SHA256_RE.fullmatch(context_hash):
            return "sha256:" + "0" * 64, "context_hash is not a sha256 digest"
        if context is None:
            if context_hash is not None:
                return context_hash, None
            material = {
                "context_id": context_id,
                "scope": scope.model_dump(mode="json"),
            }
            return calculate_content_hash(material), None
        try:
            material = {
                "context_id": context_id,
                "scope": scope.model_dump(mode="json"),
                "context": context,
            }
            computed = calculate_content_hash(material)
        except (TypeError, ValueError):
            return "sha256:" + "0" * 64, "context is not JSON-serializable"
        if context_hash is not None and context_hash != computed:
            return computed, "context_hash does not match canonical-JSON(context)"
        return computed, None

    def _safe_context_hash(
        self,
        *,
        context_id: str,
        context: Mapping[str, Any] | None,
        context_hash: str | None,
        scope: ContextScope,
    ) -> str:
        return self._safe_resolve_context_hash(
            context_id=context_id, context=context,
            context_hash=context_hash, scope=scope,
        )[0]

    def _validate_action(self, action: MemoryAction) -> _BrokerRejection | None:
        if self.snapshot.lifecycle_state is not LifecycleState.ACTIVE:
            return _BrokerRejection("snapshot lifecycle is not active")
        if not self._scope_within(self.snapshot.scope, action.scope):
            return _BrokerRejection("action scope exceeds the snapshot scope")
        if action.operation not in self.snapshot.allowed_actions:
            return _BrokerRejection("operation is outside the snapshot action vocabulary")
        if action.operation not in self.snapshot.operations_for(action.phase):
            return _BrokerRejection("operation is not permitted by the phase contract")
        if self._contains_path_traversal(action.payload):
            return _BrokerRejection("path traversal is forbidden in memory action payloads")
        # FIX-13: domain-marker check is recursive, not top-level only.
        if self._payload_keys(action.payload) & self._DOMAIN_PAYLOAD_KEYS:
            return _BrokerRejection("domain task actions have no authority through the shadow memory broker")
        if action.operation in self._WRITE_OPERATIONS:
            immutable_kind = action.payload.get("target_kind", action.payload.get("source_kind"))
            requested_kind = action.payload.get("kind")
            if (
                action.payload.get("immutable_evidence") is True
                or immutable_kind in self._IMMUTABLE_EVIDENCE_KINDS
                or requested_kind in self._IMMUTABLE_EVIDENCE_KINDS
            ):
                return _BrokerRejection("writes to immutable evidence are forbidden")
            transition = self._requested_lifecycle_transition(action.payload)
            if transition is not None:
                return _BrokerRejection(f"lifecycle bypass: {transition}")
        forbidden_keys = self._payload_keys(action.payload) & set(self.snapshot.forbidden_payload_keys)
        if forbidden_keys:
            return _BrokerRejection(
                f"redaction violation: forbidden payload keys {sorted(forbidden_keys)}",
                redaction_results=("redaction:rejected",),
            )
        sensitivity = action.payload.get("sensitivity", Sensitivity.INTERNAL.value)
        try:
            resolved = sensitivity if isinstance(sensitivity, Sensitivity) else Sensitivity(sensitivity)
        except ValueError:
            return _BrokerRejection(
                "redaction violation: unknown sensitivity",
                redaction_results=("sensitivity:rejected",),
            )
        if action.operation in self._WRITE_OPERATIONS and resolved not in self.snapshot.allowed_sensitivities:
            return _BrokerRejection(
                "redaction violation: sensitivity exceeds snapshot policy",
                redaction_results=("sensitivity:rejected",),
            )
        return None

    def _execute_action(self, action: MemoryAction, *, observed_at: int) -> _ExecutionEffect:
        if action.operation is MemoryOperation.SEARCH:
            return self._search(action)
        if action.operation is MemoryOperation.READ:
            return self._read(action)
        if action.operation in {MemoryOperation.CREATE_CANDIDATE, MemoryOperation.APPEND}:
            return self._create_candidate(action, observed_at=observed_at)
        if action.operation is MemoryOperation.LINK:
            return self._link_candidate(action, observed_at=observed_at)
        if action.operation is MemoryOperation.UPSERT and "target_record_id" not in action.payload:
            return self._create_candidate(action, observed_at=observed_at)
        if action.operation in {
            MemoryOperation.UPSERT,
            MemoryOperation.REVISE_CANDIDATE,
            MemoryOperation.COMPRESS_CANDIDATE,
        }:
            return self._revise_candidate(action, observed_at=observed_at)
        raise _BrokerRejection("operation has no shadow implementation")

    def _search(self, action: MemoryAction) -> _ExecutionEffect:
        query = action.payload.get("query")
        if not isinstance(query, str) or not query.strip():
            raise _BrokerRejection("search requires a non-empty query")
        lifecycle_filters = self._lifecycle_filters(action.payload)
        requested_kind = self._optional_kind(action.payload.get("kind"))
        terms = tuple(token for token in normalize_text(query).split() if token)
        if not terms:
            raise _BrokerRejection("search requires at least one lexical term")
        # FIX-12: lexical matching uses the FTS5 index for token-level match.
        # FTS5 bm25 is non-deterministic across platforms so we treat the
        # index only as a membership filter; the visible-candidate ordering
        # is fully determined by creation_seq + record_id + store_name.
        fts_store, fts_matches = self._fts_intersect(terms)
        ranked: list[tuple[int, str, str, MemoryRecord]] = []
        redacted = 0
        for store_name, store in self._stores.items():
            for record in store.list(project_id=action.scope.project_id, include_tombstoned=True):
                if not self._record_visible(action.scope, record):
                    continue
                if record.activation_state is not ActivationState.ACTIVE:
                    continue
                if record.lifecycle_state not in lifecycle_filters:
                    continue
                if requested_kind is not None and record.kind is not requested_kind:
                    continue
                if fts_matches is not None and record.id not in fts_matches:
                    continue
                if record.sensitivity not in self.snapshot.allowed_sensitivities:
                    redacted += 1
                    continue
                ranked.append((record.creation_seq, record.id, store_name, record))
        # FIX-11: deterministically suppress any record whose id appears in
        # another record's `supersedes` (latest-in-chain wins).
        superseded_ids = {
            sup_id
            for _, _, _, rec in ranked
            for sup_id in rec.supersedes
        }
        ranked = [item for item in ranked if item[1] not in superseded_ids]
        ranked.sort(key=lambda item: (item[0], item[1], item[2]))
        considered = tuple(item[1] for item in ranked)
        raw_limit = action.payload.get("limit", self.snapshot.query_max_results)
        if not isinstance(raw_limit, int) or isinstance(raw_limit, bool) or raw_limit <= 0:
            raise _BrokerRejection("search limit must be a positive integer")
        limit = min(raw_limit, self.snapshot.query_max_results)
        selected: list[MemoryRecord] = []
        used_tokens = 0
        for _, _, _, record in ranked:
            record_tokens = self._tokens(record.content)
            if used_tokens + record_tokens > self.snapshot.context_budget_tokens:
                continue
            selected.append(record)
            used_tokens += record_tokens
            if len(selected) >= limit:
                break
        redaction_results = (
            f"sensitivity_filtered:{redacted}",
            "context_budget:applied",
            "fts_token_match:applied" if fts_matches is not None else "fts_token_match:bypassed",
        )
        return _ExecutionEffect(
            reason="scoped deterministic search completed",
            considered=considered,
            selected=tuple(record.id for record in selected),
            redaction_results=redaction_results,
            output_tokens=used_tokens,
        )

    def _fts_intersect(self, terms: tuple[str, ...]) -> tuple[MemoryStore | None, frozenset[str] | None]:
        """FIX-12: run an FTS5 prefix-AND-query across the terms. Returns
        (fts_store, matching_ids). If no store has an FTS5 index (e.g. an
        in-memory store with no pre-populated rows), returns (None, None)
        so the search path falls back to per-record visibility checks
        without resurrecting the dead substring-scoring path.
        """

        candidates: list[tuple[MemoryStore, set[str]]] = []
        for store in self._stores.values():
            conn = getattr(store, "_conn", None)
            if conn is None:
                continue
            try:
                row = conn.execute(
                    "SELECT name FROM sqlite_master WHERE name='records_fts'"
                ).fetchone()
            except Exception:
                continue
            if row is None:
                continue
            try:
                # FIX-12: exact-token AND-match across the query terms. FTS5
                # without the ``*`` suffix is a whole-token match, so 'cat'
                # matches 'cat' but NOT 'category'. Multiple terms are
                # combined with a space (FTS5 implicit AND).
                match_expression = " ".join(terms)
                rows = conn.execute(
                    "SELECT record_id FROM records_fts WHERE records_fts MATCH ?",
                    (match_expression,),
                ).fetchall()
            except sqlite3.OperationalError:
                continue
            ids = {row[0] for row in rows}
            candidates.append((store, ids))
        if not candidates:
            return None, None
        intersected: set[str] | None = None
        chosen_store: MemoryStore | None = None
        for store, ids in candidates:
            intersected = ids if intersected is None else (intersected & ids)
            chosen_store = store
            if not intersected:
                return store, frozenset()
        return chosen_store, frozenset(intersected or ())

    def _read(self, action: MemoryAction) -> _ExecutionEffect:
        record_ids = self._record_ids(action.payload)
        if not record_ids:
            raise _BrokerRejection("read requires record_ids")
        lifecycle_filters = self._lifecycle_filters(action.payload)
        records: list[tuple[str, MemoryRecord]] = []
        for record_id in record_ids:
            _, record = self._find_record(record_id, action.payload)
            if not self._record_visible(action.scope, record):
                raise _BrokerRejection("cross-scope record id rejected", considered=record_ids)
            # FIX-5: reads enforce the same activation/lifecycle visibility as
            # search, and the receipt records the filters actually applied.
            if record.activation_state is ActivationState.TOMBSTONED:
                raise _BrokerRejection(
                    "read refuses tombstoned record",
                    considered=record_ids,
                )
            if record.lifecycle_state not in lifecycle_filters:
                raise _BrokerRejection(
                    "read refuses record outside the requested lifecycle filters",
                    considered=record_ids,
                )
            if record.sensitivity not in self.snapshot.allowed_sensitivities:
                raise _BrokerRejection(
                    "redaction violation: selected record exceeds sensitivity policy",
                    considered=record_ids,
                    redaction_results=("sensitivity:rejected",),
                )
            records.append((record_id, record))
        records.sort(key=lambda item: (item[1].creation_seq, item[0]))
        selected: list[str] = []
        used_tokens = 0
        for record_id, record in records:
            size = self._tokens(record.content)
            if used_tokens + size <= self.snapshot.context_budget_tokens:
                selected.append(record_id)
                used_tokens += size
        return _ExecutionEffect(
            reason="scoped read completed",
            considered=tuple(record_ids),
            selected=tuple(selected),
            redaction_results=("sensitivity:allowed", "context_budget:applied"),
            output_tokens=used_tokens,
        )

    def _create_candidate(self, action: MemoryAction, *, observed_at: int) -> _ExecutionEffect:
        content = action.payload.get("content")
        if not isinstance(content, str) or not content.strip():
            raise _BrokerRejection("candidate write requires non-empty content")
        kind = self._required_kind(action.payload.get("kind", MemoryKind.EPISODIC_MEMORY.value))
        store = self._store_for_kind(kind, action.payload)
        sensitivity = Sensitivity(action.payload.get("sensitivity", Sensitivity.INTERNAL.value))
        source_record_ids = self._source_record_ids(action.payload)
        self._validate_source_records(source_record_ids, action.scope)
        record = store.commit(
            kind=kind,
            content=content,
            scope=action.scope,
            source_refs=source_record_ids,
            observed_at=observed_at,
            confidence=self._confidence(action.payload),
            sensitivity=sensitivity,
            creator_id=self.snapshot.skill_id,
            lifecycle_state=LifecycleState.CANDIDATE,
            activation_state=ActivationState.ACTIVE,
        )
        return _ExecutionEffect(
            reason="candidate appended in shadow mode",
            considered=source_record_ids,
            selected=(record.id,),
            after_hashes=((record.id, calculate_content_hash(record.content)),),
            redaction_results=("sensitivity:allowed",),
            output_tokens=self._tokens(record.content),
        )

    def _link_candidate(self, action: MemoryAction, *, observed_at: int) -> _ExecutionEffect:
        source_record_ids = self._source_record_ids(action.payload)
        if not source_record_ids:
            raise _BrokerRejection("link requires source_record_ids")
        self._validate_source_records(source_record_ids, action.scope)
        payload = dict(action.payload)
        payload.setdefault("content", "links:" + ",".join(sorted(source_record_ids)))
        linked = action.model_copy(update={"payload": payload})
        return self._create_candidate(linked, observed_at=observed_at)

    def _revise_candidate(self, action: MemoryAction, *, observed_at: int) -> _ExecutionEffect:
        target_id = action.payload.get("target_record_id")
        if not isinstance(target_id, str) or not target_id:
            raise _BrokerRejection("candidate revision requires target_record_id")
        store, existing = self._find_record(target_id, action.payload)
        if not self._record_visible(action.scope, existing):
            raise _BrokerRejection("cross-scope record id rejected", considered=(target_id,))
        if existing.lifecycle_state is not LifecycleState.CANDIDATE:
            raise _BrokerRejection(
                "writes to immutable evidence/durable records are forbidden; only candidates may be revised",
                considered=(target_id,),
            )
        content = action.payload.get("content")
        if action.operation is MemoryOperation.COMPRESS_CANDIDATE and content is None:
            words = existing.content.split()
            content = " ".join(words[: self.snapshot.compression_max_tokens])
        if not isinstance(content, str) or not content.strip():
            raise _BrokerRejection("candidate revision requires non-empty content", considered=(target_id,))
        if action.operation is MemoryOperation.COMPRESS_CANDIDATE:
            if self._tokens(content) > self._tokens(existing.content):
                raise _BrokerRejection("compressed candidate cannot exceed source token count", considered=(target_id,))
        revised = store.mutate(
            existing.id,
            content=content,
            receipt="shadow-memory-action-broker",
            actor_id=self.snapshot.skill_id,
            observed_at=observed_at,
            lifecycle_state=LifecycleState.CANDIDATE,
            activation_state=ActivationState.ACTIVE,
            mutation_reason=f"shadow {action.operation.value}",
        )
        return _ExecutionEffect(
            reason="candidate revision appended; original evidence preserved",
            considered=(target_id,),
            selected=(revised.id,),
            before_hashes=((existing.id, calculate_content_hash(existing.content)),),
            after_hashes=((revised.id, calculate_content_hash(revised.content)),),
            redaction_results=("sensitivity:allowed",),
            output_tokens=self._tokens(revised.content),
        )

    def _make_proposal_receipt(
        self,
        *,
        kind: MemoryProposalKind,
        requested_transition: str,
        raw: Mapping[str, Any],
        context_id: str,
        context_hash: str,
        scope: ContextScope,
        phase: str,
        operation: str,
        payload: Mapping[str, Any],
        observed_at: int,
        input_tokens: int,
        validation: list[str],
    ) -> MemoryActionReceipt:
        # FIX-4: proposals go through the same validation gates as every
        # other operation. Cross-scope or invalid targets produce a
        # REJECTED receipt, never a stored proposal. Only in-scope, well-
        # typed lifecycle operations are converted to PROPOSED.
        target_ids = self._record_ids(payload)
        if not target_ids:
            return self._emit_receipt(
                context_id=context_id,
                context_hash=context_hash,
                scope=scope,
                phase=phase,
                operation=operation,
                payload=payload,
                observed_at=observed_at,
                input_tokens=input_tokens,
                outcome=MemoryActionOutcome.REJECTED,
                reason="lifecycle operation requires in-scope target record_ids",
                validation=tuple(validation) + ("lifecycle_direct_mutation:rejected",),
            )
        try:
            validated_targets: list[str] = []
            for target_id in target_ids:
                _, record = self._find_record(target_id, payload)
                if not self._record_visible(scope, record):
                    return self._emit_receipt(
                        context_id=context_id,
                        context_hash=context_hash,
                        scope=scope,
                        phase=phase,
                        operation=operation,
                        payload=payload,
                        observed_at=observed_at,
                        input_tokens=input_tokens,
                        outcome=MemoryActionOutcome.REJECTED,
                        reason="lifecycle proposal cross-scope target rejected",
                        validation=tuple(validation) + ("lifecycle_direct_mutation:rejected",),
                        considered=target_ids,
                    )
                validated_targets.append(target_id)
        except _BrokerRejection as exc:
            return self._emit_receipt(
                context_id=context_id,
                context_hash=context_hash,
                scope=scope,
                phase=phase,
                operation=operation,
                payload=payload,
                observed_at=observed_at,
                input_tokens=input_tokens,
                outcome=MemoryActionOutcome.REJECTED,
                reason=f"lifecycle proposal target validation failed: {exc.reason}",
                validation=tuple(validation) + ("lifecycle_direct_mutation:rejected",),
                considered=exc.considered,
                redaction_results=exc.redaction_results,
            )
        proposal = MemoryLifecycleProposal(
            proposal_id=self._proposal_id_factory(),
            proposal_kind=kind,
            snapshot_id=self.snapshot.snapshot_id,
            snapshot_content_hash=self.snapshot.content_hash,
            scope=scope,
            target_record_ids=tuple(validated_targets),
            requested_transition=requested_transition,
            reason="direct lifecycle mutation rejected; human review is required",
            observed_at=observed_at,
        )
        self._proposals.append(proposal)
        return self._emit_receipt(
            context_id=context_id,
            context_hash=context_hash,
            scope=scope,
            phase=phase,
            operation=operation,
            payload=payload,
            observed_at=observed_at,
            input_tokens=input_tokens,
            outcome=MemoryActionOutcome.PROPOSED,
            reason="lifecycle bypass rejected and converted to a reviewable proposal",
            validation=tuple(validation) + ("lifecycle_direct_mutation:rejected", "proposal:emitted"),
            considered=tuple(validated_targets),
            proposal_ids=(proposal.proposal_id,),
        )

    # FIX-4 alias: keep a private alias for the typed-action code path.
    def _propose_lifecycle_action(self, **kwargs: Any) -> MemoryActionReceipt:
        return self._make_proposal_receipt(**kwargs)

    def _emit_receipt(
        self,
        *,
        context_id: str,
        context_hash: str,
        scope: ContextScope,
        phase: str,
        operation: str,
        payload: Mapping[str, Any],
        observed_at: int,
        input_tokens: int,
        outcome: MemoryActionOutcome,
        reason: str,
        validation: Iterable[str],
        considered: Iterable[str] = (),
        selected: Iterable[str] = (),
        before_hashes: Iterable[tuple[str, str]] = (),
        after_hashes: Iterable[tuple[str, str]] = (),
        redaction_results: Iterable[str] = ("redaction:clear",),
        output_tokens: int = 0,
        proposal_ids: Iterable[str] = (),
    ) -> MemoryActionReceipt:
        receipt = MemoryActionReceipt(
            receipt_id=self._receipt_id_factory(),
            snapshot_id=self.snapshot.snapshot_id,
            snapshot_content_hash=self.snapshot.content_hash,
            skill_id=self.snapshot.skill_id,
            context_id=context_id,
            context_content_hash=context_hash,
            store_high_water_marks=self._high_water_marks(scope),
            policy_versions=self.snapshot.policy_versions,
            phase=phase,
            operation=operation,
            query=payload.get("query") if isinstance(payload.get("query"), str) else None,
            source_record_ids=self._source_ids_for_receipt(payload),
            considered_targets=tuple(considered),
            selected_targets=tuple(selected),
            scope=scope,
            lifecycle_filters=self._lifecycle_filters(payload, reject_invalid=False),
            before_content_hashes=tuple(before_hashes),
            after_content_hashes=tuple(after_hashes),
            validation_results=tuple(validation),
            redaction_results=tuple(redaction_results),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            context_budget_tokens=self.snapshot.context_budget_tokens,
            latency_ms=0,
            accepted=outcome is MemoryActionOutcome.ACCEPTED,
            outcome=outcome,
            effect_or_rejection_reason=reason,
            proposal_ids=tuple(proposal_ids),
            observed_at=observed_at,
        )
        self._receipts.append(receipt)
        if self._receipt_sink is not None:
            try:
                self._receipt_sink(receipt)
            except Exception:
                pass
        return receipt

    @staticmethod
    def _raw_tokenizable(raw: Mapping[str, Any]) -> Any:
        normalized: dict[str, Any] = dict(raw)
        scope = normalized.get("scope")
        if isinstance(scope, ContextScope):
            normalized["scope"] = scope.model_dump(mode="json")
        return normalized

    @staticmethod
    def _raw_action(action: MemoryAction | Mapping[str, Any]) -> dict[str, Any]:
        if isinstance(action, MemoryAction):
            data = action.model_dump(mode="python")
            data["scope"] = action.scope
            return data
        if isinstance(action, Mapping):
            return dict(action)
        return {"operation": "invalid", "phase": "invalid", "payload": {}}

    def _receipt_scope(self, value: Any) -> ContextScope:
        try:
            return value if isinstance(value, ContextScope) else ContextScope.model_validate(value)
        except (ValidationError, TypeError):
            return self.snapshot.scope

    @staticmethod
    def _resolve_context_hash(
        *,
        context_id: str,
        context: Mapping[str, Any] | None,
        context_hash: str | None,
        scope: ContextScope,
    ) -> str:
        if context_hash is not None and _SHA256_RE.fullmatch(context_hash):
            return context_hash
        material = context if context is not None else {
            "context_id": context_id,
            "scope": scope.model_dump(mode="json"),
        }
        return calculate_content_hash(material)

    @staticmethod
    def _scope_within(boundary: ContextScope, requested: ContextScope) -> bool:
        if boundary.project_id != requested.project_id:
            return False
        for field in ("run_id", "task_id", "attempt_id", "lineage_id"):
            expected = getattr(boundary, field)
            if expected is not None and getattr(requested, field) != expected:
                return False
        return True

    @staticmethod
    def _record_visible(scope: ContextScope, record: MemoryRecord) -> bool:
        if scope.project_id != record.scope.project_id:
            return False
        for field in ("run_id", "task_id", "attempt_id", "lineage_id"):
            requested = getattr(scope, field)
            if requested is not None and getattr(record.scope, field) != requested:
                return False
        return True

    @classmethod
    def _contains_path_traversal(cls, value: Any) -> bool:
        if isinstance(value, str):
            return ".." in value.replace("\\", "/").split("/")
        if isinstance(value, Mapping):
            return any(cls._contains_path_traversal(key) or cls._contains_path_traversal(item) for key, item in value.items())
        if isinstance(value, (list, tuple)):
            return any(cls._contains_path_traversal(item) for item in value)
        return False

    @classmethod
    def _payload_keys(cls, value: Any) -> set[str]:
        if not isinstance(value, Mapping):
            return set()
        keys = {str(key).lower() for key in value}
        for nested in value.values():
            keys.update(cls._payload_keys(nested))
        return keys

    @staticmethod
    def _requested_lifecycle_transition(payload: Mapping[str, Any]) -> str | None:
        # FIX-14: normalize enum values via .value to avoid str(LifecycleState.X)
        # false-positives (str() of an Enum is the qualified name).
        for key in ("delete", "tombstone", "activate", "expire", "expiry"):
            if key in payload and payload[key] not in (False, None, ""):
                return key
        lifecycle = payload.get("lifecycle_state")
        if lifecycle is not None:
            value = lifecycle.value if isinstance(lifecycle, LifecycleState) else str(lifecycle)
            if value != LifecycleState.CANDIDATE.value:
                return value
        activation = payload.get("activation_state")
        if activation is not None:
            value = activation.value if isinstance(activation, ActivationState) else str(activation)
            if value != ActivationState.ACTIVE.value:
                return value
        return None

    @staticmethod
    def _proposal_kind(transition: str) -> MemoryProposalKind:
        value = transition.lower()
        if "activ" in value:
            return MemoryProposalKind.ACTIVATE
        if "expir" in value:
            return MemoryProposalKind.EXPIRY
        return MemoryProposalKind.TOMBSTONE

    @staticmethod
    def _record_ids(payload: Mapping[str, Any]) -> tuple[str, ...]:
        raw_ids = payload.get("record_ids")
        if isinstance(raw_ids, (list, tuple)):
            return tuple(str(value) for value in raw_ids)
        target = payload.get("target_record_id")
        return (str(target),) if isinstance(target, str) and target else ()

    @staticmethod
    def _source_record_ids(payload: Mapping[str, Any]) -> tuple[str, ...]:
        raw_ids = payload.get("source_record_ids", payload.get("source_refs", ()))
        if not isinstance(raw_ids, (list, tuple)):
            raise _BrokerRejection("source_record_ids must be an array")
        return tuple(str(value) for value in raw_ids)

    @classmethod
    def _source_ids_for_receipt(cls, payload: Mapping[str, Any]) -> tuple[str, ...]:
        values: list[str] = []
        for key in ("record_ids", "source_record_ids", "source_refs"):
            raw = payload.get(key, ())
            if isinstance(raw, (list, tuple)):
                values.extend(str(item) for item in raw)
        target = payload.get("target_record_id")
        if isinstance(target, str):
            values.append(target)
        return tuple(dict.fromkeys(values))

    def _validate_source_records(self, record_ids: tuple[str, ...], scope: ContextScope) -> None:
        # FIX-6: a SECRET same-scope record referenced as source must not be
        # silently carried into a PUBLIC candidate. Source records must satisfy
        # the snapshot's allowed sensitivities and be non-tombstoned + visible.
        lifecycle_filters = self.snapshot.query_lifecycle_states
        for record_id in record_ids:
            _, record = self._find_record(record_id, {})
            if not self._record_visible(scope, record):
                raise _BrokerRejection("cross-scope source record id rejected", considered=record_ids)
            if record.activation_state is ActivationState.TOMBSTONED:
                raise _BrokerRejection("source record is tombstoned", considered=record_ids)
            if record.lifecycle_state not in lifecycle_filters:
                raise _BrokerRejection(
                    "source record is outside the snapshot lifecycle filters",
                    considered=record_ids,
                )
            if record.sensitivity not in self.snapshot.allowed_sensitivities:
                raise _BrokerRejection(
                    "redaction violation: source record sensitivity exceeds snapshot policy",
                    considered=record_ids,
                    redaction_results=("sensitivity:rejected",),
                )

    def _find_record(self, record_id: str, payload: Mapping[str, Any]) -> tuple[MemoryStore, MemoryRecord]:
        selected_store = payload.get("store")
        matches: list[tuple[str, MemoryStore, MemoryRecord]] = []
        for name, store in self._stores.items():
            if selected_store is not None and str(selected_store) != name:
                continue
            record = store.get(record_id)
            if record is not None:
                matches.append((name, store, record))
        if not matches:
            raise _BrokerRejection(f"unknown record id {record_id!r}")
        if len(matches) > 1:
            raise _BrokerRejection(f"ambiguous record id {record_id!r}; select a store")
        return matches[0][1], matches[0][2]

    def _store_for_kind(self, kind: MemoryKind, payload: Mapping[str, Any]) -> MemoryStore:
        selected_store = payload.get("store")
        if selected_store is not None:
            store = self._stores.get(str(selected_store))
            if store is None:
                raise _BrokerRejection(f"unknown memory store {selected_store!r}")
            if store.default_kind is not None and store.default_kind is not kind:
                raise _BrokerRejection("selected store kind does not match candidate kind")
            return store
        matches = [
            store
            for name, store in self._stores.items()
            if name == kind.value or store.default_kind is kind or store.default_kind is None
        ]
        if len(matches) != 1:
            raise _BrokerRejection(f"candidate kind {kind.value!r} does not resolve to exactly one store")
        return matches[0]

    @staticmethod
    def _required_kind(value: Any) -> MemoryKind:
        try:
            return value if isinstance(value, MemoryKind) else MemoryKind(value)
        except ValueError as exc:
            raise _BrokerRejection("candidate kind is not a memory kind") from exc

    @classmethod
    def _optional_kind(cls, value: Any) -> MemoryKind | None:
        return None if value is None else cls._required_kind(value)

    @staticmethod
    def _confidence(payload: Mapping[str, Any]) -> float:
        value = payload.get("confidence", 1.0)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0.0 <= value <= 1.0:
            raise _BrokerRejection("confidence must be between zero and one")
        return float(value)

    def _lifecycle_filters(
        self,
        payload: Mapping[str, Any],
        *,
        reject_invalid: bool = True,
    ) -> tuple[LifecycleState, ...]:
        raw = payload.get("lifecycle_filters", self.snapshot.query_lifecycle_states)
        if not isinstance(raw, (list, tuple)):
            return () if not reject_invalid else self._raise_lifecycle_filters()
        try:
            values = tuple(item if isinstance(item, LifecycleState) else LifecycleState(item) for item in raw)
        except ValueError:
            return () if not reject_invalid else self._raise_lifecycle_filters()
        return values

    @staticmethod
    def _raise_lifecycle_filters():
        raise _BrokerRejection("lifecycle_filters contain an unknown lifecycle state")

    def _high_water_marks(self, scope: ContextScope) -> tuple[tuple[str, int], ...]:
        marks: list[tuple[str, int]] = []
        for name, store in self._stores.items():
            records = [
                record
                for record in store.list(project_id=scope.project_id, include_tombstoned=True)
                if self._record_visible(scope, record)
            ]
            marks.append((name, max((record.creation_seq for record in records), default=-1)))
        return tuple(marks)

    @staticmethod
    def _tokens(value: str) -> int:
        return len(value.split())

    @staticmethod
    def _normalize_stores(
        stores: Mapping[str | MemoryKind, MemoryStore] | Iterable[MemoryStore] | MemoryStore,
    ) -> dict[str, MemoryStore]:
        if isinstance(stores, MemoryStore):
            values = [(stores.default_kind.value if stores.default_kind else "memory", stores)]
        elif isinstance(stores, Mapping):
            values = [
                (key.value if isinstance(key, MemoryKind) else str(key), store)
                for key, store in stores.items()
            ]
        else:
            values = []
            for index, store in enumerate(stores):
                name = store.default_kind.value if store.default_kind else f"memory-{index}"
                values.append((name, store))
        if not values or any(not isinstance(store, MemoryStore) for _, store in values):
            raise TypeError("stores must contain at least one MemoryStore")
        result = dict(sorted(values, key=lambda item: item[0]))
        if len(result) != len(values):
            raise ValueError("memory store names must be unique")
        return result
