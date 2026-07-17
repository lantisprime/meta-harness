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

Recency decay (review finding, 2026-07-17): marks are not lifetime
counters. On every mark event the entry's existing counters are first
multiplied by ``0.5 ** (days_since_last_mark / half_life_days)``, so an
entry that was helpful 100 times last year but is wrong today does NOT need
100 harmful marks to deprecate — old evidence fades, recent evidence wins.
Decay is lazy (applied at mark time); readers that need a current value
without marking use :func:`effective_counts`.

Everything here is counters and thresholds — no model judgment.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from selflearn.contracts import TaskOutcome
from selflearn.store.packstore import PackStore, StoredEntry

HELPFUL_WEIGHT = 1.0
APPLIED_WEIGHT = 2.0
HARMFUL_WEIGHT = 1.0
DEPRECATE_THRESHOLD = 3.0
MARK_HALF_LIFE_DAYS = 90.0


def _parse_iso(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def decay_factor(last_marked_iso: str, now: datetime,
                 half_life_days: float = MARK_HALF_LIFE_DAYS) -> float:
    """0.5 per half-life elapsed since the last mark event; 1.0 when the
    entry has never been marked or the timestamp is unparseable."""
    last = _parse_iso(last_marked_iso)
    if last is None or half_life_days <= 0:
        return 1.0
    elapsed_days = max(0.0, (now - last).total_seconds() / 86400.0)
    return 0.5 ** (elapsed_days / half_life_days)


def effective_counts(stored: StoredEntry, now: datetime,
                     half_life_days: float = MARK_HALF_LIFE_DAYS
                     ) -> tuple[float, float]:
    """Read-only decayed (helpful, harmful) as of ``now`` — for consumers
    (staleness, dashboards) that must see current evidence without writing."""
    factor = decay_factor(stored.marks_updated_at, now, half_life_days)
    return stored.helpful * factor, stored.harmful * factor


@dataclass(frozen=True)
class MarkReport:
    helpful_marked: tuple[str, ...]
    harmful_marked: tuple[str, ...]
    deprecated: tuple[str, ...]


def apply_outcome(store: PackStore, outcome: TaskOutcome,
                  helpful_weight: float = HELPFUL_WEIGHT,
                  applied_weight: float = APPLIED_WEIGHT,
                  harmful_weight: float = HARMFUL_WEIGHT,
                  deprecate_threshold: float = DEPRECATE_THRESHOLD,
                  half_life_days: float = MARK_HALF_LIFE_DAYS,
                  now: Optional[datetime] = None) -> MarkReport:
    now = now or datetime.now(timezone.utc)
    now_iso = now.isoformat()
    helpful_marked: list[str] = []
    harmful_marked: list[str] = []
    deprecated: list[str] = []

    def _mark(entry_id: str, **counts) -> StoredEntry:
        factor = decay_factor(store.get(entry_id).marks_updated_at, now,
                              half_life_days)
        return store.mark(entry_id, decay=factor, now_iso=now_iso,
                          task_type=outcome.task_type, **counts)

    if outcome.verdict == "pass":
        # credited = injected + plan-seeding entries (a workflow entry that
        # shaped the plan earns helpful evidence from a verified completion)
        for entry_id in outcome.credited:
            weight = applied_weight if entry_id in outcome.applied else helpful_weight
            _mark(entry_id, helpful=weight)
            helpful_marked.append(entry_id)
        return MarkReport(tuple(helpful_marked), (), ())

    for entry_id in outcome.implicated:
        stored = _mark(entry_id, harmful=harmful_weight)
        harmful_marked.append(entry_id)
        # Deprecation triggers on the decay-free EVENT streak (N consecutive
        # harmful marks, any cadence — decayed float counters plateau below
        # any threshold at slow cadences), while the helpful-vs-harmful
        # comparison uses the decayed counters so strong RECENT helpful
        # history still delays deprecation.
        if (stored.status == "published"
                and stored.consecutive_harmful >= deprecate_threshold
                and stored.harmful > stored.helpful):
            store.deprecate(entry_id,
                            f"auto: {stored.consecutive_harmful} consecutive "
                            f"harmful marks (threshold {deprecate_threshold}) "
                            f"and decayed harmful={stored.harmful:.2f} > "
                            f"helpful={stored.helpful:.2f}; "
                            f"last failure task={outcome.task_id} "
                            f"mode={outcome.failure_mode or 'unspecified'}")
            deprecated.append(entry_id)
    return MarkReport((), tuple(harmful_marked), tuple(deprecated))
