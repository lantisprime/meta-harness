"""YAML workflow DSL — prescribed workflows as data, not code.

A workflow is a DAG of steps. Each step is a full delegation contract (objective,
inputs, boundaries, output schema, success check) plus orchestration hints
(dependencies, tier hint, HITL gate). Example:

    name: triage
    steps:
      - id: classify
        task_type: classify
        objective: Classify the ticket severity.
        inputs: {text: "$context.ticket"}
        success_check: {one_of: [low, medium, high]}
      - id: summarize
        task_type: summarize
        objective: Summarize for the on-call engineer.
        depends_on: [classify]
        inputs: {severity: "$steps.classify.output"}
        hitl: true

`$context.<key>` pulls from the run context; `$steps.<id>.output[...]` pulls an
upstream step's verified output.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, Optional

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from metaharness.core.types import Task, TaskType, Tier


class StepSpec(BaseModel):
    id: str
    objective: str
    task_type: TaskType = TaskType.GENERAL
    inputs: dict[str, Any] = Field(default_factory=dict)
    output_schema: Optional[dict[str, Any]] = None
    boundaries: list[str] = Field(default_factory=list)
    success_check: Optional[dict[str, Any]] = None
    tier_hint: Optional[Tier] = None
    max_attempts: int = 3
    depends_on: list[str] = Field(default_factory=list)
    hitl: bool = False    # require human approval at hitl_timing
    hitl_timing: Literal["before", "after"] = "before"
    tools: list[str] = Field(default_factory=list)  # tool subset for this step
    # conditional execution: {"step": "<id>", "equals"|"contains"|"one_of": value,
    # "negate": bool} — evaluated deterministically against the referenced step's
    # output at advance-time; unmet -> this step is journaled as skipped
    when: Optional[dict[str, Any]] = None

    def to_task(self, resolved_inputs: dict[str, Any]) -> Task:
        boundaries = list(self.boundaries)
        # a one_of check is a format contract the worker deserves to see spelled
        # out — exact-match verification punishes prose around a right answer
        if self.success_check and "one_of" in self.success_check:
            allowed = ", ".join(repr(v) for v in self.success_check["one_of"])
            boundaries.append(
                f"Respond with exactly one of the following values and nothing else: {allowed}."
            )
        return Task(
            task_type=self.task_type,
            objective=self.objective,
            inputs=resolved_inputs,
            output_schema=self.output_schema,
            boundaries=boundaries,
            success_check=self.success_check,
            tier_hint=self.tier_hint,
            max_attempts=self.max_attempts,
            tools=list(self.tools),
        )


class WorkflowSpec(BaseModel):
    name: str
    steps: list[StepSpec]

    @field_validator("steps")
    @classmethod
    def _unique_ids(cls, steps: list[StepSpec]) -> list[StepSpec]:
        ids = [s.id for s in steps]
        if len(set(ids)) != len(ids):
            dupes = sorted({i for i in ids if ids.count(i) > 1})
            raise ValueError(f"duplicate step ids: {dupes}")
        return steps

    @model_validator(mode="after")
    def _deps_exist_and_acyclic(self) -> "WorkflowSpec":
        ids = {s.id for s in self.steps}
        for step in self.steps:
            unknown = set(step.depends_on) - ids
            if unknown:
                raise ValueError(f"step {step.id!r} depends on unknown steps: {sorted(unknown)}")
            if step.when is not None:
                ref = step.when.get("step")
                if not ref or ref not in ids:
                    raise ValueError(
                        f"step {step.id!r} 'when' references unknown step {ref!r}")
                if ref == step.id:
                    raise ValueError(f"step {step.id!r} 'when' cannot reference itself")
                kinds = [k for k in ("equals", "contains", "one_of") if k in step.when]
                if len(kinds) != 1:
                    raise ValueError(
                        f"step {step.id!r} 'when' needs exactly one of equals/contains/one_of")
                if ref not in step.depends_on:
                    # the condition source must have run first — make it explicit
                    step.depends_on.append(ref)
        self.topological_order()  # raises on cycles
        return self

    def step(self, step_id: str) -> StepSpec:
        for s in self.steps:
            if s.id == step_id:
                return s
        raise KeyError(step_id)

    def topological_order(self) -> list[StepSpec]:
        """Dependency-respecting order (stable w.r.t. declaration order)."""
        done: set[str] = set()
        ordered: list[StepSpec] = []
        remaining = list(self.steps)
        while remaining:
            progress = [s for s in remaining if set(s.depends_on) <= done]
            if not progress:
                raise ValueError(
                    f"dependency cycle among steps: {sorted(s.id for s in remaining)}"
                )
            for s in progress:
                done.add(s.id)
                ordered.append(s)
            remaining = [s for s in remaining if s.id not in done]
        return ordered


def load_workflow(source: str | Path) -> WorkflowSpec:
    """Load a WorkflowSpec from a YAML file path or a YAML string."""
    text = source
    if isinstance(source, Path) or (isinstance(source, str) and "\n" not in source and source.endswith((".yml", ".yaml"))):
        text = Path(source).read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    return WorkflowSpec.model_validate(data)


def resolve_reference(value: Any, context: dict[str, Any], outputs: dict[str, Any]) -> Any:
    """Resolve `$context.key` / `$steps.id.output[.subkey...]` references in step
    inputs. Non-string values and non-reference strings pass through untouched."""
    if isinstance(value, dict):
        return {k: resolve_reference(v, context, outputs) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_reference(v, context, outputs) for v in value]
    if not isinstance(value, str) or not value.startswith("$"):
        return value
    parts = value[1:].split(".")
    if parts[0] == "context":
        node: Any = context
        path = parts[1:]
    elif parts[0] == "steps":
        if len(parts) < 3 or parts[2] != "output":
            raise ValueError(f"bad step reference {value!r}; use $steps.<id>.output[...]")
        if parts[1] not in outputs:
            raise ValueError(f"step reference {value!r} points to a step with no recorded output")
        node = outputs[parts[1]]
        path = parts[3:]
    else:
        raise ValueError(f"unknown reference root {value!r}")
    for key in path:
        if isinstance(node, dict) and key in node:
            node = node[key]
        else:
            raise ValueError(f"reference {value!r}: {key!r} not found")
    return node


def when_satisfied(when: dict[str, Any], outputs: dict[str, Any]) -> bool:
    """Deterministic branch predicate: does the referenced step's output meet
    the condition? A missing/skipped source counts as NOT satisfied."""
    ref = when["step"]
    if ref not in outputs:
        return False
    output = outputs[ref]
    text = output.strip() if isinstance(output, str) else str(output).strip()
    if "equals" in when:
        want = when["equals"]
        ok = output == want or text == str(want).strip()
    elif "contains" in when:
        ok = str(when["contains"]) in (output if isinstance(output, str) else str(output))
    else:
        ok = text in [str(v).strip() for v in when.get("one_of", [])]
    return (not ok) if when.get("negate") else ok


def describe_when(when: dict[str, Any]) -> str:
    kind = next(k for k in ("equals", "contains", "one_of") if k in when)
    value = when[kind]
    if isinstance(value, list):
        value = ", ".join(str(v) for v in value)
    prefix = "unless" if when.get("negate") else "if"
    return f"{prefix} {when['step']} {kind} {value}"
