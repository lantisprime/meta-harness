"""The uniform Runner interface every worker harness sits behind.

The orchestrator never talks to a model directly — it talks to a Runner: a Task
goes in, a WorkerResult comes out. Real LLM-backed harnesses, scripted mocks, and
enrichment wrappers all present this same face, so the router and orchestrator can
compose them freely.

`BaseRunner.run` owns the cross-cutting concerns (OTel span, timing, error
capture, result signing); concrete runners implement `_execute` only.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Optional

from metaharness.core.types import Task, Tier, WorkerResult
from metaharness.identity.canonical import canonical_bytes
from metaharness.identity.keys import KeyPair
from metaharness.identity.registry import WorkerRegistry
from metaharness.observability.tracing import tracer


class WorkerTimeout(RuntimeError):
    """A worker's `_execute` ran out of its allotted wall-clock time. Carries
    the timeout actually applied (post task-type scaling / config override,
    issue #2) so callers can report it without re-deriving it."""

    def __init__(self, msg: str, timeout_s: float) -> None:
        super().__init__(msg)
        self.timeout_s = timeout_s


def result_signing_bytes(result: WorkerResult) -> bytes:
    """The exact bytes a worker signs to vouch for a result. Covers the fields
    that matter (who, what task, what output) — not latency/cost bookkeeping.
    `timed_out` is deliberately excluded (issue #2): it is unsigned derived
    metadata, and adding a key here would invalidate every signature already
    recorded in past journals."""
    return canonical_bytes(
        {
            "kind": "worker_result",
            "task_id": result.task_id,
            "worker_id": result.worker_id,
            "model": result.model,
            "output": result.output,
            "raw_text": result.raw_text,
            "error": result.error,
        }
    )


def sign_result(result: WorkerResult, keypair: KeyPair) -> WorkerResult:
    """(Re)sign a result in place. Enrichment wrappers that legitimately rewrite
    a worker's output inside the same harness boundary re-sign with the worker's
    own key so the signature keeps matching what is actually returned."""
    result.signature_b64 = keypair.sign(result_signing_bytes(result))
    return result


def verify_result(result: WorkerResult, registry: WorkerRegistry) -> bool:
    """True iff the result carries a signature that verifies under the key
    registered for its worker_id."""
    if not result.signature_b64:
        return False
    return registry.verify_message(
        result.worker_id, result_signing_bytes(result), result.signature_b64
    )


class Runner(ABC):
    """Anything that can execute a Task. Wrappers wrap Runners and are Runners."""

    worker_id: str
    tier: Tier
    model: str

    @abstractmethod
    async def run(self, task: Task) -> WorkerResult: ...


class BaseRunner(Runner):
    """Span + timing + signing wrapper around a concrete `_execute`."""

    def __init__(
        self,
        worker_id: str,
        tier: Tier,
        model: str,
        keypair: Optional[KeyPair] = None,
    ) -> None:
        self.worker_id = worker_id
        self.tier = tier
        self.model = model
        self.keypair = keypair

    async def run(self, task: Task) -> WorkerResult:
        with tracer().start_as_current_span("worker.run") as span:
            span.set_attribute("worker.id", self.worker_id)
            span.set_attribute("worker.tier", self.tier.value)
            span.set_attribute("worker.model", self.model)
            span.set_attribute("task.id", task.id)
            span.set_attribute("task.type", task.task_type.value)
            started = time.monotonic()
            try:
                result = await self._execute(task)
            except Exception as exc:  # a failed attempt is data, not a crash
                result = WorkerResult(
                    task_id=task.id,
                    worker_id=self.worker_id,
                    tier=self.tier,
                    model=self.model,
                    error=f"{type(exc).__name__}: {exc}",
                    timed_out=isinstance(exc, WorkerTimeout),  # issue #2
                )
            result.latency_s = time.monotonic() - started
            if self.keypair is not None:
                sign_result(result, self.keypair)
            span.set_attribute("worker.tokens_in", result.tokens_in)
            span.set_attribute("worker.tokens_out", result.tokens_out)
            span.set_attribute("worker.cost_usd", result.cost_usd)
            if result.error:
                span.set_attribute("worker.error", result.error)
            return result

    @abstractmethod
    async def _execute(self, task: Task) -> WorkerResult: ...
