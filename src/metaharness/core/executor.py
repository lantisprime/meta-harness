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

import asyncio
import time
from typing import Any, Awaitable, Callable, Optional

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
from metaharness.evals.execution import (
    run_workspace_execution,
    verify_code_edit_execution,
)
from metaharness.evals.verifiers import authenticity_failure, verify_output
from metaharness.harness.runner import verify_result
from metaharness.identity.keys import KeyPair
from metaharness.identity.provenance import ProvenanceLog
from metaharness.identity.registry import WorkerRegistry
from metaharness.identity.tokens import TokenIssuer
from metaharness.observability.tracing import tracer
from metaharness.observability.run_events import (
    bind_run_event_sink,
    reset_run_event_sink,
)
from metaharness.routing.router import TIER_ORDER, Router

# a reflector turns a failed attempt into advice for the next one
Reflector = Callable[[Task, Attempt], Optional[str]]
# a judge grades an UNVERIFIED output against its contract (rubric-judge slot
# of the verifier hierarchy); async because it calls a worker
Judge = Callable[..., "object"]
ExecutionVerifier = Callable[
    [Task, WorkerResult], Awaitable[Optional[VerificationResult]]
]
WorkspaceVerifier = Callable[[str], Awaitable[Optional[VerificationResult]]]
TaskEventSink = Callable[[str, dict[str, Any]], None]


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
        workspace_root: str = "",
        workspace_verifier: Optional[WorkspaceVerifier] = run_workspace_execution,
        token_issuer: Optional[TokenIssuer] = None,
        token_ttl_s: float = 600.0,
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
        self.workspace_root = workspace_root
        self.workspace_verifier = workspace_verifier
        # META-18: every dispatch goes through a pre-dispatch authorization
        # gate. A private default issuer keeps the contract universal even
        # when no harness state is wired (unit tests, library callers). The
        # authorization payload only enters the `attempt.assigned` event when
        # the issuer was EXPLICITLY supplied (canonical harness wiring); the
        # legacy payload is preserved for the private-default path so existing
        # canonical event-equality tests outside owned scope remain green.
        self.token_issuer = token_issuer or TokenIssuer()
        self.token_issuer_explicit = token_issuer is not None
        self.token_ttl_s = token_ttl_s
        if provenance is not None and orchestrator_keypair is None:
            raise ValueError("provenance logging needs the orchestrator keypair")

    def _record(self, action: str, detail: dict) -> None:
        if self.provenance is not None:
            self.provenance.append(
                "orchestrator", action, detail, keypair=self.orchestrator_keypair
            )

    def _attempt_task(self, task: Task, advice: list[str]) -> Task:
        """The task as sent for this attempt — original contract plus accumulated
        reflections/playbook hints in `advice`.

        META-19 (F2): advice lands in `task.advice`, NOT `task.boundaries`.
        Reflexion/selflearn text quotes prior worker output and retrieved
        content verbatim; folding it into boundaries laundered untrusted data
        into a caller-authored instruction contract. The worker renders advice
        as untrusted-derived feedback, never as an instruction slot.
        """
        if not advice:
            return task
        variant = task.model_copy(deep=True)
        variant.advice = list(task.advice) + advice
        return variant

    async def execute(
        self, task: Task, *, event_sink: Optional[TaskEventSink] = None
    ) -> TaskOutcome:
        """Execute one task, optionally emitting live attempt/verification events.

        The callback is synchronous by design: the workflow journal fsyncs each
        event before execution moves past the corresponding transition.
        """
        def emit(kind: str, payload: dict[str, Any]) -> None:
            if event_sink is not None:
                event_sink(kind, payload)

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
        execution_evidence_latency_s = 0.0
        if task.requires_execution_evidence:
            evidence_started = time.monotonic()
            evidence = None
            if self.workspace_root and self.workspace_verifier is not None:
                try:
                    evidence = await self.workspace_verifier(self.workspace_root)
                except Exception as exc:  # infrastructure uncertainty is evidence too
                    self._record("execution.evidence_error", {
                        "task_id": task.id,
                        "error": f"{type(exc).__name__}: {exc}"[:300],
                    })
            execution_evidence_latency_s = time.monotonic() - evidence_started
            variant = task.model_copy(deep=True)
            if evidence is None:
                receipt = (
                    "Harness-owned execution receipt: no approved runnable test "
                    "command was discovered or no OS sandbox was available. Do not "
                    "claim command-based acceptance criteria are met."
                )
            else:
                receipt = (
                    "Harness-owned execution receipt (not worker-authored):\n"
                    f"{evidence.detail}"
                )
            variant.inputs["harness_execution_evidence"] = receipt
            task_for_attempts = variant
            self._record("execution.evidence_attached", {
                "task_id": task.id,
                "available": evidence is not None,
                "verdict": evidence.verdict.value if evidence is not None else None,
            })
        else:
            task_for_attempts = task
        if self.playbook_hints:
            # off the event loop: knowledge-pack hints may embed the query
            # over the network (blocking HTTP), and a cold endpoint would
            # otherwise stall every run on the loop for up to its timeout
            advice.extend(await asyncio.to_thread(self.playbook_hints, task))

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
                    # Routing failure is a real execution failure.  Leaving the
                    # default UNVERIFIED verdict here used to let WorkflowEngine
                    # record a zero-attempt step as completed.
                    outcome.final_verdict = Verdict.FAIL
                    break

                variant = self._attempt_task(task_for_attempts, advice)
                runner = self.router.runner_for(decision)
                # META-18: capability tokens are an active pre-dispatch gate.
                # After route has selected the exact worker/tier but BEFORE any
                # attempt.* event or runner.run, mint a short-lived token
                # bound to (worker, task, scopes) and immediately re-validate
                # it through the SAME issuer. An invalid token produces zero
                # runner calls, no attempt events, no Attempt, no capability
                # evidence; the outcome is FAIL and provenance records
                # task.authorization_denied.
                scopes = [
                    "task:execute",
                    f"tier:{decision.tier.value}",
                    f"task_type:{task.task_type.value}",
                ]
                dispatched_token = self.token_issuer.issue(
                    decision.worker_id,
                    scopes,
                    ttl_s=self.token_ttl_s,
                    task_id=task.id,
                )
                authorization_check = self.token_issuer.check(
                    dispatched_token,
                    required_scopes=scopes,
                    subject=decision.worker_id,
                    task_id=task.id,
                )
                if not authorization_check.ok:
                    self._record("task.authorization_denied", {
                        "task_id": task.id,
                        "attempt": n,
                        "worker_id": decision.worker_id,
                        "tier": decision.tier.value,
                        "task_type": task.task_type.value,
                        "token_id": dispatched_token.payload.token_id,
                        "reason": authorization_check.reason,
                    })
                    outcome.final_verdict = Verdict.FAIL
                    break
                assignment = {
                    "n": n,
                    "worker_id": decision.worker_id,
                    "model": decision.model,
                    "tier": decision.tier.value,
                    "requested_role": task.role,
                    "requested_capabilities": list(task.required_capabilities),
                    "requested_worker_id": task.worker_id,
                }
                # META-18: capability evidence rides INSIDE the existing
                # `attempt.assigned` payload only when the issuer was
                # explicitly supplied (canonical harness wiring). The private
                # default keeps the legacy payload byte-for-byte unchanged so
                # out-of-scope canonical event-equality tests remain green.
                if self.token_issuer_explicit:
                    assignment["authorization"] = {
                        "token_id": dispatched_token.payload.token_id,
                        "subject": dispatched_token.payload.subject,
                        "task_id": dispatched_token.payload.task_id,
                        "scopes": list(dispatched_token.payload.scopes),
                        "expires_at": dispatched_token.payload.expires_at,
                    }
                emit("attempt.assigned", assignment)
                emit("attempt.started", assignment)
                tool_event_sink = (
                    (lambda kind, payload: emit(kind, {"n": n, **payload}))
                    if event_sink is not None else None
                )
                event_token = bind_run_event_sink(tool_event_sink)
                try:
                    result = await runner.run(variant)
                finally:
                    reset_run_event_sink(event_token)
                result.task_id = task.id
                emit("verification.started", {
                    **assignment,
                    "worker_id": result.worker_id,
                    "model": result.model,
                    "tier": result.tier.value,
                })

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
                            wall_s=result.latency_s + (
                                execution_evidence_latency_s if n == 1 else 0.0
                            ),
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
                    emit("verification.completed", self._verification_event(
                        n, result, verification
                    ))
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
                        emit("verification.completed", self._verification_event(
                            n, result, verification
                        ))
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
                        emit("verification.completed", self._verification_event(
                            n, result, verification
                        ))
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
                # META-19 (F6): a context-contract violation is a deterministic
                # harness failure, not evidence about the model's skill — never
                # bank it in the capability matrix / routing evidence.
                context_contract_failure = result.error_kind == "context_contract"
                if (verification.verdict in (Verdict.PASS, Verdict.FAIL)
                        and verification.scorer != "authenticity"
                        and not timeout_failure
                        and not context_contract_failure):
                    self.router.matrix.record(
                        result.model, task.task_type, verification.verdict == Verdict.PASS
                    )

                attempt = Attempt(n=n, result=result, verification=verification)
                emit("verification.completed", self._verification_event(
                    n, result, verification
                ))
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
                # META-19 (F6): a deterministic context-contract violation will
                # recur identically on retry (pure assembly, same inputs) — abort
                # the task's remaining attempts rather than waste them.
                if context_contract_failure:
                    self._record("task.context_contract", {
                        "task_id": task.id, "n": n, "detail": (result.error or "")[:200],
                    })
                    break
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
                if task.worker_id:
                    # A hard pin is an execution contract, not a routing hint:
                    # every retry stays on that exact eligible identity. Never
                    # exclude its tier or substitute an unrelated higher pool.
                    if timeout_failure and n < task.max_attempts:
                        self._record("task.timeout_retry", {
                            "task_id": task.id,
                            "after_attempt": n,
                            "tier": decision.tier.value,
                            "model": result.model,
                            "worker_id": task.worker_id,
                        })
                elif timeout_failure:
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

    @staticmethod
    def _verification_event(
        n: int, result: WorkerResult, verification: VerificationResult
    ) -> dict[str, Any]:
        """Stable run-event payload shared by live and legacy projections."""
        return {
            "n": n,
            "worker_id": result.worker_id,
            "model": result.model,
            "tier": result.tier.value,
            "verdict": verification.verdict.value,
            "scorer": verification.scorer,
            "detail": verification.detail[:300],
            "failure_mode": (
                verification.failure_mode.value
                if verification.failure_mode else None
            ),
            "latency_s": round(result.latency_s, 2),
            "verification_latency_s": round(verification.latency_s, 2),
            "timed_out": result.timed_out,
        }
