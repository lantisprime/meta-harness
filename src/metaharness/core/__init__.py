"""Core domain types, budgets, and the task execution loop."""
from metaharness.core.budget import Budget, BudgetExceeded, PlateauDetector, action_signature
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


def __getattr__(name: str):
    """Keep leaf type/budget imports from eagerly loading the execution graph."""
    if name == "TaskExecutor":
        from metaharness.core.executor import TaskExecutor

        return TaskExecutor
    raise AttributeError(name)
