"""Enrichment wrappers: scaffolding that lifts a cheap worker toward frontier
quality on the task shapes where scaffolding is known to work.

Each wrapper is itself a Runner, so they compose:

    SelfConsistency(ToolOffload(MockLLMWorker(...)), k=5)

- ToolOffload (PAL): for arithmetic, ask the worker to *write the computation*
  and evaluate it exactly in the sandbox. Small models transcribe far more
  reliably than they compute.
- SelfConsistency: sample k answers and majority-vote. Wrong answers scatter;
  right answers agree.
- SchemaGuard: check the output against the task's output schema and retry once
  with the violation named. Malformed output is the cheapest failure to catch.

Wrappers that rewrite the output re-sign it with the wrapped worker's key — the
enrichment runs inside the same harness boundary, and the signature must vouch
for what is actually returned.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Optional

from metaharness.core.budget import action_signature
from metaharness.core.types import Task, TaskType, WorkerResult
from metaharness.harness.runner import Runner, sign_result
from metaharness.harness.sandbox import SandboxError, eval_arithmetic
from metaharness.observability.tracing import tracer


class _Wrapper(Runner):
    def __init__(self, inner: Runner) -> None:
        self.inner = inner

    @property
    def worker_id(self) -> str:  # type: ignore[override]
        return self.inner.worker_id

    @property
    def tier(self):  # type: ignore[override]
        return self.inner.tier

    @property
    def model(self) -> str:  # type: ignore[override]
        return self.inner.model

    def _resign(self, result: WorkerResult) -> WorkerResult:
        keypair = getattr(self.inner, "keypair", None)
        if keypair is not None:
            sign_result(result, keypair)
        return result


class ToolOffload(_Wrapper):
    """PAL: reframe arithmetic as 'emit the program', evaluate exactly."""

    async def run(self, task: Task) -> WorkerResult:
        if task.task_type != TaskType.ARITHMETIC or "expression" not in task.inputs:
            return await self.inner.run(task)
        with tracer().start_as_current_span("enrich.tool_offload") as span:
            span.set_attribute("task.id", task.id)
            subtask = task.model_copy(deep=True)
            subtask.inputs["emit_program"] = True
            subtask.objective = (
                "Write the arithmetic expression that computes the answer. "
                "Do not compute it yourself. " + task.objective
            )
            result = await self.inner.run(subtask)
            result.task_id = task.id
            program = (result.output or {}).get("program") if isinstance(result.output, dict) else None
            if not program:
                result.error = result.error or "tool_offload: worker did not emit a program"
                return self._resign(result)
            try:
                value = eval_arithmetic(str(program))
            except SandboxError as exc:
                result.error = f"tool_offload: {exc}"
                return self._resign(result)
            result.tool_calls.append(
                {"tool": "python.eval_arithmetic", "input": str(program), "output": value}
            )
            result.output = value
            result.raw_text = str(value)
            span.set_attribute("enrich.program", str(program))
            return self._resign(result)


class SelfConsistency(_Wrapper):
    """Sample k answers, return the majority. Aggregates cost/tokens across
    samples so the router sees the true price of the enrichment."""

    def __init__(self, inner: Runner, k: int = 5) -> None:
        super().__init__(inner)
        self.k = max(1, k)

    async def run(self, task: Task) -> WorkerResult:
        if self.k == 1:
            return await self.inner.run(task)
        with tracer().start_as_current_span("enrich.self_consistency") as span:
            span.set_attribute("task.id", task.id)
            span.set_attribute("enrich.k", self.k)
            samples = [await self.inner.run(task) for _ in range(self.k)]
            ok = [s for s in samples if not s.error]
            if not ok:
                return samples[-1]
            votes = Counter(action_signature(s.output) for s in ok)
            winner_sig, count = votes.most_common(1)[0]
            winner = next(s for s in ok if action_signature(s.output) == winner_sig)
            winner.tokens_in = sum(s.tokens_in for s in samples)
            winner.tokens_out = sum(s.tokens_out for s in samples)
            winner.cost_usd = sum(s.cost_usd for s in samples)
            winner.latency_s = sum(s.latency_s for s in samples)
            span.set_attribute("enrich.agreement", count / len(ok))
            return winner  # tokens/cost aren't signed; output is the untouched sample's


CRITIQUE_PROMPT = (
    "A worker agent produced the draft answer given in the inputs, for this "
    "objective:\n{objective}\n\n"
    "Review the draft against the objective. List the most important concrete "
    "flaws — missing requirements, wrong assumptions, vague or unactionable "
    "parts — as short bullet points. If the draft fully satisfies the "
    "objective, reply with exactly NO_ISSUES and nothing else."
)


class SelfCritique(_Wrapper):
    """Iterative self-critique for tasks with NO checkable success signal.

    Evidence (arXiv 2512.24103): intrinsic self-critique lifts planning-shaped
    work when it is *iterative* — draft → critique → revise — not "think again".
    And per arXiv 2606.05976, the draft is presented to the critic and the
    reviser as an external artifact ("a worker produced this"), which is the
    framing models actually correct against.

    Only fires where the verifier would return UNVERIFIED (no success_check, no
    schema) and the task type is open-ended; checkable tasks keep their cheaper
    verify-and-escalate loop. Rounds default to 1 — reflection gains die fast.
    """

    APPLIES_TO = (TaskType.PLANNING, TaskType.REASONING, TaskType.GENERAL,
                  TaskType.SUMMARIZE)

    def __init__(self, inner: Runner, rounds: int = 1) -> None:
        super().__init__(inner)
        self.rounds = max(1, rounds)

    async def run(self, task: Task) -> WorkerResult:
        if task.success_check or task.output_schema or task.task_type not in self.APPLIES_TO:
            return await self.inner.run(task)
        with tracer().start_as_current_span("enrich.self_critique") as span:
            span.set_attribute("task.id", task.id)
            result = await self.inner.run(task)
            if result.error:
                return result
            extra_in = extra_out = 0
            extra_cost = extra_latency = 0.0
            rounds_used = 0
            for _ in range(self.rounds):
                critique_task = Task(
                    task_type=TaskType.REASONING,
                    objective=CRITIQUE_PROMPT.format(objective=task.objective),
                    inputs={"draft": result.raw_text or str(result.output)},
                )
                critique = await self.inner.run(critique_task)
                extra_in += critique.tokens_in; extra_out += critique.tokens_out
                extra_cost += critique.cost_usd; extra_latency += critique.latency_s
                critique_text = (critique.raw_text or "").strip()
                if critique.error or not critique_text or "NO_ISSUES" in critique_text:
                    break
                revise_task = task.model_copy(deep=True)
                revise_task.inputs = {
                    **task.inputs,
                    "previous_draft": result.raw_text or str(result.output),
                    "reviewer_critique": critique_text,
                }
                revise_task.boundaries = list(task.boundaries) + [
                    "A reviewer examined a previous worker's draft (in inputs as "
                    "'previous_draft') and found the issues listed in "
                    "'reviewer_critique'. Produce an improved answer that fixes "
                    "those issues while still satisfying the original objective.",
                ]
                revised = await self.inner.run(revise_task)
                extra_in += revised.tokens_in; extra_out += revised.tokens_out
                extra_cost += revised.cost_usd; extra_latency += revised.latency_s
                if revised.error:
                    break
                revised.task_id = task.id
                result = revised
                rounds_used += 1
            result.tokens_in += extra_in
            result.tokens_out += extra_out
            result.cost_usd += extra_cost
            result.latency_s += extra_latency
            span.set_attribute("enrich.critique_rounds", rounds_used)
            return self._resign(result)


def check_schema(output: Any, schema: Optional[dict[str, Any]]) -> list[str]:
    """Minimal JSON-schema-shaped check: required keys + primitive types.
    Returns a list of violations (empty = conforming)."""
    if not schema:
        return []
    problems: list[str] = []
    if schema.get("type") == "object" or "required" in schema or "properties" in schema:
        if not isinstance(output, dict):
            return [f"expected object, got {type(output).__name__}"]
        for key in schema.get("required", []):
            if key not in output:
                problems.append(f"missing required key {key!r}")
        type_map = {
            "string": str, "number": (int, float), "integer": int,
            "boolean": bool, "array": list, "object": dict,
        }
        for key, spec in (schema.get("properties") or {}).items():
            if key in output and spec.get("type") in type_map:
                expected = type_map[spec["type"]]
                value = output[key]
                if isinstance(value, bool) and spec["type"] in ("number", "integer"):
                    problems.append(f"key {key!r}: expected {spec['type']}, got boolean")
                elif not isinstance(value, expected):
                    problems.append(
                        f"key {key!r}: expected {spec['type']}, got {type(value).__name__}"
                    )
    return problems


class SchemaGuard(_Wrapper):
    """Validate output against task.output_schema; retry once naming the
    violations, then surface a schema error the verifier can classify."""

    def __init__(self, inner: Runner, max_retries: int = 1) -> None:
        super().__init__(inner)
        self.max_retries = max_retries

    async def run(self, task: Task) -> WorkerResult:
        result = await self.inner.run(task)
        if not task.output_schema:
            return result
        problems = check_schema(result.output, task.output_schema)
        retries = 0
        while problems and retries < self.max_retries:
            retries += 1
            with tracer().start_as_current_span("enrich.schema_retry") as span:
                span.set_attribute("task.id", task.id)
                span.set_attribute("enrich.violations", "; ".join(problems))
                retry_task = task.model_copy(deep=True)
                retry_task.objective = (
                    f"{task.objective}\nYour previous output violated the schema: "
                    f"{'; '.join(problems)}. Return output matching the schema exactly."
                )
                result = await self.inner.run(retry_task)
                result.task_id = task.id
                self._resign(result)
                problems = check_schema(result.output, task.output_schema)
        if problems:
            result.error = f"schema: {'; '.join(problems)}"
            self._resign(result)
        return result
