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


class HashEmbedder:
    embedder_id = "hash-v1"

    def embed(self, texts):
        out = []
        for t in texts:
            v = [0.0] * 64
            for tok in re.findall(r"[a-z0-9]{3,}", t.lower()):
                v[int(hashlib.md5(tok.encode()).hexdigest(), 16) % 64] += 1.0
            n = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append(tuple(x / n for x in v))
        return out


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
