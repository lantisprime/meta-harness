"""Frozen, self-hashed contracts for the native discovery kernel (META-7).

Every model here is append-only evidence: campaigns, assignments, lifecycle
events, and receipts are immutable once constructed, and each self-verifies
its own `*_hash` field against a canonical (sort-keys, no-whitespace) JSON
dump of its own fields. Canonical stable ordering is enforced by sorting
order-insensitive collections (tuples of scalars or sub-models) in a
`mode="before"` validator, so two callers who supply the same set of
elements in different insertion orders build byte-identical models and
hashes.

Nothing here mints authority: `DiscoveryBoundary` hard-rejects any attempt to
grant promotion/deployment/evaluator-write/memory-activation/weight-training/
permission-expansion, `DiscoveryCampaignManifest` rejects a proxy evaluator
reference that conflates with the protected evaluator version, and
`DiscoveryTerminalReceipt` requires an honest closest-protected-result +
unresolved-gap report on every non-omitted terminal path.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Callable, Literal

from pydantic import Field, model_validator

from metaharness.context.models import (
    SHA256_PATTERN,
    ContextVersionBindings,
    FrozenModel,
    _contains_traversal_segment,
    content_hash,
)

GIT_SHA_PATTERN = r"^[0-9a-f]{40}$"
_HASH_PLACEHOLDER = "sha256:" + "0" * 64


def _self_verifying(data: Any, handler: Callable[[Any], Any], hash_field: str, mismatch: str) -> Any:
    """Shared wrap-validator body: auto-fill `hash_field` when omitted, verify (tamper-check) when supplied.

    Mirrors the `_self_verifying_model` idiom in `metaharness.memory.broker` —
    `mode="wrap"` runs the rest of the validation chain (before/field/after
    validators) via `handler(values)` first, then computes the canonical hash
    from the resulting model's own dump, so business-rule `after` validators
    never need to reason about the hash field themselves.
    """

    supplied = not isinstance(data, dict) or bool(data.get(hash_field))
    values = data
    if isinstance(data, dict) and not supplied:
        values = dict(data)
        values[hash_field] = _HASH_PLACEHOLDER
    model = handler(values)
    expected = content_hash(model.model_dump(mode="json", exclude={hash_field}))
    if supplied:
        if getattr(model, hash_field) != expected:
            raise ValueError(mismatch)
    else:
        object.__setattr__(model, hash_field, expected)
    return model


def _dump(value: Any) -> Any:
    """Best-effort canonical dict for sort-key derivation: FrozenModel or dict pass through, else identity."""

    if isinstance(value, FrozenModel):
        return value.model_dump(mode="json")
    return value


def _sort_unique_scalars(values: Any) -> Any:
    """Sort + dedupe a sequence of scalars for canonical, insertion-order-independent hashing."""

    if not isinstance(values, (list, tuple)):
        return values
    return tuple(sorted(dict.fromkeys(values)))


def _sort_submodels(values: Any, key_fields: tuple[str, ...]) -> Any:
    """Sort a sequence of dict/model entries by a tuple of field values, order-independent."""

    if not isinstance(values, (list, tuple)):
        return values
    items = list(values)

    def key(item: Any) -> tuple[Any, ...]:
        data = _dump(item) if not isinstance(item, dict) else item
        return tuple(data.get(field) for field in key_fields)

    return tuple(sorted(items, key=key))


# ---------------------------------------------------------------------------
# Model portfolio + boundary + budgets
# ---------------------------------------------------------------------------


class DiscoveryModelPortfolioEntry(FrozenModel):
    schema_version: Literal[1] = 1
    role: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    model_version: str = Field(min_length=1)


class DiscoveryBoundary(FrozenModel):
    """The permission envelope a campaign's workers execute inside.

    The six authority flags exist only so an attempt to grant them is a
    detectable, rejected construction — bounded authority (charter
    invariant 7) means this model can never carry `True` for any of them.
    """

    workspace_root: str = Field(min_length=1)
    network_allowed: bool = False
    allowed_tools: tuple[str, ...] = ()
    # The changed-path boundary a lineage's checkpoint/child-commit must stay
    # inside (relative prefixes under a lineage worktree root). Empty means
    # this campaign has declared no restriction. `LineageWorkspaceManager`
    # enforces this BEFORE any git staging/commit mutation — see
    # `create_lineage(allowed_changed_paths=...)`.
    allowed_changed_path_prefixes: tuple[str, ...] = ()
    can_promote: bool = False
    can_deploy: bool = False
    can_write_evaluator: bool = False
    can_activate_memory: bool = False
    can_train_weights: bool = False
    can_expand_permissions: bool = False

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, data: Any) -> Any:
        if isinstance(data, dict) and "allowed_tools" in data:
            data = {**data, "allowed_tools": _sort_unique_scalars(data["allowed_tools"])}
        if isinstance(data, dict) and "allowed_changed_path_prefixes" in data:
            data = {
                **data,
                "allowed_changed_path_prefixes": _sort_unique_scalars(data["allowed_changed_path_prefixes"]),
            }
        return data

    @model_validator(mode="after")
    def _validate(self) -> "DiscoveryBoundary":
        if any(
            [
                self.can_promote,
                self.can_deploy,
                self.can_write_evaluator,
                self.can_activate_memory,
                self.can_train_weights,
                self.can_expand_permissions,
            ]
        ):
            raise ValueError(
                "discovery boundary cannot grant promotion, deployment, "
                "evaluator-write, memory-activation, weight-training, or "
                "permission-expansion authority"
            )
        if _contains_traversal_segment(self.workspace_root):
            raise ValueError("workspace_root must not contain path-traversal segments")
        if not self.workspace_root.startswith("/"):
            raise ValueError("workspace_root must be an absolute path")
        for prefix in self.allowed_changed_path_prefixes:
            if not prefix or prefix.startswith("/") or _contains_traversal_segment(prefix):
                raise ValueError(
                    f"allowed_changed_path_prefixes entry {prefix!r} must be a "
                    "non-empty, relative, traversal-free path prefix"
                )
        return self


class DiscoveryBudgets(FrozenModel):
    max_concurrency: int = Field(ge=1)
    max_restarts_per_attempt: int = Field(ge=0)
    max_attempts: int = Field(ge=1)
    max_evaluations: int = Field(ge=1)
    max_wall_seconds: int = Field(ge=1)
    attempt_timeout_seconds: int = Field(ge=1)


class DiscoveryStopCondition(str, Enum):
    GOAL_REACHED = "goal_reached"
    PLATEAU = "plateau"
    OSCILLATION = "oscillation"
    REGRESSION = "regression"
    FORGETTING = "forgetting"
    SAFETY = "safety"
    VALIDITY = "validity"
    COMPUTE_EXHAUSTED = "compute_exhausted"
    DATA_EXHAUSTED = "data_exhausted"
    WALL_TIME_EXCEEDED = "wall_time_exceeded"


# ---------------------------------------------------------------------------
# Campaign manifest
# ---------------------------------------------------------------------------


class DiscoveryCampaignManifest(FrozenModel):
    schema_version: Literal[1] = 1
    campaign_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    baseline_commit: str = Field(pattern=GIT_SHA_PATTERN)
    baseline_tree: str = Field(pattern=GIT_SHA_PATTERN)
    versions: ContextVersionBindings
    model_portfolio: tuple[DiscoveryModelPortfolioEntry, ...] = Field(min_length=1)
    proxy_evaluator_ref: str = Field(min_length=1)
    boundary: DiscoveryBoundary
    budgets: DiscoveryBudgets
    seed: int = Field(ge=0)
    stop_conditions: tuple[DiscoveryStopCondition, ...] = Field(min_length=1)
    report_unresolved_gap: bool = True
    manifest_hash: str = Field(default="", pattern=SHA256_PATTERN)

    @model_validator(mode="wrap")
    @classmethod
    def _verify_hash(cls, data: Any, handler: Callable[[Any], Any]) -> "DiscoveryCampaignManifest":
        return _self_verifying(data, handler, "manifest_hash", "manifest_hash mismatch")

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        data = dict(data)
        if "model_portfolio" in data:
            data["model_portfolio"] = _sort_submodels(
                data["model_portfolio"], ("role", "model_id", "model_version")
            )
        if "stop_conditions" in data:
            raw = data["stop_conditions"]
            normalized = [
                value.value if isinstance(value, DiscoveryStopCondition) else value
                for value in raw
            ]
            data["stop_conditions"] = _sort_unique_scalars(normalized)
        return data

    @model_validator(mode="after")
    def _validate(self) -> "DiscoveryCampaignManifest":
        if not self.report_unresolved_gap:
            raise ValueError(
                "report_unresolved_gap must stay True (honest termination, "
                "charter invariant 9): closest protected result and "
                "unresolved gap reporting cannot be disabled"
            )
        if self.proxy_evaluator_ref == self.versions.evaluator_version:
            raise ValueError(
                "proxy_evaluator_ref must not conflate with the protected "
                "evaluator identity (versions.evaluator_version)"
            )
        if self.versions.candidate_version != self.baseline_commit:
            raise ValueError(
                "versions.candidate_version must equal baseline_commit for "
                "the campaign's root manifest identity"
            )
        if self.versions.parent_candidate_version is not None:
            raise ValueError(
                "a campaign manifest's version bindings describe the "
                "baseline candidate and must not carry a parent"
            )
        return self


# ---------------------------------------------------------------------------
# Assignments
# ---------------------------------------------------------------------------


class DiscoveryRole(str, Enum):
    EXPLORER = "explorer"
    OPTIMIZER = "optimizer"


class DiscoveryAssignment(FrozenModel):
    schema_version: Literal[1] = 1
    assignment_id: str = Field(min_length=1)
    campaign_id: str = Field(min_length=1)
    lineage_id: str = Field(min_length=1)
    attempt_id: str = Field(min_length=1)
    role: DiscoveryRole
    parent_lineage_id: str | None = Field(default=None, min_length=1)
    parent_attempt_id: str | None = Field(default=None, min_length=1)
    seed: int = Field(ge=0)
    sequence: int = Field(ge=0)
    created_at: int = Field(ge=0)
    assignment_hash: str = Field(default="", pattern=SHA256_PATTERN)

    @model_validator(mode="wrap")
    @classmethod
    def _verify_hash(cls, data: Any, handler: Callable[[Any], Any]) -> "DiscoveryAssignment":
        return _self_verifying(data, handler, "assignment_hash", "assignment_hash mismatch")

    @model_validator(mode="after")
    def _validate(self) -> "DiscoveryAssignment":
        if self.role is DiscoveryRole.OPTIMIZER and self.parent_lineage_id is None:
            raise ValueError("an optimizer assignment requires a parent_lineage_id")
        if (self.parent_lineage_id is None) != (self.parent_attempt_id is None):
            raise ValueError(
                "parent_lineage_id and parent_attempt_id must be set together"
            )
        return self


# ---------------------------------------------------------------------------
# Campaign / attempt lifecycle
# ---------------------------------------------------------------------------


class CampaignState(str, Enum):
    PREPARED = "prepared"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    COMPLETED = "completed"


class AttemptState(str, Enum):
    PREPARED = "prepared"
    RUNNING = "running"
    CHECKPOINTED = "checkpointed"
    PROXY_EVALUATING = "proxy_evaluating"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    CRASHED = "crashed"
    INTERRUPTED = "interrupted"


_ATTEMPT_TERMINAL_STATES = frozenset(
    {
        AttemptState.COMPLETED,
        AttemptState.FAILED,
        AttemptState.TIMED_OUT,
        AttemptState.CANCELLED,
        AttemptState.CRASHED,
    }
)


def is_attempt_terminal(state: AttemptState) -> bool:
    return state in _ATTEMPT_TERMINAL_STATES


# ---------------------------------------------------------------------------
# Journal events
# ---------------------------------------------------------------------------


class DiscoveryEventType(str, Enum):
    CAMPAIGN_PREPARED = "campaign_prepared"
    CAMPAIGN_STOP_REQUESTED = "campaign_stop_requested"
    CAMPAIGN_STOPPED = "campaign_stopped"
    ATTEMPT_SUBMIT_INTENT = "attempt_submit_intent"
    ATTEMPT_SUBMIT_OUTCOME = "attempt_submit_outcome"
    ATTEMPT_LAUNCH_INTENT = "attempt_launch_intent"
    ATTEMPT_LAUNCH_OUTCOME = "attempt_launch_outcome"
    ATTEMPT_CHECKPOINT_INTENT = "attempt_checkpoint_intent"
    ATTEMPT_CHECKPOINT_OUTCOME = "attempt_checkpoint_outcome"
    ATTEMPT_EVALUATE_INTENT = "attempt_evaluate_intent"
    ATTEMPT_EVALUATE_OUTCOME = "attempt_evaluate_outcome"
    ATTEMPT_KNOWLEDGE_APPEND_INTENT = "attempt_knowledge_append_intent"
    ATTEMPT_KNOWLEDGE_APPEND_OUTCOME = "attempt_knowledge_append_outcome"
    ATTEMPT_TERMINAL = "attempt_terminal"
    ATTEMPT_INTERRUPTED = "attempt_interrupted"


class DiscoveryEvent(FrozenModel):
    schema_version: Literal[1] = 1
    event_id: str = Field(min_length=1)
    campaign_id: str = Field(min_length=1)
    campaign_manifest_hash: str = Field(pattern=SHA256_PATTERN)
    attempt_id: str | None = Field(default=None, min_length=1)
    event_type: DiscoveryEventType
    sequence: int = Field(ge=0)
    observed_at: int = Field(ge=0)
    payload: dict[str, Any] = Field(default_factory=dict)
    event_hash: str = Field(default="", pattern=SHA256_PATTERN)

    @model_validator(mode="wrap")
    @classmethod
    def _verify_hash(cls, data: Any, handler: Callable[[Any], Any]) -> "DiscoveryEvent":
        return _self_verifying(data, handler, "event_hash", "event_hash mismatch")


# ---------------------------------------------------------------------------
# Resource / terminal receipts
# ---------------------------------------------------------------------------


class DiscoveryResourceReceipt(FrozenModel):
    schema_version: Literal[1] = 1
    receipt_id: str = Field(min_length=1)
    campaign_id: str = Field(min_length=1)
    attempt_id: str = Field(min_length=1)
    sequence: int = Field(ge=0)
    wall_seconds: float = Field(ge=0.0)
    evaluations_used: int = Field(ge=0)
    restarts_used: int = Field(ge=0)
    receipt_hash: str = Field(default="", pattern=SHA256_PATTERN)

    @model_validator(mode="wrap")
    @classmethod
    def _verify_hash(cls, data: Any, handler: Callable[[Any], Any]) -> "DiscoveryResourceReceipt":
        return _self_verifying(data, handler, "receipt_hash", "receipt_hash mismatch")


class DiscoveryTerminalOutcome(str, Enum):
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    CRASHED = "crashed"
    WORKER_ERROR = "worker_error"
    EVALUATOR_ERROR = "evaluator_error"
    LATE_RESULT_DISCARDED = "late_result_discarded"
    OMITTED = "omitted"


class DiscoveryTerminalReceipt(FrozenModel):
    schema_version: Literal[1] = 1
    receipt_id: str = Field(min_length=1)
    campaign_id: str = Field(min_length=1)
    lineage_id: str = Field(min_length=1)
    attempt_id: str = Field(min_length=1)
    sequence: int = Field(ge=0)
    outcome: DiscoveryTerminalOutcome
    resource_receipt_id: str = Field(min_length=1)
    closest_protected_result: str | None = Field(default=None, min_length=1)
    unresolved_gap: str | None = Field(default=None, min_length=1)
    omission_reason: str | None = Field(default=None, min_length=1)
    receipt_hash: str = Field(default="", pattern=SHA256_PATTERN)

    @model_validator(mode="wrap")
    @classmethod
    def _verify_hash(cls, data: Any, handler: Callable[[Any], Any]) -> "DiscoveryTerminalReceipt":
        return _self_verifying(data, handler, "receipt_hash", "receipt_hash mismatch")

    @model_validator(mode="after")
    def _validate(self) -> "DiscoveryTerminalReceipt":
        if self.outcome is DiscoveryTerminalOutcome.OMITTED:
            if self.omission_reason is None:
                raise ValueError("an omitted terminal receipt requires omission_reason")
            if self.closest_protected_result is not None or self.unresolved_gap is not None:
                raise ValueError(
                    "an omitted terminal receipt cannot also carry a result/gap"
                )
        else:
            if self.omission_reason is not None:
                raise ValueError(
                    "a non-omitted terminal receipt cannot carry omission_reason"
                )
            if self.closest_protected_result is None or self.unresolved_gap is None:
                raise ValueError(
                    "every non-omitted terminal receipt must report the "
                    "closest protected result and unresolved gap (honest "
                    "termination, charter invariant 9)"
                )
        return self


# ---------------------------------------------------------------------------
# Lineage receipts
# ---------------------------------------------------------------------------


class DiscoveryLineageEventType(str, Enum):
    CREATED = "created"
    CHECKPOINTED = "checkpointed"
    CHILD_COMMITTED = "child_committed"
    RECOVERED = "recovered"
    QUARANTINED = "quarantined"


class DiscoveryLineageReceipt(FrozenModel):
    schema_version: Literal[1] = 1
    receipt_id: str = Field(min_length=1)
    campaign_id: str = Field(min_length=1)
    lineage_id: str = Field(min_length=1)
    attempt_id: str | None = Field(default=None, min_length=1)
    event_type: DiscoveryLineageEventType
    parent_lineage_id: str | None = Field(default=None, min_length=1)
    parent_commit: str | None = Field(default=None, pattern=GIT_SHA_PATTERN)
    tree_hash: str | None = Field(default=None, pattern=GIT_SHA_PATTERN)
    commit_hash: str | None = Field(default=None, pattern=GIT_SHA_PATTERN)
    branch_name: str = Field(min_length=1)
    worktree_path: str = Field(min_length=1)
    sequence: int = Field(ge=0)
    detail: str = ""
    # The campaign's declared changed-path boundary this lineage was created
    # under (relative prefixes under the worktree root). Empty means no
    # restriction. Durable and immutable per lineage (META-7 pre-commit fix
    # brief #9, F2) — every receipt for a lineage restates the SAME value
    # its CREATED receipt declared, so `recover()` can positively
    # reconstruct it rather than defaulting to unrestricted.
    allowed_changed_paths: tuple[str, ...] = ()
    receipt_hash: str = Field(default="", pattern=SHA256_PATTERN)

    @model_validator(mode="wrap")
    @classmethod
    def _verify_hash(cls, data: Any, handler: Callable[[Any], Any]) -> "DiscoveryLineageReceipt":
        return _self_verifying(data, handler, "receipt_hash", "receipt_hash mismatch")

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, data: Any) -> Any:
        if isinstance(data, dict) and "allowed_changed_paths" in data:
            data = {**data, "allowed_changed_paths": _sort_unique_scalars(data["allowed_changed_paths"])}
        return data

    @model_validator(mode="after")
    def _validate(self) -> "DiscoveryLineageReceipt":
        for prefix in self.allowed_changed_paths:
            if not prefix or prefix.startswith("/") or _contains_traversal_segment(prefix):
                raise ValueError(
                    f"allowed_changed_paths entry {prefix!r} must be a non-empty, "
                    "relative, traversal-free path prefix"
                )
        if self.parent_lineage_id is not None and self.parent_commit is None:
            raise ValueError("parent_lineage_id requires parent_commit")
        if (
            self.event_type
            in (
                DiscoveryLineageEventType.CREATED,
                DiscoveryLineageEventType.CHECKPOINTED,
                DiscoveryLineageEventType.CHILD_COMMITTED,
            )
            and self.parent_commit is None
        ):
            raise ValueError(
                f"{self.event_type.value} receipts must record the exact "
                "commit this lineage was branched from (baseline or parent "
                "lineage head)"
            )
        if self.event_type in (
            DiscoveryLineageEventType.CHECKPOINTED,
            DiscoveryLineageEventType.CHILD_COMMITTED,
        ) and self.commit_hash is None:
            raise ValueError(f"{self.event_type.value} receipts require commit_hash")
        if _contains_traversal_segment(self.worktree_path):
            raise ValueError("worktree_path must not contain path-traversal segments")
        if not self.worktree_path.startswith("/"):
            raise ValueError("worktree_path must be an absolute path")
        return self
