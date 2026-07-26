from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from metaharness.blueprints import ArtifactRef
from metaharness.context import ContextScope
from metaharness.evals import (
    AblationBudgetError,
    AblationContractError,
    AblationReferenceMismatchError,
    ArtifactDigestRef,
    BudgetEnvelope,
    CellEvidenceBinding,
    HAblationCampaign,
    HAblationCell,
    HAblationResult,
    HAblationResultStore,
    ProtectedCaseResult,
    ProtectedEvaluationReportRef,
    ProtectedRunContextManifest,
    RepetitionSeed,
    build_protected_evidence_row,
    evaluate_protected_h_ablation,
)
from metaharness.evals.artifact_store import (
    EvalArtifactAlreadyExistsError,
    EvaluationReportStore,
)
from metaharness.evals.artifacts import (
    EvalAttemptResult,
    EvalCaseResult,
    EvalMetrics,
    EvaluationReport,
)
from metaharness.evals.models import EvalAssertion
from metaharness.memory import MemoryCognitiveSkillSnapshot
from metaharness.memory.promotion import Evidence, PromotionGate, SearchSetLeakageError
from metaharness.portable.integrity import canonical_json_bytes, sha256_hex


CELLS = ("no_external_memory", "base_scaffold", "optimized_scaffold")
VIEWS = (
    "approved_target",
    "transfer",
    "replay_retention",
    "privacy",
    "safety",
    "efficiency",
)


def _digest(label: str) -> str:
    return sha256_hex(label.encode("utf-8"))


def _memory_hash(label: str) -> str:
    return "sha256:" + _digest(label)


def _snapshot(snapshot_id: str, *, parent: str | None = None):
    return MemoryCognitiveSkillSnapshot(
        snapshot_id=snapshot_id,
        skill_id="shadow-skill",
        scope=ContextScope(project_id="meta-harness"),
        goal_families=("protected-ablation",),
        roles=("builder",),
        parent_snapshot_hash=parent,
    )


def _report(
    *,
    cell: str,
    view: str,
    verdicts: tuple[str, ...],
    created_at: float = 1.0,
) -> EvaluationReport:
    case_id = f"case-{view.replace('_', '-')}"
    assertion = EvalAssertion(success_check={"equals": "expected"})
    assertion_digest = sha256_hex(
        canonical_json_bytes(assertion.model_dump(mode="json"))
    )
    attempts = [
        EvalAttemptResult(
            repetition=index,
            verdict=verdict,
            scorer="protected-scorer",
            detail=f"visible result {index}",
            output="expected" if verdict == "pass" else "other",
            metrics=EvalMetrics(
                tokens_in=10,
                tokens_out=5,
                cost_usd=0.1,
                latency_s=0.2,
            ),
        )
        for index, verdict in enumerate(verdicts, start=1)
    ]
    case_verdict = (
        "fail"
        if "fail" in verdicts
        else "unverified"
        if "unverified" in verdicts
        else "pass"
    )
    case = EvalCaseResult(
        case_id=case_id,
        split="validation",
        assertion_kind="success_check",
        assertion_digest=assertion_digest,
        assertion=assertion,
        attempts=attempts,
        verdict=case_verdict,
    )
    metrics = EvalMetrics()
    for attempt in attempts:
        metrics = metrics.plus(attempt.metrics)
    values = {
        "schema_version": 1,
        "id": f"report-{cell.replace('_', '-')}-{view.replace('_', '-')}",
        "blueprint_ref": ArtifactRef(
            id=f"blueprint-{cell.replace('_', '-')}", version=1
        ),
        "eval_ref": ArtifactRef(id="protected-eval", version=1),
        "split": "validation",
        "blueprint_digest": _digest(f"blueprint:{cell}"),
        "workflow_digest": _digest(f"workflow:{cell}"),
        "eval_digest": _digest("protected-eval"),
        "runner_id": "protected-runner",
        "cases": [case],
        "metrics": metrics,
        "passed": int(case_verdict == "pass"),
        "failed": int(case_verdict == "fail"),
        "unverified": int(case_verdict == "unverified"),
        "created_at": created_at,
    }
    values["content_digest"] = sha256_hex(
        canonical_json_bytes(
            {
                key: (
                    value.model_dump(mode="json")
                    if hasattr(value, "model_dump")
                    else [item.model_dump(mode="json") for item in value]
                    if key == "cases"
                    else value
                )
                for key, value in values.items()
                if key not in {"id", "created_at"}
            }
        )
    )
    return EvaluationReport.model_validate(values)


def _manifest(
    *,
    cell: str,
    reports: list[EvaluationReport],
    scaffold_hash: str | None,
    mandatory: dict[str, bool] | None = None,
    **changes,
) -> ProtectedRunContextManifest:
    mandatory = mandatory or {}
    values = {
        "cell": cell,
        "blueprint_ref": reports[0].blueprint_ref,
        "blueprint_digest": reports[0].blueprint_digest,
        "workflow_ref": ArtifactRef(
            id=f"workflow-{cell.replace('_', '-')}", version=1
        ),
        "workflow_digest": reports[0].workflow_digest,
        "scaffold_h_snapshot_hash": scaffold_hash,
        "evaluator_ref": reports[0].eval_ref,
        "evaluator_digest": reports[0].eval_digest,
        "case_set_digest": _digest("case-set"),
        "runner_id": reports[0].runner_id,
        "runner_configuration_digest": _digest("runner-config"),
        "task_model_portfolio_ref": ArtifactRef(id="portfolio", version=1),
        "task_model_portfolio_digest": _digest("portfolio"),
        "w_snapshot_refs": (
            ArtifactDigestRef(
                ref=ArtifactRef(id="weight", version=1),
                content_digest=_digest("weight"),
            ),
        ),
        "repetition_seed_schedule": (
            RepetitionSeed(repetition=1, seed=101),
            RepetitionSeed(repetition=2, seed=202),
        ),
        "budget": BudgetEnvelope(
            token_limit=10_000,
            cost_usd_limit=100.0,
            wall_time_s_limit=100.0,
        ),
        "evaluation_report_refs": tuple(
            ProtectedEvaluationReportRef(
                id=report.id,
                content_digest=report.content_digest,
                split=report.split,
                view=view,
                case_ids=tuple(case.case_id for case in report.cases),
                mandatory_case_ids=(
                    tuple(case.case_id for case in report.cases)
                    if mandatory.get(view, True)
                    else ()
                ),
            )
            for view, report in zip(VIEWS, reports)
        ),
        "protected_evaluator_authority_id": "protected-evaluator",
    }
    values.update(changes)
    return ProtectedRunContextManifest(**values)


def _replace_self_hashed(model, **changes):
    values = model.model_dump(mode="python")
    values.update(changes)
    values.pop("content_digest", None)
    return type(model).model_validate(values)


def _fixture(
    tmp_path: Path,
    *,
    outcomes: dict[tuple[str, str], tuple[str, ...]] | None = None,
    mandatory: dict[str, bool] | None = None,
):
    outcomes = outcomes or {}
    mandatory = mandatory or {}
    report_store = EvaluationReportStore(tmp_path / "reports")
    reports: dict[str, list[EvaluationReport]] = {}
    for cell in CELLS:
        cell_reports = []
        for view in VIEWS:
            default = ("pass", "pass")
            if view == "approved_target":
                default = {
                    "no_external_memory": ("fail", "fail"),
                    "base_scaffold": ("pass", "fail"),
                    "optimized_scaffold": ("pass", "pass"),
                }[cell]
            elif view == "replay_retention" and cell == "no_external_memory":
                default = ("fail", "fail")
            report = _report(
                cell=cell,
                view=view,
                verdicts=outcomes.get((cell, view), default),
            )
            report_store.create(report)
            cell_reports.append(report)
        reports[cell] = cell_reports

    base = _snapshot("base")
    optimized = _snapshot("optimized", parent=base.content_hash)
    manifests = {
        "no_external_memory": _manifest(
            cell="no_external_memory",
            reports=reports["no_external_memory"],
            scaffold_hash=None,
            mandatory=mandatory,
        ),
        "base_scaffold": _manifest(
            cell="base_scaffold",
            reports=reports["base_scaffold"],
            scaffold_hash=base.content_hash,
            mandatory=mandatory,
        ),
        "optimized_scaffold": _manifest(
            cell="optimized_scaffold",
            reports=reports["optimized_scaffold"],
            scaffold_hash=optimized.content_hash,
            mandatory=mandatory,
        ),
    }
    campaign = HAblationCampaign(
        campaign_id="protected-campaign",
        cells=tuple(
            HAblationCell(name=cell, manifest=manifests[cell]) for cell in CELLS
        ),
        base_scaffold_snapshot=base,
        optimized_scaffold_snapshot=optimized,
        rollback_snapshot_hash=base.content_hash,
        required_views=VIEWS,
        search_evidence=Evidence(
            search_set_id="search-set-one",
            evaluation_count=2,
            held_out_evaluation_count=1,
        ),
    )
    rows = []
    for index, view in enumerate(VIEWS):
        rows.append(
            build_protected_evidence_row(
                case_id=f"case-{view.replace('_', '-')}",
                view=view,
                bindings=tuple(
                    CellEvidenceBinding(
                        cell=cell,
                        report_ref=manifests[cell].evaluation_report_refs[index],
                        memory_receipt_hashes=(
                            ()
                            if cell == "no_external_memory"
                            else (_memory_hash(f"{cell}:{view}"),)
                        ),
                    )
                    for cell in CELLS
                ),
                report_store=report_store,
            )
        )
    result = evaluate_protected_h_ablation(
        result_id="ablation-result",
        campaign=campaign,
        evidence_rows=tuple(rows),
        report_store=report_store,
        created_at=10.0,
    )
    return report_store, campaign, tuple(rows), result


def test_complete_three_cell_campaign_derives_protected_result_and_round_trips(tmp_path):
    report_store, campaign, rows, result = _fixture(tmp_path)

    assert tuple(cell.name for cell in campaign.cells) == CELLS
    assert {row.view for row in rows} == set(VIEWS)
    assert result.status == "eligible_pending_human_promotion"
    assert result.w_mem_lane_unblocked is True
    assert result.improved_approved_target_case_ids == ("case-approved-target",)
    assert result.regressed_mandatory_case_ids == ()
    assert result.rollback_snapshot_hash == campaign.base_scaffold_snapshot.content_hash
    replay_delta = next(
        delta for delta in result.case_deltas if delta.case_id == "case-replay-retention"
    )
    assert replay_delta.no_memory_to_base == 1
    approved_base = next(
        summary
        for summary in result.view_summaries
        if summary.cell == "base_scaffold" and summary.view == "approved_target"
    )
    assert approved_base.outcome_variance > 0.0
    assert approved_base.metrics.tokens_in > 0
    assert all(row.outcomes[0].memory_receipt_hashes == () for row in rows)
    assert all(row.outcomes[1].memory_receipt_hashes for row in rows)
    assert all(row.outcomes[2].memory_receipt_hashes for row in rows)

    store = HAblationResultStore(tmp_path / "results", report_store=report_store)
    stored = store.create(result)
    path = tmp_path / "results" / "h-ablation-results" / "ablation-result.json"
    persisted_bytes = path.read_bytes()
    reloaded = store.get(result.id)
    assert reloaded == stored
    assert reloaded.model_dump(mode="json") == stored.model_dump(mode="json")
    assert path.read_bytes() == persisted_bytes
    assert not hasattr(store, "promote")
    assert not hasattr(store, "activate")
    assert not hasattr(store, "deploy")


def test_promotion_boundary_is_strict_immutable_and_rejects_search_set_leakage():
    gate = PromotionGate()
    with pytest.raises(SearchSetLeakageError):
        gate.decide(
            Evidence(
                search_set_id="search-one",
                evaluation_count=5,
                held_out_evaluation_count=0,
            )
        )
    accepted = gate.decide(
        Evidence(
            search_set_id="search-one",
            evaluation_count=5,
            held_out_evaluation_count=1,
        )
    )
    assert accepted.status == "evidence_accepted"
    assert not hasattr(accepted, "activate")
    with pytest.raises(ValidationError):
        Evidence(search_set_id="", evaluation_count=1, held_out_evaluation_count=1)
    with pytest.raises(ValidationError):
        Evidence(search_set_id="search", evaluation_count=-1, held_out_evaluation_count=0)
    with pytest.raises(ValidationError):
        Evidence(search_set_id="search", evaluation_count=1.5, held_out_evaluation_count=0)
    with pytest.raises(ValidationError):
        accepted.status = "promoted"  # type: ignore[misc]


def test_campaign_rejects_missing_cells_views_lineage_and_wrong_rollback(tmp_path):
    _, campaign, _, _ = _fixture(tmp_path)
    common = campaign.model_dump(mode="python")
    cases = [
        ({**common, "cells": common["cells"][:-1]}, "exactly three"),
        ({**common, "required_views": VIEWS[:-1]}, "required protected views"),
        (
            {
                **common,
                "optimized_scaffold_snapshot": _snapshot("unbound-optimized"),
            },
            "parent",
        ),
        ({**common, "rollback_snapshot_hash": _memory_hash("wrong")}, "rollback"),
    ]
    for values, message in cases:
        with pytest.raises(ValidationError, match=message):
            HAblationCampaign.model_validate(values)


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"evaluator_digest": _digest("wrong-e")}, "evaluator"),
        ({"case_set_digest": _digest("wrong-cases")}, "case"),
        ({"runner_id": "other-runner"}, "runner"),
        (
            {"task_model_portfolio_digest": _digest("other-portfolio")},
            "portfolio",
        ),
        (
            {
                "w_snapshot_refs": (
                    ArtifactDigestRef(
                        ref=ArtifactRef(id="other-weight", version=1),
                        content_digest=_digest("other-weight"),
                    ),
                )
            },
            "W",
        ),
        (
            {
                "repetition_seed_schedule": (
                    RepetitionSeed(repetition=1, seed=999),
                    RepetitionSeed(repetition=2, seed=202),
                )
            },
            "schedule",
        ),
        (
            {
                "budget": BudgetEnvelope(
                    token_limit=9_999,
                    cost_usd_limit=100.0,
                    wall_time_s_limit=100.0,
                )
            },
            "budget",
        ),
    ],
)
def test_campaign_fails_closed_when_a_per_cell_frozen_axis_differs(
    tmp_path, changes, message
):
    _, campaign, _, _ = _fixture(tmp_path)
    optimized = _replace_self_hashed(campaign.cells[2].manifest, **changes)
    cells = campaign.cells[:2] + (
        HAblationCell(name="optimized_scaffold", manifest=optimized),
    )
    with pytest.raises(ValidationError, match=message):
        HAblationCampaign.model_validate(
            {**campaign.model_dump(mode="python"), "cells": cells}
        )


def test_campaign_rejects_memory_receipt_boundary_violations(tmp_path):
    report_store, campaign, rows, _ = _fixture(tmp_path)
    no_memory = rows[0].outcomes[0]
    with pytest.raises(ValidationError, match="no-memory"):
        build_protected_evidence_row(
            case_id=rows[0].case_id,
            view=rows[0].view,
            bindings=(
                CellEvidenceBinding(
                    cell=no_memory.cell,
                    report_ref=no_memory.report_ref,
                    memory_receipt_hashes=(_memory_hash("forbidden"),),
                ),
                *(
                    CellEvidenceBinding(
                        cell=outcome.cell,
                        report_ref=outcome.report_ref,
                        memory_receipt_hashes=outcome.memory_receipt_hashes,
                    )
                    for outcome in rows[0].outcomes[1:]
                ),
            ),
            report_store=report_store,
        )
    with pytest.raises(ValidationError, match="memory cell"):
        build_protected_evidence_row(
            case_id=rows[0].case_id,
            view=rows[0].view,
            bindings=tuple(
                CellEvidenceBinding(
                    cell=outcome.cell,
                    report_ref=outcome.report_ref,
                    memory_receipt_hashes=(
                        ()
                        if outcome.cell in {"no_external_memory", "base_scaffold"}
                        else outcome.memory_receipt_hashes
                    ),
                )
                for outcome in rows[0].outcomes
            ),
            report_store=report_store,
        )
    assert campaign.cells[0].manifest.scaffold_h_snapshot_hash is None


def test_evidence_construction_resolves_refs_cases_splits_and_sealed_shape(tmp_path):
    report_store, _, rows, _ = _fixture(tmp_path)
    bindings = tuple(
        CellEvidenceBinding(
            cell=outcome.cell,
            report_ref=outcome.report_ref,
            memory_receipt_hashes=outcome.memory_receipt_hashes,
        )
        for outcome in rows[0].outcomes
    )
    bad_digest = bindings[0].report_ref.model_copy(
        update={"content_digest": _digest("tampered")}
    )
    with pytest.raises(AblationReferenceMismatchError, match="digest"):
        build_protected_evidence_row(
            case_id=rows[0].case_id,
            view=rows[0].view,
            bindings=(
                CellEvidenceBinding(
                    cell=bindings[0].cell,
                    report_ref=bad_digest,
                    memory_receipt_hashes=(),
                ),
                *bindings[1:],
            ),
            report_store=report_store,
        )
    with pytest.raises(AblationReferenceMismatchError, match="case"):
        build_protected_evidence_row(
            case_id="wrong-case",
            view=rows[0].view,
            bindings=bindings,
            report_store=report_store,
        )
    bad_split = bindings[0].report_ref.model_copy(update={"split": "development"})
    with pytest.raises(AblationReferenceMismatchError, match="split"):
        build_protected_evidence_row(
            case_id=rows[0].case_id,
            view=rows[0].view,
            bindings=(
                CellEvidenceBinding(
                    cell=bindings[0].cell,
                    report_ref=bad_split,
                    memory_receipt_hashes=(),
                ),
                *bindings[1:],
            ),
            report_store=report_store,
        )
    with pytest.raises(AblationReferenceMismatchError, match="view"):
        build_protected_evidence_row(
            case_id=rows[0].case_id,
            view="transfer",
            bindings=bindings,
            report_store=report_store,
        )
    protected = rows[0].outcomes[0].case_result.model_dump(mode="python")
    protected["output"] = "sealed payload"
    with pytest.raises(ValidationError, match="extra_forbidden"):
        ProtectedCaseResult.model_validate(protected)


def test_aggregate_gain_cannot_cancel_one_mandatory_regression(tmp_path):
    outcomes = {
        ("optimized_scaffold", "safety"): ("fail", "fail"),
        ("base_scaffold", "safety"): ("pass", "pass"),
    }
    _, _, _, result = _fixture(tmp_path, outcomes=outcomes)
    assert result.status == "ineligible"
    assert result.w_mem_lane_unblocked is False
    assert result.improved_approved_target_case_ids == ("case-approved-target",)
    assert result.regressed_mandatory_case_ids == ("case-safety",)
    assert result.closest_protected_result.cell == "base_scaffold"
    assert "mandatory regression" in result.unresolved_gap


def test_equal_result_and_mandatory_unverified_result_are_ineligible(tmp_path):
    equal_outcomes = {
        ("base_scaffold", "approved_target"): ("pass", "pass"),
        ("optimized_scaffold", "approved_target"): ("pass", "pass"),
    }
    _, _, _, equal = _fixture(tmp_path / "equal", outcomes=equal_outcomes)
    assert equal.status == "ineligible"
    assert equal.improved_approved_target_case_ids == ()
    assert "no approved-target case improved" in equal.unresolved_gap

    unverified_outcomes = {
        ("optimized_scaffold", "safety"): ("pass", "unverified"),
    }
    _, _, _, unverified = _fixture(
        tmp_path / "unverified", outcomes=unverified_outcomes
    )
    assert unverified.status == "ineligible"
    assert unverified.mandatory_unverified_case_ids == ("case-safety",)
    assert "mandatory unverified" in unverified.unresolved_gap


def test_repeated_search_evidence_is_rejected_before_eligibility(tmp_path):
    report_store, campaign, rows, _ = _fixture(tmp_path)
    leaky = HAblationCampaign.model_validate(
        {
            **campaign.model_dump(mode="python"),
            "search_evidence": Evidence(
                search_set_id="reused-search",
                evaluation_count=4,
                held_out_evaluation_count=0,
            ),
        }
    )
    with pytest.raises(SearchSetLeakageError):
        evaluate_protected_h_ablation(
            result_id="leaky-result",
            campaign=leaky,
            evidence_rows=rows,
            report_store=report_store,
        )


def test_missing_evidence_view_and_budget_breach_fail_closed(tmp_path):
    report_store, campaign, rows, _ = _fixture(tmp_path)
    with pytest.raises(AblationContractError, match="protected view"):
        evaluate_protected_h_ablation(
            result_id="missing-view",
            campaign=campaign,
            evidence_rows=rows[:-1],
            report_store=report_store,
        )

    low_budgets = (
        BudgetEnvelope(
            token_limit=1,
            cost_usd_limit=100.0,
            wall_time_s_limit=100.0,
        ),
        BudgetEnvelope(
            token_limit=10_000,
            cost_usd_limit=0.01,
            wall_time_s_limit=100.0,
        ),
        BudgetEnvelope(
            token_limit=10_000,
            cost_usd_limit=100.0,
            wall_time_s_limit=0.01,
        ),
    )
    for index, low_budget in enumerate(low_budgets):
        cells = tuple(
            HAblationCell(
                name=cell.name,
                manifest=_replace_self_hashed(cell.manifest, budget=low_budget),
            )
            for cell in campaign.cells
        )
        bounded = HAblationCampaign.model_validate(
            {**campaign.model_dump(mode="python"), "cells": cells}
        )
        with pytest.raises(AblationBudgetError, match="envelope"):
            evaluate_protected_h_ablation(
                result_id=f"over-budget-{index}",
                campaign=bounded,
                evidence_rows=rows,
                report_store=report_store,
            )


def test_result_digest_is_deterministic_tamper_evident_and_store_is_create_only(
    tmp_path,
):
    report_store, campaign, rows, result = _fixture(tmp_path)
    other = evaluate_protected_h_ablation(
        result_id="other-artifact-id",
        campaign=campaign,
        evidence_rows=rows,
        report_store=report_store,
        created_at=999.0,
    )
    assert other.content_digest == result.content_digest

    tampered = result.model_dump(mode="python")
    tampered["status"] = "ineligible"
    with pytest.raises(ValidationError, match="content_digest"):
        HAblationResult.model_validate(tampered)
    with pytest.raises(ValidationError, match="extra_forbidden"):
        HAblationResult.model_validate(
            {**result.model_dump(mode="python"), "promoted": True}
        )
    assert not hasattr(result, "promote")
    assert not hasattr(result, "activate")
    assert not hasattr(result, "deploy")

    store = HAblationResultStore(tmp_path / "results", report_store=report_store)
    store.create(result)
    with pytest.raises(EvalArtifactAlreadyExistsError):
        store.create(
            result.model_copy(
                update={"created_at": result.created_at + 1.0}
            )
        )


def test_fresh_self_hash_cannot_forge_eligibility_decision_fields(tmp_path):
    outcomes = {
        ("optimized_scaffold", "safety"): ("fail", "fail"),
        ("base_scaffold", "safety"): ("pass", "pass"),
    }
    _, _, _, result = _fixture(tmp_path, outcomes=outcomes)
    forged = result.model_dump(mode="python")
    forged.pop("content_digest")
    forged.update(
        status="eligible_pending_human_promotion",
        w_mem_lane_unblocked=True,
        regressed_mandatory_case_ids=(),
        closest_protected_result={
            "cell": "optimized_scaffold",
            "approved_target_pass_rate": 1.0,
        },
        unresolved_gap="",
    )

    with pytest.raises(ValidationError, match="does not match campaign and evidence"):
        HAblationResult.model_validate(forged)


def test_mandatory_safety_row_cannot_be_downgraded(tmp_path):
    outcomes = {
        ("optimized_scaffold", "safety"): ("fail", "fail"),
        ("base_scaffold", "safety"): ("pass", "pass"),
    }
    report_store, campaign, rows, result = _fixture(tmp_path, outcomes=outcomes)
    safety_index = next(index for index, row in enumerate(rows) if row.view == "safety")
    downgraded = rows[safety_index].model_copy(update={"mandatory": False})
    forged_rows = rows[:safety_index] + (downgraded,) + rows[safety_index + 1 :]

    with pytest.raises(ValidationError, match="mandatory mismatch"):
        evaluate_protected_h_ablation(
            result_id="mandatory-downgrade",
            campaign=campaign,
            evidence_rows=forged_rows,
            report_store=report_store,
        )

    forged_result = result.model_dump(mode="python")
    forged_result.pop("content_digest")
    forged_result["evidence_rows"][safety_index]["mandatory"] = False
    with pytest.raises(ValidationError, match="mandatory mismatch"):
        HAblationResult.model_validate(forged_result)


def test_nested_ablation_refs_and_metrics_are_frozen(tmp_path):
    _, _, _, result = _fixture(tmp_path)

    with pytest.raises(ValidationError, match="frozen"):
        result.campaign.cells[0].manifest.blueprint_ref.id = "mutated"  # type: ignore[misc]
    with pytest.raises(ValidationError, match="frozen"):
        result.evidence_rows[0].outcomes[0].case_result.metrics.tokens_in = 0  # type: ignore[misc]
    with pytest.raises(ValidationError, match="frozen"):
        result.view_summaries[0].metrics.cost_usd = 0.0  # type: ignore[misc]


def test_result_store_rechecks_report_store_instead_of_trusting_copied_rows(tmp_path):
    report_store, _, _, result = _fixture(tmp_path)
    report_ref = result.evidence_rows[0].outcomes[0].report_ref
    report_path = (
        tmp_path
        / "reports"
        / "evaluation-reports"
        / f"{report_ref.id}.json"
    )
    raw = json.loads(report_path.read_text(encoding="utf-8"))
    raw["runner_id"] = "tampered-runner"
    report_path.write_text(json.dumps(raw), encoding="utf-8")
    store = HAblationResultStore(tmp_path / "results", report_store=report_store)
    with pytest.raises(AblationReferenceMismatchError, match="report"):
        store.create(result)
