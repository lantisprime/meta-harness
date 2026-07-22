"""META-7: cross-module adversarial boundary sweep for the discovery kernel.

Per-module contract/adversarial coverage lives in tests/test_discovery_*.py.
This file exercises INTEGRATION boundaries that only show up when models,
lineage, knowledge, contexts, and the supervisor are wired together: bounded
authority end-to-end, honest (proxy-only) termination, cross-campaign/
lineage leakage across real components, and a tamper sweep over receipts
produced by an actual run rather than hand-built fixtures.
"""
from __future__ import annotations

import subprocess

import pytest
from pydantic import ValidationError

from metaharness.context import ContextScope, ContextSourceKind, ContextTrust, Sensitivity
from metaharness.context.models import ContextSourceRef
from metaharness.discovery.contexts import (
    ContextDecision,
    DiscoveryContextEntry,
    RoleContextError,
    RoleContextPolicy,
)
from metaharness.discovery.knowledge import (
    DiscoveryKnowledgeHub,
    DiscoveryKnowledgeKind,
    DiscoveryKnowledgeRequester,
    DiscoveryKnowledgeScope,
    KnowledgeError,
)
from metaharness.discovery.lineage import LineageError, LineageWorkspaceManager
from metaharness.discovery.models import (
    DiscoveryAssignment,
    DiscoveryBoundary,
    DiscoveryRole,
    DiscoveryTerminalOutcome,
)
from metaharness.discovery.supervisor import (
    CampaignSupervisor,
    DiscoveryJournal,
    EvaluationOutcome,
    ExecutionOutcome,
)
from tests.test_discovery_knowledge import FakeIssuer
from tests.test_discovery_models import make_budgets, make_manifest, make_versions

PROJECT = "meta-harness"


def _git(argv, cwd):
    return subprocess.run(["git", *argv], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture()
def pipeline(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _git(["init", "-q"], str(repo_root))
    _git(["config", "user.email", "test@example.com"], str(repo_root))
    _git(["config", "user.name", "Test"], str(repo_root))
    (repo_root / "README.md").write_text("baseline\n")
    _git(["add", "-A"], str(repo_root))
    _git(["commit", "-q", "-m", "baseline"], str(repo_root))
    baseline_commit = _git(["rev-parse", "HEAD"], str(repo_root))

    workspace_root = tmp_path / "workspace" / "camp1"
    workspace_root.mkdir(parents=True)

    manifest = make_manifest(
        campaign_id="camp1",
        project_id=PROJECT,
        baseline_commit=baseline_commit,
        baseline_tree=baseline_commit,
        versions=make_versions(candidate_version=baseline_commit),
        boundary=DiscoveryBoundary(workspace_root=str(workspace_root)),
    )
    lineage_manager = LineageWorkspaceManager(repo_root=str(repo_root), workspace_root=str(workspace_root))
    issuer = FakeIssuer()
    hub = DiscoveryKnowledgeHub(project_id=PROJECT, issuance_verifier=issuer.verify)
    hub.issuer = issuer
    return {
        "manifest": manifest,
        "baseline_commit": baseline_commit,
        "repo_root": str(repo_root),
        "workspace_root": str(workspace_root),
        "lineage": lineage_manager,
        "hub": hub,
    }


def make_assignment(**overrides) -> DiscoveryAssignment:
    defaults: dict = dict(
        assignment_id="asg-1",
        campaign_id="camp1",
        lineage_id="lin1",
        attempt_id="att-1",
        role=DiscoveryRole.EXPLORER,
        seed=1,
        sequence=0,
        created_at=0,
    )
    defaults.update(overrides)
    return DiscoveryAssignment(**defaults)


# ---------------------------------------------------------------------------
# Bounded authority — end to end
# ---------------------------------------------------------------------------


def test_manifest_boundary_never_grants_authority_even_when_wired_into_a_real_lineage_manager(pipeline):
    boundary = pipeline["manifest"].boundary
    assert boundary.can_promote is False
    assert boundary.can_deploy is False
    assert boundary.can_write_evaluator is False
    assert boundary.can_activate_memory is False
    assert boundary.can_train_weights is False
    assert boundary.can_expand_permissions is False
    # The lineage manager's actual workspace_root is the same directory the
    # manifest declared as its boundary — no separate, wider root sneaks in.
    lineage_receipt = pipeline["lineage"].create_lineage(
        campaign_id="camp1", lineage_id="lin1", baseline_commit=pipeline["baseline_commit"]
    )
    assert lineage_receipt.worktree_path.startswith(pipeline["workspace_root"])


def test_knowledge_artifact_can_never_declare_promotion_deploy_or_memory_activation():
    from metaharness.discovery.knowledge import (
        DiscoveryKnowledgeArtifact,
        DiscoveryKnowledgeKind,
        DiscoveryKnowledgeLifecycle,
        DiscoveryKnowledgeScope,
    )

    # The knowledge model's closed field set structurally has no authority
    # fields at all; the only way to smuggle one in is an `extra` field, which
    # frozen+extra=forbid already rejects.
    with pytest.raises(ValidationError):
        DiscoveryKnowledgeArtifact(
            artifact_id="note-1",
            kind=DiscoveryKnowledgeKind.NOTE,
            project_id=PROJECT,
            campaign_id="camp1",
            lineage_id="lin1",
            creator_id="worker:w1",
            content="observed regression",
            scope=DiscoveryKnowledgeScope.LINEAGE,
            trust=ContextTrust.UNTRUSTED_EVIDENCE,
            lifecycle=DiscoveryKnowledgeLifecycle.CANDIDATE,
            sensitivity=Sensitivity.INTERNAL,
            sequence=0,
            can_promote=True,
        )


# ---------------------------------------------------------------------------
# Honest termination — proxy-only MVP never implies protected approval
# ---------------------------------------------------------------------------


async def test_supervisor_terminal_receipt_never_silently_claims_protected_evaluation(tmp_path, pipeline):
    manifest = pipeline["manifest"]
    journal = DiscoveryJournal(str(tmp_path / "journal.jsonl"))

    async def executor(*, assignment, resume_from_checkpoint):
        return ExecutionOutcome(status="completed")

    async def evaluator(*, assignment):
        return EvaluationOutcome(status="completed", score_ref="proxy-score-9")

    supervisor = CampaignSupervisor(manifest=manifest, journal=journal, attempt_executor=executor, proxy_evaluator=evaluator)
    supervisor.prepare()
    supervisor.submit(make_assignment())
    await supervisor.run_available()

    receipt = supervisor.terminal_receipt("att-1")
    assert receipt.outcome is DiscoveryTerminalOutcome.COMPLETED
    assert receipt.closest_protected_result.startswith("proxy-only:")
    assert "protected evaluation is not run" in receipt.unresolved_gap


# ---------------------------------------------------------------------------
# Cross-campaign / cross-lineage leakage across real components
# ---------------------------------------------------------------------------


def test_knowledge_hub_rejects_a_real_lineage_receipt_from_a_foreign_campaign(pipeline):
    lineage_receipt = pipeline["lineage"].create_lineage(
        campaign_id="camp1", lineage_id="lin1", baseline_commit=pipeline["baseline_commit"]
    )
    checkpoint_receipt = pipeline["lineage"].checkpoint("lin1", attempt_id="att-1")
    assert checkpoint_receipt.campaign_id == "camp1"

    with pytest.raises(KnowledgeError):
        pipeline["hub"].append(
            artifact_id="note-1",
            kind=DiscoveryKnowledgeKind.NOTE,
            project_id=PROJECT,
            campaign_id="camp2",  # foreign campaign relative to the real receipt
            creator_id="worker:w1",
            content="observed regression",
            scope=DiscoveryKnowledgeScope.LINEAGE,
            trust=ContextTrust.UNTRUSTED_EVIDENCE,
            sensitivity=Sensitivity.INTERNAL,
            lineage_id="lin-other",
            source_evidence_receipts=[checkpoint_receipt],
        )
    del lineage_receipt


def test_role_context_rejects_a_real_hub_finding_from_a_sibling_lineage_without_receipt(pipeline):
    hub = pipeline["hub"]
    write_proof = hub.issuer.issue(campaign_id="camp1", lineage_id="lin1")
    artifact, _ = hub.append(
        artifact_id="synth-1",
        kind=DiscoveryKnowledgeKind.SYNTHESIS,
        project_id=PROJECT,
        campaign_id="camp1",
        creator_id="worker:w2",
        content="cross-lineage pattern observed",
        scope=DiscoveryKnowledgeScope.CAMPAIGN,
        trust=ContextTrust.GENERATED_SUMMARY,
        sensitivity=Sensitivity.INTERNAL,
        consumer_assignment=write_proof,
    )
    requester = DiscoveryKnowledgeRequester(creator_id="worker:w1", project_id=PROJECT, campaign_id="camp1", lineage_id="lin2")
    read_proof = hub.issuer.issue(campaign_id="camp1", lineage_id="lin2")
    summaries, _ = hub.query(requester=requester, consumer_assignment=read_proof)
    assert summaries[0].artifact_id == artifact.artifact_id

    finding_source = ContextSourceRef(
        source_id=artifact.artifact_id,
        kind=ContextSourceKind.POPULATION_FINDING,
        scope=ContextScope(project_id=PROJECT, lineage_id="lin2"),
        trust=artifact.trust,
        content_hash="sha256:" + "3" * 64,
        selection_reason="candidate novelty briefing item",
        sensitivity=artifact.sensitivity,
        fetchable=False,
    )
    entry = DiscoveryContextEntry(source=finding_source, decision=ContextDecision.INCLUDED, reason="novelty briefing")
    policy = RoleContextPolicy()
    with pytest.raises(ValidationError):
        policy.compose_explorer_context(project_id=PROJECT, campaign_id="camp1", lineage_id="lin1", entries=[entry])


# ---------------------------------------------------------------------------
# Tamper sweep over receipts produced by a real end-to-end run
# ---------------------------------------------------------------------------


def test_tamper_sweep_over_real_pipeline_receipts(pipeline):
    lineage = pipeline["lineage"]
    hub = pipeline["hub"]

    lineage_receipt = lineage.create_lineage(campaign_id="camp1", lineage_id="lin1", baseline_commit=pipeline["baseline_commit"])
    artifact, append_receipt = hub.append(
        artifact_id="note-1",
        kind=DiscoveryKnowledgeKind.NOTE,
        project_id=PROJECT,
        campaign_id="camp1",
        creator_id="worker:w1",
        content="observation",
        scope=DiscoveryKnowledgeScope.LINEAGE,
        trust=ContextTrust.UNTRUSTED_EVIDENCE,
        sensitivity=Sensitivity.INTERNAL,
        lineage_id="lin1",
        consumer_assignment=hub.issuer.issue(campaign_id="camp1", lineage_id="lin1"),
    )

    from metaharness.discovery.knowledge import DiscoveryKnowledgeAppendReceipt, DiscoveryKnowledgeArtifact
    from metaharness.discovery.models import DiscoveryLineageReceipt

    for model_cls, instance, hash_field in [
        (DiscoveryLineageReceipt, lineage_receipt, "receipt_hash"),
        (DiscoveryKnowledgeArtifact, artifact, "artifact_hash"),
        (DiscoveryKnowledgeAppendReceipt, append_receipt, "receipt_hash"),
    ]:
        tampered = instance.model_dump()
        tampered[hash_field] = "sha256:" + "9" * 64
        with pytest.raises(ValidationError):
            model_cls.model_validate(tampered)


# ---------------------------------------------------------------------------
# Optimizer / scheduler denial holds against real hub content too
# ---------------------------------------------------------------------------


def test_optimizer_context_rejects_real_hub_population_finding(pipeline):
    hub = pipeline["hub"]
    artifact, _ = hub.append(
        artifact_id="synth-2",
        kind=DiscoveryKnowledgeKind.SYNTHESIS,
        project_id=PROJECT,
        campaign_id="camp1",
        creator_id="worker:w2",
        content="ambient campaign pattern",
        scope=DiscoveryKnowledgeScope.CAMPAIGN,
        trust=ContextTrust.GENERATED_SUMMARY,
        sensitivity=Sensitivity.INTERNAL,
        consumer_assignment=hub.issuer.issue(campaign_id="camp1", lineage_id="lin1"),
    )
    finding_source = ContextSourceRef(
        source_id=artifact.artifact_id,
        kind=ContextSourceKind.POPULATION_FINDING,
        scope=ContextScope(project_id=PROJECT),
        trust=artifact.trust,
        content_hash="sha256:" + "4" * 64,
        selection_reason="attempted ambient inclusion",
        sensitivity=artifact.sensitivity,
        fetchable=False,
    )
    entry = DiscoveryContextEntry(source=finding_source, decision=ContextDecision.INCLUDED, reason="attempted")
    policy = RoleContextPolicy()
    with pytest.raises(RoleContextError):
        policy.compose_optimizer_context(
            project_id=PROJECT, campaign_id="camp1", lineage_id="lin1", parent_lineage_id="lin0", entries=[entry]
        )


def test_worker_authored_note_can_never_reach_reviewed_project_even_via_promotion_attempt(pipeline):
    hub = pipeline["hub"]
    lineage = pipeline["lineage"]
    lineage.create_lineage(campaign_id="camp1", lineage_id="lin1", baseline_commit=pipeline["baseline_commit"])
    checkpoint = lineage.checkpoint("lin1", attempt_id="att-1")

    artifact, _ = hub.append(
        artifact_id="note-2",
        kind=DiscoveryKnowledgeKind.NOTE,
        project_id=PROJECT,
        campaign_id="camp1",
        creator_id="worker:w1",
        content="candidate observation",
        scope=DiscoveryKnowledgeScope.LINEAGE,
        trust=ContextTrust.UNTRUSTED_EVIDENCE,
        sensitivity=Sensitivity.INTERNAL,
        lineage_id="lin1",
        source_evidence_receipts=[checkpoint],
        consumer_assignment=hub.issuer.issue(campaign_id="camp1", lineage_id="lin1"),
    )
    with pytest.raises(KnowledgeError):
        hub.promote_to_reviewed_project(
            artifact_id=artifact.artifact_id,
            new_artifact_id="note-2-reviewed",
            review_receipt_id="review-1",
            promoter_id="reviewer:coordinator",
        )
