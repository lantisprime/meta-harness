"""MAST failure classification and clustering.

The MAST finding: most multi-agent failures are harness/specification failures,
and they recur in identifiable modes. Label every failure consistently, cluster
before fixing, and aim fixes at the biggest cluster — not the most recent anecdote.
"""
from __future__ import annotations

from collections import Counter

from metaharness.core.types import Attempt, MASTMode, Task, TaskOutcome, Verdict


def classify_failure(task: Task, attempt: Attempt) -> MASTMode:
    """Deterministic MAST label for a failed attempt. The verifier's own label
    wins when present; otherwise classify from the evidence."""
    verification = attempt.verification
    if verification.failure_mode is not None:
        return verification.failure_mode
    if verification.verdict == Verdict.UNVERIFIED:
        return MASTMode.NO_VERIFICATION
    error = attempt.result.error or ""
    if error.startswith("schema:"):
        return MASTMode.SCHEMA_INVALID
    if error:
        return MASTMode.TOOL_ERROR
    # a plain wrong answer is a failure to satisfy the task contract
    return MASTMode.DISOBEY_TASK_SPEC


class FailureStats:
    """Failure counts per (task type, MAST mode) — the clustering the slow loop
    curates against."""

    def __init__(self) -> None:
        self._counts: Counter[tuple[str, MASTMode]] = Counter()

    def observe(self, outcome: TaskOutcome) -> None:
        for attempt in outcome.attempts:
            if attempt.verification.verdict == Verdict.FAIL:
                mode = classify_failure(outcome.task, attempt)
                self._counts[(outcome.task.task_type.value, mode)] += 1

    def count(self, task_type: str, mode: MASTMode) -> int:
        return self._counts[(task_type, mode)]

    def top_clusters(self, n: int = 5) -> list[tuple[str, MASTMode, int]]:
        return [
            (task_type, mode, count)
            for (task_type, mode), count in self._counts.most_common(n)
        ]

    def as_dict(self) -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = {}
        for (task_type, mode), count in sorted(self._counts.items()):
            out.setdefault(task_type, {})[mode.value] = count
        return out

    def save(self, path) -> None:
        import json
        from pathlib import Path

        Path(path).write_text(json.dumps(self.as_dict(), indent=1, sort_keys=True),
                              encoding="utf-8")

    @classmethod
    def load(cls, path) -> "FailureStats":
        import json
        from pathlib import Path

        stats = cls()
        for task_type, modes in json.loads(Path(path).read_text(encoding="utf-8")).items():
            for mode, count in modes.items():
                stats._counts[(task_type, MASTMode(mode))] = int(count)
        return stats
