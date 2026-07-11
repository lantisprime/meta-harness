"""Workflow types: named, deterministic phase templates a goal can run through.

"Deterministic spine, intelligent steps" applied to whole processes: a template
is version-controlled code, its phases become journaled steps with explicit
delegation contracts, verification expectations, and default HITL gates; LLM
intelligence works INSIDE each phase, never on the lifecycle.

The software_engineering template encodes the 2025-26 agentic-SDLC consensus
(memory/knowledge_base/agentic-sdlc-baseline.md — Spec Kit, Kiro, Claude Code,
Codex converge): spec/plan artifacts before code, per-phase verifiable done-
criteria, human gates at spec, plan and ship.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from metaharness.core.types import TaskType, Tier
from metaharness.workflows.dsl import WorkflowSpec


class PhaseSpec(BaseModel):
    """One template phase; `objective` may use {goal} and is the full
    delegation contract for the step it becomes."""

    id: str
    objective: str
    task_type: TaskType = TaskType.GENERAL
    boundaries: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    output_schema: Optional[dict[str, Any]] = None
    tier_hint: Optional[Tier] = None
    hitl: bool = False
    hitl_timing: Literal["before", "after"] = "before"
    depends_on: list[str] = Field(default_factory=list)
    feeds_from: list[str] = Field(default_factory=list)  # prior phases whose output is an input


class WorkflowTemplate(BaseModel):
    id: str
    label: str
    description: str
    phases: list[PhaseSpec]

    def instantiate(self, goal: str) -> WorkflowSpec:
        """Deterministically expand the template for one goal. No LLM in the
        loop here — the spine is code; the planner is not consulted."""
        steps = []
        for phase in self.phases:
            inputs: dict[str, Any] = {"goal": "$context.goal"}
            for prior in phase.feeds_from:
                inputs[prior] = f"$steps.{prior}.output"
            steps.append({
                "id": phase.id,
                "task_type": phase.task_type.value,
                "objective": phase.objective.format(goal=goal),
                "inputs": inputs,
                "boundaries": list(phase.boundaries),
                "tools": list(phase.tools),
                "output_schema": phase.output_schema,
                "tier_hint": phase.tier_hint.value if phase.tier_hint else None,
                "hitl": phase.hitl,
                "hitl_timing": phase.hitl_timing,
                "depends_on": list(phase.depends_on),
            })
        short = goal[:40]
        if len(goal) > 40 and " " in short:
            short = short.rsplit(" ", 1)[0]  # cut at a word, never mid-word
        return WorkflowSpec.model_validate({
            "name": f"{self.id}:{short}",
            "steps": steps,
        })


SOFTWARE_ENGINEERING = WorkflowTemplate(
    id="software_engineering",
    label="Software engineering",
    description=(
        "Agentic SDLC: explore → specify (gate) → plan (gate) → implement → "
        "verify → review (gate). Spec and plan are artifacts a human approves "
        "before any code; verification demands evidence, not assertion."
    ),
    phases=[
        PhaseSpec(
            id="explore",
            objective=(
                "Explore the workspace to ground the goal: {goal}\n"
                "Produce a context summary: the relevant files (cite paths), the "
                "existing patterns/conventions to follow, and a hypothesis of what "
                "must change. Read; do not modify anything."
            ),
            tools=["list_files", "grep", "read_file"],
            boundaries=["Read-only phase: never write or edit files."],
        ),
        PhaseSpec(
            id="specify",
            objective=(
                "Write the specification for: {goal}\n"
                "User stories with TESTABLE acceptance criteria (each criterion "
                "mechanically checkable), an explicit out-of-scope list, and any "
                "open questions stated as questions — never guessed answers."
            ),
            feeds_from=["explore"],
            depends_on=["explore"],
            boundaries=[
                "Every acceptance criterion must be verifiable by a command, test, "
                "or observable state — reject vague criteria.",
            ],
            hitl=True,  # spec approval gate (Kiro/Spec Kit default)
            hitl_timing="after",
        ),
        PhaseSpec(
            id="plan",
            objective=(
                "Write the technical plan to satisfy the approved spec for: {goal}\n"
                "Files to change, interfaces/signatures, test strategy (which tests "
                "prove which criteria), risks, and implementation order."
            ),
            task_type=TaskType.PLANNING,
            feeds_from=["explore", "specify"],
            depends_on=["specify"],
            hitl=True,  # plan approval gate
            hitl_timing="after",
        ),
        PhaseSpec(
            id="implement",
            objective=(
                "Implement the approved plan for: {goal}\n"
                "Write the code AND its tests together in the workspace. Follow the "
                "plan's file list; note any forced deviation explicitly."
            ),
            task_type=TaskType.CODE_EDIT,
            feeds_from=["specify", "plan"],
            depends_on=["plan"],
            tools=["read_file", "write_file", "edit_file", "list_files", "grep"],
            boundaries=[
                "Never weaken or delete an existing test to make it pass.",
                "Tests are written in this phase, not deferred.",
            ],
            tier_hint=Tier.FRONTIER,
        ),
        PhaseSpec(
            id="verify",
            objective=(
                "Verify the implementation against every acceptance criterion for: "
                "{goal}\nFor each criterion report met/not-met WITH evidence (file "
                "content, command output, observable state) — an assertion without "
                "evidence counts as not-met."
            ),
            feeds_from=["specify", "implement"],
            depends_on=["implement"],
            tools=["read_file", "list_files", "grep"],
            output_schema={
                "type": "object",
                "required": ["all_met", "criteria"],
                "properties": {
                    "all_met": {"type": "boolean"},
                    "criteria": {"type": "array", "items": {
                        "type": "object",
                        "properties": {"criterion": {"type": "string"},
                                       "met": {"type": "boolean"},
                                       "evidence": {"type": "string"}}}},
                },
            },
        ),
        PhaseSpec(
            id="review",
            objective=(
                "Adversarially review the delivered work for: {goal}\n"
                "You did not write this code; hunt for defects: spec criteria "
                "gamed rather than met, missing tests, edge cases, deviations from "
                "the plan. End with a ship / no-ship recommendation and findings."
            ),
            feeds_from=["specify", "plan", "verify"],
            depends_on=["verify"],
            tools=["read_file", "list_files", "grep"],
            boundaries=[
                "Review with fresh eyes: treat prior phase outputs as claims to "
                "check, not facts.",
            ],
            hitl=True,  # ship gate
            hitl_timing="after",
        ),
    ],
)

RESEARCH = WorkflowTemplate(
    id="research",
    label="Research & report",
    description="Gather → analyze → synthesize (gate before the report leaves).",
    phases=[
        PhaseSpec(
            id="gather",
            objective=("Gather source material for: {goal}\nCollect the relevant "
                       "raw facts with their origins (file paths / URLs)."),
            tools=["web_fetch", "read_file", "list_files", "grep"],
        ),
        PhaseSpec(
            id="analyze",
            objective=("Analyze the gathered material for: {goal}\nExtract the "
                       "load-bearing findings; separate facts from interpretation."),
            feeds_from=["gather"], depends_on=["gather"],
        ),
        PhaseSpec(
            id="report",
            objective=("Write the final report for: {goal}\nLead with conclusions; "
                       "every claim traceable to the analysis."),
            feeds_from=["gather", "analyze"], depends_on=["analyze"],
            hitl=True,
        ),
    ],
)

TEMPLATES: dict[str, WorkflowTemplate] = {
    t.id: t for t in (SOFTWARE_ENGINEERING, RESEARCH)
}


def get_template(template_id: str) -> Optional[WorkflowTemplate]:
    return TEMPLATES.get(template_id)


def list_templates() -> list[dict[str, Any]]:
    return [
        {"id": t.id, "label": t.label, "description": t.description,
         "phases": [{"id": p.id, "hitl": p.hitl, "hitl_timing": p.hitl_timing,
                     "task_type": p.task_type.value,
                     "tools": p.tools} for p in t.phases]}
        for t in TEMPLATES.values()
    ]
