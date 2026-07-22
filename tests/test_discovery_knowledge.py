"""META-7: DiscoveryKnowledgeHub happy path + adversarial coverage.

Covers append-only self-hashed artifacts, the five exact scopes, untrusted
agent-authored notes/skills, the reviewed-project review gate, orphan/foreign
source rejection, and deterministic query/use receipts.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from metaharness.context import ContextTrust, Sensitivity
from metaharness.discovery.knowledge import (
    DiscoveryKnowledgeArtifact,
    DiscoveryKnowledgeHub,
    DiscoveryKnowledgeKind,
    DiscoveryKnowledgeLifecycle,
    DiscoveryKnowledgeRequester,
    DiscoveryKnowledgeScope,
    KnowledgeAccessError,
    KnowledgeError,
)
from metaharness.discovery.models import DiscoveryAssignment, DiscoveryLineageReceipt, DiscoveryRole

PROJECT = "meta-harness"


class FakeIssuer:
    """Test double modeling issuance (META-7 pre-commit fix brief #9, F3): a
    `DiscoveryAssignment` verifies ONLY if THIS issuer minted it via
    `issue()` -- models "the supervisor is the only issuer" without needing
    a full CampaignSupervisor+journal in every knowledge test. A self-hashed
    assignment nobody ever `issue()`d never verifies, no matter how
    internally valid it looks."""

    def __init__(self) -> None:
        self._issued: dict[str, DiscoveryAssignment] = {}
        self._counter = 0

    def issue(self, **overrides) -> DiscoveryAssignment:
        self._counter += 1
        defaults: dict = dict(
            assignment_id=f"asg-issued-{self._counter}",
            campaign_id="camp1",
            lineage_id="lin1",
            attempt_id=f"att-issued-{self._counter}",
            role=DiscoveryRole.EXPLORER,
            seed=0,
            sequence=self._counter,
            created_at=0,
        )
        defaults.update(overrides)
        assignment = DiscoveryAssignment(**defaults)
        self._issued[assignment.attempt_id] = assignment
        return assignment

    def verify(self, candidate: DiscoveryAssignment) -> bool:
        issued = self._issued.get(candidate.attempt_id)
        return issued is not None and issued.assignment_hash == candidate.assignment_hash


def make_hub(**overrides) -> DiscoveryKnowledgeHub:
    """Wires a FRESH `FakeIssuer` by default (accessible as `hub.issuer`)
    unless the caller explicitly overrides `issuance_verifier` -- most
    existing tests need cross-scope reads/writes to keep working and should
    wire real issuance rather than have the checks weakened."""

    issuer = FakeIssuer()
    defaults: dict = dict(project_id=PROJECT, issuance_verifier=issuer.verify)
    defaults.update(overrides)
    hub = DiscoveryKnowledgeHub(**defaults)
    hub.issuer = issuer
    return hub


def make_requester(**overrides) -> DiscoveryKnowledgeRequester:
    defaults: dict = dict(
        creator_id="worker:w1",
        project_id=PROJECT,
        campaign_id="camp1",
        lineage_id="lin1",
        island_id=None,
    )
    defaults.update(overrides)
    return DiscoveryKnowledgeRequester(**defaults)


def make_consumer_assignment(**overrides) -> DiscoveryAssignment:
    """A real, self-hashed DiscoveryAssignment -- the only independently
    issued scope provenance this MVP has for LINEAGE-scope reads."""

    defaults: dict = dict(
        assignment_id="asg-consumer",
        campaign_id="camp1",
        lineage_id="lin1",
        attempt_id="att-consumer",
        role=DiscoveryRole.EXPLORER,
        seed=0,
        sequence=0,
        created_at=0,
    )
    defaults.update(overrides)
    return DiscoveryAssignment(**defaults)


def make_lineage_receipt(**overrides) -> DiscoveryLineageReceipt:
    defaults: dict = dict(
        receipt_id="lin-rcpt-1",
        campaign_id="camp1",
        lineage_id="lin1",
        attempt_id="att1",
        event_type="checkpointed",
        parent_lineage_id=None,
        parent_commit="a" * 40,
        tree_hash="b" * 40,
        commit_hash="c" * 40,
        branch_name="discovery/camp1/lin1",
        worktree_path="/work/camp1/lin1",
        sequence=0,
    )
    defaults.update(overrides)
    return DiscoveryLineageReceipt(**defaults)


def append_note(hub, **overrides):
    defaults: dict = dict(
        artifact_id="note-1",
        kind=DiscoveryKnowledgeKind.NOTE,
        project_id=PROJECT,
        campaign_id="camp1",
        creator_id="worker:w1",
        content="observed slower startup on candidate branch",
        scope=DiscoveryKnowledgeScope.LINEAGE,
        trust=ContextTrust.UNTRUSTED_EVIDENCE,
        sensitivity=Sensitivity.INTERNAL,
        lineage_id="lin1",
    )
    defaults.update(overrides)
    # LINEAGE/CAMPAIGN-scoped writes now require a verified-issued
    # assignment (META-7 pre-commit fix brief #9, F3). Auto-mint one from
    # this hub's FakeIssuer matching the write's own claimed campaign/
    # lineage, unless the caller explicitly supplied a consumer_assignment
    # (or an explicit issuance_verifier=None/other hub without an issuer,
    # in which case the caller is deliberately testing that path).
    if (
        defaults["scope"] in (DiscoveryKnowledgeScope.LINEAGE, DiscoveryKnowledgeScope.CAMPAIGN)
        and "consumer_assignment" not in overrides
    ):
        issuer = getattr(hub, "issuer", None)
        if issuer is not None:
            defaults["consumer_assignment"] = issuer.issue(
                campaign_id=defaults["campaign_id"], lineage_id=defaults.get("lineage_id") or "lin1"
            )
    return hub.append(**defaults)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_append_note_is_untrusted_candidate():
    hub = make_hub()
    artifact, receipt = append_note(hub)
    assert artifact.trust is ContextTrust.UNTRUSTED_EVIDENCE
    assert artifact.lifecycle is DiscoveryKnowledgeLifecycle.CANDIDATE
    assert receipt.artifact_id == artifact.artifact_id
    assert artifact.artifact_hash.startswith("sha256:")


def test_append_is_append_only_duplicate_id_rejected():
    hub = make_hub()
    append_note(hub)
    with pytest.raises(KnowledgeError):
        append_note(hub)


def test_supersede_creates_new_artifact_without_mutating_original():
    hub = make_hub()
    original, _ = append_note(hub)
    superseding, _ = append_note(hub, artifact_id="note-2", supersedes=["note-1"], content="revised observation")
    assert superseding.supersedes == ("note-1",)
    assert original.content == "observed slower startup on candidate branch"
    with pytest.raises(ValidationError):
        original.content = "tampered"


def test_append_does_not_publish_artifact_when_receipt_construction_fails():
    """META-7 pre-commit fix brief #8, P1-4: knowledge append must not
    publish query-visible state before the injected clock/id_source-backed
    receipt construction validates. If it did, this artifact_id would
    already exist in `self._artifacts` and a retry would be rejected as a
    duplicate (append-only) instead of succeeding."""

    calls = {"n": 0}

    def flaky_clock():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("clock is down")
        return 0

    hub = make_hub(clock=flaky_clock)
    with pytest.raises(RuntimeError):
        append_note(hub)
    artifact, receipt = append_note(hub)  # must succeed: nothing was published by the failed attempt
    assert artifact.artifact_id == "note-1"


def test_query_and_read_visibility_within_lineage():
    hub = make_hub()
    append_note(hub)
    requester = make_requester()
    # LINEAGE-scope visibility requires the requester to independently prove
    # lineage membership with a VERIFIED-ISSUED DiscoveryAssignment (META-7
    # pre-commit fix brief #9, F3) -- a bare requester.lineage_id claim, or
    # even a merely self-hashed (never-issued) assignment, is not enough.
    proof = hub.issuer.issue(campaign_id="camp1", lineage_id="lin1")
    summaries, use_receipt = hub.query(requester=requester, consumer_assignment=proof)
    assert len(summaries) == 1
    assert summaries[0].artifact_id == "note-1"
    artifact, read_receipt = hub.read("note-1", requester=requester, consumer_assignment=proof)
    assert artifact.artifact_id == "note-1"
    assert read_receipt.artifact_ids == ("note-1",)


# ---------------------------------------------------------------------------
# Exact scope visibility boundaries
# ---------------------------------------------------------------------------


def test_private_scope_visible_only_to_same_creator():
    hub = make_hub()
    append_note(hub, scope=DiscoveryKnowledgeScope.PRIVATE, creator_id="worker:w1")
    same_creator = make_requester(creator_id="worker:w1")
    other_creator = make_requester(creator_id="worker:w2")
    summaries_same, _ = hub.query(requester=same_creator)
    summaries_other, _ = hub.query(requester=other_creator)
    assert len(summaries_same) == 1
    assert len(summaries_other) == 0
    with pytest.raises(KnowledgeAccessError):
        hub.read("note-1", requester=other_creator)


def test_lineage_scope_invisible_to_sibling_lineage():
    hub = make_hub()
    append_note(hub, scope=DiscoveryKnowledgeScope.LINEAGE, lineage_id="lin1")
    sibling = make_requester(lineage_id="lin2")
    sibling_proof = make_consumer_assignment(campaign_id="camp1", lineage_id="lin2")
    summaries, _ = hub.query(requester=sibling, consumer_assignment=sibling_proof)
    assert summaries == ()


def test_lineage_claim_without_independently_issued_assignment_proof_grants_no_visibility():
    """META-7 pre-commit fix brief #8, P1-2: a requester claiming a REAL
    lineage_id (one that genuinely has visible knowledge) gains nothing
    without independently issued proof -- a bare self-asserted string can
    never widen scope."""

    hub = make_hub()
    append_note(hub, scope=DiscoveryKnowledgeScope.LINEAGE, lineage_id="lin1")
    claimant = make_requester(creator_id="worker:attacker", lineage_id="lin1")
    summaries, _ = hub.query(requester=claimant)  # no consumer_assignment at all
    assert summaries == ()
    with pytest.raises(KnowledgeAccessError):
        hub.read("note-1", requester=claimant)

    # A genuine assignment for a DIFFERENT lineage doesn't help either.
    wrong_lineage_proof = make_consumer_assignment(campaign_id="camp1", lineage_id="lin-attacker-real-home")
    summaries, _ = hub.query(requester=claimant, consumer_assignment=wrong_lineage_proof)
    assert summaries == ()


def test_lineage_claim_with_tampered_assignment_proof_rejected():
    hub = make_hub()
    append_note(hub, scope=DiscoveryKnowledgeScope.LINEAGE, lineage_id="lin1")
    requester = make_requester()
    genuine = hub.issuer.issue(campaign_id="camp1", lineage_id="lin1")
    tampered = genuine.model_copy(update={"attempt_id": "att-tampered"})  # stale hash after the field mutation
    with pytest.raises(KnowledgeAccessError):
        hub.query(requester=requester, consumer_assignment=tampered)


def test_island_scope_is_unavailable_in_this_mvp_even_with_a_matching_island_id():
    """Islands/migration execution is explicitly out of scope for META-7 and
    there is no independent island-membership issuer -- ISLAND-scoped
    knowledge is never visible via ordinary query/read, regardless of a
    caller-claimed matching island_id (META-7 pre-commit fix brief #8, P1-2)."""

    hub = make_hub()
    append_note(
        hub,
        kind=DiscoveryKnowledgeKind.SYNTHESIS,
        scope=DiscoveryKnowledgeScope.ISLAND,
        island_id="isl-a",
        trust=ContextTrust.GENERATED_SUMMARY,
        lineage_id=None,
    )
    matching = make_requester(island_id="isl-a", lineage_id=None)
    other = make_requester(island_id="isl-b", lineage_id=None)
    assert len(hub.query(requester=matching)[0]) == 0  # exact match still invisible
    assert len(hub.query(requester=other)[0]) == 0


def test_campaign_scope_visible_campaign_wide():
    hub = make_hub()
    append_note(
        hub,
        kind=DiscoveryKnowledgeKind.SYNTHESIS,
        scope=DiscoveryKnowledgeScope.CAMPAIGN,
        trust=ContextTrust.GENERATED_SUMMARY,
        lineage_id=None,
    )
    requester = make_requester(lineage_id="some-other-lineage")
    # CAMPAIGN-scope reads now ALSO require a verified-issued assignment
    # matching the claimed campaign_id (META-7 pre-commit fix brief #9, F3,
    # residual B) -- lineage_id is irrelevant to campaign-wide visibility.
    proof = hub.issuer.issue(campaign_id="camp1", lineage_id="some-other-lineage")
    summaries, _ = hub.query(requester=requester, consumer_assignment=proof)
    assert len(summaries) == 1


def test_cross_project_query_sees_nothing():
    hub = make_hub()
    append_note(hub)
    foreign_project_requester = make_requester(project_id="other-project")
    summaries, _ = hub.query(requester=foreign_project_requester)
    assert summaries == ()


def test_cross_campaign_query_sees_nothing_except_reviewed_project():
    hub = make_hub()
    append_note(
        hub,
        kind=DiscoveryKnowledgeKind.SYNTHESIS,
        scope=DiscoveryKnowledgeScope.CAMPAIGN,
        trust=ContextTrust.GENERATED_SUMMARY,
        lineage_id=None,
    )
    other_campaign_requester = make_requester(campaign_id="camp2", lineage_id=None)
    summaries, _ = hub.query(requester=other_campaign_requester)
    assert summaries == ()


# ---------------------------------------------------------------------------
# Trust / lifecycle escalation denial
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("trust", [ContextTrust.INSTRUCTION, ContextTrust.VERIFIED_FACT])
def test_note_cannot_claim_instruction_or_verified_fact_trust(trust):
    hub = make_hub()
    with pytest.raises((KnowledgeError, ValidationError)):
        append_note(hub, trust=trust)


@pytest.mark.parametrize("trust", [ContextTrust.INSTRUCTION, ContextTrust.VERIFIED_FACT])
@pytest.mark.parametrize(
    "kind",
    [
        DiscoveryKnowledgeKind.EVALUATED_ATTEMPT,
        DiscoveryKnowledgeKind.SYNTHESIS,
        DiscoveryKnowledgeKind.CONNECTION,
        DiscoveryKnowledgeKind.CONTRADICTION,
        DiscoveryKnowledgeKind.OPEN_QUESTION,
    ],
)
def test_ordinary_append_never_self_grants_instruction_or_verified_fact_for_any_kind(kind, trust):
    """META-7 pre-commit fix brief #9: the restriction is not special-cased to
    NOTE/SKILL_CANDIDATE — no artifact kind may self-grant this trust through
    the ordinary worker append() path."""

    hub = make_hub()
    with pytest.raises(KnowledgeError):
        append_note(
            hub,
            kind=kind,
            trust=trust,
            scope=DiscoveryKnowledgeScope.CAMPAIGN,
            lineage_id=None,
        )


def test_skill_candidate_cannot_carry_active_lifecycle():
    with pytest.raises(ValidationError):
        DiscoveryKnowledgeArtifact(
            artifact_id="skill-1",
            kind=DiscoveryKnowledgeKind.SKILL_CANDIDATE,
            project_id=PROJECT,
            campaign_id="camp1",
            lineage_id="lin1",
            creator_id="worker:w1",
            content="candidate skill: retry with backoff",
            scope=DiscoveryKnowledgeScope.LINEAGE,
            trust=ContextTrust.UNTRUSTED_EVIDENCE,
            lifecycle=DiscoveryKnowledgeLifecycle.ACTIVE,
            sensitivity=Sensitivity.INTERNAL,
            sequence=0,
        )


def test_note_cannot_claim_reviewed_project_scope_via_append():
    hub = make_hub()
    with pytest.raises(KnowledgeError):
        append_note(hub, scope=DiscoveryKnowledgeScope.REVIEWED_PROJECT)


def test_any_scope_cannot_reach_reviewed_project_via_append_directly():
    hub = make_hub()
    with pytest.raises(KnowledgeError):
        append_note(
            hub,
            kind=DiscoveryKnowledgeKind.SYNTHESIS,
            trust=ContextTrust.GENERATED_SUMMARY,
            scope=DiscoveryKnowledgeScope.REVIEWED_PROJECT,
        )


# ---------------------------------------------------------------------------
# Reviewed-project review gate
# ---------------------------------------------------------------------------


def test_promote_to_reviewed_project_is_fail_closed_disabled():
    """META-7 pre-commit fix brief #9: META-7 explicitly excludes promotion.
    The prior implementation accepted an arbitrary review_receipt_id/promoter
    string with no verification — a fake verifier. Until a real external
    review broker seam exists, this path must unconditionally refuse rather
    than mint REVIEWED_PROJECT visibility on say-so."""

    hub = make_hub()
    synthesis, _ = append_note(
        hub,
        artifact_id="syn-1",
        kind=DiscoveryKnowledgeKind.SYNTHESIS,
        trust=ContextTrust.GENERATED_SUMMARY,
        scope=DiscoveryKnowledgeScope.CAMPAIGN,
        lineage_id=None,
    )
    with pytest.raises(KnowledgeError):
        hub.promote_to_reviewed_project(
            artifact_id="syn-1",
            new_artifact_id="syn-1-reviewed",
            review_receipt_id="review-rcpt-1",
            promoter_id="reviewer:coordinator",
        )
    # No artifact was actually created by the disabled call.
    other_campaign_requester = make_requester(campaign_id="camp2", lineage_id=None)
    summaries, _ = hub.query(requester=other_campaign_requester)
    assert summaries == ()


def test_note_or_skill_candidate_can_never_be_promoted_to_reviewed_project():
    hub = make_hub()
    append_note(hub)
    with pytest.raises(KnowledgeError):
        hub.promote_to_reviewed_project(
            artifact_id="note-1",
            new_artifact_id="note-1-reviewed",
            review_receipt_id="review-rcpt-1",
            promoter_id="reviewer:coordinator",
        )


def test_reviewed_project_artifact_construction_requires_review_receipt():
    with pytest.raises(ValidationError):
        DiscoveryKnowledgeArtifact(
            artifact_id="syn-1-reviewed",
            kind=DiscoveryKnowledgeKind.SYNTHESIS,
            project_id=PROJECT,
            campaign_id="camp1",
            creator_id="reviewer:coordinator",
            content="reviewed synthesis",
            scope=DiscoveryKnowledgeScope.REVIEWED_PROJECT,
            trust=ContextTrust.GENERATED_SUMMARY,
            lifecycle=DiscoveryKnowledgeLifecycle.CANDIDATE,
            sensitivity=Sensitivity.INTERNAL,
            review_receipt_id=None,
            sequence=0,
        )


# ---------------------------------------------------------------------------
# Orphan / foreign source denial
# ---------------------------------------------------------------------------


def test_supersedes_unknown_artifact_is_orphan_rejected():
    hub = make_hub()
    with pytest.raises(KnowledgeError):
        append_note(hub, supersedes=["ghost"])


def test_supersedes_foreign_campaign_artifact_rejected():
    hub = make_hub()
    append_note(hub, artifact_id="note-camp1", campaign_id="camp1", lineage_id="lin1")
    with pytest.raises(KnowledgeError):
        append_note(hub, artifact_id="note-camp2", campaign_id="camp2", lineage_id="linX", supersedes=["note-camp1"])


def test_source_evidence_receipt_from_foreign_campaign_rejected():
    hub = make_hub()
    foreign_receipt = make_lineage_receipt(campaign_id="other-camp")
    with pytest.raises(KnowledgeError):
        append_note(hub, source_evidence_receipts=[foreign_receipt])


def test_source_evidence_receipt_from_foreign_lineage_rejected_for_lineage_scope():
    hub = make_hub()
    foreign_lineage_receipt = make_lineage_receipt(campaign_id="camp1", lineage_id="lin-other")
    with pytest.raises(KnowledgeError):
        append_note(hub, scope=DiscoveryKnowledgeScope.LINEAGE, lineage_id="lin1", source_evidence_receipts=[foreign_lineage_receipt])


def test_source_evidence_receipt_matching_lineage_is_accepted():
    hub = make_hub()
    matching_receipt = make_lineage_receipt(campaign_id="camp1", lineage_id="lin1")
    artifact, _ = append_note(hub, scope=DiscoveryKnowledgeScope.LINEAGE, lineage_id="lin1", source_evidence_receipts=[matching_receipt])
    assert artifact.source_evidence_receipt_ids == (matching_receipt.receipt_id,)


# ---------------------------------------------------------------------------
# Sensitivity fail-closed
# ---------------------------------------------------------------------------


def test_secret_shaped_content_below_secret_sensitivity_rejected():
    hub = make_hub()
    with pytest.raises(KnowledgeError):
        append_note(hub, content="found an aws secret token in the config", sensitivity=Sensitivity.INTERNAL)


def test_policy_exceeding_sensitivity_rejected():
    hub = make_hub(max_sensitivity=Sensitivity.RESTRICTED)
    with pytest.raises(KnowledgeError):
        append_note(hub, content="totally ordinary observation", sensitivity=Sensitivity.SECRET)


def test_cross_project_append_rejected():
    hub = make_hub()
    with pytest.raises(KnowledgeError):
        append_note(hub, project_id="other-project")


# ---------------------------------------------------------------------------
# Deterministic query/use receipts
# ---------------------------------------------------------------------------


def test_identical_queries_produce_identical_stable_ids():
    hub = make_hub()
    append_note(hub)
    requester = make_requester()
    _, receipt_a = hub.query(requester=requester)
    _, receipt_b = hub.query(requester=requester)
    assert receipt_a.query_stable_id == receipt_b.query_stable_id
    assert receipt_a.receipt_hash != receipt_b.receipt_hash  # distinct receipts (sequence differs)


def test_use_receipts_self_hash_and_are_frozen():
    hub = make_hub()
    append_note(hub)
    _, receipt = hub.query(requester=make_requester())
    assert receipt.receipt_hash.startswith("sha256:")
    with pytest.raises(ValidationError):
        receipt.sequence = 99


def test_tampered_append_receipt_hash_rejected():
    from metaharness.discovery.knowledge import DiscoveryKnowledgeAppendReceipt

    hub = make_hub()
    _, receipt = append_note(hub)
    tampered = receipt.model_dump()
    tampered["receipt_hash"] = "sha256:" + "9" * 64
    with pytest.raises(ValidationError):
        DiscoveryKnowledgeAppendReceipt.model_validate(tampered)


# ---------------------------------------------------------------------------
# Pre-commit fix brief 2, item 9: typed/revalidated knowledge provenance
# ---------------------------------------------------------------------------


def test_structural_fabricated_receipt_like_object_rejected():
    """A bare object satisfying the STRUCTURAL _ReceiptLike shape (just
    .receipt_id/.campaign_id) but not an actual typed discovery receipt
    must be rejected, not duck-typed through."""

    class FakeReceipt:
        receipt_id = "fake-1"
        campaign_id = "camp1"
        lineage_id = "lin1"

    hub = make_hub()
    with pytest.raises(KnowledgeError):
        append_note(hub, source_evidence_receipts=[FakeReceipt()])


def test_stale_hash_evidence_receipt_rejected():
    """model_copy(update=...) bypasses validation, so the resulting object's
    receipt_hash is stale (reflects the pre-mutation content). append() must
    revalidate from JSON, not trust an already-constructed instance."""

    hub = make_hub()
    original = make_lineage_receipt()
    stale = original.model_copy(update={"branch_name": "discovery/camp1/tampered"})
    assert stale.receipt_hash == original.receipt_hash  # the stale ghost hash

    with pytest.raises(KnowledgeError):
        append_note(hub, source_evidence_receipts=[stale])


def test_resource_receipt_accepted_as_typed_evidence():
    from metaharness.discovery.models import DiscoveryResourceReceipt

    resource = DiscoveryResourceReceipt(
        receipt_id="res-1",
        campaign_id="camp1",
        attempt_id="att1",
        sequence=0,
        wall_seconds=1.0,
        evaluations_used=1,
        restarts_used=0,
    )
    hub = make_hub()
    artifact, _ = append_note(hub, source_evidence_receipts=[resource])
    assert artifact.source_evidence_receipt_ids == (resource.receipt_id,)


def test_terminal_receipt_accepted_as_typed_evidence():
    from metaharness.discovery.models import DiscoveryTerminalReceipt

    terminal = DiscoveryTerminalReceipt(
        receipt_id="term-1",
        campaign_id="camp1",
        lineage_id="lin1",
        attempt_id="att1",
        sequence=0,
        outcome="completed",
        resource_receipt_id="res-1",
        closest_protected_result="proxy-only:x",
        unresolved_gap="none",
    )
    hub = make_hub()
    artifact, _ = append_note(hub, source_evidence_receipts=[terminal])
    assert artifact.source_evidence_receipt_ids == (terminal.receipt_id,)


def test_supersede_requires_prior_visible_to_requester_from_new_write_scope():
    """A new write scoped to lineage A must not be able to supersede a prior
    artifact scoped LINEAGE to a DIFFERENT lineage B in the same campaign —
    the prior isn't visible to a requester built from the new write's own
    project/campaign/creator/lineage/island scope."""

    hub = make_hub()
    append_note(hub, artifact_id="note-lin-b", lineage_id="lin-b", creator_id="worker:w2")
    with pytest.raises(KnowledgeError):
        append_note(
            hub,
            artifact_id="note-lin-a-v2",
            lineage_id="lin-a",
            creator_id="worker:w1",
            supersedes=["note-lin-b"],
        )


def test_supersede_allowed_when_prior_visible_to_new_write_scope():
    hub = make_hub()
    append_note(hub, artifact_id="note-1", lineage_id="lin1", creator_id="worker:w1")
    superseding, _ = append_note(
        hub,
        artifact_id="note-2",
        lineage_id="lin1",
        creator_id="worker:w1",
        supersedes=["note-1"],
    )
    assert superseding.supersedes == ("note-1",)


def test_supersede_campaign_scoped_prior_visible_across_lineages():
    """A CAMPAIGN-scoped prior is visible campaign-wide, so a new write from
    a different lineage CAN legitimately supersede it."""

    hub = make_hub()
    prior, _ = append_note(
        hub,
        artifact_id="syn-1",
        kind=DiscoveryKnowledgeKind.SYNTHESIS,
        trust=ContextTrust.GENERATED_SUMMARY,
        scope=DiscoveryKnowledgeScope.CAMPAIGN,
        lineage_id=None,
        creator_id="worker:w1",
    )
    superseding, _ = append_note(
        hub,
        artifact_id="syn-2",
        kind=DiscoveryKnowledgeKind.SYNTHESIS,
        trust=ContextTrust.GENERATED_SUMMARY,
        scope=DiscoveryKnowledgeScope.CAMPAIGN,
        lineage_id=None,
        creator_id="worker:w2",
        supersedes=["syn-1"],
    )
    assert superseding.supersedes == ("syn-1",)


# ---------------------------------------------------------------------------
# META-7 pre-commit fix brief #9, F3: issuance, not just integrity
# ---------------------------------------------------------------------------


def test_lineage_scoped_append_without_issuance_rejected():
    """kimi#2: append() used to publish LINEAGE-scoped knowledge into any
    claimed lineage on a bare string, with no proof the writer actually
    belongs there. Now requires a verified-issued assignment."""

    hub = make_hub()
    with pytest.raises(KnowledgeError):
        hub.append(
            artifact_id="note-victim",
            kind=DiscoveryKnowledgeKind.NOTE,
            project_id=PROJECT,
            campaign_id="camp1",
            creator_id="worker:attacker",
            content="planted observation",
            scope=DiscoveryKnowledgeScope.LINEAGE,
            trust=ContextTrust.UNTRUSTED_EVIDENCE,
            sensitivity=Sensitivity.INTERNAL,
            lineage_id="lin-victim",
            # no consumer_assignment: a bare lineage_id claim is not proof
        )
    assert "note-victim" not in hub._artifacts


def test_campaign_scoped_write_with_arbitrary_campaign_id_rejected():
    """kimi#5: a CAMPAIGN-scoped write could claim an arbitrary campaign_id
    with no proof of membership in that campaign."""

    hub = make_hub()
    with pytest.raises(KnowledgeError):
        hub.append(
            artifact_id="synth-victim",
            kind=DiscoveryKnowledgeKind.SYNTHESIS,
            project_id=PROJECT,
            campaign_id="camp-arbitrary",
            creator_id="worker:attacker",
            content="planted campaign-wide claim",
            scope=DiscoveryKnowledgeScope.CAMPAIGN,
            trust=ContextTrust.GENERATED_SUMMARY,
            sensitivity=Sensitivity.INTERNAL,
        )


def test_lineage_scoped_write_with_wrong_issued_assignment_rejected():
    """A verified-issued assignment for a DIFFERENT lineage does not
    authorize writing into the claimed one."""

    hub = make_hub()
    wrong_lineage_proof = hub.issuer.issue(campaign_id="camp1", lineage_id="lin-attacker-home")
    with pytest.raises(KnowledgeError):
        hub.append(
            artifact_id="note-victim-2",
            kind=DiscoveryKnowledgeKind.NOTE,
            project_id=PROJECT,
            campaign_id="camp1",
            creator_id="worker:attacker",
            content="planted observation",
            scope=DiscoveryKnowledgeScope.LINEAGE,
            trust=ContextTrust.UNTRUSTED_EVIDENCE,
            sensitivity=Sensitivity.INTERNAL,
            lineage_id="lin-victim",
            consumer_assignment=wrong_lineage_proof,
        )


def test_no_verifier_configured_disables_cross_scope_writes_private_still_works():
    hub = DiscoveryKnowledgeHub(project_id=PROJECT)  # no issuance_verifier at all
    with pytest.raises(KnowledgeError):
        hub.append(
            artifact_id="note-x",
            kind=DiscoveryKnowledgeKind.NOTE,
            project_id=PROJECT,
            campaign_id="camp1",
            creator_id="worker:w1",
            content="x",
            scope=DiscoveryKnowledgeScope.LINEAGE,
            trust=ContextTrust.UNTRUSTED_EVIDENCE,
            sensitivity=Sensitivity.INTERNAL,
            lineage_id="lin1",
        )
    # PRIVATE-scope writes are unaffected by a missing verifier.
    artifact, _ = hub.append(
        artifact_id="note-private",
        kind=DiscoveryKnowledgeKind.NOTE,
        project_id=PROJECT,
        campaign_id="camp1",
        creator_id="worker:w1",
        content="private observation",
        scope=DiscoveryKnowledgeScope.PRIVATE,
        trust=ContextTrust.UNTRUSTED_EVIDENCE,
        sensitivity=Sensitivity.INTERNAL,
    )
    assert artifact.artifact_id == "note-private"


def test_campaign_scope_read_without_verified_assignment_sees_nothing_residual_b():
    """Residual B: CAMPAIGN-scope reads used to trust a bare
    requester.campaign_id claim with no proof at all."""

    hub = make_hub()
    append_note(
        hub,
        artifact_id="synth-campaign-wide",
        kind=DiscoveryKnowledgeKind.SYNTHESIS,
        scope=DiscoveryKnowledgeScope.CAMPAIGN,
        trust=ContextTrust.GENERATED_SUMMARY,
        lineage_id=None,
    )
    requester = make_requester()
    summaries, _ = hub.query(requester=requester)  # no consumer_assignment
    assert summaries == ()

    proof = hub.issuer.issue(campaign_id="camp1", lineage_id="lin1")
    summaries, _ = hub.query(requester=requester, consumer_assignment=proof)
    assert len(summaries) == 1
