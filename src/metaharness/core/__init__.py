"""Core domain types, budgets, and the task execution loop."""
from metaharness.core.budget import Budget, BudgetExceeded, PlateauDetector, action_signature
from metaharness.core.executor import TaskExecutor
from metaharness.core.types import (
    Attempt, MASTMode, Task, TaskOutcome, TaskType, Tier,
    VerificationResult, Verdict, WorkerResult,
)

__all__ = [
    "Task", "TaskType", "Tier", "Verdict", "MASTMode",
    "WorkerResult", "VerificationResult", "Attempt", "TaskOutcome",
    "Budget", "BudgetExceeded", "PlateauDetector", "action_signature",
    "TaskExecutor",
]
