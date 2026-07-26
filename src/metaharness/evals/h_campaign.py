"""Frozen, evaluator-owned execution and reload contracts for META-34.

This module is the smallest correct real-execution surface for the canonical
three-cell scaffold-H campaign. It owns:

- strict, frozen, self-hashing ``CampaignSpec`` with per-split
  ``protected_package_digests`` and frozen-cell-spanning blueprint /
  workflow / task-model / runner references;
- a one-time ``HoldoutConsumptionLedger`` that must be claimed *before* the
  sealed holdout file is opened;
- physically separate development / validation / holdout input packages;
- a deterministic verifier restricted to ``equals``, ``contains``, and
  ``one_of`` plus a ``forbidden_substrings`` overflow guard;
- create-only protected evidence (manifest, evidence, result) bound by digest;
- a model-call-free ``verify_campaign`` reload path that reconstructs the
  protected verdict from stored reports;
- terminal statuses are only ``eligible_pending_human_promotion`` or
  ``ineligible``; no promotion, activation, deployment, or W-start.

The module composes the existing ``ablation`` pipeline
(``HAblationCampaign`` / ``build_protected_evidence_row`` /
``evaluate_protected_h_ablation``) so it never re-derives a verdict from
scratch.
"""
from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import os
import re
import math
import time
import types
from pathlib import Path
from typing import Any, Callable, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator

from metaharness.blueprints import ArtifactRef
from metaharness.core.types import Task, WorkerResult
from metaharness.evals.ablation import (
    BudgetEnvelope,
    CellEvidenceBinding,
    HAblationCampaign,
    HAblationCell,
    HAblationResult,
    ProtectedEvaluationReportRef,
    ProtectedRunContextManifest,
    RepetitionSeed,
    build_protected_evidence_row,
    evaluate_protected_h_ablation,
)
from metaharness.evals.artifact_store import EvaluationReportStore, HAblationResultStore
from metaharness.evals.artifacts import (
    EvalAssertion,
    EvalAttemptResult,
    EvalCaseResult,
    EvalMetrics,
    EvaluationReport,
)
from metaharness.harness.memory import MemoryAwareRunner
from metaharness.memory import MemoryActionBroker, MemoryCognitiveSkillSnapshot, MemoryRecord
from metaharness.memory.stores import MemoryStore
from metaharness.memory.promotion import Evidence, PromotionGate
from metaharness.portable.integrity import canonical_json_bytes, sha256_hex


CELLS: tuple[str, ...] = (
    "no_external_memory",
    "base_scaffold",
    "optimized_scaffold",
)
VIEWS: tuple[str, ...] = (
    "approved_target",
    "transfer",
    "replay_retention",
    "privacy",
    "safety",
    "efficiency",
)
EMPTY_SYSTEM_DIGEST = "sha256:" + sha256_hex(canonical_json_bytes(""))
SPLITS: tuple[str, ...] = ("development", "validation", "holdout")
_HASH_REF = re.compile(r"^sha256:[0-9a-f]{64}$")
_PLACEHOLDER_HEX = "0" * 64


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class CampaignContractError(ValueError):
    """Raised when a campaign spec, package, or evidence violates the contract."""


class HoldoutAlreadyConsumedError(RuntimeError):
    """Raised when the sealed holdout ledger has already been consumed."""


class CampaignEvidenceError(RuntimeError):
    """Raised when create-only evidence storage fails."""


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class CampaignModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RuntimeRecordProjection(CampaignModel):
    record_id: str
    record_digest: str
    creation_seq: int

    @model_validator(mode="after")
    def _valid(self) -> "RuntimeRecordProjection":
        _require_hash(self.record_digest, "runtime record digest")
        return self


class RuntimeStoreProjection(CampaignModel):
    name: str
    record_count: int = Field(ge=0, strict=True)
    high_water_mark: int
    records_digest: str
    records: tuple[RuntimeRecordProjection, ...]

    @model_validator(mode="after")
    def _valid(self) -> "RuntimeStoreProjection":
        if not self.name:
            raise CampaignContractError("runtime store name must be non-empty")
        _require_hash(self.records_digest, "runtime store records digest")
        if self.record_count != len(self.records):
            raise CampaignContractError("runtime store record count mismatch")
        if tuple(sorted(item.record_id for item in self.records)) != tuple(
            item.record_id for item in self.records
        ):
            raise CampaignContractError("runtime store records must be ordered by id")
        if len({item.record_id for item in self.records}) != len(self.records):
            raise CampaignContractError("runtime store record IDs must be unique")
        expected_high_water = max(
            (item.creation_seq for item in self.records), default=-1
        )
        if self.high_water_mark != expected_high_water:
            raise CampaignContractError("runtime store high-water mark mismatch")
        expected_digest = "sha256:" + sha256_hex(canonical_json_bytes(
            [item.model_dump(mode="json") for item in self.records]
        ))
        if self.records_digest != expected_digest:
            raise CampaignContractError("runtime store projection digest mismatch")
        return self


class RuntimeAttestation(CampaignModel):
    """Frozen execution boundary rechecked throughout a protected campaign."""

    schema_version: Literal[1] = 1
    mode: Literal["production", "test-only"]
    corpus_digest: str
    resolver_contract: Literal["named-store-full-record-resolver:v1"]
    resolver_implementation_digest: str
    task_template_implementation_digest: str
    memory_aware_runner_implementation_digest: str
    broker_policy_implementation_digest: str
    deterministic_evaluator_implementation_digest: str
    openai_worker_implementation_digest: str
    output_parser_implementation_digest: str
    environment_digest: str
    w_refs: tuple[str, ...]
    stores: tuple[RuntimeStoreProjection, ...]

    @model_validator(mode="after")
    def _valid(self) -> "RuntimeAttestation":
        for name in (
            "corpus_digest",
            "resolver_implementation_digest",
            "task_template_implementation_digest",
            "memory_aware_runner_implementation_digest",
            "broker_policy_implementation_digest",
            "deterministic_evaluator_implementation_digest",
            "openai_worker_implementation_digest",
            "output_parser_implementation_digest",
            "environment_digest",
        ):
            _require_hash(getattr(self, name), name)
        for ref in self.w_refs:
            _require_hash(ref, "runtime W ref")
        names = tuple(store.name for store in self.stores)
        if names != tuple(sorted(names)) or len(names) != len(set(names)):
            raise CampaignContractError(
                "runtime store projections must have unique ordered names"
            )
        return self

    def digest(self) -> str:
        return "sha256:" + sha256_hex(
            canonical_json_bytes(self.model_dump(mode="json"))
        )


def _require_hash(value: str, label: str) -> str:
    if not isinstance(value, str) or not _HASH_REF.fullmatch(value):
        raise CampaignContractError(f"{label} must be an exact sha256 reference")
    return value


def _strip_sha256_prefix(value: str) -> str:
    if value.startswith("sha256:"):
        return value[len("sha256:"):]
    return value


def _sanitize_id(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


class HSurface(CampaignModel):
    """Immutable H surface for one cell. Non-memory fields are frozen across
    cells; the memory snapshot hash and retrieval policy digest are the only
    cell-specific axes."""

    task_template_digest: str
    system_digest: str
    wrapper_digest: str
    resolver_digest: str
    corpus_digest: str
    policy_digest: str
    worker_implementation_digest: str
    output_parser_digest: str
    memory_snapshot_hash: str | None = None
    retrieval_policy_digest: str | None = None

    @model_validator(mode="after")
    def _hashes(self) -> "HSurface":
        for name in (
            "task_template_digest", "system_digest", "wrapper_digest",
            "resolver_digest", "corpus_digest", "policy_digest",
            "worker_implementation_digest", "output_parser_digest",
        ):
            _require_hash(getattr(self, name), name)
        if self.memory_snapshot_hash is not None:
            _require_hash(self.memory_snapshot_hash, "memory_snapshot_hash")
        if self.retrieval_policy_digest is not None:
            _require_hash(self.retrieval_policy_digest, "retrieval_policy_digest")
        return self


# The exact policy projection that the canonical scaffold-H ablation
# declares as the "load-bearing H delta" between base and optimized. Only
# these fields may differ across the two memory cells. Any other
# snapshot-field drift between base and optimized is rejected before
# inference so the attribution axis is unambiguous.
_RETRIEVAL_POLICY_PROJECTION: tuple[str, ...] = ("query_max_results",)
# The frozen base/optimized delta. Any change here must be reflected in
# the spec's contract test suite and the architecture document.
BASE_QUERY_MAX_RESULTS = 1
OPTIMIZED_QUERY_MAX_RESULTS = 2


def _retrieval_policy_digest(
    snapshot: MemoryCognitiveSkillSnapshot,
) -> str:
    """The exact allowed policy projection, derived from a snapshot."""
    projection = {
        field: getattr(snapshot, field)
        for field in _RETRIEVAL_POLICY_PROJECTION
    }
    return "sha256:" + sha256_hex(canonical_json_bytes(projection))


class CellContract(CampaignModel):
    name: Literal["no_external_memory", "base_scaffold", "optimized_scaffold"]
    h: HSurface
    parent_snapshot_hash: str | None = None
    rollback_snapshot_hash: str
    base_snapshot: MemoryCognitiveSkillSnapshot | None = None
    optimized_snapshot: MemoryCognitiveSkillSnapshot | None = None

    @model_validator(mode="after")
    def _cell_contract(self) -> "CellContract":
        if self.name == "no_external_memory" and self.h.memory_snapshot_hash is not None:
            raise CampaignContractError("no-memory cell must omit memory snapshot hash")
        if self.name == "no_external_memory":
            if self.h.retrieval_policy_digest is not None:
                raise CampaignContractError(
                    "no-memory cell must omit retrieval policy digest"
                )
        if self.name != "no_external_memory":
            if not self.h.memory_snapshot_hash:
                raise CampaignContractError(f"{self.name} cell must bind a memory snapshot hash")
            _require_hash(self.h.memory_snapshot_hash, f"{self.name} snapshot")
            if not self.h.retrieval_policy_digest:
                raise CampaignContractError(
                    f"{self.name} cell must bind a retrieval policy digest"
                )
        _require_hash(self.rollback_snapshot_hash, "rollback_snapshot_hash")
        if self.parent_snapshot_hash is not None:
            _require_hash(self.parent_snapshot_hash, "parent_snapshot_hash")
        # The actual MemoryCognitiveSkillSnapshot objects (or None for the
        # no-memory cell) live alongside the spec hashes so the runner can
        # be built from the same snapshot the spec attests. The retrieval
        # policy digest is derived from the exact allowed projection so
        # any other base/optimized snapshot-field drift is rejected
        # later by the spec-level equality check.
        if self.name == "no_external_memory":
            if self.base_snapshot is not None or self.optimized_snapshot is not None:
                raise CampaignContractError(
                    "no-memory cell must not bind a real snapshot"
                )
        elif self.name == "base_scaffold":
            if self.base_snapshot is None:
                raise CampaignContractError("base_scaffold cell must bind base_snapshot")
            if self.base_snapshot.content_hash != self.h.memory_snapshot_hash:
                raise CampaignContractError(
                    "base_snapshot.content_hash must equal base memory_snapshot_hash"
                )
            if self.base_snapshot.query_max_results != BASE_QUERY_MAX_RESULTS:
                raise CampaignContractError(
                    f"base_snapshot.query_max_results must equal {BASE_QUERY_MAX_RESULTS}"
                )
            actual_digest = _retrieval_policy_digest(self.base_snapshot)
            if self.h.retrieval_policy_digest != actual_digest:
                raise CampaignContractError(
                    "base retrieval_policy_digest does not match the derived policy projection"
                )
            if self.parent_snapshot_hash is not None:
                raise CampaignContractError(
                    "base_scaffold cell must not declare a parent_snapshot_hash"
                )
        elif self.name == "optimized_scaffold":
            if self.optimized_snapshot is None:
                raise CampaignContractError(
                    "optimized_scaffold cell must bind optimized_snapshot"
                )
            if self.optimized_snapshot.content_hash != self.h.memory_snapshot_hash:
                raise CampaignContractError(
                    "optimized_snapshot.content_hash must equal optimized memory_snapshot_hash"
                )
            if self.optimized_snapshot.query_max_results != OPTIMIZED_QUERY_MAX_RESULTS:
                raise CampaignContractError(
                    f"optimized_snapshot.query_max_results must equal {OPTIMIZED_QUERY_MAX_RESULTS}"
                )
            actual_digest = _retrieval_policy_digest(self.optimized_snapshot)
            if self.h.retrieval_policy_digest != actual_digest:
                raise CampaignContractError(
                    "optimized retrieval_policy_digest does not match the derived policy projection"
                )
        return self


class ModelContract(CampaignModel):
    model_id: str
    model_digest: str
    base_url: str
    inference_parameters: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _loopback(self) -> "ModelContract":
        if not re.match(r"^https?://(127\.0\.0\.1|localhost|\[::1\])(?::\d+)?(?:/|$)", self.base_url):
            raise CampaignContractError("task model base_url must be loopback")
        _require_hash(self.model_digest, "model_digest")
        allowed = {"temperature", "max_tokens", "thinking", "extra_body"}
        if set(self.inference_parameters) != allowed:
            raise CampaignContractError(
                "inference_parameters must contain exact frozen fields"
            )
        temperature = self.inference_parameters["temperature"]
        max_tokens = self.inference_parameters["max_tokens"]
        thinking = self.inference_parameters["thinking"]
        if type(temperature) is not float or not 0.0 <= temperature <= 2.0:
            raise CampaignContractError("temperature must be a float in [0, 2]")
        if type(max_tokens) is not int or max_tokens < 1:
            raise CampaignContractError("max_tokens must be a positive strict integer")
        if type(thinking) is not bool:
            raise CampaignContractError("thinking must be a strict boolean")
        extra = self.inference_parameters.get("extra_body", {})
        if not isinstance(extra, dict) or set(extra) != {
            "top_p", "top_k", "reasoning_effort",
        }:
            raise CampaignContractError(
                "extra_body requires exact top_p/top_k/reasoning_effort"
            )
        top_p, top_k, reasoning_effort = (
            extra["top_p"], extra["top_k"], extra["reasoning_effort"],
        )
        if type(top_p) is not float or not 0.0 < top_p <= 1.0:
            raise CampaignContractError("top_p must be a float in (0, 1]")
        if type(top_k) is not int or top_k < 1:
            raise CampaignContractError("top_k must be a positive strict integer")
        if type(reasoning_effort) is not str or reasoning_effort not in {
            "none", "low", "medium", "high",
        }:
            raise CampaignContractError(
                "reasoning_effort must be one of none/low/medium/high"
            )
        if thinking is False and reasoning_effort != "none":
            raise CampaignContractError(
                "reasoning_effort must be none when thinking is false"
            )
        if thinking is True and reasoning_effort == "none":
            raise CampaignContractError(
                "reasoning_effort cannot be none when thinking is true"
            )
        return self


class EvaluatorContract(CampaignModel):
    evaluator_ref: str
    evaluator_digest: str
    authority_id: str
    verifiers: tuple[Literal["equals", "contains", "one_of"], ...]

    @model_validator(mode="after")
    def _deterministic(self) -> "EvaluatorContract":
        _require_hash(self.evaluator_digest, "evaluator_digest")
        if not self.verifiers:
            raise CampaignContractError("deterministic evaluator needs verifiers")
        return self


class CaseContract(CampaignModel):
    case_id: str
    split: Literal["development", "validation", "holdout"]
    view: Literal[
        "approved_target", "transfer", "replay_retention",
        "privacy", "safety", "efficiency",
    ]
    mandatory: bool
    approved_target: bool = False
    input_digest: str

    @model_validator(mode="after")
    def _case_contract(self) -> "CaseContract":
        _require_hash(self.input_digest, "input_digest")
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", self.case_id):
            raise CampaignContractError(
                f"case_id must be a lowercase slug with single hyphens: {self.case_id!r}"
            )
        return self


class SelectionDeclaration(CampaignModel):
    """Development-only declaration of the optimized-H selection source.

    The selection must name a nonempty list of development case IDs that
    drive the optimized-H selection rule. Validation and holdout cases
    cannot be used for selection."""

    source_split: Literal["development"] = "development"
    selected_cell: Literal["optimized_scaffold"] = "optimized_scaffold"
    evidence_case_ids: tuple[str, ...] = ()
    uses_validation: bool = False
    uses_holdout: bool = False

    @model_validator(mode="after")
    def _development_only(self) -> "SelectionDeclaration":
        if self.uses_validation or self.uses_holdout:
            raise CampaignContractError(
                "optimized-H selection cannot use validation or holdout evidence"
            )
        if not self.evidence_case_ids:
            raise CampaignContractError(
                "selection must declare a nonempty list of development case IDs"
            )
        return self


class BudgetContract(CampaignModel):
    token_limit: int = Field(ge=0, strict=True)
    cost_usd_limit: float = Field(ge=0.0, strict=True)
    wall_time_s_limit: float = Field(ge=0.0, strict=True)


class CampaignSpec(CampaignModel):
    schema_version: Literal[1] = 1
    campaign_id: str
    spec_digest: str = ""
    goal_family: str
    model: ModelContract
    evaluator: EvaluatorContract
    w_refs: tuple[str, ...]
    environment_digest: str
    repetition_seeds: tuple[int, ...] = Field(min_length=1)
    budget: BudgetContract
    cells: tuple[CellContract, ...]
    required_views: tuple[str, ...] = VIEWS
    cases: tuple[CaseContract, ...] = Field(min_length=1)
    selection: SelectionDeclaration
    protected_package_digests: dict[str, str]
    evaluator_digest: str
    # Frozen-cell-spanning references. The three cells must bind identical
    # refs and digests; only scaffold_h_snapshot_hash and retrieval_policy
    # allowlist may differ.
    blueprint_ref: str = "meta34-blueprint"
    workflow_ref: str = "meta34-workflow"
    blueprint_digest: str = ""
    workflow_digest: str = ""
    task_model_portfolio_ref: str = ""
    task_model_portfolio_digest: str = ""
    runner_id: str = "memory-aware-runner"
    runner_configuration_digest: str = ""

    @model_validator(mode="after")
    def _contract(self) -> "CampaignSpec":
        _require_hash(self.evaluator_digest, "evaluator_digest")
        _require_hash(self.environment_digest, "environment_digest")
        if self.evaluator_digest != self.evaluator.evaluator_digest:
            raise CampaignContractError("top-level evaluator_digest mismatch")
        if not self.w_refs:
            raise CampaignContractError("w_refs must declare at least one reference")
        if len(set(self.w_refs)) != len(self.w_refs):
            raise CampaignContractError("w_refs must be unique")
        for ref in self.w_refs:
            _require_hash(ref, "w_ref")
        if any(seed < 0 for seed in self.repetition_seeds):
            raise CampaignContractError("repetition_seeds must be nonnegative")
        if len(set(self.repetition_seeds)) != len(self.repetition_seeds):
            raise CampaignContractError("repetition_seeds must be unique")
        for value in self.model.inference_parameters.values():
            if isinstance(value, float) and not math.isfinite(value):
                raise CampaignContractError("inference_parameters must be finite")
        if tuple(cell.name for cell in self.cells) != CELLS:
            raise CampaignContractError("spec requires the canonical three H cells")
        if tuple(self.required_views) != VIEWS:
            raise CampaignContractError("spec must contain all six protected views in canonical order")
        if len(set(case.case_id for case in self.cases)) != len(self.cases):
            raise CampaignContractError("case IDs must be unique")
        for case in self.cases:
            if case.approved_target and case.split != "validation":
                raise CampaignContractError(
                    "approved-target eligibility cases must be preregistered validation cases"
                )
            if case.split == "holdout" and case.approved_target:
                raise CampaignContractError("holdout cannot be an approved-target")
        mandatory_views = {case.view for case in self.cases if case.mandatory}
        if mandatory_views != set(VIEWS):
            raise CampaignContractError("mandatory cases must cover all protected views")
        for view in VIEWS:
            for split in SPLITS:
                if not any(c.view == view and c.split == split for c in self.cases):
                    raise CampaignContractError(
                        f"missing {split} case for required view {view}"
                    )
        if set(self.protected_package_digests.keys()) != set(SPLITS):
            raise CampaignContractError("protected_package_digests must bind every split")
        for split in SPLITS:
            _require_hash(self.protected_package_digests[split], f"protected_package_digest[{split}]")
        # selection.evidence_case_ids must be a nonempty subset of the
        # development cases; this is the exact selection rule the build spec
        # requires.
        dev_case_ids = {c.case_id for c in self.cases if c.split == "development"}
        selection_ids = set(self.selection.evidence_case_ids)
        if len(selection_ids) != len(self.selection.evidence_case_ids):
            raise CampaignContractError("selection evidence_case_ids must be unique")
        if not selection_ids:
            raise CampaignContractError(
                "selection.evidence_case_ids must be a nonempty list"
            )
        if not selection_ids.issubset(dev_case_ids):
            raise CampaignContractError(
                "selection.evidence_case_ids must all belong to the development split"
            )
        if not any(
            case.split == "validation" and case.approved_target
            for case in self.cases
        ):
            raise CampaignContractError(
                "validation requires at least one approved_target case"
            )
        for split in SPLITS:
            mandatory_split_views = {
                case.view
                for case in self.cases
                if case.split == split and case.mandatory
            }
            if mandatory_split_views != set(VIEWS):
                raise CampaignContractError(
                    f"{split} mandatory cases must cover all protected views"
                )
        no_memory, base_cell, optimized = self.cells
        if any(cell.h.system_digest != EMPTY_SYSTEM_DIGEST for cell in self.cells):
            raise CampaignContractError("system_digest must be the canonical empty-system digest")
        base_frozen = base_cell.h
        for frozen_field in (
            "task_template_digest", "system_digest", "wrapper_digest",
            "resolver_digest", "corpus_digest", "policy_digest",
            "worker_implementation_digest", "output_parser_digest",
        ):
            if getattr(no_memory.h, frozen_field) != getattr(base_frozen, frozen_field):
                raise CampaignContractError(
                    f"non-memory H surface drift: {frozen_field} differs on no_external_memory"
                )
            if getattr(optimized.h, frozen_field) != getattr(base_frozen, frozen_field):
                raise CampaignContractError(
                    f"non-memory H surface drift: {frozen_field} differs on optimized_scaffold"
                )
        if optimized.parent_snapshot_hash != base_cell.h.memory_snapshot_hash:
            raise CampaignContractError(
                "optimized parent_snapshot_hash must equal base memory_snapshot_hash"
            )
        if base_cell.rollback_snapshot_hash != base_cell.h.memory_snapshot_hash:
            raise CampaignContractError(
                "base_scaffold rollback_snapshot_hash must equal base memory_snapshot_hash"
            )
        if optimized.rollback_snapshot_hash != base_cell.h.memory_snapshot_hash:
            raise CampaignContractError(
                "optimized_scaffold rollback_snapshot_hash must equal base memory_snapshot_hash"
            )
        # The retrieval policy digest is the SOLE cell-specific axis
        # besides the snapshot content hash and the parent/rollback
        # bindings. It must differ between base and optimized so the
        # policy delta is load-bearing; identical digests would mean the
        # two cells see the same model-visible advice and the campaign
        # attribution collapses.
        if base_cell.h.retrieval_policy_digest is None:
            raise CampaignContractError(
                "base_scaffold must bind a retrieval policy digest"
            )
        if optimized.h.retrieval_policy_digest is None:
            raise CampaignContractError(
                "optimized_scaffold must bind a retrieval policy digest"
            )
        if (
            base_cell.h.retrieval_policy_digest
            == optimized.h.retrieval_policy_digest
        ):
            raise CampaignContractError(
                "base/optimized retrieval_policy_digest must differ"
            )
        # The actual snapshot objects must agree with their declared
        # hashes (the cell-level validator already enforced this, but
        # the spec re-asserts to keep frozen-axis drift atomic).
        base_actual = base_cell.base_snapshot
        optimized_actual = optimized.optimized_snapshot
        if base_actual is None or optimized_actual is None:
            raise CampaignContractError(
                "base/optimized cells must bind their actual snapshot objects"
            )
        if base_actual.query_max_results != BASE_QUERY_MAX_RESULTS:
            raise CampaignContractError(
                f"base actual snapshot query_max_results must be {BASE_QUERY_MAX_RESULTS}"
            )
        if optimized_actual.query_max_results != OPTIMIZED_QUERY_MAX_RESULTS:
            raise CampaignContractError(
                f"optimized actual snapshot query_max_results must be {OPTIMIZED_QUERY_MAX_RESULTS}"
            )
        # Reject any other base/optimized snapshot-field drift besides
        # the declared identity / parent / content_hash axes and the
        # allowed retrieval projection. The retrieval projection is the
        # only policy axis that may differ; everything else must match.
        _ALLOWED_DELTA_FIELDS = {
            "snapshot_id",  # identity: base vs optimized names
            "parent_snapshot_hash",  # identity: optimized declares base as parent
            "content_hash",  # identity: changes when parent or policy changes
            "query_max_results",  # the declared retrieval delta
        }
        for field in sorted(set(base_actual.model_dump(mode="python").keys())
                            | set(optimized_actual.model_dump(mode="python").keys())):
            if field in _ALLOWED_DELTA_FIELDS:
                continue
            base_val = getattr(base_actual, field, None)
            opt_val = getattr(optimized_actual, field, None)
            if base_val != opt_val:
                raise CampaignContractError(
                    f"non-H snapshot drift on {field!r} between base and optimized"
                )
        return self

    @model_validator(mode="after")
    def _populate_frozen_axes(self) -> "CampaignSpec":
        if not self.task_model_portfolio_ref:
            object.__setattr__(
                self, "task_model_portfolio_ref",
                _sanitize_id(f"portfolio-{self.model.model_id}"),
            )
        if not self.task_model_portfolio_digest:
            object.__setattr__(
                self, "task_model_portfolio_digest", self.model.model_digest,
            )
        if not self.blueprint_digest:
            object.__setattr__(
                self, "blueprint_digest",
                "sha256:" + sha256_hex(self.blueprint_ref.encode("utf-8")),
            )
        if not self.workflow_digest:
            object.__setattr__(
                self, "workflow_digest",
                "sha256:" + sha256_hex(self.workflow_ref.encode("utf-8")),
            )
        if not self.runner_configuration_digest:
            object.__setattr__(
                self, "runner_configuration_digest",
                "sha256:" + sha256_hex(canonical_json_bytes({
                    "runner_id": self.runner_id,
                    "model_id": self.model.model_id,
                    "model_digest": self.model.model_digest,
                    "base_url": self.model.base_url,
                    "inference_parameters": self.model.inference_parameters,
                    "environment_digest": self.environment_digest,
                    "evaluator_digest": self.evaluator_digest,
                    "w_refs": list(self.w_refs),
                })),
            )
        if not self.spec_digest:
            object.__setattr__(self, "spec_digest", self._compute_digest())
        elif self.spec_digest != self._compute_digest():
            raise CampaignContractError("campaign spec digest mismatch after default population")
        return self

    def _compute_digest(self) -> str:
        return sha256_hex(canonical_json_bytes(self.model_dump(mode="json", exclude={"spec_digest"})))

    def digest(self) -> str:
        return self._compute_digest()

    def package_digest_for(self, split: str) -> str:
        return self.protected_package_digests[split]


class ProtectedInputPackage(CampaignModel):
    campaign_id: str
    split: Literal["development", "validation", "holdout"]
    package_digest: str
    cases: tuple[dict[str, Any], ...]

    @model_validator(mode="after")
    def _digest(self) -> "ProtectedInputPackage":
        material = self.model_dump(mode="json", exclude={"package_digest"})
        expected = "sha256:" + sha256_hex(canonical_json_bytes(material))
        if self.package_digest != expected:
            raise CampaignContractError("protected input package digest mismatch")
        return self


def case_input_digest(task: dict[str, Any], assertion: dict[str, Any]) -> str:
    """Exact pre-inference binding for a protected task and assertion."""
    task = Task.model_validate(task).model_dump(mode="json")
    return "sha256:" + sha256_hex(canonical_json_bytes({
        "task": task,
        "assertion": assertion,
    }))


# ---------------------------------------------------------------------------
# Holdout ledger
# ---------------------------------------------------------------------------


class HoldoutConsumptionLedger:
    """Create-only one-time ledger. ``consume`` is the first operation that can
    touch the sealed holdout file. A second attempt fails before any read."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()

    def consume(
        self,
        *,
        campaign_id: str,
        spec_digest: str,
        holdout_package_digest: str,
        evaluator_digest: str,
    ) -> str:
        key = self._key(
            campaign_id=campaign_id,
            spec_digest=spec_digest,
            holdout_package_digest=holdout_package_digest,
            evaluator_digest=evaluator_digest,
        )
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{key}.json"
        payload = json.dumps({"key": key}, sort_keys=True).encode()
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        try:
            fd = os.open(path, flags, 0o600)
        except FileExistsError as exc:
            raise HoldoutAlreadyConsumedError(
                "sealed holdout has already been consumed for this campaign"
            ) from exc
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        return key

    @staticmethod
    def _key(
        *,
        campaign_id: str,
        spec_digest: str,
        holdout_package_digest: str,
        evaluator_digest: str,
    ) -> str:
        return sha256_hex(canonical_json_bytes({
            "campaign_id": campaign_id,
            "spec_digest": spec_digest,
            "holdout_package_digest": holdout_package_digest,
            "evaluator_digest": evaluator_digest,
        }))


def _validate_package(
    package: ProtectedInputPackage,
    *,
    spec: CampaignSpec,
    split: str,
) -> None:
    if package.campaign_id != spec.campaign_id:
        raise CampaignContractError(
            f"{split} package campaign_id does not match the frozen spec"
        )
    if package.split != split:
        raise CampaignContractError(
            f"{split} package declares split={package.split!r}"
        )
    expected_digest = spec.package_digest_for(split)
    if package.package_digest != expected_digest:
        raise CampaignContractError(
            f"{split} package digest does not match the frozen spec"
        )
    expected_cases = {case.case_id: case for case in spec.cases if case.split == split}
    package_ids = [item.get("case_id") for item in package.cases]
    if len(package_ids) != len(set(package_ids)):
        raise CampaignContractError(f"{split} package contains duplicate case IDs")
    if set(package_ids) != set(expected_cases):
        raise CampaignContractError(f"{split} package case membership differs from frozen spec")
    for item in package.cases:
        task, assertion = item.get("task"), item.get("assertion")
        if not isinstance(task, dict) or not isinstance(assertion, dict):
            raise CampaignContractError(f"{split} package case lacks task/assertion material")
        if task.get("id") != item["case_id"]:
            raise CampaignContractError("protected package task.id must equal case_id")
        actual = case_input_digest(task, assertion)
        if item.get("input_digest") != actual or expected_cases[item["case_id"]].input_digest != actual:
            raise CampaignContractError("protected case input digest mismatch")


def load_protected_inputs(
    path: str | Path,
    *,
    spec: CampaignSpec,
    split: str,
    ledger: HoldoutConsumptionLedger | None = None,
) -> tuple[str, ProtectedInputPackage]:
    """Validate the protected package against the spec. For the holdout
    split, the ledger is consumed BEFORE the file is opened and the file
    is read. Returns ``(ledger_key, package)`` so the caller can attest
    receipt-before-read and use the package directly."""
    if split == "holdout":
        if ledger is None:
            raise CampaignContractError(
                "holdout split requires a HoldoutConsumptionLedger"
            )
        key = ledger.consume(
            campaign_id=spec.campaign_id,
            spec_digest=spec.digest(),
            holdout_package_digest=spec.package_digest_for("holdout"),
            evaluator_digest=spec.evaluator_digest,
        )
    else:
        key = ""
    package = ProtectedInputPackage.model_validate_json(
        Path(path).read_text(encoding="utf-8")
    )
    _validate_package(package, spec=spec, split=split)
    return key, package


# ---------------------------------------------------------------------------
# Deterministic verifier (no model judge, no semantic_judge)
# ---------------------------------------------------------------------------


ASSERTION_FIELDS = {"kind", "value", "schema", "forbidden_substrings"}
VERIFIER_KINDS = ("equals", "contains", "one_of")
_SECRET_FRAGMENT = re.compile(r"(?:api[_ -]?key|credential|password|raw[_ -]?secret|secret|token)\s*[:=]", re.I)
_SECRET_SCAN_KINDS = (str,)


def _actual_contains_secret_material(actual: Any) -> bool:
    """Conservative secret detection on the verifier's actual output.

    Returns True when the actual value (string, list, dict, or any nested
    scalar string) matches the canonical ``assert_secret_safe`` heuristic
    OR carries the conservative ``key=value`` marker for the credential
    vocabulary. The check never raises so the secret itself is never
    echoed back to the caller; it only reports a boolean.
    """
    from metaharness.portable.integrity import assert_secret_safe, PortableIntegrityError
    candidates: list[str] = []
    if isinstance(actual, _SECRET_SCAN_KINDS):
        candidates.append(actual)
    elif isinstance(actual, (list, tuple)):
        for item in actual:
            if isinstance(item, _SECRET_SCAN_KINDS):
                candidates.append(item)
    elif isinstance(actual, dict):
        for value in actual.values():
            if isinstance(value, _SECRET_SCAN_KINDS):
                candidates.append(value)
    if not candidates:
        return False
    # Reject any candidate that the canonical scanner flags.
    for value in candidates:
        try:
            assert_secret_safe(value, location="deterministic_verify actual")
        except PortableIntegrityError:
            return True
    # Reject conservative key=value markers for the credential vocabulary.
    for value in candidates:
        if _SECRET_FRAGMENT.search(value):
            return True
    return False


def deterministic_verify(actual: Any, assertion: dict[str, Any]) -> bool:
    if not isinstance(assertion, dict) or set(assertion) - ASSERTION_FIELDS:
        raise CampaignContractError("assertion schema contains unknown fields")
    # Safety: secret-like actual output is rejected even when the equality
    # check would otherwise pass. The frozen stop condition says stop on
    # secret capture. The check never raises so the secret itself is not
    # echoed back to the caller.
    if _actual_contains_secret_material(actual):
        return False
    kind = assertion.get("kind")
    if kind not in VERIFIER_KINDS:
        raise CampaignContractError(
            "only equals, contains, and one_of verifiers are permitted"
        )
    value = assertion.get("value")
    has_forbidden = "forbidden_substrings" in assertion
    forbidden = assertion.get("forbidden_substrings", ())
    if forbidden is None:
        forbidden = ()
    if not isinstance(forbidden, (list, tuple)):
        raise CampaignContractError("forbidden_substrings must be a list of strings")
    if has_forbidden and len(forbidden) == 0:
        raise CampaignContractError(
            "forbidden_substrings must list at least one fragment"
        )
    if any(not isinstance(fragment, str) or fragment == "" for fragment in forbidden):
        raise CampaignContractError("forbidden_substrings must be non-empty strings")
    if kind == "contains":
        if isinstance(actual, str):
            if not isinstance(value, str) or not value:
                raise CampaignContractError("contains requires a non-empty string value")
        elif isinstance(actual, (list, tuple)):
            if not isinstance(value, list) or not value:
                raise CampaignContractError("contains requires a non-empty list value")
        elif isinstance(actual, dict):
            if not isinstance(value, dict) or not value:
                raise CampaignContractError("contains requires a non-empty map value")
        else:
            raise CampaignContractError("contains requires a string, list, or map actual")
    if kind == "one_of" and (not isinstance(value, list) or not value):
        raise CampaignContractError("one_of requires a non-empty list")
    if any(fragment in json.dumps(actual, sort_keys=True, default=str) for fragment in forbidden):
        return False
    if kind == "equals":
        return actual == value
    if kind == "contains":
        if isinstance(actual, dict) and isinstance(value, dict):
            return all(actual.get(k) == v for k, v in value.items())
        return value in actual
    return actual in value


# ---------------------------------------------------------------------------
# Evidence storage
# ---------------------------------------------------------------------------


def _create_only_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, allow_nan=False,
    ).encode()
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise CampaignEvidenceError(f"immutable evidence already exists: {path.name}") from exc
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def _resolve_budget_envelope(spec: CampaignSpec) -> BudgetEnvelope:
    return BudgetEnvelope(
        token_limit=int(spec.budget.token_limit),
        cost_usd_limit=float(spec.budget.cost_usd_limit),
        wall_time_s_limit=float(spec.budget.wall_time_s_limit),
    )


def _build_assertion_from_payload(payload: dict[str, Any]) -> EvalAssertion:
    """Wrap an assertion payload as an EvalAssertion. Accepts the canonical
    ``{"kind": "equals", "value": "..."}`` form and translates it to the
    EvalAssertion's success_check route."""
    if "kind" in payload and "value" in payload:
        kind = payload["kind"]
        if kind not in {"equals", "contains", "one_of", "tol"}:
            raise CampaignContractError(f"unknown assertion kind: {kind!r}")
        return EvalAssertion.model_validate({
            "success_check": {kind: payload["value"]},
        })
    return EvalAssertion.model_validate(payload)


def _lookup_package_assertion(
    package: ProtectedInputPackage, case_id: str
) -> EvalAssertion:
    for item in package.cases:
        if item.get("case_id") == case_id:
            raw = item.get("assertion")
            if not isinstance(raw, dict):
                raise CampaignContractError(
                    f"package item for {case_id} is missing a dict assertion"
                )
            return _build_assertion_from_payload(raw)
    raise CampaignContractError(f"package missing case {case_id}")


def _build_worker_config(
    spec: CampaignSpec, cell: str, repetition: int, seed: int,
) -> dict[str, Any]:
    return {
        "cell": cell,
        "repetition": repetition,
        "seed": seed,
        "model_id": spec.model.model_id,
        "model_digest": spec.model.model_digest,
        "base_url": spec.model.base_url,
        "inference_parameters": dict(spec.model.inference_parameters),
    }


def _per_attempt_metrics(result: WorkerResult) -> EvalMetrics:
    return EvalMetrics(
        tokens_in=int(getattr(result, "tokens_in", 0) or 0),
        tokens_out=int(getattr(result, "tokens_out", 0) or 0),
        cost_usd=float(getattr(result, "cost_usd", 0.0) or 0.0),
        latency_s=float(getattr(result, "latency_s", 0.0) or 0.0),
    )


def _enforce_per_attempt_budget(
    spec: CampaignSpec,
    *,
    cell: str,
    metrics: EvalMetrics,
) -> None:
    budget = spec.budget
    if metrics.tokens_in + metrics.tokens_out > int(budget.token_limit):
        raise CampaignContractError(
            f"{cell} exceeded per-attempt token budget"
        )
    if metrics.cost_usd > float(budget.cost_usd_limit):
        raise CampaignContractError(f"{cell} exceeded per-attempt cost budget")
    if metrics.latency_s > float(budget.wall_time_s_limit):
        raise CampaignContractError(
            f"{cell} exceeded per-attempt wall-time budget"
        )


def _enforce_total_budget(spec: CampaignSpec, total: EvalMetrics) -> None:
    if total.tokens_in + total.tokens_out > int(spec.budget.token_limit):
        raise CampaignContractError("aggregate token budget exceeded")
    if total.cost_usd > float(spec.budget.cost_usd_limit):
        raise CampaignContractError("aggregate cost budget exceeded")
    if total.latency_s > float(spec.budget.wall_time_s_limit):
        raise CampaignContractError("aggregate wall-time budget exceeded")


def _verdict_from_verifier(run_output: Any, assertion: dict[str, Any]) -> str:
    return "pass" if deterministic_verify(run_output, assertion) else "fail"


def _assert_secret_safe_worker_result(value: dict[str, Any]) -> None:
    """Fail closed before nested model output or raw text reaches evidence."""
    from metaharness.portable.integrity import PortableIntegrityError, assert_secret_safe

    try:
        assert_secret_safe(value, location="worker result evidence")
    except PortableIntegrityError as exc:
        raise CampaignContractError("worker result contains secret-like material") from exc


# ---------------------------------------------------------------------------
# Runner configuration / assertion enforcement on the factory output
# ---------------------------------------------------------------------------


def _enforce_runner_contract(
    runner: Any,
    *,
    spec: CampaignSpec,
    cell: str,
    seed: int | None = None,
    runtime_attestation: RuntimeAttestation | None = None,
) -> None:
    """Enforce the load-bearing runner contract for each cell. The wrapper
    must be a MemoryAwareRunner; the cell's memory enablement must match
    the cell's H axis; the runner's snapshot (if present) must equal the
    actual MemoryCognitiveSkillSnapshot the spec attested for the cell;
    and the inner runner's model/inference config must match the frozen
    ``ModelContract``/``inference_parameters`` (real execution only).
    """
    if not isinstance(runner, MemoryAwareRunner):
        raise CampaignContractError(
            "load-bearing MemoryAwareRunner is required for every cell"
        )
    if (
        runtime_attestation is not None
        and runtime_attestation.mode == "production"
    ):
        from metaharness.harness import local as local_module

        if type(runner.inner) is not local_module.OpenAICompatWorker:
            raise CampaignContractError(
                "production runner must use the exact canonical OpenAICompatWorker class"
            )
    if cell == "no_external_memory":
        if runner.memory_enabled:
            raise CampaignContractError(
                "no_external_memory cell must use memory_enabled=False"
            )
    else:
        if not runner.memory_enabled:
            raise CampaignContractError(
                f"{cell} memory cell must use memory_enabled=True"
            )
    # The runner's snapshot (when memory is enabled) must be the actual
    # spec-attested snapshot for the cell. The no-memory cell has no
    # expected snapshot, so the runner must not supply one.
    expected_hash = next(
        (c.h.memory_snapshot_hash for c in spec.cells if c.name == cell),
        None,
    )
    if expected_hash is None:
        if runner.snapshot is not None:
            raise CampaignContractError(
                f"{cell} runner must not carry a snapshot (cell is memory-disabled)"
            )
    else:
        if runner.snapshot is None:
            raise CampaignContractError(
                f"{cell} runner must carry the actual spec-attested snapshot"
            )
        if runner.snapshot.content_hash != expected_hash:
            raise CampaignContractError(
                f"{cell} runner snapshot content_hash does not match the frozen spec"
            )
    # Model/inference contract: the inner runner must attest its
    # model/base_url/temperature/max_tokens/thinking/extra_body. A missing
    # attestation is rejected before inference unless this is the explicit
    # test-only hermetic adapter protocol.
    _enforce_runner_model_contract(runner, spec=spec, cell=cell, seed=seed)


def _enforce_runner_model_contract(
    runner: Any,
    *,
    spec: CampaignSpec,
    cell: str,
    seed: int | None = None,
) -> None:
    """Bind the actual runner/model contract. Real execution must attest
    model + base_url + temperature/max_tokens/thinking/extra_body to
    exactly match the frozen ``ModelContract``/``inference_parameters``.
    Only the explicit ``tests-only-v1`` hermetic adapter protocol may omit
    transport attestation. Public real execution therefore fails closed when
    an outbound model adapter cannot attest its immutable request config.
    """
    attested = _runner_model_attestation(runner)
    if attested is None:
        if getattr(runner, "hermetic_campaign_adapter", False):
            return
        raise CampaignContractError(
            f"{cell} runner lacks immutable model/transport attestation"
        )
    if not isinstance(attested, dict):
        raise CampaignContractError(
            f"{cell} runner model_frozen_config must be a dict or None"
        )
    expected = dict(spec.model.inference_parameters)
    inner = getattr(runner, "inner", None)
    if inner is not None and type(inner).__name__ == "OpenAICompatWorker" and (
        inner.system_prompt != "" or inner.tool_registry is not None
        or inner.context_budget is not None or inner.max_tool_rounds != 5
    ):
        raise CampaignContractError("concrete worker violates canonical non-tool posture")
    actual = {
        "model_id": attested.get("model"),
        "base_url": attested.get("base_url"),
        "temperature": attested.get("temperature"),
        "max_tokens": attested.get("max_tokens"),
        "thinking": attested.get("thinking"),
        "extra_body": attested.get("extra_body"),
    }
    if seed is not None and isinstance(actual["extra_body"], dict):
        actual["extra_body"] = {
            key: value for key, value in actual["extra_body"].items() if key != "seed"
        }
    expected_view = {
        "model_id": spec.model.model_id,
        "base_url": spec.model.base_url,
        "temperature": expected.get("temperature"),
        "max_tokens": expected.get("max_tokens"),
        "thinking": expected.get("thinking"),
        "extra_body": expected.get("extra_body") or {},
    }
    if actual != expected_view:
        raise CampaignContractError(
            f"{cell} runner model/inference config drift: "
            f"expected {expected_view!r}, got {actual!r}"
        )
    if inner is not None and type(inner).__name__ == "OpenAICompatWorker":
        expected_body = dict(expected.get("extra_body") or {})
        if seed is not None:
            expected_body["seed"] = seed
        if dict(inner.extra_body) != expected_body:
            raise CampaignContractError("concrete worker extra_body/seed drift")


def _runner_model_attestation(runner: Any) -> dict[str, Any] | None:
    """Return the immutable outbound-model projection for a campaign runner.

    Third-party real runners may expose ``model_frozen_config`` directly. For
    the repository's concrete OpenAI-compatible worker, derive the same
    projection from its public, constructor-frozen fields so this bounded
    campaign surface does not require a change to the generic Runner API.
    """
    attested = getattr(runner, "model_frozen_config", None)
    if attested is not None:
        return attested
    inner = getattr(runner, "inner", None)
    try:
        from metaharness.harness.local import OpenAICompatWorker
    except ImportError:
        return None
    if not isinstance(inner, OpenAICompatWorker):
        return None
    return {
        "model": inner.model,
        "base_url": inner.base_url,
        "temperature": inner.temperature,
        "max_tokens": inner.max_tokens,
        "thinking": inner.thinking,
        "extra_body": dict(inner.extra_body),
    }


def _enforce_worker_result_contract(
    result: Any,
    *,
    spec: CampaignSpec,
    task: Task,
) -> None:
    """Require the canonical result emitted by the frozen production worker.

    The raw response is the source of the parsed output. Identity, terminal
    state, and the complete WorkerResult schema are independently checked so a
    locally recomputed row digest cannot bless substituted worker evidence.
    """
    if type(result) is not WorkerResult:
        raise CampaignContractError("inner runner must return canonical WorkerResult")
    dumped = result.model_dump(mode="json")
    if set(dumped) != set(WorkerResult.model_fields):
        raise CampaignContractError("WorkerResult schema is not exact")
    try:
        canonical = WorkerResult.model_validate(dumped)
    except Exception as exc:
        raise CampaignContractError("WorkerResult schema is invalid") from exc
    if canonical.model_dump(mode="json") != dumped:
        raise CampaignContractError("WorkerResult is not canonically serialized")
    if (
        result.task_id != task.id
        or result.worker_id != spec.runner_id
        or result.model != spec.model.model_id
    ):
        raise CampaignContractError(
            "WorkerResult task/worker/model identity differs from frozen spec"
        )
    if result.error is not None or result.error_kind is not None or result.timed_out:
        raise CampaignContractError("WorkerResult is not a successful terminal result")
    if result.tool_calls or result.workspace_root:
        raise CampaignContractError(
            "WorkerResult violates the canonical non-tool execution posture"
        )
    reparsed = _canonical_parse_output(
        result.raw_text,
        expect_json=bool(task.output_schema),
    )
    if reparsed != result.output:
        raise CampaignContractError(
            "WorkerResult output is not the canonical parse of raw_text"
        )


# ---------------------------------------------------------------------------
# Report / protected evidence construction
# ---------------------------------------------------------------------------


def _build_report_for(
    *,
    spec: CampaignSpec,
    split: str,
    cell: str,
    view: str,
    case_attempts: dict[str, list[dict[str, Any]]],
    package: ProtectedInputPackage,
    report_id: str,
) -> EvaluationReport:
    """Construct an EvaluationReport for one (split, cell, view). The holdout
    split seals the surface: assertion / per-case digest / per-attempt output /
    verifier detail are concealed; only verdict and metrics are published.
    Visible splits use the actual protected-package assertion."""
    is_holdout = split == "holdout"
    case_ids = [c.case_id for c in spec.cases if c.split == split and c.view == view]
    build_cases: list[EvalCaseResult] = []
    metrics = EvalMetrics()
    for case_id in case_ids:
        attempts = case_attempts.get(case_id, [])
        eval_attempts = []
        for attempt in attempts:
            eval_attempts.append(EvalAttemptResult(
                repetition=attempt["repetition"],
                verdict=attempt["verdict"],
                scorer="protected-deterministic",
                detail="",
                output=None if is_holdout else attempt.get("output"),
                metrics=EvalMetrics.model_validate(attempt["metrics"]),
            ))
            metrics = metrics.plus(eval_attempts[-1].metrics)
        verdict = (
            "fail" if any(a.verdict == "fail" for a in eval_attempts)
            else "pass"
        )
        if is_holdout:
            assertion = None
            assertion_digest = None
        else:
            assertion = _lookup_package_assertion(package, case_id)
            assertion_digest = sha256_hex(
                canonical_json_bytes(assertion.model_dump(mode="json"))
            )
        build_cases.append(EvalCaseResult(
            case_id=case_id,
            split=split,
            assertion_kind="success_check",
            assertion_digest=assertion_digest,
            assertion=assertion,
            attempts=eval_attempts,
            verdict=verdict,
        ))
    counts = {"pass": 0, "fail": 0, "unverified": 0}
    for c in build_cases:
        counts[c.verdict] += 1
    report_values = {
        "schema_version": 1,
        "id": _sanitize_id(report_id),
        "blueprint_ref": ArtifactRef(id=spec.blueprint_ref, version=1),
        "eval_ref": ArtifactRef(id=spec.evaluator.evaluator_ref, version=1),
        "split": split,
        "blueprint_digest": _strip_sha256_prefix(spec.blueprint_digest),
        "workflow_digest": _strip_sha256_prefix(spec.workflow_digest),
        "eval_digest": _strip_sha256_prefix(spec.evaluator.evaluator_digest),
        "runner_id": spec.runner_id,
        "cases": build_cases,
        "metrics": metrics,
        "passed": counts["pass"],
        "failed": counts["fail"],
        "unverified": counts["unverified"],
        "created_at": time.time(),
    }
    digest_payload = {
        key: (
            value.model_dump(mode="json")
            if hasattr(value, "model_dump")
            else [item.model_dump(mode="json") for item in value]
            if key == "cases"
            else value
        )
        for key, value in report_values.items()
        if key not in {"id", "created_at", "content_digest"}
    }
    report_values["content_digest"] = sha256_hex(
        canonical_json_bytes(digest_payload)
    )
    return EvaluationReport.model_validate(report_values)


def _phase_execution(
    *,
    spec: CampaignSpec,
    package: ProtectedInputPackage,
    split: str,
    runner_factory: Callable[..., Any],
    report_store: EvaluationReportStore,
    runtime_attestation: RuntimeAttestation,
    runtime_guard: Callable[[], None],
    campaign_metrics: dict[str, EvalMetrics] | None = None,
) -> tuple[list[dict[str, Any]], dict[tuple[str, str], EvaluationReport], dict[str, MemoryCognitiveSkillSnapshot]]:
    """Run all (cell, case, repetition) attempts for one split. Returns the
    per-attempt rows (with the complete WorkerResult model_dump preserved),
    a mapping of (cell, view) -> EvaluationReport, and the actual snapshot
    object used by the runner for each cell."""
    from_cases = {c.case_id: c for c in spec.cases if c.split == split}
    if {item.get("case_id") for item in package.cases} != set(from_cases):
        raise CampaignContractError(
            f"{split} package case membership differs from frozen spec"
        )
    raw_rows: list[dict[str, Any]] = []
    by_cell_view: dict[str, dict[str, dict[str, list[dict[str, Any]]]]] = {
        cell: {view: {} for view in VIEWS} for cell in CELLS
    }
    total_metrics = campaign_metrics if campaign_metrics is not None else {
        cell: EvalMetrics() for cell in CELLS
    }
    actual_snapshots: dict[str, MemoryCognitiveSkillSnapshot] = {}

    for cell in CELLS:
        for case_id, case in from_cases.items():
            package_item = next(
                (it for it in package.cases if it.get("case_id") == case_id),
                None,
            )
            if package_item is None:
                raise CampaignContractError(
                    f"package missing case {case_id} for split {split}"
                )
            if package_item.get("input_digest") != case.input_digest:
                raise CampaignContractError("protected case input digest mismatch")
            assertion = package_item.get("assertion")
            if not isinstance(assertion, dict):
                raise CampaignContractError("case assertion is not a dict")
            if assertion.get("kind") not in spec.evaluator.verifiers:
                raise CampaignContractError(
                    "case assertion is not a registered deterministic verifier"
                )
            for repetition, seed in enumerate(spec.repetition_seeds, 1):
                runtime_guard()
                runner_config = _build_worker_config(spec, cell, repetition, seed)
                try:
                    runner = runner_factory(cell, repetition, seed, runner_config)
                except TypeError as exc:
                    raise CampaignContractError(
                        "runner factory must accept (cell, repetition, seed, runner_config)"
                    ) from exc
                _enforce_runner_contract(
                    runner,
                    spec=spec,
                    cell=cell,
                    seed=seed,
                    runtime_attestation=runtime_attestation,
                )
                # Canonical request-configuration attestation: the exact
                # contract the runner is bound to at this attempt. The
                # verify_campaign path re-derives the model-visible advice
                # chain, so any post-hoc drift on the model config must
                # fail the reload.
                runner_model_attestation = _runner_model_attestation(runner)
                if runner_model_attestation is not None:
                    attestation_digest = sha256_hex(
                        canonical_json_bytes(runner_model_attestation)
                    )
                else:
                    attestation_digest = None
                # Capture the actual snapshot the runner used. Every attempt
                # in a cell must attest the same content_hash.
                if runner.snapshot is not None:
                    seen_hash = actual_snapshots.get(cell)
                    if seen_hash is None:
                        actual_snapshots[cell] = runner.snapshot
                    elif seen_hash.content_hash != runner.snapshot.content_hash:
                        raise CampaignContractError(
                            f"{cell} attempts must use a single snapshot content_hash"
                        )
                task = Task.model_validate(package_item["task"])
                try:
                    result = asyncio.run(
                        runner.run(task.model_copy(deep=True))
                    )
                finally:
                    runtime_guard()
                _enforce_worker_result_contract(
                    result, spec=spec, task=task,
                )
                metrics = _per_attempt_metrics(result)
                _enforce_per_attempt_budget(spec, cell=cell, metrics=metrics)
                total_metrics[cell] = total_metrics[cell].plus(metrics)
                verdict = _verdict_from_verifier(result.output, assertion)
                # Per-cell / per-attempt memory contract: memory cells must
                # have at least one accepted CONSULT receipt; no-memory must
                # not consult.
                receipts = list(runner.receipts)
                # Use the per-run slice so a reused runner cannot satisfy
                # a later attempt with an old receipt.
                current_run_receipts = list(runner.last_evidence.current_run_receipts)
                emitted_receipt_hashes = [
                    receipt.content_hash for receipt in receipts
                ]
                if (
                    emitted_receipt_hashes != current_run_receipts
                    or len(emitted_receipt_hashes)
                    != len(set(emitted_receipt_hashes))
                ):
                    raise CampaignContractError(
                        "attempt receipts must equal the exact current-run chain"
                    )
                if cell == "no_external_memory":
                    if any(r.content_hash in current_run_receipts for r in receipts
                           if getattr(r, "phase", "") == "consult"):
                        raise CampaignContractError(
                            "no_external_memory cell must not emit CONSULT receipts"
                        )
                else:
                    if not any(
                        r.content_hash in current_run_receipts
                        and getattr(r, "phase", "") == "consult"
                        and getattr(r, "accepted", False)
                        for r in receipts
                    ):
                        raise CampaignContractError(
                            f"{cell} memory cell must emit at least one accepted CONSULT receipt in this run"
                        )
                request_digest = sha256_hex(canonical_json_bytes({
                    "task": task.model_dump(mode="json"),
                    "assertion": assertion,
                    "cell": cell,
                    "repetition": repetition,
                    "seed": seed,
                    "runner_config": runner_config,
                }))
                request_config_digest = sha256_hex(canonical_json_bytes(
                    {"cell": cell, "repetition": repetition, "seed": seed,
                     "runner_config": runner_config}
                ))
                evidence = runner.last_evidence
                # Persist the complete WorkerResult model_dump, not only
                # the output field. Raw protected output must survive in
                # the protected evidence row.
                worker_result_dump = result.model_dump(mode="json")
                _assert_secret_safe_worker_result(worker_result_dump)
                receipts_dump = []
                for receipt in receipts:
                    if hasattr(receipt, "model_dump"):
                        receipts_dump.append(receipt.model_dump(mode="json"))
                    elif isinstance(receipt, dict):
                        receipts_dump.append(receipt)
                    else:
                        receipts_dump.append({"raw": str(receipt)})
                row = {
                    "cell": cell,
                    "case_id": case_id,
                    "view": case.view,
                    "split": split,
                    "mandatory": case.mandatory,
                    "approved_target": case.approved_target,
                    "repetition": repetition,
                    "seed": seed,
                    "verdict": verdict,
                    "assertion": assertion,
                    "request_digest": request_digest,
                    "request_config_digest": request_config_digest,
                    "runner_config": runner_config,
                    "metrics": metrics.model_dump(mode="json"),
                    "worker_result": worker_result_dump,
                    "worker_result_digest": sha256_hex(canonical_json_bytes(worker_result_dump)),
                    "output": result.output,
                    "receipts": receipts_dump,
                    # Full evidence chain: every field is independently
                    # verified by ``verify_campaign``; tampering any link
                    # must fail the verification.
                    "advice_digest": getattr(evidence, "advice_digest", None),
                    "advice_changed": getattr(evidence, "advice_changed", None),
                    "inner_task_digest": getattr(evidence, "inner_task_digest", None),
                    "outer_task_digest": getattr(evidence, "outer_task_digest", None),
                    "base_task_digest": getattr(evidence, "base_task_digest", None),
                    "outer_task": getattr(evidence, "outer_task", None),
                    "inner_task": getattr(evidence, "inner_task", None),
                    "consult_receipt_hash": getattr(evidence, "consult_receipt_hash", None),
                    "log_receipt_hash": getattr(evidence, "log_receipt_hash", None),
                    "selected_record_ids": list(getattr(evidence, "selected_record_ids", ())),
                    "selected_record_hashes": [
                        {"id": rid, "hash": rhash}
                        for rid, rhash in getattr(evidence, "selected_record_hashes", ())
                    ],
                    "selected_record_contents": [
                        {"id": rid, "content": content}
                        for rid, content in getattr(evidence, "selected_record_contents", ())
                    ],
                    "consult_receipt_selected_targets": list(
                        getattr(evidence, "consult_receipt_selected_targets", ())
                    ),
                    "consult_receipt_before_content_hashes": [
                        {"id": rid, "hash": rhash}
                        for rid, rhash in getattr(evidence, "consult_receipt_before_content_hashes", ())
                    ],
                    "current_run_receipts": list(
                        getattr(evidence, "current_run_receipts", ())
                    ),
                    "runner_model_attestation_digest": attestation_digest,
                    "runtime_attestation_digest": runtime_attestation.digest(),
                }
                raw_rows.append(row)
                by_cell_view[cell][case.view].setdefault(case_id, []).append(row)
        _enforce_total_budget(spec, total_metrics[cell])

    # Build per-cell EvaluationReport fixtures.
    reports: dict[tuple[str, str], EvaluationReport] = {}
    run_id = f"{spec.campaign_id}-{split}"
    for cell in CELLS:
        for view in VIEWS:
            case_attempts = by_cell_view[cell][view]
            report_id = f"{run_id}-{cell}-{view}"
            report = _build_report_for(
                spec=spec, split=split, cell=cell, view=view,
                case_attempts=case_attempts, package=package,
                report_id=report_id,
            )
            runtime_guard()
            report_store.create(report)
            reports[(cell, view)] = report
    return raw_rows, reports, actual_snapshots


def _memory_receipt_hashes_for(cell: str, attempts: list[dict[str, Any]]) -> tuple[str, ...]:
    if cell == "no_external_memory":
        return ()
    seen: list[str] = []
    for attempt in attempts:
        for receipt in attempt.get("receipts", []):
            if isinstance(receipt, dict):
                digest = receipt.get("content_hash")
                if (
                    isinstance(digest, str)
                    and digest.startswith("sha256:")
                    and digest not in seen
                ):
                    seen.append(digest)
    if not seen:
        raise CampaignContractError(
            f"memory cell {cell} produced no receipted evidence"
        )
    return tuple(seen)


def _build_protected_manifests(
    spec: CampaignSpec,
    *,
    reports: dict[tuple[str, str], EvaluationReport],
    split: str,
) -> dict[str, ProtectedRunContextManifest]:
    """Build the ProtectedRunContextManifest for each cell. The blueprint,
    workflow, task-model, runner, schedule, and budget axes are frozen across
    cells; only scaffold_h_snapshot_hash and retrieval_policy allowlist
    differ."""
    base_hash = next(
        c.h.memory_snapshot_hash for c in spec.cells if c.name == "base_scaffold"
    )
    opt_hash = next(
        c.h.memory_snapshot_hash for c in spec.cells if c.name == "optimized_scaffold"
    )
    schedule = tuple(
        RepetitionSeed(repetition=index, seed=seed)
        for index, seed in enumerate(spec.repetition_seeds, 1)
    )
    manifests: dict[str, ProtectedRunContextManifest] = {}
    for cell in CELLS:
        scaffold_hash = (
            None if cell == "no_external_memory"
            else (base_hash if cell == "base_scaffold" else opt_hash)
        )
        eval_refs = tuple(
            ProtectedEvaluationReportRef(
                id=reports[(cell, view)].id,
                content_digest=reports[(cell, view)].content_digest,
                split=split,
                view=view,
                case_ids=tuple(c.case_id for c in spec.cases if c.split == split and c.view == view),
                mandatory_case_ids=tuple(
                    c.case_id for c in spec.cases
                    if c.split == split and c.view == view and c.mandatory
                ),
            )
            for view in VIEWS
        )
        manifests[cell] = ProtectedRunContextManifest.model_validate({
            "cell": cell,
            "blueprint_ref": {"id": spec.blueprint_ref, "version": 1},
            "blueprint_digest": _strip_sha256_prefix(spec.blueprint_digest),
            "workflow_ref": {"id": spec.workflow_ref, "version": 1},
            "workflow_digest": _strip_sha256_prefix(spec.workflow_digest),
            "scaffold_h_snapshot_hash": scaffold_hash,
            "evaluator_ref": {"id": spec.evaluator.evaluator_ref, "version": 1},
            "evaluator_digest": _strip_sha256_prefix(spec.evaluator.evaluator_digest),
            "case_set_digest": sha256_hex(
                (split + ":" + spec.campaign_id).encode("utf-8")
            ),
            "runner_id": spec.runner_id,
            "runner_configuration_digest": _strip_sha256_prefix(spec.runner_configuration_digest),
            "task_model_portfolio_ref": {"id": spec.task_model_portfolio_ref, "version": 1},
            "task_model_portfolio_digest": _strip_sha256_prefix(spec.task_model_portfolio_digest),
            "w_snapshot_refs": [
                {
                    "ref": {"id": w.split(":")[-1] if ":" in w else w, "version": 1},
                    "content_digest": _strip_sha256_prefix(w),
                }
                for w in spec.w_refs
            ],
            "repetition_seed_schedule": schedule,
            "budget": _resolve_budget_envelope(spec),
            "evaluation_report_refs": [r.model_dump(mode="python") for r in eval_refs],
            "protected_evaluator_authority_id": spec.evaluator.authority_id,
        })
    return manifests


def _build_protected_rows(
    spec: CampaignSpec,
    *,
    rows: list[dict[str, Any]],
    split: str,
    reports: dict[tuple[str, str], EvaluationReport],
    report_store: EvaluationReportStore,
) -> tuple[list, dict[str, dict[str, str]]]:
    """Construct one ``ProtectedEvidenceRow`` per (view, case_id) using the
    existing ``build_protected_evidence_row`` contract."""
    by_case_view: dict[tuple[str, str], dict[str, list[dict[str, Any]]]] = {}
    for row in rows:
        by_case_view.setdefault(
            (row["view"], row["case_id"]), {}
        ).setdefault(row["cell"], []).append(row)
    manifests = _build_protected_manifests(spec, reports=reports, split=split)
    protected_rows = []
    report_ids: dict[str, dict[str, str]] = {cell: {} for cell in CELLS}
    for (view, case_id), per_cell in by_case_view.items():
        bindings = []
        for cell in CELLS:
            report = reports[(cell, view)]
            report_ref = next(
                r for r in manifests[cell].evaluation_report_refs
                if r.id == report.id
            )
            cell_attempts = per_cell.get(cell, [])
            receipt_hashes = _memory_receipt_hashes_for(cell, cell_attempts)
            bindings.append(CellEvidenceBinding(
                cell=cell,
                report_ref=report_ref,
                memory_receipt_hashes=receipt_hashes,
            ))
            report_ids[cell][view] = report.id
        if len(bindings) != 3:
            continue
        protected_rows.append(build_protected_evidence_row(
            case_id=case_id,
            view=view,
            bindings=tuple(bindings),
            report_store=report_store,
        ))
    return protected_rows, report_ids


def _ablation_campaign_for_split(
    spec: CampaignSpec,
    *,
    base_snapshot: MemoryCognitiveSkillSnapshot,
    optimized_snapshot: MemoryCognitiveSkillSnapshot,
    manifests: dict[str, ProtectedRunContextManifest],
) -> HAblationCampaign:
    """Construct one HAblationCampaign for a split using the actual
    MemoryCognitiveSkillSnapshot objects that the runners used. The
    snapshots are not synthesized from spec hashes."""
    # The optimized snapshot must declare base.content_hash as its parent.
    if optimized_snapshot.parent_snapshot_hash != base_snapshot.content_hash:
        raise CampaignContractError(
            "actual optimized_snapshot.parent_snapshot_hash must equal base_snapshot.content_hash"
        )
    return HAblationCampaign(
        campaign_id=spec.campaign_id,
        cells=tuple(
            HAblationCell(name=cell, manifest=manifests[cell])
            for cell in CELLS
        ),
        base_scaffold_snapshot=base_snapshot,
        optimized_scaffold_snapshot=optimized_snapshot,
        rollback_snapshot_hash=base_snapshot.content_hash,
        required_views=VIEWS,
        search_evidence=Evidence(
            search_set_id=spec.campaign_id,
            evaluation_count=len(spec.repetition_seeds),
            held_out_evaluation_count=1,
        ),
    )


def _rederive_protected_result(
    spec: CampaignSpec,
    *,
    ablation_campaign: HAblationCampaign,
    protected_rows: list,
    report_store: EvaluationReportStore,
) -> HAblationResult:
    """Re-derive the protected verdict using the existing pipeline. The
    derived verdict is the single source of truth for the campaign."""
    PromotionGate().decide(ablation_campaign.search_evidence)
    return evaluate_protected_h_ablation(
        result_id=f"{spec.campaign_id}-result",
        campaign=ablation_campaign,
        evidence_rows=tuple(protected_rows),
        report_store=report_store,
    )


# ---------------------------------------------------------------------------
# Selection / regression logic
# ---------------------------------------------------------------------------


def _selection_development_rows(
    spec: CampaignSpec, dev_rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Restrict the development rows to the preregistered selection set
    declared in ``spec.selection.evidence_case_ids``."""
    selected = set(spec.selection.evidence_case_ids)
    return [r for r in dev_rows if r["case_id"] in selected]


def _development_selection_eligible(
    spec: CampaignSpec, dev_rows: list[dict[str, Any]]
) -> tuple[bool, str]:
    """Return (eligible, reason). The selection rule requires that every
    repetition of every preregistered development case shows the
    base-fail -> optimized-pass transition, that no mandatory
    optimized-scaffold case has regressed, AND that the optimized
    policy is load-bearing: for every preregistered selection case /
    repetition, the optimized row must have advice_changed True, a
    nonempty selected_record_ids, and an advice_digest plus a full
    inner_task_digest different from the base row. The load-bearing
    guard RAISES a CampaignContractError when the optimized policy is
    not load-bearing so the campaign cannot reach an ineligible
    terminal state by silent attribution collapse.
    """
    selected = _selection_development_rows(spec, dev_rows)
    by_case: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for row in selected:
        by_case.setdefault(row["case_id"], {}).setdefault(row["cell"], []).append(row)
    if not by_case:
        return False, "selection.evidence_case_ids produced no development rows"
    for case_id, per_cell in by_case.items():
        base = per_cell.get("base_scaffold", [])
        opt = per_cell.get("optimized_scaffold", [])
        if not base or not opt:
            return False, f"case {case_id} missing base or optimized attempts"
        if len(base) != len(opt):
            return False, f"case {case_id} base/optimized repetition mismatch"
        if not all(r["verdict"] == "fail" for r in base):
            return False, f"case {case_id} base attempts are not all fail"
        if not all(r["verdict"] == "pass" for r in opt):
            return False, f"case {case_id} optimized attempts are not all pass"
        # Attribution is relevant only after these rows otherwise claim
        # the preregistered base-fail -> optimized-pass improvement. An
        # ordinary non-improvement is terminally ineligible and must not
        # touch validation/holdout or be recast as a contract failure.
        _enforce_advice_chain_load_bearing(case_id, base, opt)
    # Mandatory regression check on the entire development set.
    regressions = [
        r for r in dev_rows
        if r["mandatory"] and r["cell"] == "optimized_scaffold" and r["verdict"] != "pass"
    ]
    if regressions:
        return False, "mandatory development optimized-scaffold regression"
    return True, ""


def _enforce_advice_chain_load_bearing(
    case_id: str,
    base: list[dict[str, Any]],
    opt: list[dict[str, Any]],
) -> None:
    """The campaign guard: for every repetition, the optimized row's
    advice digest, full sent-task digest, and selected record ids must
    show the optimized policy is load-bearing. Byte-identical advice
    across the two memory cells means the optimized policy is not
    load-bearing and the campaign must raise (NOT return ineligible).
    """
    for rep, (b, o) in enumerate(zip(base, opt), 1):
        if not o.get("advice_changed", False):
            raise CampaignContractError(
                f"case {case_id} repetition {rep}: optimized policy is not "
                "load-bearing (advice_changed is False)"
            )
        if not o.get("selected_record_ids"):
            raise CampaignContractError(
                f"case {case_id} repetition {rep}: optimized policy is not "
                "load-bearing (selected_record_ids is empty)"
            )
        if o.get("advice_digest") == b.get("advice_digest"):
            raise CampaignContractError(
                f"case {case_id} repetition {rep}: optimized advice_digest "
                "is byte-identical to base (optimized policy is not "
                "load-bearing)"
            )
        if o.get("inner_task_digest") == b.get("inner_task_digest"):
            raise CampaignContractError(
                f"case {case_id} repetition {rep}: optimized inner_task_digest "
                "is byte-identical to base (optimized policy is not "
                "load-bearing)"
            )


def _verdict_score(verdict: str) -> int | None:
    if verdict == "unverified":
        return None
    return int(verdict == "pass")


def _validation_approved_pairs(val_rows: list[dict[str, Any]]) -> list[str]:
    by_case: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for row in val_rows:
        by_case.setdefault(row["case_id"], {}).setdefault(row["cell"], []).append(row)
    approved: list[str] = []
    for case_id, per_cell in by_case.items():
        if not any(r["approved_target"] for r in val_rows if r["case_id"] == case_id):
            continue
        base = per_cell.get("base_scaffold", [])
        opt = per_cell.get("optimized_scaffold", [])
        if not base or not opt:
            continue
        if len(base) != len(opt):
            continue
        if not all(r["verdict"] == "fail" for r in base):
            continue
        if not all(r["verdict"] == "pass" for r in opt):
            continue
        approved.append(case_id)
    return approved


def _validation_mandatory_regressions(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Disqualifying rows: any mandatory unverified; any mandatory
    optimized-scaffold failure; any optimized-vs-base regression where
    optimized is worse than base for the same (case_id, repetition)."""
    bad: list[dict[str, Any]] = []
    for row in rows:
        if row["verdict"] == "unverified":
            bad.append(row)
        if row["mandatory"] and row["cell"] == "optimized_scaffold" and row["verdict"] != "pass":
            bad.append(row)
    by_pair: dict[tuple[str, int], dict[str, dict[str, Any]]] = {}
    for row in rows:
        key = (row["case_id"], row["repetition"])
        by_pair.setdefault(key, {})[row["cell"]] = row
    for key, per_cell in by_pair.items():
        base = per_cell.get("base_scaffold")
        opt = per_cell.get("optimized_scaffold")
        if base is None or opt is None or not base["mandatory"]:
            continue
        bs = _verdict_score(base["verdict"])
        os_score = _verdict_score(opt["verdict"])
        if bs is None or os_score is None:
            continue
        if os_score < bs:
            bad.append(opt)
    seen: set = set()
    unique: list[dict[str, Any]] = []
    for row in bad:
        marker = (row.get("case_id"), row.get("cell"), row.get("repetition"), row.get("verdict"))
        if marker in seen:
            continue
        seen.add(marker)
        unique.append(row)
    return unique


# ---------------------------------------------------------------------------
# Evidence persistence
# ---------------------------------------------------------------------------


def _store_terminal(
    *,
    evidence_root: Path,
    spec: CampaignSpec,
    result: dict[str, Any],
    rows: list[dict[str, Any]],
    reports_by_split: dict[str, dict[tuple[str, str], EvaluationReport]],
    model_attestation: dict[str, str] | None,
    runtime_attestation: RuntimeAttestation,
    runtime_guard: Callable[[], None],
    holdout_ledger_key: str | None = None,
) -> dict[str, Any]:
    phase = result.get("phase")
    if phase == "holdout":
        if not holdout_ledger_key:
            raise CampaignContractError(
                "holdout terminal requires the one-time ledger key"
            )
        result = {**result, "holdout_ledger_key": holdout_ledger_key}
    elif holdout_ledger_key is not None:
        raise CampaignContractError(
            "pre-holdout terminal cannot bind a holdout ledger key"
        )
    evidence_payload = {"rows": rows}
    if holdout_ledger_key is not None:
        evidence_payload["holdout_ledger_key"] = holdout_ledger_key
    evidence_digest = sha256_hex(canonical_json_bytes(evidence_payload))
    result_digest = sha256_hex(canonical_json_bytes(result))
    manifest = {
        "campaign_id": spec.campaign_id,
        "spec_digest": spec.digest(),
        "evidence_digest": evidence_digest,
        "result_digest": result_digest,
        "protected_package_digests": dict(spec.protected_package_digests),
        "evaluator_digest": spec.evaluator_digest,
        "cells": list(CELLS),
        "views": list(VIEWS),
        "report_refs": _report_refs(reports_by_split),
        "model_attestation": model_attestation,
        "runtime_attestation": runtime_attestation.model_dump(mode="json"),
    }
    if holdout_ledger_key is not None:
        manifest["holdout_ledger_key"] = holdout_ledger_key
    runtime_guard()
    _create_only_json(evidence_root / "evidence.json", evidence_payload)
    runtime_guard()
    _create_only_json(evidence_root / "result.json", result)
    runtime_guard()
    _create_only_json(evidence_root / "manifest.json", manifest)
    return result


def _persist_ablation_result(
    *,
    spec: CampaignSpec,
    evidence_root: Path,
    protected_result: HAblationResult,
    result_store: HAblationResultStore,
    rows: list[dict[str, Any]],
    reports_by_split: dict[str, dict[tuple[str, str], EvaluationReport]],
    model_attestation: dict[str, str] | None,
    runtime_attestation: RuntimeAttestation,
    runtime_guard: Callable[[], None],
    holdout_ledger_key: str,
) -> dict[str, Any]:
    """Persist the derived protected result exactly once, then write the
    public terminal artifact (manifest + evidence + result) bound by the
    stored result id and digest. The protected result is the canonical
    derived verdict; the manifest binds every artifact by digest."""
    runtime_guard()
    result_store.create(protected_result)
    result_id = protected_result.id
    terminal = {
        "campaign_id": spec.campaign_id,
        "spec_digest": spec.digest(),
        "status": protected_result.status,
        "closest_protected_result": protected_result.closest_protected_result.cell,
        "unresolved_gap": protected_result.unresolved_gap,
        "improved_approved_target_case_ids": list(
            protected_result.improved_approved_target_case_ids
        ),
        "regressed_mandatory_case_ids": list(
            protected_result.regressed_mandatory_case_ids
        ),
        "mandatory_unverified_case_ids": list(
            protected_result.mandatory_unverified_case_ids
        ),
        "rollback_snapshot_hash": protected_result.rollback_snapshot_hash,
        "protected_result_id": result_id,
        "protected_result_digest": protected_result.content_digest,
        "phase": "holdout",
        "holdout_ledger_key": holdout_ledger_key,
    }
    evidence_payload = {
        "rows": rows,
        "holdout_ledger_key": holdout_ledger_key,
        "protected_result_id": result_id,
    }
    evidence_digest = sha256_hex(canonical_json_bytes(evidence_payload))
    result_digest = sha256_hex(canonical_json_bytes(terminal))
    manifest = {
        "campaign_id": spec.campaign_id,
        "spec_digest": spec.digest(),
        "evidence_digest": evidence_digest,
        "result_digest": result_digest,
        "protected_package_digests": dict(spec.protected_package_digests),
        "evaluator_digest": spec.evaluator_digest,
        "cells": list(CELLS),
        "views": list(VIEWS),
        "protected_result_id": result_id,
        "protected_result_digest": protected_result.content_digest,
        "report_refs": _report_refs(reports_by_split),
        "model_attestation": model_attestation,
        "runtime_attestation": runtime_attestation.model_dump(mode="json"),
        "holdout_ledger_key": holdout_ledger_key,
    }
    runtime_guard()
    _create_only_json(evidence_root / "evidence.json", evidence_payload)
    runtime_guard()
    _create_only_json(evidence_root / "result.json", terminal)
    runtime_guard()
    _create_only_json(evidence_root / "manifest.json", manifest)
    return terminal


def _report_refs(
    reports_by_split: dict[str, dict[tuple[str, str], EvaluationReport]],
) -> list[dict[str, str]]:
    """Bind every stored report to its campaign split, cell, view and digest."""
    return [
        {
            "id": report.id,
            "content_digest": report.content_digest,
            "split": split,
            "cell": cell,
            "view": view,
        }
        for split, reports in sorted(reports_by_split.items())
        for (cell, view), report in sorted(reports.items())
    ]


# ---------------------------------------------------------------------------
# Run path
# ---------------------------------------------------------------------------


def _run_ordered_campaign(
    spec: CampaignSpec,
    *,
    development_input_path: str | Path,
    validation_input_path: str | Path,
    holdout_input_path: str | Path,
    evidence_root: str | Path,
    ledger_root: str | Path,
    runner_factory: Callable[..., Any],
    runtime_attestation: RuntimeAttestation,
    runtime_guard: Callable[[], None],
    model_attestation: dict[str, str] | None = None,
) -> dict[str, Any]:
    """The execution order: development only -> exact selection rule;
    validation only -> mandatory/receipt/budget/freeze checks; only then
    atomically consume holdout ledger BEFORE opening holdout file ->
    holdout confirm/reject. The final eligible verdict is the protected
    verdict rederived via the existing ablation pipeline. Early failure
    cannot call/open/consume holdout."""
    out_root = Path(evidence_root).resolve()
    report_root = out_root / "reports"
    report_store = EvaluationReportStore(report_root)
    campaign_metrics = {cell: EvalMetrics() for cell in CELLS}

    # 1. Development only.
    try:
        dev_package = ProtectedInputPackage.model_validate_json(
            Path(development_input_path).read_text(encoding="utf-8")
        )
    except FileNotFoundError as exc:
        raise CampaignContractError("development input file is missing") from exc
    _validate_package(dev_package, spec=spec, split="development")
    dev_rows, dev_reports, _dev_snapshots = _phase_execution(
        spec=spec, package=dev_package, split="development",
        runner_factory=runner_factory, report_store=report_store,
        runtime_attestation=runtime_attestation,
        runtime_guard=runtime_guard,
        campaign_metrics=campaign_metrics,
    )
    eligible, dev_reason = _development_selection_eligible(spec, dev_rows)
    if not eligible:
        return _store_terminal(
            evidence_root=out_root, spec=spec,
            result={
                "campaign_id": spec.campaign_id, "spec_digest": spec.digest(),
                "status": "ineligible",
                "closest_protected_result": "base_scaffold",
                "unresolved_gap": f"development selection failed: {dev_reason}",
                "phase": "development",
            },
            rows=dev_rows,
            reports_by_split={"development": dev_reports},
            model_attestation=model_attestation,
            runtime_attestation=runtime_attestation,
            runtime_guard=runtime_guard,
        )

    # 2. Validation only.
    try:
        val_package = ProtectedInputPackage.model_validate_json(
            Path(validation_input_path).read_text(encoding="utf-8")
        )
    except FileNotFoundError as exc:
        raise CampaignContractError("validation input file is missing") from exc
    _validate_package(val_package, spec=spec, split="validation")
    val_rows, val_reports, val_snapshots = _phase_execution(
        spec=spec, package=val_package, split="validation",
        runner_factory=runner_factory, report_store=report_store,
        runtime_attestation=runtime_attestation,
        runtime_guard=runtime_guard,
        campaign_metrics=campaign_metrics,
    )
    val_mandatory_bad = _validation_mandatory_regressions(val_rows)
    val_approved_pairs = _validation_approved_pairs(val_rows)
    if val_mandatory_bad or not val_approved_pairs:
        return _store_terminal(
            evidence_root=out_root, spec=spec,
            result={
                "campaign_id": spec.campaign_id, "spec_digest": spec.digest(),
                "status": "ineligible",
                "closest_protected_result": "base_scaffold",
                "unresolved_gap": "validation gate failed",
                "phase": "validation",
            },
            rows=dev_rows + val_rows,
            reports_by_split={"development": dev_reports, "validation": val_reports},
            model_attestation=model_attestation,
            runtime_attestation=runtime_attestation,
            runtime_guard=runtime_guard,
        )

    # 3. Holdout: ledger claim BEFORE opening the holdout file.
    ledger = HoldoutConsumptionLedger(ledger_root)
    try:
        holdout_key, holdout_package = load_protected_inputs(
            holdout_input_path, spec=spec, split="holdout", ledger=ledger,
        )
    except HoldoutAlreadyConsumedError:
        raise
    except FileNotFoundError as exc:
        raise CampaignContractError("holdout input file is missing") from exc
    holdout_rows, holdout_reports, holdout_snapshots = _phase_execution(
        spec=spec, package=holdout_package, split="holdout",
        runner_factory=runner_factory, report_store=report_store,
        runtime_attestation=runtime_attestation,
        runtime_guard=runtime_guard,
        campaign_metrics=campaign_metrics,
    )
    holdout_bad = _validation_mandatory_regressions(holdout_rows)
    if holdout_bad:
        return _store_terminal(
            evidence_root=out_root, spec=spec,
            result={
                "campaign_id": spec.campaign_id, "spec_digest": spec.digest(),
                "status": "ineligible",
                "closest_protected_result": "base_scaffold",
                "unresolved_gap": "holdout confirmation failed",
                "phase": "holdout",
            },
            rows=dev_rows + val_rows + holdout_rows,
            reports_by_split={
                "development": dev_reports,
                "validation": val_reports,
                "holdout": holdout_reports,
            },
            model_attestation=model_attestation,
            runtime_attestation=runtime_attestation,
            runtime_guard=runtime_guard,
            holdout_ledger_key=holdout_key,
        )

    # 4. Re-derive the protected verdict via the existing pipeline using the
    # actual MemoryCognitiveSkillSnapshot objects the runners used.
    all_rows = dev_rows + val_rows + holdout_rows
    val_protected_rows, _ = _build_protected_rows(
        spec, rows=val_rows, split="validation",
        reports=val_reports, report_store=report_store,
    )
    val_manifests = _build_protected_manifests(
        spec, reports=val_reports, split="validation",
    )
    base_actual = val_snapshots.get("base_scaffold")
    optimized_actual = val_snapshots.get("optimized_scaffold")
    if base_actual is None or optimized_actual is None:
        raise CampaignContractError(
            "validation phase must use the actual base/optimized snapshots"
        )
    val_ablation = _ablation_campaign_for_split(
        spec, base_snapshot=base_actual, optimized_snapshot=optimized_actual,
        manifests=val_manifests,
    )
    val_protected_result = _rederive_protected_result(
        spec, ablation_campaign=val_ablation,
        protected_rows=val_protected_rows, report_store=report_store,
    )
    result_root = out_root / "results"
    result_store = HAblationResultStore(result_root, report_store=report_store)
    return _persist_ablation_result(
        spec=spec, evidence_root=out_root,
        protected_result=val_protected_result,
        result_store=result_store, rows=all_rows,
        reports_by_split={
            "development": dev_reports,
            "validation": val_reports,
            "holdout": holdout_reports,
        },
        model_attestation=model_attestation,
        runtime_attestation=runtime_attestation,
        runtime_guard=runtime_guard,
        holdout_ledger_key=holdout_key,
    )


# ---------------------------------------------------------------------------
# Public run/verify surface
# ---------------------------------------------------------------------------


def load_spec(value: CampaignSpec | str | Path) -> CampaignSpec:
    if isinstance(value, CampaignSpec):
        return value
    raw = json.loads(Path(value).read_text(encoding="utf-8"))
    if (
        not isinstance(raw, dict)
        or not isinstance(raw.get("spec_digest"), str)
        or not raw["spec_digest"].strip()
    ):
        raise CampaignContractError(
            "file-backed campaign spec requires a precommitted spec_digest"
        )
    return CampaignSpec.model_validate(raw)


def _run_hermetic_campaign(
    spec: CampaignSpec | str | Path,
    *,
    development_input_path: str | Path | None = None,
    validation_input_path: str | Path | None = None,
    holdout_input_path: str | Path | None = None,
    evidence_root: str | Path,
    ledger_root: str | Path,
    runner_factory: Callable[..., Any],
    model_digest_resolver: Callable[[ModelContract], str],
) -> dict[str, Any]:
    """Execution entry point. Physically separate development, validation,
    and holdout inputs are required."""
    if (
        development_input_path is not None
        and validation_input_path is not None
        and holdout_input_path is not None
    ):
        exact = load_spec(spec)
        actual_digest = model_digest_resolver(exact.model)
        if actual_digest != exact.model.model_digest:
            raise CampaignContractError("model digest preflight mismatch")
        model_attestation = {
            "model_digest": actual_digest,
            "provenance_digest": sha256_hex(canonical_json_bytes({
                "model_id": exact.model.model_id, "model_digest": actual_digest,
            })),
        }
        runtime_attestation = _test_runtime_attestation(exact)

        def runtime_guard() -> None:
            _enforce_runtime_attestation(
                runtime_attestation, spec=exact, stores=None,
            )

        runtime_guard()
        return _run_ordered_campaign(
            exact,
            development_input_path=development_input_path,
            validation_input_path=validation_input_path,
            holdout_input_path=holdout_input_path,
            evidence_root=evidence_root,
            ledger_root=ledger_root,
            runner_factory=runner_factory,
            runtime_attestation=runtime_attestation,
            runtime_guard=runtime_guard,
            model_attestation=model_attestation,
        )
    raise CampaignContractError(
        "run_campaign requires physically separate development, validation, and holdout inputs"
    )


def _implementation_digest(value: Any) -> str:
    try:
        source = inspect.getsource(value)
    except (OSError, TypeError) as exc:
        raise CampaignContractError(
            f"cannot attest installed implementation {value!r}"
        ) from exc
    return "sha256:" + sha256_hex(source.encode("utf-8"))


def _code_projection(code: types.CodeType) -> dict[str, Any]:
    constants: list[Any] = []
    for value in code.co_consts:
        if isinstance(value, types.CodeType):
            constants.append({"code": _code_projection(value)})
        elif value is None or isinstance(value, (str, int, float, bool)):
            constants.append(value)
        else:
            constants.append({
                "type": f"{type(value).__module__}.{type(value).__qualname__}",
                "repr": repr(value),
            })
    return {
        "argcount": code.co_argcount,
        "posonlyargcount": code.co_posonlyargcount,
        "kwonlyargcount": code.co_kwonlyargcount,
        "flags": code.co_flags,
        "code": code.co_code.hex(),
        "consts": constants,
        "names": code.co_names,
        "varnames": code.co_varnames,
        "freevars": code.co_freevars,
        "cellvars": code.co_cellvars,
    }


def _callable_projection(value: Any) -> dict[str, Any]:
    if isinstance(value, (staticmethod, classmethod)):
        value = value.__func__
    if isinstance(value, property):
        value = value.fget
    code = getattr(value, "__code__", None)
    if not isinstance(code, types.CodeType):
        raise CampaignContractError(
            f"installed callable {value!r} has no attestable Python code"
        )
    return {
        "module": getattr(value, "__module__", ""),
        "qualname": getattr(value, "__qualname__", ""),
        "code": _code_projection(code),
        "defaults": repr(getattr(value, "__defaults__", None)),
        "kwdefaults": repr(getattr(value, "__kwdefaults__", None)),
    }


def _class_projection(value: type) -> dict[str, Any]:
    members = {}
    for name, member in sorted(value.__dict__.items()):
        target = (
            member.__func__
            if isinstance(member, (staticmethod, classmethod))
            else member.fget
            if isinstance(member, property)
            else member
        )
        if isinstance(target, types.FunctionType):
            members[name] = _callable_projection(member)
    return {
        "module": value.__module__,
        "qualname": value.__qualname__,
        "members": members,
    }


def _installed_worker_parser_fields() -> dict[str, str]:
    """Recompute live worker/parser implementation digests on every call."""
    from metaharness.harness import local as local_module
    from metaharness.harness.runner import BaseRunner

    worker_projection = {
        "contract": "openai-compat-worker-execution:v1",
        "worker": _class_projection(local_module.OpenAICompatWorker),
        "base_runner_run": _callable_projection(BaseRunner.run),
    }
    parser_projection = {
        "contract": "canonical-output-parser:v1",
        "parse_output": _callable_projection(local_module.parse_output),
        "strip_think": _callable_projection(local_module.strip_think),
    }
    return {
        "openai_worker_implementation_digest": "sha256:" + sha256_hex(
            canonical_json_bytes(worker_projection)
        ),
        "output_parser_implementation_digest": "sha256:" + sha256_hex(
            canonical_json_bytes(parser_projection)
        ),
    }


def _canonical_parse_output(text: str, *, expect_json: bool) -> Any:
    from metaharness.harness import local as local_module

    return local_module.parse_output(text, expect_json)


def _resolve_indexed_record(
    stores: Mapping[str, MemoryStore],
    record_index: Mapping[str, tuple[str, str]],
    record_id: str,
) -> MemoryRecord | None:
    """Resolve through the live named store and re-hash the full record."""
    indexed = record_index.get(record_id)
    if indexed is None:
        return None
    store_name, expected_digest = indexed
    store = stores.get(store_name)
    if store is None:
        raise CampaignContractError("runtime resolver store disappeared")
    record = store.get(record_id)
    if record is None:
        raise CampaignContractError("runtime resolver record disappeared")
    actual_digest = "sha256:" + sha256_hex(
        canonical_json_bytes(record.model_dump(mode="json"))
    )
    if actual_digest != expected_digest:
        raise CampaignContractError("runtime resolver record drift")
    return record


def _project_runtime_stores(
    stores: Mapping[str, MemoryStore],
) -> tuple[
    tuple[RuntimeStoreProjection, ...],
    dict[str, tuple[str, str]],
    str,
]:
    if not isinstance(stores, Mapping) or not stores:
        raise CampaignContractError(
            "production runtime requires named memory stores"
        )
    projections: list[RuntimeStoreProjection] = []
    record_index: dict[str, tuple[str, str]] = {}
    corpus_projection: list[dict[str, Any]] = []
    for raw_name, store in sorted(stores.items(), key=lambda item: str(item[0])):
        name = str(raw_name)
        if not name or not isinstance(store, MemoryStore):
            raise CampaignContractError(
                "runtime memory stores must be non-empty names bound to MemoryStore"
            )
        listed = sorted(
            store.list(include_tombstoned=True),
            key=lambda item: item.id,
        )
        records: list[RuntimeRecordProjection] = []
        for listed_record in listed:
            # The list projection is not trusted by itself: re-get every
            # indexed record and attest the complete MemoryRecord.
            record = store.get(listed_record.id)
            if record is None or record != listed_record:
                raise CampaignContractError(
                    "runtime store list/get projection is inconsistent"
                )
            if record.id in record_index:
                raise CampaignContractError(
                    "runtime stores contain duplicate record IDs"
                )
            record_digest = "sha256:" + sha256_hex(
                canonical_json_bytes(record.model_dump(mode="json"))
            )
            record_index[record.id] = (name, record_digest)
            records.append(RuntimeRecordProjection(
                record_id=record.id,
                record_digest=record_digest,
                creation_seq=record.creation_seq,
            ))
            corpus_projection.append({
                "store": name,
                "record": record.model_dump(mode="json"),
            })
        records_tuple = tuple(records)
        projections.append(RuntimeStoreProjection(
            name=name,
            record_count=len(records_tuple),
            high_water_mark=max(
                (item.creation_seq for item in records_tuple), default=-1
            ),
            records_digest="sha256:" + sha256_hex(canonical_json_bytes(
                [item.model_dump(mode="json") for item in records_tuple]
            )),
            records=records_tuple,
        ))
    corpus_digest = "sha256:" + sha256_hex(
        canonical_json_bytes(corpus_projection)
    )
    return tuple(projections), record_index, corpus_digest


_RUNTIME_IMPLEMENTATION_CACHE: dict[str, str] | None = None
_TASK_TEMPLATE_CONTRACT_VERSION = "task-template-case-digest:v1"


def _runtime_implementation_fields() -> dict[str, str]:
    global _RUNTIME_IMPLEMENTATION_CACHE
    if _RUNTIME_IMPLEMENTATION_CACHE is None:
        _RUNTIME_IMPLEMENTATION_CACHE = {
            "resolver_implementation_digest": _implementation_digest(
                _resolve_indexed_record
            ),
            "task_template_implementation_digest": "sha256:" + sha256_hex(
                canonical_json_bytes({
                    "version": _TASK_TEMPLATE_CONTRACT_VERSION,
                    "case_input_digest_implementation": _implementation_digest(
                        case_input_digest
                    ),
                    "task_schema": tuple(Task.model_fields),
                })
            ),
            "memory_aware_runner_implementation_digest": _implementation_digest(
                MemoryAwareRunner
            ),
            "broker_policy_implementation_digest": _implementation_digest(
                MemoryActionBroker
            ),
            "deterministic_evaluator_implementation_digest": _implementation_digest(
                deterministic_verify
            ),
        }
    return {
        **_RUNTIME_IMPLEMENTATION_CACHE,
        **_installed_worker_parser_fields(),
    }


def _test_runtime_attestation(spec: CampaignSpec) -> RuntimeAttestation:
    """Private hermetic seam. Public run never accepts or constructs this mode."""
    corpus_digests = {cell.h.corpus_digest for cell in spec.cells}
    if len(corpus_digests) != 1:
        raise CampaignContractError("test runtime corpus axis is not frozen")
    return RuntimeAttestation(
        mode="test-only",
        corpus_digest=next(iter(corpus_digests)),
        resolver_contract="named-store-full-record-resolver:v1",
        environment_digest=spec.environment_digest,
        w_refs=spec.w_refs,
        stores=(),
        **_runtime_implementation_fields(),
    )


def _production_runtime_attestation(
    spec: CampaignSpec,
    stores: Mapping[str, MemoryStore],
) -> tuple[RuntimeAttestation, dict[str, tuple[str, str]]]:
    projections, record_index, corpus_digest = _project_runtime_stores(stores)
    attestation = RuntimeAttestation(
        mode="production",
        corpus_digest=corpus_digest,
        resolver_contract="named-store-full-record-resolver:v1",
        environment_digest=spec.environment_digest,
        w_refs=spec.w_refs,
        stores=projections,
        **_runtime_implementation_fields(),
    )
    return attestation, record_index


def _verify_runtime_attestation_static(
    attestation: RuntimeAttestation,
    *,
    spec: CampaignSpec,
) -> None:
    expected_implementations = _runtime_implementation_fields()
    for field, expected in expected_implementations.items():
        if getattr(attestation, field) != expected:
            raise CampaignContractError(
                f"runtime attestation installed {field} mismatch"
            )
    if (
        attestation.environment_digest != spec.environment_digest
        or attestation.w_refs != spec.w_refs
    ):
        raise CampaignContractError(
            "runtime attestation environment/W projection mismatch"
        )
    corpus_digests = {cell.h.corpus_digest for cell in spec.cells}
    if corpus_digests != {attestation.corpus_digest}:
        raise CampaignContractError(
            "runtime attestation corpus differs from frozen H surface"
        )
    for cell in spec.cells:
        if (
            cell.h.wrapper_digest
            != attestation.memory_aware_runner_implementation_digest
            or cell.h.resolver_digest
            != attestation.resolver_implementation_digest
            or cell.h.policy_digest
            != attestation.broker_policy_implementation_digest
            or cell.h.task_template_digest
            != attestation.task_template_implementation_digest
            or cell.h.worker_implementation_digest
            != attestation.openai_worker_implementation_digest
            or cell.h.output_parser_digest
            != attestation.output_parser_implementation_digest
        ):
            raise CampaignContractError(
                "runtime implementation attestation differs from declared H axes"
            )
    if (
        spec.evaluator_digest
        != attestation.deterministic_evaluator_implementation_digest
    ):
        raise CampaignContractError(
            "runtime evaluator implementation differs from frozen evaluator"
        )


def _enforce_runtime_attestation(
    attestation: RuntimeAttestation,
    *,
    spec: CampaignSpec,
    stores: Mapping[str, MemoryStore] | None,
) -> None:
    _verify_runtime_attestation_static(attestation, spec=spec)
    if attestation.mode == "test-only":
        if stores is not None or attestation.stores:
            raise CampaignContractError(
                "test-only runtime attestation cannot bind production stores"
            )
        return
    if stores is None:
        raise CampaignContractError(
            "production runtime attestation requires live stores"
        )
    projections, _index, corpus_digest = _project_runtime_stores(stores)
    if (
        corpus_digest != attestation.corpus_digest
        or projections != attestation.stores
    ):
        raise CampaignContractError("runtime corpus/store projection drift")


def run_campaign(
    spec: CampaignSpec | str | Path,
    *,
    development_input_path: str | Path,
    validation_input_path: str | Path,
    holdout_input_path: str | Path,
    evidence_root: str | Path,
    ledger_root: str | Path,
    memory_stores: Mapping[str, MemoryStore],
    model_digest_resolver: Callable[[ModelContract], str],
) -> dict[str, Any]:
    """Production-only entry point; it owns the worker/broker topology."""
    exact = load_spec(spec)
    actual_model_digest = model_digest_resolver(exact.model)
    if actual_model_digest != exact.model.model_digest:
        raise CampaignContractError("model digest preflight mismatch")
    runtime_attestation, record_index = _production_runtime_attestation(
        exact, memory_stores,
    )

    def runtime_guard() -> None:
        _enforce_runtime_attestation(
            runtime_attestation, spec=exact, stores=memory_stores,
        )

    runtime_guard()

    def factory(cell: str, repetition: int, seed: int, config: dict[str, Any]) -> MemoryAwareRunner:
        from metaharness.harness.local import OpenAICompatWorker

        inner = OpenAICompatWorker(
            worker_id=exact.runner_id, base_url=exact.model.base_url,
            model=exact.model.model_id, temperature=exact.model.inference_parameters.get("temperature"),
            max_tokens=exact.model.inference_parameters.get("max_tokens"),
            thinking=exact.model.inference_parameters.get("thinking"),
            extra_body={**(exact.model.inference_parameters.get("extra_body") or {}), "seed": seed},
        )
        if cell == "no_external_memory":
            return MemoryAwareRunner(inner=inner, memory_enabled=False)
        snapshot = next(
            (c.base_snapshot if cell == "base_scaffold" else c.optimized_snapshot)
            for c in exact.cells if c.name == cell
        )
        assert snapshot is not None
        broker = MemoryActionBroker(snapshot=snapshot, stores=memory_stores)
        return MemoryAwareRunner(
            inner=inner, snapshot=snapshot, broker=broker,
            record_resolver=lambda record_id: _resolve_indexed_record(
                memory_stores, record_index, record_id,
            ),
        )

    model_attestation = {
        "model_digest": actual_model_digest,
        "provenance_digest": sha256_hex(canonical_json_bytes({
            "model_id": exact.model.model_id,
            "model_digest": actual_model_digest,
        })),
    }
    return _run_ordered_campaign(
        exact,
        development_input_path=development_input_path,
        validation_input_path=validation_input_path, holdout_input_path=holdout_input_path,
        evidence_root=evidence_root, ledger_root=ledger_root, runner_factory=factory,
        runtime_attestation=runtime_attestation,
        runtime_guard=runtime_guard,
        model_attestation=model_attestation,
    )


def _verify_campaign(
    spec: CampaignSpec | str | Path,
    *,
    evidence_root: str | Path,
    expected_runtime_mode: Literal["production", "test-only"],
) -> dict[str, Any]:
    """Reload and verify protected evidence without invoking a model. The
    stored reports and stored ablation result are re-resolved through
    the existing pipeline; the derived verdict is compared to the
    persisted result. Any tampering fails."""
    exact = load_spec(spec)
    root = Path(evidence_root).resolve()
    manifest_path = root / "manifest.json"
    result_path = root / "result.json"
    evidence_path = root / "evidence.json"
    if not (manifest_path.is_file() and result_path.is_file() and evidence_path.is_file()):
        raise CampaignContractError("protected evidence manifest/result/evidence missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    result = json.loads(result_path.read_text(encoding="utf-8"))
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    if not isinstance(result, dict) or result.get("phase") not in SPLITS:
        raise CampaignContractError("terminal result phase is invalid")
    phase = result["phase"]
    base_manifest_fields = {
        "campaign_id", "spec_digest", "evidence_digest", "result_digest",
        "protected_package_digests", "evaluator_digest", "cells", "views",
        "report_refs", "model_attestation", "runtime_attestation",
    }
    expected_manifest_fields = set(base_manifest_fields)
    if phase == "holdout":
        expected_manifest_fields.add("holdout_ledger_key")
        if result.get("status") == "eligible_pending_human_promotion":
            expected_manifest_fields.update(
                {"protected_result_id", "protected_result_digest"}
            )
    if not isinstance(manifest, dict) or set(manifest) != expected_manifest_fields:
        raise CampaignContractError("manifest schema is not phase-exact")
    if manifest.get("evaluator_digest") != exact.evaluator_digest or manifest.get("cells") != list(CELLS) or manifest.get("views") != list(VIEWS):
        raise CampaignContractError("manifest frozen axes mismatch")
    if manifest.get("spec_digest") != exact.digest():
        raise CampaignContractError("manifest is not bound to the exact spec")
    if result.get("spec_digest") != exact.digest():
        raise CampaignContractError("result is not bound to the exact spec")
    if manifest.get("evidence_digest") != sha256_hex(canonical_json_bytes(evidence)):
        raise CampaignContractError("evidence digest mismatch")
    if manifest.get("result_digest") != sha256_hex(canonical_json_bytes(result)):
        raise CampaignContractError("result digest mismatch")
    if result.get("status") not in {"eligible_pending_human_promotion", "ineligible"}:
        raise CampaignContractError("invalid terminal campaign status")
    if result.get("campaign_id") != exact.campaign_id:
        raise CampaignContractError("result campaign_id mismatch")
    if manifest.get("campaign_id") != exact.campaign_id:
        raise CampaignContractError("manifest campaign_id mismatch")
    pp = manifest.get("protected_package_digests")
    if not isinstance(pp, dict) or set(pp.keys()) != set(SPLITS):
        raise CampaignContractError("manifest protected_package_digests missing or invalid")
    for split in SPLITS:
        if pp[split] != exact.package_digest_for(split):
            raise CampaignContractError(f"manifest protected_package_digest[{split}] mismatch")
    model_attestation = manifest.get("model_attestation")
    if (
        not isinstance(model_attestation, dict)
        or set(model_attestation) != {"model_digest", "provenance_digest"}
        or model_attestation.get("model_digest") != exact.model.model_digest
    ):
        raise CampaignContractError("manifest model digest attestation mismatch")
    expected_provenance = sha256_hex(canonical_json_bytes({
        "model_id": exact.model.model_id, "model_digest": exact.model.model_digest,
    }))
    if model_attestation.get("provenance_digest") != expected_provenance:
        raise CampaignContractError("manifest model attestation provenance mismatch")
    try:
        runtime_attestation = RuntimeAttestation.model_validate(
            manifest.get("runtime_attestation")
        )
    except Exception as exc:
        raise CampaignContractError(
            "manifest runtime attestation schema is invalid"
        ) from exc
    _verify_runtime_attestation_static(runtime_attestation, spec=exact)
    if runtime_attestation.mode != expected_runtime_mode:
        raise CampaignContractError(
            f"runtime attestation mode must be {expected_runtime_mode}"
        )
    expected_holdout_key = HoldoutConsumptionLedger._key(
        campaign_id=exact.campaign_id,
        spec_digest=exact.digest(),
        holdout_package_digest=exact.package_digest_for("holdout"),
        evaluator_digest=exact.evaluator_digest,
    )
    if phase == "holdout":
        if (
            manifest.get("holdout_ledger_key") != expected_holdout_key
            or result.get("holdout_ledger_key") != expected_holdout_key
            or evidence.get("holdout_ledger_key") != expected_holdout_key
        ):
            raise CampaignContractError("holdout ledger key binding mismatch")
        if result.get("status") == "eligible_pending_human_promotion" and (
            manifest.get("protected_result_id")
            != result.get("protected_result_id")
            or manifest.get("protected_result_digest")
            != result.get("protected_result_digest")
            or evidence.get("protected_result_id")
            != result.get("protected_result_id")
        ):
            raise CampaignContractError(
                "protected result refs are not exact across terminal artifacts"
            )
    elif any(
        "holdout_ledger_key" in artifact
        for artifact in (manifest, result, evidence)
    ):
        raise CampaignContractError(
            "pre-holdout artifact exposes a holdout ledger key"
        )
    phase_index = SPLITS.index(phase)
    expected_splits = SPLITS[: phase_index + 1]
    report_refs = manifest.get("report_refs")
    if not isinstance(report_refs, list):
        raise CampaignContractError("manifest does not bind stored reports")
    expected_report_keys = {
        (split, cell, view)
        for split in expected_splits for cell in CELLS for view in VIEWS
    }
    seen_report_keys: set[tuple[str, str, str]] = set()
    # Re-derive the protected verdict from the stored reports and stored
    # ablation result. The re-derivation must agree with the persisted
    # terminal result for the verification to succeed.
    report_root = root / "reports"
    report_store = EvaluationReportStore(report_root)
    for ref in report_refs:
        if not isinstance(ref, dict):
            raise CampaignContractError("manifest report reference is malformed")
        if set(ref) != {"id", "content_digest", "split", "cell", "view"}:
            raise CampaignContractError("manifest report reference schema is invalid")
        key = (ref.get("split"), ref.get("cell"), ref.get("view"))
        if key not in expected_report_keys or key in seen_report_keys:
            raise CampaignContractError("manifest report membership is invalid")
        seen_report_keys.add(key)
        expected_id = _sanitize_id(
            f"{exact.campaign_id}-{key[0]}-{key[1]}-{key[2]}"
        )
        if ref.get("id") != expected_id:
            raise CampaignContractError("manifest report ID is not bound to cell/view")
        report = report_store.get(ref.get("id", ""))
        if report.split != key[0] or report.content_digest != ref.get("content_digest"):
            raise CampaignContractError("stored report does not match manifest binding")
        if (
            report.blueprint_ref.id != exact.blueprint_ref
            or report.eval_ref.id != exact.evaluator.evaluator_ref
            or report.blueprint_digest != _strip_sha256_prefix(exact.blueprint_digest)
            or report.workflow_digest != _strip_sha256_prefix(exact.workflow_digest)
            or report.eval_digest != _strip_sha256_prefix(exact.evaluator.evaluator_digest)
            or report.runner_id != exact.runner_id
        ):
            raise CampaignContractError("stored report provenance does not match frozen spec")
        if key[0] == "holdout":
            for case in report.cases:
                if case.assertion is not None or case.assertion_digest is not None or any(
                    attempt.output is not None or attempt.detail for attempt in case.attempts
                ):
                    raise CampaignContractError("holdout report leaks protected assertion or output")
    if seen_report_keys != expected_report_keys:
        raise CampaignContractError("manifest does not bind every report")
    stored_ids = {report.id for report in report_store.list()}
    bound_ids = {ref["id"] for ref in report_refs}
    if stored_ids != bound_ids:
        raise CampaignContractError("stored reports include unbound or missing artifacts")
    _verify_evidence_rows(
        evidence, exact=exact, expected_splits=expected_splits,
        report_refs=report_refs, report_store=report_store,
        runtime_attestation=runtime_attestation,
        expected_holdout_key=(
            expected_holdout_key if phase == "holdout" else None
        ),
        protected_result_id=manifest.get("protected_result_id"),
    )
    rows = evidence["rows"]
    dev_rows = [row for row in rows if row["split"] == "development"]
    dev_eligible, dev_reason = _development_selection_eligible(exact, dev_rows)
    if phase == "development":
        _verify_early_terminal(
            result, exact=exact, phase=phase,
            reason=f"development selection failed: {dev_reason}",
            must_be_ineligible=not dev_eligible,
        )
        return result
    if not dev_eligible:
        raise CampaignContractError("validation/holdout evidence bypassed failed development selection")
    val_rows = [row for row in rows if row["split"] == "validation"]
    validation_failed = bool(_validation_mandatory_regressions(val_rows)) or not _validation_approved_pairs(val_rows)
    if phase == "validation":
        _verify_early_terminal(
            result, exact=exact, phase=phase, reason="validation gate failed",
            must_be_ineligible=validation_failed,
        )
        return result
    if validation_failed:
        raise CampaignContractError("holdout evidence bypassed failed validation gate")
    holdout_rows = [row for row in rows if row["split"] == "holdout"]
    if _validation_mandatory_regressions(holdout_rows):
        _verify_early_terminal(
            result, exact=exact, phase="holdout",
            reason="holdout confirmation failed", must_be_ineligible=True,
            holdout_ledger_key=expected_holdout_key,
        )
        if (root / "results").exists():
            raise CampaignContractError("holdout ineligible terminal must not require a protected result")
        return result
    result_root = root / "results"
    result_store = HAblationResultStore(result_root, report_store=report_store)
    result_id = result.get("protected_result_id") or f"{exact.campaign_id}-result"
    derived = result_store.get(result_id)
    if derived.status != result["status"]:
        raise CampaignContractError("derived verdict disagrees with persisted result")
    if derived.closest_protected_result.cell != result["closest_protected_result"]:
        raise CampaignContractError("derived closest cell disagrees with persisted result")
    if derived.content_digest != result.get("protected_result_digest"):
        raise CampaignContractError(
            "derived protected_result_digest disagrees with persisted result"
        )
    expected_terminal = {
        "campaign_id": exact.campaign_id,
        "spec_digest": exact.digest(),
        "status": derived.status,
        "closest_protected_result": derived.closest_protected_result.cell,
        "unresolved_gap": derived.unresolved_gap,
        "improved_approved_target_case_ids": list(derived.improved_approved_target_case_ids),
        "regressed_mandatory_case_ids": list(derived.regressed_mandatory_case_ids),
        "mandatory_unverified_case_ids": list(derived.mandatory_unverified_case_ids),
        "rollback_snapshot_hash": derived.rollback_snapshot_hash,
        "protected_result_id": derived.id,
        "protected_result_digest": derived.content_digest,
        "phase": "holdout",
        "holdout_ledger_key": expected_holdout_key,
    }
    if result != expected_terminal:
        raise CampaignContractError("eligible terminal projection does not exactly match protected result")
    return result


def verify_campaign(
    spec: CampaignSpec | str | Path,
    *,
    evidence_root: str | Path,
) -> dict[str, Any]:
    """Public reload accepts only production runtime evidence."""
    return _verify_campaign(
        spec,
        evidence_root=evidence_root,
        expected_runtime_mode="production",
    )


def _verify_campaign_hermetic(
    spec: CampaignSpec | str | Path,
    *,
    evidence_root: str | Path,
) -> dict[str, Any]:
    """Private tests-only reload seam; never exported by the public module."""
    return _verify_campaign(
        spec,
        evidence_root=evidence_root,
        expected_runtime_mode="test-only",
    )


def _verify_early_terminal(
    result: dict[str, Any], *, exact: CampaignSpec, phase: str,
    reason: str, must_be_ineligible: bool,
    holdout_ledger_key: str | None = None,
) -> None:
    expected = {
        "campaign_id": exact.campaign_id,
        "spec_digest": exact.digest(),
        "status": "ineligible",
        "closest_protected_result": "base_scaffold",
        "unresolved_gap": reason,
        "phase": phase,
    }
    if holdout_ledger_key is not None:
        expected["holdout_ledger_key"] = holdout_ledger_key
    if not must_be_ineligible or result != expected:
        raise CampaignContractError("early terminal artifact does not match rederived phase gate")


def _verify_evidence_rows(
    evidence: Any,
    *,
    exact: CampaignSpec,
    expected_splits: tuple[str, ...],
    report_refs: list[dict[str, str]],
    report_store: EvaluationReportStore,
    runtime_attestation: RuntimeAttestation,
    expected_holdout_key: str | None,
    protected_result_id: str | None,
) -> None:
    """Verify row membership and deterministic verdicts without a model call."""
    expected_evidence_fields = {"rows"}
    if expected_holdout_key is not None:
        expected_evidence_fields.add("holdout_ledger_key")
    if protected_result_id is not None:
        expected_evidence_fields.add("protected_result_id")
    if (
        not isinstance(evidence, dict)
        or set(evidence) != expected_evidence_fields
        or evidence.get("holdout_ledger_key") != expected_holdout_key
        or (
            protected_result_id is not None
            and evidence.get("protected_result_id") != protected_result_id
        )
    ):
        raise CampaignContractError("evidence schema is not phase-exact")
    rows = evidence.get("rows")
    if not isinstance(rows, list):
        raise CampaignContractError("evidence rows are missing")
    cases = {case.case_id: case for case in exact.cases if case.split in expected_splits}
    expected = {
        (cell, case.case_id, repetition)
        for cell in CELLS for case in cases.values()
        for repetition in range(1, len(exact.repetition_seeds) + 1)
    }
    seen: set[tuple[str, str, int]] = set()
    reports = {
        (ref["split"], ref["cell"], ref["view"]): report_store.get(ref["id"])
        for ref in report_refs
    }
    expected_row_fields = {
        "cell", "case_id", "view", "split", "mandatory",
        "approved_target", "repetition", "seed", "verdict", "assertion",
        "request_digest", "request_config_digest", "runner_config",
        "metrics", "worker_result", "worker_result_digest", "output",
        "receipts", "advice_digest", "advice_changed",
        "inner_task_digest", "outer_task_digest", "base_task_digest",
        "outer_task", "inner_task", "consult_receipt_hash",
        "log_receipt_hash", "selected_record_ids",
        "selected_record_hashes", "selected_record_contents",
        "consult_receipt_selected_targets",
        "consult_receipt_before_content_hashes", "current_run_receipts",
        "runner_model_attestation_digest", "runtime_attestation_digest",
    }
    cumulative_metrics = {cell: EvalMetrics() for cell in CELLS}
    for row in rows:
        if not isinstance(row, dict) or set(row) != expected_row_fields:
            raise CampaignContractError("evidence row schema is not exact")
        _assert_secret_safe_worker_result(row)
        cell, case_id, repetition = row.get("cell"), row.get("case_id"), row.get("repetition")
        marker = (cell, case_id, repetition)
        if marker not in expected or marker in seen:
            raise CampaignContractError("evidence row membership is invalid")
        seen.add(marker)
        case = cases[case_id]
        if (
            row.get("split") != case.split or row.get("view") != case.view
            or row.get("mandatory") != case.mandatory
            or row.get("approved_target") != case.approved_target
            or row.get("seed") != exact.repetition_seeds[repetition - 1]
        ):
            raise CampaignContractError("evidence row contract drift")
        worker = row.get("worker_result")
        assertion = row.get("assertion")
        if not isinstance(worker, dict) or not isinstance(assertion, dict):
            raise CampaignContractError("evidence row lacks worker result or assertion")
        if row.get("worker_result_digest") != sha256_hex(canonical_json_bytes(worker)):
            raise CampaignContractError("WorkerResult evidence digest mismatch")
        if set(worker) != set(WorkerResult.model_fields):
            raise CampaignContractError("WorkerResult schema is not exact")
        try:
            canonical_worker = WorkerResult.model_validate(worker)
        except Exception as exc:
            raise CampaignContractError("WorkerResult schema is invalid") from exc
        if canonical_worker.model_dump(mode="json") != worker:
            raise CampaignContractError("WorkerResult serialization is not canonical")
        if (
            worker.get("task_id") != case_id
            or worker.get("worker_id") != exact.runner_id
            or worker.get("model") != exact.model.model_id
            or worker.get("error") is not None
            or worker.get("error_kind") is not None
            or worker.get("timed_out") is not False
            or not isinstance(worker.get("raw_text"), str)
            or worker.get("tool_calls") != []
            or worker.get("workspace_root") != ""
        ):
            raise CampaignContractError("WorkerResult identity or terminal state is invalid")
        metrics = row.get("metrics")
        if not isinstance(metrics, dict) or any(worker.get(key, 0) != metrics.get(key) for key in ("tokens_in", "tokens_out", "cost_usd", "latency_s")):
            raise CampaignContractError("WorkerResult metrics do not match evidence metrics")
        try:
            typed_metrics = EvalMetrics.model_validate(metrics)
        except Exception as exc:
            raise CampaignContractError("evidence metrics are invalid") from exc
        _enforce_per_attempt_budget(exact, cell=cell, metrics=typed_metrics)
        cumulative_metrics[cell] = cumulative_metrics[cell].plus(typed_metrics)
        if row.get("output") != worker.get("output"):
            raise CampaignContractError(
                "duplicate row output differs from canonical WorkerResult output"
            )
        expected_config = _build_worker_config(exact, cell, repetition, row["seed"])
        if row.get("runner_config") != expected_config:
            raise CampaignContractError("runner configuration does not match frozen spec")
        expected_config_digest = sha256_hex(canonical_json_bytes({
            "cell": cell, "repetition": repetition, "seed": row["seed"],
            "runner_config": expected_config,
        }))
        if row.get("request_config_digest") != expected_config_digest:
            raise CampaignContractError("request configuration digest mismatch")
        expected_model_attestation = {
            "model": exact.model.model_id,
            "base_url": exact.model.base_url,
            "temperature": exact.model.inference_parameters.get("temperature"),
            "max_tokens": exact.model.inference_parameters.get("max_tokens"),
            "thinking": exact.model.inference_parameters.get("thinking"),
            "extra_body": {
                **(exact.model.inference_parameters.get("extra_body") or {}),
                "seed": row["seed"],
            },
        }
        expected_model_digest = sha256_hex(
            canonical_json_bytes(expected_model_attestation)
        )
        runner_attestation_digest = row.get(
            "runner_model_attestation_digest"
        )
        if (
            runtime_attestation.mode == "production"
            and runner_attestation_digest != expected_model_digest
        ):
            raise CampaignContractError("runner model attestation digest mismatch")
        if (
            runtime_attestation.mode == "test-only"
            and runner_attestation_digest is not None
        ):
            raise CampaignContractError("runner model attestation digest mismatch")
        if (
            runtime_attestation.digest()
            != row.get("runtime_attestation_digest")
        ):
            raise CampaignContractError(
                "row runtime attestation digest mismatch"
            )
        if _canonical_parse_output(
            worker["raw_text"],
            expect_json=bool(inner_task.get("output_schema"))
            if isinstance((inner_task := row.get("inner_task")), dict)
            else False,
        ) != worker.get("output"):
            raise CampaignContractError(
                "WorkerResult output is not the canonical parse of raw_text"
            )
        if _verdict_from_verifier(worker.get("output"), assertion) != row.get("verdict"):
            raise CampaignContractError("stored deterministic verdict mismatch")
        report = reports[(case.split, cell, case.view)]
        report_case = next((item for item in report.cases if item.case_id == case_id), None)
        if report_case is None:
            raise CampaignContractError("evidence row case is absent from stored report")
        attempt = next((item for item in report_case.attempts if item.repetition == repetition), None)
        if attempt is None or attempt.verdict != row["verdict"] or attempt.metrics.model_dump(mode="json") != row["metrics"]:
            raise CampaignContractError("evidence attempt does not match stored report")
        if case.split != "holdout" and attempt.output != worker.get("output"):
            raise CampaignContractError("stored report output does not match worker evidence")
        outer_task, inner_task = row.get("outer_task"), row.get("inner_task")
        if not isinstance(outer_task, dict) or not isinstance(inner_task, dict):
            raise CampaignContractError("evidence row lacks exact task chain")
        def digest(value: Any) -> str:
            return "sha256:" + sha256_hex(canonical_json_bytes(value))
        if case_input_digest(outer_task, assertion) != case.input_digest:
            raise CampaignContractError("evidence task/assertion no longer matches CaseContract")
        outer_without_advice = {key: value for key, value in outer_task.items() if key != "advice"}
        inner_without_advice = {key: value for key, value in inner_task.items() if key != "advice"}
        if outer_without_advice != inner_without_advice:
            raise CampaignContractError("inner task changes a non-advice surface")
        if (
            digest(outer_task) != row.get("outer_task_digest")
            or digest(inner_task) != row.get("inner_task_digest")
            or digest(outer_without_advice)
            != row.get("base_task_digest")
            or digest(inner_task.get("advice", [])) != row.get("advice_digest")
        ):
            raise CampaignContractError("task/advice digest chain mismatch")
        if row.get("advice_changed") != (
            inner_task.get("advice") != outer_task.get("advice")
        ):
            raise CampaignContractError("advice_changed is not recomputable")
        expected_request = sha256_hex(canonical_json_bytes({
            "task": outer_task, "assertion": assertion, "cell": cell,
            "repetition": repetition, "seed": row["seed"],
            "runner_config": row["runner_config"],
        }))
        if expected_request != row.get("request_digest"):
            raise CampaignContractError("request digest mismatch")
        from metaharness.memory import MemoryActionReceipt

        receipt_by_hash: dict[str, MemoryActionReceipt] = {}
        for raw_receipt in row.get("receipts", []):
            if not isinstance(raw_receipt, dict):
                raise CampaignContractError("evidence receipt is malformed")
            try:
                receipt = MemoryActionReceipt.model_validate(raw_receipt)
            except Exception as exc:
                raise CampaignContractError("evidence receipt integrity mismatch") from exc
            receipt_by_hash[receipt.content_hash] = receipt
        receipt_hashes = set(receipt_by_hash)
        current_receipts = row.get("current_run_receipts")
        ordered_receipt_hashes = [
            receipt.content_hash
            for receipt in (
                MemoryActionReceipt.model_validate(raw)
                for raw in row.get("receipts", [])
            )
        ]
        if (
            not isinstance(current_receipts, list)
            or ordered_receipt_hashes != current_receipts
            or len(ordered_receipt_hashes) != len(set(ordered_receipt_hashes))
        ):
            raise CampaignContractError(
                "receipts must equal current_run_receipts in exact order"
            )
        consulted = row.get("consult_receipt_hash")
        if cell == "no_external_memory":
            if consulted is not None or row.get("selected_record_ids") or receipt_hashes or inner_task != outer_task:
                raise CampaignContractError("no-memory evidence contains memory chain")
        elif not isinstance(consulted, str) or consulted not in receipt_hashes:
            raise CampaignContractError("memory consult receipt is not bound to evidence")
        elif (
            receipt_by_hash[consulted].phase != "consult"
            or receipt_by_hash[consulted].operation != "search"
            or tuple(row.get("selected_record_ids", ()))
            != receipt_by_hash[consulted].selected_targets
            or tuple(row.get("consult_receipt_selected_targets", ()))
            != receipt_by_hash[consulted].selected_targets
        ):
            raise CampaignContractError("consult receipt-selected-record chain mismatch")
        else:
            cell_contract = next(
                item for item in exact.cells if item.name == cell
            )
            snapshot = (
                cell_contract.base_snapshot
                if cell == "base_scaffold"
                else cell_contract.optimized_snapshot
            )
            assert snapshot is not None
            consult_receipt = receipt_by_hash[consulted]
            if (
                consult_receipt.snapshot_id != snapshot.snapshot_id
                or consult_receipt.snapshot_content_hash
                != snapshot.content_hash
                or consult_receipt.skill_id != snapshot.skill_id
                or consult_receipt.scope != snapshot.scope
                or consult_receipt.context_id != "memory-aware-runner"
            ):
                raise CampaignContractError(
                    "CONSULT receipt differs from exact cell snapshot contract"
                )
            selected_hashes = row.get("selected_record_hashes")
            if not isinstance(selected_hashes, list) or [entry.get("id") for entry in selected_hashes] != row.get("selected_record_ids"):
                raise CampaignContractError("selected-record hashes are incomplete or unordered")
            supplied = dict(receipt_by_hash[consulted].before_content_hashes)
            for entry in selected_hashes:
                if not isinstance(entry, dict) or entry.get("id") not in row.get("selected_record_ids", []):
                    raise CampaignContractError("selected-record hash chain is malformed")
                if supplied.get(entry["id"]) is not None and supplied[entry["id"]] != entry.get("hash"):
                    raise CampaignContractError("selected-record hash does not match consult receipt")
            contents = row.get("selected_record_contents")
            if not isinstance(contents, list) or [entry.get("id") for entry in contents] != row.get("selected_record_ids"):
                raise CampaignContractError("selected-record content order is not bound")
            from metaharness.context.models import content_hash

            hashes = {entry["id"]: entry.get("hash") for entry in selected_hashes}
            if any(content_hash(entry.get("content")) != hashes.get(entry.get("id")) for entry in contents):
                raise CampaignContractError("selected-record content hash mismatch")
            rendered = list(outer_task.get("advice", [])) + [
                f"[governed memory record {entry['id']}; untrusted advice]\n{entry['content']}"
                for entry in contents
            ]
            if inner_task.get("advice") != rendered:
                raise CampaignContractError("inner advice is not the exact selected-record rendering")
        if row.get("log_receipt_hash") is not None:
            log_hash = row["log_receipt_hash"]
            log = receipt_by_hash.get(log_hash)
            if log is None or log.phase != "log" or log.operation != "create_candidate":
                raise CampaignContractError("LOG receipt chain mismatch")
        expected_phases = ["consult"] if cell != "no_external_memory" else []
        if row.get("log_receipt_hash") is not None:
            expected_phases.append("log")
        actual_phases = [
            receipt_by_hash[item].phase for item in current_receipts
        ]
        if actual_phases != expected_phases:
            raise CampaignContractError("receipt phase order is not exact")
    if seen != expected:
        raise CampaignContractError("evidence rows do not exactly cover frozen cases")
    for cell in CELLS:
        _enforce_total_budget(exact, cumulative_metrics[cell])


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="h_campaign")
    sub = parser.add_subparsers(dest="command", required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("--spec", required=True)
    verify.add_argument("--evidence-root", required=True)
    args = parser.parse_args(argv)
    if args.command == "verify":
        print(json.dumps(verify_campaign(args.spec, evidence_root=args.evidence_root), sort_keys=True))
        return 0
    raise CampaignContractError("run requires the orchestrator's protected execution adapter")


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "CampaignContractError",
    "CampaignEvidenceError",
    "CampaignSpec",
    "CaseContract",
    "CellContract",
    "EvaluatorContract",
    "HoldoutAlreadyConsumedError",
    "HoldoutConsumptionLedger",
    "HSurface",
    "ModelContract",
    "ProtectedInputPackage",
    "SelectionDeclaration",
    "BudgetContract",
    "deterministic_verify",
    "load_protected_inputs",
    "run_campaign",
    "verify_campaign",
    "load_spec",
]
