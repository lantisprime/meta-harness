"""Rubric judge: LLM evaluation of steps that have no deterministic check.

Slot 3 of the verifier hierarchy (execution > deterministic scorer > RUBRIC
JUDGE > human): when a step's output can't be checked mechanically, a separate
LLM call — fresh context, external-role framing per arXiv 2606.05976, never
the transcript that produced the output — grades it against the step's own
delegation contract. A FAIL feeds the normal retry/reflect/escalate loop, so
a bad intermediate output is reworked BEFORE dependent steps consume it.

Honesty rules: the judge returns strict JSON; anything unparseable degrades to
UNVERIFIED (never a fabricated verdict), and judge verdicts are stamped
scorer="judge" so downstream consumers can always tell rubric grades from
deterministic ones.
"""
from __future__ import annotations

import json
from typing import Any, Awaitable, Callable, Optional

from metaharness.core.budget import Budget
from metaharness.core.types import (
    MASTMode,
    Task,
    TaskType,
    VerificationResult,
    Verdict,
    WorkerResult,
)
from metaharness.harness.runner import Runner

JUDGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["pass", "reason"],
    "properties": {
        "pass": {"type": "boolean"},
        "reason": {"type": "string"},
    },
}

JUDGE_PROMPT = """\
You are grading an artifact produced by a worker agent. You did not produce it;
judge it with fresh eyes against its contract.

The task the worker was given:
{objective}
{boundaries}
The artifact the worker returned:
{output}
{evidence}
Does the artifact actually satisfy the task — complete, on-scope, and usable
by the next step of the workflow? Judge substance, not style. An artifact that
asserts success without doing the work fails.{evidence_rule}

Respond with ONLY a JSON object: {{"pass": true/false, "reason": "<one sentence>"}}
"""

_EVIDENCE_RULE = """
When workspace evidence is shown above, it is ground truth: the task's real
deliverable may live in those files. Grade the FILES against the contract —
a weak text summary over correct, complete files passes; a confident text
summary over missing or wrong files fails."""

_MAX_OUTPUT_CHARS = 6_000

Judge = Callable[[Task, WorkerResult], Awaitable[VerificationResult]]


def make_judge(judge_runner: Runner, budget: Optional[Budget] = None) -> Judge:
    """Build the executor's judge hook from a (capable) runner. The runner is
    called with a fresh, depersonalized grading task per evaluation.

    `budget`, when given, charges the judge runner's own cost/tokens/wall-clock
    against the shared budget (charge-always semantics, issue #5 item 3b) —
    otherwise a judge's worker spend is invisible to the run's budget. A
    BudgetExceeded from this charge propagates to the caller (the executor's
    judge except-block handles it like the charge-site case, never as a
    swallowed judge.error)."""

    async def judge(task: Task, result: WorkerResult) -> VerificationResult:
        from metaharness.evals.evidence import (
            attempt_window_start,
            collect_evidence,
            render_evidence,
        )

        output = result.raw_text or result.output
        text = output if isinstance(output, str) else json.dumps(
            output, ensure_ascii=False, default=str)
        if len(text) > _MAX_OUTPUT_CHARS:
            text = text[:_MAX_OUTPUT_CHARS] + "…[truncated]"
        boundaries = ""
        if task.boundaries:
            boundaries = "Constraints it had to respect:\n" + "\n".join(
                f"- {b}" for b in task.boundaries) + "\n"
        # the judge's blind spot (observed live: false-negative FAILs on
        # code_edit steps): workers that do their work through file tools or a
        # CLI subprocess return narration as text — collect what actually
        # changed in the recorded workspace root and grade that too
        evidence = collect_evidence(
            result.workspace_root,
            attempt_window_start(result.latency_s),
            result.tool_calls,
        )
        evidence_text = ("\n" + render_evidence(evidence) + "\n") if evidence else ""
        grading_task = Task(
            task_type=TaskType.REASONING,
            objective=JUDGE_PROMPT.format(
                objective=task.objective, boundaries=boundaries, output=text,
                evidence=evidence_text,
                evidence_rule=_EVIDENCE_RULE if evidence else ""),
            output_schema=JUDGE_SCHEMA,
            max_attempts=1,
        )
        graded = await judge_runner.run(grading_task)
        # charge always: even a broken/unparseable judge call spent real tokens
        if budget is not None:
            budget.charge(
                cost_usd=graded.cost_usd, tokens=graded.tokens_in + graded.tokens_out,
                wall_s=graded.latency_s,
            )
        verdict_data = graded.output if isinstance(graded.output, dict) else None
        if graded.error or verdict_data is None or "pass" not in verdict_data:
            # no usable judgment is NOT a failure — it is honest uncertainty
            return VerificationResult(
                verdict=Verdict.UNVERIFIED, score=0.0,
                detail="judge unavailable or unparseable", scorer="judge")
        passed = bool(verdict_data["pass"])
        reason = str(verdict_data.get("reason", ""))[:300]
        return VerificationResult(
            verdict=Verdict.PASS if passed else Verdict.FAIL,
            score=1.0 if passed else 0.0,
            detail=f"judge: {reason}",
            failure_mode=None if passed else MASTMode.DISOBEY_TASK_SPEC,
            scorer="judge",
        )

    return judge
