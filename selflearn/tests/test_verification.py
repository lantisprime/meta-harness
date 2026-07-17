"""Verification: corroboration, citations, vision class, skills, judge, strict mode."""
import pytest

from selflearn.contracts import CandidateEntry, EntrySource
from selflearn.ports import ExecutionResult
from selflearn.verification import (
    CorroborationRule,
    VerificationError,
    Verifier,
)


def src(url="https://docs.example.org/x", tier="official", sha="0" * 64):
    return EntrySource(url=url, fetched_at="t", sha256=sha, tier=tier)


def entry(**kw):
    base = dict(id="kn-p-t-001", pack="p", kind="knowledge", body="b",
                claims=("c",), sources=(src(),), topic="t")
    base.update(kw)
    return CandidateEntry(**base)


class FakeExec:
    def __init__(self, ok=True, output=""):
        self._r = ExecutionResult(ok=ok, output=output)

    def run_check(self, check):
        return self._r


class FakeJudge:
    model_id = "judge-model"

    def __init__(self, supported=True, unsupported=()):
        self.result = {"supported": supported,
                       "unsupported_claims": list(unsupported)}

    def complete(self, role, prompt, context):
        assert role == "knowledge-judge"
        return self.result


def test_official_source_suffices():
    report = Verifier().verify(entry())
    assert report.ok and any("official" in b for b in report.basis)


def test_two_independent_domains_suffice():
    e = entry(sources=(src("https://a.example.org/1", "primary"),
                       src("https://b.example.net/2", "community")))
    assert Verifier().verify(e).ok


def test_same_domain_twice_is_not_independent():
    e = entry(sources=(src("https://a.example.org/1", "primary"),
                       src("https://a.example.org/2", "primary")))
    report = Verifier().verify(e)
    assert not report.ok and "insufficient corroboration" in report.rejected[0]


def test_unknown_tier_cannot_be_sole_support():
    e = entry(sources=(src("https://sketchy.example.net/1", "unknown"),))
    report = Verifier().verify(e)
    assert not report.ok and "unknown" in report.rejected[0]


def test_vision_class_needs_corroboration_even_with_official():
    e = entry(extraction="vision")   # one official source only
    report = Verifier().verify(e)
    assert not report.ok and "vision" in report.rejected[0]
    e2 = entry(extraction="vision",
               sources=(src("https://a.example.org/1", "official"),
                        src("https://b.example.net/2", "primary")))
    assert Verifier().verify(e2).ok


def test_quarantined_is_rejected_outright():
    e = entry(quarantined=True, quarantine_reason="injection screen")
    report = Verifier().verify(e)
    assert not report.ok and "quarantined" in report.rejected[0]


def test_missing_content_hash_fails_citations():
    e = entry(sources=(src(sha=""),))
    report = Verifier().verify(e)
    assert not report.ok and "citations incomplete" in report.rejected[0]


def test_skill_check_executes_and_gates():
    e = entry(kind="skill", skill_check=(("cmd", "pytest -q"),))
    ok = Verifier(execution=FakeExec(ok=True)).verify(e)
    assert ok.ok and any("PASS (sandboxed)" in b for b in ok.basis)
    bad = Verifier(execution=FakeExec(ok=False, output="2 failed")).verify(e)
    assert not bad.ok and "FAIL" in bad.rejected[0]


def test_skill_check_without_sandbox_is_loud_not_skipped():
    e = entry(kind="skill", skill_check=(("cmd", "pytest"),))
    with pytest.raises(VerificationError, match="no ExecutionPort"):
        Verifier(execution=None).verify(e)


def test_skill_without_check_notes_lower_evidence():
    e = entry(kind="skill")
    report = Verifier().verify(e)
    assert report.ok and any("check: none" in b for b in report.basis)


def test_judge_gates_when_bound():
    good = Verifier(judge=FakeJudge(True)).verify(entry(), "excerpts here")
    assert good.ok and any("judge" in b for b in good.basis)
    bad = Verifier(judge=FakeJudge(False, ["claim x"])).verify(entry(), "ex")
    assert not bad.ok and "claim x" in bad.rejected[0]


def test_no_judge_is_visible_in_basis():
    report = Verifier().verify(entry())
    assert any("no judge bound" in b for b in report.basis)


def test_decision_is_strict_mode():
    v = Verifier()
    e = entry()
    d = v.decide(e, v.verify(e))
    assert d.publish and d.strict_mode
    assert "M5" in d.identity_basis      # says probe validation hasn't run


def test_configurable_rule():
    rule = CorroborationRule(min_independent_domains=3, official_suffices=False)
    e = entry()   # single official
    report = Verifier(rule=rule).verify(e)
    assert not report.ok
