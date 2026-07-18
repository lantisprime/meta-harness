"""Graph projection: store -> nodes/edges, renderers, CLI, read-only."""
import json

from selflearn.cli import main
from selflearn.contracts import (
    CandidateEntry,
    EntrySource,
    ProcedureStep,
    PublishDecision,
)
from selflearn.graph import build_graph, to_dot, to_json, to_mermaid
from selflearn.store import PackStore

import pytest


def _cand(eid, pack="p", topic="t", kind="knowledge", urls=(), **kw):
    urls = urls or ("https://docs.example.com/x",)
    return CandidateEntry(
        id=eid, pack=pack, kind=kind, body=f"body of {eid}",
        claims=("a claim",), topic=topic,
        sources=tuple(EntrySource(url=u, fetched_at="2026-07-01",
                                  sha256="0" * 64, tier="official")
                      for u in urls), **kw)


def _publish(store, eid):
    store.publish(eid, PublishDecision(entry_id=eid, publish=True,
                                       basis=("test",),
                                       identity_basis="test"))


def _store(root):
    store = PackStore(root)
    store.add_candidate(_cand(
        "e1", urls=("https://docs.example.com/x", "https://blog.other.org/y"),
        task_types=("code_edit",)))
    _publish(store, "e1")
    store.mark("e1", helpful=2.0, task_type="code_edit",
               now_iso="2026-07-01T00:00:00Z")
    store.add_candidate(_cand(
        "wf1", topic="deploys", kind="workflow",
        procedure=(ProcedureStep(id="s1", objective="build",
                                 task_type="code_edit"),
                   ProcedureStep(id="s2", objective="ship",
                                 task_type="code_edit",
                                 depends_on=("s1",)))))
    store.add_candidate(_cand("q1", topic="risky", quarantined=True,
                              quarantine_reason="screen hit"))
    store.claim_topics("p", ["gap-topic"])
    return store


def test_build_graph_projects_all_node_and_edge_kinds(tmp_path):
    graph = build_graph(_store(tmp_path / "s"))
    kinds = {n.kind for n in graph.nodes}
    assert kinds == {"pack", "topic", "entry", "step", "domain", "task_type"}
    edge_kinds = {e.kind for e in graph.edges}
    assert edge_kinds == {"claims", "contains", "cites", "applies_to",
                          "has_step", "depends_on"}
    by_id = {n.id: n for n in graph.nodes}
    # entry attrs carry lifecycle + evidence state
    e1 = by_id["entry:e1"]
    assert e1.attrs["status"] == "published" and e1.attrs["helpful"] == 2.0
    assert by_id["entry:q1"].attrs["quarantined"] is True
    # both source domains present; the claimed-but-uncovered topic too
    assert "domain:docs.example.com" in by_id
    assert "domain:blog.other.org" in by_id
    assert "topic:p/gap-topic" in by_id
    # the workflow DAG survives projection
    assert ("step:wf1/s1", "step:wf1/s2") in [
        (e.src, e.dst) for e in graph.edges if e.kind == "depends_on"]
    # evidence weights ride the applies_to edge
    marks = [e for e in graph.edges if e.kind == "applies_to"]
    assert marks and marks[0].attrs["helpful"] == 2.0


def test_pack_filter_and_unknown_pack(tmp_path):
    store = _store(tmp_path / "s")
    store.add_candidate(_cand("other-e", pack="p2"))
    graph = build_graph(store, packs=["p2"])
    assert {n.id for n in graph.nodes if n.kind == "pack"} == {"pack:p2"}
    with pytest.raises(ValueError, match="unknown pack"):
        build_graph(store, packs=["nope"])


def test_renderers_are_well_formed(tmp_path):
    graph = build_graph(_store(tmp_path / "s"))
    data = to_json(graph)
    assert data["stats"]["nodes"] == len(data["nodes"]) > 0
    json.dumps(data)                       # JSON-serializable end to end

    dot = to_dot(graph)
    assert dot.startswith("digraph") and dot.rstrip().endswith("}")
    assert '"entry:e1"' in dot

    mermaid = to_mermaid(graph)
    assert mermaid.startswith("flowchart LR")
    # raw ids contain ':' and '/' which break mermaid — aliases must be used
    body = "\n".join(l for l in mermaid.splitlines()
                     if "-->" in l or "-.->" in l)
    assert "entry:" not in body and "step:" not in body


def test_projection_is_read_only(tmp_path):
    root = tmp_path / "s"
    _store(root)
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    build_graph(PackStore(root))
    after = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert before == after


def test_cli_graph_formats_and_out_file(tmp_path, capsys):
    root = tmp_path / "s"
    _store(root)
    assert main(["graph", "--store", str(root)]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["stats"]["edges"] > 0

    assert main(["graph", "--store", str(root), "--format", "mermaid"]) == 0
    assert capsys.readouterr().out.startswith("flowchart LR")

    out = tmp_path / "g.dot"
    assert main(["graph", "--store", str(root), "--format", "dot",
                 "--out", str(out), "--packs", "p"]) == 0
    assert out.read_text().startswith("digraph")
    assert "wrote dot graph" in capsys.readouterr().out


def test_cli_graph_empty_store_exits_one(tmp_path, capsys):
    assert main(["graph", "--store", str(tmp_path / "empty")]) == 1
    assert "no packs" in capsys.readouterr().err
