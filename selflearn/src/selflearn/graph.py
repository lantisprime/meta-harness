"""Read-only graph projection of a knowledge store.

The store's files already contain a graph; nothing materializes it. This
module derives it on demand — no new state, nothing for the doctor to
repair, and (same rule as the advisor) absolutely no store mutation:

nodes   pack, topic, entry, step (workflow procedure steps), domain
        (registrable source domain), task_type
edges   pack --claims--> topic            coverage map + entry topics
        topic --contains--> entry         (topicless entries hang off
        pack --contains--> entry           the pack directly)
        entry --cites--> domain           EntrySource, with source tier
        entry --applies_to--> task_type   declared task_types plus
                                          marks_by_task evidence weights
        entry --has_step--> step          workflow procedure
        step --depends_on--> step         the executable plan DAG

Uses: blast-radius questions ("which published claims rest on this one
domain?"), cross-pack structure (shared sources/topics flat retrieval
cannot see), and spotting graph-isolated entries (single source, no
marks, no shared topic) as deprecation candidates.

Renderers: ``to_json`` (machines, the web UI), ``to_dot`` (Graphviz),
``to_mermaid`` (docs and terminals).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from selflearn.contracts import registrable_domain
from selflearn.store.packstore import PackStore


@dataclass(frozen=True)
class GraphNode:
    id: str                 # "<kind>:<name>", unique across the graph
    kind: str               # pack | topic | entry | step | domain | task_type
    label: str
    attrs: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GraphEdge:
    src: str
    dst: str
    kind: str               # claims | contains | cites | applies_to |
    #                         has_step | depends_on
    attrs: dict[str, Any] = field(default_factory=dict)


@dataclass
class KnowledgeGraph:
    nodes: list[GraphNode] = field(default_factory=list)
    edges: list[GraphEdge] = field(default_factory=list)

    def stats(self) -> dict[str, int]:
        by_kind: dict[str, int] = {}
        for n in self.nodes:
            by_kind[n.kind] = by_kind.get(n.kind, 0) + 1
        return {"nodes": len(self.nodes), "edges": len(self.edges), **by_kind}


def build_graph(store: PackStore,
                packs: Optional[Sequence[str]] = None) -> KnowledgeGraph:
    """Project the store (or a subset of its packs) into a graph."""
    graph = KnowledgeGraph()
    seen: set[str] = set()

    def node(nid: str, kind: str, label: str, **attrs: Any) -> str:
        if nid not in seen:
            seen.add(nid)
            graph.nodes.append(GraphNode(nid, kind, label, dict(attrs)))
        return nid

    def edge(src: str, dst: str, kind: str, **attrs: Any) -> None:
        graph.edges.append(GraphEdge(src, dst, kind, dict(attrs)))

    selected = list(packs) if packs else store.packs()
    known = set(store.packs())
    for pack in selected:
        if pack not in known:
            raise ValueError(f"unknown pack {pack!r}; store has: "
                             f"{sorted(known) or 'none'}")

    for pack in sorted(selected):
        pack_id = node(f"pack:{pack}", "pack", pack)
        coverage = store.coverage(pack)
        topic_ids: dict[str, str] = {}

        def topic_node(topic: str) -> str:
            if topic not in topic_ids:
                topic_ids[topic] = node(
                    f"topic:{pack}/{topic}", "topic", topic,
                    coverage=coverage.get(topic, ""))
                edge(pack_id, topic_ids[topic], "claims",
                     coverage=coverage.get(topic, ""))
            return topic_ids[topic]

        for topic in sorted(coverage):
            topic_node(topic)

        for e in store.entries_for(pack):
            cand = e.cand
            entry_id = node(
                f"entry:{cand.id}", "entry", cand.id,
                status=e.status, entry_kind=cand.kind,
                quarantined=cand.quarantined,
                helpful=round(e.helpful, 3), harmful=round(e.harmful, 3),
                score=round(e.score, 3),
                consecutive_harmful=e.consecutive_harmful,
                has_vector=bool(e.vector),
                probes=len(store.probes_for(cand.id)))
            if cand.topic:
                edge(topic_node(cand.topic), entry_id, "contains")
            else:
                edge(pack_id, entry_id, "contains")

            for src in cand.sources:
                domain = registrable_domain(src.url) or "unknown"
                domain_id = node(f"domain:{domain}", "domain", domain)
                edge(entry_id, domain_id, "cites", tier=src.tier)

            weights = {t: (0.0, 0.0) for t in cand.task_types}
            for task_type, bucket in e.marks_by_task.items():
                weights[task_type] = (bucket[0], bucket[1])
            for task_type, (helpful, harmful) in sorted(weights.items()):
                task_id = node(f"task_type:{task_type}", "task_type",
                               task_type)
                edge(entry_id, task_id, "applies_to",
                     helpful=round(helpful, 3), harmful=round(harmful, 3))

            for step in cand.procedure:
                step_id = node(f"step:{cand.id}/{step.id}", "step",
                               step.id, objective=step.objective,
                               task_type=step.task_type)
                edge(entry_id, step_id, "has_step")
            for step in cand.procedure:
                for dep in step.depends_on:
                    dep_id = f"step:{cand.id}/{dep}"
                    if dep_id in seen:    # dangling deps stay out of the graph
                        edge(dep_id, f"step:{cand.id}/{step.id}",
                             "depends_on")
    return graph


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------

def to_json(graph: KnowledgeGraph) -> dict[str, Any]:
    return {
        "nodes": [{"id": n.id, "kind": n.kind, "label": n.label, **n.attrs}
                  for n in graph.nodes],
        "edges": [{"src": e.src, "dst": e.dst, "kind": e.kind, **e.attrs}
                  for e in graph.edges],
        "stats": graph.stats(),
    }


_DOT_STYLE = {
    "pack": 'shape=folder fillcolor="#dbeafe"',
    "topic": 'shape=oval fillcolor="#ede9fe"',
    "entry": 'shape=box fillcolor="#dcfce7"',
    "step": 'shape=cds fillcolor="#fef9c3"',
    "domain": 'shape=house fillcolor="#fee2e2"',
    "task_type": 'shape=component fillcolor="#e0f2fe"',
}


def _dq(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def to_dot(graph: KnowledgeGraph) -> str:
    lines = ["digraph knowledge {",
             "  rankdir=LR;",
             '  node [style=filled fontname="Helvetica" fontsize=10];']
    for n in graph.nodes:
        extra = ""
        if n.kind == "entry":
            extra = (f"\\n{n.attrs.get('status', '')}"
                     f" · score {n.attrs.get('score', '')}")
        lines.append(f"  {_dq(n.id)} [label={_dq(n.label + extra)} "
                     f"{_DOT_STYLE.get(n.kind, '')}];")
    for e in graph.edges:
        label = f' [label="{e.kind}"]' if e.kind != "contains" else ""
        lines.append(f"  {_dq(e.src)} -> {_dq(e.dst)}{label};")
    lines.append("}")
    return "\n".join(lines)


def to_mermaid(graph: KnowledgeGraph) -> str:
    """flowchart LR with one class per node kind; ids are sanitized to
    Mermaid-safe aliases and real names live in the labels."""
    alias = {n.id: f"n{i}" for i, n in enumerate(graph.nodes)}
    lines = ["flowchart LR"]
    shape = {"pack": ("[", "]"), "topic": ("([", "])"),
             "entry": ("[", "]"), "step": ("[[", "]]"),
             "domain": ("{{", "}}"), "task_type": ("[/", "/]")}
    for n in graph.nodes:
        left, right = shape.get(n.kind, ("[", "]"))
        label = n.label.replace('"', "'")
        lines.append(f'  {alias[n.id]}{left}"{label}"{right}')
    for e in graph.edges:
        arrow = "-.->" if e.kind in ("cites", "applies_to") else "-->"
        lines.append(f"  {alias[e.src]} {arrow} {alias[e.dst]}")
    for kind, color in (("pack", "#dbeafe"), ("topic", "#ede9fe"),
                        ("entry", "#dcfce7"), ("step", "#fef9c3"),
                        ("domain", "#fee2e2"), ("task_type", "#e0f2fe")):
        members = [alias[n.id] for n in graph.nodes if n.kind == kind]
        if members:
            lines.append(f"  classDef {kind} fill:{color};")
            lines.append(f"  class {','.join(members)} {kind};")
    return "\n".join(lines)


RENDERERS = {"dot": to_dot, "mermaid": to_mermaid}
