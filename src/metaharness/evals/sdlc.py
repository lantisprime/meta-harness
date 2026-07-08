"""SDLC capability suite: can a model FOLLOW agentic-coding process?

Deterministic golden micro-tasks probing the per-phase capabilities the
agentic-SDLC research says models fail at (memory/knowledge_base/
agentic-sdlc-baseline.md): localization, spec discipline, plan adherence,
edit precision, evidence-over-assertion, gate compliance. Every task carries
its ground truth in the objective (the harness's honesty rule: checks are
derivable only when the goal contains the truth), so run_suite grades them
with zero judges. Use with pass^k (k>=3): single-run pass rates flatter.

Grouping convention: task ids are "sdlc-<phase>-<n>"; summarize_by_phase
rolls a SuiteResult up per phase for the go/no-go report.
"""
from __future__ import annotations

from metaharness.core.types import Task, TaskType
from metaharness.evals.gate import SuiteResult

_TREE = """\
src/parser.py    — parses input files (dates, numbers, encodings)
src/api.py       — HTTP endpoints, request validation
src/cli.py       — command-line entry point
src/report.py    — output formatting
tests/test_parser.py
"""


def sdlc_capability_suite() -> list[Task]:
    return [
        # -- explore / localize ------------------------------------------------
        Task(
            id="sdlc-localize-1", task_type=TaskType.CLASSIFY,
            objective=(
                "Codebase:\n" + _TREE +
                "\nBug report: 'dates like 2026-07-08 come back as None'. "
                "Which single file most likely contains the defect? "
                "Respond with exactly the file's basename, e.g. report.py."
            ),
            success_check={"equals": "parser.py"},
        ),
        Task(
            id="sdlc-localize-2", task_type=TaskType.CLASSIFY,
            objective=(
                "Codebase:\n" + _TREE +
                "\nBug report: 'running with --verbose crashes before any request "
                "is made'. Which single file most likely contains the defect? "
                "Respond with exactly the file's basename."
            ),
            success_check={"equals": "cli.py"},
        ),
        # -- specify: testability discipline ------------------------------------
        Task(
            id="sdlc-spec-1", task_type=TaskType.CLASSIFY,
            objective=(
                "Acceptance criteria:\n"
                "A) The response returns HTTP 201 and the row exists in the table.\n"
                "B) The interface feels intuitive.\n"
                "C) p95 latency stays under 200ms at 100 rps.\n"
                "How many of these are mechanically testable? "
                "Respond with exactly the number."
            ),
            success_check={"equals": "2"},
        ),
        Task(
            id="sdlc-spec-2", task_type=TaskType.CLASSIFY,
            objective=(
                "A requirement says 'support big uploads'. The upload size limit is "
                "not specified anywhere. Per spec-driven development, what must the "
                "spec author do? Respond with exactly one of: ask, guess."
            ),
            success_check={"equals": "ask"},
        ),
        # -- plan: adherence ------------------------------------------------------
        Task(
            id="sdlc-plan-1", task_type=TaskType.CLASSIFY,
            objective=(
                "Codebase:\n" + _TREE +
                "\nApproved spec: 'error messages must include the failing line "
                "number in the OUTPUT shown to users'. The plan may touch exactly "
                "one file. Respond with exactly that file's basename."
            ),
            success_check={"equals": "report.py"},
        ),
        # -- implement: edit precision -------------------------------------------
        Task(
            id="sdlc-edit-1", task_type=TaskType.TRANSFORM,
            objective=(
                "Apply this change: raise the retry limit from 3 to 5.\n"
                "Current line: MAX_RETRIES = 3\n"
                "Respond with exactly the corrected line and nothing else."
            ),
            success_check={"equals": "MAX_RETRIES = 5"},
        ),
        Task(
            id="sdlc-edit-2", task_type=TaskType.TRANSFORM,
            objective=(
                "Apply this rename: parse_all -> parse_documents.\n"
                "Current line: def parse_all(paths, strict=False):\n"
                "Respond with exactly the corrected line and nothing else."
            ),
            success_check={"equals": "def parse_documents(paths, strict=False):"},
        ),
        # -- verify: evidence over assertion ---------------------------------------
        Task(
            id="sdlc-verify-1", task_type=TaskType.ARITHMETIC,
            objective=(
                "The verification phase checks 3 acceptance criteria on each of 4 "
                "platforms. Compute the total number of checks."
            ),
            inputs={"expression": "3 * 4"},
            success_check={"equals": 12},
        ),
        Task(
            id="sdlc-verify-2", task_type=TaskType.CLASSIFY,
            objective=(
                "Verification evidence:\n"
                "criterion 1: met (test output attached)\n"
                "criterion 2: met (test output attached)\n"
                "criterion 3: 'should work' — no evidence provided\n"
                "Per evidence-over-assertion, are ALL criteria met? "
                "Respond with exactly one of: yes, no."
            ),
            success_check={"equals": "no"},
        ),
        # -- process compliance ------------------------------------------------------
        Task(
            id="sdlc-gate-1", task_type=TaskType.CLASSIFY,
            objective=(
                "Agentic SDLC gate order: may implementation start before the plan "
                "is approved? Respond with exactly one of: yes, no."
            ),
            success_check={"equals": "no"},
        ),
        Task(
            id="sdlc-gate-2", task_type=TaskType.CLASSIFY,
            objective=(
                "Your change makes test_login fail. Tests are locked: modifying "
                "them is forbidden. What do you change? "
                "Respond with exactly one of: code, test."
            ),
            success_check={"equals": "code"},
        ),
    ]


def phase_of(task_id: str) -> str:
    parts = task_id.split("-")
    return parts[1] if len(parts) >= 3 and parts[0] == "sdlc" else "other"


def summarize_by_phase(suite: SuiteResult) -> dict[str, dict[str, float]]:
    """pass^k and pass@1 per SDLC phase — the go/no-go view for 'can this
    model follow agentic coding process'."""
    grouped: dict[str, list] = {}
    for result in suite.results:
        grouped.setdefault(phase_of(result.task_id), []).append(result)
    return {
        phase: {
            "tasks": len(results),
            "pass_hat_k": sum(r.pass_all for r in results) / len(results),
            "pass_at_1": sum(r.pass_rate for r in results) / len(results),
        }
        for phase, results in sorted(grouped.items())
    }
