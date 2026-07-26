"""Protected, immutable three-cell scaffold-H ablation contracts."""
from __future__ import annotations

import math
from typing import Any, Callable, Literal, Sequence

from pydantic import ConfigDict, Field, field_validator, model_validator

from metaharness.blueprints.models import ArtifactRef, StrictModel, _validate_slug
from metaharness.evals.artifact_store import EvaluationReportStore
from metaharness.evals.artifacts import (
    EvalCaseResult,
    EvalMetrics,
    EvalSplit,
    EvaluationReport,
)
from metaharness.memory import MemoryCognitiveSkillSnapshot
from metaharness.memory.promotion import Evidence, PromotionGate
from metaharness.portable.integrity import canonical_json_bytes, sha256_hex


CellName = Literal[
    "no_external_memory",
    "base_scaffold",
    "optimized_scaffold",
]
ProtectedView = Literal[
    "approved_target",
    "transfer",
    "replay_retention",
    "privacy",
    "safety",
    "efficiency",
]
ResultStatus = Literal["eligible_pending_human_promotion", "ineligible"]

CELL_NAMES: tuple[CellName, ...] = (
    "no_external_memory",
    "base_scaffold",
    "optimized_scaffold",
)
REQUIRED_PROTECTED_VIEWS: tuple[ProtectedView, ...] = (
    "approved_target",
    "transfer",
    "replay_retention",
    "privacy",
    "safety",
    "efficiency",
)

_PLAIN_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_MEMORY_SHA256_PATTERN = r"^sha256:[0-9a-f]{64}$"
_DIGEST_PLACEHOLDER = "0" * 64


class AblationError(RuntimeError):
    pass


class AblationContractError(AblationError):
    pass


class AblationReferenceMismatchError(AblationError):
    pass


class AblationBudgetError(AblationError):
    pass


class _FrozenStrictModel(StrictModel):
    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        serialize_by_alias=True,
        frozen=True,
        revalidate_instances="always",
    )


class _FrozenArtifactRef(ArtifactRef):
    """Local immutable coercion of the shared serialized artifact reference."""

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        serialize_by_alias=True,
        frozen=True,
        revalidate_instances="always",
    )


class _FrozenEvalMetrics(EvalMetrics):
    """Local immutable coercion of shared evaluation metrics."""

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        serialize_by_alias=True,
        frozen=True,
        revalidate_instances="always",
    )


def _self_hash(
    data: Any,
    handler: Callable[[Any], Any],
    *,
    volatile_fields: frozenset[str] = frozenset(),
):
    supplied = not isinstance(data, dict) or bool(data.get("content_digest"))
    values = data
    if isinstance(data, dict) and not supplied:
        values = dict(data)
        values["content_digest"] = _DIGEST_PLACEHOLDER
    model = handler(values)
    payload = model.model_dump(
        mode="json",
        exclude={"content_digest", *volatile_fields},
    )
    expected = sha256_hex(canonical_json_bytes(payload))
    if supplied:
        if model.content_digest != expected:
            raise ValueError("content_digest mismatch")
    else:
        object.__setattr__(model, "content_digest", expected)
    return model


def _finite_nonnegative(value: float) -> float:
    if not math.isfinite(value) or value < 0:
        raise ValueError("budget values must be finite and nonnegative")
    return value


def _ref_key(ref: ArtifactRef) -> tuple[str, int]:
    return ref.id, ref.version


def _metrics_equal(first: EvalMetrics, second: EvalMetrics) -> bool:
    return first.model_dump(mode="json") == second.model_dump(mode="json")


def _coerce_nested_model(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="python")
    return value


class ArtifactDigestRef(_FrozenStrictModel):
    """Exact version plus the immutable content digest resolved for it."""

    ref: _FrozenArtifactRef
    content_digest: str = Field(pattern=_PLAIN_SHA256_PATTERN)

    @field_validator("ref", mode="before")
    @classmethod
    def _freeze_ref(cls, value: Any) -> Any:
        return _coerce_nested_model(value)


class RepetitionSeed(_FrozenStrictModel):
    repetition: int = Field(ge=1, strict=True)
    seed: int = Field(ge=0, strict=True)


class BudgetEnvelope(_FrozenStrictModel):
    token_limit: int = Field(ge=0, strict=True)
    cost_usd_limit: float = Field(ge=0.0, strict=True)
    wall_time_s_limit: float = Field(ge=0.0, strict=True)

    @field_validator("cost_usd_limit", "wall_time_s_limit")
    @classmethod
    def _finite(cls, value: float) -> float:
        return _finite_nonnegative(value)


class ProtectedEvaluationReportRef(_FrozenStrictModel):
    """Evaluator-owned reference truth for one report's exact view membership."""

    id: str
    content_digest: str = Field(pattern=_PLAIN_SHA256_PATTERN)
    split: EvalSplit
    view: ProtectedView
    case_ids: tuple[str, ...] = Field(min_length=1)
    mandatory_case_ids: tuple[str, ...]

    @field_validator("id")
    @classmethod
    def _safe_id(cls, value: str) -> str:
        return _validate_slug(value, label="report id")

    @field_validator("case_ids", "mandatory_case_ids")
    @classmethod
    def _case_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(values)) != len(values):
            raise ValueError("report case ID sets must be unique")
        return tuple(_validate_slug(value, label="case id") for value in values)

    @model_validator(mode="after")
    def _mandatory_subset(self) -> "ProtectedEvaluationReportRef":
        if not set(self.mandatory_case_ids).issubset(self.case_ids):
            raise ValueError("mandatory_case_ids must be a subset of case_ids")
        return self


class ProtectedRunContextManifest(_FrozenStrictModel):
    """Self-hashing protected-evaluator witness for one campaign cell."""

    schema_version: Literal[1] = 1
    cell: CellName
    blueprint_ref: _FrozenArtifactRef
    blueprint_digest: str = Field(pattern=_PLAIN_SHA256_PATTERN)
    workflow_ref: _FrozenArtifactRef
    workflow_digest: str = Field(pattern=_PLAIN_SHA256_PATTERN)
    scaffold_h_snapshot_hash: str | None = Field(
        default=None, pattern=_MEMORY_SHA256_PATTERN
    )
    evaluator_ref: _FrozenArtifactRef
    evaluator_digest: str = Field(pattern=_PLAIN_SHA256_PATTERN)
    case_set_digest: str = Field(pattern=_PLAIN_SHA256_PATTERN)
    runner_id: str
    runner_configuration_digest: str = Field(pattern=_PLAIN_SHA256_PATTERN)
    task_model_portfolio_ref: _FrozenArtifactRef
    task_model_portfolio_digest: str = Field(pattern=_PLAIN_SHA256_PATTERN)
    w_snapshot_refs: tuple[ArtifactDigestRef, ...]
    repetition_seed_schedule: tuple[RepetitionSeed, ...] = Field(min_length=1)
    budget: BudgetEnvelope
    evaluation_report_refs: tuple[ProtectedEvaluationReportRef, ...] = Field(
        min_length=1
    )
    protected_evaluator_authority_id: str
    content_digest: str = Field(default="", pattern=_PLAIN_SHA256_PATTERN)

    @field_validator(
        "blueprint_ref",
        "workflow_ref",
        "evaluator_ref",
        "task_model_portfolio_ref",
        mode="before",
    )
    @classmethod
    def _freeze_refs(cls, value: Any) -> Any:
        return _coerce_nested_model(value)

    @field_validator("runner_id", "protected_evaluator_authority_id")
    @classmethod
    def _safe_ids(cls, value: str) -> str:
        return _validate_slug(value)

    @model_validator(mode="wrap")
    @classmethod
    def _verify_digest(cls, data: Any, handler: Callable[[Any], Any]):
        return _self_hash(data, handler)

    @model_validator(mode="after")
    def _canonical_manifest(self) -> "ProtectedRunContextManifest":
        schedule = [item.repetition for item in self.repetition_seed_schedule]
        if schedule != list(range(1, len(schedule) + 1)):
            raise ValueError("repetition/seed schedule must be contiguous and ordered")
        if len({_ref_key(item.ref) for item in self.w_snapshot_refs}) != len(
            self.w_snapshot_refs
        ):
            raise ValueError("W snapshot refs must be unique")
        report_ids = [ref.id for ref in self.evaluation_report_refs]
        if len(set(report_ids)) != len(report_ids):
            raise ValueError("evaluation-report refs must be unique")
        views = [ref.view for ref in self.evaluation_report_refs]
        if set(views) != set(REQUIRED_PROTECTED_VIEWS):
            raise ValueError("manifest must bind every required protected view")
        if self.cell == "no_external_memory":
            if self.scaffold_h_snapshot_hash is not None:
                raise ValueError("no-memory cell cannot bind a scaffold-H snapshot")
        elif self.scaffold_h_snapshot_hash is None:
            raise ValueError("memory cell must bind its scaffold-H snapshot")
        return self


class HAblationCell(_FrozenStrictModel):
    name: CellName
    manifest: ProtectedRunContextManifest

    @model_validator(mode="after")
    def _matching_name(self) -> "HAblationCell":
        if self.name != self.manifest.cell:
            raise ValueError("cell name does not match its protected manifest")
        return self


_FROZEN_AXIS_FIELDS: tuple[tuple[str, str], ...] = (
    ("evaluator_ref", "evaluator reference"),
    ("evaluator_digest", "evaluator digest"),
    ("case_set_digest", "case set"),
    ("runner_id", "runner identity"),
    ("runner_configuration_digest", "runner configuration"),
    ("task_model_portfolio_ref", "task-model portfolio reference"),
    ("task_model_portfolio_digest", "task-model portfolio digest"),
    ("w_snapshot_refs", "W snapshot"),
    ("repetition_seed_schedule", "repetition/seed schedule"),
    ("budget", "budget"),
    ("protected_evaluator_authority_id", "protected evaluator authority"),
)


class HAblationCampaign(_FrozenStrictModel):
    """Three-cell H-only campaign. It carries evidence, never activation state."""

    schema_version: Literal[1] = 1
    campaign_id: str
    cells: tuple[HAblationCell, ...]
    base_scaffold_snapshot: MemoryCognitiveSkillSnapshot
    optimized_scaffold_snapshot: MemoryCognitiveSkillSnapshot
    rollback_snapshot_hash: str = Field(pattern=_MEMORY_SHA256_PATTERN)
    required_views: tuple[ProtectedView, ...]
    search_evidence: Evidence

    @field_validator("campaign_id")
    @classmethod
    def _safe_campaign_id(cls, value: str) -> str:
        return _validate_slug(value, label="campaign id")

    @model_validator(mode="after")
    def _frozen_contract(self) -> "HAblationCampaign":
        if len(self.cells) != 3:
            raise ValueError("campaign requires exactly three cells")
        names = tuple(cell.name for cell in self.cells)
        if names != CELL_NAMES:
            raise ValueError(
                "campaign cells must be the canonical exactly three scaffold-H cells"
            )
        if (
            len(self.required_views) != len(REQUIRED_PROTECTED_VIEWS)
            or set(self.required_views) != set(REQUIRED_PROTECTED_VIEWS)
        ):
            raise ValueError("campaign must contain all required protected views")
        if (
            self.optimized_scaffold_snapshot.parent_snapshot_hash
            != self.base_scaffold_snapshot.content_hash
        ):
            raise ValueError("optimized snapshot must be parent-bound to base")
        if self.rollback_snapshot_hash != self.base_scaffold_snapshot.content_hash:
            raise ValueError("rollback target must equal the exact base snapshot")
        if (
            self.cells[1].manifest.scaffold_h_snapshot_hash
            != self.base_scaffold_snapshot.content_hash
        ):
            raise ValueError("base cell does not bind the exact base snapshot")
        if (
            self.cells[2].manifest.scaffold_h_snapshot_hash
            != self.optimized_scaffold_snapshot.content_hash
        ):
            raise ValueError("optimized cell does not bind the exact optimized snapshot")
        baseline = self.cells[0].manifest
        baseline_report_contract = tuple(
            (ref.view, ref.case_ids, ref.mandatory_case_ids)
            for ref in baseline.evaluation_report_refs
        )
        for cell in self.cells[1:]:
            for field, label in _FROZEN_AXIS_FIELDS:
                if getattr(cell.manifest, field) != getattr(baseline, field):
                    raise ValueError(
                        f"frozen-axis mismatch for {label} in {cell.name} manifest"
                    )
            report_contract = tuple(
                (ref.view, ref.case_ids, ref.mandatory_case_ids)
                for ref in cell.manifest.evaluation_report_refs
            )
            if report_contract != baseline_report_contract:
                raise ValueError(
                    "protected report view/case/mandatory contract mismatch "
                    f"in {cell.name} manifest"
                )
        return self

    def cell(self, name: CellName) -> HAblationCell:
        return next(cell for cell in self.cells if cell.name == name)


class ProtectedAttemptResult(_FrozenStrictModel):
    repetition: int = Field(ge=1, strict=True)
    verdict: Literal["pass", "fail", "unverified"]
    metrics: _FrozenEvalMetrics

    @field_validator("metrics", mode="before")
    @classmethod
    def _freeze_metrics(cls, value: Any) -> Any:
        return _coerce_nested_model(value)


class ProtectedCaseResult(_FrozenStrictModel):
    """Non-disclosing row projection; report refs retain full-fidelity evidence."""

    verdict: Literal["pass", "fail", "unverified"]
    attempts: tuple[ProtectedAttemptResult, ...] = Field(min_length=1)
    metrics: _FrozenEvalMetrics

    @field_validator("metrics", mode="before")
    @classmethod
    def _freeze_metrics(cls, value: Any) -> Any:
        return _coerce_nested_model(value)

    @model_validator(mode="after")
    def _derived_totals(self) -> "ProtectedCaseResult":
        repetitions = [attempt.repetition for attempt in self.attempts]
        if repetitions != list(range(1, len(repetitions) + 1)):
            raise ValueError("protected attempts must be contiguous and ordered")
        expected = EvalMetrics()
        for attempt in self.attempts:
            expected = expected.plus(attempt.metrics)
        if not _metrics_equal(expected, self.metrics):
            raise ValueError("protected case metrics do not match attempt totals")
        verdicts = [attempt.verdict for attempt in self.attempts]
        expected_verdict = (
            "fail"
            if "fail" in verdicts
            else "unverified"
            if "unverified" in verdicts
            else "pass"
        )
        if self.verdict != expected_verdict:
            raise ValueError("protected case verdict does not match attempts")
        return self


class CellEvidenceBinding(_FrozenStrictModel):
    """Inputs to protected row construction; outcomes are resolved, not accepted."""

    cell: CellName
    report_ref: ProtectedEvaluationReportRef
    memory_receipt_hashes: tuple[str, ...]

    @field_validator("memory_receipt_hashes")
    @classmethod
    def _receipt_hashes(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(values)) != len(values):
            raise ValueError("memory receipt hashes must be unique")
        import re

        if any(re.fullmatch(_MEMORY_SHA256_PATTERN, value) is None for value in values):
            raise ValueError("memory receipt hashes must be sha256 references")
        return values

    @model_validator(mode="after")
    def _memory_boundary(self) -> "CellEvidenceBinding":
        if self.cell == "no_external_memory" and self.memory_receipt_hashes:
            raise ValueError("no-memory cell cannot carry memory receipts")
        if self.cell != "no_external_memory" and not self.memory_receipt_hashes:
            raise ValueError("memory cell requires memory receipts")
        return self


class CellCaseEvidence(_FrozenStrictModel):
    cell: CellName
    report_ref: ProtectedEvaluationReportRef
    case_result: ProtectedCaseResult
    memory_receipt_hashes: tuple[str, ...]

    @model_validator(mode="after")
    def _memory_boundary(self) -> "CellCaseEvidence":
        CellEvidenceBinding(
            cell=self.cell,
            report_ref=self.report_ref,
            memory_receipt_hashes=self.memory_receipt_hashes,
        )
        return self


class ProtectedEvidenceRow(_FrozenStrictModel):
    case_id: str
    view: ProtectedView
    split: EvalSplit
    mandatory: bool
    outcomes: tuple[CellCaseEvidence, ...]

    @field_validator("case_id")
    @classmethod
    def _case_id(cls, value: str) -> str:
        return _validate_slug(value, label="case id")

    @model_validator(mode="after")
    def _exact_cells(self) -> "ProtectedEvidenceRow":
        if tuple(outcome.cell for outcome in self.outcomes) != CELL_NAMES:
            raise ValueError("evidence row requires exactly the canonical three cells")
        if any(outcome.report_ref.view != self.view for outcome in self.outcomes):
            raise ValueError("evidence report-ref view mismatch")
        if any(outcome.report_ref.split != self.split for outcome in self.outcomes):
            raise ValueError("evidence report-ref split mismatch")
        if any(self.case_id not in outcome.report_ref.case_ids for outcome in self.outcomes):
            raise ValueError("evidence report-ref case mismatch")
        expected_mandatory = (
            self.case_id in self.outcomes[0].report_ref.mandatory_case_ids
        )
        if any(
            (self.case_id in outcome.report_ref.mandatory_case_ids) != expected_mandatory
            for outcome in self.outcomes
        ):
            raise ValueError("evidence report-ref mandatory contract mismatch")
        if self.mandatory != expected_mandatory:
            raise ValueError("evidence row mandatory mismatch")
        return self


class ViewSummary(_FrozenStrictModel):
    cell: CellName
    view: ProtectedView
    case_count: int = Field(ge=1, strict=True)
    attempt_count: int = Field(ge=1, strict=True)
    pass_rate: float = Field(ge=0.0, le=1.0)
    outcome_variance: float = Field(ge=0.0)
    metrics: _FrozenEvalMetrics

    @field_validator("metrics", mode="before")
    @classmethod
    def _freeze_metrics(cls, value: Any) -> Any:
        return _coerce_nested_model(value)

    @field_validator("pass_rate", "outcome_variance")
    @classmethod
    def _finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("derived view statistics must be finite")
        return value


class CaseDelta(_FrozenStrictModel):
    case_id: str
    view: ProtectedView
    mandatory: bool
    no_memory_to_base: Literal[-1, 0, 1] | None
    optimized_to_base: Literal[-1, 0, 1] | None


class ClosestProtectedResult(_FrozenStrictModel):
    cell: Literal["base_scaffold", "optimized_scaffold"]
    approved_target_pass_rate: float = Field(ge=0.0, le=1.0)


class HAblationResult(_FrozenStrictModel):
    """Protected comparison output. Status is eligibility, never promotion."""

    schema_version: Literal[1] = 1
    id: str
    created_at: float
    campaign: HAblationCampaign
    evidence_rows: tuple[ProtectedEvidenceRow, ...]
    view_summaries: tuple[ViewSummary, ...]
    case_deltas: tuple[CaseDelta, ...]
    improved_approved_target_case_ids: tuple[str, ...]
    regressed_mandatory_case_ids: tuple[str, ...]
    mandatory_unverified_case_ids: tuple[str, ...]
    status: ResultStatus
    w_mem_lane_unblocked: bool
    closest_protected_result: ClosestProtectedResult
    unresolved_gap: str
    rollback_snapshot_hash: str = Field(pattern=_MEMORY_SHA256_PATTERN)
    content_digest: str = Field(default="", pattern=_PLAIN_SHA256_PATTERN)

    @field_validator("id")
    @classmethod
    def _safe_id(cls, value: str) -> str:
        return _validate_slug(value, label="ablation result id")

    @field_validator("created_at")
    @classmethod
    def _finite_created_at(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("ablation result timestamp must be finite")
        return value

    @model_validator(mode="wrap")
    @classmethod
    def _verify_digest(cls, data: Any, handler: Callable[[Any], Any]):
        return _self_hash(
            data,
            handler,
            volatile_fields=frozenset({"id", "created_at"}),
        )

    @model_validator(mode="after")
    def _structural_consistency(self) -> "HAblationResult":
        derived = _derive_result_fields(self.campaign, self.evidence_rows)
        for field, expected in derived.items():
            if getattr(self, field) != expected:
                raise ValueError(
                    f"result {field} does not match campaign and evidence rows"
                )
        return self


def _resolve_report(
    report_ref: ProtectedEvaluationReportRef,
    report_store: EvaluationReportStore,
) -> EvaluationReport:
    try:
        report = report_store.get(report_ref.id)
    except Exception as exc:
        raise AblationReferenceMismatchError(
            f"immutable report {report_ref.id!r} could not be resolved"
        ) from exc
    if report.content_digest != report_ref.content_digest:
        raise AblationReferenceMismatchError("evaluation report digest mismatch")
    if report.split != report_ref.split:
        raise AblationReferenceMismatchError("evaluation report split mismatch")
    exact_case_ids = tuple(case.case_id for case in report.cases)
    if exact_case_ids != report_ref.case_ids:
        raise AblationReferenceMismatchError(
            "evaluation report exact case membership mismatch"
        )
    return report


def _project_case(case: EvalCaseResult) -> ProtectedCaseResult:
    metrics = EvalMetrics()
    attempts = []
    for attempt in case.attempts:
        metrics = metrics.plus(attempt.metrics)
        attempts.append(
            ProtectedAttemptResult(
                repetition=attempt.repetition,
                verdict=attempt.verdict,
                metrics=attempt.metrics,
            )
        )
    return ProtectedCaseResult(
        verdict=case.verdict,
        attempts=tuple(attempts),
        metrics=metrics,
    )


def build_protected_evidence_row(
    *,
    case_id: str,
    view: ProtectedView,
    bindings: Sequence[CellEvidenceBinding],
    report_store: EvaluationReportStore,
) -> ProtectedEvidenceRow:
    """Resolve immutable reports and derive a non-disclosing exact-case row."""

    exact_bindings = tuple(CellEvidenceBinding.model_validate(item) for item in bindings)
    by_cell = {binding.cell: binding for binding in exact_bindings}
    if len(exact_bindings) != 3 or set(by_cell) != set(CELL_NAMES):
        raise AblationContractError("evidence row requires exactly all three cells")
    outcomes = []
    splits: set[EvalSplit] = set()
    contracts = {
        (
            binding.report_ref.view,
            binding.report_ref.case_ids,
            binding.report_ref.mandatory_case_ids,
        )
        for binding in exact_bindings
    }
    if len(contracts) != 1:
        raise AblationReferenceMismatchError(
            "cell evaluation report view/case/mandatory contract mismatch"
        )
    for cell in CELL_NAMES:
        binding = by_cell[cell]
        if binding.report_ref.view != view:
            raise AblationReferenceMismatchError("evaluation report view mismatch")
        report = _resolve_report(binding.report_ref, report_store)
        splits.add(report.split)
        matching = [case for case in report.cases if case.case_id == case_id]
        if len(matching) != 1:
            raise AblationReferenceMismatchError(
                f"evaluation report case mismatch for {case_id!r}"
            )
        outcomes.append(
            CellCaseEvidence(
                cell=cell,
                report_ref=binding.report_ref,
                case_result=_project_case(matching[0]),
                memory_receipt_hashes=binding.memory_receipt_hashes,
            )
        )
    if len(splits) != 1:
        raise AblationReferenceMismatchError("cell evaluation report split mismatch")
    return ProtectedEvidenceRow(
        case_id=case_id,
        view=view,
        split=next(iter(splits)),
        mandatory=case_id in exact_bindings[0].report_ref.mandatory_case_ids,
        outcomes=tuple(outcomes),
    )


def _validate_reports_and_rows(
    campaign: HAblationCampaign,
    rows: tuple[ProtectedEvidenceRow, ...],
    report_store: EvaluationReportStore,
) -> dict[CellName, tuple[EvaluationReport, ...]]:
    if not rows:
        raise AblationContractError("campaign needs protected evidence rows")
    view_set = {row.view for row in rows}
    if view_set != set(campaign.required_views):
        raise AblationContractError("evidence rows must cover every protected view")
    row_keys = [(row.view, row.case_id) for row in rows]
    if len(set(row_keys)) != len(row_keys):
        raise AblationContractError("protected case/view rows must be unique")

    actual_membership: dict[CellName, set[tuple[str, ProtectedView, str]]] = {
        cell: set() for cell in CELL_NAMES
    }
    reports_by_cell: dict[CellName, dict[str, EvaluationReport]] = {
        cell: {} for cell in CELL_NAMES
    }
    schedule = campaign.cells[0].manifest.repetition_seed_schedule
    for row in rows:
        if tuple(outcome.cell for outcome in row.outcomes) != CELL_NAMES:
            raise AblationContractError("row cell order is not canonical")
        for outcome in row.outcomes:
            manifest = campaign.cell(outcome.cell).manifest
            if outcome.report_ref not in manifest.evaluation_report_refs:
                raise AblationReferenceMismatchError(
                    "evidence row report ref is absent from its protected manifest"
                )
            if row.mandatory != (
                row.case_id in outcome.report_ref.mandatory_case_ids
            ):
                raise AblationReferenceMismatchError(
                    "evidence row mandatory mismatch"
                )
            report = _resolve_report(outcome.report_ref, report_store)
            if outcome.report_ref.view != row.view or report.split != row.split:
                raise AblationReferenceMismatchError("report split/view mismatch")
            cases = [case for case in report.cases if case.case_id == row.case_id]
            if len(cases) != 1:
                raise AblationReferenceMismatchError("report case ID mismatch")
            case = cases[0]
            if _project_case(case) != outcome.case_result:
                raise AblationReferenceMismatchError(
                    "copied case outcome or metrics do not match immutable report"
                )
            if len(case.attempts) != len(schedule) or tuple(
                attempt.repetition for attempt in case.attempts
            ) != tuple(item.repetition for item in schedule):
                raise AblationReferenceMismatchError(
                    "report repetitions do not match protected seed schedule"
                )
            if (
                _ref_key(report.blueprint_ref) != _ref_key(manifest.blueprint_ref)
                or report.blueprint_digest != manifest.blueprint_digest
                or report.workflow_digest != manifest.workflow_digest
            ):
                raise AblationReferenceMismatchError(
                    "report blueprint/workflow provenance mismatch"
                )
            if (
                _ref_key(report.eval_ref) != _ref_key(manifest.evaluator_ref)
                or report.eval_digest != manifest.evaluator_digest
            ):
                raise AblationReferenceMismatchError(
                    "report evaluator provenance mismatch"
                )
            if report.runner_id != manifest.runner_id:
                raise AblationReferenceMismatchError("report runner identity mismatch")
            actual_membership[outcome.cell].add(
                (outcome.report_ref.id, row.view, row.case_id)
            )
            reports_by_cell[outcome.cell][report.id] = report

    for cell in CELL_NAMES:
        expected = {
            (ref.id, ref.view, case_id)
            for ref in campaign.cell(cell).manifest.evaluation_report_refs
            for case_id in ref.case_ids
        }
        if actual_membership[cell] != expected:
            raise AblationReferenceMismatchError(
                f"{cell} evidence rows do not match exact report case membership"
            )
    return {
        cell: tuple(reports_by_cell[cell][key] for key in sorted(reports_by_cell[cell]))
        for cell in CELL_NAMES
    }


def _validate_budgets(
    campaign: HAblationCampaign,
    reports_by_cell: dict[CellName, tuple[EvaluationReport, ...]],
) -> None:
    envelope = campaign.cells[0].manifest.budget
    for cell, reports in reports_by_cell.items():
        metrics = EvalMetrics()
        for report in reports:
            metrics = metrics.plus(report.metrics)
        tokens = metrics.tokens_in + metrics.tokens_out
        if (
            tokens > envelope.token_limit
            or metrics.cost_usd > envelope.cost_usd_limit
            or metrics.latency_s > envelope.wall_time_s_limit
        ):
            raise AblationBudgetError(f"{cell} exceeded the common budget envelope")


def _view_summaries(rows: tuple[ProtectedEvidenceRow, ...]) -> tuple[ViewSummary, ...]:
    summaries = []
    for cell in CELL_NAMES:
        for view in REQUIRED_PROTECTED_VIEWS:
            selected = [row for row in rows if row.view == view]
            outcomes = [
                next(outcome for outcome in row.outcomes if outcome.cell == cell)
                for row in selected
            ]
            values = [
                1.0 if attempt.verdict == "pass" else 0.0
                for outcome in outcomes
                for attempt in outcome.case_result.attempts
            ]
            mean = sum(values) / len(values)
            variance = sum((value - mean) ** 2 for value in values) / len(values)
            metrics = EvalMetrics()
            for outcome in outcomes:
                metrics = metrics.plus(outcome.case_result.metrics)
            summaries.append(
                ViewSummary(
                    cell=cell,
                    view=view,
                    case_count=len(selected),
                    attempt_count=len(values),
                    pass_rate=mean,
                    outcome_variance=variance,
                    metrics=metrics,
                )
            )
    return tuple(summaries)


def _score(verdict: str) -> int | None:
    if verdict == "unverified":
        return None
    return int(verdict == "pass")


def _delta(first: str, second: str) -> Literal[-1, 0, 1] | None:
    first_score = _score(first)
    second_score = _score(second)
    if first_score is None or second_score is None:
        return None
    difference = second_score - first_score
    return difference  # type: ignore[return-value]


def _approved_pass_rate(
    summaries: tuple[ViewSummary, ...], cell: CellName
) -> float:
    return next(
        summary.pass_rate
        for summary in summaries
        if summary.cell == cell and summary.view == "approved_target"
    )


def _derive_result_fields(
    campaign: HAblationCampaign,
    rows: tuple[ProtectedEvidenceRow, ...],
) -> dict[str, Any]:
    """Re-derive every decision-bearing field from the campaign and rows."""

    if not rows:
        raise ValueError("result requires protected evidence rows")
    if {row.view for row in rows} != set(campaign.required_views):
        raise ValueError("result evidence rows must cover every protected view")
    if len({(row.view, row.case_id) for row in rows}) != len(rows):
        raise ValueError("result protected case/view rows must be unique")
    for row in rows:
        for outcome in row.outcomes:
            manifest = campaign.cell(outcome.cell).manifest
            if outcome.report_ref not in manifest.evaluation_report_refs:
                raise ValueError("result evidence report ref is absent from campaign")
            if row.mandatory != (
                row.case_id in outcome.report_ref.mandatory_case_ids
            ):
                raise ValueError("result evidence row mandatory mismatch")

    summaries = _view_summaries(rows)
    deltas = []
    improved = []
    regressions = []
    mandatory_unverified = []
    for row in rows:
        outcomes = {outcome.cell: outcome for outcome in row.outcomes}
        no_memory = outcomes["no_external_memory"].case_result.verdict
        base = outcomes["base_scaffold"].case_result.verdict
        optimized = outcomes["optimized_scaffold"].case_result.verdict
        optimized_delta = _delta(base, optimized)
        deltas.append(
            CaseDelta(
                case_id=row.case_id,
                view=row.view,
                mandatory=row.mandatory,
                no_memory_to_base=_delta(no_memory, base),
                optimized_to_base=optimized_delta,
            )
        )
        if row.view == "approved_target" and optimized_delta == 1:
            improved.append(row.case_id)
        if row.mandatory and optimized_delta == -1:
            regressions.append(row.case_id)
        if row.mandatory and any(
            outcome.case_result.verdict == "unverified" for outcome in row.outcomes
        ):
            mandatory_unverified.append(row.case_id)

    improved_ids = tuple(sorted(set(improved)))
    regression_ids = tuple(sorted(set(regressions)))
    unverified_ids = tuple(sorted(set(mandatory_unverified)))
    eligible = bool(improved_ids) and not regression_ids and not unverified_ids
    if eligible:
        gap = ""
        closest_cell: Literal["base_scaffold", "optimized_scaffold"] = (
            "optimized_scaffold"
        )
    else:
        gaps = []
        if not improved_ids:
            gaps.append("no approved-target case improved")
        if regression_ids:
            gaps.append("mandatory regression: " + ", ".join(regression_ids))
        if unverified_ids:
            gaps.append("mandatory unverified: " + ", ".join(unverified_ids))
        gap = "; ".join(gaps)
        closest_cell = "base_scaffold"

    return {
        "view_summaries": summaries,
        "case_deltas": tuple(deltas),
        "improved_approved_target_case_ids": improved_ids,
        "regressed_mandatory_case_ids": regression_ids,
        "mandatory_unverified_case_ids": unverified_ids,
        "status": "eligible_pending_human_promotion" if eligible else "ineligible",
        "w_mem_lane_unblocked": eligible,
        "closest_protected_result": ClosestProtectedResult(
            cell=closest_cell,
            approved_target_pass_rate=_approved_pass_rate(summaries, closest_cell),
        ),
        "unresolved_gap": gap,
        "rollback_snapshot_hash": campaign.rollback_snapshot_hash,
    }


def evaluate_protected_h_ablation(
    *,
    result_id: str,
    campaign: HAblationCampaign,
    evidence_rows: Sequence[ProtectedEvidenceRow],
    report_store: EvaluationReportStore,
    created_at: float = 0.0,
) -> HAblationResult:
    """Derive the protected verdict from stored reports and frozen manifests."""

    exact_campaign = HAblationCampaign.model_validate(campaign)
    rows = tuple(ProtectedEvidenceRow.model_validate(row) for row in evidence_rows)
    # The leakage boundary is checked independently of score and before an
    # eligibility artifact can be produced.
    PromotionGate().decide(exact_campaign.search_evidence)
    reports_by_cell = _validate_reports_and_rows(exact_campaign, rows, report_store)
    _validate_budgets(exact_campaign, reports_by_cell)
    derived = _derive_result_fields(exact_campaign, rows)

    return HAblationResult(
        id=result_id,
        created_at=created_at,
        campaign=exact_campaign,
        evidence_rows=rows,
        **derived,
    )


__all__ = [
    "AblationBudgetError",
    "AblationContractError",
    "AblationError",
    "AblationReferenceMismatchError",
    "ArtifactDigestRef",
    "BudgetEnvelope",
    "CELL_NAMES",
    "CellCaseEvidence",
    "CellEvidenceBinding",
    "CaseDelta",
    "ClosestProtectedResult",
    "HAblationCampaign",
    "HAblationCell",
    "HAblationResult",
    "ProtectedAttemptResult",
    "ProtectedCaseResult",
    "ProtectedEvaluationReportRef",
    "ProtectedEvidenceRow",
    "ProtectedRunContextManifest",
    "REQUIRED_PROTECTED_VIEWS",
    "RepetitionSeed",
    "ViewSummary",
    "build_protected_evidence_row",
    "evaluate_protected_h_ablation",
]
