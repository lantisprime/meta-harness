"""META-8: typed heartbeat checkpoints (reflection / consolidation / redirection).

Covers HeartbeatAction/HeartbeatOutcome frozen self-hashing, closed-enum and
protected-capture invariant rejection, cooldown gating, plateau/epsilon
trigger math, budget gating, deterministic outcome hashes, authority-shaped
extra-field rejection, tamper detection, and that reflection/consolidation
outcomes land as untrusted CANDIDATE artifacts in a real DiscoveryKnowledgeHub
(never activated, never widened in scope).
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from metaharness.context import ContextTrust, Sensitivity
from metaharness.discovery.heartbeat import (
    ConsolidationProposal,
    HeartbeatAction,
    HeartbeatEngine,
    HeartbeatKind,
    HeartbeatOutcome,
    HeartbeatTrigger,
    RedirectProposal,
    RedirectTarget,
    ReflectionProposal,
)
from metaharness.discovery.knowledge import (
    DiscoveryKnowledgeHub,
    DiscoveryKnowledgeKind,
    DiscoveryKnowledgeLifecycle,
    DiscoveryKnowledgeRequester,
    DiscoveryKnowledgeScope,
)
from metaharness.discovery.models import DiscoveryRole
from metaharness.discovery.population import (
    ApproachFingerprint,
    PopulationDescriptor,
)


def make_descriptor(**overrides) -> PopulationDescriptor:
    defaults: dict = dict(
        campaign_id="campaign-1",
        window_id="window-1",
        candidate_nodes=[
            ApproachFingerprint(
                candidate_id="candidate-1",
                lineage_id="lineage-1",
                approach_descriptor_tokens=["structural"],
                structure_signature="python:graph-a",
                score_tier="frontier",
            ),
        ],
        parent_edges=[("candidate-1", "baseline-candidate")],
        best_score=0.9,
        frontier_score=0.85,
        window_score_mean=0.7,
        window_score_variance=0.04,
        approach_diversity=0.6,
        behavioral_diversity=0.5,
        parent_selection_concentration=0.4,
        lineage_depth=1,
        lineage_width=1,
        score_tier_coverage=0.5,
        pareto_coverage=0.4,
        steps_since_meaningful_improvement=4,
        variation_operator_yield={"local": 0.5},
        cross_agent_transfer_count=0,
        memory_use_concentration=0.1,
        evaluator_failure_count=0,
        cost_so_far=1.0,
        latency_stats={"mean_seconds": 1.0},
        remaining_budget={"attempts": 9.0},
    )
    defaults.update(overrides)
    return PopulationDescriptor(**defaults)


def make_action(**overrides) -> HeartbeatAction:
    defaults: dict = dict(
        action_id="hb-1",
        kind=HeartbeatKind.REFLECTION,
        trigger=HeartbeatTrigger.PLATEAU,
        scope=DiscoveryKnowledgeScope.CAMPAIGN,
        improvement_epsilon=0.5,
        cooldown_sequences=1,
        context_template_id="reflect-brief-v1",
        resource_cost=0.0,
    )
    defaults.update(overrides)
    return HeartbeatAction(**defaults)


def make_hub(*, project_id: str = "project-1") -> DiscoveryKnowledgeHub:
    return DiscoveryKnowledgeHub(project_id=project_id)


def _read_artifact(hub: DiscoveryKnowledgeHub, artifact_id: str, campaign_id: str):
    requester = DiscoveryKnowledgeRequester(
        creator_id="heartbeat-engine",
        project_id=hub._project_id,
        campaign_id=campaign_id,
    )
    return hub.read(artifact_id, requester=requester)[0]


# ---------------------------------------------------------------------------
# Trigger math and gating
# ---------------------------------------------------------------------------


def test_heartbeat_plateau_trigger_math_respects_epsilon():
    # epsilon=0.5 -> one full expected improvement period elapses at 2 steps.
    not_yet = make_descriptor(steps_since_meaningful_improvement=1)
    engine = HeartbeatEngine(
        (make_action(improvement_epsilon=0.5, cooldown_sequences=1),),
        make_hub(),
    )
    assert engine.evaluate(not_yet, sequence=2, last_fired={}) == ()

    plateau = make_descriptor(steps_since_meaningful_improvement=2)
    outcomes = engine.evaluate(plateau, sequence=2, last_fired={})
    assert len(outcomes) == 1
    assert outcomes[0].reflection_note is not None

    # epsilon == 0 means no improvement is expected -> plateau is undefined.
    zero_engine = HeartbeatEngine(
        (make_action(improvement_epsilon=0.0, cooldown_sequences=1),),
        make_hub(),
    )
    assert zero_engine.evaluate(plateau, sequence=2, last_fired={}) == ()


def test_heartbeat_cooldown_blocks_refiring_until_elapsed():
    action = make_action(
        kind=HeartbeatKind.REFLECTION,
        trigger=HeartbeatTrigger.PLATEAU,
        improvement_epsilon=0.5,
        cooldown_sequences=3,
    )
    engine = HeartbeatEngine((action,), make_hub())
    descriptor = make_descriptor(steps_since_meaningful_improvement=4)

    first = engine.evaluate(descriptor, sequence=5, last_fired={})
    assert len(first) == 1, "first firing with no prior last_fired must fire"

    blocked = engine.evaluate(descriptor, sequence=6, last_fired={"hb-1": 5})
    assert blocked == (), "an action within its cooldown must not refire"

    elapsed = engine.evaluate(descriptor, sequence=8, last_fired={"hb-1": 5})
    assert len(elapsed) == 1, "an action past its cooldown must refire"
    assert elapsed[0].fired_sequence == 8


def test_heartbeat_budget_exceeded_action_does_not_fire():
    action = make_action(
        kind=HeartbeatKind.REFLECTION,
        trigger=HeartbeatTrigger.PLATEAU,
        improvement_epsilon=0.5,
        cooldown_sequences=1,
        resource_cost=5.0,
    )
    engine = HeartbeatEngine((action,), make_hub())
    descriptor = make_descriptor(
        steps_since_meaningful_improvement=4,
        remaining_budget={"attempts": 9.0, "cost": 1.0},
    )
    assert engine.evaluate(descriptor, sequence=5, last_fired={}) == ()

    # Under budget -> fires.
    cheap = make_action(
        kind=HeartbeatKind.REFLECTION,
        trigger=HeartbeatTrigger.PLATEAU,
        improvement_epsilon=0.5,
        cooldown_sequences=1,
        resource_cost=0.5,
    )
    cheap_engine = HeartbeatEngine((cheap,), make_hub())
    assert len(cheap_engine.evaluate(descriptor, sequence=5, last_fired={})) == 1


def test_heartbeat_time_trigger_uses_sequence_arithmetic():
    action = make_action(
        kind=HeartbeatKind.REFLECTION,
        trigger=HeartbeatTrigger.TIME,
        cooldown_sequences=5,
    )
    engine = HeartbeatEngine((action,), make_hub())
    descriptor = make_descriptor()

    assert engine.evaluate(descriptor, sequence=1, last_fired={}) == ()
    assert engine.evaluate(descriptor, sequence=4, last_fired={}) == ()
    fired = engine.evaluate(descriptor, sequence=5, last_fired={})
    assert len(fired) == 1
    assert fired[0].trigger_evidence == descriptor.descriptor_hash


def test_heartbeat_evaluation_and_event_triggers_require_explicit_evidence():
    eval_action = make_action(
        action_id="hb-eval",
        kind=HeartbeatKind.CONSOLIDATION,
        trigger=HeartbeatTrigger.EVALUATION,
        cooldown_sequences=1,
    )
    event_action = make_action(
        action_id="hb-event",
        kind=HeartbeatKind.REDIRECTION,
        trigger=HeartbeatTrigger.EVENT,
        cooldown_sequences=1,
    )
    engine = HeartbeatEngine((eval_action, event_action), make_hub())
    descriptor = make_descriptor()

    assert engine.evaluate(descriptor, sequence=3, last_fired={}) == ()

    with_evidence = engine.evaluate(
        descriptor,
        sequence=3,
        last_fired={},
        evaluation_evidence=("eval-result-7",),
        event_evidence=("event-9",),
    )
    assert {o.action_hash for o in with_evidence} == {
        eval_action.action_hash,
        event_action.action_hash,
    }
    eval_outcome = next(o for o in with_evidence if o.action_hash == eval_action.action_hash)
    event_outcome = next(o for o in with_evidence if o.action_hash == event_action.action_hash)
    assert eval_outcome.trigger_evidence == "eval-result-7"
    assert event_outcome.trigger_evidence == "event-9"
    assert event_outcome.redirect_proposal is not None
    assert event_outcome.redirect_proposal.target is RedirectTarget.SCHEDULER_ACTION


# ---------------------------------------------------------------------------
# Knowledge-hub integration
# ---------------------------------------------------------------------------


def test_heartbeat_reflection_lands_in_hub_as_untrusted_candidate_note():
    action = make_action(
        action_id="hb-note",
        kind=HeartbeatKind.REFLECTION,
        trigger=HeartbeatTrigger.PLATEAU,
        improvement_epsilon=0.5,
        cooldown_sequences=1,
    )
    hub = make_hub()
    engine = HeartbeatEngine((action,), hub)
    descriptor = make_descriptor(steps_since_meaningful_improvement=4)

    outcomes = engine.evaluate(descriptor, sequence=5, last_fired={})
    assert len(outcomes) == 1

    artifact = _read_artifact(hub, "hb-hb-note-seq5", descriptor.campaign_id)
    assert artifact.kind is DiscoveryKnowledgeKind.NOTE
    assert artifact.lifecycle is DiscoveryKnowledgeLifecycle.CANDIDATE
    assert artifact.trust is ContextTrust.UNTRUSTED_EVIDENCE
    assert artifact.scope is DiscoveryKnowledgeScope.PRIVATE
    assert artifact.creator_id == "heartbeat-engine"


def test_heartbeat_consolidation_lands_in_hub_as_untrusted_skill_candidate():
    action = make_action(
        action_id="hb-skill",
        kind=HeartbeatKind.CONSOLIDATION,
        trigger=HeartbeatTrigger.EVALUATION,
        cooldown_sequences=1,
    )
    hub = make_hub()
    engine = HeartbeatEngine((action,), hub)
    descriptor = make_descriptor()

    outcomes = engine.evaluate(
        descriptor,
        sequence=5,
        last_fired={},
        evaluation_evidence=("eval-result-7",),
    )
    assert len(outcomes) == 1
    proposal = outcomes[0].consolidation_proposal
    assert proposal is not None
    assert proposal.kind is DiscoveryKnowledgeKind.SKILL_CANDIDATE

    artifact = _read_artifact(hub, "hb-hb-skill-seq5", descriptor.campaign_id)
    assert artifact.kind is DiscoveryKnowledgeKind.SKILL_CANDIDATE
    assert artifact.lifecycle is DiscoveryKnowledgeLifecycle.CANDIDATE
    assert artifact.trust is ContextTrust.UNTRUSTED_EVIDENCE


def test_heartbeat_redirection_never_appends_and_is_only_a_proposal():
    action = make_action(
        action_id="hb-redirect",
        kind=HeartbeatKind.REDIRECTION,
        trigger=HeartbeatTrigger.PLATEAU,
        improvement_epsilon=0.5,
        cooldown_sequences=1,
    )
    hub = make_hub()
    engine = HeartbeatEngine((action,), hub)
    descriptor = make_descriptor(steps_since_meaningful_improvement=4)

    outcomes = engine.evaluate(descriptor, sequence=5, last_fired={})
    assert len(outcomes) == 1
    assert outcomes[0].redirect_proposal is not None
    # A redirect outcome is a queued proposal only: nothing is appended.
    requester = DiscoveryKnowledgeRequester(
        creator_id="heartbeat-engine",
        project_id=hub._project_id,
        campaign_id=descriptor.campaign_id,
    )
    summaries, _ = hub.query(requester=requester)
    assert summaries == ()


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_heartbeat_evaluate_is_deterministic_for_identical_inputs():
    action = make_action(
        action_id="hb-det",
        kind=HeartbeatKind.REFLECTION,
        trigger=HeartbeatTrigger.PLATEAU,
        improvement_epsilon=0.5,
        cooldown_sequences=1,
    )
    descriptor = make_descriptor(steps_since_meaningful_improvement=4)

    hub_a = make_hub()
    engine_a = HeartbeatEngine((action,), hub_a)
    first = engine_a.evaluate(descriptor, sequence=5, last_fired={})

    hub_b = make_hub()
    engine_b = HeartbeatEngine((action,), hub_b)
    second = engine_b.evaluate(descriptor, sequence=5, last_fired={})

    assert len(first) == len(second) == 1
    assert first[0].outcome_hash == second[0].outcome_hash
    assert first[0].model_dump_json() == second[0].model_dump_json()

    # The same engine re-evaluated at the same sequence yields identical
    # outcomes (the append side effect is idempotent, not part of the receipt).
    repeat = engine_a.evaluate(descriptor, sequence=5, last_fired={})
    assert repeat[0].outcome_hash == first[0].outcome_hash


# ---------------------------------------------------------------------------
# Boundary / authority rejection
# ---------------------------------------------------------------------------


def test_heartbeat_action_rejects_protected_capture_false():
    with pytest.raises(ValidationError):
        make_action(protected_capture_immutable=False)


def test_heartbeat_action_rejects_authority_shaped_extra_field():
    with pytest.raises(ValidationError):
        make_action(can_promote=True)
    with pytest.raises(ValidationError):
        make_action(can_deploy=True)


def test_heartbeat_action_rejects_invalid_scope_and_out_of_range_bounds():
    with pytest.raises(ValidationError):
        make_action(scope=DiscoveryKnowledgeScope.PRIVATE)
    with pytest.raises(ValidationError):
        make_action(scope=DiscoveryKnowledgeScope.ISLAND)
    with pytest.raises(ValidationError):
        make_action(improvement_epsilon=1.01)
    with pytest.raises(ValidationError):
        make_action(improvement_epsilon=-0.01)
    with pytest.raises(ValidationError):
        make_action(cooldown_sequences=0)
    with pytest.raises(ValidationError):
        make_action(resource_cost=-1.0)


def test_heartbeat_action_self_hash_rejects_tampering():
    action = make_action()
    tampered = action.model_dump(mode="json")
    tampered["action_hash"] = "sha256:" + "9" * 64
    with pytest.raises(ValidationError):
        HeartbeatAction(**tampered)


def test_heartbeat_outcome_self_hash_rejects_tampering():
    action = make_action()
    outcome = HeartbeatOutcome(
        action_hash=action.action_hash,
        fired_sequence=5,
        trigger_evidence="sha256:" + "0" * 64,
        reflection_note=ReflectionProposal(content="reflection note"),
    )
    assert outcome.outcome_hash.startswith("sha256:")
    tampered = outcome.model_dump(mode="json")
    tampered["outcome_hash"] = "sha256:" + "9" * 64
    with pytest.raises(ValidationError):
        HeartbeatOutcome(**tampered)


def test_heartbeat_outcome_requires_exactly_one_proposal():
    action = make_action()
    common = dict(
        action_hash=action.action_hash,
        fired_sequence=5,
        trigger_evidence="evidence-1",
    )
    note = ReflectionProposal(content="reflection note")
    consolidation = ConsolidationProposal(
        kind=DiscoveryKnowledgeKind.SYNTHESIS, content="synthesis note"
    )
    redirect = RedirectProposal(
        target=RedirectTarget.SCHEDULER_ACTION,
        target_id="sched-action-1",
        suggestion="reconsider scheduling",
    )

    # zero proposals rejected
    with pytest.raises(ValidationError):
        HeartbeatOutcome(**common)
    # two proposals rejected
    with pytest.raises(ValidationError):
        HeartbeatOutcome(
            **common, reflection_note=note, consolidation_proposal=consolidation
        )
    with pytest.raises(ValidationError):
        HeartbeatOutcome(**common, reflection_note=note, redirect_proposal=redirect)
    # exactly one accepted
    assert HeartbeatOutcome(**common, reflection_note=note).reflection_note == note
    assert (
        HeartbeatOutcome(**common, consolidation_proposal=consolidation).consolidation_proposal
        == consolidation
    )
    assert HeartbeatOutcome(**common, redirect_proposal=redirect).redirect_proposal == redirect


def test_heartbeat_consolidation_proposal_kind_is_restricted():
    with pytest.raises(ValidationError):
        ConsolidationProposal(
            kind=DiscoveryKnowledgeKind.EVALUATED_ATTEMPT, content="x"
        )
    with pytest.raises(ValidationError):
        ConsolidationProposal(
            kind=DiscoveryKnowledgeKind.CONNECTION, content="x"
        )
    for kind in (
        DiscoveryKnowledgeKind.NOTE,
        DiscoveryKnowledgeKind.SKILL_CANDIDATE,
        DiscoveryKnowledgeKind.SYNTHESIS,
    ):
        proposal = ConsolidationProposal(kind=kind, content="ok")
        assert proposal.kind is kind
