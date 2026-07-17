"""Learning module. M2 ships the fast-loop marks (asymmetric credit
assignment + auto-deprecation); gap detection and staleness land in M6."""
from selflearn.learning.marks import MarkReport, apply_outcome

__all__ = ["MarkReport", "apply_outcome"]
