"""Deterministic verification: turn a worker result into a checkable verdict.

The verifier hierarchy (from the eval literature): execution/state checks beat
schema checks beat judge opinions. This module implements the deterministic end —
the signals reliable enough to drive routing escalation and matrix updates. A
rubric judge can be layered on later for tasks with no checkable signal; those
come back UNVERIFIED here, never falsely PASS.

`success_check` vocabulary (on Task):
    {"equals": value}                  exact match (numeric tolerance for floats)
    {"equals": value, "tol": 1e-6}     explicit tolerance
    {"contains": "substring"}          raw_text or stringified output contains it
    {"one_of": [a, b, c]}              membership
"""
from __future__ import annotations

import math
from typing import Any, Optional

from metaharness.core.types import (
    MASTMode,
    Task,
    VerificationResult,
    Verdict,
    WorkerResult,
)
from metaharness.harness.enrichment import check_schema

MAX_TOL = 1.0  # a tol above this stops being a float-rounding tolerance and makes
               # non-adjacent answers match — treat as corruption / unscoreable.


def scoreable_tol(value: Any) -> Optional[float]:
    """A tol usable by math.isclose: coerce to a finite, non-negative float within MAX_TOL.
    Returns None if it is not (junk, non-finite, negative, over-large, or overflows float())."""
    try:
        tol = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(tol) or tol < 0 or tol > MAX_TOL:
        return None
    return tol


def scoreable_number(value: Any) -> bool:
    """True unless `value` is a numeric ground-truth that math.isclose cannot score: a huge int
    that overflows float() (e.g. a 400-digit int), OR a non-finite float — inf/nan, which
    eval_arithmetic("1e999")/("1e999-1e999") and a generator's JSON `1e999` all produce WITHOUT
    raising. Symmetric with scoreable_tol's math.isfinite check (Issue #9 panel: opus/codex/kimi/
    GLM converged — an inf equals PASSes any "inf" output, a nan equals FAILs every output).
    Non-numeric (str) equals/one_of members pass."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return True
    try:
        coerced = float(value)
    except OverflowError:
        return False
    return math.isfinite(coerced)


def check_value_problems(check: Optional[dict[str, Any]]) -> list[str]:
    """Human-readable VALUE-level hazards in a success_check — the source-side
    intake gate (Issue #10) for the workflow endpoints (app.py validate_workflow /
    start_run) and the planner's LLM-authored checks (plan_workflow, post-
    _derive_checks). Reuses the same scoreable_tol / scoreable_number policy as
    verify_output and optimization.suites.check_value_ok so a hand-crafted API
    body or an LLM plan can't smuggle in a value that later crashes or silently
    corrupts scoring. VALUE-level only: no vocabulary/shape enforcement — a
    string equals/one_of member is benign here (verify_output already treats it
    as non-numeric, per Issue #9) and passes through untouched. Empty list means
    no problems.

    TOTAL over untrusted input (issue-#10 panel P1, codex): the planner calls
    this on RAW LLM output, before WorkflowSpec.model_validate — a non-dict
    success_check ("oops", ["equals", 1]) must return [] here, not raise, or
    the planner's fallback contract breaks (500 instead of fallback_spec).
    Shape validation stays model_validate's job downstream."""
    if not isinstance(check, dict) or not check:
        return []
    problems: list[str] = []
    if "tol" in check and scoreable_tol(check["tol"]) is None:
        problems.append(f"tol {check['tol']!r} is not a finite tolerance in [0, {MAX_TOL}]")
    equals = check.get("equals")
    if "equals" in check and isinstance(equals, (int, float)) and not isinstance(equals, bool) \
            and not scoreable_number(equals):
        problems.append(f"equals {equals!r} is non-finite or overflowing")
    if "one_of" in check and isinstance(check["one_of"], list):
        for member in check["one_of"]:
            if isinstance(member, (int, float)) and not isinstance(member, bool) \
                    and not scoreable_number(member):
                problems.append(f"one_of member {member!r} is non-finite or overflowing")
    return problems


def _values_equal(got: Any, want: Any, tol: float = 1e-9) -> bool:
    if isinstance(got, bool) != isinstance(want, bool):
        return False
    # LLM workers answer in text: "3767" must satisfy an expected 3767
    if isinstance(want, (int, float)) and isinstance(got, str):
        try:
            got = float(got.strip().rstrip("."))
        except ValueError:  # non-numeric answer text; float(str) never raises OverflowError (→ inf)
            return False
    if isinstance(got, (int, float)) and isinstance(want, (int, float)):
        try:
            return math.isclose(float(got), float(want), rel_tol=tol, abs_tol=tol)
        except OverflowError:  # a huge numeric got/want/one_of member → non-match, never a crash
            return False
    if isinstance(got, str) and isinstance(want, str):
        return got.strip() == want.strip()  # LLM answers carry incidental whitespace
    return got == want


def verify_output(task: Task, result: WorkerResult) -> VerificationResult:
    """Deterministic verdict for one attempt. FAIL always carries a reason; a
    missing ground-truth signal yields UNVERIFIED, never a fake PASS.

    UNVERIFIED is the least-bad verdict for malformed ground truth: it avoids
    deterministic false-blame and matrix FAIL recording (gate.py:100 only records
    PASS/FAIL), but it is NOT fully neutral — the optimization loop still counts
    non-PASS as a miss (loop.py:125). (codex P2.)"""
    if result.error:
        mode = MASTMode.SCHEMA_INVALID if result.error.startswith("schema:") else MASTMode.TOOL_ERROR
        return VerificationResult(
            verdict=Verdict.FAIL, score=0.0, detail=result.error, failure_mode=mode,
            scorer="execution",
        )

    problems = check_schema(result.output, task.output_schema)
    if problems:
        return VerificationResult(
            verdict=Verdict.FAIL, score=0.0, detail="; ".join(problems),
            failure_mode=MASTMode.SCHEMA_INVALID, scorer="schema",
        )

    check = task.success_check
    if not check:
        return VerificationResult(
            verdict=Verdict.UNVERIFIED, score=0.5,
            detail="no checkable success signal for this task", scorer="none",
        )

    # malformed GROUND TRUTH → UNVERIFIED, never crash, never fake-PASS, never unfairly FAIL
    if ("tol" in check and scoreable_tol(check["tol"]) is None) \
            or not scoreable_number(check.get("equals")) \
            or ("one_of" in check and isinstance(check["one_of"], list)
                and not all(scoreable_number(v) for v in check["one_of"])):
        return VerificationResult(
            verdict=Verdict.UNVERIFIED, score=0.5,
            detail="success_check ground-truth value is not numerically scoreable", scorer="none")

    if "equals" in check:
        want = check["equals"]
        # the guard above already rejected an unscoreable present tol, so this is
        # non-None; `is None` (not `or`) preserves an explicit tol=0 (exact match)
        # instead of silently widening it to the 1e-9 default.
        tol = scoreable_tol(check.get("tol", 1e-9))
        if tol is None:
            tol = 1e-9
        if _values_equal(result.output, want, tol):
            return VerificationResult(verdict=Verdict.PASS, score=1.0, scorer="deterministic")
        return VerificationResult(
            verdict=Verdict.FAIL, score=0.0,
            detail=f"expected {want!r}, got {result.output!r}", scorer="deterministic",
        )

    if "contains" in check:
        needle = str(check["contains"]).lower()
        haystack = (result.raw_text or str(result.output)).lower()
        if needle in haystack:
            return VerificationResult(verdict=Verdict.PASS, score=1.0, scorer="deterministic")
        return VerificationResult(
            verdict=Verdict.FAIL, score=0.0,
            detail=f"output does not contain {check['contains']!r}", scorer="deterministic",
        )

    if "one_of" in check:
        allowed = check["one_of"]
        got = result.output
        if isinstance(got, str):  # labels are semantic: "Low" satisfies "low"
            got = got.strip().lower()
            allowed = [v.lower() if isinstance(v, str) else v for v in allowed]
        if any(_values_equal(got, v) for v in allowed):
            return VerificationResult(verdict=Verdict.PASS, score=1.0, scorer="deterministic")
        return VerificationResult(
            verdict=Verdict.FAIL, score=0.0,
            detail=f"{result.output!r} not in {allowed!r}", scorer="deterministic",
        )

    return VerificationResult(
        verdict=Verdict.UNVERIFIED, score=0.5,
        detail=f"unrecognized success_check keys: {sorted(check)}", scorer="none",
    )


def authenticity_failure(detail: str) -> VerificationResult:
    """The result's signature didn't verify against the registry — the attempt is
    rejected regardless of content. Not recorded in the capability matrix (it says
    nothing about the model's skill)."""
    return VerificationResult(
        verdict=Verdict.FAIL, score=0.0, detail=detail,
        failure_mode=MASTMode.DISOBEY_ROLE_SPEC, scorer="authenticity",
    )
