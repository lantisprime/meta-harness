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


CURRENT_RESULT_SIGNATURE_VERSION = 3


def result_signing_bytes(result: WorkerResult, *, version: Optional[int] = None) -> bytes:
    """The exact bytes a worker signs to vouch for a result.

    Version 1 is the historical payload and remains readable so old journals do
    not become unverifiable. Version 2 additionally attests ``workspace_root``
    and ``timed_out``: both now affect control flow (automatic test execution and
    timeout classification), so treating them as mutable bookkeeping would let a
    valid signature bless attacker-selected behavior. Version 3 (META-19 FIX-7,
    codex#8) additionally attests ``error_kind``: it drives retry-abort and
    capability-evidence exclusion, so an unsigned field would let a tampered but
    still-verifying result flip that control flow.
    """
    version = result.signature_version if version is None else version
    payload = {
        "kind": "worker_result",
        "task_id": result.task_id,
        "worker_id": result.worker_id,
        "model": result.model,
        "output": result.output,
        "raw_text": result.raw_text,
        "error": result.error,
    }
    if version in (2, 3):
        payload.update({
            "signature_version": version,
            "timed_out": result.timed_out,
            "workspace_root": result.workspace_root,
        })
        if version == 3:
            payload["error_kind"] = result.error_kind
    elif version != 1:
        raise ValueError(f"unsupported worker-result signature version {version}")
    return canonical_bytes(payload)


def sign_result(result: WorkerResult, keypair: KeyPair) -> WorkerResult:
    """(Re)sign a result in place. Enrichment wrappers that legitimately rewrite
    a worker's output inside the same harness boundary re-sign with the worker's
    own key so the signature keeps matching what is actually returned."""
    result.signature_version = CURRENT_RESULT_SIGNATURE_VERSION
    result.signature_b64 = keypair.sign(result_signing_bytes(result))
    return result


def verify_result(result: WorkerResult, registry: WorkerRegistry) -> bool:
    """True iff the result carries a signature that verifies under the key
    registered for its worker_id."""
    if not result.signature_b64:
        return False
    try:
        payload = result_signing_bytes(result)
    except ValueError:
        return False
    return registry.verify_message(result.worker_id, payload, result.signature_b64)


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
                # META-19 (F6): a live-context contract violation is deterministic
                # (pure assembly, no infrastructure luck) — tag it so the executor
                # aborts retries and excludes it from capability evidence. Imported
                # lazily to avoid a harness->context import cycle at module load.
                from metaharness.context.live import LiveContextViolation

                error_kind = "context_contract" if isinstance(exc, LiveContextViolation) else None
                result = WorkerResult(
                    task_id=task.id,
                    worker_id=self.worker_id,
                    tier=self.tier,
                    model=self.model,
                    error=f"{type(exc).__name__}: {exc}",
                    error_kind=error_kind,
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
