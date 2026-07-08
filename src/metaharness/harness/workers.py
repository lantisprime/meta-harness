"""Local workers: a simulated LLM with tunable skill, and a scripted worker.

`MockLLMWorker` simulates a model at a given tier so the orchestration machinery
(routing, escalation, verification, self-correction, evals) can be exercised
deterministically and offline. It "knows" the expected answer from the task's
`success_check` and returns it with probability = skill for that task type,
otherwise a corrupted answer — which is exactly the statistical behavior the
router and eval harness have to cope with in production.

The PAL detail worth noting: when asked to *emit a program* instead of computing,
the simulated skill is substantially higher (`transcribe boost`) — writing the
computation down is easier than doing it, which is the entire reason tool offload
lifts small models.
"""
from __future__ import annotations

import random
from typing import Any, Awaitable, Callable, Optional

from metaharness.core.types import Task, TaskType, Tier, WorkerResult
from metaharness.harness.runner import BaseRunner
from metaharness.identity.keys import KeyPair

# simulated $/1k tokens (blended in+out), per tier
TIER_COST_PER_1K = {Tier.SMALL: 0.0006, Tier.MID: 0.006, Tier.FRONTIER: 0.03}

# emitting a program is much easier than computing the result (PAL insight)
TRANSCRIBE_BOOST = 0.35

DEFAULT_SKILLS: dict[Tier, dict[TaskType, float]] = {
    Tier.SMALL: {
        TaskType.CLASSIFY: 0.85,
        TaskType.EXTRACT: 0.80,
        TaskType.SUMMARIZE: 0.75,
        TaskType.TRANSFORM: 0.70,
        TaskType.ARITHMETIC: 0.55,
        TaskType.CODE_EDIT: 0.45,
        TaskType.REASONING: 0.40,
        TaskType.PLANNING: 0.30,
        TaskType.GENERAL: 0.55,
    },
    Tier.MID: {
        TaskType.CLASSIFY: 0.95,
        TaskType.EXTRACT: 0.93,
        TaskType.SUMMARIZE: 0.90,
        TaskType.TRANSFORM: 0.88,
        TaskType.ARITHMETIC: 0.75,
        TaskType.CODE_EDIT: 0.78,
        TaskType.REASONING: 0.72,
        TaskType.PLANNING: 0.65,
        TaskType.GENERAL: 0.80,
    },
    Tier.FRONTIER: {
        TaskType.CLASSIFY: 0.99,
        TaskType.EXTRACT: 0.98,
        TaskType.SUMMARIZE: 0.97,
        TaskType.TRANSFORM: 0.96,
        TaskType.ARITHMETIC: 0.90,
        TaskType.CODE_EDIT: 0.93,
        TaskType.REASONING: 0.92,
        TaskType.PLANNING: 0.90,
        TaskType.GENERAL: 0.94,
    },
}


class MockLLMWorker(BaseRunner):
    def __init__(
        self,
        worker_id: str,
        tier: Tier,
        model: str = "",
        keypair: Optional[KeyPair] = None,
        skills: Optional[dict[TaskType, float]] = None,
        seed: int = 0,
    ) -> None:
        super().__init__(
            worker_id=worker_id,
            tier=tier,
            model=model or f"mock-{tier.value}",
            keypair=keypair,
        )
        self.skills = skills or dict(DEFAULT_SKILLS[tier])
        self.rng = random.Random(seed)

    # -- simulation core ---------------------------------------------------------

    def _skill_for(self, task: Task) -> float:
        base = self.skills.get(task.task_type, 0.5)
        if task.inputs.get("emit_program"):
            return min(1.0, base + TRANSCRIBE_BOOST)
        return base

    def _expected(self, task: Task) -> Any:
        if task.inputs.get("emit_program") and "expression" in task.inputs:
            return {"program": task.inputs["expression"]}
        check = task.success_check or {}
        if "equals" in check:
            return check["equals"]
        if "contains" in check:
            return f"{check['contains']}: attempt at {task.objective[:60]}"
        if "one_of" in check and check["one_of"]:
            return check["one_of"][0]
        return {"answer": f"attempt at: {task.objective[:60]}"}

    def _corrupt(self, value: Any, task: Task) -> Any:
        """A plausible-but-wrong answer, scattered so wrong answers rarely agree
        (which is what makes self-consistency voting work)."""
        if isinstance(value, dict) and "program" in value:
            expr = str(value["program"])
            return {"program": expr.replace("+", "-", 1) if "+" in expr else expr + "+1"}
        if isinstance(value, bool):
            return not value
        if isinstance(value, (int, float)):
            return value + self.rng.choice([-7, -3, -1, 1, 2, 5, 11, 13])
        if isinstance(value, str):
            labels = task.inputs.get("labels")
            if isinstance(labels, list) and len(labels) > 1:
                wrong = [l for l in labels if l != value]
                return self.rng.choice(wrong)
            return value[::-1] if len(value) > 1 else value + "?"
        if isinstance(value, list) and value:
            return value[:-1]
        if isinstance(value, dict) and value:
            k = self.rng.choice(sorted(value))
            return {kk: vv for kk, vv in value.items() if kk != k}
        return "unknown"

    # -- Runner ------------------------------------------------------------------

    async def _execute(self, task: Task) -> WorkerResult:
        expected = self._expected(task)
        correct = self.rng.random() < self._skill_for(task)
        output = expected if correct else self._corrupt(expected, task)
        tokens_in = max(20, len(task.objective) // 4 + len(str(task.inputs)) // 4)
        tokens_out = max(10, len(str(output)) // 4)
        cost = (tokens_in + tokens_out) / 1000 * TIER_COST_PER_1K[self.tier]
        return WorkerResult(
            task_id=task.id,
            worker_id=self.worker_id,
            tier=self.tier,
            model=self.model,
            output=output,
            raw_text=str(output),
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost,
        )


class ScriptedWorker(BaseRunner):
    """Fully deterministic worker driven by a user-supplied handler — for tests
    that need exact control over what a worker returns."""

    def __init__(
        self,
        worker_id: str,
        handler: Callable[[Task], Any | Awaitable[Any]],
        tier: Tier = Tier.SMALL,
        model: str = "scripted",
        keypair: Optional[KeyPair] = None,
    ) -> None:
        super().__init__(worker_id=worker_id, tier=tier, model=model, keypair=keypair)
        self._handler = handler

    async def _execute(self, task: Task) -> WorkerResult:
        output = self._handler(task)
        if hasattr(output, "__await__"):
            output = await output
        return WorkerResult(
            task_id=task.id,
            worker_id=self.worker_id,
            tier=self.tier,
            model=self.model,
            output=output,
            raw_text=str(output),
            tokens_in=len(str(task.inputs)) // 4,
            tokens_out=len(str(output)) // 4,
            cost_usd=0.0,
        )
