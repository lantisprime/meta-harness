"""META-7: RoleContextPolicy happy path + adversarial coverage.

Explorer raw-conversation denial, optimizer sibling-lineage denial, scheduler
transcript/worktree denial, explicit inclusion/withholding/omission
accounting, and the exact one-to-one cross-lineage use-receipt binding
(role, consumer lineage, actual source lineage/source ID, project, campaign)
required by the META-7 pre-commit fix brief (#10).
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from metaharness.context import ContextScope, ContextSourceKind, ContextTrust, Sensitivity
from metaharness.context.models import ContextSourceRef
from metaharness.discovery.contexts import (
    ContextDecision,
    DiscoveryContextEntry,
    DiscoveryContextRole,
    RoleContextError,
    RoleContextPolicy,
)
from metaharness.discovery.models import DiscoveryAssignment, DiscoveryRole
from tests.test_discovery_knowledge import FakeIssuer

PROJECT = "meta-harness"
OTHER_PROJECT = "other-project"
CAMPAIGN = "camp1"
OTHER_CAMPAIGN = "camp2"
LINEAGE = "lin1"
PARENT_LINEAGE = "lin0"
SIBLING_LINEAGE = "lin2"


def make_source(**overrides) -> ContextSourceRef:
    defaults: dict = dict(
        source_id="goal-1",
        kind=ContextSourceKind.GOAL,
        scope=ContextScope(project_id=PROJECT, lineage_id=LINEAGE),
        trust=ContextTrust.INSTRUCTION,
        content_hash="sha256:" + "1" * 64,
        selection_reason="declared campaign goal",
        sensitivity=Sensitivity.INTERNAL,
        fetchable=False,
    )
    defaults.update(overrides)
    return ContextSourceRef(**defaults)


def make_entry(**overrides) -> DiscoveryContextEntry:
    defaults: dict = dict(source=make_source(), decision=ContextDecision.INCLUDED, reason="required goal context")
    defaults.update(overrides)
    return DiscoveryContextEntry(**defaults)


def make_policy() -> RoleContextPolicy:
    """Wires a FRESH FakeIssuer by default (accessible as `policy.issuer`) so
    `cross_lineage_receipt` (META-7 pre-commit fix brief #9, F3) keeps
    working -- most existing tests need it and should wire real issuance
    rather than have the check weakened."""

    issuer = FakeIssuer()
    policy = RoleContextPolicy(issuance_verifier=issuer.verify)
    policy.issuer = issuer
    return policy


def make_finding_source(**overrides) -> ContextSourceRef:
    defaults: dict = dict(
        source_id="finding-from-lin2",
        kind=ContextSourceKind.POPULATION_FINDING,
        scope=ContextScope(project_id=PROJECT, lineage_id=SIBLING_LINEAGE),
        trust=ContextTrust.GENERATED_SUMMARY,
        content_hash="sha256:" + "4" * 64,
        selection_reason="candidate novelty briefing item",
        sensitivity=Sensitivity.INTERNAL,
        fetchable=False,
    )
    defaults.update(overrides)
    return ContextSourceRef(**defaults)


_CONTEXT_ROLE_TO_ASSIGNMENT_ROLE = {
    DiscoveryContextRole.EXPLORER: DiscoveryRole.EXPLORER,
    DiscoveryContextRole.OPTIMIZER: DiscoveryRole.OPTIMIZER,
}


def make_receipt(policy: RoleContextPolicy, **overrides):
    """Builds a cross-lineage receipt through the fixed, provenance-backed
    `cross_lineage_receipt` API (META-7 pre-commit fix brief #8, P1-2) while
    keeping the SAME override surface the adversarial tests below already
    use: overriding `role`/`consumer_lineage_id`/`campaign_id` constructs a
    (possibly deliberately "wrong") `DiscoveryAssignment` to derive the
    receipt's consumer identity from; overriding `source_lineage_id`/
    `source_id` constructs the `ContextSourceRef` the receipt cites."""

    defaults: dict = dict(
        role=DiscoveryContextRole.EXPLORER,
        project_id=PROJECT,
        campaign_id=CAMPAIGN,
        consumer_lineage_id=LINEAGE,
        source_lineage_id=SIBLING_LINEAGE,
        source_id="finding-from-lin2",
        justification="surface a distinct explored region without prescribing an answer",
    )
    defaults.update(overrides)

    assignment_role = _CONTEXT_ROLE_TO_ASSIGNMENT_ROLE[defaults["role"]]
    assignment_kwargs: dict = dict(
        assignment_id="asg-cross",
        campaign_id=defaults["campaign_id"],
        lineage_id=defaults["consumer_lineage_id"],
        attempt_id="att-cross",
        role=assignment_role,
        seed=0,
        sequence=0,
        created_at=0,
    )
    if assignment_role is DiscoveryRole.OPTIMIZER:
        assignment_kwargs["parent_lineage_id"] = "lin-cross-parent"
        assignment_kwargs["parent_attempt_id"] = "att-cross-parent"
    # Issue (not merely construct) the assignment through the policy's own
    # FakeIssuer so it is ISSUANCE-VERIFIED, not just self-hashed (META-7
    # pre-commit fix brief #9, F3).
    assignment = policy.issuer.issue(**assignment_kwargs)

    source = make_finding_source(
        source_id=defaults["source_id"],
        scope=ContextScope(project_id=defaults["project_id"], lineage_id=defaults["source_lineage_id"]),
    )
    return policy.cross_lineage_receipt(
        consumer_assignment=assignment,
        project_id=defaults["project_id"],
        source=source,
        justification=defaults["justification"],
    )


# ---------------------------------------------------------------------------
# Explorer
# ---------------------------------------------------------------------------


def test_explorer_context_happy_path_with_withheld_and_omitted():
    policy = make_policy()
    goal_entry = make_entry()
    withheld_entry = make_entry(
        source=make_source(
            source_id="prior-conversation",
            kind=ContextSourceKind.WORKING_MEMORY,
            content_hash="sha256:" + "2" * 64,
        ),
        decision=ContextDecision.WITHHELD,
        reason="explorer starts fresh: no inherited conversation",
    )
    omitted_entry = make_entry(
        source=make_source(source_id="low-priority-note", content_hash="sha256:" + "3" * 64),
        decision=ContextDecision.OMITTED,
        reason="token budget exceeded",
    )
    manifest = policy.compose_explorer_context(
        project_id=PROJECT,
        campaign_id=CAMPAIGN,
        lineage_id=LINEAGE,
        entries=[goal_entry, withheld_entry, omitted_entry],
    )
    assert manifest.role is DiscoveryContextRole.EXPLORER
    assert manifest.project_id == PROJECT
    assert len(manifest.entries) == 3
    assert manifest.manifest_hash.startswith("sha256:")


def test_explorer_context_rejects_included_working_memory():
    policy = make_policy()
    entry = make_entry(
        source=make_source(kind=ContextSourceKind.WORKING_MEMORY, source_id="raw-convo"),
        decision=ContextDecision.INCLUDED,
        reason="attempted inherited conversation",
    )
    with pytest.raises(RoleContextError):
        policy.compose_explorer_context(project_id=PROJECT, campaign_id=CAMPAIGN, lineage_id=LINEAGE, entries=[entry])


def test_explorer_context_rejects_included_parent_lineage_history():
    policy = make_policy()
    entry = make_entry(
        source=make_source(kind=ContextSourceKind.PARENT_LINEAGE, source_id="full-history"),
        decision=ContextDecision.INCLUDED,
        reason="attempted full inherited history",
    )
    with pytest.raises(RoleContextError):
        policy.compose_explorer_context(project_id=PROJECT, campaign_id=CAMPAIGN, lineage_id=LINEAGE, entries=[entry])


def test_explorer_novelty_briefing_requires_cross_lineage_receipt():
    policy = make_policy()
    entry = make_entry(source=make_finding_source(), decision=ContextDecision.INCLUDED, reason="novelty briefing")
    with pytest.raises(ValidationError):
        policy.compose_explorer_context(project_id=PROJECT, campaign_id=CAMPAIGN, lineage_id=LINEAGE, entries=[entry])

    receipt = make_receipt(policy)
    manifest = policy.compose_explorer_context(
        project_id=PROJECT,
        campaign_id=CAMPAIGN,
        lineage_id=LINEAGE,
        entries=[entry],
        cross_lineage_receipts=[receipt],
    )
    assert manifest.cross_lineage_receipts[0].source_id == "finding-from-lin2"


def test_explorer_population_finding_cannot_claim_instruction_trust():
    policy = make_policy()
    finding_source = make_finding_source(source_id="finding-x", trust=ContextTrust.INSTRUCTION)
    entry = make_entry(source=finding_source, decision=ContextDecision.INCLUDED, reason="novelty briefing")
    receipt = make_receipt(policy, source_id="finding-x")
    with pytest.raises(ValidationError):
        policy.compose_explorer_context(
            project_id=PROJECT, campaign_id=CAMPAIGN, lineage_id=LINEAGE, entries=[entry], cross_lineage_receipts=[receipt]
        )


# ---------------------------------------------------------------------------
# Cross-lineage receipt provenance (pre-commit fix brief #8, P1-2): identity
# is derived from an independently issued DiscoveryAssignment/ContextSourceRef,
# never asserted as bare strings.
# ---------------------------------------------------------------------------


def test_cross_lineage_receipt_requires_independently_issued_assignment_not_bare_strings():
    """A caller can no longer mint a cross-lineage receipt by asserting bare
    consumer_lineage_id/source_lineage_id strings directly."""

    policy = make_policy()
    with pytest.raises(TypeError):
        policy.cross_lineage_receipt(
            role=DiscoveryContextRole.EXPLORER,
            project_id=PROJECT,
            campaign_id=CAMPAIGN,
            consumer_lineage_id=LINEAGE,
            source_lineage_id=SIBLING_LINEAGE,
            source_id="finding-from-lin2",
            justification="bare string claim",
        )


def test_cross_lineage_receipt_derives_identity_from_assignment_and_source():
    policy = make_policy()
    assignment = policy.issuer.issue(
        assignment_id="asg-1", campaign_id=CAMPAIGN, lineage_id=LINEAGE, attempt_id="att-1",
        role=DiscoveryRole.EXPLORER, seed=0, sequence=0, created_at=0,
    )
    source = make_finding_source()
    receipt = policy.cross_lineage_receipt(
        consumer_assignment=assignment, project_id=PROJECT, source=source, justification="j",
    )
    assert receipt.consumer_lineage_id == LINEAGE
    assert receipt.source_lineage_id == SIBLING_LINEAGE
    assert receipt.campaign_id == CAMPAIGN
    assert receipt.source_id == source.source_id


def test_cross_lineage_receipt_rejects_tampered_assignment():
    policy = make_policy()
    assignment = DiscoveryAssignment(
        assignment_id="asg-1", campaign_id=CAMPAIGN, lineage_id=LINEAGE, attempt_id="att-1",
        role=DiscoveryRole.EXPLORER, seed=0, sequence=0, created_at=0,
    )
    tampered = assignment.model_copy(update={"lineage_id": "lin-tampered"})  # stale hash after the mutation
    with pytest.raises(RoleContextError):
        policy.cross_lineage_receipt(
            consumer_assignment=tampered, project_id=PROJECT, source=make_finding_source(), justification="j",
        )


# ---------------------------------------------------------------------------
# META-7 pre-commit fix brief #9, F3: issuance, not just integrity
# ---------------------------------------------------------------------------


def test_cross_lineage_receipt_rejects_self_hashed_but_never_issued_assignment():
    """A self-hashed DiscoveryAssignment proves integrity, not issuance --
    anyone with model access can mint one. cross_lineage_receipt must
    reject it unless the policy's OWN issuance verifier confirms it."""

    policy = make_policy()
    forged = DiscoveryAssignment(
        assignment_id="asg-forged", campaign_id=CAMPAIGN, lineage_id=LINEAGE, attempt_id="att-forged",
        role=DiscoveryRole.EXPLORER, seed=0, sequence=0, created_at=0,
    )
    with pytest.raises(RoleContextError):
        policy.cross_lineage_receipt(
            consumer_assignment=forged, project_id=PROJECT, source=make_finding_source(), justification="j",
        )


def test_cross_lineage_receipt_unavailable_with_no_issuance_verifier_configured():
    policy = RoleContextPolicy()  # no issuance_verifier at all
    assignment = DiscoveryAssignment(
        assignment_id="asg-1", campaign_id=CAMPAIGN, lineage_id=LINEAGE, attempt_id="att-1",
        role=DiscoveryRole.EXPLORER, seed=0, sequence=0, created_at=0,
    )
    with pytest.raises(RoleContextError):
        policy.cross_lineage_receipt(
            consumer_assignment=assignment, project_id=PROJECT, source=make_finding_source(), justification="j",
        )


# ---------------------------------------------------------------------------
# Cross-lineage receipt exact one-to-one binding (pre-commit fix brief #10)
# ---------------------------------------------------------------------------


def test_cross_lineage_receipt_wrong_role_rejected():
    policy = make_policy()
    entry = make_entry(source=make_finding_source(), decision=ContextDecision.INCLUDED, reason="novelty briefing")
    receipt = make_receipt(policy, role=DiscoveryContextRole.OPTIMIZER)
    with pytest.raises(ValidationError, match="wrong role"):
        policy.compose_explorer_context(
            project_id=PROJECT, campaign_id=CAMPAIGN, lineage_id=LINEAGE, entries=[entry], cross_lineage_receipts=[receipt]
        )


def test_cross_lineage_receipt_wrong_consumer_lineage_rejected():
    policy = make_policy()
    entry = make_entry(source=make_finding_source(), decision=ContextDecision.INCLUDED, reason="novelty briefing")
    receipt = make_receipt(policy, consumer_lineage_id="lin-NOT-THE-CONSUMER")
    with pytest.raises(ValidationError, match="wrong consumer"):
        policy.compose_explorer_context(
            project_id=PROJECT, campaign_id=CAMPAIGN, lineage_id=LINEAGE, entries=[entry], cross_lineage_receipts=[receipt]
        )


def test_cross_lineage_receipt_wrong_source_lineage_rejected():
    policy = make_policy()
    entry = make_entry(source=make_finding_source(), decision=ContextDecision.INCLUDED, reason="novelty briefing")
    receipt = make_receipt(policy, source_lineage_id="lin-NOT-THE-ACTUAL-SOURCE")
    with pytest.raises(ValidationError, match="wrong source lineage"):
        policy.compose_explorer_context(
            project_id=PROJECT, campaign_id=CAMPAIGN, lineage_id=LINEAGE, entries=[entry], cross_lineage_receipts=[receipt]
        )


def test_cross_lineage_receipt_foreign_project_rejected():
    policy = make_policy()
    entry = make_entry(source=make_finding_source(), decision=ContextDecision.INCLUDED, reason="novelty briefing")
    receipt = make_receipt(policy, project_id=OTHER_PROJECT)
    with pytest.raises(ValidationError, match="foreign project"):
        policy.compose_explorer_context(
            project_id=PROJECT, campaign_id=CAMPAIGN, lineage_id=LINEAGE, entries=[entry], cross_lineage_receipts=[receipt]
        )


def test_cross_lineage_receipt_foreign_campaign_rejected():
    policy = make_policy()
    entry = make_entry(source=make_finding_source(), decision=ContextDecision.INCLUDED, reason="novelty briefing")
    receipt = make_receipt(policy, campaign_id=OTHER_CAMPAIGN)
    with pytest.raises(ValidationError, match="foreign campaign"):
        policy.compose_explorer_context(
            project_id=PROJECT, campaign_id=CAMPAIGN, lineage_id=LINEAGE, entries=[entry], cross_lineage_receipts=[receipt]
        )


def test_duplicate_cross_lineage_receipts_for_same_source_rejected():
    policy = make_policy()
    entry = make_entry(source=make_finding_source(), decision=ContextDecision.INCLUDED, reason="novelty briefing")
    receipt_a = make_receipt(policy)
    receipt_b = make_receipt(policy)
    with pytest.raises(ValidationError, match="duplicate"):
        policy.compose_explorer_context(
            project_id=PROJECT,
            campaign_id=CAMPAIGN,
            lineage_id=LINEAGE,
            entries=[entry],
            cross_lineage_receipts=[receipt_a, receipt_b],
        )


def test_orphan_surplus_cross_lineage_receipt_rejected():
    policy = make_policy()
    goal_entry = make_entry()  # no population finding at all in this manifest
    receipt = make_receipt(policy, source_id="finding-that-does-not-exist")
    with pytest.raises(ValidationError, match="orphan/surplus"):
        policy.compose_explorer_context(
            project_id=PROJECT,
            campaign_id=CAMPAIGN,
            lineage_id=LINEAGE,
            entries=[goal_entry],
            cross_lineage_receipts=[receipt],
        )


def test_surplus_receipt_for_withheld_finding_rejected():
    policy = make_policy()
    withheld_entry = make_entry(
        source=make_finding_source(), decision=ContextDecision.WITHHELD, reason="not needed this round"
    )
    receipt = make_receipt(policy)
    with pytest.raises(ValidationError, match="orphan/surplus"):
        policy.compose_explorer_context(
            project_id=PROJECT,
            campaign_id=CAMPAIGN,
            lineage_id=LINEAGE,
            entries=[withheld_entry],
            cross_lineage_receipts=[receipt],
        )


def test_surplus_receipt_for_local_finding_rejected():
    """A receipt naming a finding that IS included but is NOT foreign (same
    lineage as the consumer) is surplus — nothing crossed a lineage boundary."""

    policy = make_policy()
    local_finding = make_finding_source(source_id="local-finding", scope=ContextScope(project_id=PROJECT, lineage_id=LINEAGE))
    entry = make_entry(source=local_finding, decision=ContextDecision.INCLUDED, reason="local finding")
    receipt = make_receipt(policy, source_id="local-finding")  # source_lineage_id=SIBLING_LINEAGE (distinct from consumer)
    with pytest.raises(ValidationError, match="orphan/surplus"):
        policy.compose_explorer_context(
            project_id=PROJECT,
            campaign_id=CAMPAIGN,
            lineage_id=LINEAGE,
            entries=[entry],
            cross_lineage_receipts=[receipt],
        )


# ---------------------------------------------------------------------------
# Optimizer
# ---------------------------------------------------------------------------


def test_optimizer_context_happy_path_direct_parent_worktree_and_history():
    policy = make_policy()
    worktree_entry = make_entry(
        source=make_source(
            source_id="parent-worktree",
            kind=ContextSourceKind.CANDIDATE_WORKTREE,
            scope=ContextScope(project_id=PROJECT, lineage_id=PARENT_LINEAGE),
            content_hash="sha256:" + "6" * 64,
        ),
        decision=ContextDecision.INCLUDED,
        reason="direct parent worktree",
    )
    history_entry = make_entry(
        source=make_source(
            source_id="parent-failure-history",
            kind=ContextSourceKind.PARENT_LINEAGE,
            scope=ContextScope(project_id=PROJECT, lineage_id=PARENT_LINEAGE),
            trust=ContextTrust.VERIFIED_FACT,
            content_hash="sha256:" + "7" * 64,
        ),
        decision=ContextDecision.INCLUDED,
        reason="direct parent failure/history",
    )
    manifest = policy.compose_optimizer_context(
        project_id=PROJECT,
        campaign_id=CAMPAIGN,
        lineage_id=LINEAGE,
        parent_lineage_id=PARENT_LINEAGE,
        entries=[worktree_entry, history_entry],
    )
    assert manifest.role is DiscoveryContextRole.OPTIMIZER
    assert manifest.project_id == PROJECT
    assert manifest.cross_lineage_receipts == ()


def test_optimizer_context_rejects_sibling_lineage_worktree():
    policy = make_policy()
    entry = make_entry(
        source=make_source(
            source_id="sibling-worktree",
            kind=ContextSourceKind.CANDIDATE_WORKTREE,
            scope=ContextScope(project_id=PROJECT, lineage_id=SIBLING_LINEAGE),
            content_hash="sha256:" + "8" * 64,
        ),
        decision=ContextDecision.INCLUDED,
        reason="attempted sibling worktree",
    )
    with pytest.raises(RoleContextError):
        policy.compose_optimizer_context(
            project_id=PROJECT,
            campaign_id=CAMPAIGN,
            lineage_id=LINEAGE,
            parent_lineage_id=PARENT_LINEAGE,
            entries=[entry],
        )


def test_optimizer_context_rejects_population_finding():
    policy = make_policy()
    entry = make_entry(
        source=make_source(
            source_id="ambient-finding",
            kind=ContextSourceKind.POPULATION_FINDING,
            trust=ContextTrust.GENERATED_SUMMARY,
            content_hash="sha256:" + "9" * 64,
        ),
        decision=ContextDecision.INCLUDED,
        reason="attempted ambient campaign transcript",
    )
    with pytest.raises(RoleContextError):
        policy.compose_optimizer_context(
            project_id=PROJECT,
            campaign_id=CAMPAIGN,
            lineage_id=LINEAGE,
            parent_lineage_id=PARENT_LINEAGE,
            entries=[entry],
        )


def test_optimizer_context_rejects_included_working_memory():
    policy = make_policy()
    entry = make_entry(
        source=make_source(kind=ContextSourceKind.WORKING_MEMORY, source_id="raw-convo-2"),
        decision=ContextDecision.INCLUDED,
        reason="attempted raw conversation",
    )
    with pytest.raises(RoleContextError):
        policy.compose_optimizer_context(
            project_id=PROJECT,
            campaign_id=CAMPAIGN,
            lineage_id=LINEAGE,
            parent_lineage_id=PARENT_LINEAGE,
            entries=[entry],
        )


def test_optimizer_own_lineage_worktree_is_allowed():
    policy = make_policy()
    entry = make_entry(
        source=make_source(
            source_id="own-worktree",
            kind=ContextSourceKind.CANDIDATE_WORKTREE,
            scope=ContextScope(project_id=PROJECT, lineage_id=LINEAGE),
            content_hash="sha256:" + "a" * 64,
        ),
        decision=ContextDecision.INCLUDED,
        reason="serial refinement of own lineage",
    )
    manifest = policy.compose_optimizer_context(
        project_id=PROJECT,
        campaign_id=CAMPAIGN,
        lineage_id=LINEAGE,
        parent_lineage_id=PARENT_LINEAGE,
        entries=[entry],
    )
    assert len(manifest.entries) == 1


def test_optimizer_composer_has_no_cross_lineage_receipts_parameter():
    """Structural guarantee (not just a runtime check): optimizer cannot
    smuggle a cross-lineage receipt because the composer doesn't accept one."""

    policy = make_policy()
    entry = make_entry(source=make_finding_source(), decision=ContextDecision.WITHHELD, reason="n/a")
    with pytest.raises(TypeError):
        policy.compose_optimizer_context(
            project_id=PROJECT,
            campaign_id=CAMPAIGN,
            lineage_id=LINEAGE,
            parent_lineage_id=PARENT_LINEAGE,
            entries=[entry],
            cross_lineage_receipts=[make_receipt(policy)],
        )


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------


def test_scheduler_context_happy_path():
    policy = make_policy()
    finding_entry = make_entry(
        source=make_source(
            source_id="pop-summary",
            kind=ContextSourceKind.POPULATION_FINDING,
            trust=ContextTrust.GENERATED_SUMMARY,
            content_hash="sha256:" + "b" * 64,
        ),
        decision=ContextDecision.INCLUDED,
        reason="compact eligible population summary",
    )
    receipt_entry = make_entry(
        source=make_source(
            source_id="eval-receipt-1",
            kind=ContextSourceKind.EVALUATOR_RECEIPT,
            trust=ContextTrust.UNTRUSTED_EVIDENCE,
            content_hash="sha256:" + "c" * 64,
        ),
        decision=ContextDecision.INCLUDED,
        reason="evaluation receipt",
    )
    manifest = policy.compose_scheduler_context(
        project_id=PROJECT, campaign_id=CAMPAIGN, entries=[finding_entry, receipt_entry]
    )
    assert manifest.role is DiscoveryContextRole.SCHEDULER
    assert manifest.project_id == PROJECT
    assert manifest.lineage_id is None


def test_scheduler_context_rejects_raw_conversation():
    policy = make_policy()
    entry = make_entry(
        source=make_source(kind=ContextSourceKind.WORKING_MEMORY, source_id="raw-convo-3"),
        decision=ContextDecision.INCLUDED,
        reason="attempted raw conversation",
    )
    with pytest.raises(RoleContextError):
        policy.compose_scheduler_context(project_id=PROJECT, campaign_id=CAMPAIGN, entries=[entry])


def test_scheduler_context_rejects_worktree_bytes():
    policy = make_policy()
    entry = make_entry(
        source=make_source(
            source_id="worktree-bytes",
            kind=ContextSourceKind.CANDIDATE_WORKTREE,
            content_hash="sha256:" + "d" * 64,
        ),
        decision=ContextDecision.INCLUDED,
        reason="attempted raw worktree bytes",
    )
    with pytest.raises(RoleContextError):
        policy.compose_scheduler_context(project_id=PROJECT, campaign_id=CAMPAIGN, entries=[entry])


def test_scheduler_composer_has_no_cross_lineage_receipts_parameter():
    policy = make_policy()
    entry = make_entry(source=make_finding_source(), decision=ContextDecision.WITHHELD, reason="n/a")
    with pytest.raises(TypeError):
        policy.compose_scheduler_context(
            project_id=PROJECT, campaign_id=CAMPAIGN, entries=[entry], cross_lineage_receipts=[make_receipt(policy)]
        )


# ---------------------------------------------------------------------------
# Manifest-level invariants
# ---------------------------------------------------------------------------


def test_manifest_rejects_duplicate_source_id():
    policy = make_policy()
    entry_a = make_entry(source=make_source(source_id="dup", content_hash="sha256:" + "e" * 64))
    entry_b = make_entry(
        source=make_source(source_id="dup", content_hash="sha256:" + "e" * 64),
        decision=ContextDecision.WITHHELD,
        reason="second mention",
    )
    with pytest.raises(ValidationError):
        policy.compose_explorer_context(
            project_id=PROJECT, campaign_id=CAMPAIGN, lineage_id=LINEAGE, entries=[entry_a, entry_b]
        )


def test_manifest_is_frozen():
    policy = make_policy()
    manifest = policy.compose_explorer_context(
        project_id=PROJECT, campaign_id=CAMPAIGN, lineage_id=LINEAGE, entries=[make_entry()]
    )
    with pytest.raises(ValidationError):
        manifest.sequence = 42


# ---------------------------------------------------------------------------
# Pre-commit fix brief 2, item 7: reject EVERY entry whose scope.project_id
# differs from the manifest project, regardless of kind/decision/role/receipt
# ---------------------------------------------------------------------------


def test_withheld_entry_with_foreign_project_id_rejected():
    """Not just INCLUDED population findings — ANY entry, ANY decision,
    ANY kind must be bound to the manifest's own project."""

    policy = make_policy()
    withheld_entry = make_entry(
        source=make_source(source_id="foreign-withheld", scope=ContextScope(project_id=OTHER_PROJECT, lineage_id=LINEAGE)),
        decision=ContextDecision.WITHHELD,
        reason="considered but withheld",
    )
    with pytest.raises(ValidationError, match="foreign project"):
        policy.compose_explorer_context(
            project_id=PROJECT, campaign_id=CAMPAIGN, lineage_id=LINEAGE, entries=[withheld_entry]
        )


def test_omitted_entry_with_foreign_project_id_rejected():
    policy = make_policy()
    omitted_entry = make_entry(
        source=make_source(source_id="foreign-omitted", scope=ContextScope(project_id=OTHER_PROJECT, lineage_id=LINEAGE)),
        decision=ContextDecision.OMITTED,
        reason="token budget exceeded",
    )
    with pytest.raises(ValidationError, match="foreign project"):
        policy.compose_explorer_context(
            project_id=PROJECT, campaign_id=CAMPAIGN, lineage_id=LINEAGE, entries=[omitted_entry]
        )


def test_scheduler_entry_with_foreign_project_id_rejected():
    """Even a role/kind that never touches cross-lineage receipts (scheduler)
    must still bind every entry to its own project."""

    policy = make_policy()
    entry = make_entry(
        source=make_source(
            source_id="foreign-scheduler-finding",
            kind=ContextSourceKind.POPULATION_FINDING,
            trust=ContextTrust.GENERATED_SUMMARY,
            scope=ContextScope(project_id=OTHER_PROJECT),
        ),
        decision=ContextDecision.INCLUDED,
        reason="attempted",
    )
    with pytest.raises(ValidationError, match="foreign project"):
        policy.compose_scheduler_context(project_id=PROJECT, campaign_id=CAMPAIGN, entries=[entry])


def test_cross_lineage_receipted_finding_still_rejected_if_project_mismatched():
    """A population finding otherwise correctly receipted for cross-lineage
    use must STILL be rejected if its own scope.project_id doesn't match —
    the receipt cannot excuse a foreign-project entry."""

    policy = make_policy()
    foreign_finding = make_finding_source(scope=ContextScope(project_id=OTHER_PROJECT, lineage_id=SIBLING_LINEAGE))
    entry = make_entry(source=foreign_finding, decision=ContextDecision.INCLUDED, reason="novelty briefing")
    receipt = make_receipt(policy)
    with pytest.raises(ValidationError, match="foreign project"):
        policy.compose_explorer_context(
            project_id=PROJECT, campaign_id=CAMPAIGN, lineage_id=LINEAGE, entries=[entry], cross_lineage_receipts=[receipt]
        )
