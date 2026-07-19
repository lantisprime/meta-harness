"""Learning module: fast-loop marks (M2), slow-loop gap detection,
staleness, advisory suggestions, suite regression (M6), and the
evidence contracts for bounded specialist improvement campaigns
(readiness assessment, evaluation splits, trial eligibility)."""
from selflearn.learning.gaps import Learner, LearningConfig, label_topic
from selflearn.learning.improvement import (
    DomainReadinessReport,
    EvaluationCriterion,
    EvaluationItemResult,
    EvaluationSplits,
    ExpertExample,
    FailureCluster,
    ImprovementDecision,
    ImprovementPolicy,
    ImprovementTrial,
    assess_domain_readiness,
    cluster_failures,
    evaluate_improvement_trial,
)
from selflearn.learning.marks import (
    MARK_HALF_LIFE_DAYS,
    MarkReport,
    apply_outcome,
    decay_factor,
    effective_counts,
)
from selflearn.learning.regression import (
    RegressionReport,
    check_regression,
    snapshot_baseline,
)

__all__ = [
    "MarkReport", "apply_outcome", "Learner", "LearningConfig",
    "label_topic", "RegressionReport", "check_regression",
    "snapshot_baseline", "MARK_HALF_LIFE_DAYS", "decay_factor",
    "effective_counts", "DomainReadinessReport", "EvaluationCriterion",
    "EvaluationItemResult",
    "EvaluationSplits", "ExpertExample", "FailureCluster",
    "ImprovementDecision", "ImprovementPolicy", "ImprovementTrial",
    "assess_domain_readiness", "cluster_failures",
    "evaluate_improvement_trial",
]
