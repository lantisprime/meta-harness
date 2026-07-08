"""Shared domain types for the meta-harness.

These are the vocabulary the whole system speaks: tasks flow in, get routed to a
capability tier, executed by a worker harness, verified, and (on failure) reflected
on. Everything is a plain pydantic model so it serializes cleanly into the journal,
the provenance log, and the WebUI API.
"""
from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def now() -> float:
    return time.time()


class Tier(str, Enum):
    """Capability tiers. The router picks the cheapest tier likely to succeed and
    escalates on a verifiable failure signal."""

    SMALL = "small"    # haiku / 8B-class: narrow, verifiable, single-pass work
    MID = "mid"        # sonnet-class: multi-step but bounded
    FRONTIER = "frontier"  # opus/fable-class: long-horizon, ambiguous, novel


class TaskType(str, Enum):
    """Coarse task taxonomy. Drives the capability matrix and the enrichment stack.
    A prescribed workflow tags each step with one of these."""

    CLASSIFY = "classify"
    EXTRACT = "extract"
    SUMMARIZE = "summarize"
    TRANSFORM = "transform"
    ARITHMETIC = "arithmetic"      # always tool-offloaded to code
    CODE_EDIT = "code_edit"
    REASONING = "reasoning"
    PLANNING = "planning"
    GENERAL = "general"


class Verdict(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    UNVERIFIED = "unverified"      # no scorer could produce ground truth


class MASTMode(str, Enum):
    """Failure vocabulary from the MAST taxonomy (Berkeley, 2025). Used to label
    failures consistently so the learning loop can cluster before fixing."""

    # system / specification design (~44% of MAS failures)
    DISOBEY_TASK_SPEC = "disobey_task_spec"
    DISOBEY_ROLE_SPEC = "disobey_role_spec"
    STEP_REPETITION = "step_repetition"
    LOSE_HISTORY = "lose_history"
    UNAWARE_TERMINATION = "unaware_termination"
    # inter-agent misalignment (~32%)
    IGNORE_INPUT = "ignore_input"
    WITHHELD_INFO = "withheld_info"
    MISMATCHED_ASSUMPTION = "mismatched_assumption"
    # task verification & termination
    PREMATURE_TERMINATION = "premature_termination"
    NO_VERIFICATION = "no_verification"
    INCORRECT_VERIFICATION = "incorrect_verification"
    # operational (harness-level)
    TOOL_ERROR = "tool_error"
    SCHEMA_INVALID = "schema_invalid"
    BUDGET_EXCEEDED = "budget_exceeded"
    UNKNOWN = "unknown"


class Task(BaseModel):
    """A unit of work handed to a worker harness."""

    id: str = Field(default_factory=lambda: _new_id("task"))
    task_type: TaskType = TaskType.GENERAL
    # explicit delegation contract (Anthropic multi-agent lesson): objective,
    # output format, and boundaries are load-bearing, not decoration.
    objective: str = ""
    inputs: dict[str, Any] = Field(default_factory=dict)
    output_schema: Optional[dict[str, Any]] = None
    boundaries: list[str] = Field(default_factory=list)
    # a checkable success signal, if one exists. Interpreted by the verifier.
    success_check: Optional[dict[str, Any]] = None
    tier_hint: Optional[Tier] = None
    max_attempts: int = 3
    # tool names this task may call (small per-step subset, never the catalog)
    tools: list[str] = Field(default_factory=list)


class WorkerResult(BaseModel):
    """What a worker harness returns for one attempt."""

    task_id: str
    worker_id: str
    tier: Tier
    model: str
    output: Any = None
    raw_text: str = ""
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    latency_s: float = 0.0
    error: Optional[str] = None
    # the directory this worker's file side-effects land in (builtin tool jail
    # root or coding-CLI cwd), recorded by the runner that KNOWS it — evidence
    # collection and run packaging must never infer this from cwd or guess
    workspace_root: str = ""
    # detached signature over `result_signing_bytes(...)`, made with the worker's
    # registered key, so the orchestrator can confirm who produced this result.
    signature_b64: Optional[str] = None


class VerificationResult(BaseModel):
    verdict: Verdict
    score: float = 0.0            # 0..1
    detail: str = ""
    failure_mode: Optional[MASTMode] = None
    scorer: str = ""             # which scorer produced this (execution/schema/judge)


class Attempt(BaseModel):
    """One pass through execute+verify. A task may take several before it passes
    or exhausts its budget."""

    n: int
    result: WorkerResult
    verification: VerificationResult
    reflection: Optional[str] = None   # written only after a failed attempt


class TaskOutcome(BaseModel):
    """The full record for a task: every attempt and the final disposition."""

    task: Task
    attempts: list[Attempt] = Field(default_factory=list)
    final_verdict: Verdict = Verdict.UNVERIFIED
    final_output: Any = None
    total_cost_usd: float = 0.0
    escalations: int = 0
    started_at: float = Field(default_factory=now)
    ended_at: Optional[float] = None
