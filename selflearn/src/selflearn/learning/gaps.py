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

import dataclasses
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from selflearn.contracts import GapSignal, TaskOutcome
from selflearn.learning.marks import (
    MARK_HALF_LIFE_DAYS,
    MarkReport,
    apply_outcome,
    effective_counts,
)
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
    mark_half_life_days: float = MARK_HALF_LIFE_DAYS   # recency decay on marks
    max_failures: int = 500          # FIFO cap on retained failure evidence


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
    """Facade over both loops: fast marks (M2) + slow gap detection (M6).

    Slow-loop state is DURABLE (review finding, 2026-07-17): retained
    failures and backoff counters write through to ``learner-state.json``
    under the store root and reload on construction, so a restart loses no
    accumulated evidence. Failures that produced a signal are *consumed*
    (pruned) — old failures cannot re-signal every time backoff expires.
    """

    def __init__(self, store: PackStore, config: LearningConfig = LearningConfig(),
                 state_path: Optional[Path] = None):
        self.store = store
        self.config = config
        self.state_path = Path(state_path) if state_path else \
            store.root / "learner-state.json"
        self._failures: list[TaskOutcome] = []
        self._backoff: dict[str, int] = {}
        self._load_state()

    # -- durable state --------------------------------------------------

    def _load_state(self) -> None:
        if not self.state_path.exists():
            return
        data = json.loads(self.state_path.read_text())
        self._backoff = {str(k): int(v) for k, v in data.get("backoff", {}).items()}
        self._failures = [
            TaskOutcome(
                task_id=f["task_id"], task_type=f["task_type"],
                topic=f["topic"], verdict=f["verdict"],
                injected=tuple(f.get("injected", [])),
                applied=tuple(f.get("applied", [])),
                failure_mode=f.get("failure_mode", ""),
                implicated=tuple(f.get("implicated", [])),
                step_id=f.get("step_id", ""))
            for f in data.get("failures", [])]

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps({
            "failures": [dataclasses.asdict(f) for f in self._failures],
            "backoff": self._backoff}, indent=1, sort_keys=True))

    # -- fast loop ------------------------------------------------------

    def observe(self, outcome: TaskOutcome) -> MarkReport:
        report = apply_outcome(self.store, outcome,
                               half_life_days=self.config.mark_half_life_days)
        if outcome.verdict == "fail":
            self._failures.append(outcome)
            if len(self._failures) > self.config.max_failures:
                self._failures = self._failures[-self.config.max_failures:]
            self._save_state()
        return report

    # -- slow loop ------------------------------------------------------

    def gap_signals(self, pack: str) -> list[GapSignal]:
        by_topic: dict[str, list[TaskOutcome]] = {}
        for f in self._failures:
            if f.topic:                         # unlabeled excluded, not guessed
                by_topic.setdefault(f.topic, []).append(f)
        coverage = self.store.coverage(pack)
        # backoff is round-based: every sweep ages all of this pack's
        # counters, whether or not that topic has pending failures
        suppressed: set[str] = set()
        for key, rounds in list(self._backoff.items()):
            if key.startswith(f"{pack}:") and rounds > 0:
                self._backoff[key] = rounds - 1
                suppressed.add(key)
        signals: list[GapSignal] = []
        consumed_topics: set[str] = set()
        for topic, fails in sorted(by_topic.items()):
            if len(fails) < self.config.min_failures:
                continue
            key = f"{pack}:{topic}"
            if key in suppressed:
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
            consumed_topics.add(topic)
        if consumed_topics:
            # consumed failures produced their signal; they never re-signal
            self._failures = [f for f in self._failures
                              if f.topic not in consumed_topics]
        self._save_state()
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
            # time-decayed evidence, not lifetime counters: an entry helpful
            # 100 times last year but silent since decays toward the prior
            helpful, harmful = effective_counts(
                stored, now, self.config.mark_half_life_days)
            score = (helpful + 1.0) / (helpful + harmful + 2.0)
            if score > self.config.staleness_score_max:
                continue                        # old but still earning its keep
            age_days = (now - fetched).days
            signals.append(GapSignal(
                pack=pack, topic=stored.cand.topic, kind="staleness",
                evidence=f"{stored.cand.id}: sources {age_days}d old, "
                         f"decayed score {score:.2f} "
                         f"(decayed helpful={helpful:.1f}, "
                         f"harmful={harmful:.1f})"))
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
