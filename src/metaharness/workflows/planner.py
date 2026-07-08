"""Plan-then-execute: turn a natural-language goal into a WorkflowSpec.

The meta move: the harness itself asks its most capable worker to decompose a
goal into the same YAML-DSL steps a human would write — typed task steps with
dependencies, checkable success signals where they exist, and a HITL gate on the
final outward-facing step. The plan is validated against the DSL (unique ids,
acyclic deps, known task types); an invalid or unparseable plan falls back to a
single-step workflow rather than failing the launch — the fallback is explicit
in the return value and in provenance, never silent.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from metaharness.core.types import Task, TaskType
from metaharness.harness.runner import Runner
from metaharness.observability.tracing import tracer
from metaharness.workflows.dsl import WorkflowSpec

PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["name", "steps"],
    "properties": {
        "name": {"type": "string"},
        "steps": {"type": "array"},
    },
}

PLANNER_PROMPT = """\
Decompose the goal below into a workflow of typed steps for worker agents.

Rules:
- Each step: short kebab-case "id", one "task_type" from {task_types},
  a precise "objective" (the full delegation contract for that step),
  optional "inputs" — a JSON OBJECT mapping input names to values, where values
  may reference "$context.<key>" or "$steps.<id>.output"
  (e.g. {{"text": "$context.report", "severity": "$steps.classify.output"}}),
  "depends_on" (list of step ids), and optional "success_check"
  ({{"equals": v}} / {{"contains": s}} / {{"one_of": [...]}}) when the result is
  mechanically checkable — omit it when it is not; never invent a fake check.
  Example checkable step: {{"id": "classify-urgency", "task_type": "classify",
  "objective": "Classify urgency as exactly one of: low, high.",
  "inputs": {{"ticket": "$context.ticket"}}, "success_check": {{"one_of": ["low", "high"]}}}}
- For arithmetic steps ALWAYS put the bare expression in inputs, e.g.
  {{"expression": "340 * 6"}} — the harness evaluates it exactly.
- Prefer few, well-scoped steps over many vague ones.
- Set "hitl": true on any step whose result leaves the system (sending,
  publishing, paging) so a human approves it first.
{tool_rules}
Goal: {goal}

Context keys available: {context_keys}

Return ONLY a JSON object: {{"name": "...", "steps": [...]}}
"""

TOOL_RULES = """\
- If a step genuinely needs to touch files, the web, or external systems, set
  "tools": [...] on that step, choosing ONLY from this catalog (name: purpose):
{catalog}
  Most steps need NO tools — omit the field unless the objective is impossible
  without one.
"""


def _assign_tools(plan: dict[str, Any], registry) -> dict[str, Any]:
    """Final tool subset per step: planner-proposed names that exist in the
    registry, unioned with deterministic keyword detection over the objective.
    Both signals are conservative — no signal means NO tools for the step."""
    from metaharness.tools.registry import DEFAULT_SUBSET_CAP

    for step in plan.get("steps", []):
        if not isinstance(step, dict):
            continue
        proposed = step.get("tools")
        valid = [t for t in proposed if registry.get(t)] if isinstance(proposed, list) else []
        detected = registry.select_for(
            str(step.get("objective", "")),
            [str(b) for b in step.get("boundaries", []) if b] or None,
        )
        merged = valid + [t for t in detected if t not in valid]
        step["tools"] = merged[:DEFAULT_SUBSET_CAP]
    return plan


def _slug(text: str, limit: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:limit] or "goal"


def fallback_spec(goal: str, context: Optional[dict[str, Any]] = None) -> WorkflowSpec:
    """One honest step wrapping the goal, with the full run context handed
    through as inputs. UNVERIFIED rather than fake-checked."""
    return WorkflowSpec.model_validate({
        "name": _slug(goal),
        "steps": [{
            "id": "do-goal",
            "task_type": TaskType.GENERAL.value,
            "objective": goal,
            "inputs": {key: f"$context.{key}" for key in (context or {})},
        }],
    })


def _normalize_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """Forgive common LLM shape drift without changing meaning:
    - inputs given as a list of references → a dict keyed by the referenced name
      ("$context.report" → "report", "$steps.classify.output" → "classify")
    - depends_on given as a string → one-element list
    """
    for step in plan.get("steps", []):
        if not isinstance(step, dict):
            continue
        inputs = step.get("inputs")
        if isinstance(inputs, list):
            normalized: dict[str, Any] = {}
            for i, item in enumerate(inputs):
                key = f"input_{i}"
                if isinstance(item, str) and item.startswith("$"):
                    parts = item[1:].split(".")
                    if parts[0] == "steps" and len(parts) >= 2:
                        key = parts[1]
                    elif len(parts) >= 2:
                        key = parts[-1]
                while key in normalized:
                    key = f"{key}_{i}"
                normalized[key] = item
            step["inputs"] = normalized
        deps = step.get("depends_on")
        if isinstance(deps, str):
            step["depends_on"] = [deps]
    return plan


_ONE_OF_RE = re.compile(r"exactly one of:?\s*([A-Za-z0-9_'\" ,-]+)", re.IGNORECASE)
# "the exact word 'X'" / "exactly the phrase \"X\"" — a quoted literal near "exact"
_EXACT_QUOTED_RE = re.compile(r"exact(?:ly)?[^:'\"]{0,24}['\"]([^'\"]+)['\"]", re.IGNORECASE)
# "return exactly the word: X" — colon form, single token
_EXACT_COLON_RE = re.compile(r"exactly[^:]{0,24}:\s*(\S+)", re.IGNORECASE)


def _derive_checks(plan: dict[str, Any]) -> dict[str, Any]:
    """Synthesize success_checks the harness can vouch for itself, when the
    planner omitted them (models reliably state the constraint in the objective
    but reliably forget the schema field — observed run_ef22d875cfa3):

    - classify: "exactly one of: low, high" in the objective → one_of check
    - arithmetic with an `expression` input → the sandbox computes the expected
      value; ground truth comes from the harness, not from any model
    """
    from metaharness.harness.sandbox import SandboxError, eval_arithmetic

    for step in plan.get("steps", []):
        if not isinstance(step, dict) or step.get("success_check"):
            continue
        task_type = step.get("task_type", "")
        objective = str(step.get("objective", ""))
        if task_type == "classify":
            match = _ONE_OF_RE.search(objective)
            if match:
                options = [
                    o.strip(" .'\"") for o in re.split(r",|\bor\b", match.group(1))
                    if o.strip(" .'\"")
                ]
                if 2 <= len(options) <= 8:
                    step["success_check"] = {"one_of": options}
        elif task_type == "arithmetic":
            expression = (step.get("inputs") or {}).get("expression")
            if isinstance(expression, str):
                try:
                    step["success_check"] = {"equals": eval_arithmetic(expression)}
                except SandboxError:
                    pass
        elif task_type in ("extract", "transform", "general"):
            # a literal expected answer stated in the objective is checkable
            match = _EXACT_QUOTED_RE.search(objective) or _EXACT_COLON_RE.search(objective)
            if match:
                literal = match.group(1).strip().rstrip(".,;")
                if literal:
                    step["success_check"] = {"equals": literal}
    return plan


def _coerce_plan(raw: Any) -> Optional[dict[str, Any]]:
    """Best-effort extraction of a plan dict from a worker's output."""
    if isinstance(raw, dict) and "steps" in raw:
        return raw
    if isinstance(raw, str):
        start, end = raw.find("{"), raw.rfind("}")
        if 0 <= start < end:
            try:
                data = json.loads(raw[start : end + 1])
                if isinstance(data, dict) and "steps" in data:
                    return data
            except ValueError:
                return None
    return None


async def plan_workflow(
    goal: str,
    planner: Runner,
    context: Optional[dict[str, Any]] = None,
    tools=None,
) -> tuple[WorkflowSpec, str]:
    """Returns (spec, source) where source is "planner" or "fallback".
    `tools` is the harness ToolRegistry; when given, the planner sees the
    catalog and each step gets its (small) tool subset assigned."""
    context = context or {}
    tool_rules = ""
    if tools is not None and tools.names():
        catalog = "\n".join(
            f"    {t.name}: {t.description}" for t in tools.all()
        )
        tool_rules = TOOL_RULES.format(catalog=catalog)
    task = Task(
        task_type=TaskType.PLANNING,
        objective=PLANNER_PROMPT.format(
            goal=goal,
            task_types=", ".join(t.value for t in TaskType),
            context_keys=sorted(context) or "(none)",
            tool_rules=tool_rules,
        ),
        inputs={"goal": goal},
        output_schema=PLAN_SCHEMA,
    )
    with tracer().start_as_current_span("workflow.plan") as span:
        span.set_attribute("plan.goal", goal[:200])
        span.set_attribute("plan.model", planner.model)
        result = await planner.run(task)
        plan = None if result.error else _coerce_plan(result.output)
        if plan is not None:
            plan = _derive_checks(_normalize_plan(plan))
            if tools is not None:
                plan = _assign_tools(plan, tools)
            plan.setdefault("name", _slug(goal))
            try:
                spec = WorkflowSpec.model_validate(plan)
                span.set_attribute("plan.source", "planner")
                span.set_attribute("plan.steps", len(spec.steps))
                return spec, "planner"
            except ValueError as exc:
                span.set_attribute("plan.invalid", str(exc)[:200])
        span.set_attribute("plan.source", "fallback")
        return fallback_spec(goal, context), "fallback"
