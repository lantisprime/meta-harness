"""Typed heartbeat checkpoints for the native discovery kernel (META-8).

A heartbeat is a policy-owned reflection, consolidation, or redirection
checkpoint that fires from a versioned event/evaluation/time/plateau trigger,
subject to a task-specific improvement epsilon, a per-action cooldown, and a
budget gate. This module defines the frozen ``HeartbeatAction`` policy
contract, the self-hashed ``HeartbeatOutcome`` receipt, and a deterministic
``HeartbeatEngine`` that evaluates a population descriptor and an optional
evidence bundle.

Authority boundary (charter invariants 7 and 9): every action declares
``protected_capture_immutable: Literal[True]`` so a ``False`` is a
structurally rejected construction (mirroring ``DiscoveryBoundary``'s
authority flags), and ``FrozenModel``'s ``extra='forbid'`` rejects any
authority-shaped extra (``can_promote``/``can_deploy``/...) at construction.
The engine never widens scope (reflection/consolidation appends go through
the *existing* ``DiscoveryKnowledgeHub.append()`` API as PRIVATE, untrusted,
candidate-lifecycle artifacts — the narrowest scope), never activates memory
(lifecycle stays ``CANDIDATE``), and never deletes (the hub is append-only).
Redirection is recorded as a queued *proposal* for the scheduler only; the
engine applies nothing.

MVP limitation (stated honestly): there is **no live worker interruption**.
Redirection is a queued proposal the scheduler may consume on a later decision;
``safe-interrupt`` checkpoints that preserve in-flight worker state and resume
with a receipt are a later card. The engine is deterministic in all inputs
(no wall clock, no randomness), so identical ``evaluate`` calls produce
byte-identical outcome hashes.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import Enum
from typing import Any, Callable, Literal

from pydantic import Field, ValidationError, model_validator

from metaharness.context import ContextTrust, Sensitivity
from metaharness.context.models import SHA256_PATTERN, FrozenModel
from metaharness.discovery.knowledge import (
    DiscoveryKnowledgeHub,
    DiscoveryKnowledgeKind,
    DiscoveryKnowledgeScope,
    KnowledgeError,
)
from metaharness.discovery.models import _self_verifying
from metaharness.discovery.policy import BoundedIdentifier
from metaharness.discovery.population import PopulationDescriptor


class HeartbeatError(ValueError):
    """A heartbeat evaluation was rejected — fail closed, never guessed around."""


class HeartbeatKind(str, Enum):
    """The closed set of heartbeat checkpoint kinds."""

    REFLECTION = "reflection"
    CONSOLIDATION = "consolidation"
    REDIRECTION = "redirection"


class HeartbeatTrigger(str, Enum):
    """The closed set of policy-owned heartbeat triggers."""

    EVENT = "event"
    EVALUATION = "evaluation"
    TIME = "time"
    PLATEAU = "plateau"


class RedirectTarget(str, Enum):
    """What a redirection proposal names — a scheduler action or a policy child."""

    SCHEDULER_ACTION = "scheduler_action"
    POLICY_CHILD = "policy_child"


# The consolidation kinds a heartbeat may propose to externalize (the
# untrusted-candidate kinds the knowledge hub accepts through append()).
_CONSOLIDATION_KINDS = frozenset(
    {
        DiscoveryKnowledgeKind.NOTE,
        DiscoveryKnowledgeKind.SKILL_CANDIDATE,
        DiscoveryKnowledgeKind.SYNTHESIS,
    }
)
# Scopes a heartbeat action may declare as its intended audience.
_ACTION_SCOPES = frozenset(
    {DiscoveryKnowledgeScope.LINEAGE, DiscoveryKnowledgeScope.CAMPAIGN}
)


class HeartbeatAction(FrozenModel):
    """Frozen, self-hashed policy contract for one heartbeat checkpoint.

    Carries no authority (``protected_capture_immutable`` exists only so that
    ``False`` is a rejected construction, like ``DiscoveryBoundary``'s flags;
    ``extra='forbid'`` rejects any authority-shaped extra such as
    ``can_promote``). The declared ``scope`` records the *intended* audience;
    the MVP engine appends reflection/consolidation outcomes PRIVATELY because
    it holds no verified worker assignment (the narrowest scope — it never
    widens).
    """

    schema_version: Literal[1] = 1
    action_id: str = Field(min_length=1)
    kind: HeartbeatKind
    trigger: HeartbeatTrigger
    scope: DiscoveryKnowledgeScope
    improvement_epsilon: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    cooldown_sequences: int = Field(gt=0)
    context_template_id: BoundedIdentifier
    resource_cost: float = Field(ge=0.0, allow_inf_nan=False)
    protected_capture_immutable: Literal[True] = True
    action_hash: str = Field(default="", pattern=SHA256_PATTERN)

    @model_validator(mode="wrap")
    @classmethod
    def _verify_hash(cls, data: Any, handler: Callable[[Any], Any]) -> "HeartbeatAction":
        return _self_verifying(data, handler, "action_hash", "action_hash mismatch")

    @model_validator(mode="after")
    def _validate_closed_enums_and_scope(self) -> "HeartbeatAction":
        # Explicit closed-enum/scope assertions (the typing already constrains
        # membership, but this makes a coerce-shaped failure a clear rejection
        # and pins the scope to LINEAGE/CAMPAIGN only).
        if not isinstance(self.kind, HeartbeatKind):
            raise ValueError(
                f"kind {self.kind!r} is not a member of the HeartbeatKind enum"
            )
        if not isinstance(self.trigger, HeartbeatTrigger):
            raise ValueError(
                f"trigger {self.trigger!r} is not a member of the HeartbeatTrigger enum"
            )
        if self.scope not in _ACTION_SCOPES:
            raise ValueError(
                "heartbeat scope must be LINEAGE or CAMPAIGN "
                f"(got {self.scope.value!r})"
            )
        return self


class ReflectionProposal(FrozenModel):
    """A NOTE candidate payload a reflection checkpoint proposes to externalize."""

    schema_version: Literal[1] = 1
    content: str = Field(min_length=1, max_length=4096)


class ConsolidationProposal(FrozenModel):
    """A consolidation checkpoint proposal (kind restricted to untrusted kinds)."""

    schema_version: Literal[1] = 1
    kind: DiscoveryKnowledgeKind
    content: str = Field(min_length=1, max_length=4096)

    @model_validator(mode="after")
    def _validate_kind(self) -> "ConsolidationProposal":
        if self.kind not in _CONSOLIDATION_KINDS:
            raise ValueError(
                "consolidation_proposal kind must be NOTE, SKILL_CANDIDATE, or "
                f"SYNTHESIS (got {self.kind.value!r})"
            )
        return self


class RedirectProposal(FrozenModel):
    """A queued scheduler/policy suggestion — a proposal only, never applied."""

    schema_version: Literal[1] = 1
    target: RedirectTarget
    target_id: str = Field(min_length=1, max_length=128)
    suggestion: str = Field(min_length=1, max_length=1024)


class HeartbeatOutcome(FrozenModel):
    """Frozen, self-hashed receipt for one fired heartbeat.

    Records the action (by hash) that fired, the sequence it fired at, the
    trigger evidence (a descriptor hash or event/evaluation id), and exactly
    one proposal: a reflection note, a consolidation proposal, or a redirect
    proposal. The outcome carries the *proposal* content; it never carries an
    applied change, an activation, or the hub's append receipt (the append is
    a side effect of ``HeartbeatEngine.evaluate``).
    """

    schema_version: Literal[1] = 1
    action_hash: str = Field(pattern=SHA256_PATTERN)
    fired_sequence: int = Field(ge=0)
    trigger_evidence: str = Field(min_length=1, max_length=256)
    reflection_note: ReflectionProposal | None = None
    consolidation_proposal: ConsolidationProposal | None = None
    redirect_proposal: RedirectProposal | None = None
    outcome_hash: str = Field(default="", pattern=SHA256_PATTERN)

    @model_validator(mode="wrap")
    @classmethod
    def _verify_hash(cls, data: Any, handler: Callable[[Any], Any]) -> "HeartbeatOutcome":
        return _self_verifying(data, handler, "outcome_hash", "outcome_hash mismatch")

    @model_validator(mode="after")
    def _validate_exactly_one_proposal(self) -> "HeartbeatOutcome":
        present = [
            self.reflection_note is not None,
            self.consolidation_proposal is not None,
            self.redirect_proposal is not None,
        ]
        if sum(present) != 1:
            raise ValueError(
                "a heartbeat outcome must carry exactly one of "
                "reflection_note, consolidation_proposal, or redirect_proposal"
            )
        return self


# ---------------------------------------------------------------------------
# HeartbeatEngine
# ---------------------------------------------------------------------------

# Consolidation output kind derived deterministically from the firing trigger:
# a plateau consolidates into a SYNTHESIS, an evaluation/event extracts a
# SKILL_CANDIDATE, and a periodic time checkpoint emits a NOTE.
_CONSOLIDATION_KIND_BY_TRIGGER: dict[HeartbeatTrigger, DiscoveryKnowledgeKind] = {
    HeartbeatTrigger.PLATEAU: DiscoveryKnowledgeKind.SYNTHESIS,
    HeartbeatTrigger.EVALUATION: DiscoveryKnowledgeKind.SKILL_CANDIDATE,
    HeartbeatTrigger.TIME: DiscoveryKnowledgeKind.NOTE,
    HeartbeatTrigger.EVENT: DiscoveryKnowledgeKind.SKILL_CANDIDATE,
}

_HEARTBEAT_CREATOR_ID = "heartbeat-engine"


class HeartbeatEngine:
    """Deterministic evaluator of policy-owned heartbeat actions.

    Constructed with a tuple of ``HeartbeatAction`` contracts, a real
    ``DiscoveryKnowledgeHub``, and the explicit ``project_id`` the engine is
    scoped to (F10 — the engine no longer reaches into the hub's private
    ``_project_id``; the caller asserts the binding). :meth:`evaluate` fires
    the actions whose trigger condition holds (subject to cooldown and a
    *cumulative* cost budget), appends reflection/consolidation outcomes to
    the hub as untrusted candidates through the existing ``append()`` API, and
    returns the self-hashed outcome receipts. No wall clock, no randomness.
    """

    def __init__(
        self,
        actions: Sequence[HeartbeatAction],
        hub: DiscoveryKnowledgeHub,
        *,
        project_id: str,
    ) -> None:
        # F9: round-trip re-validate each action from its own JSON dump so a
        # stale in-process action_hash (model_copy tampering) fails closed at
        # construction rather than embedding a stale binding downstream.
        validated: list[HeartbeatAction] = []
        for action in actions:
            try:
                validated.append(
                    HeartbeatAction.model_validate(action.model_dump(mode="json"))
                )
            except (ValidationError, ValueError) as exc:
                raise HeartbeatError(
                    f"heartbeat action {getattr(action, 'action_id', '?')!r} "
                    f"failed self-hash revalidation: {exc}"
                ) from exc
        self._actions: tuple[HeartbeatAction, ...] = tuple(validated)
        self._hub = hub
        if not project_id:
            raise HeartbeatError("project_id must be a non-empty string")
        self._project_id = project_id
        # F8: idempotent-append guard keyed by (campaign_id, action_id,
        # sequence). An exact same-key replay suppresses the append (the
        # outcome receipt is recomputed deterministically); any other append
        # rejection surfaces as HeartbeatError (fail-closed).
        self._appended: set[tuple[str, str, int]] = set()

    @property
    def actions(self) -> tuple[HeartbeatAction, ...]:
        return self._actions

    @property
    def hub(self) -> DiscoveryKnowledgeHub:
        return self._hub

    @property
    def project_id(self) -> str:
        return self._project_id

    def evaluate(
        self,
        descriptor: PopulationDescriptor,
        *,
        sequence: int,
        last_fired: Mapping[str, int],
        evaluation_evidence: Sequence[str] = (),
        event_evidence: Sequence[str] = (),
    ) -> tuple[HeartbeatOutcome, ...]:
        """Fire every action whose trigger holds under cooldown and budget.

        ``last_fired`` is caller-managed (the engine is stateless w.r.t. it):
        it maps ``action_id`` to the last sequence at which that action fired.
        ``evaluation_evidence`` / ``event_evidence`` are explicit evidence
        bundles required by the EVALUATION / EVENT triggers; absent (the
        default) those triggers never fire.
        """
        if sequence < 0:
            raise HeartbeatError("sequence must be non-negative")

        # F9: round-trip re-validate the descriptor from its own JSON dump so a
        # stale in-process descriptor_hash (model_copy tampering) fails closed
        # before any trigger math runs.
        try:
            descriptor = PopulationDescriptor.model_validate(
                descriptor.model_dump(mode="json")
            )
        except (ValidationError, ValueError) as exc:
            raise HeartbeatError(
                f"population descriptor failed self-hash revalidation: {exc}"
            ) from exc

        remaining = dict(descriptor.remaining_budget)
        # F10: the cost budget is read once here, then decremented cumulatively
        # per firing action (F7) so two 0.75-cost actions cannot both pass
        # against a 1.0 budget.
        if "cost" in remaining:
            remaining_cost_budget: float | None = float(remaining["cost"])
        else:
            remaining_cost_budget = None
        descriptor_hash = descriptor.descriptor_hash

        # F7: process actions in a deterministic order (by action_id) so the
        # cumulative cost budget is applied the same way regardless of
        # construction order, and a cost-contention tie has a deterministic
        # winner.
        ordered_actions = sorted(self._actions, key=lambda a: a.action_id)

        outcomes: list[HeartbeatOutcome] = []
        for action in ordered_actions:
            if not self._trigger_holds(
                action,
                descriptor,
                sequence,
                evaluation_evidence,
                event_evidence,
            ):
                continue
            # Cooldown gate: a prior firing within cooldown_sequences blocks
            # this one. last_fired is caller-managed and never mutated here.
            last = last_fired.get(action.action_id)
            if last is not None and (sequence - last) < action.cooldown_sequences:
                continue

            # F10: a costly action must be budget-checkable. If the descriptor
            # reports no "cost" entry at all, a resource_cost > 0 action cannot
            # be gated — fail closed (raise) rather than skipping the gate.
            if action.resource_cost > 0.0 and remaining_cost_budget is None:
                raise HeartbeatError(
                    f"action {action.action_id!r} has resource_cost "
                    f"{action.resource_cost} but the descriptor reports no "
                    "'cost' budget entry (fail closed)"
                )
            # F7: cumulative cost gate — the action's resource_cost must fit the
            # *remaining* (decremented) cost budget.
            if (
                remaining_cost_budget is not None
                and action.resource_cost > remaining_cost_budget + 1e-12
            ):
                continue

            trigger_evidence = self._trigger_evidence(
                action,
                descriptor_hash,
                evaluation_evidence,
                event_evidence,
            )
            outcome = self._build_outcome(
                action=action,
                descriptor=descriptor,
                sequence=sequence,
                trigger_evidence=trigger_evidence,
            )
            outcomes.append(outcome)

            # F7: decrement the cumulative cost budget by what this firing
            # actually cost.
            if remaining_cost_budget is not None:
                remaining_cost_budget -= action.resource_cost

            if action.kind in (
                HeartbeatKind.REFLECTION,
                HeartbeatKind.CONSOLIDATION,
            ):
                self._append_outcome_to_hub(action, descriptor, sequence, outcome)

        return tuple(outcomes)

    # -- trigger + proposal internals ---------------------------------------

    @staticmethod
    def _trigger_holds(
        action: HeartbeatAction,
        descriptor: PopulationDescriptor,
        sequence: int,
        evaluation_evidence: Sequence[str],
        event_evidence: Sequence[str],
    ) -> bool:
        trigger = action.trigger
        if trigger is HeartbeatTrigger.PLATEAU:
            # A plateau fires when the (deterministic) expected-improvement
            # budget elapses without a meaningful gain: steps_since_meaningful
            # _improvement multiplied by the per-task improvement epsilon
            # reaches one full expected improvement period. epsilon == 0 means
            # no improvement is expected, so a plateau is undefined (never fires).
            epsilon = action.improvement_epsilon
            if epsilon <= 0.0:
                return False
            return (
                descriptor.steps_since_meaningful_improvement * epsilon >= 1.0
            )
        if trigger is HeartbeatTrigger.TIME:
            # Sequence arithmetic: fire on multiples of the cooldown cadence.
            return (
                sequence > 0
                and action.cooldown_sequences > 0
                and (sequence % action.cooldown_sequences) == 0
            )
        if trigger is HeartbeatTrigger.EVALUATION:
            return len(evaluation_evidence) > 0
        if trigger is HeartbeatTrigger.EVENT:
            return len(event_evidence) > 0
        return False

    @staticmethod
    def _trigger_evidence(
        action: HeartbeatAction,
        descriptor_hash: str,
        evaluation_evidence: Sequence[str],
        event_evidence: Sequence[str],
    ) -> str:
        if action.trigger is HeartbeatTrigger.EVALUATION:
            return evaluation_evidence[0]
        if action.trigger is HeartbeatTrigger.EVENT:
            return event_evidence[0]
        # PLATEAU / TIME evidence the population descriptor that triggered it.
        return descriptor_hash

    def _build_outcome(
        self,
        *,
        action: HeartbeatAction,
        descriptor: PopulationDescriptor,
        sequence: int,
        trigger_evidence: str,
    ) -> HeartbeatOutcome:
        kind = action.kind
        if kind is HeartbeatKind.REFLECTION:
            reflection = ReflectionProposal(
                content=(
                    f"reflection@seq{sequence}:{action.context_template_id}:"
                    f"steps={descriptor.steps_since_meaningful_improvement}:"
                    f"approach_diversity={descriptor.approach_diversity:.4f}:"
                    f"descriptor={descriptor.descriptor_hash[:16]}"
                )
            )
            return HeartbeatOutcome(
                action_hash=action.action_hash,
                fired_sequence=sequence,
                trigger_evidence=trigger_evidence,
                reflection_note=reflection,
            )
        if kind is HeartbeatKind.CONSOLIDATION:
            consolidation_kind = _CONSOLIDATION_KIND_BY_TRIGGER[action.trigger]
            proposal = ConsolidationProposal(
                kind=consolidation_kind,
                content=(
                    f"consolidation@seq{sequence}:{action.context_template_id}:"
                    f"concentration={descriptor.parent_selection_concentration:.4f}:"
                    f"evaluator_failures={descriptor.evaluator_failure_count}:"
                    f"descriptor={descriptor.descriptor_hash[:16]}"
                ),
            )
            return HeartbeatOutcome(
                action_hash=action.action_hash,
                fired_sequence=sequence,
                trigger_evidence=trigger_evidence,
                consolidation_proposal=proposal,
            )
        # REDIRECTION: a proposal only, never an applied change (no append).
        redirect = RedirectProposal(
            target=RedirectTarget.SCHEDULER_ACTION,
            target_id=f"sched-action:{descriptor.descriptor_hash[:12]}",
            suggestion=(
                f"reconsider scheduling under {action.context_template_id} "
                f"(plateau_steps={descriptor.steps_since_meaningful_improvement})"
            ),
        )
        return HeartbeatOutcome(
            action_hash=action.action_hash,
            fired_sequence=sequence,
            trigger_evidence=trigger_evidence,
            redirect_proposal=redirect,
        )

    def _append_outcome_to_hub(
        self,
        action: HeartbeatAction,
        descriptor: PopulationDescriptor,
        sequence: int,
        outcome: HeartbeatOutcome,
    ) -> None:
        """Append a reflection/consolidation outcome as an untrusted candidate.

        Routes through the hub's existing ``append()`` API at PRIVATE scope
        (the narrowest scope — the engine holds no verified worker assignment
        to claim LINEAGE/CAMPAIGN-scoped writes), with UNTRUSTED_EVIDENCE
        trust and INTERNAL sensitivity. The hub forces lifecycle to CANDIDATE
        and append-only storage; the engine never widens scope, never
        activates, and never deletes.

        F8: the idempotency key is (campaign_id, action_id, sequence) and the
        artifact_id includes campaign_id, so the same action/sequence across
        two campaigns append as distinct artifacts. An exact same-key replay
        (already appended by this engine) suppresses the append; any other
        append rejection surfaces as HeartbeatError (fail-closed — never
        silently swallowed).
        """
        campaign_id = descriptor.campaign_id
        idempotency_key = (campaign_id, action.action_id, sequence)
        if idempotency_key in self._appended:
            # Exact same-key idempotent replay: the outcome is recomputed
            # deterministically; the append side effect is suppressed.
            return
        if outcome.reflection_note is not None:
            kind = DiscoveryKnowledgeKind.NOTE
            content = outcome.reflection_note.content
        elif outcome.consolidation_proposal is not None:
            kind = outcome.consolidation_proposal.kind
            content = outcome.consolidation_proposal.content
        else:
            # Redirection outcomes are never appended (proposal only).
            return
        artifact_id = f"hb-{campaign_id}-{action.action_id}-seq{sequence}"
        try:
            self._hub.append(
                artifact_id=artifact_id,
                kind=kind,
                project_id=self._project_id,
                campaign_id=campaign_id,
                creator_id=_HEARTBEAT_CREATOR_ID,
                content=content,
                scope=DiscoveryKnowledgeScope.PRIVATE,
                trust=ContextTrust.UNTRUSTED_EVIDENCE,
                sensitivity=Sensitivity.INTERNAL,
            )
        except KnowledgeError as exc:
            # F8: do NOT silently swallow. The only suppressed case is the
            # exact same-key idempotent replay (handled above). Any other
            # append rejection — a foreign artifact_id collision, a
            # sensitivity ceiling breach, secret-shaped content, ... — is a
            # real failure that must surface, not blend into a silent skip.
            raise HeartbeatError(
                f"heartbeat append for action {action.action_id!r} "
                f"(artifact_id {artifact_id!r}) was rejected: {exc}"
            ) from exc
        self._appended.add(idempotency_key)
