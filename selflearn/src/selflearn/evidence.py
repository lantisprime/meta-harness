"""Single source of truth for evidence math and timestamps.

One Laplace formula, one decay curve, one ISO parser — every consumer
(store scores, retrieval priors, mark application, staleness) reads through
these, so the views can never drift (review finding: the formula was
inlined in three places and retrieval ranked on undecayed counters while
staleness used decayed ones)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

MARK_HALF_LIFE_DAYS = 90.0


def parse_iso(value: str) -> Optional[datetime]:
    """Lenient ISO-8601 parse: Z suffix and naive timestamps tolerated;
    empty/garbage -> None (never raises)."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def decay_factor(last_marked_iso: str, now: datetime,
                 half_life_days: float = MARK_HALF_LIFE_DAYS) -> float:
    """0.5 per half-life elapsed since the last mark event; 1.0 when never
    marked or unparseable."""
    last = parse_iso(last_marked_iso)
    if last is None or half_life_days <= 0:
        return 1.0
    elapsed_days = max(0.0, (now - last).total_seconds() / 86400.0)
    return 0.5 ** (elapsed_days / half_life_days)


def laplace_score(helpful: float, harmful: float) -> float:
    """Laplace-smoothed evidence score in (0, 1); no evidence -> 0.5."""
    return (helpful + 1.0) / (helpful + harmful + 2.0)
