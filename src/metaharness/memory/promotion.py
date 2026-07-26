"""Inert evidence gate for detecting repeated search-set evaluation leakage."""
from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict, Field, field_validator, model_validator

from metaharness.blueprints.models import StrictModel


class SearchSetLeakageError(RuntimeError):
    """Raised when repeated search-set results have no held-out evidence."""


class _FrozenStrictModel(StrictModel):
    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        serialize_by_alias=True,
        frozen=True,
    )


class Evidence(_FrozenStrictModel):
    """Minimal immutable provenance needed to detect search-set reuse."""

    search_set_id: str
    evaluation_count: int = Field(ge=0, strict=True)
    held_out_evaluation_count: int = Field(ge=0, strict=True)

    @field_validator("search_set_id")
    @classmethod
    def _nonempty_identifier(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("search_set_id must not be empty")
        return value

    @model_validator(mode="after")
    def _valid_counts(self) -> "Evidence":
        if self.held_out_evaluation_count > self.evaluation_count:
            raise ValueError(
                "held_out_evaluation_count cannot exceed evaluation_count"
            )
        return self


class PromotionEvidenceDecision(_FrozenStrictModel):
    """Inert evidence disposition; it grants no promotion or activation authority."""

    status: Literal["evidence_accepted"] = "evidence_accepted"
    evidence: Evidence


class PromotionGate:
    """Reject leaked evidence without mutating any active or deployment state."""

    def decide(self, evidence: Evidence) -> PromotionEvidenceDecision:
        exact = Evidence.model_validate(evidence)
        if exact.evaluation_count > 1 and exact.held_out_evaluation_count == 0:
            raise SearchSetLeakageError(
                "repeated search-set evaluation requires held-out evidence"
            )
        return PromotionEvidenceDecision(evidence=exact)


__all__ = [
    "Evidence",
    "PromotionEvidenceDecision",
    "PromotionGate",
    "SearchSetLeakageError",
]
