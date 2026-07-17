"""Fast-loop marks: asymmetric credit assignment + auto-deprecation."""
import pytest

from selflearn.contracts import (
    CandidateEntry,
    EntrySource,
    PublishDecision,
    TaskOutcome,
)
from selflearn.learning import apply_outcome
from selflearn.store import PackStore

SRC = EntrySource(url="https://docs.example.org/x", fetched_at="t",
                  sha256="0" * 64, tier="official")


@pytest.fixture()
def store(tmp_path):
    s = PackStore(tmp_path)
    for eid in ("kn-a", "kn-b"):
        e = CandidateEntry(id=eid, pack="p", kind="knowledge", body="b",
                           claims=("c",), sources=(SRC,), topic="t")
        s.add_candidate(e)
        s.publish(eid, PublishDecision(entry_id=eid, publish=True,
                                       basis=("test",), identity_basis="m"))
    return s


def outcome(verdict, injected=(), applied=(), implicated=()):
    return TaskOutcome(task_id="t1", task_type="code_edit", topic="t",
                       verdict=verdict, injected=tuple(injected),
                       applied=tuple(applied), implicated=tuple(implicated))


def test_pass_credits_injected_with_applied_weighting(store):
    report = apply_outcome(store, outcome("pass", injected=["kn-a", "kn-b"],
                                          applied=["kn-a"]))
    assert store.get("kn-a").helpful == 2.0     # cited as used
    assert store.get("kn-b").helpful == 1.0     # merely injected
    assert set(report.helpful_marked) == {"kn-a", "kn-b"}


def test_fail_harms_only_implicated(store):
    apply_outcome(store, outcome("fail", injected=["kn-a", "kn-b"],
                                 implicated=["kn-a"]))
    assert store.get("kn-a").harmful == 1.0
    assert store.get("kn-b").harmful == 0.0     # injection alone never harms


def test_auto_deprecation_at_threshold(store):
    store.mark("kn-a", helpful=1.0)
    reports = [apply_outcome(store, outcome("fail", injected=["kn-a"],
                                            implicated=["kn-a"]))
               for _ in range(3)]
    assert store.get("kn-a").status == "deprecated"
    assert reports[-1].deprecated == ("kn-a",)
    assert reports[0].deprecated == ()           # not before threshold
    # deprecation reason is journaled in pack provenance
    prov = (store.root / "p" / "provenance.jsonl").read_text()
    assert "auto: harmful=3.00" in prov


def test_helpful_history_delays_deprecation(store):
    store.mark("kn-a", helpful=5.0)
    for _ in range(3):
        apply_outcome(store, outcome("fail", injected=["kn-a"],
                                     implicated=["kn-a"]))
    assert store.get("kn-a").status == "published"   # harmful <= helpful
