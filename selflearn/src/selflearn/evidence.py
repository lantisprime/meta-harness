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
    """Laplace-smoothed evidence score in (0, 1); no evidence -> 0.5.

    This is the MEAN of the Beta(helpful+1, harmful+1) posterior over an
    entry's helpfulness. Its uncertainty is ``laplace_variance`` below."""
    return (helpful + 1.0) / (helpful + harmful + 2.0)


def laplace_variance(helpful: float, harmful: float) -> float:
    """Variance of the Beta(helpful+1, harmful+1) posterior whose mean is
    ``laplace_score`` — the epistemic uncertainty we otherwise discard.

    Maximal at no evidence (Beta(1,1) -> 1/12 ≈ 0.083) and shrinks toward 0
    as marks accumulate, so it distinguishes "0.5 because genuinely mixed"
    from "0.5 because we know nothing." Active-inference epistemic value and
    the advisor's confidence badge read this (design note §3.2)."""
    a = helpful + 1.0
    b = harmful + 1.0
    n = a + b
    return (a * b) / (n * n * (n + 1.0))
