"""TaskExecutor: the execute→verify→reflect→escalate loop for one task.

This is the heart of the meta-harness. One call runs a task to completion:

    route (cheapest capable) → run → confirm authenticity → verify →
    on FAIL: reflect, retry timeout once in-tier, otherwise escalate → record everything

Every attempt updates the capability matrix (verified outcomes only), charges
the budget, and is journaled into the provenance chain under the orchestrator's
own key. Guardrails from core.budget stop runaway loops: hard budget ceilings,
plateau detection across attempts, and step-repetition dedup.
"""
from __future__ import annotations

import time
from typing import Awaitable, Callable, Optional

from metaharness.core.budget import Budget, BudgetExceeded, PlateauDetector, action_signature
from metaharness.core.types import (
    Attempt,
    MASTMode,
    Task,
    TaskOutcome,
    TaskType,
    Tier,
    VerificationResult,
    Verdict,
    WorkerResult,
    now,
)
from metaharness.evals.execution import verify_code_edit_execution
from metaharness.evals.verifiers import authenticity_failure, verify_output
from metaharness.harness.runner import verify_result
from metaharness.identity.keys import KeyPair
from metaharness.identity.provenance import ProvenanceLog
from metaharness.identity.registry import WorkerRegistry
from metaharness.observability.tracing import tracer
from metaharness.routing.router import TIER_ORDER, Router

# a reflector turns a failed attempt into advice for the next one
Reflector = Callable[[Task, Attempt], Optional[str]]
# a judge grades an UNVERIFIED output against its contract (rubric-judge slot
# of the verifier hierarchy); async because it calls a worker
Judge = Callable[..., "object"]
ExecutionVerifier = Callable[
    [Task, WorkerResult], Awaitable[Optional[VerificationResult]]
]


class TaskExecutor:
    def __init__(
        self,
        router: Router,
        registry: Optional[WorkerRegistry] = None,
        provenance: Optional[ProvenanceLog] = None,
        orchestrator_keypair: Optional[KeyPair] = None,
        budget: Optional[Budget] = None,
        reflector: Optional[Reflector] = None,
        playbook_hints: Optional[Callable[[Task], list[str]]] = None,
        observer: Optional[Callable[[TaskOutcome], None]] = None,
        judge: Optional[Judge] = None,
        execution_verifier: Optional[ExecutionVerifier] = verify_code_edit_execution,
    ) -> None:
        self.router = router
        self.registry = registry
        self.provenance = provenance
        self.orchestrator_keypair = orchestrator_keypair
        self.budget = budget
        self.reflector = reflector
        self.playbook_hints = playbook_hints
        self.observer = observer
        self.judge = judge
        self.execution_verifier = execution_verifier
        if provenance is not None and orchestrator_keypair is None:
            raise ValueError("provenance logging needs the orchestrator keypair")

    def _record(self, action: str, detail: dict) -> None:
        if self.provenance is not None:
            self.provenance.append(
                "orchestrator", action, detail, keypair=self.orchestrator_keypair
            )

    def _attempt_task(self, task: Task, advice: list[str]) -> Task:
        """The task as sent for this attempt — original contract plus accumulated
        reflections/playbook hints in the boundaries."""
        if not advice:
            return task
        variant = task.model_copy(deep=True)
        variant.boundaries = list(task.boundaries) + advice
        return variant

    async def execute(self, task: Task) -> TaskOutcome:
        outcome = TaskOutcome(task=task)
        plateau = PlateauDetector()
        seen_signatures: set[str] = set()
        excluded_tiers: set[Tier] = set()
        # A timeout is an operational signal, not evidence that the tier lacks
        # capability. Give each routed tier one retry before excluding it; the
        # set keeps repeated timeouts from turning max_attempts into an
        # unbounded same-tier loop (issue #11).
        timeout_retry_used: set[Tier] = set()
        forced_retry_tier: Optional[Tier] = None
        advice: list[str] = []
        if self.playbook_hints:
            advice.extend(self.playbook_hints(task))

        with tracer().start_as_current_span("task.execute") as span:
            span.set_attribute("task.id", task.id)
            span.set_attribute("task.type", task.task_type.value)
            self._record("task.started", {"task_id": task.id, "objective": task.objective[:200]})

            for n in range(1, task.max_attempts + 1):
                route_exclusions = excluded_tiers
                if forced_retry_tier is not None:
                    # One-shot exact-tier retry. Merely leaving the tier
                    # available is insufficient: after charging the timed-out
                    # attempt, Router's affordability filter could otherwise
                    # select a different (usually cheaper) tier.
                    route_exclusions = excluded_tiers | {
                        tier for tier in TIER_ORDER if tier != forced_retry_tier
                    }
                    forced_retry_tier = None
                try:
                    decision = self.router.decide(
                        task, exclude=route_exclusions, budget=self.budget
                    )
                except ValueError as exc:
                    self._record("task.aborted", {"task_id": task.id, "reason": str(exc)})
                    break

                variant = self._attempt_task(task, advice)
                runner = self.router.runner_for(decision)
                result = await runner.run(variant)
                result.task_id = task.id

                # charge always, fail truthfully (issue #5): the deterministic
                # verification (authenticity, then verify_output) is computed
                # BEFORE the charge so a genuine worker malfunction is already
                # known when the budget check below decides whether to mask it.
                # The rubric-judge slot stays AFTER the charge — an over-budget
                # attempt must stop before paying for an extra LLM judge call.
                signature_verified = (
                    self.registry is not None and verify_result(result, self.registry)
                )
                if self.registry is not None and not signature_verified:
                    verification = authenticity_failure(
                        f"result from {result.worker_id!r} has no valid signature "
                        "under its registered key"
                    )
                    worker_malfunctioned = True
                else:
                    verification = verify_output(task, result)
                    worker_malfunctioned = bool(result.error)
                # Issue #1: the workspace selector itself must be covered by a
                # verified v2 signature. Legacy v1 roots remain readable for
                # history but never select code for execution.
                workspace_attested = (
                    signature_verified and result.signature_version >= 2
                )
                if (
                    task.task_type == TaskType.CODE_EDIT
                    and result.workspace_root
                    and not workspace_attested
                ):
                    self._record("execution.skipped", {
                        "task_id": task.id,
                        "attempt": n,
                        "reason": "workspace_root is not covered by a verified v2 signature",
                    })

                try:
                    if self.budget is not None:
                        self.budget.charge(
                            cost_usd=result.cost_usd,
                            tokens=result.tokens_in + result.tokens_out,
                            wall_s=result.latency_s,
                        )
                except BudgetExceeded as exc:
                    # a genuine worker malfunction (result.error or an
                    # authenticity failure) always wins over budget-exhausted —
                    # keep the verification already computed above. A mere
                    # low-score verify-FAIL is NOT a masked failure; that case
                    # keeps today's budget-FAIL verification exactly.
                    if not worker_malfunctioned:
                        verification = VerificationResult(
                            verdict=Verdict.FAIL, score=0.0, detail=str(exc),
                            failure_mode=MASTMode.BUDGET_EXCEEDED, scorer="budget",
                        )
                    outcome.attempts.append(Attempt(n=n, result=result, verification=verification))
                    self._record("task.budget_exceeded", {"task_id": task.id, "detail": str(exc)})
                    break

                # Execution/tests are stronger evidence than output narration
                # or a text success_check, but stay AFTER the worker budget gate:
                # an already-over-budget attempt must not launch another process.
                if (
                    workspace_attested
                    and self.execution_verifier is not None
                    and not result.error
                    and verification.scorer != "schema"
                ):
                    execution_started = time.monotonic()
                    try:
                        execution = await self.execution_verifier(variant, result)
                    except Exception as exc:  # verifier infrastructure is not model failure
                        execution = None
                        self._record("execution.error", {
                            "task_id": task.id,
                            "attempt": n,
                            "error": f"{type(exc).__name__}: {exc}"[:300],
                        })
                    execution_latency_s = time.monotonic() - execution_started
                    if execution is not None:
                        execution.latency_s = execution_latency_s
                        verification = execution
                    try:
                        if self.budget is not None:
                            self.budget.charge(wall_s=execution_latency_s)
                    except BudgetExceeded as exc:
                        verification = VerificationResult(
                            verdict=Verdict.FAIL, score=0.0, detail=str(exc),
                            failure_mode=MASTMode.BUDGET_EXCEEDED, scorer="budget",
                        )
                        outcome.attempts.append(
                            Attempt(n=n, result=result, verification=verification)
                        )
                        self._record("task.budget_exceeded", {
                            "task_id": task.id, "detail": str(exc),
                        })
                        break

                if (verification.verdict == Verdict.UNVERIFIED
                        and self.judge is not None
                        and task.task_type != TaskType.PLANNING):
                    # rubric-judge slot: no deterministic check exists, so a
                    # fresh-context LLM grades the output against the step's
                    # own contract — a FAIL enters the normal retry loop
                    # BEFORE any dependent step consumes the output
                    try:
                        judge_result = result
                        if result.workspace_root and not (
                            signature_verified and result.signature_version >= 2
                        ):
                            # Evidence collection reads files. Apply the same
                            # attestation rule as execution so a legacy signed
                            # result cannot redirect the judge into arbitrary
                            # host paths.
                            judge_result = result.model_copy(
                                update={"workspace_root": ""}
                            )
                        verification = await self.judge(variant, judge_result)
                    except BudgetExceeded as exc:
                        # the judge call's own worker spend is charged against
                        # the same budget (make_judge) — a budget stop here
                        # must surface as a budget stop, never be laundered
                        # into a "broken judge" note.
                        verification = VerificationResult(
                            verdict=Verdict.FAIL, score=0.0, detail=str(exc),
                            failure_mode=MASTMode.BUDGET_EXCEEDED, scorer="budget",
                        )
                        outcome.attempts.append(Attempt(n=n, result=result, verification=verification))
                        self._record("task.budget_exceeded", {"task_id": task.id, "detail": str(exc)})
                        break
                    except Exception as exc:  # a broken judge must never fail the task,
                        # but a silent one is undiagnosable — record why it broke
                        self._record("judge.error", {
                            "task_id": task.id, "attempt": n,
                            "error": f"{type(exc).__name__}: {exc}"[:300],
                        })
                timeout_failure = (
                    verification.verdict == Verdict.FAIL
                    and verification.failure_mode == MASTMode.TIMEOUT
                )

                # capability matrix learns from checkable outcomes only
                # (deterministic or rubric-judged; scorer says which). An
                # authenticity failure says nothing about the model's SKILL and
                # must never be recorded (verifiers.authenticity_failure
                # invariant) — pre-issue-#5 this was enforced by nesting; the
                # reorder flattened it, so guard on the scorer (panel P1).
                # A timeout is likewise neutral: it says the attempt exhausted
                # its wall-clock allowance, not that the model could not solve
                # the task. A later PASS still earns normal positive evidence.
                if (verification.verdict in (Verdict.PASS, Verdict.FAIL)
                        and verification.scorer != "authenticity"
                        and not timeout_failure):
                    self.router.matrix.record(
                        result.model, task.task_type, verification.verdict == Verdict.PASS
                    )

                attempt = Attempt(n=n, result=result, verification=verification)
                outcome.attempts.append(attempt)
                outcome.total_cost_usd += result.cost_usd
                self._record(
                    "task.attempt",
                    {
                        "task_id": task.id, "n": n, "tier": decision.tier.value,
                        "model": result.model, "verdict": verification.verdict.value,
                        "detail": verification.detail[:200],
                        "worker_signature": result.signature_b64,
                        "worker_signature_version": result.signature_version,
                        # issue #2: parity with the step.attempt journal payload
                        "failure_mode": (verification.failure_mode.value
                                         if verification.failure_mode else None),
                        "latency_s": round(result.latency_s, 2),
                        "verification_latency_s": round(verification.latency_s, 2),
                        "timed_out": result.timed_out,
                    },
                )

                if verification.verdict == Verdict.PASS:
                    outcome.final_verdict = Verdict.PASS
                    outcome.final_output = result.output
                    break
                if verification.verdict == Verdict.UNVERIFIED:
                    # no signal to iterate against — iterating would be vibes
                    outcome.final_verdict = Verdict.UNVERIFIED
                    outcome.final_output = result.output
                    break

                # ---- failed, decide how to continue ----
                plateau.record(verification.score)
                sig = action_signature(result.output)
                repeated = sig in seen_signatures
                seen_signatures.add(sig)

                if self.reflector is not None:
                    reflection = self.reflector(task, attempt)
                    if reflection:
                        attempt.reflection = reflection
                        advice.append(reflection)
                if repeated:
                    # external-role framing (arXiv 2606.05976): describe the prior
                    # attempt as an addressable artifact, not the model's own thought
                    advice.append(
                        "A previous attempt produced this exact answer and it was "
                        "rejected. Take a materially different approach."
                    )

                # A timeout gets one same-tier retry before escalation. This
                # branch intentionally precedes ε-exploration handling: timeout
                # FAILs are not banked in the matrix, so the exploration rule's
                # "failure evidence" rationale does not apply. After the retry
                # is consumed, another timeout escalates for progress/cost
                # control even if that second route happened to be exploratory.
                if timeout_failure:
                    if decision.tier not in timeout_retry_used:
                        timeout_retry_used.add(decision.tier)
                        if n < task.max_attempts:
                            forced_retry_tier = decision.tier
                            self._record("task.timeout_retry", {
                                "task_id": task.id,
                                "after_attempt": n,
                                "tier": decision.tier.value,
                                "model": result.model,
                            })
                    elif n < task.max_attempts:
                        next_tier = self.router.next_tier(decision.tier)
                        if next_tier is not None:
                            excluded_tiers.add(decision.tier)
                            outcome.escalations += 1

                # an ε-explored failure is the benched member's, not the tier's:
                # the FAIL is already banked in the matrix, so retrying lets the
                # next decide() pick the best member on merit — escalating would
                # pay a higher tier for evidence we chose to buy cheap
                elif not decision.explored:
                    next_tier = self.router.next_tier(decision.tier)
                    if next_tier is not None:
                        excluded_tiers.add(decision.tier)
                        outcome.escalations += 1
                if plateau.plateaued():
                    self._record("task.plateaued", {"task_id": task.id, "after_attempts": n})
                    break

            if outcome.final_verdict != Verdict.PASS and outcome.attempts:
                # attempts exhausted (or stopped early): the last verified verdict
                # is the task's verdict — never leave a failed task UNVERIFIED
                outcome.final_verdict = outcome.attempts[-1].verification.verdict
                outcome.final_output = outcome.attempts[-1].result.output
            outcome.ended_at = now()
            span.set_attribute("task.verdict", outcome.final_verdict.value)
            span.set_attribute("task.attempts", len(outcome.attempts))
            span.set_attribute("task.escalations", outcome.escalations)
            span.set_attribute("task.cost_usd", outcome.total_cost_usd)
            self._record(
                "task.finished",
                {
                    "task_id": task.id,
                    "verdict": outcome.final_verdict.value,
                    "attempts": len(outcome.attempts),
                    "escalations": outcome.escalations,
                    "cost_usd": outcome.total_cost_usd,
                },
            )
            if self.observer is not None:
                try:
                    self.observer(outcome)  # learning loop: stats + bullet credit
                except Exception:  # observation must never fail the task itself
                    pass
            return outcome
