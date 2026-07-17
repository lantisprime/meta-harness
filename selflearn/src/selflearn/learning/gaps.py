"""Slow-loop learning: gap detection, staleness, advisory suggestions (M6).

Deterministic counters, thresholds, and joins over externally verified
events — no model judgment. Three typed signals, each implying a different
acquisition prompt:

- ``coverage``: failures cluster in a topic the pack claims but retrieval
  surfaced nothing for — the knowledge doesn't exist.
- ``quality``: entries were retrieved for the failing cluster but aren't
  working — re-verify/amend, don't re-acquire.
- ``staleness``: an entry's marks decayed and its sources are old — the
  world moved; re-fetch.

Guardrails: signals only *propose* (the host surfaces them advisory-only,
never auto-runs acquisition), and a topic that just signaled is suppressed
with backoff so even suggestions can't nag in a loop.

Topic labeling (simulation finding 3): outcomes carry a topic assigned
deterministically by ``label_topic`` — semantic match of the task text
against pack content — and an empty topic means *unlabeled*, excluded from
gap joins rather than guessed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from selflearn.contracts import GapSignal, TaskOutcome
from selflearn.learning.marks import MarkReport, apply_outcome
from selflearn.store.packstore import PackStore

ACTIONS = {
    "coverage": "propose acquisition run: acquire the topic from reputable "
                "sources (knowledge missing)",
    "quality": "propose amendment run: re-verify/amend the implicated "
               "entries (knowledge not working)",
    "staleness": "propose refresh run: re-fetch sources and regenerate the "
                 "entry (knowledge aging)",
}


@dataclass(frozen=True)
class LearningConfig:
    min_failures: int = 2            # failures per topic before a signal
    backoff_rounds: int = 2          # suppressed signal rounds after firing
    staleness_max_age_days: int = 180
    staleness_score_max: float = 0.45   # only aging entries that also stopped helping


def label_topic(retriever, packs: list[str], text: str,
                threshold: float = 0.08) -> str:
    """Deterministic topic labeling for TaskOutcome.topic: the coverage-map
    topic of the best-matching published entry, or "" (unlabeled) below the
    threshold — never a guess."""
    results = retriever.retrieve(list(packs), text, k=1)
    if results and results[0].score >= threshold:
        return results[0].entry.cand.topic
    return ""


class Learner:
    """Facade over both loops: fast marks (M2) + slow gap detection (M6)."""

    def __init__(self, store: PackStore, config: LearningConfig = LearningConfig()):
        self.store = store
        self.config = config
        self._failures: list[TaskOutcome] = []
        self._backoff: dict[str, int] = {}

    # -- fast loop ------------------------------------------------------

    def observe(self, outcome: TaskOutcome) -> MarkReport:
        report = apply_outcome(self.store, outcome)
        if outcome.verdict == "fail":
            self._failures.append(outcome)
        return report

    # -- slow loop ------------------------------------------------------

    def gap_signals(self, pack: str) -> list[GapSignal]:
        by_topic: dict[str, list[TaskOutcome]] = {}
        for f in self._failures:
            if f.topic:                         # unlabeled excluded, not guessed
                by_topic.setdefault(f.topic, []).append(f)
        coverage = self.store.coverage(pack)
        signals: list[GapSignal] = []
        for topic, fails in sorted(by_topic.items()):
            if len(fails) < self.config.min_failures:
                continue
            key = f"{pack}:{topic}"
            if self._backoff.get(key, 0) > 0:
                self._backoff[key] -= 1
                continue
            retrieved_any = any(f.injected for f in fails)
            if coverage.get(topic) != "covered" or not retrieved_any:
                signals.append(GapSignal(
                    pack=pack, topic=topic, kind="coverage",
                    evidence=f"{len(fails)} verified failures; topic "
                             f"{'claimed but not covered' if topic in coverage else 'not in coverage map'}"
                             f"{'' if retrieved_any else '; nothing was retrieved'}"))
            else:
                signals.append(GapSignal(
                    pack=pack, topic=topic, kind="quality",
                    evidence=f"{len(fails)} verified failures despite "
                             f"retrieval; implicated: "
                             f"{sorted({e for f in fails for e in f.implicated})}"))
            self._backoff[key] = self.config.backoff_rounds
        return signals

    def staleness_signals(self, pack: str,
                          now: Optional[datetime] = None) -> list[GapSignal]:
        now = now or datetime.now(timezone.utc)
        horizon = now - timedelta(days=self.config.staleness_max_age_days)
        signals = []
        for stored in self.store.published(pack):
            fetched = _parse_when(stored.cand.sources[0].fetched_at)
            if fetched is None or fetched > horizon:
                continue
            if stored.score > self.config.staleness_score_max:
                continue                        # old but still earning its keep
            age_days = (now - fetched).days
            signals.append(GapSignal(
                pack=pack, topic=stored.cand.topic, kind="staleness",
                evidence=f"{stored.cand.id}: sources {age_days}d old, "
                         f"score {stored.score:.2f} "
                         f"(helpful={stored.helpful}, harmful={stored.harmful})"))
        return signals

    def suggestions(self, pack: str,
                    now: Optional[datetime] = None) -> list[dict]:
        """Advisory-only proposals for the host's console: what to run and
        why. The host NEVER auto-runs these — a human starts acquisition."""
        out = []
        for sig in self.gap_signals(pack) + self.staleness_signals(pack, now):
            out.append({"pack": sig.pack, "topic": sig.topic,
                        "kind": sig.kind, "evidence": sig.evidence,
                        "proposed_action": ACTIONS[sig.kind],
                        "advisory": "requires human approval; never auto-run"})
        return out


def _parse_when(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed
