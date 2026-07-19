"""M6: gap detection, topic labeling, staleness, backoff, suite regression."""
import hashlib
import math
import re
from datetime import datetime, timezone

import pytest

from selflearn.contracts import (
    CandidateEntry,
    EntrySource,
    Probe,
    PublishDecision,
    TaskOutcome,
)
from selflearn.learning import (
    Learner,
    LearningConfig,
    check_regression,
    label_topic,
    snapshot_baseline,
)
from selflearn.retrieval import Retriever
from selflearn.store import PackStore, StoreError
from selflearn.verification.suite import SuiteResult, ProbeResult
from selflearn.testing import HashEmbedder

def src(fetched_at="2026-07-01T00:00:00Z"):
    return EntrySource(url="https://docs.example.org/x", fetched_at=fetched_at,
                       sha256="0" * 64, tier="official")


def publish(store, eid, body, topic, fetched_at="2026-07-01T00:00:00Z"):
    e = CandidateEntry(id=eid, pack="fastapi", kind="knowledge", body=body,
                       claims=(body.split(".")[0],), sources=(src(fetched_at),),
                       topic=topic)
    store.add_candidate(e)
    store.publish(e.id, PublishDecision(entry_id=e.id, publish=True,
                                        basis=("t",), identity_basis="m"))
    return e


def fail(topic, injected=(), implicated=(), task_id="t"):
    return TaskOutcome(task_id=task_id, task_type="code_edit", topic=topic,
                       verdict="fail", injected=tuple(injected),
                       implicated=tuple(implicated))


@pytest.fixture()
def store(tmp_path):
    s = PackStore(tmp_path)
    publish(s, "kn-f-lifespan", "Lifespan context manager replaces on_event "
            "handlers for startup shutdown.", "lifespan")
    s.claim_topics("fastapi", ["middleware"])       # claimed, never covered
    return s


def test_coverage_gap_for_claimed_uncovered_topic(store):
    learner = Learner(store)
    for i in range(2):
        learner.observe(fail("middleware", task_id=f"t{i}"))
    signals = learner.gap_signals("fastapi")
    assert len(signals) == 1
    assert signals[0].kind == "coverage" and signals[0].topic == "middleware"
    assert "claimed but not covered" in signals[0].evidence


def test_quality_gap_when_retrieval_happened(store):
    learner = Learner(store)
    for i in range(2):
        learner.observe(fail("lifespan", injected=["kn-f-lifespan"],
                             implicated=["kn-f-lifespan"], task_id=f"t{i}"))
    signals = learner.gap_signals("fastapi")
    assert signals[0].kind == "quality"
    assert "kn-f-lifespan" in signals[0].evidence


def test_unlabeled_outcomes_are_excluded_not_guessed(store):
    learner = Learner(store)
    for i in range(3):
        learner.observe(fail("", task_id=f"t{i}"))    # unlabeled bucket
    assert learner.gap_signals("fastapi") == []


def test_backoff_suppresses_even_fresh_failures(store):
    """Backoff is round-based: after a signal fires, the topic stays quiet
    for backoff_rounds sweeps even when NEW failures keep arriving."""
    learner = Learner(store, LearningConfig(backoff_rounds=2))
    for i in range(2):
        learner.observe(fail("middleware", task_id=f"t{i}"))
    assert learner.gap_signals("fastapi")           # fires (and consumes)
    for i in range(2):
        learner.observe(fail("middleware", task_id=f"n{i}"))
    assert learner.gap_signals("fastapi") == []     # suppressed round 1
    assert learner.gap_signals("fastapi") == []     # suppressed round 2
    assert learner.gap_signals("fastapi")           # fresh failures fire


def test_below_min_failures_no_signal(store):
    learner = Learner(store)
    learner.observe(fail("middleware"))
    assert learner.gap_signals("fastapi") == []


def test_staleness_needs_age_AND_decayed_score(store):
    old = "2025-01-01T00:00:00Z"
    now = datetime(2026, 7, 17, tzinfo=timezone.utc)
    publish(store, "kn-f-old-good", "Old but still earning helpful marks.",
            "oldtopic", fetched_at=old)
    publish(store, "kn-f-old-bad", "Old and no longer helping anyone.",
            "oldtopic", fetched_at=old)
    store.mark("kn-f-old-good", helpful=10.0)
    store.mark("kn-f-old-bad", harmful=3.0)
    learner = Learner(store)
    signals = learner.staleness_signals("fastapi", now=now)
    ids = " ".join(s.evidence for s in signals)
    assert "kn-f-old-bad" in ids and "kn-f-old-good" not in ids
    # fresh entries never stale regardless of score
    assert all("kn-f-lifespan" not in s.evidence for s in signals)


def test_suggestions_are_advisory_with_actions(store):
    learner = Learner(store)
    for i in range(2):
        learner.observe(fail("middleware", task_id=f"t{i}"))
    suggestions = learner.suggestions("fastapi")
    assert suggestions[0]["proposed_action"].startswith("propose acquisition")
    assert "never auto-run" in suggestions[0]["advisory"]


def test_learner_facade_still_marks_and_deprecates(store):
    learner = Learner(store)
    for i in range(3):
        report = learner.observe(fail("lifespan", injected=["kn-f-lifespan"],
                                      implicated=["kn-f-lifespan"],
                                      task_id=f"t{i}"))
    assert report.deprecated == ("kn-f-lifespan",)
    assert store.get("kn-f-lifespan").status == "deprecated"


def test_label_topic_deterministic_with_unlabeled_floor(store):
    retriever = Retriever(store, HashEmbedder())
    retriever.index("fastapi")
    assert label_topic(retriever, ["fastapi"],
                       "startup shutdown lifespan handler work") == "lifespan"
    assert label_topic(retriever, ["fastapi"], "zzz qqq unrelated") == ""


# -- durable state + recency decay (review findings, 2026-07-17) ------------

def test_learner_state_survives_restart(store):
    learner = Learner(store)
    learner.observe(fail("middleware", task_id="t0"))
    learner.observe(fail("middleware", task_id="t1"))
    assert learner.gap_signals("fastapi")            # fires, sets backoff
    # a fresh Learner over the same store resumes failures AND backoff
    reborn = Learner(store)
    assert reborn._backoff == {"fastapi:middleware": 2}
    assert reborn.gap_signals("fastapi") == []       # backoff persisted


def test_consumed_failures_never_resignal(store):
    """The review's re-signal bug: old failures must not fire again every
    time backoff expires — a signal consumes its evidence."""
    learner = Learner(store, LearningConfig(backoff_rounds=1))
    for i in range(2):
        learner.observe(fail("middleware", task_id=f"t{i}"))
    assert learner.gap_signals("fastapi")            # fires + consumes
    for _ in range(4):                               # long after backoff expiry
        assert learner.gap_signals("fastapi") == []
    # fresh failures start a new cycle (one suppressed round, then fire)
    for i in range(2):
        learner.observe(fail("middleware", task_id=f"n{i}"))
    assert learner.gap_signals("fastapi")


def test_failure_cap_is_fifo(store):
    learner = Learner(store, LearningConfig(max_failures=3))
    for i in range(5):
        learner.observe(fail("middleware", task_id=f"t{i}"))
    assert [f.task_id for f in learner._failures] == ["t2", "t3", "t4"]


def test_decay_factor_half_life_math():
    from selflearn.learning import decay_factor

    a_year_ago = datetime(2025, 7, 17, tzinfo=timezone.utc)
    now = datetime(2026, 7, 17, tzinfo=timezone.utc)
    assert decay_factor(a_year_ago.isoformat(), now, 90.0) == \
        pytest.approx(0.5 ** (365 / 90), rel=1e-3)
    assert decay_factor("", now) == 1.0                    # never marked
    assert decay_factor("not-a-date", now) == 1.0          # unparseable
    assert decay_factor(now.isoformat(), now) == 1.0       # no time passed


def test_apply_outcome_decay_with_explicit_clock(store):
    """helpful=100 a year ago decays to ~6 (half-life 90d): seven recent
    harmful marks deprecate the entry — without decay it would take 101."""
    from selflearn.learning import apply_outcome

    a_year_ago = datetime(2025, 7, 17, tzinfo=timezone.utc)
    now = datetime(2026, 7, 17, tzinfo=timezone.utc)
    store.mark("kn-f-lifespan", helpful=100.0, now_iso=a_year_ago.isoformat())
    report = None
    for i in range(7):
        report = apply_outcome(store, TaskOutcome(
            task_id=f"t{i}", task_type="code_edit", topic="lifespan",
            verdict="fail", injected=("kn-f-lifespan",),
            implicated=("kn-f-lifespan",)), now=now)
        if report.deprecated:
            break
    stored = store.get("kn-f-lifespan")
    assert stored.status == "deprecated"          # recent evidence won
    assert report.deprecated == ("kn-f-lifespan",)
    assert stored.helpful < 10.0                  # lifetime 100 decayed away
    assert stored.harmful <= 7.0                  # nowhere near 101


def test_marks_timestamp_survives_reload(store, tmp_path):
    from selflearn.learning import apply_outcome

    now = datetime(2026, 7, 17, tzinfo=timezone.utc)
    apply_outcome(store, TaskOutcome(
        task_id="t", task_type="code_edit", topic="lifespan", verdict="pass",
        injected=("kn-f-lifespan",)), now=now)
    reloaded = PackStore(store.root)
    assert reloaded.get("kn-f-lifespan").marks_updated_at == now.isoformat()


def test_staleness_uses_decayed_score(store):
    """Old sources + helpful history that decayed + recent harmful signal:
    decayed score fires staleness where the lifetime score (~0.91) never
    would — the review's 'no time decay, only ratio shift' fix."""
    from selflearn.learning import decay_factor

    old = "2025-01-01T00:00:00Z"
    a_year_ago = datetime(2025, 7, 17, tzinfo=timezone.utc)
    now = datetime(2026, 7, 17, tzinfo=timezone.utc)
    publish(store, "kn-f-was-loved", "Once loved, now failing.", "aging",
            fetched_at=old)
    store.mark("kn-f-was-loved", helpful=100.0, now_iso=a_year_ago.isoformat())
    # recent harmful evidence arrives now: decay the old 100 first
    factor = decay_factor(a_year_ago.isoformat(), now)      # ≈ 0.06
    store.mark("kn-f-was-loved", harmful=8.0, decay=factor,
               now_iso=now.isoformat())
    learner = Learner(store)
    signals = learner.staleness_signals("fastapi", now=now)
    assert any("kn-f-was-loved" in s.evidence for s in signals)
    # lifetime counters would read 101/111 ≈ 0.91 — far above the 0.45 bar


# -- per-task-type granularity (review finding 4) ---------------------------

def test_contract_rejects_incoherent_attribution():
    from selflearn.contracts import ContractError

    with pytest.raises(ContractError, match="applied .* subset"):
        TaskOutcome(task_id="t", task_type="code_edit", topic="x",
                    verdict="pass", injected=("a",), applied=("b",))
    with pytest.raises(ContractError, match="no influence"):
        TaskOutcome(task_id="t", task_type="code_edit", topic="x",
                    verdict="fail", injected=(), implicated=("ghost",))


def test_marks_land_in_task_buckets_and_persist(store):
    learner = Learner(store)
    learner.observe(TaskOutcome(
        task_id="t1", task_type="code_edit", topic="lifespan", verdict="pass",
        injected=("kn-f-lifespan",), applied=("kn-f-lifespan",)))
    learner.observe(TaskOutcome(
        task_id="t2", task_type="reasoning", topic="lifespan", verdict="fail",
        injected=("kn-f-lifespan",), implicated=("kn-f-lifespan",)))
    stored = store.get("kn-f-lifespan")
    # approx: consecutive wall-clock marks decay by a sliver of elapsed time
    assert stored.marks_by_task["code_edit"][0] == pytest.approx(2.0, abs=1e-3)
    assert stored.marks_by_task["reasoning"][1] == pytest.approx(1.0, abs=1e-3)
    reloaded = PackStore(store.root)
    assert reloaded.get("kn-f-lifespan").marks_by_task == stored.marks_by_task


def test_score_for_learns_helps_A_misleads_B(store):
    """The review's exact nuance: helpful for task type A, harmful for B."""
    for i in range(4):
        store.mark("kn-f-lifespan", helpful=1.0, task_type="code_edit")
    for i in range(4):
        store.mark("kn-f-lifespan", harmful=1.0, task_type="reasoning")
    stored = store.get("kn-f-lifespan")
    assert stored.score_for("code_edit") > stored.score       # boosted for A
    assert stored.score_for("reasoning") < stored.score       # sunk for B
    assert stored.score_for("") == stored.score               # no type -> global
    assert stored.score_for("never_seen") == stored.score     # no evidence -> global


def test_retrieval_ranks_per_task_type(store):
    publish(store, "kn-f-alt", "Lifespan context manager replaces on_event "
            "handlers for startup shutdown.", "lifespan")   # near-identical body
    for _ in range(6):
        store.mark("kn-f-lifespan", helpful=1.0, task_type="code_edit")
        store.mark("kn-f-lifespan", harmful=1.0, task_type="reasoning")
    retriever = Retriever(store, HashEmbedder())
    retriever.index("fastapi")
    query = "lifespan startup shutdown handlers"
    top_code = retriever.retrieve(["fastapi"], query, task_type="code_edit")
    top_reason = retriever.retrieve(["fastapi"], query, task_type="reasoning")
    assert top_code[0].entry_id == "kn-f-lifespan"      # helps for A: first
    assert top_reason[0].entry_id == "kn-f-alt"         # misleads for B: sunk


def test_decay_applies_to_task_buckets_too(store):
    a_year_ago = datetime(2025, 7, 17, tzinfo=timezone.utc)
    now = datetime(2026, 7, 17, tzinfo=timezone.utc)
    store.mark("kn-f-lifespan", helpful=10.0, task_type="code_edit",
               now_iso=a_year_ago.isoformat())
    from selflearn.learning import apply_outcome
    apply_outcome(store, TaskOutcome(
        task_id="t", task_type="code_edit", topic="lifespan", verdict="pass",
        injected=("kn-f-lifespan",)), now=now)
    bucket = store.get("kn-f-lifespan").marks_by_task["code_edit"]
    assert bucket[0] < 2.0        # 10 decayed to ~0.6, +1 new


# -- suite regression -------------------------------------------------------

def suite(model_id, passed, total, pack="fastapi"):
    s = SuiteResult(model_id=model_id, pack=pack, injected=True)
    s.results = [ProbeResult(f"p{i}", "recall", i < passed)
                 for i in range(total)]
    return s


def test_regression_snapshot_and_check(store):
    snapshot_baseline(store, "fastapi", suite("m1", 8, 10))
    ok = check_regression(store, "fastapi", suite("m1", 9, 10))
    assert ok.ok() and ok.delta == pytest.approx(0.1)
    bad = check_regression(store, "fastapi", suite("m1", 5, 10))
    assert not bad.ok() and "REGRESSION" in bad.summary()


def test_regression_loud_paths(store):
    with pytest.raises(StoreError, match="no suite baseline"):
        check_regression(store, "fastapi", suite("m1", 1, 2))
    with pytest.raises(StoreError, match="empty suite"):
        snapshot_baseline(store, "fastapi", suite("m1", 0, 0))
    snapshot_baseline(store, "fastapi", suite("m1", 2, 2))
    with pytest.raises(StoreError, match="same model"):
        check_regression(store, "fastapi", suite("OTHER", 2, 2))


def test_baseline_reason_distinguishes_missing_from_no_injection(store):
    """META-24 finding 3: readiness said 'has no frozen suite baseline' even
    when a baseline existed but was a without-injection run (which
    snapshot_baseline happily writes); the two cases now read differently,
    and the baseline path comes from regression.BASELINE_FILE."""
    from selflearn.learning.improvement import _baseline_issue

    assert _baseline_issue(store, "fastapi") == "has no frozen suite baseline"
    no_inj = SuiteResult(model_id="m1", pack="fastapi", injected=False)
    no_inj.results = [ProbeResult("p0", "recall", True)]
    snapshot_baseline(store, "fastapi", no_inj)
    assert _baseline_issue(store, "fastapi") == \
        "suite baseline is not a with-injection run"
    snapshot_baseline(store, "fastapi", suite("m1", 1, 1))
    assert _baseline_issue(store, "fastapi") == ""


# ---------------------------------------------------------------------------
# Phase 1/2 — posterior uncertainty and epistemic (EFE) acquisition
# (design note docs/selflearn-learning-module-improvements.md §3.1/§3.2)
# ---------------------------------------------------------------------------

def test_laplace_variance_math():
    from selflearn.evidence import laplace_variance, laplace_score
    # no evidence -> maximal Beta(1,1) variance = 1/12
    assert laplace_variance(0.0, 0.0) == pytest.approx(1.0 / 12.0)
    # symmetric in helpful/harmful (same as the mean sitting at 0.5)
    assert laplace_variance(3.0, 1.0) == laplace_variance(1.0, 3.0)
    # more evidence -> tighter posterior
    assert laplace_variance(20.0, 20.0) < laplace_variance(1.0, 1.0)
    # mean unchanged by the new function
    assert laplace_score(4.0, 0.0) == pytest.approx(5.0 / 6.0)


def test_uncertainty_for_decays_and_conditions(store):
    e = publish(store, "kn-f-u", "Body.", "utopic")
    stored = store.get("kn-f-u")
    fresh = stored.uncertainty_for()                     # no marks -> ~0.083
    assert fresh == pytest.approx(1.0 / 12.0)
    store.mark("kn-f-u", helpful=8.0, now_iso="2026-07-17T00:00:00Z")
    stored = store.get("kn-f-u")
    assert stored.uncertainty_for() < fresh              # evidence tightened it
    # stale evidence widens the posterior again (decay) — the intended signal
    later = datetime(2027, 7, 18, tzinfo=timezone.utc)
    assert stored.uncertainty_for(now=later) > stored.uncertainty_for()
    # a task_type bucket with no marks is more uncertain than the global
    assert stored.uncertainty_for(task_type="unseen") >= stored.uncertainty_for()


def test_epistemic_signals_flag_thin_knowledge_only(store):
    publish(store, "kn-f-thin", "Thin.", "thin-topic")
    well = publish(store, "kn-f-well", "Well.", "well-topic")
    for _ in range(12):
        store.mark("kn-f-well", helpful=1.0, now_iso="2026-07-17T00:00:00Z")
    learner = Learner(store)
    before = (store.root / "learner-state.json").read_bytes() \
        if (store.root / "learner-state.json").exists() else None
    sigs = learner.epistemic_signals("fastapi")
    topics = {s.topic for s in sigs}
    assert "thin-topic" in topics and "well-topic" not in topics
    assert all(s.kind == "uncertainty" for s in sigs)
    # read-only: viewing epistemic advice must not write learner state
    after = (store.root / "learner-state.json").read_bytes() \
        if (store.root / "learner-state.json").exists() else None
    assert after == before


def test_expected_free_energy_orders_kinds():
    from selflearn.contracts import GapSignal
    from selflearn.learning.gaps import expected_free_energy_value as efe
    cov = GapSignal(pack="p", topic="t", kind="coverage",
                    evidence="5 verified failures")
    qual = GapSignal(pack="p", topic="t", kind="quality",
                     evidence="5 verified failures despite retrieval")
    unc = GapSignal(pack="p", topic="t", kind="uncertainty",
                    evidence="posterior variance 0.08")
    # missing knowledge (coverage) beats retrieved-but-failing (quality),
    # which beats a purely proactive uncertainty nudge
    assert efe(cov) > efe(qual) > efe(unc)
    # more cited failures raise pragmatic value
    few = GapSignal(pack="p", topic="t", kind="coverage",
                    evidence="2 verified failures")
    assert efe(cov) > efe(few)


def test_suggestions_efe_ranked(store):
    """suggestions() is deliberately NOT read-only — it runs the gap sweep,
    which ages backoff counters and saves learner state (use
    ``staleness_signals``/``epistemic_signals`` for read-only views)."""
    # a covered-but-thin topic (epistemic) plus a claimed-uncovered topic
    publish(store, "kn-f-cov", "Covered.", "covered")
    store.claim_topics("fastapi", ["missing"])
    learner = Learner(store)
    out = learner.suggestions("fastapi")
    assert out and all("efe_value" in d for d in out)
    # sorted by descending EFE value
    vals = [d["efe_value"] for d in out]
    assert vals == sorted(vals, reverse=True)


def test_staleness_efe_never_boosted_by_age(store):
    """META-24 finding 1: _leading_int read the staleness evidence's leading
    age-in-days (always >=180) as a failure count, so every staleness signal
    got the max 2x pragmatic boost (EFE 1.40) and outranked quality signals
    (1.38) — inverting the documented quality-high > staleness-moderate
    ordering. The failure-count boost now applies only to coverage/quality."""
    from selflearn.contracts import GapSignal
    from selflearn.learning.gaps import expected_free_energy_value as efe

    stale = GapSignal(pack="p", topic="t", kind="staleness",
                      evidence="kn-x: sources 320d old, evidence score 0.20 "
                               "(decayed helpful=0.5, harmful=2.0)")
    qual = GapSignal(pack="p", topic="t", kind="quality",
                     evidence="2 verified failures despite retrieval; "
                              "implicated: ['kn-x']")
    assert efe(qual) > efe(stale)          # documented ordering restored
    ancient = GapSignal(pack="p", topic="t", kind="staleness",
                        evidence="kn-x: sources 9999d old, evidence "
                                 "score 0.20 (decayed helpful=0.5, "
                                 "harmful=2.0)")
    assert efe(stale) == efe(ancient)      # staleness value is age-independent
    assert efe(stale) == pytest.approx(0.9)   # 0.5 pragmatic + 0.4 epistemic


def test_covered_topic_with_no_retrieval_says_so(store):
    """META-24 finding 2: the covered-but-nothing-retrieved coverage signal
    used to misprint 'claimed but not covered' (and the 'not in coverage
    map' branch was unreachable — the join requires topic membership)."""
    learner = Learner(store)
    for i in range(2):
        learner.observe(fail("lifespan", task_id=f"t{i}"))   # no injection
    signals = learner.gap_signals("fastapi")
    assert len(signals) == 1 and signals[0].kind == "coverage"
    assert "covered but nothing was retrieved" in signals[0].evidence
    assert "claimed but not covered" not in signals[0].evidence


def test_min_validation_gain_gates_eligibility():
    """META-24 finding 5: the min_validation_gain eligibility gate was
    untested (every existing trial test used the 0.0 default)."""
    from selflearn.learning import (
        EvaluationCriterion,
        EvaluationItemResult,
        EvaluationSplits,
        ExpertExample,
        FailureCluster,
        ImprovementPolicy,
        ImprovementTrial,
        evaluate_improvement_trial,
    )

    def policy(gate):
        crit = EvaluationCriterion(
            id="c", description="label matches", failure_mode="wrong",
            check_kind="deterministic", probe_ids=("p",),
            anchors=("exact",), approved_by="expert@example.org")
        return ImprovementPolicy(
            domain_expert="expert@example.org",
            optimizer_identity="opt", evaluator_identity="eval",
            criteria=(crit,),
            expert_examples=(ExpertExample(
                id="e", criterion_id="c", expected="x", rationale="r"),),
            splits=EvaluationSplits(fit=("f1",), validation=("v1", "v2"),
                                    test=("t1",)),
            target_validation_score=1.0, max_iterations=10,
            plateau_rounds=10, min_validation_gain=gate)

    def result(item, passed):
        return EvaluationItemResult(
            item_id=item, passed=passed, evidence="frozen evidence",
            evaluator_identity="eval",
            failure_mode="" if passed else "wrong")

    dominant = FailureCluster(topic="t", failure_mode="wrong", count=2,
                              task_ids=("a", "b"))
    trial = ImprovementTrial(
        iteration=1, target_cluster=dominant.id, evaluator_identity="eval",
        fit_results=(result("f1", True),),
        validation_results=(result("v1", True), result("v2", False)))
    best = (result("v1", False), result("v2", False))    # gain = +0.5
    below = evaluate_improvement_trial(
        policy(0.6), trial, dominant_cluster=dominant,
        best_validation_results=best, stagnant_rounds=0)
    assert not below.eligible and below.stagnant_rounds == 1
    met = evaluate_improvement_trial(
        policy(0.5), trial, dominant_cluster=dominant,
        best_validation_results=best, stagnant_rounds=0)
    assert met.eligible and met.stagnant_rounds == 0
