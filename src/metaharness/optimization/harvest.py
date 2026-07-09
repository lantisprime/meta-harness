"""Harvest real run journals into optimization-suite extras.

Tuning suites ship with hard-coded seed tasks (suites.py). Production runs,
though, exercise task *shapes* the seeds never cover — the actual objectives,
inputs, and success checks a workflow put in front of a worker. This module
turns `~/.metaharness/journals/run_*.jsonl` into `<suite>/extra_tasks.json`
entries so a tuning run scores against work that really happened.

Ground truth is the step's deterministic `success_check`, never the run's
outcome: a task a real run *failed* is discriminative suite material, so failed
steps are harvested too (the journaled verdict rides along as provenance only).
We reuse the coverage endpoint's discipline (app.py:330-406): only builtin task
types are allowed, arithmetic answers are recomputed from scratch and never
trusted, and dedupe is content-based against builtin + existing extras.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field, ValidationError

from metaharness.core.types import Task, TaskType
from metaharness.harness.sandbox import eval_arithmetic
from metaharness.optimization.suites import (
    extras_path,
    load_extras,
    save_extras,
    search_and_holdout,
)
from metaharness.workflows.dsl import StepSpec, WorkflowSpec, resolve_reference
from metaharness.workflows.journal import Journal, JournalEntry

# Closed vocabulary of skip reasons — kept stable so the report is a contract
# for downstream consumers (CLI JSON, and any future web/advisor placement).
SKIP_REASONS = (
    "no_success_check",
    "bad_check_vocab",
    "bad_check_value",
    "type_not_allowed",
    "unresolvable_inputs",
    "arithmetic_unevaluable",
    "oversized",
    "duplicate",
)

_PRIMARY_CHECK_KEYS = ("equals", "contains", "one_of")


class HarvestReport(BaseModel):
    """What a harvest pass did — every skipped candidate is accounted for by a
    reason from `SKIP_REASONS`, and every task that was (or would be, in
    dry-run) added carries its provenance in `candidates`."""

    files_scanned: int = 0
    files_unreadable: int = 0
    steps_executed: int = 0
    added: int = 0
    skipped: dict[str, int] = Field(default_factory=lambda: {r: 0 for r in SKIP_REASONS})
    # provenance lives here, NOT on the Task (schema unchanged): objective is
    # truncated so a report over a corpus with 20KB artifact inputs stays small.
    candidates: list[dict[str, Any]] = Field(default_factory=list)


def _check_vocab_ok(check: dict[str, Any]) -> bool:
    """The check keys match a deterministic verifier shape (verifiers.py:70-101):
    exactly one of equals/contains/one_of, `tol` only alongside `equals`, nothing
    else. A judge/schema-only check yields no PASS-able ground truth, so we drop
    it rather than launder an unverifiable task into the suite."""
    primary = [k for k in _PRIMARY_CHECK_KEYS if k in check]
    if len(primary) != 1:
        return False
    extra = set(check) - {*_PRIMARY_CHECK_KEYS, "tol"}
    if extra:
        return False
    if "tol" in check and primary[0] != "equals":
        return False
    return True


def _check_value_ok(check: dict[str, Any]) -> bool:
    """The key shape is right; would the VALUES crash or degrade a consumer?
    verify_output does an uncaught `float(check["tol"])` (verifiers.py:72) and
    indexes `one_of`/`contains` directly — a harvested check must not smuggle in
    a value that turns a later tuning run into a crash."""
    if "tol" in check:
        try:
            tol = float(check["tol"])
        except (TypeError, ValueError):
            return False
        # panel F1: math.isclose raises on a negative tol (crashing tuning), and
        # an inf tol makes ANY numeric output PASS (silent ground-truth
        # corruption). `tol >= 0` also rejects NaN, since NaN >= 0 is False.
        if not math.isfinite(tol) or tol < 0:
            return False
    if "one_of" in check:
        allowed = check["one_of"]
        if not isinstance(allowed, list) or not allowed:
            return False
        if not all(isinstance(v, (str, int, float)) for v in allowed):
            return False
    if "contains" in check:
        if not isinstance(check["contains"], str) or not check["contains"]:
            return False
    return True


def _dedupe_key(objective: str, inputs: dict[str, Any]) -> tuple[str, str]:
    """Content-based identity — task_ids/run_ids are single-use, so dedupe must
    be on (objective, inputs), mirroring the coverage endpoint (app.py:381)."""
    return (objective, json.dumps(inputs, sort_keys=True, default=str))


def harvest_journals(
    journal_dir: Path | str,
    suite: str,
    root: Path | str,
    *,
    dry_run: bool = False,
    max_task_chars: int = 16000,
) -> HarvestReport:
    """Extract PASS-able suite tasks from every `run_*.jsonl` under `journal_dir`
    into `<root>/<suite>/extra_tasks.json`. Deterministic (filename-sorted scan,
    spec-order steps) and idempotent (a re-run adds 0 and leaves the file
    byte-identical). Unless `dry_run`, writes only when something was added."""
    journal_dir = Path(journal_dir)
    suite_dir = Path(root) / suite

    # builtin search+holdout fixes both the allowed task types and half the
    # dedupe seed set; raises ValueError on an unknown suite (caller surfaces it).
    builtin_search, builtin_holdout = search_and_holdout(suite)
    allowed_types = {t.task_type for t in builtin_search}

    existing_extras = load_extras(suite_dir)
    seen: set[tuple[str, str]] = {
        _dedupe_key(t.objective, t.inputs)
        for t in (*builtin_search, *builtin_holdout, *existing_extras)
    }

    report = HarvestReport()
    new_tasks: list[Task] = []

    for path in sorted(journal_dir.glob("run_*.jsonl")):
        report.files_scanned += 1
        parsed = _load_run(path)
        if parsed is None:
            report.files_unreadable += 1
            continue
        spec, context, events = parsed

        # validated JournalEntry objects, so payload/step_id fall back to their
        # pydantic defaults (panel F4: a payload-less line is valid per
        # journal.py:25-26 and must degrade to "not executed", not a KeyError
        # that aborts every remaining file).
        # outputs feed $steps references; only completed steps produce them (a
        # failed step halts the run, so nothing downstream ever consumed it).
        outputs = {
            e.step_id: e.payload["output"]
            for e in events
            if e.kind == "step.completed" and e.step_id and "output" in e.payload
        }
        # a step "executed" iff its terminal event carries a verdict — that
        # excludes reason-only orchestration failures (engine.py:171-172).
        verdict_by_step = {
            e.step_id: e.payload["verdict"]
            for e in events
            if e.kind in ("step.completed", "step.failed")
            and e.step_id and "verdict" in e.payload
        }

        for step in spec.steps:  # spec order ⇒ deterministic candidate order
            if step.id not in verdict_by_step:
                continue
            report.steps_executed += 1
            reason, task = _evaluate_step(
                step, context, outputs, allowed_types, seen, max_task_chars
            )
            if reason is not None:
                report.skipped[reason] += 1
                continue
            assert task is not None
            seen.add(_dedupe_key(task.objective, task.inputs))
            new_tasks.append(task)
            report.candidates.append({
                "objective": task.objective[:80],
                "task_type": task.task_type.value,
                "run_id": path.stem,
                "step_id": step.id,
                "verdict": verdict_by_step[step.id],
            })

    report.added = len(new_tasks)
    if not dry_run and new_tasks:
        # panel F3 (mitigation): the coverage endpoint (app.py:405) can write
        # extras while we scan journals — re-read fresh and re-dedupe right
        # before saving so its additions survive, shrinking the lost-update
        # window from the whole scan to this read-write gap. (Cross-writer
        # locking is a deferred follow-up, not this module's to solve.)
        fresh_extras = load_extras(suite_dir)
        fresh_keys = {_dedupe_key(t.objective, t.inputs) for t in fresh_extras}
        surviving = [t for t in new_tasks
                     if _dedupe_key(t.objective, t.inputs) not in fresh_keys]
        report.added = len(surviving)
        save_extras(suite_dir, [*fresh_extras, *surviving])
    return report


def _load_run(path: Path) -> Optional[tuple[WorkflowSpec, dict[str, Any], list[JournalEntry]]]:
    """Parse a journal into (spec, context, entries), or None if the file is
    unreadable — same tolerance stance as `adopt_all` (engine.py:286-295): a
    corrupt journal is skipped, never fatal."""
    try:
        journal = Journal.load(path)
        started = journal.entries("run.started")
        if not started:
            return None
        payload = started[0].payload
        spec = WorkflowSpec.model_validate(payload["workflow"])
        context = payload.get("context", {})
        return spec, context, journal.entries()
    except (ValueError, KeyError, TypeError, OSError, ValidationError):
        return None


def _evaluate_step(
    step: StepSpec,
    context: dict[str, Any],
    outputs: dict[str, Any],
    allowed_types: set[TaskType],
    seen: set[tuple[str, str]],
    max_task_chars: int,
) -> tuple[Optional[str], Optional[Task]]:
    """Run one executed step through the skip gates. Returns (reason, None) on
    the first failing gate, or (None, task) on a harvestable task. Gate order is
    the spec's: cheap structural checks before resolution/recompute/dedupe."""
    check = step.success_check
    if not check:
        return "no_success_check", None
    if not _check_vocab_ok(check):
        return "bad_check_vocab", None
    if not _check_value_ok(check):
        return "bad_check_value", None
    if step.task_type not in allowed_types:
        return "type_not_allowed", None

    try:
        resolved = resolve_reference(step.inputs, context, outputs)
    except ValueError:
        return "unresolvable_inputs", None

    task = step.to_task(resolved)

    # arithmetic answers are recomputed exactly and REPLACE the journaled value —
    # never trust a model's math (app.py:392-397). Unevaluable ⇒ drop the task.
    if task.task_type == TaskType.ARITHMETIC:
        # panel F5: only an `equals` primary can hold a recomputed answer; a
        # one_of/contains arithmetic check would gain a second primary key
        # (a shape this module itself rejects) and silently flip membership
        # semantics to exact-equality. We never trust journaled arithmetic
        # ground truth we cannot recompute ⇒ skip conservatively.
        if "equals" not in task.success_check:
            return "arithmetic_unevaluable", None
        expr = task.inputs.get("expression")
        try:
            recomputed = eval_arithmetic(str(expr))
        except Exception:  # noqa: BLE001 — panel F2: truediv can raise
            # ZeroDivisionError (sandbox.py:49) and pow can overflow; anything
            # the evaluator throws means "no trustworthy answer", not "abort
            # the whole harvest".
            return "arithmetic_unevaluable", None
        task.success_check = {**task.success_check, "equals": recomputed}

    if len(json.dumps(task.model_dump(), default=str)) > max_task_chars:
        return "oversized", None
    if _dedupe_key(task.objective, task.inputs) in seen:
        return "duplicate", None
    return None, task
