"""Frozen, self-hashed population-state contracts for discovery scheduling.

A population descriptor is a compact, immutable scheduler input derived from
candidate evidence.  It does not replace per-candidate evidence and carries no
promotion, deployment, evaluator, memory-activation, weight-training, or
permission authority.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any, Callable, Literal

from pydantic import Field, model_validator

from metaharness.context.models import SHA256_PATTERN, FrozenModel
from metaharness.discovery.models import (
    _self_verifying,
    _sort_submodels,
    _sort_unique_scalars,
)

NonEmptyString = Annotated[str, Field(min_length=1)]
NonNegativeFloat = Annotated[
    float, Field(ge=0.0, allow_inf_nan=False)
]
Fraction = Annotated[
    float, Field(ge=0.0, le=1.0, allow_inf_nan=False)
]
ParentEdge = tuple[NonEmptyString, NonEmptyString]
NonNegativeMetricEntry = tuple[NonEmptyString, NonNegativeFloat]
FractionMetricEntry = tuple[NonEmptyString, Fraction]


def _sort_pair_entries(values: Any, *, accept_mapping: bool) -> Any:
    """Canonicalize edge or immutable mapping entries without masking bad shapes."""

    if accept_mapping and isinstance(values, Mapping):
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
        # Let Pydantic report malformed/unhashable keys or values as a normal
        # ValidationError rather than leaking an implementation exception from
        # this canonicalization pass.
        return values


class ApproachFingerprint(FrozenModel):
    """Stable structural and descriptive identity for one candidate node."""

    schema_version: Literal[1] = 1
    candidate_id: str = Field(min_length=1)
    lineage_id: str = Field(min_length=1)
    approach_descriptor_tokens: tuple[NonEmptyString, ...]
    structure_signature: str = Field(min_length=1)
    score_tier: str = Field(min_length=1)

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, data: Any) -> Any:
        if isinstance(data, dict) and "approach_descriptor_tokens" in data:
            data = {
                **data,
                "approach_descriptor_tokens": _sort_unique_scalars(
                    data["approach_descriptor_tokens"]
                ),
            }
        return data


class PopulationDescriptor(FrozenModel):
    """Canonical population summary consumed by a bounded scheduler.

    Mapping-shaped metrics are stored as sorted key/value tuples.  This keeps
    the contract deeply immutable while retaining deterministic mapping
    semantics and canonical self-hash bytes.
    """

    schema_version: Literal[1] = 1
    campaign_id: str = Field(min_length=1)
    window_id: str = Field(min_length=1)
    candidate_nodes: tuple[ApproachFingerprint, ...]
    parent_edges: tuple[ParentEdge, ...]

    best_score: float = Field(ge=0.0, allow_inf_nan=False)
    frontier_score: float = Field(ge=0.0, allow_inf_nan=False)
    window_score_mean: float = Field(ge=0.0, allow_inf_nan=False)
    window_score_variance: float = Field(ge=0.0, allow_inf_nan=False)

    approach_diversity: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    behavioral_diversity: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    parent_selection_concentration: float = Field(
        ge=0.0, le=1.0, allow_inf_nan=False
    )
    lineage_depth: int = Field(ge=0)
    lineage_width: int = Field(ge=0)
    score_tier_coverage: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    pareto_coverage: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    steps_since_meaningful_improvement: int = Field(ge=0)

    variation_operator_yield: tuple[FractionMetricEntry, ...]
    cross_agent_transfer_count: int = Field(ge=0)
    memory_use_concentration: float = Field(
        ge=0.0, le=1.0, allow_inf_nan=False
    )
    evaluator_failure_count: int = Field(ge=0)
    cost_so_far: float = Field(ge=0.0, allow_inf_nan=False)
    latency_stats: tuple[NonNegativeMetricEntry, ...]
    remaining_budget: tuple[NonNegativeMetricEntry, ...]
    descriptor_hash: str = Field(default="", pattern=SHA256_PATTERN)

    @model_validator(mode="wrap")
    @classmethod
    def _verify_hash(
        cls, data: Any, handler: Callable[[Any], Any]
    ) -> "PopulationDescriptor":
        return _self_verifying(
            data, handler, "descriptor_hash", "descriptor_hash mismatch"
        )

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        data = dict(data)
        if "candidate_nodes" in data:
            data["candidate_nodes"] = _sort_submodels(
                data["candidate_nodes"], ("candidate_id", "lineage_id")
            )
        if "parent_edges" in data:
            data["parent_edges"] = _sort_pair_entries(
                data["parent_edges"], accept_mapping=False
            )
        for field in (
            "variation_operator_yield",
            "latency_stats",
            "remaining_budget",
        ):
            if field in data:
                data[field] = _sort_pair_entries(data[field], accept_mapping=True)
        return data

    @model_validator(mode="after")
    def _validate_unique_keys(self) -> "PopulationDescriptor":
        candidate_ids = [node.candidate_id for node in self.candidate_nodes]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("candidate_nodes must have unique candidate_id values")

        for field in (
            "variation_operator_yield",
            "latency_stats",
            "remaining_budget",
        ):
            entries = getattr(self, field)
            keys = [key for key, _ in entries]
            if len(keys) != len(set(keys)):
                raise ValueError(f"{field} must have unique mapping keys")
        return self


class PopulationWindow(FrozenModel):
    """Attempt-sequence interval bound to the descriptor that summarizes it."""

    schema_version: Literal[1] = 1
    window_id: str = Field(min_length=1)
    start_attempt_sequence: int = Field(ge=0)
    end_attempt_sequence: int = Field(ge=0)
    descriptor_hash: str = Field(pattern=SHA256_PATTERN)

    @model_validator(mode="after")
    def _validate_bounds(self) -> "PopulationWindow":
        if self.end_attempt_sequence < self.start_attempt_sequence:
            raise ValueError(
                "end_attempt_sequence must be greater than or equal to "
                "start_attempt_sequence"
            )
        return self
