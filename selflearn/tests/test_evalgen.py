"""M5: evalgen, second-model validation, eval gate, qualification, auto mode."""
import pytest

from selflearn.contracts import CandidateEntry, EntrySource, Probe
from selflearn.ports import ModelIdIdentity
from selflearn.store import PackStore
from selflearn.verification import (
    EvalGen,
    EvalGenError,
    Verifier,
    eval_gated_decision,
    qualify_model,
)

SRC = EntrySource(url="https://docs.example.org/x", fetched_at="t",
                  sha256="0" * 64, tier="official")
SOURCES = ("The lifespan context manager replaces on_event handlers for "
           "startup and shutdown work in FastAPI applications.")


def entry(**kw):
    base = dict(id="kn-fastapi-lifespan", pack="fastapi", kind="knowledge",
                body="Lifespan context manager replaces on_event handlers.",
                claims=("lifespan replaces on_event",), sources=(SRC,),
                topic="lifespan")
    base.update(kw)
    return CandidateEntry(**base)


class AuthorModel:
    model_id = "author-a"

    def complete(self, role, prompt, context):
        assert role == "probe-author"
        return {"probes": [
            {"kind": "recall",
             "question": "What replaces on_event handlers in FastAPI?",
             "expected": "lifespan context manager"},
            {"kind": "application",
             "question": "A service must open a DB pool at startup; what "
                         "mechanism should hold it?",
             "expected": "lifespan context manager"},
            {"kind": "recall", "question": "What color is the maintainer's "
             "bike?", "expected": "purple"},          # unanswerable from sources
        ]}


class ValidatorModel:
    """Answers strictly from sources: knows only what the excerpts say."""
    model_id = "validator-b"

    def complete(self, role, prompt, context):
        if "on_event" in context["question"] or "startup" in context["question"]:
            return {"answer": "the lifespan context manager"}
        return {"answer": "cannot determine from sources"}


class AnswerModel:
    """Simulated specialist: answers correctly only WITH the knowledge block."""
    model_id = "answerer-c"

    def complete(self, role, prompt, context):
        if "lifespan" in context.get("knowledge_block", "").lower():
            return {"answer": "use the lifespan context manager"}
        return {"answer": "probably some startup decorator?"}


def make_evalgen():
    return EvalGen(AuthorModel(), ValidatorModel(), ModelIdIdentity())


def test_same_worker_cannot_validate_its_own_probes():
    with pytest.raises(EvalGenError, match="identity violation"):
        EvalGen(AuthorModel(), AuthorModel(), ModelIdIdentity())


def test_validator_rejects_unanswerable_probe():
    eg = make_evalgen()
    probes = eg.generate(entry())
    report = eg.validate(probes, SOURCES)
    assert len(report.validated) == 2                  # bike probe rejected
    assert any("p2" in r for r in report.rejected)
    assert all(p.validated_by == "validator-b" for p in report.validated)


def test_validation_requires_source_excerpts():
    eg = make_evalgen()
    with pytest.raises(EvalGenError, match="source excerpts"):
        eg.validate(eg.generate(entry()), "")


def test_skill_entries_get_execution_probe_for_free():
    eg = make_evalgen()
    probes = eg.generate(entry(kind="skill", skill_check=(("cmd", "x"),)))
    assert any(p.check_kind == "execution" for p in probes)


def test_eval_gate_publishes_only_when_probes_pass_with_injection(tmp_path):
    eg = make_evalgen()
    e = entry()
    v = Verifier()
    vreport = v.verify(e)
    validated = eg.validate(eg.generate(e), SOURCES).validated
    d = eval_gated_decision(e, vreport, validated, AnswerModel(),
                            suite_size=0, identity_basis=ModelIdIdentity.basis)
    assert d.publish and not d.strict_mode
    assert any("BOOTSTRAP" in b for b in d.basis)      # finding 1 rule

    class IgnorantAnswerer:
        model_id = "ignorant"

        def complete(self, role, prompt, context):
            return {"answer": "no idea"}

    d2 = eval_gated_decision(e, vreport, validated, IgnorantAnswerer(),
                             suite_size=0, identity_basis="m")
    assert not d2.publish and any("fail WITH the entry injected" in b
                                  for b in d2.basis)


def test_eval_gate_refuses_without_validated_probes():
    e = entry()
    v = Verifier()
    d = eval_gated_decision(e, v.verify(e), [], AnswerModel(), suite_size=9,
                            identity_basis="m")
    assert not d.publish and any("no second-model-validated" in b
                                 for b in d.basis)


def test_qualification_measures_injection_delta(tmp_path):
    store = PackStore(tmp_path)
    eg = make_evalgen()
    e = entry()
    v = Verifier()
    validated = eg.validate(eg.generate(e), SOURCES).validated
    d = eval_gated_decision(e, v.verify(e), validated, AnswerModel(),
                            suite_size=0, identity_basis="m")
    store.add_candidate(e)
    store.publish(e.id, d, probes=validated)

    q = qualify_model(AnswerModel(), store, "fastapi")
    assert q.total_probes == 2
    assert q.with_injection == 1.0 and q.without_injection == 0.0
    assert q.delta == 1.0 and q.qualified

    class Ignorant:
        model_id = "ignorant"

        def complete(self, role, prompt, context):
            return {"answer": "no idea"}

    assert not qualify_model(Ignorant(), store, "fastapi").qualified
