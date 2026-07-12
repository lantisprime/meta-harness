"""Exact eval execution behind a mandatory operating-system sandbox."""
from __future__ import annotations

import asyncio
import json
import math
import os
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, field_validator

from metaharness.blueprints import ArtifactRef, BlueprintCatalog, blueprint_digest
from metaharness.blueprints.models import BlueprintVersion, StrictModel, _validate_slug
from metaharness.core.executor import TaskExecutor
from metaharness.core.types import Task, Tier, WorkerResult
from metaharness.evals.artifacts import (
    EvalAttemptResult,
    EvalCaseResult,
    EvalMetrics,
    EvalSplit,
    EvaluationReport,
)
from metaharness.evals.execution import (
    MAX_CAPTURE_BYTES,
    _clean_environment,
    _sandbox_failed_to_start,
    _system_sandbox,
)
from metaharness.evals.models import (
    EvalAssertion,
    EvalCase,
    EvalSuiteVersion,
    _assert_secret_safe_json,
)
from metaharness.evals.store import EvalSuiteStore
from metaharness.evals.verifiers import verify_output
from metaharness.portable.integrity import canonical_json_bytes, sha256_hex
from metaharness.routing.router import Router
from metaharness.workflows.dsl import WorkflowSpec
from metaharness.workflows.engine import RunStatus, WorkflowEngine


class EvaluationError(RuntimeError):
    pass


class EvalReferenceMismatchError(EvaluationError):
    pass


class UnsafeEvalRunnerError(EvaluationError):
    pass


class EvalBudgetExceededError(EvaluationError):
    pass


class _SandboxStartError(UnsafeEvalRunnerError):
    """Internal retry signal; never permits an unsandboxed fallback."""


class IsolatedCaseExecution(StrictModel):
    """The only accepted stdout document from a sandboxed runner command."""

    output: Any = None
    raw_text: str = ""
    error: str | None = None
    tokens_in: int = Field(default=0, ge=0, strict=True)
    tokens_out: int = Field(default=0, ge=0, strict=True)
    cost_usd: float = Field(default=0.0, ge=0.0, strict=True)
    latency_s: float = Field(default=0.0, ge=0.0, strict=True)

    @field_validator("cost_usd", "latency_s")
    @classmethod
    def _finite_metrics(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("isolated execution metrics must be finite")
        return value


class SandboxedTaskRequest(StrictModel):
    """One engine-resolved stage task passed to the isolated worker command."""

    schema_version: Literal[1] = 1
    blueprint_ref: ArtifactRef
    task: Task
    case_id: str
    split: EvalSplit
    repetition: int = Field(ge=1, strict=True)


class SandboxedCaseRunner(StrictModel):
    """Data-only command descriptor; no in-process callback is ever invoked."""

    runner_id: str
    argv: tuple[str, ...]
    timeout_s: float = Field(default=30.0, gt=0.0, le=300.0, strict=True)
    sealed_holdout_access: bool = False

    @field_validator("runner_id")
    @classmethod
    def _runner_id(cls, value: str) -> str:
        return _validate_slug(value, label="runner id")

    @field_validator("argv")
    @classmethod
    def _argv(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value or any(not item or "\x00" in item for item in value):
            raise ValueError("sandboxed runner argv must contain nonempty safe strings")
        _assert_secret_safe_json(value, location="sandboxed runner argv")
        return value

    @field_validator("timeout_s")
    @classmethod
    def _finite_timeout(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("runner timeout must be finite")
        return value


def _assertion_kind(assertion: EvalAssertion) -> str:
    if assertion.success_check is not None:
        return "success_check"
    if assertion.output_schema is not None:
        return "output_schema"
    return "rubric"


def _assertion_digest(assertion: EvalAssertion) -> str:
    return sha256_hex(canonical_json_bytes(assertion.model_dump(mode="json")))


def eval_suite_digest(suite: EvalSuiteVersion) -> str:
    return sha256_hex(canonical_json_bytes(suite.model_dump(mode="json")))


def workflow_digest(blueprint: BlueprintVersion) -> str:
    return sha256_hex(
        canonical_json_bytes(blueprint.workflow.model_dump(mode="json"))
    )


def _task_for_case(case: EvalCase) -> Task:
    assertion = case.assertion
    return Task(
        id=f"eval-{case.id}",
        objective=case.name,
        success_check=assertion.success_check,
        output_schema=assertion.output_schema,
    )


def _cases_for_split(suite: EvalSuiteVersion, split: EvalSplit) -> list[EvalCase]:
    return {
        "development": suite.development_cases,
        "validation": suite.validation_cases,
        "holdout": suite.holdout_cases,
    }[split]


def _effective_workflow(
    blueprint: BlueprintVersion, suite: EvalSuiteVersion
) -> WorkflowSpec:
    """Clone the workflow and substitute every production tool with eval binding."""
    mapping: dict[str, str] = {}
    for binding in suite.policy.tool_bindings:
        if binding.tool in mapping:
            raise UnsafeEvalRunnerError(
                f"duplicate isolated eval binding for tool {binding.tool!r}"
            )
        mapping[binding.tool] = binding.binding
    workflow = blueprint.workflow.model_copy(deep=True)
    for step in workflow.steps:
        missing = sorted(set(step.tools) - set(mapping))
        if missing:
            raise UnsafeEvalRunnerError(
                f"workflow tools lack declared isolated eval bindings: {missing}"
            )
        step.tools = [mapping[tool] for tool in step.tools]
        # Production assignment is not an authorization to use production
        # workers during evaluation. The isolated runner is the explicit eval
        # binding; DAG, contracts, dependencies, branches, and tier floors stay
        # under WorkflowEngine control.
        step.worker_id = None
        step.role = None
        step.required_capabilities = []
    return WorkflowSpec.model_validate(workflow.model_dump(mode="python"))


def _strict_json(raw: bytes) -> Any:
    def reject_constant(value: str) -> None:
        raise ValueError(f"non-finite JSON constant {value}")

    return json.loads(raw.decode("utf-8"), parse_constant=reject_constant)


def _kill_process_group(proc: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        if proc.poll() is None:
            proc.kill()


def _run_sandboxed_once(
    runner: SandboxedCaseRunner, request: StrictModel
) -> IsolatedCaseExecution:
    """Execute argv only through Seatbelt/bubblewrap; unavailable means failure."""
    if type(runner) is not SandboxedCaseRunner:
        raise UnsafeEvalRunnerError("runner must be an exact data-only sandbox descriptor")
    with tempfile.TemporaryDirectory(prefix="metaharness-eval-") as temporary:
        root = Path(temporary).resolve()
        workspace = root / "workspace"
        scratch = root / "scratch"
        workspace.mkdir()
        scratch.mkdir()
        wrapped = _system_sandbox(runner.argv, workspace, scratch)
        if wrapped is None:
            raise UnsafeEvalRunnerError(
                "no supported operating-system eval sandbox is available"
            )
        argv, backend = wrapped
        try:
            proc = subprocess.Popen(
                argv,
                cwd=workspace,
                env=_clean_environment(scratch, runner.argv),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
        except OSError as exc:
            raise UnsafeEvalRunnerError("sandboxed eval runner failed to start") from exc
        try:
            stdout, stderr = proc.communicate(
                canonical_json_bytes(request.model_dump(mode="json")),
                timeout=runner.timeout_s,
            )
        except subprocess.TimeoutExpired as exc:
            _kill_process_group(proc)
            proc.communicate()
            raise UnsafeEvalRunnerError("sandboxed eval runner timed out") from exc
        finally:
            _kill_process_group(proc)
        if len(stdout) > MAX_CAPTURE_BYTES or len(stderr) > MAX_CAPTURE_BYTES:
            raise UnsafeEvalRunnerError("sandboxed eval runner output exceeded limit")
        diagnostic = stderr.decode("utf-8", errors="replace")
        if _sandbox_failed_to_start(backend, diagnostic):
            raise _SandboxStartError("operating-system eval sandbox failed to start")
        if proc.returncode != 0:
            raise UnsafeEvalRunnerError(
                f"sandboxed eval runner exited with status {proc.returncode}"
            )
        try:
            return IsolatedCaseExecution.model_validate(_strict_json(stdout))
        except (UnicodeError, ValueError) as exc:
            raise UnsafeEvalRunnerError(
                "sandboxed eval runner returned an invalid result document"
            ) from exc


def _run_sandboxed(
    runner: SandboxedCaseRunner, request: StrictModel
) -> IsolatedCaseExecution:
    """Retry one transient sandbox bootstrap failure, never without isolation."""
    for attempt in range(2):
        try:
            return _run_sandboxed_once(runner, request)
        except _SandboxStartError:
            if attempt:
                raise
            time.sleep(0.05)
    raise AssertionError("unreachable")


class _SandboxedTaskRunner:
    """Runner adapter: the sandbox command executes one stage, never a DAG."""

    def __init__(
        self,
        descriptor: SandboxedCaseRunner,
        *,
        blueprint_ref: ArtifactRef,
        case_id: str,
        split: EvalSplit,
        repetition: int,
        tier: Tier,
    ) -> None:
        self.worker_id = descriptor.runner_id
        self.model = descriptor.runner_id
        self.tier = tier
        self.descriptor = descriptor
        self.blueprint_ref = blueprint_ref
        self.case_id = case_id
        self.split = split
        self.repetition = repetition
        self.executions: list[IsolatedCaseExecution] = []

    async def run(self, task: Task) -> WorkerResult:
        execution = await asyncio.to_thread(
            _run_sandboxed,
            self.descriptor,
            SandboxedTaskRequest(
                blueprint_ref=self.blueprint_ref,
                task=task,
                case_id=self.case_id,
                split=self.split,
                repetition=self.repetition,
            ),
        )
        self.executions.append(execution)
        return WorkerResult(
            task_id=task.id,
            worker_id=self.worker_id,
            tier=self.tier,
            model=self.model,
            output=execution.output,
            raw_text=execution.raw_text,
            tokens_in=execution.tokens_in,
            tokens_out=execution.tokens_out,
            cost_usd=execution.cost_usd,
            latency_s=execution.latency_s,
            error=execution.error,
        )


async def _execute_workflow_case(
    *,
    runner: SandboxedCaseRunner,
    blueprint: BlueprintVersion,
    workflow: WorkflowSpec,
    case: EvalCase,
    split: EvalSplit,
    repetition: int,
    approve_isolated_hitl: bool,
) -> IsolatedCaseExecution:
    """Execute exactly one case through the production orchestration spine."""
    if type(runner) is not SandboxedCaseRunner:
        raise UnsafeEvalRunnerError("runner must be an exact data-only sandbox descriptor")
    if case.output_step is None:
        raise EvaluationError(f"eval case {case.id!r} must declare output_step")
    try:
        workflow.step(case.output_step)
    except KeyError as exc:
        raise EvaluationError(
            f"eval case {case.id!r} output_step {case.output_step!r} is not in workflow"
        ) from exc

    task_runners = {
        tier: _SandboxedTaskRunner(
            runner,
            blueprint_ref=blueprint.ref,
            case_id=case.id,
            split=split,
            repetition=repetition,
            tier=tier,
        )
        for tier in Tier
    }
    executor = TaskExecutor(
        Router(task_runners, explore_rate=0.0),
        execution_verifier=None,
        workspace_verifier=None,
    )
    engine = WorkflowEngine(executor)
    state = engine.start(
        workflow,
        context=dict(case.context),
        blueprint_ref=blueprint.ref.model_dump(mode="json"),
        blueprint_snapshot=blueprint.model_dump(mode="json"),
    )
    while state.status in {RunStatus.RUNNING, RunStatus.AWAITING_APPROVAL}:
        state = await engine.advance(state.run_id)
        if state.status != RunStatus.AWAITING_APPROVAL:
            continue
        if not approve_isolated_hitl or state.awaiting is None:
            raise UnsafeEvalRunnerError("eval workflow paused for disallowed HITL approval")
        engine.approve(state.run_id, state.awaiting)

    executions = [
        execution
        for task_runner in task_runners.values()
        for execution in task_runner.executions
    ]
    metrics = {
        "tokens_in": sum(item.tokens_in for item in executions),
        "tokens_out": sum(item.tokens_out for item in executions),
        "cost_usd": sum(item.cost_usd for item in executions),
        "latency_s": sum(item.latency_s for item in executions),
    }
    if state.status != RunStatus.COMPLETED:
        return IsolatedCaseExecution(
            error=f"workflow failed before output step {case.output_step!r}", **metrics
        )
    record = state.completed.get(case.output_step)
    if record is None:
        return IsolatedCaseExecution(
            error=f"workflow did not complete output step {case.output_step!r}", **metrics
        )
    return IsolatedCaseExecution(
        output=record.output,
        raw_text=record.output if isinstance(record.output, str) else "",
        **metrics,
    )


def _run_workflow_case(**kwargs: Any) -> IsolatedCaseExecution:
    """Synchronous evaluator facade around the async WorkflowEngine."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_execute_workflow_case(**kwargs))
    raise EvaluationError(
        "synchronous ExactSuiteEvaluator cannot run inside an active event loop"
    )


class ExactSuiteEvaluator:
    """Resolve exact inputs and evaluate through WorkflowEngine plus sandbox workers."""

    def __init__(
        self,
        blueprint_catalog: BlueprintCatalog,
        eval_store: EvalSuiteStore,
        runner: SandboxedCaseRunner,
        sealed_runner: SandboxedCaseRunner | None = None,
    ) -> None:
        self.blueprint_catalog = blueprint_catalog
        self.eval_store = eval_store
        self.runner = runner
        self.sealed_runner = sealed_runner

    @staticmethod
    def _validate_refs(
        blueprint: BlueprintVersion,
        suite: EvalSuiteVersion,
        blueprint_ref: ArtifactRef,
        eval_ref: ArtifactRef,
    ) -> None:
        if blueprint.ref != blueprint_ref or suite.ref != eval_ref:
            raise EvalReferenceMismatchError("resolved artifact identity mismatches exact ref")
        if eval_ref not in blueprint.eval_suites:
            raise EvalReferenceMismatchError(
                "eval suite exact ref is not frozen on the blueprint version"
            )

    @staticmethod
    def _enforce_budget(metrics: EvalMetrics, suite: EvalSuiteVersion) -> None:
        policy = suite.policy
        if policy.max_tokens is not None and (
            metrics.tokens_in + metrics.tokens_out > policy.max_tokens
        ):
            raise EvalBudgetExceededError("evaluation exceeded max_tokens")
        if policy.max_cost_usd is not None and metrics.cost_usd > policy.max_cost_usd:
            raise EvalBudgetExceededError("evaluation exceeded max_cost_usd")
        if policy.max_wall_s is not None and metrics.latency_s > policy.max_wall_s:
            raise EvalBudgetExceededError("evaluation exceeded max_wall_s")

    def evaluate(
        self,
        *,
        report_id: str,
        blueprint_ref: ArtifactRef,
        eval_ref: ArtifactRef,
        split: Literal["development", "validation"],
        created_at: float | None = None,
    ) -> EvaluationReport:
        if split not in {"development", "validation"}:
            raise EvaluationError("public evaluation cannot access sealed holdout cases")
        return self._evaluate(
            report_id=report_id,
            blueprint_ref=blueprint_ref,
            eval_ref=eval_ref,
            split=split,
            created_at=created_at,
            runner=self.runner,
        )

    def evaluate_sealed_holdout(
        self,
        *,
        report_id: str,
        blueprint_ref: ArtifactRef,
        eval_ref: ArtifactRef,
        created_at: float | None = None,
    ) -> EvaluationReport:
        if (
            self.sealed_runner is None
            or self.sealed_runner.sealed_holdout_access is not True
        ):
            raise UnsafeEvalRunnerError(
                "sealed holdout evaluation requires a separately wired trusted runner"
            )
        return self._evaluate(
            report_id=report_id,
            blueprint_ref=blueprint_ref,
            eval_ref=eval_ref,
            split="holdout",
            created_at=created_at,
            runner=self.sealed_runner,
        )

    def _evaluate(
        self,
        *,
        report_id: str,
        blueprint_ref: ArtifactRef,
        eval_ref: ArtifactRef,
        split: EvalSplit,
        created_at: float | None,
        runner: SandboxedCaseRunner,
    ) -> EvaluationReport:
        blueprint = self.blueprint_catalog.get_version(blueprint_ref)
        suite = self.eval_store.get_version_for_evaluation(eval_ref.id, eval_ref.version)
        self._validate_refs(blueprint, suite, blueprint_ref, eval_ref)
        if (
            any(step.hitl for step in blueprint.workflow.steps)
            and suite.policy.hitl_mode == "block"
        ):
            raise UnsafeEvalRunnerError("HITL workflow steps are blocked by this eval policy")
        effective_workflow = _effective_workflow(blueprint, suite)
        selected_cases = _cases_for_split(suite, split)
        if not selected_cases:
            raise EvaluationError(f"eval split {split!r} contains no cases")

        case_results: list[EvalCaseResult] = []
        total = EvalMetrics()
        for case in selected_cases:
            attempts: list[EvalAttemptResult] = []
            for repetition in range(1, suite.policy.k + 1):
                execution = _run_workflow_case(
                    runner=runner,
                    blueprint=blueprint,
                    workflow=effective_workflow,
                    case=case,
                    split=split,
                    repetition=repetition,
                    approve_isolated_hitl=(
                        suite.policy.hitl_mode == "approve_isolated_only"
                    ),
                )
                worker_result = WorkerResult(
                    task_id=f"eval-{case.id}",
                    worker_id=runner.runner_id,
                    tier=Tier.SMALL,
                    model=runner.runner_id,
                    output=execution.output,
                    raw_text=execution.raw_text,
                    tokens_in=execution.tokens_in,
                    tokens_out=execution.tokens_out,
                    cost_usd=execution.cost_usd,
                    latency_s=execution.latency_s,
                    error=execution.error,
                )
                verification = verify_output(_task_for_case(case), worker_result)
                metrics = EvalMetrics(
                    tokens_in=execution.tokens_in,
                    tokens_out=execution.tokens_out,
                    cost_usd=execution.cost_usd,
                    latency_s=execution.latency_s,
                )
                total = total.plus(metrics)
                self._enforce_budget(total, suite)
                sealed = split == "holdout"
                attempts.append(
                    EvalAttemptResult(
                        repetition=repetition,
                        verdict=verification.verdict.value,
                        scorer=verification.scorer,
                        detail="" if sealed else verification.detail,
                        output=None if sealed else execution.output,
                        metrics=metrics,
                    )
                )
            verdicts = [attempt.verdict for attempt in attempts]
            final = (
                "fail"
                if "fail" in verdicts
                else "unverified"
                if "unverified" in verdicts
                else "pass"
            )
            case_results.append(
                EvalCaseResult(
                    case_id=case.id,
                    split=split,
                    assertion_kind=_assertion_kind(case.assertion),
                    assertion_digest=(
                        None if sealed else _assertion_digest(case.assertion)
                    ),
                    assertion=None if sealed else case.assertion,
                    attempts=attempts,
                    verdict=final,
                )
            )

        report_data = {
            "schema_version": 1,
            "id": report_id,
            "blueprint_ref": blueprint_ref,
            "eval_ref": eval_ref,
            "split": split,
            "blueprint_digest": blueprint_digest(blueprint),
            "workflow_digest": workflow_digest(blueprint),
            "eval_digest": eval_suite_digest(suite),
            "runner_id": runner.runner_id,
            "cases": case_results,
            "metrics": total,
            "passed": sum(case.verdict == "pass" for case in case_results),
            "failed": sum(case.verdict == "fail" for case in case_results),
            "unverified": sum(case.verdict == "unverified" for case in case_results),
            "created_at": time.time() if created_at is None else created_at,
        }
        digest_payload = {
            key: value
            for key, value in report_data.items()
            if key not in {"id", "created_at"}
        }
        digest = sha256_hex(
            canonical_json_bytes(
                {
                    key: value.model_dump(mode="json")
                    if hasattr(value, "model_dump")
                    else [item.model_dump(mode="json") for item in value]
                    if isinstance(value, list)
                    and value
                    and hasattr(value[0], "model_dump")
                    else value
                    for key, value in digest_payload.items()
                }
            )
        )
        return EvaluationReport(**report_data, content_digest=digest)
