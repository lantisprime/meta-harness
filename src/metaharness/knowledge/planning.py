"""Knowledge-driven planning (plan decision 10): before the LLM planner
runs, check whether a published workflow-kind entry already knows how to
structure this goal.

Strong semantic match → the entry's ``procedure:`` compiles deterministically
into a WorkflowSpec (no LLM in the loop, checks derived by the existing
engine machinery). Weak match → the entry's prose returns as planning
guidance for the normal planner. No match → nothing changes.

Param filling for ``{placeholders}`` in step objectives is a named
knowledge-scout step (simulation finding 5) — until M5 wires it, the goal
itself is appended to each step objective so instantiation stays useful
without invented parameter values.
"""
from __future__ import annotations

from typing import Optional, Sequence

from metaharness.core.types import TaskType
from metaharness.workflows.dsl import WorkflowSpec

STRONG_MATCH_SCORE = 0.15

_VALID_TASK_TYPES = {t.value for t in TaskType}


def plan_from_knowledge(
    goal: str,
    store,
    packs: Sequence[str],
    embedder,
) -> tuple[Optional[WorkflowSpec], str, str]:
    """Returns (spec, seeded_by, guidance).

    spec is a deterministic WorkflowSpec when a workflow entry matched
    strongly (seeded_by = its entry id, recorded for plan-level marks);
    otherwise spec is None and guidance carries the best weak match's prose
    ("" when no workflow entries exist at all).
    """
    from selflearn.retrieval import Retriever

    retriever = Retriever(store, embedder)
    if embedder is not None:
        for pack in packs:
            retriever.index(pack)
    results = retriever.retrieve(list(packs), goal, k=8, budget_tokens=100000)
    workflows = [r for r in results if r.entry.cand.kind == "workflow"]
    if not workflows:
        return None, "", ""
    best = workflows[0]
    if best.score < STRONG_MATCH_SCORE:
        return (None, best.entry_id,
                f"Planning guidance from {best.entry_id} (weak match):\n"
                f"{best.entry.cand.body}")

    cand = best.entry.cand
    steps = []
    for step in cand.procedure:
        task_type = (step.task_type if step.task_type in _VALID_TASK_TYPES
                     else TaskType.GENERAL.value)
        inputs = {"goal": "$context.goal"}
        for dep in step.depends_on:
            inputs[dep] = f"$steps.{dep}.output"
        steps.append({
            "id": step.id,
            "task_type": task_type,
            "objective": f"{step.objective}\nGoal: {goal}",
            "inputs": inputs,
            "boundaries": [f"Seeded by knowledge entry {cand.id}; per-step "
                           f"check: {step.check_dict() or 'none declared'}"],
            "tools": list(step.tools),
            "depends_on": list(step.depends_on),
        })
    spec = WorkflowSpec.model_validate({
        "name": f"knowledge:{cand.id}"[:60],
        "steps": steps,
    })
    return spec, cand.id, ""
