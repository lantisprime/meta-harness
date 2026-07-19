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
import warnings
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from selflearn.contracts import ContractError, GapSignal, TaskOutcome
from selflearn.evidence import MARK_HALF_LIFE_DAYS, laplace_score, parse_iso
from selflearn.learning.marks import MarkReport, apply_outcome, effective_counts
from selflearn.learning.improvement import FailureCluster, cluster_failures
from selflearn.store.packstore import PackStore

ACTIONS = {
    "coverage": "propose acquisition run: acquire the topic from reputable "
                "sources (knowledge missing)",
    "quality": "propose amendment run: re-verify/amend the implicated "
               "entries (knowledge not working)",
    "staleness": "propose refresh run: re-fetch sources and regenerate the "
                 "entry (knowledge aging)",
    "uncertainty": "propose probe/strengthen run: generate evals or gather "
                   "corroboration for thinly-evidenced published knowledge "
                   "before it is relied on (epistemic value)",
}


@dataclass(frozen=True)
class LearningConfig:
    min_failures: int = 2            # failures per topic before a signal
    backoff_rounds: int = 2          # suppressed signal rounds after firing
    staleness_max_age_days: int = 180
    staleness_score_max: float = 0.45   # only aging entries that also stopped helping
    mark_half_life_days: float = MARK_HALF_LIFE_DAYS   # recency decay on marks
    max_failures: int = 500          # FIFO cap on retained failure evidence
    # epistemic gap (design note §3.1/§3.2): a published entry whose decayed
    # Beta posterior variance exceeds this is "we serve it but barely know if
    # it works" — proactively probe it. Beta(1,1) [no marks] is ~0.083, and a
    # net ~3 marks lands near 0.038, so 0.04 flags roughly-untested knowledge.
    uncertainty_min: float = 0.04


def parse_state_data(data) -> tuple[list, dict[str, int]]:
    """Structural shape check for learner-state.json content: returns
    ``(raw_failure_records, backoff)`` or raises ``ValueError`` on any shape
    the Learner cannot load. The doctor validates through this same
    function, so everything that would brick ``Learner.__init__`` is
    detectable (and repairable) — not just invalid JSON. Per-record
    contract tolerance on the failures stays in ``Learner._load_state``."""
    if not isinstance(data, dict):
        raise ValueError("learner state is not a JSON object")
    backoff_raw = data.get("backoff", {})
    if not isinstance(backoff_raw, dict):
        raise ValueError("'backoff' is not an object")
    try:
        backoff = {str(k): int(v) for k, v in backoff_raw.items()}
    except (TypeError, ValueError):
        raise ValueError("'backoff' values are not integers")
    failures = data.get("failures", [])
    if not isinstance(failures, list):
        raise ValueError("'failures' is not a list")
    return failures, backoff


def expected_free_energy_value(signal: GapSignal) -> float:
    """The value of acting on a gap signal, as negative expected free energy
    (design note §3.1): higher = acquire sooner. G = pragmatic + epistemic.

    Deterministic and cheap — a coarse ordering prior over signals, not a
    literal Friston-style planner (the design note is explicit that full EFE
    with a transition model is out of scope). Pragmatic value approximates
    expected task-failure reduction from the signal's own evidence; epistemic
    value is a fixed per-kind prior on how much acting reduces uncertainty:

    - coverage : knowledge is missing -> highest pragmatic + epistemic
    - quality  : retrieved but failing -> high pragmatic, lower epistemic
    - staleness: aged and fading      -> moderate
    - uncertainty: proactive, no failures yet -> pure epistemic
    """
    pragmatic = {"coverage": 1.0, "quality": 0.9,
                 "staleness": 0.5, "uncertainty": 0.0}.get(signal.kind, 0.3)
    epistemic = {"coverage": 1.0, "quality": 0.3,
                 "staleness": 0.4, "uncertainty": 0.6}.get(signal.kind, 0.3)
    if signal.kind in ("coverage", "quality"):
        # more cited failures => more expected pragmatic payoff, saturating.
        # Only these kinds lead their evidence with a failure count;
        # staleness leads with age-in-days and uncertainty with a variance,
        # so boosting them here would invert the documented ordering.
        n = _leading_int(signal.evidence)
        pragmatic *= 1.0 + min(n, 10) / 10.0
    return pragmatic + epistemic


def _leading_int(text: str) -> int:
    """First integer in a signal's evidence string (its failure count when
    present), else 0 — used only to weight pragmatic value."""
    num = ""
    for ch in text:
        if ch.isdigit():
            num += ch
        elif num:
            break
    return int(num) if num else 0


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
        failures_raw, self._backoff = parse_state_data(
            json.loads(self.state_path.read_text()))
        self._failures = []
        skipped = 0
        for f in failures_raw:
            # Migration tolerance (review finding): records persisted under
            # an older contract must not brick the learning loop — skip
            # individually-invalid records loudly instead of dying in
            # __init__ on the whole file.
            try:
                self._failures.append(TaskOutcome(
                    task_id=f["task_id"], task_type=f["task_type"],
                    topic=f["topic"], verdict=f["verdict"],
                    injected=tuple(f.get("injected", [])),
                    applied=tuple(f.get("applied", [])),
                    failure_mode=f.get("failure_mode", ""),
                    implicated=tuple(f.get("implicated", [])),
                    step_id=f.get("step_id", ""),
                    seeded_by=tuple(f.get("seeded_by", []))))
            except (ContractError, KeyError, TypeError) as exc:
                skipped += 1
                if skipped == 1:
                    warnings.warn(
                        f"learner-state {self.state_path}: skipping records "
                        f"invalid under the current contract (first: {exc}); "
                        "they stay on disk until the next state save",
                        stacklevel=2)
        # Deliberately no rewrite here: loading must be read-only, because
        # merely *viewing* advice ('selflearn next', the wizard status
        # screen) constructs a Learner — a load-time rewrite would make
        # looking at the store destroy the skipped records. They are
        # dropped only when a real mutation (observe/gap sweep) saves.

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

    def failure_clusters(self, pack: str) -> tuple[FailureCluster, ...]:
        """Return pack-scoped verified failures, largest pattern first.

        This is a read-only view: unlike ``gap_signals`` it neither consumes
        failures nor advances backoff counters.
        """
        coverage = self.store.coverage(pack)
        return cluster_failures(
            failure for failure in self._failures
            if failure.topic in coverage
        )

    def gap_signals(self, pack: str) -> list[GapSignal]:
        coverage = self.store.coverage(pack)
        by_topic: dict[str, list[TaskOutcome]] = {}
        for f in self._failures:
            # Pack-scoped join (review finding): only topics this pack's
            # coverage map owns. A topic owned by another pack is left for
            # that pack's sweep; a topic owned by no pack is excluded like
            # an unlabeled outcome — never attributed by sweep order.
            if f.topic and f.topic in coverage:
                by_topic.setdefault(f.topic, []).append(f)
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
            covered = coverage.get(topic) == "covered"
            if not covered or not retrieved_any:
                # the join above guarantees topic is in this pack's coverage
                # map, so the only cases are claimed-but-not-covered and
                # covered-but-retrieval-surfaced-nothing
                if not covered:
                    detail = "claimed but not covered"
                    if not retrieved_any:
                        detail += "; nothing was retrieved"
                else:
                    detail = "covered but nothing was retrieved"
                signals.append(GapSignal(
                    pack=pack, topic=topic, kind="coverage",
                    evidence=f"{len(fails)} verified failures; topic {detail}"))
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
        if consumed_topics or suppressed:
            self._save_state()      # only when state actually changed
        return signals

    def staleness_signals(self, pack: str,
                          now: Optional[datetime] = None) -> list[GapSignal]:
        now = now or datetime.now(timezone.utc)
        horizon = now - timedelta(days=self.config.staleness_max_age_days)
        signals = []
        for stored in self.store.published(pack):
            fetched = parse_iso(stored.cand.sources[0].fetched_at)
            if fetched is None or fetched > horizon:
                continue
            # min(lifetime, decayed): decayed alone converges to the 0.5
            # prior under silence, silently un-flagging historically bad
            # entries (review finding); lifetime alone never forgets. The
            # minimum keeps both failure modes covered.
            helpful, harmful = effective_counts(
                stored, now, self.config.mark_half_life_days)
            score = min(stored.score, laplace_score(helpful, harmful))
            if score > self.config.staleness_score_max:
                continue                        # old but still earning its keep
            age_days = (now - fetched).days
            signals.append(GapSignal(
                pack=pack, topic=stored.cand.topic, kind="staleness",
                evidence=f"{stored.cand.id}: sources {age_days}d old, "
                         f"evidence score {score:.2f} "
                         f"(decayed helpful={helpful:.1f}, "
                         f"harmful={harmful:.1f})"))
        return signals

    def epistemic_signals(self, pack: str,
                          now: Optional[datetime] = None) -> list[GapSignal]:
        """Proactive, uncertainty-seeking signal (design note §3.1/§3.2):
        published topics we serve on thin evidence, surfaced BEFORE they fail.

        This is the active-inference epistemic term made concrete — the slow
        loop's only forward-looking signal (coverage/quality/staleness are all
        reactive to failures or age). Read-only: like ``staleness_signals`` it
        mutates no state and no backoff, so merely viewing advice is safe. One
        signal per topic, so a fresh unvalidated pack yields a bounded list."""
        now = now or datetime.now(timezone.utc)
        worst: dict[str, tuple[float, str]] = {}
        for stored in self.store.published(pack):
            u = stored.uncertainty_for(now=now,
                                       half_life_days=self.config.mark_half_life_days)
            if u < self.config.uncertainty_min:
                continue
            topic = stored.cand.topic
            if topic not in worst or u > worst[topic][0]:
                worst[topic] = (u, stored.cand.id)
        signals: list[GapSignal] = []
        for topic, (u, entry_id) in sorted(worst.items()):
            signals.append(GapSignal(
                pack=pack, topic=topic, kind="uncertainty",
                evidence=f"published knowledge in {topic!r} is thinly "
                         f"evidenced (posterior variance {u:.3f}, e.g. "
                         f"{entry_id}); probe or corroborate before relying"))
        return signals

    def suggestions(self, pack: str,
                    now: Optional[datetime] = None) -> list[dict]:
        """Advisory-only proposals for the host's console: what to run and
        why, EFE-ranked (design note §3.1). The host NEVER auto-runs these —
        a human starts acquisition."""
        signals = (self.gap_signals(pack)
                   + self.staleness_signals(pack, now)
                   + self.epistemic_signals(pack, now))
        out = []
        for sig in signals:
            out.append({"pack": sig.pack, "topic": sig.topic,
                        "kind": sig.kind, "evidence": sig.evidence,
                        "proposed_action": ACTIONS[sig.kind],
                        "efe_value": round(expected_free_energy_value(sig), 4),
                        "advisory": "requires human approval; never auto-run"})
        # highest expected free energy reduction first (design note §3.1)
        out.sort(key=lambda d: d["efe_value"], reverse=True)
        return out

