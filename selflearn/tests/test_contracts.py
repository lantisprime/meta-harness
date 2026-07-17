"""Contract value objects: frozen, validated, loud."""
import dataclasses

import pytest

from selflearn.contracts import (
    CandidateEntry,
    ContractError,
    EntrySource,
    Probe,
    ProcedureStep,
    PublishDecision,
    SourceDocument,
    SourceRef,
    TaskOutcome,
    Provenance,
)

SRC = EntrySource(url="https://docs.example.org/x", fetched_at="t",
                  sha256="0" * 64, tier="official")


def make_entry(**kw) -> CandidateEntry:
    base = dict(id="kn-p-t-001", pack="p", kind="knowledge", body="b",
                claims=("c",), sources=(SRC,), topic="t")
    base.update(kw)
    return CandidateEntry(**base)


def test_entries_are_frozen():
    e = make_entry()
    with pytest.raises(dataclasses.FrozenInstanceError):
        e.body = "mutated"


def test_invalid_kind_is_loud():
    with pytest.raises(ContractError, match="kind"):
        make_entry(kind="opinion")


def test_entry_requires_sources():
    with pytest.raises(ContractError, match="source"):
        make_entry(sources=())


def test_workflow_entry_requires_procedure():
    with pytest.raises(ContractError, match="procedure"):
        make_entry(kind="workflow")


def test_workflow_dependencies_must_reference_earlier_steps():
    steps = (ProcedureStep(id="b", objective="o", task_type="t",
                           depends_on=("a",)),)
    with pytest.raises(ContractError, match="depends on"):
        make_entry(kind="workflow", procedure=steps)
    ok = (ProcedureStep(id="a", objective="o", task_type="t"),
          ProcedureStep(id="b", objective="o", task_type="t", depends_on=("a",)))
    assert make_entry(kind="workflow", procedure=ok).procedure[1].depends_on == ("a",)


def test_independent_domains_uses_registrable_domain():
    e = make_entry(sources=(
        EntrySource(url="https://a.example.org/1", fetched_at="", sha256="", tier="primary"),
        EntrySource(url="https://a.example.org/2", fetched_at="", sha256="", tier="primary"),
        EntrySource(url="https://b.example.net/1", fetched_at="", sha256="", tier="community"),
    ))
    assert e.independent_domains() == {"a.example.org", "b.example.net"}


def test_probe_kind_validation():
    with pytest.raises(ContractError):
        Probe(id="p", entry_id="e", kind="vibes", question="q", expected="x",
              check_kind="deterministic")


def test_task_outcome_pass_cannot_implicate():
    with pytest.raises(ContractError, match="implicate"):
        TaskOutcome(task_id="t", task_type="code_edit", topic="x",
                    verdict="pass", injected=("e1",), implicated=("e1",))


def test_task_outcome_carries_step_id():
    o = TaskOutcome(task_id="t", task_type="code_edit", topic="x",
                    verdict="fail", injected=(), step_id="implement")
    assert o.step_id == "implement"


def test_publish_decision_requires_basis():
    with pytest.raises(ContractError, match="basis"):
        PublishDecision(entry_id="e", publish=True, basis=(),
                        identity_basis="model-id")


def test_source_document_requires_content():
    ref = SourceRef(uri="https://x.example")
    prov = Provenance(url="https://x.example", fetched_at="t", sha256="0" * 64,
                      plugin="web", plugin_version="1")
    with pytest.raises(ContractError, match="carry"):
        SourceDocument(ref=ref, blocks=(), chunks=(), assets=(),
                       provenance=prov)
