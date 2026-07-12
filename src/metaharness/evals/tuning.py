"""Inert frontier proposals and a narrow human-gated draft-only bridge."""
from __future__ import annotations

import time
from typing import Iterable

from pydantic import TypeAdapter

from metaharness.blueprints import (
    ArtifactRef,
    BlueprintCatalog,
    blueprint_digest,
)
from metaharness.blueprints.models import BlueprintContent, BlueprintDraft
from metaharness.blueprints.store import BlueprintNotFoundError
from metaharness.evals.artifacts import (
    EvaluationReportRef,
    ReplaceStepBoundariesPatch,
    SafeBlueprintPatch,
    SetDescriptionPatch,
    SetStepMaxAttemptsPatch,
    SetStepObjectivePatch,
    TuningProposal,
    proposal_digest,
)
from metaharness.evals.artifact_store import EvaluationReportStore
from metaharness.evals.evaluator import eval_suite_digest, workflow_digest
from metaharness.evals.store import EvalSuiteStore
from metaharness.portable.integrity import canonical_json_bytes, sha256_hex


class TuningError(RuntimeError):
    pass


class TuningReferenceMismatchError(TuningError):
    pass


class UnsafeTuningPatchError(TuningError):
    pass


class HumanApprovalRequiredError(TuningError):
    pass


def create_tuning_proposal(
    *,
    proposal_id: str,
    blueprint_ref: ArtifactRef,
    eval_refs: Iterable[ArtifactRef],
    catalog: BlueprintCatalog,
    eval_store: EvalSuiteStore,
    report_store: EvaluationReportStore,
    report_refs: Iterable[EvaluationReportRef],
    patches: Iterable[SafeBlueprintPatch | dict],
    rationale: str,
    created_at: float | None = None,
) -> TuningProposal:
    """Build an immutable proposal from visible-split evidence only."""
    exact_eval_refs = tuple(ArtifactRef.model_validate(ref) for ref in eval_refs)
    blueprint = catalog.get_version(blueprint_ref)
    if tuple(blueprint.eval_suites) != exact_eval_refs:
        raise TuningReferenceMismatchError(
            "frozen eval refs do not match the exact blueprint version"
        )
    exact_reports = tuple(
        EvaluationReportRef.model_validate(ref) for ref in report_refs
    )
    if not exact_reports:
        raise TuningReferenceMismatchError("tuning requires at least one evaluation report")
    allowed_eval_refs = {(ref.id, ref.version) for ref in exact_eval_refs}
    suites = {
        (ref.id, ref.version): eval_store.get_version_for_evaluation(ref.id, ref.version)
        for ref in exact_eval_refs
    }
    report_list = []
    for report_ref in exact_reports:
        report = report_store.get(report_ref.id)
        if (
            report.content_digest != report_ref.content_digest
            or report.split != report_ref.split
        ):
            raise TuningReferenceMismatchError(
                "immutable evaluation report ref does not match stored content"
            )
        if report.blueprint_ref != blueprint_ref:
            raise TuningReferenceMismatchError(
                "evaluation report blueprint ref does not match frozen tuning input"
            )
        if (report.eval_ref.id, report.eval_ref.version) not in allowed_eval_refs:
            raise TuningReferenceMismatchError(
                "evaluation report suite ref is not in frozen tuning input"
            )
        if (
            report.blueprint_digest != blueprint_digest(blueprint)
            or report.workflow_digest != workflow_digest(blueprint)
            or report.eval_digest
            != eval_suite_digest(suites[(report.eval_ref.id, report.eval_ref.version)])
        ):
            raise TuningReferenceMismatchError(
                "evaluation report provenance does not match exact artifacts"
            )
        suite = suites[(report.eval_ref.id, report.eval_ref.version)]
        expected_cases = (
            suite.development_cases
            if report.split == "development"
            else suite.validation_cases
        )
        if [case.case_id for case in report.cases] != [
            case.id for case in expected_cases
        ]:
            raise TuningReferenceMismatchError(
                "evaluation report cases do not match the exact suite split"
            )
        for result, expected_case in zip(report.cases, expected_cases):
            expected_digest = sha256_hex(
                canonical_json_bytes(
                    expected_case.assertion.model_dump(mode="json")
                )
            )
            if (
                result.assertion != expected_case.assertion
                or result.assertion_digest != expected_digest
            ):
                raise TuningReferenceMismatchError(
                    "evaluation report assertions do not match the exact suite split"
                )
        report_list.append(report)

    timestamp = time.time() if created_at is None else created_at
    patch_adapter = TypeAdapter(SafeBlueprintPatch)
    validated_patches = [patch_adapter.validate_python(patch) for patch in patches]
    data = {
        "schema_version": 1,
        "generator": "frontier",
        "id": proposal_id,
        "blueprint_ref": blueprint_ref,
        "eval_refs": exact_eval_refs,
        "report_refs": exact_reports,
        "patches": validated_patches,
        "rationale": rationale,
        "created_at": timestamp,
    }
    serialized = {
        "schema_version": 1,
        "generator": "frontier",
        "id": proposal_id,
        "blueprint_ref": blueprint_ref.model_dump(mode="json"),
        "eval_refs": [ref.model_dump(mode="json") for ref in exact_eval_refs],
        "report_refs": [
            {
                "id": report.id,
                "content_digest": report.content_digest,
                "split": report.split,
            }
            for report in report_list
        ],
        "patches": [patch.model_dump(mode="json") for patch in validated_patches],
        "rationale": rationale,
        "created_at": timestamp,
    }
    return TuningProposal(**data, proposal_digest=proposal_digest(serialized))


def _apply_safe_patches(
    base: BlueprintContent, patches: list[SafeBlueprintPatch]
) -> BlueprintContent:
    content = base.model_copy(deep=True)
    for patch in patches:
        if isinstance(patch, SetDescriptionPatch):
            content.description = patch.value
            continue
        try:
            step = content.workflow.step(patch.step_id)
        except KeyError as exc:
            raise UnsafeTuningPatchError(
                f"tuning patch references unknown step {patch.step_id!r}"
            ) from exc
        if isinstance(patch, SetStepObjectivePatch):
            step.objective = patch.value
        elif isinstance(patch, ReplaceStepBoundariesPatch):
            step.boundaries = list(patch.value)
        elif isinstance(patch, SetStepMaxAttemptsPatch):
            step.max_attempts = patch.value
        else:  # defensive fail-closed boundary if the union grows without bridge support
            raise UnsafeTuningPatchError(
                f"unsupported safe patch model: {type(patch).__name__}"
            )
    return BlueprintContent.model_validate(content.model_dump(mode="python"))


def apply_tuning_proposal_to_draft(
    proposal: TuningProposal,
    *,
    catalog: BlueprintCatalog,
    owner: str,
    base_version: int,
    expected_revision: int | None,
    human_approved: bool,
    now: float | None = None,
) -> BlueprintDraft:
    """Apply safe patches to an owned draft; never publish or activate eval cases.

    ``base_version`` is deliberately explicit even though it also appears in
    the proposal. This prevents a UI or API from accidentally applying a stale
    proposal to the latest version.
    """
    proposal = TuningProposal.model_validate(proposal.model_dump(mode="python"))
    if human_approved is not True:
        raise HumanApprovalRequiredError(
            "human approval is required before creating a tuned draft"
        )
    if base_version != proposal.blueprint_ref.version:
        raise TuningReferenceMismatchError(
            "explicit base_version does not match tuning proposal"
        )
    base = catalog.get_version(proposal.blueprint_ref)
    if tuple(base.eval_suites) != proposal.eval_refs:
        raise TuningReferenceMismatchError(
            "tuning proposal eval refs do not match the exact blueprint version"
        )

    store = catalog.store
    creating = False
    try:
        current = store.get_draft(base.id)
    except BlueprintNotFoundError:
        if expected_revision is not None:
            raise TuningReferenceMismatchError(
                "expected_revision must be omitted when creating a tuned draft"
            )
        creating = True
        content_source = base
    else:
        if expected_revision is None or current.revision != expected_revision:
            raise TuningReferenceMismatchError(
                "expected_revision does not match the existing tuned draft"
            )
        if current.base_version != base_version:
            raise TuningReferenceMismatchError(
                "existing draft is not based on the frozen blueprint version"
            )
        if tuple(current.eval_suites) != proposal.eval_refs:
            raise TuningReferenceMismatchError(
                "existing draft changed the frozen eval refs"
            )
        content_source = current

    content = BlueprintContent.model_validate(
        {
            name: getattr(content_source, name)
            for name in BlueprintContent.model_fields
        }
    )
    # Complete patch/step/content validation before the first store mutation.
    # An unknown target or invalid patched model therefore leaves no new draft
    # and does not advance an existing revision.
    patched = _apply_safe_patches(content, proposal.patches)
    # Eval references are not part of the patch vocabulary. Stamp the frozen
    # tuple again so future vocabulary changes cannot accidentally alter them.
    patched.eval_suites = list(proposal.eval_refs)
    if creating:
        # This owned-artifact path deliberately uses the store's exact-version
        # copy operation. Built-ins must first be explicitly forked by a human.
        try:
            current = store.create_draft_from_version(
                proposal.blueprint_ref, owner=owner, now=now
            )
        except BlueprintNotFoundError as exc:
            raise TuningReferenceMismatchError(
                "built-in tuning requires an explicit human-created fork"
            ) from exc
    return store.update_draft(
        base.id,
        patched,
        expected_revision=current.revision,
        now=now,
    )
