"""META-7: happy-path + adversarial coverage for the discovery kernel's frozen contracts.

Covers self-hash auto-fill/tamper detection, extra-field rejection, identity/
version binding checks, budget/authority rejection, and canonical-ordering
determinism (same elements, different insertion order -> identical hash).
"""
from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from metaharness.context import ContextVersionBindings
from metaharness.discovery.models import (
    DiscoveryAssignment,
    DiscoveryBoundary,
    DiscoveryBudgets,
    DiscoveryCampaignManifest,
    DiscoveryEvent,
    DiscoveryEventType,
    DiscoveryLineageEventType,
    DiscoveryLineageReceipt,
    DiscoveryModelPortfolioEntry,
    DiscoveryResourceReceipt,
    DiscoveryRole,
    DiscoveryStopCondition,
    DiscoveryTerminalOutcome,
    DiscoveryTerminalReceipt,
)

BASELINE_COMMIT = "a" * 40
BASELINE_TREE = "b" * 40


def make_versions(**overrides) -> ContextVersionBindings:
    defaults = dict(
        model_portfolio_version="mp-1",
        harness_version="h-1",
        evaluator_version="e-1",
        weight_snapshot_version=None,
        memory_snapshot_version=None,
        evidence_snapshot_version="ev-1",
        candidate_version=BASELINE_COMMIT,
        parent_candidate_version=None,
    )
    defaults.update(overrides)
    return ContextVersionBindings(**defaults)


def make_boundary(**overrides) -> DiscoveryBoundary:
    defaults: dict = {"workspace_root": "/work/campaigns/c1"}
    defaults.update(overrides)
    return DiscoveryBoundary(**defaults)


def make_budgets(**overrides) -> DiscoveryBudgets:
    defaults = dict(
        max_concurrency=2,
        max_restarts_per_attempt=1,
        max_attempts=10,
        max_evaluations=10,
        max_wall_seconds=3600,
        attempt_timeout_seconds=600,
    )
    defaults.update(overrides)
    return DiscoveryBudgets(**defaults)


def make_manifest(**overrides) -> DiscoveryCampaignManifest:
    defaults: dict = dict(
        campaign_id="camp-1",
        project_id="meta-harness",
        baseline_commit=BASELINE_COMMIT,
        baseline_tree=BASELINE_TREE,
        versions=make_versions(),
        model_portfolio=[
            {"role": "explorer", "model_id": "m1", "model_version": "v1"},
            {"role": "optimizer", "model_id": "m2", "model_version": "v1"},
        ],
        proxy_evaluator_ref="proxy-eval-ref-1",
        boundary=make_boundary(),
        budgets=make_budgets(),
        seed=42,
        stop_conditions=[DiscoveryStopCondition.WALL_TIME_EXCEEDED, DiscoveryStopCondition.GOAL_REACHED],
    )
    defaults.update(overrides)
    return DiscoveryCampaignManifest(**defaults)


def make_assignment(**overrides) -> DiscoveryAssignment:
    defaults: dict = dict(
        assignment_id="asg-1",
        campaign_id="camp-1",
        lineage_id="lin-1",
        attempt_id="att-1",
        role=DiscoveryRole.EXPLORER,
        seed=1,
        sequence=0,
        created_at=0,
    )
    defaults.update(overrides)
    return DiscoveryAssignment(**defaults)


def make_event(**overrides) -> DiscoveryEvent:
    defaults: dict = dict(
        event_id="evt-1",
        campaign_id="camp-1",
        campaign_manifest_hash="sha256:" + "1" * 64,
        attempt_id=None,
        event_type=DiscoveryEventType.CAMPAIGN_PREPARED,
        sequence=0,
        observed_at=0,
        payload={},
    )
    defaults.update(overrides)
    return DiscoveryEvent(**defaults)


def make_resource_receipt(**overrides) -> DiscoveryResourceReceipt:
    defaults: dict = dict(
        receipt_id="res-1",
        campaign_id="camp-1",
        attempt_id="att-1",
        sequence=0,
        wall_seconds=1.5,
        evaluations_used=1,
        restarts_used=0,
    )
    defaults.update(overrides)
    return DiscoveryResourceReceipt(**defaults)


def make_terminal_receipt(**overrides) -> DiscoveryTerminalReceipt:
    defaults: dict = dict(
        receipt_id="term-1",
        campaign_id="camp-1",
        lineage_id="lin-1",
        attempt_id="att-1",
        sequence=0,
        outcome=DiscoveryTerminalOutcome.COMPLETED,
        resource_receipt_id="res-1",
        closest_protected_result="protected-score-ref-1",
        unresolved_gap="none",
    )
    defaults.update(overrides)
    return DiscoveryTerminalReceipt(**defaults)


def make_lineage_receipt(**overrides) -> DiscoveryLineageReceipt:
    defaults: dict = dict(
        receipt_id="lin-rcpt-1",
        campaign_id="camp-1",
        lineage_id="lin-1",
        attempt_id="att-1",
        event_type=DiscoveryLineageEventType.CREATED,
        parent_lineage_id=None,
        parent_commit=BASELINE_COMMIT,
        tree_hash=BASELINE_TREE,
        commit_hash=None,
        branch_name="discovery/lin-1",
        worktree_path="/work/campaigns/c1/lin-1",
        sequence=0,
    )
    defaults.update(overrides)
    return DiscoveryLineageReceipt(**defaults)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_manifest_self_hashes_and_is_frozen():
    manifest = make_manifest()
    assert manifest.manifest_hash.startswith("sha256:")
    with pytest.raises(ValidationError):
        manifest.seed = 99


def test_assignment_event_and_receipts_self_hash():
    assert make_assignment().assignment_hash.startswith("sha256:")
    assert make_event().event_hash.startswith("sha256:")
    assert make_resource_receipt().receipt_hash.startswith("sha256:")
    assert make_terminal_receipt().receipt_hash.startswith("sha256:")
    assert make_lineage_receipt().receipt_hash.startswith("sha256:")


def test_terminal_receipt_omission_path():
    receipt = make_terminal_receipt(
        outcome=DiscoveryTerminalOutcome.OMITTED,
        closest_protected_result=None,
        unresolved_gap=None,
        omission_reason="worker crashed before evidence capture",
    )
    assert receipt.outcome is DiscoveryTerminalOutcome.OMITTED


def test_lineage_child_commit_receipt():
    receipt = make_lineage_receipt(
        event_type=DiscoveryLineageEventType.CHILD_COMMITTED,
        parent_lineage_id="lin-1",
        parent_commit=BASELINE_COMMIT,
        commit_hash="c" * 40,
        lineage_id="lin-2",
        branch_name="discovery/lin-2",
        worktree_path="/work/campaigns/c1/lin-2",
        sequence=1,
    )
    assert receipt.commit_hash == "c" * 40


# ---------------------------------------------------------------------------
# Self-hash tamper rejection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "make, hash_field",
    [
        (make_manifest, "manifest_hash"),
        (make_assignment, "assignment_hash"),
        (make_event, "event_hash"),
        (make_resource_receipt, "receipt_hash"),
        (make_terminal_receipt, "receipt_hash"),
        (make_lineage_receipt, "receipt_hash"),
    ],
)
def test_tampered_hash_is_rejected(make, hash_field):
    with pytest.raises(ValidationError):
        make(**{hash_field: "sha256:" + "9" * 64})


# ---------------------------------------------------------------------------
# Extra-field rejection (frozen + extra="forbid")
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "make",
    [make_boundary, make_budgets, make_manifest, make_assignment, make_event, make_resource_receipt, make_terminal_receipt, make_lineage_receipt],
)
def test_extra_field_is_rejected(make):
    with pytest.raises(ValidationError):
        make(unexpected_field="attacker-controlled")


# ---------------------------------------------------------------------------
# Identity / version rejection
# ---------------------------------------------------------------------------


def test_manifest_rejects_proxy_evaluator_identity_conflation():
    with pytest.raises(ValidationError):
        make_manifest(proxy_evaluator_ref="e-1")  # same as versions.evaluator_version


def test_manifest_rejects_candidate_version_mismatch_with_baseline_commit():
    with pytest.raises(ValidationError):
        make_manifest(versions=make_versions(candidate_version="f" * 40))


def test_manifest_rejects_parent_candidate_version():
    with pytest.raises(ValidationError):
        make_manifest(versions=make_versions(parent_candidate_version="d" * 40))


def test_manifest_rejects_non_hex_baseline_commit():
    with pytest.raises(ValidationError):
        make_manifest(baseline_commit="not-a-sha")


# ---------------------------------------------------------------------------
# Budget / authority rejection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field",
    [
        "can_promote",
        "can_deploy",
        "can_write_evaluator",
        "can_activate_memory",
        "can_train_weights",
        "can_expand_permissions",
    ],
)
def test_boundary_rejects_any_authority_grant(field):
    with pytest.raises(ValidationError):
        make_boundary(**{field: True})


@pytest.mark.parametrize(
    "field",
    ["max_concurrency", "max_attempts", "max_evaluations", "max_wall_seconds", "attempt_timeout_seconds"],
)
def test_budgets_reject_non_positive_values(field):
    with pytest.raises(ValidationError):
        make_budgets(**{field: 0})


def test_budgets_reject_negative_restart_count():
    with pytest.raises(ValidationError):
        make_budgets(max_restarts_per_attempt=-1)


def test_manifest_rejects_disabling_unresolved_gap_reporting():
    with pytest.raises(ValidationError):
        make_manifest(report_unresolved_gap=False)


def test_manifest_requires_at_least_one_stop_condition():
    with pytest.raises(ValidationError):
        make_manifest(stop_conditions=[])


def test_optimizer_assignment_requires_parent_lineage():
    with pytest.raises(ValidationError):
        make_assignment(role=DiscoveryRole.OPTIMIZER, parent_lineage_id=None, parent_attempt_id=None)


def test_terminal_receipt_requires_gap_report_on_non_omitted_outcome():
    with pytest.raises(ValidationError):
        make_terminal_receipt(closest_protected_result=None)
    with pytest.raises(ValidationError):
        make_terminal_receipt(unresolved_gap=None)


def test_terminal_receipt_omission_forbids_result_and_gap():
    with pytest.raises(ValidationError):
        make_terminal_receipt(
            outcome=DiscoveryTerminalOutcome.OMITTED,
            omission_reason="lost",
        )


def test_lineage_receipt_created_requires_parent_commit():
    with pytest.raises(ValidationError):
        make_lineage_receipt(parent_commit=None)


def test_lineage_receipt_child_commit_requires_commit_hash():
    with pytest.raises(ValidationError):
        make_lineage_receipt(
            event_type=DiscoveryLineageEventType.CHILD_COMMITTED,
            parent_lineage_id="lin-1",
            parent_commit=BASELINE_COMMIT,
            commit_hash=None,
        )


def test_lineage_receipt_rejects_relative_worktree_path():
    with pytest.raises(ValidationError):
        make_lineage_receipt(worktree_path="relative/path")


def test_lineage_receipt_rejects_traversal_worktree_path():
    with pytest.raises(ValidationError):
        make_lineage_receipt(worktree_path="/work/campaigns/../../etc")


def test_boundary_rejects_traversal_workspace_root():
    with pytest.raises(ValidationError):
        make_boundary(workspace_root="/work/../etc")


def test_boundary_rejects_absolute_changed_path_prefix():
    with pytest.raises(ValidationError):
        make_boundary(allowed_changed_path_prefixes=["/etc"])


def test_boundary_rejects_traversal_changed_path_prefix():
    with pytest.raises(ValidationError):
        make_boundary(allowed_changed_path_prefixes=["../escape"])


def test_boundary_allowed_changed_path_prefixes_order_independent():
    boundary_a = make_boundary(allowed_changed_path_prefixes=["src/", "docs/"])
    boundary_b = make_boundary(allowed_changed_path_prefixes=["docs/", "src/"])
    assert boundary_a == boundary_b


# ---------------------------------------------------------------------------
# Canonical ordering / determinism
# ---------------------------------------------------------------------------


def test_manifest_hash_is_independent_of_portfolio_and_stop_condition_order():
    portfolio_a = [
        {"role": "explorer", "model_id": "m1", "model_version": "v1"},
        {"role": "optimizer", "model_id": "m2", "model_version": "v1"},
    ]
    portfolio_b = list(reversed(portfolio_a))
    stops_a = [DiscoveryStopCondition.WALL_TIME_EXCEEDED, DiscoveryStopCondition.GOAL_REACHED]
    stops_b = list(reversed(stops_a))

    manifest_a = make_manifest(model_portfolio=copy.deepcopy(portfolio_a), stop_conditions=stops_a)
    manifest_b = make_manifest(model_portfolio=copy.deepcopy(portfolio_b), stop_conditions=stops_b)

    assert manifest_a.manifest_hash == manifest_b.manifest_hash
    assert manifest_a.model_portfolio == manifest_b.model_portfolio
    assert manifest_a.stop_conditions == manifest_b.stop_conditions


def test_boundary_allowed_tools_order_independent():
    boundary_a = make_boundary(allowed_tools=["read", "grep", "bash"])
    boundary_b = make_boundary(allowed_tools=["bash", "read", "grep"])
    assert boundary_a == boundary_b
