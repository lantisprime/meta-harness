"""Store: state machine, write-through persistence, boot loading, loud failures."""
import json

import pytest

from selflearn.contracts import CandidateEntry, EntrySource, Probe, PublishDecision
from selflearn.ports import JsonlProvenance
from selflearn.store import PackStore, StoreError

SRC = EntrySource(url="https://docs.example.org/x", fetched_at="t",
                  sha256="0" * 64, tier="official")


def make_entry(eid="kn-fastapi-lifespan-001", **kw) -> CandidateEntry:
    base = dict(id=eid, pack="fastapi", kind="knowledge",
                body="Lifespan replaces on_event.", claims=("lifespan",),
                sources=(SRC,), topic="lifespan")
    base.update(kw)
    return CandidateEntry(**base)


def decision(eid) -> PublishDecision:
    return PublishDecision(entry_id=eid, publish=True,
                           basis=("test basis",), identity_basis="model-id")


def probe(eid, validated=True) -> Probe:
    return Probe(id=f"{eid}-p0", entry_id=eid, kind="recall", question="q?",
                 expected="lifespan", check_kind="deterministic",
                 validated=validated, validated_by="model-b")


def test_full_lifecycle_survives_reload(tmp_path):
    store = PackStore(tmp_path, provenance=JsonlProvenance(tmp_path / "prov.jsonl"))
    e = make_entry()
    store.add_candidate(e)
    store.claim_topics("fastapi", ["lifespan", "middleware"])
    store.publish(e.id, decision(e.id), probes=[probe(e.id)],
                  vector=(0.1, 0.2), embedder_id="emb-v1")
    store.mark(e.id, helpful=2.0)
    assert store.coverage("fastapi") == {"lifespan": "covered",
                                         "middleware": "claimed"}

    # Boot loading: a fresh store over the same root reconstructs everything.
    reloaded = PackStore(tmp_path)
    got = reloaded.get(e.id)
    assert got.status == "published"
    assert got.helpful == 2.0
    assert got.vector == (0.1, 0.2) and got.embedder_id == "emb-v1"
    assert got.cand.body == e.body
    assert got.cand.sources[0].url == SRC.url
    assert reloaded.probes_for(e.id)[0].expected == "lifespan"
    assert reloaded.coverage("fastapi")["middleware"] == "claimed"
    assert reloaded.suite_size("fastapi") == 1


def test_deprecate_retires_probes_and_restore_reverses(tmp_path):
    store = PackStore(tmp_path)
    e = make_entry()
    store.add_candidate(e)
    store.publish(e.id, decision(e.id), probes=[probe(e.id)])
    store.deprecate(e.id, "harmful>helpful")
    assert store.get(e.id).status == "deprecated"
    assert store.probes_for(e.id) == []
    assert store.probes_for(e.id, include_retired=True)
    assert store.published("fastapi") == []
    reloaded = PackStore(tmp_path)
    assert reloaded.get(e.id).status == "deprecated"
    assert reloaded.probes_for(e.id) == []
    reloaded.restore(e.id, "false alarm")
    assert reloaded.get(e.id).status == "published"
    assert reloaded.probes_for(e.id)


def test_state_machine_violations_are_loud(tmp_path):
    store = PackStore(tmp_path)
    e = make_entry()
    store.add_candidate(e)
    with pytest.raises(StoreError, match="already exists"):
        store.add_candidate(e)
    with pytest.raises(StoreError, match="positive PublishDecision"):
        store.publish(e.id, PublishDecision(entry_id=e.id, publish=False,
                                            basis=("rejected",),
                                            identity_basis="model-id"))
    with pytest.raises(StoreError, match="cannot deprecate"):
        store.deprecate(e.id, "not published yet")
    store.publish(e.id, decision(e.id))
    with pytest.raises(StoreError, match="cannot publish"):
        store.publish(e.id, decision(e.id))
    with pytest.raises(StoreError, match="cannot restore"):
        store.restore(e.id, "not deprecated")


def test_quarantined_entry_cannot_be_gate_published(tmp_path):
    store = PackStore(tmp_path)
    q = make_entry(eid="kn-fastapi-hostile-001", quarantined=True,
                   quarantine_reason="injection screen")
    store.add_candidate(q)
    with pytest.raises(StoreError, match="quarantined"):
        store.publish(q.id, decision(q.id))


def test_unvalidated_probes_rejected(tmp_path):
    store = PackStore(tmp_path)
    e = make_entry()
    store.add_candidate(e)
    with pytest.raises(StoreError, match="not validated"):
        store.publish(e.id, decision(e.id), probes=[probe(e.id, validated=False)])


def test_reindex_needed_on_embedder_swap(tmp_path):
    store = PackStore(tmp_path)
    e = make_entry()
    store.add_candidate(e)
    store.publish(e.id, decision(e.id), vector=(1.0,), embedder_id="emb-v1")
    assert store.reindex_needed("fastapi", "emb-v1") == []
    assert store.reindex_needed("fastapi", "emb-v2") == [e.id]
    store.set_vector(e.id, (0.5,), "emb-v2")
    assert store.reindex_needed("fastapi", "emb-v2") == []
    with pytest.raises(StoreError, match="embedder_id"):
        store.set_vector(e.id, (0.5,), "")


def test_boot_load_is_loud_on_missing_entry_file(tmp_path):
    store = PackStore(tmp_path)
    e = make_entry()
    store.add_candidate(e)
    store.publish(e.id, decision(e.id))
    (tmp_path / "fastapi" / "entries" / f"{e.id}.md").unlink()
    with pytest.raises(StoreError, match="missing"):
        PackStore(tmp_path)


def test_boot_load_is_loud_on_corrupt_manifest(tmp_path):
    store = PackStore(tmp_path)
    store.add_candidate(make_entry())
    (tmp_path / "fastapi" / "manifest.json").write_text("{not json")
    with pytest.raises(StoreError, match="corrupt manifest"):
        PackStore(tmp_path)


def test_provenance_events_recorded(tmp_path):
    prov_path = tmp_path / "host-prov.jsonl"
    store = PackStore(tmp_path / "packs", provenance=JsonlProvenance(prov_path))
    e = make_entry()
    store.add_candidate(e)
    store.publish(e.id, decision(e.id))
    events = [json.loads(l) for l in prov_path.read_text().splitlines()]
    assert [ev["event"] for ev in events] == ["candidate.added", "entry.published"]
    assert events[1]["identity_basis"] == "model-id"
    local = (tmp_path / "packs" / "fastapi" / "provenance.jsonl").read_text()
    assert "entry.published" in local


def test_workflow_entry_roundtrip(tmp_path):
    from selflearn.contracts import ProcedureStep
    steps = (ProcedureStep(id="spec", objective="Write failing tests",
                           task_type="code_edit", tools=("write_file",),
                           check=(("kind", "tests_fail"),)),
             ProcedureStep(id="impl", objective="Implement until green",
                           task_type="code_edit", depends_on=("spec",),
                           check=(("kind", "tests_pass"),)))
    wf = make_entry(eid="wf-fastapi-tdd-001", kind="workflow", procedure=steps)
    store = PackStore(tmp_path)
    store.add_candidate(wf)
    store.publish(wf.id, decision(wf.id))
    reloaded = PackStore(tmp_path)
    got = reloaded.get(wf.id).cand
    assert got.kind == "workflow"
    assert [s.id for s in got.procedure] == ["spec", "impl"]
    assert got.procedure[1].depends_on == ("spec",)
    assert got.procedure[0].check_dict() == {"kind": "tests_fail"}
