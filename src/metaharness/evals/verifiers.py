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


def _values_equal(got: Any, want: Any, tol: float = 1e-9) -> bool:
    if isinstance(got, bool) != isinstance(want, bool):
        return False
    # LLM workers answer in text: "3767" must satisfy an expected 3767
    if isinstance(want, (int, float)) and isinstance(got, str):
        try:
            got = float(got.strip().rstrip("."))
        except ValueError:
            return False
    if isinstance(got, (int, float)) and isinstance(want, (int, float)):
        return math.isclose(float(got), float(want), rel_tol=tol, abs_tol=tol)
    if isinstance(got, str) and isinstance(want, str):
        return got.strip() == want.strip()  # LLM answers carry incidental whitespace
    return got == want


def verify_output(task: Task, result: WorkerResult) -> VerificationResult:
    """Deterministic verdict for one attempt. FAIL always carries a reason; a
    missing ground-truth signal yields UNVERIFIED, never a fake PASS."""
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

    if "equals" in check:
        want = check["equals"]
        tol = float(check.get("tol", 1e-9))
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
