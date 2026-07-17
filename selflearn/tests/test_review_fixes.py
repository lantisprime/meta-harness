"""Regression tests for the 2026-07-17 independent code review (top 10)."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from selflearn.contracts import (
    CandidateEntry,
    ContractError,
    EntrySource,
    PublishDecision,
    TaskOutcome,
    registrable_domain,
)
from selflearn.learning import Learner, apply_outcome
from selflearn.store import PackStore
from selflearn.verification import Verifier

SRC = EntrySource(url="https://docs.example.org/x", fetched_at="t",
                  sha256="0" * 64, tier="official")


def publish(store, eid, body="Lifespan replaces on_event.", topic="lifespan",
            pack="fastapi"):
    e = CandidateEntry(id=eid, pack=pack, kind="knowledge", body=body,
                       claims=(body.split(".")[0],), sources=(SRC,), topic=topic)
    store.add_candidate(e)
    store.publish(eid, PublishDecision(entry_id=eid, publish=True,
                                       basis=("t",), identity_basis="m"))
    return e


# -- F1: plan-level marks via seeded_by (finding 1) --------------------------

def test_seeded_by_enables_plan_level_marks(tmp_path):
    store = PackStore(tmp_path)
    publish(store, "wf-tdd", body="TDD workflow.", topic="tdd")
    # failing step implicates the plan-seeding entry (never injected)
    report = apply_outcome(store, TaskOutcome(
        task_id="t1", task_type="code_edit", topic="tdd", verdict="fail",
        injected=(), implicated=("wf-tdd",), seeded_by=("wf-tdd",)))
    assert report.harmful_marked == ("wf-tdd",)
    # verified completion credits the seeding entry
    report = apply_outcome(store, TaskOutcome(
        task_id="t2", task_type="code_edit", topic="tdd", verdict="pass",
        injected=(), seeded_by=("wf-tdd",)))
    assert report.helpful_marked == ("wf-tdd",)
    # ghosts (neither injected nor seeding) still rejected
    with pytest.raises(ContractError, match="no influence"):
        TaskOutcome(task_id="t3", task_type="code_edit", topic="tdd",
                    verdict="fail", injected=(), implicated=("ghost",))


# -- F2: learner-state migration tolerance (finding 2) -----------------------

def test_stale_learner_state_skips_invalid_records_loudly(tmp_path):
    store = PackStore(tmp_path)
    state = {
        "backoff": {"fastapi:x": 1},
        "failures": [
            # legal under the pre-validation contract, illegal now
            {"task_id": "old", "task_type": "code_edit", "topic": "x",
             "verdict": "fail", "injected": [], "implicated": ["kn-ghost"]},
            # still-valid record must survive
            {"task_id": "ok", "task_type": "code_edit", "topic": "x",
             "verdict": "fail", "injected": ["kn-a"], "implicated": ["kn-a"]},
        ]}
    (store.root / "learner-state.json").write_text(json.dumps(state))
    with pytest.warns(UserWarning, match="skipping records"):
        learner = Learner(store)
    assert [f.task_id for f in learner._failures] == ["ok"]
    assert learner._backoff == {"fastapi:x": 1}
    # rewritten so the warning fires once
    Learner(store)   # no warning expected — pytest.warns above scoped it


# -- F3: lazy re-index on retrieve (finding 3) -------------------------------

def test_publish_without_vector_lazily_indexes(tmp_path):
    from selflearn.retrieval import Retriever
    from selflearn.testing import HashEmbedder

    store = PackStore(tmp_path)
    publish(store, "kn-late", body="Lifespan startup shutdown handlers.")
    retriever = Retriever(store, HashEmbedder())
    got = retriever.retrieve(["fastapi"], "lifespan startup")
    assert got and got[0].entry_id == "kn-late"
    assert store.get("kn-late").embedder_id == "hash-v1"   # vector persisted


# -- F4: jail escape (finding 4) ---------------------------------------------

def test_workdir_jail_blocks_sibling_prefix(tmp_path):
    from selflearn.acquisition import AcquireContext, AcquisitionError

    ctx = AcquireContext(workdir=tmp_path / "jail")
    with pytest.raises(AcquisitionError, match="escapes"):
        ctx.artifact_path("../jail-escape/steal.txt")
    assert not (tmp_path / "jail-escape").exists()


# -- F5: domain normalization (finding 5) ------------------------------------

def test_registrable_domain_handles_w_domains_and_www():
    assert registrable_domain("https://www.web.dev/x") == "web.dev"
    assert registrable_domain("https://www.wwf.org/a") == "wwf.org"
    assert registrable_domain("https://example.org:8443/x") == "example.org"


def test_www_and_bare_are_one_domain_for_corroboration():
    e = CandidateEntry(
        id="kn-x", pack="p", kind="knowledge", body="b", claims=("c",),
        topic="t",
        sources=(EntrySource(url="https://www.sketchy.blog/a", fetched_at="t",
                             sha256="0" * 64, tier="unknown"),
                 EntrySource(url="https://sketchy.blog/b", fetched_at="t",
                             sha256="0" * 64, tier="unknown")))
    assert e.independent_domains() == {"sketchy.blog"}
    report = Verifier().verify(e)
    assert not report.ok       # one site cannot self-corroborate


def test_tier_lookup_normalizes_both_sides():
    from selflearn.acquisition import ReputabilityPolicy

    policy = ReputabilityPolicy(official=frozenset({"www.example.com",
                                                    "web.dev"}))
    assert policy.tier_for("https://example.com/docs") == "official"
    assert policy.tier_for("https://www.web.dev/learn") == "official"


# -- F6: deprecation streak at any cadence (finding 6) -----------------------

def test_slow_cadence_harmful_marks_still_deprecate(tmp_path):
    store = PackStore(tmp_path)
    publish(store, "kn-slow")
    t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    report = None
    for i in range(3):    # 60-day spacing: decayed counter plateaus < 3
        report = apply_outcome(store, TaskOutcome(
            task_id=f"t{i}", task_type="code_edit", topic="lifespan",
            verdict="fail", injected=("kn-slow",), implicated=("kn-slow",)),
            now=t0 + timedelta(days=60 * i))
    assert store.get("kn-slow").status == "deprecated"
    assert report.deprecated == ("kn-slow",)
    assert store.get("kn-slow").harmful < 3.0    # decayed — streak decided


def test_helpful_mark_resets_the_streak(tmp_path):
    store = PackStore(tmp_path)
    publish(store, "kn-mixed")
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i, verdict in enumerate(["fail", "fail", "pass", "fail"]):
        apply_outcome(store, TaskOutcome(
            task_id=f"t{i}", task_type="code_edit", topic="lifespan",
            verdict=verdict, injected=("kn-mixed",),
            implicated=("kn-mixed",) if verdict == "fail" else ()),
            now=now + timedelta(hours=i))
    stored = store.get("kn-mixed")
    assert stored.status == "published"          # streak broke at the pass
    assert stored.consecutive_harmful == 1


# -- F7: pack-scoped gap join (finding 7) ------------------------------------

def test_gap_signals_never_consume_other_packs_evidence(tmp_path):
    store = PackStore(tmp_path)
    store.claim_topics("pack-a", ["a-topic"])
    store.claim_topics("pack-b", ["b-topic"])
    learner = Learner(store)
    for i in range(2):
        learner.observe(TaskOutcome(
            task_id=f"t{i}", task_type="code_edit", topic="b-topic",
            verdict="fail", injected=()))
    assert learner.gap_signals("pack-a") == []          # not A's topic
    signals = learner.gap_signals("pack-b")             # evidence intact
    assert signals and signals[0].pack == "pack-b"
    assert signals[0].topic == "b-topic"


# -- F9: entry-id digest never truncated (finding 9) -------------------------

def test_long_pack_topic_ids_keep_digest_distinct():
    from selflearn.distillation.distiller import _entry_id

    pack = "fastapi-advanced-lifespan-management"
    topic = "dependency-injection-with-async-context-managers"
    id1 = _entry_id(pack, topic, "body one")
    id2 = _entry_id(pack, topic, "body two")
    assert id1 != id2
    assert len(id1) <= 80 and len(id2) <= 80


# -- F10: think-block/fence tolerant JSON extraction (finding 10) ------------

def test_extract_json_handles_think_blocks_and_fences():
    from selflearn.cli import _extract_json

    payload = {"entries": [{"kind": "knowledge"}]}
    plain = json.dumps(payload)
    assert _extract_json(plain) == payload
    assert _extract_json(f"<think>let me reason...</think>{plain}") == payload
    assert _extract_json(f"<think>hmm</think>```json\n{plain}\n```") == payload
    assert _extract_json(f"Here you go:\n```\n{plain}\n```") == payload
    assert _extract_json(f"prefix text {plain} suffix") == payload
