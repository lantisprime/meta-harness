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
    # loading is read-only: the skipped records stay on disk (merely
    # viewing advice must never destroy evidence), so a reload warns again
    on_disk = json.loads((store.root / "learner-state.json").read_text())
    assert len(on_disk["failures"]) == 2
    with pytest.warns(UserWarning, match="skipping records"):
        Learner(store)


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


# -- below-cap batch --------------------------------------------------------

def test_vectors_live_in_sidecar_not_manifest(tmp_path):
    """C17: a mark update must not rewrite embedding vectors."""
    store = PackStore(tmp_path)
    publish(store, "kn-vec")
    store.set_vector("kn-vec", (0.1, 0.2, 0.3), "emb-v1")
    manifest = json.loads((tmp_path / "fastapi" / "manifest.json").read_text())
    assert "vector" not in manifest["entries"]["kn-vec"]
    sidecar = json.loads((tmp_path / "fastapi" / "vectors.json").read_text())
    assert sidecar["kn-vec"]["vector"] == [0.1, 0.2, 0.3]
    reloaded = PackStore(tmp_path)
    assert reloaded.get("kn-vec").vector == (0.1, 0.2, 0.3)


def test_release_quarantine_is_journaled_and_gated(tmp_path):
    """C29: quarantine now has a journaled human release transition."""
    store = PackStore(tmp_path)
    q = CandidateEntry(id="kn-q", pack="fastapi", kind="knowledge", body="b",
                       claims=("c",), sources=(SRC,), topic="t",
                       quarantined=True, quarantine_reason="injection screen")
    store.add_candidate(q)
    with pytest.raises(Exception, match="quarantined"):
        store.publish("kn-q", PublishDecision(entry_id="kn-q", publish=True,
                                              basis=("t",), identity_basis="m"))
    with pytest.raises(Exception, match="reason"):
        store.release_quarantine("kn-q", reason="", released_by="x")
    store.release_quarantine("kn-q", reason="false positive: benign vocabulary",
                             released_by="dev@znp.pw")
    assert not store.get("kn-q").cand.quarantined
    prov = (tmp_path / "fastapi" / "provenance.jsonl").read_text()
    assert "quarantine.released" in prov and "dev@znp.pw" in prov
    # released entries are ordinary candidates: gates still apply
    assert store.get("kn-q").status == "candidate"
    with pytest.raises(Exception, match="not a quarantined"):
        store.release_quarantine("kn-q", reason="again", released_by="x")


def test_staleness_keeps_flagging_historically_bad_entries(tmp_path):
    """C15: silence must not launder a low lifetime score above the bar."""
    from selflearn.learning import Learner

    store = PackStore(tmp_path)
    e = CandidateEntry(id="kn-bad-old", pack="fastapi", kind="knowledge",
                       body="b", claims=("c",), topic="t",
                       sources=(EntrySource(url="https://docs.example.org/x",
                                            fetched_at="2025-01-01T00:00:00Z",
                                            sha256="0" * 64, tier="official"),))
    store.add_candidate(e)
    store.publish(e.id, PublishDecision(entry_id=e.id, publish=True,
                                        basis=("t",), identity_basis="m"))
    store.mark(e.id, helpful=1.0, harmful=2.0,
               now_iso="2025-02-01T00:00:00+00:00")   # lifetime score 0.4
    learner = Learner(store)
    much_later = datetime(2027, 7, 17, tzinfo=timezone.utc)
    signals = learner.staleness_signals("fastapi", now=much_later)
    assert any("kn-bad-old" in s.evidence for s in signals)


def test_arxiv_single_file_gzip_and_pdf_routing(tmp_path):
    """C5: gzip'd single-file submissions parse; pdf URLs reach PdfPlugin."""
    import gzip as gz

    from selflearn.acquisition import AcquireContext, PluginRegistry, builtin_plugins
    from selflearn.acquisition.plugins import ArxivPlugin, PdfPlugin
    from selflearn.contracts import SourceRef

    class Fetcher:
        def fetch(self, url):
            return gz.compress(
                rb"\section{Intro} Attention mechanisms weigh token pairs.")

    ctx = AcquireContext(workdir=tmp_path / "w", fetcher=Fetcher(),
                         min_fetch_interval_s=0.0)
    docs = ArxivPlugin().acquire(
        SourceRef(uri="https://arxiv.org/abs/1706.03762"), ctx)
    assert "Attention mechanisms" in docs[0].blocks[0]
    # pdf URLs are no longer shadowed by the arxiv plugin
    registry = PluginRegistry(builtin_plugins())
    plugin = registry.resolve(SourceRef(uri="https://arxiv.org/pdf/1706.03762"))
    assert plugin.id == "pdf"


def test_ytdistill_parser_is_shared_and_consistent(tmp_path):
    """C28: one parser; unknown record types and empty text are skipped
    identically by the plugin and the seeder."""
    from selflearn.acquisition.ytdistill import parse_chunks

    lines = [
        json.dumps({"record_type": "summary", "text": "sum", "start": 0}),
        json.dumps({"record_type": "chapter_marker", "text": "ch1"}),   # unknown
        json.dumps({"text": "", "start": 1}),                            # empty
        json.dumps({"text": "real chunk", "start": 3.0, "end": 9.0,
                    "source_url": "https://youtu.be/x"}),
    ]
    parsed = parse_chunks("\n".join(lines))
    assert parsed.skipped_unknown == 1 and parsed.skipped_empty == 1
    assert [r.text for r in parsed.chunks] == ["real chunk"]
    assert parsed.chunks[0].locator == "t=3-9s"
    assert parsed.records[0].is_summary


def test_report_verified_is_derived():
    """C24: verified cannot desynchronize from held + published."""
    from selflearn.pipeline import AcquisitionReport

    report = AcquisitionReport(pack="p", topic="t")
    report.held_for_approval.append("a")
    report.published.append("b")
    assert report.verified == ["a", "b"]


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
