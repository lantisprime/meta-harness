"""Deterministic, validation-gated evolution of declarative search policies.

`SearchPolicyEvolver` owns policy-controller state only: the current immutable
policy snapshot, append-only strategy history, and append-only activation
receipts.  It deliberately has no candidate-population state, evaluator
authority, deployment pointer, memory activation, weight-training capability,
or I/O seam.  Failed policy validation therefore leaves the parent active and
cannot reset or otherwise touch the candidate population.
"""
from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Callable, Literal

from pydantic import Field, ValidationError, model_validator

from metaharness.context.models import SHA256_PATTERN, FrozenModel, content_hash
from metaharness.discovery.models import _self_verifying
from metaharness.discovery.policy import (
    BoundedIdentifier,
    PolicyValidationReceipt,
    PolicyValidationStage,
    PolicyValidationVerdict,
    SearchPolicyDSL,
    SearchPolicySnapshot,
    validate_policy,
)
from metaharness.discovery.population import PopulationDescriptor

HashString = Annotated[str, Field(pattern=SHA256_PATTERN)]


class EvolutionError(ValueError):
    """A policy evolution operation failed closed without changing state."""


class StrategyHistoryOutcome(str, Enum):
    ACTIVATED = "activated"
    REJECTED_SCHEMA = "rejected_schema"
    REJECTED_STATIC = "rejected_static"
    REJECTED_SIMULATION = "rejected_simulation"
    REJECTED_SHADOW = "rejected_shadow"
    ROLLED_BACK = "rolled_back"


class PolicyWindowScore(FrozenModel):
    """Deterministic score change across one frozen policy window."""

    schema_version: Literal[1] = 1
    window_id: BoundedIdentifier
    policy_hash: str = Field(pattern=SHA256_PATTERN)
    descriptor_hash_before: str = Field(pattern=SHA256_PATTERN)
    descriptor_hash_after: str = Field(pattern=SHA256_PATTERN)
    observed_progress: float = Field(
        ge=-1.0,
        le=1.0,
        allow_inf_nan=False,
    )
    # Required by the stagnation predicate; copied from descriptor_after.
    steps_since_meaningful_improvement: int = Field(ge=0)
    attempts_run: int = Field(ge=0)
    cost_spent: float = Field(ge=0.0, allow_inf_nan=False)
    score_hash: str = Field(default="", pattern=SHA256_PATTERN)

    @model_validator(mode="wrap")
    @classmethod
    def _verify_hash(
        cls, data: Any, handler: Callable[[Any], Any]
    ) -> "PolicyWindowScore":
        return _self_verifying(data, handler, "score_hash", "score_hash mismatch")


class StrategyHistoryRow(FrozenModel):
    """Append-only outcome for one policy consideration or rollback."""

    schema_version: Literal[1] = 1
    sequence: int = Field(ge=0)
    policy_hash: str = Field(pattern=SHA256_PATTERN)
    parent_policy_hash: str | None = Field(default=None, pattern=SHA256_PATTERN)
    window_score_hash: str = Field(pattern=SHA256_PATTERN)
    outcome: StrategyHistoryOutcome
    reason: str = Field(min_length=1, max_length=512)
    row_hash: str = Field(default="", pattern=SHA256_PATTERN)

    @model_validator(mode="wrap")
    @classmethod
    def _verify_hash(
        cls, data: Any, handler: Callable[[Any], Any]
    ) -> "StrategyHistoryRow":
        return _self_verifying(data, handler, "row_hash", "row_hash mismatch")

    @model_validator(mode="after")
    def _validate_lineage(self) -> "StrategyHistoryRow":
        if self.parent_policy_hash == self.policy_hash:
            raise ValueError("a strategy-history row cannot name itself as parent")
        return self


class PolicyActivationReceipt(FrozenModel):
    """Structural proof that all four policy-validation stages passed.

    Full immutable stage receipts are retained so their PASSED verdicts and
    stage identities can be checked at model construction.  Their exact hashes
    are also stored explicitly as the activation dependency tuple.
    """

    schema_version: Literal[1] = 1
    policy_hash: str = Field(pattern=SHA256_PATTERN)
    validation_receipts: tuple[PolicyValidationReceipt, ...]
    validation_receipt_hashes: tuple[HashString, ...]
    activated_for_window: BoundedIdentifier
    activated_sequence: int = Field(ge=0)
    actor_label: BoundedIdentifier
    activation_hash: str = Field(default="", pattern=SHA256_PATTERN)

    @model_validator(mode="wrap")
    @classmethod
    def _verify_hash(
        cls, data: Any, handler: Callable[[Any], Any]
    ) -> "PolicyActivationReceipt":
        return _self_verifying(
            data,
            handler,
            "activation_hash",
            "activation_hash mismatch",
        )

    @model_validator(mode="after")
    def _validate_stage_receipts(self) -> "PolicyActivationReceipt":
        expected_stages = tuple(PolicyValidationStage)
        if len(self.validation_receipts) != len(expected_stages):
            raise ValueError(
                "activation requires exactly four policy-validation receipts"
            )

        verified_receipts: list[PolicyValidationReceipt] = []
        for receipt in self.validation_receipts:
            try:
                verified = PolicyValidationReceipt.model_validate(
                    receipt.model_dump(mode="json")
                )
            except ValidationError as exc:
                raise ValueError(
                    "activation validation receipt failed self-hash validation"
                ) from exc
            verified_receipts.append(verified)

        stages = tuple(receipt.stage for receipt in verified_receipts)
        if stages != expected_stages:
            raise ValueError(
                "activation receipts must be ordered SCHEMA, STATIC, "
                "SIMULATION, SHADOW"
            )
        if any(
            receipt.verdict is not PolicyValidationVerdict.PASSED
            for receipt in verified_receipts
        ):
            raise ValueError("activation requires PASSED verdicts at all four stages")
        if any(
            receipt.policy_hash != self.policy_hash
            for receipt in verified_receipts
        ):
            raise ValueError(
                "every activation validation receipt must judge policy_hash"
            )

        receipt_hashes = tuple(
            receipt.receipt_hash for receipt in verified_receipts
        )
        if self.validation_receipt_hashes != receipt_hashes:
            raise ValueError(
                "validation_receipt_hashes must exactly match the four stage receipts"
            )
        if (
            verified_receipts[2].descriptor_hash
            != verified_receipts[3].descriptor_hash
        ):
            raise ValueError(
                "SIMULATION and SHADOW receipts must bind the same descriptor"
            )
        return self


class SearchPolicyEvolver:
    """Small deterministic controller for bounded policy activation/rollback.

    This class contains no candidate population and has no method capable of
    mutating one.  Policy activation is an internal H-plane controller choice,
    not evaluator approval, release promotion, or deployment authority.
    """

    _ACTOR_LABEL = "policy-validation-gate"

    def __init__(self, root: SearchPolicySnapshot):
        try:
            root = SearchPolicySnapshot.model_validate(
                root.model_dump(mode="json")
            )
        except (AttributeError, ValidationError) as exc:
            raise EvolutionError("root policy failed self-hash validation") from exc
        if root.parent_policy_id is not None:
            raise EvolutionError("root policy must not name a parent_policy_id")

        self._current = root
        self._strategy_history: tuple[StrategyHistoryRow, ...] = ()
        self._activation_receipts: tuple[PolicyActivationReceipt, ...] = ()
        # Frozen snapshots in this tuple are controller lineage only, never
        # candidate-population state.  Root is known but is not rollback-
        # eligible until an ACTIVATED history row exists for the target hash.
        self._known_snapshots: tuple[SearchPolicySnapshot, ...] = (root,)

    @property
    def current(self) -> SearchPolicySnapshot:
        return self._current

    @property
    def strategy_history(self) -> tuple[StrategyHistoryRow, ...]:
        return self._strategy_history

    @property
    def activation_receipts(self) -> tuple[PolicyActivationReceipt, ...]:
        return self._activation_receipts

    @staticmethod
    def _validated_descriptor(
        descriptor: PopulationDescriptor,
    ) -> PopulationDescriptor:
        try:
            return PopulationDescriptor.model_validate(
                descriptor.model_dump(mode="json")
            )
        except (AttributeError, ValidationError) as exc:
            raise EvolutionError(
                "population descriptor failed self-hash validation"
            ) from exc

    def _validate_sequence(self, sequence: int) -> None:
        if sequence < 0:
            raise EvolutionError("sequence must be non-negative")
        if (
            self._strategy_history
            and sequence <= self._strategy_history[-1].sequence
        ):
            raise EvolutionError(
                "strategy-history sequence must increase monotonically"
            )

    def score_window(
        self,
        window_id: str,
        descriptor_before: PopulationDescriptor,
        descriptor_after: PopulationDescriptor,
        *,
        attempts_run: int,
        cost_spent: float,
    ) -> PolicyWindowScore:
        """Purely compute normalized progress; do not mutate evolver state."""

        before = self._validated_descriptor(descriptor_before)
        after = self._validated_descriptor(descriptor_after)
        if before.campaign_id != after.campaign_id:
            raise EvolutionError(
                "before and after descriptors must belong to one campaign"
            )
        if after.campaign_id != self._current.campaign_id:
            raise EvolutionError(
                "window descriptors must belong to the active policy campaign"
            )
        if after.window_id != window_id:
            raise EvolutionError(
                "descriptor_after.window_id must equal the scored window_id"
            )

        normalized_deltas = []
        for before_value, after_value in zip(
            (
                before.best_score,
                before.frontier_score,
                before.window_score_mean,
            ),
            (
                after.best_score,
                after.frontier_score,
                after.window_score_mean,
            ),
            strict=True,
        ):
            scale = max(abs(before_value), abs(after_value), 1.0)
            normalized_deltas.append((after_value - before_value) / scale)
        observed_progress = sum(normalized_deltas) / len(normalized_deltas)
        if observed_progress == 0.0:
            observed_progress = 0.0  # canonicalize negative zero

        return PolicyWindowScore(
            window_id=window_id,
            policy_hash=self._current.policy_hash,
            descriptor_hash_before=before.descriptor_hash,
            descriptor_hash_after=after.descriptor_hash,
            observed_progress=observed_progress,
            steps_since_meaningful_improvement=(
                after.steps_since_meaningful_improvement
            ),
            attempts_run=attempts_run,
            cost_spent=cost_spent,
        )

    @staticmethod
    def is_stagnant(
        score: PolicyWindowScore,
        *,
        stagnation_window: int,
    ) -> bool:
        """Return true only at/after the window boundary with no progress."""

        if stagnation_window <= 0:
            raise EvolutionError("stagnation_window must be positive")
        try:
            score = PolicyWindowScore.model_validate(
                score.model_dump(mode="json")
            )
        except (AttributeError, ValidationError) as exc:
            raise EvolutionError("window score failed self-hash validation") from exc
        return (
            score.steps_since_meaningful_improvement >= stagnation_window
            and score.observed_progress <= 0.0
        )

    def propose_child(
        self,
        candidate_dsl: SearchPolicyDSL,
        *,
        window_id: str,
        sequence: int,
    ) -> SearchPolicySnapshot:
        """Build a deterministic child of current; do not score or activate it."""

        if sequence < 0:
            raise EvolutionError("sequence must be non-negative")
        try:
            candidate_dsl = SearchPolicyDSL.model_validate(
                candidate_dsl.model_dump(mode="json")
            )
        except (AttributeError, ValidationError) as exc:
            raise EvolutionError("candidate DSL failed schema validation") from exc

        child_material = {
            "parent_policy_hash": self._current.policy_hash,
            "policy": candidate_dsl.model_dump(mode="json"),
            "sequence": sequence,
            "window_id": window_id,
        }
        digest = content_hash(child_material).removeprefix("sha256:")[:24]
        return SearchPolicySnapshot(
            policy_id=f"policy-{sequence}-{digest}",
            parent_policy_id=self._current.policy_id,
            campaign_id=self._current.campaign_id,
            policy=candidate_dsl,
            window_id=window_id,
            created_sequence=sequence,
        )

    @staticmethod
    def _consideration_window_score(
        policy_hash: str,
        window: PopulationDescriptor,
    ) -> PolicyWindowScore:
        """Bind validation to a zero-cost, zero-delta frozen score view."""

        return PolicyWindowScore(
            window_id=window.window_id,
            policy_hash=policy_hash,
            descriptor_hash_before=window.descriptor_hash,
            descriptor_hash_after=window.descriptor_hash,
            observed_progress=0.0,
            steps_since_meaningful_improvement=(
                window.steps_since_meaningful_improvement
            ),
            attempts_run=0,
            cost_spent=0.0,
        )

    @staticmethod
    def _failed_stage(
        receipts: tuple[PolicyValidationReceipt, ...],
        policy_hash: str,
    ) -> tuple[PolicyValidationStage | None, str]:
        expected = tuple(PolicyValidationStage)
        for index, stage in enumerate(expected):
            if index >= len(receipts):
                return stage, f"{stage.value} rejected: PASSED receipt missing"
            receipt = receipts[index]
            try:
                receipt = PolicyValidationReceipt.model_validate(
                    receipt.model_dump(mode="json")
                )
            except (AttributeError, ValidationError):
                return stage, f"{stage.value} rejected: receipt hash invalid"
            if receipt.stage is not stage:
                return stage, f"{stage.value} rejected: ordered receipt missing"
            if receipt.policy_hash != policy_hash:
                return stage, f"{stage.value} rejected: receipt policy mismatch"
            if receipt.verdict is not PolicyValidationVerdict.PASSED:
                return stage, receipt.reason
        if len(receipts) != len(expected):
            return (
                PolicyValidationStage.SHADOW,
                "shadow rejected: unexpected surplus validation receipt",
            )
        return None, "all four validation stages passed"

    @staticmethod
    def _rejection_outcome(
        stage: PolicyValidationStage,
    ) -> StrategyHistoryOutcome:
        return {
            PolicyValidationStage.SCHEMA: StrategyHistoryOutcome.REJECTED_SCHEMA,
            PolicyValidationStage.STATIC: StrategyHistoryOutcome.REJECTED_STATIC,
            PolicyValidationStage.SIMULATION: (
                StrategyHistoryOutcome.REJECTED_SIMULATION
            ),
            PolicyValidationStage.SHADOW: StrategyHistoryOutcome.REJECTED_SHADOW,
        }[stage]

    def consider(
        self,
        candidate: SearchPolicySnapshot,
        *,
        window: PopulationDescriptor,
        sequence: int,
    ) -> StrategyHistoryRow:
        """Validate and either activate candidate or retain the exact parent."""

        self._validate_sequence(sequence)
        parent = self._current
        receipts = validate_policy(candidate, parent=parent, window=window)
        failed_stage, reason = self._failed_stage(receipts, candidate.policy_hash)
        window_score = self._consideration_window_score(
            parent.policy_hash,
            window,
        )

        if failed_stage is not None:
            row = StrategyHistoryRow(
                sequence=sequence,
                policy_hash=candidate.policy_hash,
                parent_policy_hash=parent.policy_hash,
                window_score_hash=window_score.score_hash,
                outcome=self._rejection_outcome(failed_stage),
                reason=reason,
            )
            self._strategy_history = (*self._strategy_history, row)
            if self._current != parent:
                raise AssertionError("rejected policy changed the active parent")
            return row

        try:
            activation = PolicyActivationReceipt(
                policy_hash=candidate.policy_hash,
                validation_receipts=receipts,
                validation_receipt_hashes=tuple(
                    receipt.receipt_hash for receipt in receipts
                ),
                activated_for_window=candidate.window_id,
                activated_sequence=sequence,
                actor_label=self._ACTOR_LABEL,
            )
        except ValidationError:
            row = StrategyHistoryRow(
                sequence=sequence,
                policy_hash=candidate.policy_hash,
                parent_policy_hash=parent.policy_hash,
                window_score_hash=window_score.score_hash,
                outcome=StrategyHistoryOutcome.REJECTED_SHADOW,
                reason="shadow rejected: activation receipt validation failed",
            )
            self._strategy_history = (*self._strategy_history, row)
            return row

        row = StrategyHistoryRow(
            sequence=sequence,
            policy_hash=candidate.policy_hash,
            parent_policy_hash=parent.policy_hash,
            window_score_hash=window_score.score_hash,
            outcome=StrategyHistoryOutcome.ACTIVATED,
            reason=(
                "activated after four PASSED policy-validation receipts; "
                "no protected promotion authority granted"
            ),
        )
        self._activation_receipts = (*self._activation_receipts, activation)
        self._known_snapshots = (*self._known_snapshots, candidate)
        self._current = candidate
        self._strategy_history = (*self._strategy_history, row)
        return row

    def _snapshot_by_policy_id(
        self, policy_id: str
    ) -> SearchPolicySnapshot | None:
        return next(
            (
                snapshot
                for snapshot in reversed(self._known_snapshots)
                if snapshot.policy_id == policy_id
            ),
            None,
        )

    def _activated_ancestor_hashes(self) -> set[str]:
        ancestors: set[str] = set()
        cursor = self._current
        while cursor.parent_policy_id is not None:
            parent = self._snapshot_by_policy_id(cursor.parent_policy_id)
            if parent is None:
                raise EvolutionError("active policy lineage is incomplete")
            ancestors.add(parent.policy_hash)
            cursor = parent
        activated_hashes = {
            row.policy_hash
            for row in self._strategy_history
            if row.outcome is StrategyHistoryOutcome.ACTIVATED
        }
        return ancestors & activated_hashes

    def rollback(
        self,
        *,
        to_policy_hash: str,
        sequence: int,
        reason: str,
    ) -> StrategyHistoryRow:
        """Restore a previously activated ancestor without touching population."""

        self._validate_sequence(sequence)
        if to_policy_hash not in self._activated_ancestor_hashes():
            raise EvolutionError(
                "rollback target must be a previously ACTIVATED ancestor"
            )
        target = next(
            (
                snapshot
                for snapshot in reversed(self._known_snapshots)
                if snapshot.policy_hash == to_policy_hash
            ),
            None,
        )
        if target is None:
            raise EvolutionError("rollback target snapshot is unavailable")
        activation_row = next(
            row
            for row in reversed(self._strategy_history)
            if row.outcome is StrategyHistoryOutcome.ACTIVATED
            and row.policy_hash == to_policy_hash
        )
        previous = self._current
        row = StrategyHistoryRow(
            sequence=sequence,
            policy_hash=target.policy_hash,
            parent_policy_hash=previous.policy_hash,
            window_score_hash=activation_row.window_score_hash,
            outcome=StrategyHistoryOutcome.ROLLED_BACK,
            reason=reason,
        )
        self._current = target
        self._strategy_history = (*self._strategy_history, row)
        return row
