"""Learning module: fast-loop marks (M2) + slow-loop gap detection,
staleness, advisory suggestions, and suite regression (M6)."""
from selflearn.learning.gaps import Learner, LearningConfig, label_topic
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

__all__ = ["MarkReport", "apply_outcome", "Learner", "LearningConfig",
           "label_topic", "RegressionReport", "check_regression",
           "snapshot_baseline", "MARK_HALF_LIFE_DAYS", "decay_factor",
           "effective_counts"]
