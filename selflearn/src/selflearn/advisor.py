"""Next-best-action advisor: one prioritized, executable to-do list.

``selflearn next`` (and the wizard's status screen) call ``suggest_actions``
to turn the store's current state into concrete operator moves, ordered by
urgency:

1. quarantined candidates      — need a journaled human release
2. candidates awaiting gates   — run verification, then approve (also:
   a specialist not ready for self-improvement, from
   ``suggest_specialist_improvement``)
3. harmful published entries   — deprecate or refresh (marks say they hurt)
4. stale published entries     — sources aged and evidence faded; re-fetch
5. thin epistemic evidence and claimed-but-uncovered topics — probe or
   acquire before relying
6. missing embeddings          — retrieval is degraded to keyword matching
7. bounded improvement campaign — a ready specialist's next safe step
   (``suggest_specialist_improvement``)

Everything here is advisory-only, same rule as the learning module: the
advisor proposes commands, a human runs them. Nothing is executed and no
store state is mutated (staleness reuses ``Learner.staleness_signals``,
which is read-only; the backoff-mutating gap sweep is deliberately NOT
called from here).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from selflearn.evidence import decay_factor
from selflearn.learning.gaps import expected_free_energy_value
from selflearn.learning.marks import DEPRECATE_THRESHOLD
from selflearn.store.packstore import PackStore
from selflearn.specialist import SpecialistSpec

# A published entry is flagged as harmful on an unbroken losing streak
# (same threshold the learning loop auto-deprecates at), or on a low
# *decayed* evidence score once enough recent evidence exists — all
# through evidence.py, so the advisor can never disagree with the loop.
LOW_SCORE = 0.35
MIN_EVIDENCE = 4.0
# EFE prior for a coverage gap (knowledge missing) — matches the coverage
# weighting in expected_free_energy_value so gaps sort above weaker signals.
COVERAGE_GAP_EFE = 2.0


@dataclass(frozen=True)
class Suggestion:
    """One proposed operator move. ``command`` is empty when the action has
    no single CLI incantation (e.g. re-index with an embedding endpoint)."""

    priority: int          # 1 = most urgent; ties broken by efe then order
    action: str            # short imperative headline
    reason: str            # why the store's state implies this action
    command: str = ""      # copy-pasteable CLI command, when one exists
    efe: float = 0.0       # acquisition value (negative expected free energy);
    #                        higher floats up WITHIN a priority tier (§3.1)


def suggest_specialist_improvement(spec: SpecialistSpec,
                                   store: PackStore) -> Suggestion:
    """Describe the next safe improvement step without changing the store."""
    report = spec.assess_improvement(store)
    if not report.ready:
        return Suggestion(
            2,
            f"specialist {spec.name!r} is not ready for self-improvement",
            "; ".join(report.reasons),
        )
    policy = spec.improvement_policy
    assert policy is not None  # report.ready establishes the typed invariant
    return Suggestion(
        7,
        f"run a bounded improvement campaign for specialist {spec.name!r}",
        f"target the largest verified failure cluster; fit on "
        f"{report.fit_items} item(s), mark eligible only on "
        f"{report.validation_items} unseen validation item(s), and keep the "
        f"{report.test_items}-item sealed test for the final comparison; "
        f"stop at score {policy.target_validation_score:.2f}, after "
        f"{policy.max_iterations} iterations, or after "
        f"{policy.plateau_rounds} stagnant rounds",
    )


def suggest_actions(store: PackStore,
                    now: Optional[datetime] = None) -> list[Suggestion]:
    now = now or datetime.now(timezone.utc)
    s = str(store.root)
    out: list[Suggestion] = []
    packs = store.packs()
    if not packs:
        return [Suggestion(
            1, "seed or acquire your first pack",
            "the store has no packs yet; seed existing material (no model "
            "needed) or run the full acquisition pipeline",
            f"selflearn seed-kb <dir> --pack <pack> --store {s}   (or: "
            f"selflearn acquire \"search:<question>\" --pack <pack> "
            f"--topic <topic> --store {s} --workdir <w> "
            f"--endpoint <url> --model <id>)")]

    learner_broken = ""
    try:
        from selflearn.learning.gaps import Learner
        learner = Learner(store)
    except Exception as exc:          # corrupt learner-state.json etc.
        learner, learner_broken = None, str(exc)
    if learner_broken:
        out.append(Suggestion(
            1, "repair the store's learning state",
            f"learner state failed to load ({learner_broken}); staleness "
            "advice is unavailable until it is repaired",
            f"selflearn doctor --store {s} --fix"))

    for pack in packs:
        candidates = store.entries_for(pack, "candidate")
        quarantined = [e for e in candidates if e.cand.quarantined]
        pending = [e for e in candidates if not e.cand.quarantined]
        if quarantined:
            first = quarantined[0].cand
            out.append(Suggestion(
                1, f"review {len(quarantined)} quarantined candidate(s) "
                   f"in pack {pack!r}",
                f"quarantined entries need a journaled human release before "
                f"they can ever publish (first: {first.id}: "
                f"{first.quarantine_reason})",
                f"selflearn release {first.id} --store {s} "
                f"--reason '<why it is safe>' --by <you@example.com>"))
        if pending:
            out.append(Suggestion(
                2, f"verify {len(pending)} candidate(s) in pack {pack!r}",
                "candidates only become retrievable knowledge after "
                "verification plus approval",
                f"selflearn verify --pack {pack} --store {s}"))

        published = store.published(pack)
        for e in published:
            evidence = (e.helpful + e.harmful) * decay_factor(
                e.marks_updated_at, now)
            score = e.score_for("", now=now)
            streak = e.consecutive_harmful >= DEPRECATE_THRESHOLD
            low = evidence >= MIN_EVIDENCE and score < LOW_SCORE
            if streak or low:
                why = (f"{e.consecutive_harmful} consecutive harmful marks"
                       if streak else
                       f"evidence score {score:.2f} over "
                       f"{evidence:.0f} recent marks")
                out.append(Suggestion(
                    3, f"deprecate or refresh {e.cand.id}",
                    f"published entry is hurting tasks ({why})",
                    f"selflearn deprecate {e.cand.id} --store {s} "
                    f"--reason '<why>'"))

        if learner is not None:
            for sig in learner.staleness_signals(pack, now):
                out.append(Suggestion(
                    4, f"refresh stale knowledge in {pack!r}/{sig.topic}",
                    sig.evidence,
                    f"selflearn acquire \"search:{sig.topic}\" --pack {pack} "
                    f"--topic {sig.topic} --store {s} --workdir <w> "
                    f"--endpoint <url> --model <id>",
                    efe=expected_free_energy_value(sig)))
            # proactive epistemic signal (§3.1): strengthen thinly-evidenced
            # published knowledge BEFORE it fails — the acquisition loop's
            # only forward-looking suggestion
            for sig in learner.epistemic_signals(pack, now):
                out.append(Suggestion(
                    5, f"strengthen low-confidence knowledge in "
                       f"{pack!r}/{sig.topic}",
                    sig.evidence,
                    f"selflearn verify --pack {pack} --store {s}",
                    efe=expected_free_energy_value(sig)))

        cov = store.coverage(pack)
        for topic in sorted(t for t, v in cov.items() if v != "covered"):
            out.append(Suggestion(
                5, f"acquire claimed topic {topic!r} for pack {pack!r}",
                "the coverage map claims this topic but no published entry "
                "covers it",
                f"selflearn acquire \"search:{topic}\" --pack {pack} "
                f"--topic {topic} --store {s} --workdir <w> "
                f"--endpoint <url> --model <id>",
                efe=COVERAGE_GAP_EFE))

        unindexed = [e for e in published if not e.vector]
        if unindexed:
            out.append(Suggestion(
                6, f"add embeddings for {len(unindexed)} published "
                   f"entries in pack {pack!r}",
                "without vectors retrieval degrades to keyword matching "
                "(loudly); re-run acquisition/publish with "
                "--embedding-endpoint, or set vectors via the API"))

    if not out:
        out.append(Suggestion(
            9, "nothing urgent — smoke-test retrieval",
            "no quarantine, no waiting candidates, no harmful/stale entries "
            "and no coverage gaps detected",
            f"selflearn retrieve \"<question>\" --packs {' '.join(packs)} "
            f"--store {s}"))
    # priority tiers first (qualitatively different actions), then highest
    # expected-free-energy-reduction within a tier (§3.1), then discovery
    # order — a stable sort preserves the last for equal keys
    return sorted(out, key=lambda x: (x.priority, -x.efe))


def render_suggestions(suggestions: list[Suggestion]) -> str:
    lines = []
    for i, sug in enumerate(suggestions, 1):
        lines.append(f" {i}. (p{sug.priority}) {sug.action}")
        lines.append(f"    why: {sug.reason}")
        if sug.command:
            lines.append(f"    run: {sug.command}")
    return "\n".join(lines)
