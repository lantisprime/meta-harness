"""Grounded reflection: turn a failed attempt into advice for the next one.

The research consensus is blunt: intrinsic self-correction ("think again") doesn't
work; reflection helps only when grounded in an external signal (CRITIC, Reflexion).
So this reflector speaks only from the verifier's evidence — and it deliberately
does NOT leak the expected answer for equality checks, because "the answer is X"
isn't learning, it's dictation. Spec-level requirements (schema shape, required
substring) are fair game: the worker was already entitled to them.

Framing note (arXiv 2606.05976, "The Self-Correction Illusion"): models refute an
erroneous claim far more reliably when it is presented as an external, addressable
artifact than as their own prior thought. Reflections therefore describe "a
previous attempt" in the third person — never "you were wrong" — and arrive via
the task contract's boundaries, i.e. an external role.
"""
from __future__ import annotations

from typing import Optional

from metaharness.core.types import Attempt, MASTMode, Task, Verdict


def grounded_reflector(task: Task, attempt: Attempt) -> Optional[str]:
    verification = attempt.verification
    if verification.verdict != Verdict.FAIL:
        return None
    mode = verification.failure_mode

    if mode == MASTMode.SCHEMA_INVALID:
        return (
            f"A previous attempt at this task violated the required schema: "
            f"{verification.detail}. Return output that matches the schema exactly — "
            "every required key, correct types."
        )
    if mode == MASTMode.TOOL_ERROR:
        return (
            f"A previous attempt failed with an execution error: {verification.detail}. "
            "Simplify the approach and avoid whatever triggered the error."
        )
    if mode == MASTMode.TIMEOUT:
        # advice must fit what a retry can do — raising the timeout is a
        # config action, not something the worker itself can choose (issue #2)
        return (
            "A previous attempt ran out of time before finishing. Take the most "
            "direct path to the objective and keep the change minimal."
        )
    if mode == MASTMode.BUDGET_EXCEEDED:
        return None  # no next attempt is coming; advice would be noise

    check = task.success_check or {}
    if "contains" in check:
        return (
            f"A previous attempt was rejected because its output did not include the "
            f"required content {check['contains']!r}. Make sure it appears explicitly."
        )
    if "one_of" in check:
        return (
            f"A previous attempt returned {attempt.result.output!r}, which is not one "
            f"of the allowed values {check['one_of']!r}. Choose exactly one of them."
        )
    if "equals" in check:
        # grounded but not leaked: name what was wrong, not what is right
        return (
            f"A previous attempt returned {attempt.result.output!r}; the verifier "
            "checked it against the task's expected result and rejected it. "
            "Re-read the objective and inputs, reason step by step, and answer again."
        )
    return (
        f"A previous attempt failed verification: {verification.detail or 'no detail'}. "
        "Take a materially different approach."
    )
