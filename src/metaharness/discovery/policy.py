"""Declarative, bounded search-policy contracts and staged validation.

The DSL in this module is the only meta-evolvable search control surface.  It
contains only closed enums, bounded identifiers, finite numbers, and frozen
submodels: executable code, paths, tools, evaluator references, permissions,
and activation or deployment authority are structurally absent.  Validation is
pure and deterministic, and each completed stage emits an immutable receipt.
SIMULATION is a deterministic coarse pre-projection of scheduler behavior;
authoritative diversity-floor enforcement happens when the scheduler emits the
actual batch.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from enum import Enum
from typing import Annotated, Any, Callable, Literal

from pydantic import Field, ValidationError, model_validator

from metaharness.context.models import SHA256_PATTERN, FrozenModel
from metaharness.discovery.models import _self_verifying, _sort_unique_scalars
from metaharness.discovery.population import PopulationDescriptor

# Bounds the declarative width/depth search envelope checked by STATIC.
MAX_POLICY_WIDTH_DEPTH_PRODUCT = 4096
# A child may change at most this many top-level SearchPolicyDSL fields.
MAX_POLICY_MUTATION_FIELDS = 3

_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"
BoundedIdentifier = Annotated[
    str, Field(min_length=1, max_length=128, pattern=_IDENTIFIER_PATTERN)
]
NonNegativeFiniteFloat = Annotated[
    float, Field(ge=0.0, allow_inf_nan=False)
]
VariationWeightEntry = tuple["VariationClass", NonNegativeFiniteFloat]


class ParentSelector(str, Enum):
    BASELINE = "baseline"
    ELITE = "elite"
    DIVERSE = "diverse"
    UNDEREXPLORED = "underexplored"
    UNCERTAIN = "uncertain"
    SCORE_TIER = "score_tier"
    PARETO = "pareto"


class InspirationSelector(str, Enum):
    NONE = "none"
    ELITE = "elite"
    DIVERSE = "diverse"
    RANDOM_SCOPED = "random_scoped"


class VariationClass(str, Enum):
    LOCAL = "local"
    STRUCTURAL = "structural"
    COUNTEREXAMPLE = "counterexample"
    COMPOSITIONAL = "compositional"


class PolicyValidationStage(str, Enum):
    SCHEMA = "schema"
    STATIC = "static"
    SIMULATION = "simulation"
    SHADOW = "shadow"


class PolicyValidationVerdict(str, Enum):
    PASSED = "passed"
    FAILED = "failed"


class MemoryVisibility(str, Enum):
    """Bounded memory scopes; reviewed-project visibility is not representable."""

    NONE = "none"
    PRIVATE = "private"
    LINEAGE = "lineage"
    ISLAND = "island"
    CAMPAIGN = "campaign"


class IslandVisibility(str, Enum):
    """Whether inspiration stays island-isolated or sees campaign summaries."""

    ISOLATED = "isolated"
    CAMPAIGN = "campaign"


def _sort_variation_weights(values: Any) -> Any:
    """Convert mapping-shaped weights to sorted, immutable key/value entries."""

    if isinstance(values, Mapping):
        values = tuple(values.items())
    if not isinstance(values, (list, tuple)):
        return values

    pairs: list[Any] = []
    for value in values:
        if not isinstance(value, (list, tuple)) or len(value) != 2:
            return values
        pairs.append(tuple(value))
    try:
        return _sort_unique_scalars(tuple(pairs))
    except TypeError:
        return values


class SearchPolicyStopRules(FrozenModel):
    """Finite stop bounds for one declarative search policy."""

    schema_version: Literal[1] = 1
    max_attempts: int = Field(gt=0)
    max_cost: float = Field(gt=0.0, allow_inf_nan=False)
    stagnation_window: int = Field(gt=0)


class SearchPolicyDSL(FrozenModel):
    """Non-executable, schema-bounded search controls."""

    schema_version: Literal[1] = 1
    parent_selector: ParentSelector
    inspiration_selector: InspirationSelector
    explorer_fraction: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    optimizer_fraction: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    variation_weights: tuple[VariationWeightEntry, ...]
    briefing_template_id: BoundedIdentifier
    max_width: int = Field(gt=0)
    max_depth: int = Field(gt=0)
    max_concurrency: int = Field(gt=0)
    diversity_floor: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    baseline_reseed_interval: int = Field(gt=0)
    memory_visibility: MemoryVisibility
    island_visibility: IslandVisibility
    stop_rules: SearchPolicyStopRules

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, data: Any) -> Any:
        if isinstance(data, dict) and "variation_weights" in data:
            data = {
                **data,
                "variation_weights": _sort_variation_weights(
                    data["variation_weights"]
                ),
            }
        return data

    @model_validator(mode="after")
    def _validate_bounded_controls(self) -> "SearchPolicyDSL":
        if not math.isclose(
            self.explorer_fraction + self.optimizer_fraction,
            1.0,
            rel_tol=0.0,
            abs_tol=1e-9,
        ):
            raise ValueError("explorer_fraction and optimizer_fraction must sum to 1.0")

        variation_classes = [variation for variation, _ in self.variation_weights]
        if len(variation_classes) != len(set(variation_classes)):
            raise ValueError("variation_weights must have unique VariationClass keys")
        if not any(weight > 0.0 for _, weight in self.variation_weights):
            raise ValueError("variation_weights must contain at least one positive weight")
        return self


class SearchPolicySnapshot(FrozenModel):
    """Immutable policy candidate; it carries neither score nor approval state."""

    schema_version: Literal[1] = 1
    policy_id: BoundedIdentifier
    parent_policy_id: BoundedIdentifier | None = None
    parent_policy_hash: str | None = Field(default=None, pattern=SHA256_PATTERN)
    campaign_id: BoundedIdentifier
    policy: SearchPolicyDSL
    window_id: BoundedIdentifier
    created_sequence: int = Field(ge=0)
    policy_hash: str = Field(default="", pattern=SHA256_PATTERN)

    @model_validator(mode="wrap")
    @classmethod
    def _verify_hash(
        cls, data: Any, handler: Callable[[Any], Any]
    ) -> "SearchPolicySnapshot":
        return _self_verifying(data, handler, "policy_hash", "policy_hash mismatch")

    @model_validator(mode="after")
    def _validate_lineage(self) -> "SearchPolicySnapshot":
        if self.parent_policy_id == self.policy_id:
            raise ValueError("a search policy snapshot cannot name itself as its parent")
        return self


class PolicyValidationReceipt(FrozenModel):
    """One deterministic stage verdict over an immutable policy hash."""

    schema_version: Literal[1] = 1
    policy_hash: str = Field(pattern=SHA256_PATTERN)
    parent_policy_hash: str | None = Field(default=None, pattern=SHA256_PATTERN)
    stage: PolicyValidationStage
    verdict: PolicyValidationVerdict
    reason: str = Field(min_length=1, max_length=512)
    descriptor_hash: str | None = Field(default=None, pattern=SHA256_PATTERN)
    receipt_hash: str = Field(default="", pattern=SHA256_PATTERN)

    @model_validator(mode="wrap")
    @classmethod
    def _verify_hash(
        cls, data: Any, handler: Callable[[Any], Any]
    ) -> "PolicyValidationReceipt":
        return _self_verifying(data, handler, "receipt_hash", "receipt_hash mismatch")

    @model_validator(mode="after")
    def _validate_window_binding(self) -> "PolicyValidationReceipt":
        if self.stage in (
            PolicyValidationStage.SCHEMA,
            PolicyValidationStage.STATIC,
        ) and self.descriptor_hash is not None:
            raise ValueError("SCHEMA and STATIC receipts cannot carry descriptor_hash")
        if self.stage is PolicyValidationStage.SHADOW and self.descriptor_hash is None:
            raise ValueError("SHADOW receipts require descriptor_hash")
        if (
            self.stage is PolicyValidationStage.SIMULATION
            and self.verdict is PolicyValidationVerdict.PASSED
            and self.descriptor_hash is None
        ):
            raise ValueError("a passed SIMULATION receipt requires descriptor_hash")
        if (
            self.stage is PolicyValidationStage.SHADOW
            and "no activation authority" not in self.reason.lower()
        ):
            raise ValueError(
                "a SHADOW receipt must state that it grants no activation authority"
            )
        return self


def _receipt(
    candidate: SearchPolicySnapshot,
    parent_policy_hash: str | None,
    stage: PolicyValidationStage,
    verdict: PolicyValidationVerdict,
    reason: str,
    *,
    descriptor_hash: str | None = None,
) -> PolicyValidationReceipt:
    return PolicyValidationReceipt(
        policy_hash=candidate.policy_hash,
        parent_policy_hash=parent_policy_hash,
        stage=stage,
        verdict=verdict,
        reason=reason,
        descriptor_hash=descriptor_hash,
    )


def _static_failure(
    candidate: SearchPolicySnapshot,
    parent_policy_hash: str | None,
    reason: str,
) -> PolicyValidationReceipt:
    return _receipt(
        candidate,
        parent_policy_hash,
        PolicyValidationStage.STATIC,
        PolicyValidationVerdict.FAILED,
        f"static failed: {reason}",
    )


def _changed_policy_fields(
    candidate: SearchPolicyDSL, parent: SearchPolicyDSL
) -> int:
    candidate_fields = candidate.model_dump(mode="json")
    parent_fields = parent.model_dump(mode="json")
    return sum(
        candidate_fields[field] != parent_fields[field]
        for field in SearchPolicyDSL.model_fields
    )


def _validate_static(
    candidate: SearchPolicySnapshot,
    parent: SearchPolicySnapshot | None,
    parent_policy_hash: str | None,
) -> PolicyValidationReceipt:
    policy = candidate.policy
    if not math.isclose(
        policy.explorer_fraction + policy.optimizer_fraction,
        1.0,
        rel_tol=0.0,
        abs_tol=1e-9,
    ):
        return _static_failure(
            candidate,
            parent_policy_hash,
            "role fractions do not sum to 1.0",
        )
    if (
        policy.parent_selector is ParentSelector.ELITE
        and policy.diversity_floor <= 0.0
    ):
        return _static_failure(
            candidate,
            parent_policy_hash,
            "ELITE parent selection requires a positive diversity_floor",
        )
    if policy.max_width * policy.max_depth > MAX_POLICY_WIDTH_DEPTH_PRODUCT:
        return _static_failure(
            candidate,
            parent_policy_hash,
            "max_width multiplied by max_depth exceeds the bounded search envelope",
        )
    if policy.stop_rules is None:
        return _static_failure(
            candidate,
            parent_policy_hash,
            "stop_rules are required",
        )

    if parent is None:
        if (
            candidate.parent_policy_id is not None
            or candidate.parent_policy_hash is not None
        ):
            return _static_failure(
                candidate,
                parent_policy_hash,
                "a child naming a parent requires the parent snapshot",
            )
    else:
        try:
            parent = SearchPolicySnapshot.model_validate(
                parent.model_dump(mode="json")
            )
        except (AttributeError, ValidationError):
            return _static_failure(
                candidate,
                parent_policy_hash,
                "parent snapshot failed hash validation",
            )
        if candidate.parent_policy_id != parent.policy_id:
            return _static_failure(
                candidate,
                parent_policy_hash,
                "parent_policy_id does not match the supplied parent snapshot",
            )
        if candidate.parent_policy_hash != parent.policy_hash:
            return _static_failure(
                candidate,
                parent_policy_hash,
                "parent_policy_hash does not match the supplied parent snapshot",
            )
        if candidate.campaign_id != parent.campaign_id:
            return _static_failure(
                candidate,
                parent_policy_hash,
                "child and parent policies must belong to the same campaign",
            )
        changed_fields = _changed_policy_fields(candidate.policy, parent.policy)
        if changed_fields > MAX_POLICY_MUTATION_FIELDS:
            return _static_failure(
                candidate,
                parent_policy_hash,
                "child changes more than three top-level DSL fields",
            )

    return _receipt(
        candidate,
        parent_policy_hash,
        PolicyValidationStage.STATIC,
        PolicyValidationVerdict.PASSED,
        "static passed: cross-field bounds and policy lineage verified",
    )


def _select_parent_ids(
    policy: SearchPolicyDSL, window: PopulationDescriptor
) -> tuple[str, ...]:
    """Deterministically dry-run the selector over canonical candidate nodes."""

    nodes = window.candidate_nodes
    node_count = len(nodes)
    if policy.parent_selector is ParentSelector.BASELINE:
        return ("baseline",)

    if policy.parent_selector is ParentSelector.DIVERSE:
        key_field = "structure_signature"
        limit = node_count
    elif policy.parent_selector is ParentSelector.UNDEREXPLORED:
        key_field = "lineage_id"
        limit = node_count
    elif policy.parent_selector is ParentSelector.SCORE_TIER:
        key_field = "score_tier"
        limit = node_count
    else:
        key_field = "candidate_id"
        if policy.parent_selector is ParentSelector.ELITE:
            limit = max(
                1,
                math.ceil(node_count * (1.0 - policy.diversity_floor)),
            )
        elif policy.parent_selector is ParentSelector.UNCERTAIN:
            limit = max(1, math.ceil(node_count * window.behavioral_diversity))
        else:
            limit = max(1, math.ceil(node_count * window.pareto_coverage))

    selected: list[str] = []
    seen: set[str] = set()
    for node in nodes:
        selector_key = getattr(node, key_field)
        if selector_key in seen:
            continue
        seen.add(selector_key)
        selected.append(node.candidate_id)
        if len(selected) >= min(policy.max_width, limit):
            break
    return tuple(selected)


def _validate_simulation(
    candidate: SearchPolicySnapshot,
    parent_policy_hash: str | None,
    window: PopulationDescriptor | None,
) -> PolicyValidationReceipt:
    if window is None:
        return _receipt(
            candidate,
            parent_policy_hash,
            PolicyValidationStage.SIMULATION,
            PolicyValidationVerdict.FAILED,
            "simulation failed: a PopulationDescriptor window is required",
        )

    descriptor_hash = window.descriptor_hash
    try:
        window = PopulationDescriptor.model_validate(window.model_dump(mode="json"))
    except (AttributeError, ValidationError):
        return _receipt(
            candidate,
            parent_policy_hash,
            PolicyValidationStage.SIMULATION,
            PolicyValidationVerdict.FAILED,
            "simulation failed: population descriptor failed hash validation",
            descriptor_hash=descriptor_hash,
        )

    if candidate.campaign_id != window.campaign_id:
        return _receipt(
            candidate,
            parent_policy_hash,
            PolicyValidationStage.SIMULATION,
            PolicyValidationVerdict.FAILED,
            "simulation failed: population descriptor campaign mismatch",
            descriptor_hash=window.descriptor_hash,
        )
    if candidate.window_id != window.window_id:
        return _receipt(
            candidate,
            parent_policy_hash,
            PolicyValidationStage.SIMULATION,
            PolicyValidationVerdict.FAILED,
            "simulation failed: population descriptor window mismatch",
            descriptor_hash=window.descriptor_hash,
        )
    if not window.candidate_nodes:
        return _receipt(
            candidate,
            parent_policy_hash,
            PolicyValidationStage.SIMULATION,
            PolicyValidationVerdict.FAILED,
            "simulation failed: population descriptor has no candidate nodes",
            descriptor_hash=window.descriptor_hash,
        )

    policy = candidate.policy
    selected_parent_ids = _select_parent_ids(policy, window)
    parent_count = len(selected_parent_ids)
    if policy.parent_selector is ParentSelector.BASELINE:
        # The scheduler emits BASELINE selections as fresh explorers with no
        # candidate parent, so candidate-parent concentration is exactly zero.
        projected_concentration = 0.0
    else:
        projected_concentration = max(
            window.parent_selection_concentration,
            1.0 / parent_count,
        )
    # Concentration and diversity are complementary fractions: a diversity
    # floor of d permits concentration of at most 1-d.
    maximum_concentration = 1.0 - policy.diversity_floor
    if projected_concentration > maximum_concentration + 1e-12:
        return _receipt(
            candidate,
            parent_policy_hash,
            PolicyValidationStage.SIMULATION,
            PolicyValidationVerdict.FAILED,
            "simulation failed: projected parent concentration exceeds diversity allowance",
            descriptor_hash=window.descriptor_hash,
        )

    remaining_budget = dict(window.remaining_budget)
    missing_budget_keys = {"attempts", "cost"} - set(remaining_budget)
    if missing_budget_keys:
        return _receipt(
            candidate,
            parent_policy_hash,
            PolicyValidationStage.SIMULATION,
            PolicyValidationVerdict.FAILED,
            "simulation failed: remaining_budget requires attempts and cost entries",
            descriptor_hash=window.descriptor_hash,
        )

    planned_attempts = min(
        policy.stop_rules.max_attempts,
        policy.max_width * policy.max_depth,
    )
    if planned_attempts > remaining_budget["attempts"]:
        return _receipt(
            candidate,
            parent_policy_hash,
            PolicyValidationStage.SIMULATION,
            PolicyValidationVerdict.FAILED,
            "simulation failed: planned attempts exceed remaining budget",
            descriptor_hash=window.descriptor_hash,
        )
    if window.cost_so_far > policy.stop_rules.max_cost:
        return _receipt(
            candidate,
            parent_policy_hash,
            PolicyValidationStage.SIMULATION,
            PolicyValidationVerdict.FAILED,
            "simulation failed: policy max_cost is already exhausted",
            descriptor_hash=window.descriptor_hash,
        )
    additional_cost_cap = policy.stop_rules.max_cost - window.cost_so_far
    # Deliberately conservative: reject when the policy's entire remaining
    # cost ceiling exceeds the campaign's reported remaining cost budget.
    # This may over-reject a policy that would spend less than its ceiling,
    # but it cannot permit runtime overspend.
    if additional_cost_cap > remaining_budget["cost"]:
        return _receipt(
            candidate,
            parent_policy_hash,
            PolicyValidationStage.SIMULATION,
            PolicyValidationVerdict.FAILED,
            "simulation failed: additional cost cap exceeds remaining budget",
            descriptor_hash=window.descriptor_hash,
        )

    explorer_slots = math.floor(
        policy.max_concurrency * policy.explorer_fraction + 0.5
    )
    optimizer_slots = policy.max_concurrency - explorer_slots
    return _receipt(
        candidate,
        parent_policy_hash,
        PolicyValidationStage.SIMULATION,
        PolicyValidationVerdict.PASSED,
        (
            "simulation passed: "
            f"parents={parent_count}, explorers={explorer_slots}, "
            f"optimizers={optimizer_slots}, attempts={planned_attempts}"
        ),
        descriptor_hash=window.descriptor_hash,
    )


def validate_policy(
    candidate: SearchPolicySnapshot,
    *,
    parent: SearchPolicySnapshot | None,
    window: PopulationDescriptor | None,
) -> tuple[PolicyValidationReceipt, ...]:
    """Run SCHEMA -> STATIC -> SIMULATION -> SHADOW, stopping on failure."""

    parent_policy_hash = parent.policy_hash if parent is not None else None
    try:
        candidate_dump = candidate.model_dump(mode="json")
        candidate_bytes = candidate.model_dump_json()
        verified_candidate = SearchPolicySnapshot.model_validate(candidate_dump)
        dsl_dump = verified_candidate.policy.model_dump(mode="json")
        dsl_bytes = verified_candidate.policy.model_dump_json()
        verified_dsl = SearchPolicyDSL.model_validate(dsl_dump)
        if (
            verified_candidate.model_dump_json() != candidate_bytes
            or verified_dsl.model_dump_json() != dsl_bytes
        ):
            raise ValueError("non-identical policy round-trip")
    except (AttributeError, TypeError, ValueError, ValidationError):
        return (
            _receipt(
                candidate,
                parent_policy_hash,
                PolicyValidationStage.SCHEMA,
                PolicyValidationVerdict.FAILED,
                "schema failed: snapshot hash or DSL round-trip is invalid",
            ),
        )

    receipts = [
        _receipt(
            verified_candidate,
            parent_policy_hash,
            PolicyValidationStage.SCHEMA,
            PolicyValidationVerdict.PASSED,
            "schema passed: snapshot hash and DSL round-trip verified",
        )
    ]

    static_receipt = _validate_static(
        verified_candidate,
        parent,
        parent_policy_hash,
    )
    receipts.append(static_receipt)
    if static_receipt.verdict is PolicyValidationVerdict.FAILED:
        return tuple(receipts)

    simulation_receipt = _validate_simulation(
        verified_candidate,
        parent_policy_hash,
        window,
    )
    receipts.append(simulation_receipt)
    if simulation_receipt.verdict is PolicyValidationVerdict.FAILED:
        return tuple(receipts)

    receipts.append(
        _receipt(
            verified_candidate,
            parent_policy_hash,
            PolicyValidationStage.SHADOW,
            PolicyValidationVerdict.PASSED,
            "shadow passed: policy is eligible only and grants no activation authority",
            descriptor_hash=simulation_receipt.descriptor_hash,
        )
    )
    return tuple(receipts)
