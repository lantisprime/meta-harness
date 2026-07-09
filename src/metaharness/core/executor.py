"""TaskExecutor: the execute→verify→reflect→escalate loop for one task.

This is the heart of the meta-harness. One call runs a task to completion:

    route (cheapest capable) → run → confirm authenticity → verify →
    on FAIL: reflect, escalate the tier, retry → record everything

Every attempt updates the capability matrix (verified outcomes only), charges
the budget, and is journaled into the provenance chain under the orchestrator's
own key. Guardrails from core.budget stop runaway loops: hard budget ceilings,
plateau detection across attempts, and step-repetition dedup.
"""
from __future__ import annotations

from typing import Callable, Optional

from metaharness.core.budget import Budget, BudgetExceeded, PlateauDetector, action_signature
from metaharness.core.types import (
    Attempt,
    MASTMode,
    Task,
    TaskOutcome,
    TaskType,
    VerificationResult,
    Verdict,
    now,
)
from metaharness.evals.verifiers import authenticity_failure, verify_output
from metaharness.harness.runner import verify_result
from metaharness.identity.keys import KeyPair
from metaharness.identity.provenance import ProvenanceLog
from metaharness.identity.registry import WorkerRegistry
from metaharness.observability.tracing import tracer
from metaharness.routing.router import Router

# a reflector turns a failed attempt into advice for the next one
Reflector = Callable[[Task, Attempt], Optional[str]]
# a judge grades an UNVERIFIED output against its contract (rubric-judge slot
# of the verifier hierarchy); async because it calls a worker
Judge = Callable[..., "object"]


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
        excluded_tiers: set = set()
        advice: list[str] = []
        if self.playbook_hints:
            advice.extend(self.playbook_hints(task))

        with tracer().start_as_current_span("task.execute") as span:
            span.set_attribute("task.id", task.id)
            span.set_attribute("task.type", task.task_type.value)
            self._record("task.started", {"task_id": task.id, "objective": task.objective[:200]})

            for n in range(1, task.max_attempts + 1):
                try:
                    decision = self.router.decide(task, exclude=excluded_tiers, budget=self.budget)
                except ValueError as exc:
                    self._record("task.aborted", {"task_id": task.id, "reason": str(exc)})
                    break

                variant = self._attempt_task(task, advice)
                runner = self.router.runner_for(decision)
                result = await runner.run(variant)
                result.task_id = task.id

                try:
                    if self.budget is not None:
                        self.budget.charge(
                            cost_usd=result.cost_usd,
                            tokens=result.tokens_in + result.tokens_out,
                        )
                except BudgetExceeded as exc:
                    verification = VerificationResult(
                        verdict=Verdict.FAIL, score=0.0, detail=str(exc),
                        failure_mode=MASTMode.BUDGET_EXCEEDED, scorer="budget",
                    )
                    outcome.attempts.append(Attempt(n=n, result=result, verification=verification))
                    self._record("task.budget_exceeded", {"task_id": task.id, "detail": str(exc)})
                    break

                # authenticity first: an unverifiable result is rejected on sight
                if self.registry is not None and not verify_result(result, self.registry):
                    verification = authenticity_failure(
                        f"result from {result.worker_id!r} has no valid signature "
                        "under its registered key"
                    )
                else:
                    verification = verify_output(task, result)
                    if (verification.verdict == Verdict.UNVERIFIED
                            and self.judge is not None
                            and task.task_type != TaskType.PLANNING):
                        # rubric-judge slot: no deterministic check exists, so a
                        # fresh-context LLM grades the output against the step's
                        # own contract — a FAIL enters the normal retry loop
                        # BEFORE any dependent step consumes the output
                        try:
                            verification = await self.judge(variant, result)
                        except Exception as exc:  # a broken judge must never fail the task,
                            # but a silent one is undiagnosable — record why it broke
                            self._record("judge.error", {
                                "task_id": task.id, "attempt": n,
                                "error": f"{type(exc).__name__}: {exc}"[:300],
                            })
                    # capability matrix learns from checkable outcomes only
                    # (deterministic or rubric-judged; scorer says which)
                    if verification.verdict in (Verdict.PASS, Verdict.FAIL):
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

                # an ε-explored failure is the benched member's, not the tier's:
                # the FAIL is already banked in the matrix, so retrying lets the
                # next decide() pick the best member on merit — escalating would
                # pay a higher tier for evidence we chose to buy cheap
                if not decision.explored:
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
