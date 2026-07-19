"""Evidence contracts for bounded specialist improvement campaigns.

The contracts in this module deliberately stop short of running an optimizer.
They establish whether a specialist has enough domain evidence to begin, keep
fit/validation/test data separate, and decide candidate eligibility from
frozen validation evidence. Final test data remains sealed until an explicit
final evaluation; human promotion remains outside this module.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterable

from selflearn.contracts import ContractError, TaskOutcome

if TYPE_CHECKING:
    from selflearn.specialist import SpecialistSpec
    from selflearn.store.packstore import PackStore


_CHECK_KINDS = {"deterministic", "judge", "execution"}


def _nonempty_unique(values: tuple[str, ...], field: str) -> None:
    if any(not value for value in values):
        raise ContractError(f"{field} values must be non-empty")
    if len(values) != len(set(values)):
        raise ContractError(f"{field} values must be unique")


@dataclass(frozen=True)
class EvaluationCriterion:
    """A domain-expert-approved, probe-backed success criterion."""

    id: str
    description: str
    failure_mode: str
    check_kind: str
    probe_ids: tuple[str, ...]
    anchors: tuple[str, ...] = ()
    approved_by: str = ""

    def __post_init__(self) -> None:
        if not self.id or not self.description or not self.failure_mode:
            raise ContractError(
                "evaluation criterion id, description, and failure_mode "
                "must be non-empty")
        if self.check_kind not in _CHECK_KINDS:
            raise ContractError(
                f"unsupported evaluation check_kind {self.check_kind!r}")
        _nonempty_unique(self.probe_ids, "criterion probe_ids")
        _nonempty_unique(self.anchors, "criterion anchors")

    @property
    def high_signal(self) -> bool:
        """Whether the criterion is objective or sufficiently anchored."""
        minimum_anchors = 2 if self.check_kind == "judge" else 1
        return bool(self.approved_by) and len(self.anchors) >= minimum_anchors

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "description": self.description,
            "failure_mode": self.failure_mode,
            "check_kind": self.check_kind,
            "probe_ids": list(self.probe_ids),
            "anchors": list(self.anchors),
            "approved_by": self.approved_by,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "EvaluationCriterion":
        return cls(
            id=data.get("id", ""),
            description=data.get("description", ""),
            failure_mode=data.get("failure_mode", ""),
            check_kind=data.get("check_kind", ""),
            probe_ids=tuple(data.get("probe_ids", [])),
            anchors=tuple(data.get("anchors", [])),
            approved_by=data.get("approved_by", ""),
        )


@dataclass(frozen=True)
class ExpertExample:
    """A domain expert's labelled example and its rationale."""

    id: str
    criterion_id: str
    expected: str
    rationale: str

    def __post_init__(self) -> None:
        if not all((self.id, self.criterion_id, self.expected, self.rationale)):
            raise ContractError("expert example fields must be non-empty")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "criterion_id": self.criterion_id,
            "expected": self.expected,
            "rationale": self.rationale,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ExpertExample":
        return cls(
            id=data.get("id", ""),
            criterion_id=data.get("criterion_id", ""),
            expected=data.get("expected", ""),
            rationale=data.get("rationale", ""),
        )


@dataclass(frozen=True)
class EvaluationSplits:
    """Disjoint item identifiers with a sealed final-test partition."""

    fit: tuple[str, ...]
    validation: tuple[str, ...]
    test: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in ("fit", "validation", "test"):
            _nonempty_unique(getattr(self, name), f"{name} split")
        overlap = (
            set(self.fit) & set(self.validation)
            | set(self.fit) & set(self.test)
            | set(self.validation) & set(self.test)
        )
        if overlap:
            raise ContractError(
                f"evaluation split overlap is forbidden: {sorted(overlap)}")

    def items_for(self, split: str) -> tuple[str, ...]:
        if split == "test":
            raise ContractError(
                "test split is sealed; request final_test only after the "
                "improvement campaign stops")
        if split == "final_test":
            return self.test
        if split not in {"fit", "validation"}:
            raise ContractError(f"unknown evaluation split {split!r}")
        return getattr(self, split)

    def to_dict(self) -> dict:
        return {
            "fit": list(self.fit),
            "validation": list(self.validation),
            "test": list(self.test),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "EvaluationSplits":
        return cls(
            fit=tuple(data.get("fit", [])),
            validation=tuple(data.get("validation", [])),
            test=tuple(data.get("test", [])),
        )


@dataclass(frozen=True)
class ImprovementPolicy:
    """Versionable evidence and stopping policy for one specialist."""

    domain_expert: str
    optimizer_identity: str
    evaluator_identity: str
    criteria: tuple[EvaluationCriterion, ...]
    expert_examples: tuple[ExpertExample, ...]
    splits: EvaluationSplits
    target_validation_score: float
    max_iterations: int
    plateau_rounds: int
    min_validation_gain: float = 0.0

    def __post_init__(self) -> None:
        if not all((self.domain_expert, self.optimizer_identity,
                    self.evaluator_identity)):
            raise ContractError("improvement policy identities must be non-empty")
        if self.optimizer_identity == self.evaluator_identity:
            raise ContractError(
                "optimizer and evaluator identities must be distinct")
        if not 0.0 <= self.target_validation_score <= 1.0:
            raise ContractError("target_validation_score must be between 0 and 1")
        if self.max_iterations <= 0:
            raise ContractError("max_iterations must be positive")
        if self.plateau_rounds <= 0 or self.plateau_rounds > self.max_iterations:
            raise ContractError(
                "plateau_rounds must be positive and no greater than "
                "max_iterations")
        if self.min_validation_gain < 0.0:
            raise ContractError("min_validation_gain cannot be negative")
        criterion_ids = tuple(item.id for item in self.criteria)
        _nonempty_unique(criterion_ids, "criteria")
        example_ids = tuple(item.id for item in self.expert_examples)
        _nonempty_unique(example_ids, "expert_examples")
        unknown = sorted({item.criterion_id for item in self.expert_examples}
                         - set(criterion_ids))
        if unknown:
            raise ContractError(
                f"expert examples reference unknown criteria: {unknown}")

    def to_dict(self) -> dict:
        return {
            "domain_expert": self.domain_expert,
            "optimizer_identity": self.optimizer_identity,
            "evaluator_identity": self.evaluator_identity,
            "criteria": [item.to_dict() for item in self.criteria],
            "expert_examples": [item.to_dict()
                                for item in self.expert_examples],
            "splits": self.splits.to_dict(),
            "target_validation_score": self.target_validation_score,
            "max_iterations": self.max_iterations,
            "plateau_rounds": self.plateau_rounds,
            "min_validation_gain": self.min_validation_gain,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ImprovementPolicy":
        if not isinstance(data, dict):
            raise ContractError("improvement_policy must be a mapping")
        return cls(
            domain_expert=data.get("domain_expert", ""),
            optimizer_identity=data.get("optimizer_identity", ""),
            evaluator_identity=data.get("evaluator_identity", ""),
            criteria=tuple(EvaluationCriterion.from_dict(item)
                           for item in data.get("criteria", [])),
            expert_examples=tuple(ExpertExample.from_dict(item)
                                  for item in data.get("expert_examples", [])),
            splits=EvaluationSplits.from_dict(data.get("splits", {})),
            target_validation_score=float(
                data.get("target_validation_score", 0.0)),
            max_iterations=int(data.get("max_iterations", 0)),
            plateau_rounds=int(data.get("plateau_rounds", 0)),
            min_validation_gain=float(data.get("min_validation_gain", 0.0)),
        )


@dataclass(frozen=True)
class DomainReadinessReport:
    ready: bool
    reasons: tuple[str, ...]
    high_signal_criteria: tuple[str, ...] = ()
    weak_criteria: tuple[str, ...] = ()
    fit_items: int = 0
    validation_items: int = 0
    test_items: int = 0


@dataclass(frozen=True)
class FailureCluster:
    """A recurring, externally verified failure pattern."""

    topic: str
    failure_mode: str
    count: int
    task_ids: tuple[str, ...]

    @property
    def id(self) -> str:
        return f"{self.topic}:{self.failure_mode}"


def cluster_failures(outcomes: Iterable[TaskOutcome]) -> tuple[FailureCluster, ...]:
    grouped: dict[tuple[str, str], list[str]] = {}
    for outcome in outcomes:
        if outcome.verdict != "fail" or not outcome.topic:
            continue
        mode = outcome.failure_mode or "unspecified"
        grouped.setdefault((outcome.topic, mode), []).append(outcome.task_id)
    clusters = [
        FailureCluster(topic=topic, failure_mode=mode, count=len(task_ids),
                       task_ids=tuple(task_ids))
        for (topic, mode), task_ids in grouped.items()
    ]
    return tuple(sorted(clusters,
                        key=lambda item: (-item.count, item.topic,
                                          item.failure_mode)))


def _valid_baseline(store: "PackStore", pack: str) -> bool:
    path = store.root / pack / "evals" / "baseline.json"
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text())
        return (data.get("pack") == pack
                and bool(data.get("model_id"))
                and int(data.get("total", 0)) > 0
                and 0.0 <= float(data.get("pass_rate", -1.0)) <= 1.0
                and data.get("injected") is True)
    except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
        return False


def assess_domain_readiness(spec: "SpecialistSpec",
                            store: "PackStore") -> DomainReadinessReport:
    """Read-only readiness check for a specialist improvement campaign."""
    policy = spec.improvement_policy
    if policy is None:
        return DomainReadinessReport(
            ready=False,
            reasons=(
                "specialist has no improvement_policy; retrieval may run but "
                "self-improvement is not ready",
            ),
        )

    reasons: list[str] = []
    high_signal: list[str] = []
    weak: list[str] = []
    examples_by_criterion = {item.criterion_id
                             for item in policy.expert_examples}

    if not policy.criteria:
        reasons.append("improvement policy has no evaluation criteria")
    if not policy.expert_examples:
        reasons.append("improvement policy has no domain-expert examples")
    if not policy.splits.fit:
        reasons.append("fit split has no examples")
    if not policy.splits.validation:
        reasons.append("validation split has no examples")
    if not policy.splits.test:
        reasons.append("sealed test split has no examples")

    probes = {}
    for pack in spec.packs:
        published = store.published(pack)
        if not published:
            reasons.append(f"pack {pack!r} has no published knowledge")
        if not _valid_baseline(store, pack):
            reasons.append(f"pack {pack!r} has no frozen suite baseline")
        for entry in published:
            for probe in store.probes_for(entry.cand.id):
                probes[probe.id] = probe

    for criterion in policy.criteria:
        criterion_reasons: list[str] = []
        if criterion.approved_by != policy.domain_expert:
            criterion_reasons.append(
                "approval does not match the declared domain expert")
        if criterion.id not in examples_by_criterion:
            criterion_reasons.append("has no domain-expert example")
        if criterion.check_kind == "judge" and len(criterion.anchors) < 2:
            criterion_reasons.append("uses an unanchored judge scale")
        elif not criterion.anchors:
            criterion_reasons.append("has no explicit pass/fail anchor")
        for probe_id in criterion.probe_ids:
            probe = probes.get(probe_id)
            if probe is None:
                criterion_reasons.append(
                    f"references missing published probe {probe_id!r}")
            elif not probe.validated:
                criterion_reasons.append(f"probe {probe_id!r} is not validated")
            elif not probe.validated_by:
                criterion_reasons.append(
                    f"probe {probe_id!r} has no validator identity")
            elif probe.validated_by == policy.optimizer_identity:
                criterion_reasons.append(
                    f"probe {probe_id!r} was validated by the optimizer")
            elif probe.check_kind != criterion.check_kind:
                criterion_reasons.append(
                    f"probe {probe_id!r} check kind does not match")
        if criterion_reasons:
            weak.append(criterion.id)
            reasons.extend(f"criterion {criterion.id!r} {reason}"
                           for reason in criterion_reasons)
        else:
            high_signal.append(criterion.id)

    return DomainReadinessReport(
        ready=not reasons,
        reasons=tuple(reasons),
        high_signal_criteria=tuple(high_signal),
        weak_criteria=tuple(weak),
        fit_items=len(policy.splits.fit),
        validation_items=len(policy.splits.validation),
        test_items=len(policy.splits.test),
    )


@dataclass(frozen=True)
class EvaluationItemResult:
    """Full-fidelity evaluator evidence for one non-test split item."""

    item_id: str
    passed: bool
    evidence: str
    evaluator_identity: str
    failure_mode: str = ""

    def __post_init__(self) -> None:
        if not self.item_id or not self.evidence or not self.evaluator_identity:
            raise ContractError(
                "evaluation item id, evidence, and evaluator_identity must "
                "be non-empty")
        if not isinstance(self.passed, bool):
            raise ContractError("evaluation item passed must be a boolean")
        if self.passed and self.failure_mode:
            raise ContractError("a passing evaluation item cannot have a failure_mode")
        if not self.passed and not self.failure_mode:
            raise ContractError("a failing evaluation item needs a failure_mode")


def _score(results: tuple[EvaluationItemResult, ...]) -> float:
    return sum(item.passed for item in results) / len(results)


def _result_map(
    results: tuple[EvaluationItemResult, ...],
    expected_ids: tuple[str, ...],
    split: str,
) -> dict[str, EvaluationItemResult]:
    by_id = {item.item_id: item for item in results}
    if len(by_id) != len(results):
        raise ContractError(f"{split} results contain duplicate item ids")
    if set(by_id) != set(expected_ids):
        missing = sorted(set(expected_ids) - set(by_id))
        extra = sorted(set(by_id) - set(expected_ids))
        raise ContractError(
            f"{split} results do not match the frozen split "
            f"(missing={missing}, extra={extra})")
    return by_id


@dataclass(frozen=True)
class ImprovementTrial:
    """One targeted candidate; final-test results are intentionally absent."""

    iteration: int
    target_cluster: str
    evaluator_identity: str
    fit_results: tuple[EvaluationItemResult, ...]
    validation_results: tuple[EvaluationItemResult, ...]

    def __post_init__(self) -> None:
        if self.iteration <= 0:
            raise ContractError("trial iteration must be positive")
        if not self.target_cluster or not self.evaluator_identity:
            raise ContractError(
                "trial target_cluster and evaluator_identity must be non-empty")
        if not self.fit_results or not self.validation_results:
            raise ContractError("trial fit and validation results must be non-empty")

    @property
    def fit_score(self) -> float:
        return _score(self.fit_results)

    @property
    def validation_score(self) -> float:
        return _score(self.validation_results)


@dataclass(frozen=True)
class ImprovementDecision:
    eligible: bool
    stop: bool
    best_validation_results: tuple[EvaluationItemResult, ...]
    stagnant_rounds: int
    reason: str

    @property
    def best_validation_score(self) -> float:
        return _score(self.best_validation_results)


def evaluate_improvement_trial(
    policy: ImprovementPolicy,
    trial: ImprovementTrial,
    *,
    dominant_cluster: FailureCluster,
    best_validation_results: tuple[EvaluationItemResult, ...],
    stagnant_rounds: int,
) -> ImprovementDecision:
    """Mark only targeted, non-regressing candidates eligible for review."""
    if stagnant_rounds < 0:
        raise ContractError("stagnant_rounds cannot be negative")
    if trial.evaluator_identity != policy.evaluator_identity:
        raise ContractError(
            "trial evaluator_identity does not match the frozen evaluator")
    all_results = (trial.fit_results + trial.validation_results
                   + best_validation_results)
    if any(item.evaluator_identity != policy.evaluator_identity
           for item in all_results):
        raise ContractError(
            "evaluation item identity does not match the frozen evaluator")

    _result_map(trial.fit_results, policy.splits.fit, "fit")
    current_by_id = _result_map(
        trial.validation_results, policy.splits.validation, "validation")
    best_by_id = _result_map(
        best_validation_results, policy.splits.validation,
        "best validation")

    targeted = trial.target_cluster == dominant_cluster.id
    regressions = tuple(sorted(
        item_id for item_id, previous in best_by_id.items()
        if previous.passed and not current_by_id[item_id].passed
    ))
    best_validation_score = _score(best_validation_results)
    gain = trial.validation_score - best_validation_score
    eligible = (targeted and not regressions and gain > 0.0
                and gain >= policy.min_validation_gain)
    next_best_results = (trial.validation_results if eligible
                         else best_validation_results)
    next_best_score = _score(next_best_results)
    next_stagnant = 0 if eligible else stagnant_rounds + 1

    reasons = []
    if not targeted:
        reasons.append("candidate does not target the dominant failure cluster")
    if regressions:
        reasons.append(
            f"per-item validation regression on {list(regressions)}")
    if targeted and not regressions and eligible:
        reasons.append(f"unseen validation improved by {gain:+.3f}")
    elif targeted and not regressions:
        reasons.append("candidate did not improve unseen validation")

    stop_reasons = []
    if next_best_score >= policy.target_validation_score:
        stop_reasons.append("validation target reached")
    if trial.iteration >= policy.max_iterations:
        stop_reasons.append("maximum iterations reached")
    if next_stagnant >= policy.plateau_rounds:
        stop_reasons.append("validation plateau reached")
    if stop_reasons:
        reasons.extend(stop_reasons)

    return ImprovementDecision(
        eligible=eligible,
        stop=bool(stop_reasons),
        best_validation_results=next_best_results,
        stagnant_rounds=next_stagnant,
        reason="; ".join(reasons),
    )
