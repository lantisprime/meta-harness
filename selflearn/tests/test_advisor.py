"""Advisor: store state -> prioritized, executable next-best-action list."""
from datetime import datetime, timezone

from selflearn.advisor import suggest_actions
from selflearn.cli import main
from selflearn.contracts import CandidateEntry, EntrySource, PublishDecision
from selflearn.store import PackStore

NOW = datetime(2026, 7, 18, tzinfo=timezone.utc)


def _cand(eid, pack="p", topic="t", quarantined=False, fetched="2026-07-01"):
    return CandidateEntry(
        id=eid, pack=pack, kind="knowledge", body=f"body of {eid}",
        claims=("a claim",), topic=topic,
        quarantined=quarantined,
        quarantine_reason="injection screen" if quarantined else "",
        sources=(EntrySource(url="https://docs.example.com/x",
                             fetched_at=fetched, sha256="0" * 64,
                             tier="official"),))


def _publish(store, eid, probes=()):
    store.publish(eid, PublishDecision(entry_id=eid, publish=True,
                                       basis=("test",),
                                       identity_basis="test"),
                  probes=probes)


def test_empty_store_suggests_getting_started(tmp_path):
    got = suggest_actions(PackStore(tmp_path / "s"), now=NOW)
    assert len(got) == 1 and got[0].priority == 1
    assert "seed" in got[0].command


def test_quarantined_candidates_come_first(tmp_path):
    store = PackStore(tmp_path / "s")
    store.add_candidate(_cand("q1", quarantined=True))
    store.add_candidate(_cand("c1"))
    got = suggest_actions(store, now=NOW)
    assert got[0].priority == 1 and "quarantined" in got[0].action
    assert "selflearn release q1" in got[0].command
    assert got[1].priority == 2 and "verify" in got[1].action
    assert "selflearn verify --pack p" in got[1].command


def test_harmful_published_entry_suggests_deprecation(tmp_path):
    store = PackStore(tmp_path / "s")
    store.add_candidate(_cand("bad"))
    _publish(store, "bad")
    for _ in range(3):
        store.mark("bad", harmful=1.0, now_iso="2026-07-17T00:00:00Z")
    got = suggest_actions(store, now=NOW)
    dep = [s for s in got if "deprecate" in s.action]
    assert dep and "selflearn deprecate bad" in dep[0].command
    assert "3 consecutive harmful marks" in dep[0].reason


def test_low_score_uses_decayed_evidence(tmp_path):
    store = PackStore(tmp_path / "s")
    store.add_candidate(_cand("meh"))
    _publish(store, "meh")
    store.mark("meh", helpful=1.0, now_iso="2026-07-17T00:00:00Z")
    store.mark("meh", harmful=5.0, now_iso="2026-07-17T00:00:00Z")
    got = suggest_actions(store, now=NOW)
    assert any("deprecate" in s.action and "recent marks" in s.reason
               for s in got)
    # the same marks a year stale have decayed below the evidence floor:
    # the advisor reads through evidence.py, not lifetime counters
    later = datetime(2027, 7, 18, tzinfo=timezone.utc)
    got = suggest_actions(store, now=later)
    assert not any("deprecate" in s.action for s in got)


def test_viewing_advice_never_rewrites_learner_state(tmp_path):
    import json as _json
    store = PackStore(tmp_path / "s")
    store.add_candidate(_cand("e1"))
    _publish(store, "e1")
    state_path = tmp_path / "s" / "learner-state.json"
    # one record invalid under the current contract, one valid
    state = {"backoff": {}, "failures": [
        {"task_id": "old", "task_type": "code_edit", "topic": "t",
         "verdict": "fail", "injected": [], "implicated": ["ghost"]},
        {"task_id": "ok", "task_type": "code_edit", "topic": "t",
         "verdict": "fail", "injected": ["e1"], "implicated": ["e1"]}]}
    state_path.write_text(_json.dumps(state))
    before = state_path.read_bytes()
    import warnings as _warnings
    with _warnings.catch_warnings():
        _warnings.simplefilter("ignore")
        suggest_actions(store, now=NOW)
    assert state_path.read_bytes() == before   # advice is read-only


def test_stale_entry_suggests_refresh(tmp_path):
    store = PackStore(tmp_path / "s")
    store.add_candidate(_cand("old", topic="aging", fetched="2025-01-01"))
    _publish(store, "old")
    store.mark("old", harmful=2.0, now_iso="2026-07-01T00:00:00Z")
    got = suggest_actions(store, now=NOW)
    stale = [s for s in got if s.priority == 4]
    assert stale and "aging" in stale[0].action


def test_claimed_uncovered_topic_suggests_acquisition(tmp_path):
    store = PackStore(tmp_path / "s")
    store.add_candidate(_cand("e1", topic="done"))
    _publish(store, "e1")
    store.claim_topics("p", ["missing-topic"])
    got = suggest_actions(store, now=NOW)
    gap = [s for s in got if s.priority == 5]
    assert gap and "missing-topic" in gap[0].action
    assert "--topic missing-topic" in gap[0].command


def test_unindexed_published_entries_noted(tmp_path):
    store = PackStore(tmp_path / "s")
    store.add_candidate(_cand("e1"))
    _publish(store, "e1")
    got = suggest_actions(store, now=NOW)
    assert any(s.priority == 6 and "embedding" in s.action for s in got)


def test_all_clear_when_nothing_pending(tmp_path):
    store = PackStore(tmp_path / "s")
    store.add_candidate(_cand("e1"))
    _publish(store, "e1")
    store.set_vector("e1", (1.0, 0.0), "emb-1")
    store.mark("e1", helpful=5.0, now_iso="2026-07-17T00:00:00Z")
    got = suggest_actions(store, now=NOW)
    assert len(got) == 1 and got[0].priority == 9
    assert "retrieve" in got[0].command


def test_epistemic_suggestion_for_thin_published_knowledge(tmp_path):
    # published but never validated -> proactive "strengthen" suggestion,
    # BEFORE any failure (the active-inference epistemic term, §3.1)
    store = PackStore(tmp_path / "s")
    store.add_candidate(_cand("thin", topic="lonely"))
    _publish(store, "thin")
    store.set_vector("thin", (1.0, 0.0), "emb-1")     # silence the embeddings note
    got = suggest_actions(store, now=NOW)
    strengthen = [x for x in got if "strengthen low-confidence" in x.action]
    assert strengthen and "lonely" in strengthen[0].action
    assert strengthen[0].efe > 0

    # once it earns solid evidence, the proactive suggestion goes away
    for _ in range(12):
        store.mark("thin", helpful=1.0, now_iso="2026-07-17T00:00:00Z")
    got = suggest_actions(store, now=NOW)
    assert not any("strengthen low-confidence" in x.action for x in got)


def test_coverage_gap_outranks_weaker_signal_in_tier(tmp_path):
    # within the same priority tier, higher EFE floats up (§3.1): a missing
    # claimed topic (coverage) should precede a proactive uncertainty nudge
    store = PackStore(tmp_path / "s")
    store.add_candidate(_cand("e1", topic="have"))
    _publish(store, "e1")
    store.set_vector("e1", (1.0, 0.0), "emb-1")
    store.claim_topics("p", ["missing"])
    got = suggest_actions(store, now=NOW)
    tier5 = [x for x in got if x.priority == 5]
    assert len(tier5) >= 2
    assert "acquire claimed topic" in tier5[0].action   # coverage gap first


def test_cmd_next_prints_and_exits_zero(tmp_path, capsys):
    store = PackStore(tmp_path / "s")
    store.add_candidate(_cand("c1"))
    rc = main(["next", "--store", str(tmp_path / "s")])
    out = capsys.readouterr().out
    assert rc == 0 and "next best actions" in out
    assert "selflearn verify --pack p" in out


def test_cmd_next_broken_store_points_at_doctor(tmp_path, capsys):
    root = tmp_path / "s"
    (root / "p").mkdir(parents=True)          # pack dir with no manifest
    rc = main(["next", "--store", str(root)])
    err = capsys.readouterr().err
    assert rc == 2 and "selflearn doctor" in err   # broken store = error


def test_corrupt_learner_state_suggests_doctor(tmp_path):
    store = PackStore(tmp_path / "s")
    store.add_candidate(_cand("e1"))
    _publish(store, "e1")
    (tmp_path / "s" / "learner-state.json").write_text("{nope")
    got = suggest_actions(store, now=NOW)
    assert got[0].priority == 1 and "doctor" in got[0].command
