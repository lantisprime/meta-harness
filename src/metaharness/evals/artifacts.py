"""Immutable report and tuning-proposal artifacts for exact eval runs."""
from __future__ import annotations

import math
from typing import Any, Annotated, Literal, Optional, Union

from pydantic import Field, field_validator, model_validator

from metaharness.blueprints.models import ArtifactRef, StrictModel, _validate_slug
from metaharness.evals.models import EvalAssertion, _assert_secret_safe_json
from metaharness.portable.integrity import canonical_json_bytes, sha256_hex


_SHA256_PATTERN = r"^[0-9a-f]{64}$"
EvalSplit = Literal["development", "validation", "holdout"]


class EvalMetrics(StrictModel):
    tokens_in: int = Field(default=0, ge=0, strict=True)
    tokens_out: int = Field(default=0, ge=0, strict=True)
    cost_usd: float = Field(default=0.0, ge=0.0, strict=True)
    latency_s: float = Field(default=0.0, ge=0.0, strict=True)

    @field_validator("cost_usd", "latency_s")
    @classmethod
    def _finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("evaluation metrics must be finite")
        return value

    def plus(self, other: "EvalMetrics") -> "EvalMetrics":
        return EvalMetrics(
            tokens_in=self.tokens_in + other.tokens_in,
            tokens_out=self.tokens_out + other.tokens_out,
            cost_usd=self.cost_usd + other.cost_usd,
            latency_s=self.latency_s + other.latency_s,
        )


class EvalAttemptResult(StrictModel):
    repetition: int = Field(ge=1, strict=True)
    verdict: Literal["pass", "fail", "unverified"]
    scorer: str
    detail: str = ""
    output: Any = None
    metrics: EvalMetrics = Field(default_factory=EvalMetrics)

    @model_validator(mode="after")
    def _safe_payload(self) -> "EvalAttemptResult":
        _assert_secret_safe_json(
            {
                "scorer": self.scorer,
                "detail": self.detail,
                "output": self.output,
            },
            location="evaluation attempt",
            reject_sensitive_keys=True,
        )
        return self


class EvalCaseResult(StrictModel):
    case_id: str
    split: EvalSplit
    assertion_kind: Literal["success_check", "output_schema", "rubric"]
    assertion_digest: Optional[str] = Field(default=None, pattern=_SHA256_PATTERN)
    # The exact assertion is useful evidence for visible splits. It is always
    # absent for holdout records, where it could disclose ground truth.
    assertion: Optional[EvalAssertion] = None
    attempts: list[EvalAttemptResult]
    verdict: Literal["pass", "fail", "unverified"]

    @field_validator("case_id")
    @classmethod
    def _case_id(cls, value: str) -> str:
        return _validate_slug(value, label="case id")

    @model_validator(mode="after")
    def _canonical_and_sealed(self) -> "EvalCaseResult":
        if not self.attempts:
            raise ValueError("an eval case result needs at least one attempt")
        repetitions = [attempt.repetition for attempt in self.attempts]
        if repetitions != list(range(1, len(repetitions) + 1)):
            raise ValueError("eval attempt repetitions must be contiguous and ordered")
        if self.split == "holdout":
            if self.assertion is not None or self.assertion_digest is not None:
                raise ValueError(
                    "holdout results cannot disclose assertions or per-case digests"
                )
            for attempt in self.attempts:
                if attempt.output is not None or attempt.detail:
                    raise ValueError(
                        "holdout results cannot disclose outputs or verifier detail"
                    )
        elif self.assertion is None:
            raise ValueError("visible split results must include their exact assertion")
        elif self.assertion_digest is None:
            raise ValueError("visible split results must include assertion_digest")
        elif sha256_hex(
            canonical_json_bytes(self.assertion.model_dump(mode="json"))
        ) != self.assertion_digest:
            raise ValueError("visible assertion does not match assertion_digest")
        verdicts = [attempt.verdict for attempt in self.attempts]
        expected_verdict = (
            "fail"
            if "fail" in verdicts
            else "unverified"
            if "unverified" in verdicts
            else "pass"
        )
        if self.verdict != expected_verdict:
            raise ValueError("case verdict does not match attempt results")
        return self


class EvaluationReport(StrictModel):
    schema_version: Literal[1] = 1
    id: str
    blueprint_ref: ArtifactRef
    eval_ref: ArtifactRef
    split: EvalSplit
    blueprint_digest: str = Field(pattern=_SHA256_PATTERN)
    workflow_digest: str = Field(pattern=_SHA256_PATTERN)
    eval_digest: str = Field(pattern=_SHA256_PATTERN)
    runner_id: str
    cases: list[EvalCaseResult]
    metrics: EvalMetrics = Field(default_factory=EvalMetrics)
    passed: int = Field(ge=0, strict=True)
    failed: int = Field(ge=0, strict=True)
    unverified: int = Field(ge=0, strict=True)
    created_at: float
    content_digest: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("id", "runner_id")
    @classmethod
    def _safe_ids(cls, value: str) -> str:
        return _validate_slug(value)

    @field_validator("created_at")
    @classmethod
    def _finite_timestamp(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("evaluation report timestamp must be finite")
        return value

    def digest_payload(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        payload.pop("id")
        payload.pop("created_at")
        payload.pop("content_digest")
        return payload

    @model_validator(mode="after")
    def _provenance_is_self_consistent(self) -> "EvaluationReport":
        if any(case.split != self.split for case in self.cases):
            raise ValueError("all report cases must belong to the report split")
        case_ids = [case.case_id for case in self.cases]
        if len(set(case_ids)) != len(case_ids):
            raise ValueError("evaluation report case ids must be unique")
        counts = {
            "pass": sum(case.verdict == "pass" for case in self.cases),
            "fail": sum(case.verdict == "fail" for case in self.cases),
            "unverified": sum(case.verdict == "unverified" for case in self.cases),
        }
        if (self.passed, self.failed, self.unverified) != (
            counts["pass"], counts["fail"], counts["unverified"]
        ):
            raise ValueError("evaluation report summary does not match case results")
        expected_metrics = EvalMetrics()
        for case in self.cases:
            for attempt in case.attempts:
                expected_metrics = expected_metrics.plus(attempt.metrics)
        if expected_metrics != self.metrics:
            raise ValueError("evaluation report metrics do not match attempt totals")
        expected_digest = sha256_hex(canonical_json_bytes(self.digest_payload()))
        if self.content_digest != expected_digest:
            raise ValueError("evaluation report content_digest mismatch")
        return self


class EvaluationReportRef(StrictModel):
    """Exact visible-split report input frozen into a tuning proposal."""

    id: str
    content_digest: str = Field(pattern=_SHA256_PATTERN)
    split: Literal["development", "validation"]

    @field_validator("id")
    @classmethod
    def _id(cls, value: str) -> str:
        return _validate_slug(value, label="report id")


class SetDescriptionPatch(StrictModel):
    op: Literal["set_description"]
    value: str

    @field_validator("value")
    @classmethod
    def _safe_value(cls, value: str) -> str:
        _assert_secret_safe_json(value, location="tuning patch description")
        return value


class SetStepObjectivePatch(StrictModel):
    op: Literal["set_step_objective"]
    step_id: str
    value: str

    @field_validator("step_id")
    @classmethod
    def _step_id(cls, value: str) -> str:
        return _validate_slug(value, label="step id")

    @field_validator("value")
    @classmethod
    def _safe_value(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("step objective cannot be blank")
        _assert_secret_safe_json(value, location="tuning patch objective")
        return value


class ReplaceStepBoundariesPatch(StrictModel):
    op: Literal["replace_step_boundaries"]
    step_id: str
    value: list[str]

    @field_validator("step_id")
    @classmethod
    def _step_id(cls, value: str) -> str:
        return _validate_slug(value, label="step id")

    @field_validator("value")
    @classmethod
    def _safe_value(cls, value: list[str]) -> list[str]:
        if any(not item.strip() for item in value):
            raise ValueError("step boundaries cannot contain blank values")
        _assert_secret_safe_json(value, location="tuning patch boundaries")
        return value


class SetStepMaxAttemptsPatch(StrictModel):
    op: Literal["set_step_max_attempts"]
    step_id: str
    value: int = Field(ge=1, le=7, strict=True)

    @field_validator("step_id")
    @classmethod
    def _step_id(cls, value: str) -> str:
        return _validate_slug(value, label="step id")


SafeBlueprintPatch = Annotated[
    Union[
        SetDescriptionPatch,
        SetStepObjectivePatch,
        ReplaceStepBoundariesPatch,
        SetStepMaxAttemptsPatch,
    ],
    Field(discriminator="op"),
]


class TuningProposal(StrictModel):
    """Frozen, inert frontier output. It has no publication or activation state."""

    schema_version: Literal[1] = 1
    generator: Literal["frontier"] = "frontier"
    id: str
    blueprint_ref: ArtifactRef
    eval_refs: tuple[ArtifactRef, ...]
    report_refs: tuple[EvaluationReportRef, ...]
    patches: list[SafeBlueprintPatch]
    rationale: str
    created_at: float
    proposal_digest: str = Field(pattern=_SHA256_PATTERN)

    @field_validator("id")
    @classmethod
    def _id(cls, value: str) -> str:
        return _validate_slug(value)

    @field_validator("report_refs")
    @classmethod
    def _reports(
        cls, values: tuple[EvaluationReportRef, ...]
    ) -> tuple[EvaluationReportRef, ...]:
        ids = [value.id for value in values]
        if not values or len(set(ids)) != len(ids):
            raise ValueError("report_refs must be nonempty and unique")
        return values

    @model_validator(mode="after")
    def _frozen_and_safe(self) -> "TuningProposal":
        if not self.eval_refs or len(
            set((ref.id, ref.version) for ref in self.eval_refs)
        ) != len(self.eval_refs):
            raise ValueError("eval_refs must be nonempty and unique")
        if not self.patches:
            raise ValueError("a tuning proposal must contain at least one safe patch")
        if not self.rationale.strip():
            raise ValueError("tuning proposal rationale cannot be blank")
        _assert_secret_safe_json(self.rationale, location="tuning proposal rationale")
        payload = self.model_dump(mode="json")
        payload.pop("proposal_digest")
        expected = sha256_hex(canonical_json_bytes(payload))
        if self.proposal_digest != expected:
            raise ValueError("tuning proposal digest mismatch")
        return self


def proposal_digest(data: dict[str, Any]) -> str:
    return sha256_hex(canonical_json_bytes(data))
