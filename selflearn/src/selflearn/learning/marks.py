"""Fast-loop feedback: asymmetric credit assignment over verified outcomes.

The plan's rules, exactly:

- Helpful marks are cheap and noisy-tolerant: a verified PASS credits every
  injected entry, weighted heavier for entries the worker cited as used
  (``applied_knowledge``). A false helpful mark only keeps an entry
  retrievable.
- Harmful marks require implication evidence: a verified FAIL harms only
  the entries the host explicitly implicated (grounded reflection cited
  them, or the failure landed in their claimed domain). Injection alone
  never harms.
- Persistently harmful entries auto-deprecate: harmful past a threshold and
  exceeding helpful. Deprecation is journaled by the store and reversible.

Everything here is counters and thresholds — no model judgment.
"""
from __future__ import annotations

from dataclasses import dataclass

from selflearn.contracts import TaskOutcome
from selflearn.store.packstore import PackStore

HELPFUL_WEIGHT = 1.0
APPLIED_WEIGHT = 2.0
HARMFUL_WEIGHT = 1.0
DEPRECATE_THRESHOLD = 3.0


@dataclass(frozen=True)
class MarkReport:
    helpful_marked: tuple[str, ...]
    harmful_marked: tuple[str, ...]
    deprecated: tuple[str, ...]


def apply_outcome(store: PackStore, outcome: TaskOutcome,
                  helpful_weight: float = HELPFUL_WEIGHT,
                  applied_weight: float = APPLIED_WEIGHT,
                  harmful_weight: float = HARMFUL_WEIGHT,
                  deprecate_threshold: float = DEPRECATE_THRESHOLD) -> MarkReport:
    helpful_marked: list[str] = []
    harmful_marked: list[str] = []
    deprecated: list[str] = []

    if outcome.verdict == "pass":
        for entry_id in outcome.injected:
            weight = applied_weight if entry_id in outcome.applied else helpful_weight
            store.mark(entry_id, helpful=weight)
            helpful_marked.append(entry_id)
        return MarkReport(tuple(helpful_marked), (), ())

    for entry_id in outcome.implicated:
        stored = store.mark(entry_id, harmful=harmful_weight)
        harmful_marked.append(entry_id)
        if (stored.status == "published"
                and stored.harmful >= deprecate_threshold
                and stored.harmful > stored.helpful):
            store.deprecate(entry_id,
                            f"auto: harmful={stored.harmful} > "
                            f"helpful={stored.helpful} "
                            f"(threshold {deprecate_threshold}); "
                            f"last failure task={outcome.task_id} "
                            f"mode={outcome.failure_mode or 'unspecified'}")
            deprecated.append(entry_id)
    return MarkReport((), tuple(harmful_marked), tuple(deprecated))
