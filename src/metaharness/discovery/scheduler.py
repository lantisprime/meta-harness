"""Deterministic population scheduler for the native discovery kernel (META-8).

``PopulationScheduler`` turns an immutable, already-validated
``SearchPolicySnapshot``, a campaign budget envelope, and an immutable
``PopulationDescriptor`` into a deterministic batch of ``ScheduledSpawn``
pairs: a fully self-hashed ``DiscoveryAssignment`` (ready for
``CampaignSupervisor.submit()``) plus a ``SearchDecisionReceipt`` that honestly
records why each spawn selected its parent, role, variation class, briefing,
budget, alternatives considered, and expected information gain.

The scheduler is a pure, policy-bounded planner. It carries **no wall clock
and no randomness**: the same ``(policy, descriptor, sequence)`` triple always
produces byte-identical assignments and receipts. It grants **no** promotion,
deployment, evaluator-write, memory-activation, weight-training, or
permission-expansion authority (``FrozenModel``'s ``extra='forbid'`` rejects
any authority-shaped extra the way ``DiscoveryBoundary`` does), and every
spawn either carries a verified parent lineage (optimizer) or none (explorer),
mirroring ``DiscoveryAssignment``'s own validation.

MVP limitations (stated honestly): the scheduler plans one bounded batch per
decision and does not perform live worker interruption, cross-island
migration, or analysis warm-start — those remain separate cards. Heartbeat
redirection is consumed here as a *queued proposal for a later decision*, not
an in-flight mutation of a running worker.
"""
from __future__ import annotations

import math
from typing import Literal

from pydantic import Field, ValidationError, model_validator

from metaharness.context.models import SHA256_PATTERN, FrozenModel
from metaharness.discovery.models import (
    DiscoveryAssignment,
    DiscoveryBudgets,
    DiscoveryRole,
    _self_verifying,
    _sort_submodels,
)
from metaharness.discovery.policy import (
    BoundedIdentifier,
    ParentSelector,
    SearchPolicySnapshot,
    VariationClass,
)
from metaharness.discovery.population import ApproachFingerprint, PopulationDescriptor


class SchedulerError(ValueError):
    """A scheduling decision was rejected — fail closed, never guessed around."""


# ---------------------------------------------------------------------------
# Receipt sub-models
# ---------------------------------------------------------------------------

# Deterministic per-candidate preference score under a selector, in [0.0, 1.0].
# ``1.0`` is the most preferred candidate the selector ranks; lower values are
# weaker alternatives. ``Baseline`` selector scores every candidate ``0.0``
# because the baseline (not a candidate node) is the chosen parent.
_MAX_RECEIPT_REASON_LEN = 1024


class CandidateAlternative(FrozenModel):
    """One candidate the selector considered for a spawn's parent choice."""

    schema_version: Literal[1] = 1
    candidate_id: str = Field(min_length=1)
    selector_score: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)


class BudgetAllocation(FrozenModel):
    """The bounded resource budget one spawn allocates (never over-budget)."""

    schema_version: Literal[1] = 1
    attempts: int = Field(ge=1)
    cost: float = Field(ge=0.0, allow_inf_nan=False)


class SearchDecisionReceipt(FrozenModel):
    """Frozen, self-hashed receipt for one scheduler decision.

    Records the campaign, decision sequence, the immutable population
    descriptor and policy it was computed against (by hash), the chosen parent
    lineage/candidate (or ``None`` for a fresh explorer), the role, variation
    class, briefing template, the width/depth/concurrency/budget allocated,
    the alternatives actually considered with their selector scores, the
    bounded expected information gain, a human-readable reason, and the
    ``assignment_hash`` of the paired ``DiscoveryAssignment`` (F4 binding). It
    carries no approval or authority field of any kind.
    """

    schema_version: Literal[1] = 1
    campaign_id: str = Field(min_length=1)
    sequence: int = Field(ge=0)
    descriptor_hash: str = Field(pattern=SHA256_PATTERN)
    policy_hash: str = Field(pattern=SHA256_PATTERN)
    # F4: binds the receipt to its paired DiscoveryAssignment so a receipt
    # cannot be re-paired onto a foreign assignment (foreign campaign, foreign
    # parent, foreign role, foreign sequence). Verified on ScheduledSpawn.
    assignment_hash: str = Field(pattern=SHA256_PATTERN)
    parent_lineage_id: str | None = Field(default=None, min_length=1)
    parent_candidate_id: str | None = Field(default=None, min_length=1)
    role: DiscoveryRole
    variation_class: VariationClass
    briefing_template_id: BoundedIdentifier
    width_allocated: int = Field(ge=1)
    depth_allocated: int = Field(ge=0)
    concurrency_allocated: int = Field(ge=1)
    budget_allocated: BudgetAllocation
    alternatives_considered: tuple[CandidateAlternative, ...]
    expected_information_gain: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    reason: str = Field(min_length=1, max_length=_MAX_RECEIPT_REASON_LEN)
    receipt_hash: str = Field(default="", pattern=SHA256_PATTERN)

    @model_validator(mode="wrap")
    @classmethod
    def _verify_hash(cls, data: object, handler) -> "SearchDecisionReceipt":
        return _self_verifying(data, handler, "receipt_hash", "receipt_hash mismatch")

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, data: object) -> object:
        if isinstance(data, dict) and "alternatives_considered" in data:
            data = {**data, "alternatives_considered": _sort_submodels(
                data["alternatives_considered"], ("candidate_id",)
            )}
        return data

    @model_validator(mode="after")
    def _validate_role_parent_consistency(self) -> "SearchDecisionReceipt":
        # Mirror DiscoveryAssignment's own validation: an optimizer must name
        # a parent lineage AND candidate; an explorer must name neither. This
        # makes the receipt itself reject the optimizer-needs-parent /
        # explorer-must-not-have-parent invariants the assignment enforces.
        if self.role is DiscoveryRole.OPTIMIZER and self.parent_lineage_id is None:
            raise ValueError("an optimizer receipt requires a parent_lineage_id")
        if self.role is DiscoveryRole.OPTIMIZER and self.parent_candidate_id is None:
            raise ValueError("an optimizer receipt requires a parent_candidate_id")
        if self.role is DiscoveryRole.EXPLORER and self.parent_lineage_id is not None:
            raise ValueError("an explorer receipt must not name a parent_lineage_id")
        if (self.parent_lineage_id is None) != (self.parent_candidate_id is None):
            raise ValueError(
                "parent_lineage_id and parent_candidate_id must be set together"
            )
        return self


class ScheduledSpawn(FrozenModel):
    """A frozen ``(assignment, receipt)`` pair for one scheduled candidate.

    ``assignment`` is a fully self-hashed ``DiscoveryAssignment`` ready for
    ``CampaignSupervisor.submit()``; ``receipt`` is the honest decision receipt
    that explains the assignment's parent/role/variation/budget/briefing. The
    after-validator (F4) binds them: the receipt's assignment_hash, role,
    parent lineage/candidate, campaign, and sequence must all match the
    paired assignment, and an explorer assignment may not carry a parent.
    """

    schema_version: Literal[1] = 1
    assignment: DiscoveryAssignment
    receipt: SearchDecisionReceipt

    @model_validator(mode="after")
    def _validate_receipt_assignment_binding(self) -> "ScheduledSpawn":
        a = self.assignment
        r = self.receipt
        if r.assignment_hash != a.assignment_hash:
            raise ValueError(
                "receipt.assignment_hash must match assignment.assignment_hash"
            )
        if r.role != a.role:
            raise ValueError("receipt.role must match assignment.role")
        if r.parent_lineage_id != a.parent_lineage_id:
            raise ValueError(
                "receipt.parent_lineage_id must match assignment.parent_lineage_id"
            )
        # F4 maps the receipt's parent candidate to the assignment's parent
        # attempt (the scheduler populates assignment.parent_attempt_id with
        # the chosen parent's candidate_id, mirroring the receipt's
        # parent_candidate_id).
        if r.parent_candidate_id != a.parent_attempt_id:
            raise ValueError(
                "receipt.parent_candidate_id must match assignment.parent_attempt_id"
            )
        if r.campaign_id != a.campaign_id:
            raise ValueError("receipt.campaign_id must match assignment.campaign_id")
        if r.sequence != a.sequence:
            raise ValueError("receipt.sequence must match assignment.sequence")
        if r.role is DiscoveryRole.EXPLORER and a.parent_lineage_id is not None:
            raise ValueError(
                "an explorer assignment must not carry a parent_lineage_id"
            )
        return self


# ---------------------------------------------------------------------------
# Selector helpers — deterministic, scored alternative emission.
#
# These helpers order and score candidates for the *emission* path. They are
# NOT a mirror of policy.py's _select_parent_ids: ELITE here tier-sorts (so the
# frontier-tier candidate ranks first), and distinct-lineage dedup is always by
# lineage_id regardless of selector. The policy.py SIMULATION stage is a coarse
# pre-projection over the descriptor's *historical* concentration; THIS
# emission path (with _enforce_emitted_diversity_floor below) is the
# authoritative diversity-floor enforcement on what the scheduler actually
# emits.
# ---------------------------------------------------------------------------

_TIER_PRIORITY: dict[str, float] = {
    "frontier": 1.0,
    "promising": 0.7,
    "baseline": 0.5,
    "stale": 0.3,
    "regression": 0.2,
}
_UNKNOWN_TIER_PRIORITY = 0.5


def _tier_priority(tier: str) -> float:
    return _TIER_PRIORITY.get(tier, _UNKNOWN_TIER_PRIORITY)


def _selector_sort_key(
    node: ApproachFingerprint, selector: ParentSelector
) -> tuple[object, ...]:
    """Deterministic ordering key per selector for *emission* ranking.

    ELITE tier-sorts (frontier first); DIVERSE orders by structure_signature;
    UNDEREXPLORED and UNCERTAIN order by lineage_id; SCORE_TIER by score_tier;
    PARETO (and any future closed-enum member) orders by candidate_id. All ties
    break by candidate_id. This is an emission-time ranking, not a mirror of
    policy.py's _select_parent_ids.
    """

    if selector is ParentSelector.ELITE:
        return (-_tier_priority(node.score_tier), node.candidate_id)
    if selector is ParentSelector.DIVERSE:
        return (node.structure_signature, node.candidate_id)
    if selector is ParentSelector.UNDEREXPLORED:
        return (node.lineage_id, node.candidate_id)
    if selector is ParentSelector.SCORE_TIER:
        return (node.score_tier, node.candidate_id)
    if selector is ParentSelector.UNCERTAIN:
        # Behavioral-diversity proxy: distinct lineages first (no per-node
        # behavioral score is carried on the fingerprint in this MVP).
        return (node.lineage_id, node.candidate_id)
    # PARETO (and any future closed-enum member) — deterministic by id.
    return (node.candidate_id,)


def _rank_candidates(
    nodes: tuple[ApproachFingerprint, ...], selector: ParentSelector
) -> list[tuple[ApproachFingerprint, float]]:
    """Return each candidate with a deterministic selector preference score.

    Scores lie in ``(0.0, 1.0]`` for a ranked selector (``1.0`` is the most
    preferred candidate, lower values weaker alternatives) and are exactly
    ``0.0`` for the ``BASELINE`` selector, where every candidate is bypassed in
    favour of the baseline. Ranking is order-independent (ties broken by
    candidate id) so identical inputs produce identical alternatives tuples.
    """

    if selector is ParentSelector.BASELINE:
        return [(node, 0.0) for node in nodes]
    ordered = sorted(nodes, key=lambda n: _selector_sort_key(n, selector))
    denominator = max(1, len(ordered))
    return [(node, 1.0 - index / denominator) for index, node in enumerate(ordered)]


def _pick_distinct_lineage_parents(
    nodes: tuple[ApproachFingerprint, ...],
    selector: ParentSelector,
    count: int,
) -> list[ApproachFingerprint]:
    """Pick ``count`` parents on distinct lineages, ordered by the selector.

    Distinct-lineage parents keep optimizer spawns from collapsing onto a
    single lineage within one decision. Returns as many as available (fewer
    than ``count`` when distinct lineages run out); the caller degrades the
    unsatisfied optimizer slots to fresh explorers rather than over-claiming a
    parent.
    """

    if count <= 0:
        return []
    ranked = _rank_candidates(nodes, selector)
    chosen: list[ApproachFingerprint] = []
    seen_lineages: set[str] = set()
    for node, _score in ranked:
        if node.lineage_id in seen_lineages:
            continue
        seen_lineages.add(node.lineage_id)
        chosen.append(node)
        if len(chosen) >= count:
            break
    return chosen


def _clamp_fraction(value: float) -> float:
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


# ---------------------------------------------------------------------------
# PopulationScheduler
# ---------------------------------------------------------------------------


class PopulationScheduler:
    """A deterministic, policy-bounded planner over an immutable population.

    Constructed with a validated ``SearchPolicySnapshot`` and the campaign's
    ``DiscoveryBudgets``. Its single public method
    :meth:`schedule` returns a deterministic tuple of ``ScheduledSpawn`` pairs
    for one decision sequence. No wall clock, no randomness, no authority.
    """

    def __init__(
        self, policy: SearchPolicySnapshot, *, campaign_budgets: DiscoveryBudgets
    ) -> None:
        # Revalidate the policy from its own JSON dump before trusting it: a
        # caller could hand a `model_copy(update=...)`-mutated snapshot whose
        # policy_hash is stale relative to its post-mutation fields. The
        # receipt binds the verified policy_hash, so a tampered policy must
        # fail closed here rather than embed a stale binding downstream.
        try:
            policy = SearchPolicySnapshot.model_validate(policy.model_dump(mode="json"))
        except (ValidationError, ValueError) as exc:
            raise SchedulerError(
                f"policy snapshot failed self-hash revalidation: {exc}"
            ) from exc
        try:
            campaign_budgets = DiscoveryBudgets.model_validate(
                campaign_budgets.model_dump(mode="json")
            )
        except (ValidationError, ValueError) as exc:
            raise SchedulerError(
                f"campaign budgets failed self-hash revalidation: {exc}"
            ) from exc
        self._policy = policy
        self._budgets = campaign_budgets

    @property
    def policy(self) -> SearchPolicySnapshot:
        return self._policy

    @property
    def campaign_budgets(self) -> DiscoveryBudgets:
        return self._budgets

    # -- public API ---------------------------------------------------------

    def schedule(
        self, descriptor: PopulationDescriptor, *, sequence: int,
        window_attempts_used: int
    ) -> tuple[ScheduledSpawn, ...]:
        """Plan one bounded decision batch; deterministic in all inputs.

        ``window_attempts_used`` is caller-managed exactly like the heartbeat
        ``last_fired`` mapping: the attempts already emitted under the current
        policy window (F-final-1). The per-window attempt cap
        (``dsl.stop_rules.max_attempts``) is enforced *cumulatively across
        decisions* via this parameter, not per-batch, so two consecutive
        schedule() calls cannot exceed the window cap together (e.g. 4+4 under
        max_attempts=4).
        """
        if sequence < 0:
            raise SchedulerError("sequence must be non-negative")
        if window_attempts_used < 0:
            raise SchedulerError("window_attempts_used must be non-negative")

        # F5: round-trip re-validate the descriptor from its own JSON dump so
        # a stale in-process hash (model_copy tampering) fails closed before
        # use. A self-hashed descriptor proves integrity only when the hash
        # matches the current field values; the receipt binds the verified
        # descriptor_hash, so a tampered descriptor must fail here.
        try:
            descriptor = PopulationDescriptor.model_validate(
                descriptor.model_dump(mode="json")
            )
        except (ValidationError, ValueError) as exc:
            raise SchedulerError(
                f"population descriptor failed self-hash revalidation: {exc}"
            ) from exc

        policy = self._policy
        if policy.campaign_id != descriptor.campaign_id:
            raise SchedulerError(
                "scheduler policy campaign_id does not match descriptor campaign_id"
            )

        dsl = policy.policy
        # F-final-1: the policy's own stop-rule attempt cap is enforced
        # cumulatively across the window. remaining_window is the budget left
        # for THIS and later decisions; fail closed when it is exhausted.
        remaining_window = dsl.stop_rules.max_attempts - window_attempts_used
        if remaining_window <= 0:
            raise SchedulerError(
                "window attempt budget is exhausted (max_attempts="
                f"{dsl.stop_rules.max_attempts}, window_attempts_used="
                f"{window_attempts_used}); cannot schedule without over-"
                "allocating the policy window"
            )
        remaining = dict(descriptor.remaining_budget)
        if "attempts" not in remaining:
            raise SchedulerError(
                "descriptor remaining_budget must report the attempts entry"
            )
        remaining_attempts = int(remaining["attempts"])
        if remaining_attempts <= 0:
            raise SchedulerError(
                "campaign attempt budget is exhausted; cannot schedule a spawn "
                "without over-allocating"
            )

        # width / concurrency respect DSL maxima, the campaign budget, the
        # policy's own per-window stop-rule attempt cap (F2 + F-final-1: capped
        # by remaining_window, not max_attempts alone), and the descriptor's
        # remaining attempts budget — never over-allocate.
        width = min(
            dsl.max_width,
            dsl.max_concurrency,
            self._budgets.max_concurrency,
            remaining_attempts,
            remaining_window,
        )
        if width < 1:
            raise SchedulerError("scheduler width is non-positive after budget caps")

        concurrency = min(dsl.max_concurrency, self._budgets.max_concurrency, width)

        # Diversity-floor enforcement (descriptor historical concentration):
        # when concentration exceeds 1 - diversity_floor, force non-elite
        # UNDEREXPLORED optimizer parents so the scheduler cannot silently
        # collapse onto the current leader. _enforce_emitted_diversity_floor
        # below additionally enforces the floor on the *emitted* batch (F3).
        maximum_concentration = 1.0 - dsl.diversity_floor
        concentrated = descriptor.parent_selection_concentration > (
            maximum_concentration + 1e-12
        )
        effective_selector = (
            ParentSelector.UNDEREXPLORED if concentrated else dsl.parent_selector
        )

        # Baseline reseeding: every baseline_reseed_interval decisions
        # (sequence % interval == 0) guarantees at least one fresh,
        # baseline-rooted explorer regardless of the role fractions.
        reseed_due = dsl.baseline_reseed_interval > 0 and (
            sequence % dsl.baseline_reseed_interval == 0
        )

        # Depth cap: a lineage cannot deepen past the DSL max_depth.
        can_deepen = descriptor.lineage_depth < dsl.max_depth

        nodes = descriptor.candidate_nodes

        # Role split over the full width (mirrors policy.py simulation math).
        explorer_count = int(math.floor(width * dsl.explorer_fraction + 0.5))
        if explorer_count > width:
            explorer_count = width
        optimizer_count = width - explorer_count

        # BASELINE parent selector: every spawn is baseline-rooted, so there is
        # no parent lineage to refine — all slots become explorers.
        if dsl.parent_selector is ParentSelector.BASELINE:
            explorer_count += optimizer_count
            optimizer_count = 0

        # Cannot deepen past the DSL max_depth: convert optimizers to fresh
        # explorers rather than emitting a parented spawn that would exceed it.
        if not can_deepen and optimizer_count > 0:
            explorer_count += optimizer_count
            optimizer_count = 0

        # No candidate nodes means no parent can be named: optimizers degrade
        # to fresh explorers (parenting is impossible this decision).
        if not nodes and optimizer_count > 0:
            explorer_count += optimizer_count
            optimizer_count = 0

        # Pick optimizer parents on distinct lineages, forcing UNDEREXPLORED
        # (distinct-lineage, non-elite) ordering when concentrated.
        optimizer_parents: list[ApproachFingerprint] = []
        if optimizer_count > 0:
            optimizer_parents = _pick_distinct_lineage_parents(
                nodes, effective_selector, optimizer_count
            )
        degraded_to_explorer = optimizer_count - len(optimizer_parents)
        explorer_count += degraded_to_explorer
        optimizer_count = len(optimizer_parents)

        # F1: the baseline reseed occupies a slot INSIDE width. Consume one
        # explorer slot if any; otherwise degrade the lowest-ranked optimizer
        # slot to the reseed. Total spawns never exceed width.
        reseed_emitted = False
        if reseed_due:
            if explorer_count > 0:
                explorer_count -= 1
            elif optimizer_count > 0:
                optimizer_count -= 1
                if optimizer_parents:
                    optimizer_parents.pop()
            reseed_emitted = True

        # F3: enforce the diversity floor on the EMITTED batch. While the batch
        # parent concentration (max spawns-per-single-parent-lineage / total
        # spawns in the batch) exceeds (1 - diversity_floor) + 1e-12, degrade
        # the lowest-ranked surplus optimizer parent to a fresh explorer.
        # Mutates optimizer_parents in place; returns the updated explorer
        # count. Total emitted spawns stay equal to width (each degraded
        # optimizer becomes an explorer — never removed, never over-allocated).
        explorer_count = self._enforce_emitted_diversity_floor(
            optimizer_parents,
            explorer_count=explorer_count,
            reseed_emitted=reseed_emitted,
            diversity_floor=dsl.diversity_floor,
        )

        # The highest-weight variation class heads every spawn (deterministic;
        # ties broken by class value).
        variation_class = max(
            dsl.variation_weights, key=lambda vw: (vw[1], vw[0].value)
        )[0]

        # Decision-level shared receipt fields.
        budget_allocation = BudgetAllocation(attempts=1, cost=0.0)
        alternatives = self._alternatives_for(nodes, effective_selector)
        descriptor_hash = descriptor.descriptor_hash
        policy_hash = policy.policy_hash

        spawns: list[ScheduledSpawn] = []
        spawn_index = 0

        # 1) baseline reseed (if due) — a fresh explorer flagged as the reseed.
        if reseed_emitted:
            spawns.append(
                self._build_spawn(
                    role=DiscoveryRole.EXPLORER,
                    parent=None,
                    spawn_index=spawn_index,
                    sequence=sequence,
                    campaign_id=descriptor.campaign_id,
                    descriptor_hash=descriptor_hash,
                    policy_hash=policy_hash,
                    width=width,
                    depth_allocated=1,
                    concurrency=concurrency,
                    budget_allocation=budget_allocation,
                    alternatives=alternatives,
                    variation_class=variation_class,
                    briefing_template_id=dsl.briefing_template_id,
                    expected_gain=_clamp_fraction(descriptor.approach_diversity),
                    reason=(
                        f"baseline reseed at interval {dsl.baseline_reseed_interval} "
                        f"(decision sequence {sequence})"
                    ),
                )
            )
            spawn_index += 1

        # 2) fresh explorers (baseline-rooted, no parent lineage).
        for _ in range(explorer_count):
            spawns.append(
                self._build_spawn(
                    role=DiscoveryRole.EXPLORER,
                    parent=None,
                    spawn_index=spawn_index,
                    sequence=sequence,
                    campaign_id=descriptor.campaign_id,
                    descriptor_hash=descriptor_hash,
                    policy_hash=policy_hash,
                    width=width,
                    depth_allocated=1,
                    concurrency=concurrency,
                    budget_allocation=budget_allocation,
                    alternatives=alternatives,
                    variation_class=variation_class,
                    briefing_template_id=dsl.briefing_template_id,
                    expected_gain=_clamp_fraction(descriptor.approach_diversity),
                    reason=(
                        "fresh explorer (baseline-rooted, no parent lineage) "
                        f"under selector {effective_selector.value}"
                    ),
                )
            )
            spawn_index += 1

        # 3) optimizers refining a chosen parent (distinct lineages).
        for parent in optimizer_parents:
            reason = (
                "optimizer parent forced to UNDEREXPLORED (concentration "
                f"{descriptor.parent_selection_concentration:.4f} exceeds "
                f"diversity allowance {maximum_concentration:.4f})"
                if concentrated
                else f"optimizer refining parent via selector {dsl.parent_selector.value}"
            )
            spawns.append(
                self._build_spawn(
                    role=DiscoveryRole.OPTIMIZER,
                    parent=parent,
                    spawn_index=spawn_index,
                    sequence=sequence,
                    campaign_id=descriptor.campaign_id,
                    descriptor_hash=descriptor_hash,
                    policy_hash=policy_hash,
                    width=width,
                    depth_allocated=descriptor.lineage_depth + 1,
                    concurrency=concurrency,
                    budget_allocation=budget_allocation,
                    alternatives=alternatives,
                    variation_class=variation_class,
                    briefing_template_id=dsl.briefing_template_id,
                    expected_gain=_clamp_fraction(
                        1.0 - descriptor.parent_selection_concentration
                    ),
                    reason=reason,
                )
            )
            spawn_index += 1

        # F1 safety guard: total spawns must never exceed width. This is an
        # invariant of the accounting above; the guard survives `python -O`.
        if len(spawns) > width:
            raise SchedulerError(
                f"scheduler emitted {len(spawns)} spawns for a width-{width} batch "
                "(over-allocation invariant violated)"
            )
        return tuple(spawns)

    # -- internals ----------------------------------------------------------

    @staticmethod
    def _enforce_emitted_diversity_floor(
        optimizer_parents: list[ApproachFingerprint],
        *,
        explorer_count: int,
        reseed_emitted: bool,
        diversity_floor: float,
    ) -> int:
        """F3: degrade lowest-ranked surplus optimizers to explorers until the
        emitted batch's parent concentration respects the diversity floor.

        ``optimizer_parents`` is the selector-ordered (best-first) list of
        distinct-lineage optimizer parents still scheduled; this method pops
        surplus entries from it and returns the updated ``explorer_count``.
        The total emitted-spawn count (reseed + explorers + optimizers) is
        unchanged: each degraded optimizer becomes a fresh explorer.
        """
        if not optimizer_parents:
            return explorer_count
        maximum_concentration = 1.0 - diversity_floor
        while optimizer_parents:
            total = (
                explorer_count + len(optimizer_parents) + (1 if reseed_emitted else 0)
            )
            if total <= 0:
                break
            counts: dict[str, int] = {}
            for parent in optimizer_parents:
                counts[parent.lineage_id] = counts.get(parent.lineage_id, 0) + 1
            max_count = max(counts.values())
            concentration = max_count / total
            if concentration <= maximum_concentration + 1e-12:
                break
            # The over-concentrated lineage (max count; deterministic tie-break
            # by lexicographically smallest lineage id).
            over_lineage = min(
                lineage for lineage, count in counts.items() if count == max_count
            )
            # Degrade the lowest-ranked optimizer on that lineage (last in the
            # selector-ordered list) to a fresh explorer.
            degrade_idx: int | None = None
            for idx in range(len(optimizer_parents) - 1, -1, -1):
                if optimizer_parents[idx].lineage_id == over_lineage:
                    degrade_idx = idx
                    break
            if degrade_idx is None:
                break
            optimizer_parents.pop(degrade_idx)
            explorer_count += 1
        return explorer_count

    @staticmethod
    def _alternatives_for(
        nodes: tuple[ApproachFingerprint, ...], selector: ParentSelector
    ) -> tuple[CandidateAlternative, ...]:
        """The alternatives actually considered, scored and sorted by id.

        Never empty when more than one candidate existed (when at least one
        candidate exists the tuple records all of them), so a spawn that chose
        fresh exploration still attests to the candidates it weighed.
        """
        ranked = _rank_candidates(nodes, selector)
        return tuple(
            CandidateAlternative(
                candidate_id=node.candidate_id, selector_score=score
            )
            for node, score in ranked
        )

    def _build_spawn(
        self,
        *,
        role: DiscoveryRole,
        parent: ApproachFingerprint | None,
        spawn_index: int,
        sequence: int,
        campaign_id: str,
        descriptor_hash: str,
        policy_hash: str,
        width: int,
        depth_allocated: int,
        concurrency: int,
        budget_allocation: BudgetAllocation,
        alternatives: tuple[CandidateAlternative, ...],
        variation_class: VariationClass,
        briefing_template_id: str,
        expected_gain: float,
        reason: str,
    ) -> ScheduledSpawn:
        if role is DiscoveryRole.OPTIMIZER:
            # F6: an optimizer spawn requires a parent fingerprint. This guard
            # raises (not `assert`) so it survives `python -O`.
            if parent is None:
                raise SchedulerError(
                    "an optimizer spawn requires a parent fingerprint "
                    "(invariant violated by schedule())"
                )
            lineage_id = f"{campaign_id}-lin-{sequence}-{spawn_index}"
            attempt_id = f"{campaign_id}-att-{sequence}-optimizer-{spawn_index}"
            parent_lineage_id = parent.lineage_id
            parent_candidate_id = parent.candidate_id
        else:
            lineage_id = f"{campaign_id}-lin-{sequence}-{spawn_index}"
            attempt_id = f"{campaign_id}-att-{sequence}-explorer-{spawn_index}"
            parent_lineage_id = None
            parent_candidate_id = None

        assignment = DiscoveryAssignment(
            assignment_id=f"{campaign_id}-assign-{sequence}-{spawn_index}",
            campaign_id=campaign_id,
            lineage_id=lineage_id,
            attempt_id=attempt_id,
            role=role,
            parent_lineage_id=parent_lineage_id,
            parent_attempt_id=parent_candidate_id,
            seed=sequence * 1_000_003 + spawn_index,
            sequence=sequence,
            created_at=sequence,
        )
        receipt = SearchDecisionReceipt(
            campaign_id=campaign_id,
            sequence=sequence,
            descriptor_hash=descriptor_hash,
            policy_hash=policy_hash,
            assignment_hash=assignment.assignment_hash,
            parent_lineage_id=parent_lineage_id,
            parent_candidate_id=parent_candidate_id,
            role=role,
            variation_class=variation_class,
            briefing_template_id=briefing_template_id,
            width_allocated=width,
            depth_allocated=depth_allocated,
            concurrency_allocated=concurrency,
            budget_allocated=budget_allocation,
            alternatives_considered=alternatives,
            expected_information_gain=expected_gain,
            reason=reason,
        )
        return ScheduledSpawn(assignment=assignment, receipt=receipt)
