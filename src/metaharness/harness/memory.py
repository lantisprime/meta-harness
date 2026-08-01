"""A narrowly bounded memory-aware Runner wrapper.

Memory is an H surface: CONSULT can alter only the untrusted advice field of a
copied Task. The inner worker remains authoritative for the domain result and
the broker remains a shadow, receipt-producing boundary.

The wrapper enforces, in order, before any inner.run call:

1. The supplied snapshot is exactly ``broker.snapshot`` (the broker cannot
   attest a different snapshot from the one declared to the wrapper).
2. Every receipt is coerced through ``MemoryActionReceipt``; its
   snapshot_id / snapshot_content_hash / skill_id / context_id / phase /
   operation / scope / selected_targets are validated against the
   wrapper's declared contract.
3. Selected record ids are resolved through the caller-supplied resolver;
   each resolved MemoryRecord's current content hash is computed via
   ``context.models.content_hash(record.content)`` (the broker convention).
   If the receipt supplies a hash for that id, it must match the resolved
   content hash; SEARCH receipts do not supply hashes so this matching is
   skipped when absent.
4. ``assert_secret_safe`` is invoked on every record's content and on every
   LOG observation; a failure becomes ``MemoryAdviceError`` (the only
   portable integrity exception the wrapper catches).

The rejected-CONSULT branch retains the receipt and raises
``MemoryAdviceError`` BEFORE ``inner.run`` is called so the inner worker
cannot be invoked against an unverified advice slot.

The wrapper records per-run evidence (``current_run_receipts``) alongside
the lifetime history so the campaign can attest only the receipts produced
by the current attempt.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Callable

from metaharness.context import estimate_tokens
from metaharness.context.models import content_hash as record_content_hash
from metaharness.core.types import Task, WorkerResult
from metaharness.harness.runner import Runner
from metaharness.memory.broker import (
    MemoryAction,
    MemoryActionBroker,
    MemoryActionReceipt,
    MemoryCognitiveSkillSnapshot,
    MemoryOperation,
    MemoryPhase,
)
from metaharness.memory.records import MemoryRecord
from metaharness.portable.integrity import (
    PortableIntegrityError,
    assert_secret_safe,
    canonical_json_bytes,
)


class MemoryAdviceError(RuntimeError):
    """A governed memory result cannot safely be rendered into advice."""


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json_bytes(value)).hexdigest()


@dataclass(frozen=True)
class MemoryRunEvidence:
    """The exact evidence chain for one run.

    The campaign verifies each field independently: tampering with the
    CONSULT receipt hash, the selected record ids/hashes, the advice
    digest, the full inner-task digest, or the receipt's selected_targets
    must fail ``verify_campaign``.
    """

    consult_receipt_hash: str | None
    consult_receipt_selected_targets: tuple[str, ...]
    consult_receipt_before_content_hashes: tuple[tuple[str, str], ...]
    log_receipt_hash: str | None
    selected_record_ids: tuple[str, ...]
    selected_record_hashes: tuple[tuple[str, str], ...]
    selected_record_contents: tuple[tuple[str, str], ...]
    advice_digest: str
    advice_changed: bool
    inner_task_digest: str
    outer_task_digest: str
    base_task_digest: str
    current_run_receipts: tuple[str, ...]
    outer_task: dict[str, Any]
    inner_task: dict[str, Any]


class MemoryAwareRunner(Runner):
    """Inject broker-selected records into ``Task.advice`` and nothing else."""

    def __init__(
        self,
        *,
        inner: Runner,
        snapshot: MemoryCognitiveSkillSnapshot | None = None,
        broker: MemoryActionBroker | Any | None = None,
        record_resolver: Callable[[str], MemoryRecord | str | None] | None = None,
        observation_selector: Callable[[Task, WorkerResult], str | None] | None = None,
        memory_enabled: bool = True,
        context_id: str = "memory-aware-runner",
        advice_limit: int | None = None,
    ) -> None:
        if memory_enabled and (snapshot is None or broker is None or record_resolver is None):
            raise ValueError("enabled memory requires snapshot, broker, and record_resolver")
        self.inner = inner
        self.snapshot = snapshot
        self.broker = broker
        self.record_resolver = record_resolver
        self.observation_selector = observation_selector
        self.memory_enabled = memory_enabled
        self.context_id = context_id
        self.advice_limit = advice_limit
        self._receipts: list[MemoryActionReceipt] = []
        self._last_evidence: MemoryRunEvidence | None = None

    @property
    def worker_id(self) -> str:
        return self.inner.worker_id

    @property
    def tier(self):
        return self.inner.tier

    @property
    def model(self) -> str:
        return self.inner.model

    @property
    def model_frozen_config(self) -> dict[str, Any] | None:
        """The inner runner's exact model/inference contract. The h_campaign
        loader compares this to the frozen ``ModelContract``/``inference_parameters``
        to reject model/config drift before inference. Returns None when the
        inner runner is a narrow hermetic test adapter."""
        return getattr(self.inner, "model_frozen_config", None)

    @property
    def hermetic_campaign_adapter(self) -> bool:
        """Explicit test-only adapter marker for the campaign contract."""
        return getattr(self.inner, "meta34_hermetic_adapter", None) == "tests-only-v1"

    @property
    def receipts(self) -> tuple[MemoryActionReceipt, ...]:
        return tuple(self._receipts)

    @property
    def last_evidence(self) -> MemoryRunEvidence:
        if self._last_evidence is None:
            raise RuntimeError("runner has not run a task")
        return self._last_evidence

    @staticmethod
    def _task_digest_full(task: Task) -> str:
        """Hash the complete task including advice."""
        return _digest(task.model_dump(mode="json"))

    @staticmethod
    def _task_digest_base(task: Task) -> str:
        """Hash the task with advice stripped out (base task)."""
        return _digest(task.model_dump(mode="json", exclude={"advice"}))

    def _coerce_receipt(self, raw: Any) -> MemoryActionReceipt:
        """Coerce a raw broker return into a MemoryActionReceipt. The
        hermetic protocol adapter may emit a dict-like object; this
        adapter validates every receipt-critical field against the
        declared contract."""
        if isinstance(raw, MemoryActionReceipt):
            receipt = raw
        elif isinstance(raw, dict):
            receipt = MemoryActionReceipt.model_validate(raw)
        else:
            if hasattr(raw, "model_dump"):
                receipt = MemoryActionReceipt.model_validate(raw.model_dump(mode="json"))
            else:
                raise MemoryAdviceError(
                    "broker returned an unsupported receipt type"
                )
        if receipt.snapshot_id != self.snapshot.snapshot_id:
            raise MemoryAdviceError(
                "receipt snapshot_id does not match the declared snapshot"
            )
        if receipt.snapshot_content_hash != self.snapshot.content_hash:
            raise MemoryAdviceError(
                "receipt snapshot_content_hash does not match the declared snapshot"
            )
        if receipt.skill_id != self.snapshot.skill_id:
            raise MemoryAdviceError(
                "receipt skill_id does not match the declared snapshot"
            )
        if receipt.scope != self.snapshot.scope:
            raise MemoryAdviceError(
                "receipt scope does not match the declared snapshot"
            )
        if receipt.context_id != self.context_id:
            raise MemoryAdviceError(
                "receipt context_id does not match the wrapper's context_id"
            )
        if receipt.phase != MemoryPhase.CONSULT.value and receipt.phase != MemoryPhase.LOG.value:
            raise MemoryAdviceError(
                f"receipt phase {receipt.phase!r} is not CONSULT or LOG"
            )
        return receipt

    def _validate_broker_snapshot(self) -> None:
        """Require ``broker.snapshot`` to equal the supplied snapshot."""
        if self.broker is None:
            return
        broker_snapshot = getattr(self.broker, "snapshot", None)
        if broker_snapshot is None:
            raise MemoryAdviceError(
                "broker has no declared snapshot; cannot attest"
            )
        if broker_snapshot.content_hash != self.snapshot.content_hash:
            raise MemoryAdviceError(
                "broker.snapshot content_hash does not match the supplied snapshot"
            )
        if broker_snapshot.snapshot_id != self.snapshot.snapshot_id:
            raise MemoryAdviceError(
                "broker.snapshot snapshot_id does not match the supplied snapshot"
            )

    def _consult(
        self, task: Task
    ) -> tuple[Task, MemoryActionReceipt, tuple[str, ...], tuple[tuple[str, str], ...], tuple[tuple[str, str], ...], tuple[str, ...], tuple[tuple[str, str], ...]]:
        assert self.snapshot is not None and self.broker is not None
        self._validate_broker_snapshot()
        visible = {
            key: value
            for key, value in (task.inputs or {}).items()
            if not key.startswith("_")
        }
        query = task.objective + (
            "\n" + json.dumps(visible, sort_keys=True, ensure_ascii=False, default=str)
            if visible else ""
        )
        action = MemoryAction(
            operation=MemoryOperation.SEARCH, phase=MemoryPhase.CONSULT,
            scope=self.snapshot.scope,
            payload={
                "query": query,
                "lifecycle_filters": list(self.snapshot.query_lifecycle_states),
            },
        )
        raw_receipt = self.broker.invoke(
            action,
            context_id=self.context_id,
            context={"objective": task.objective, "inputs": visible},
        )
        receipt = self._coerce_receipt(raw_receipt)
        self._receipts.append(receipt)
        if not receipt.accepted:
            raise MemoryAdviceError(
                f"CONSULT rejected: {receipt.effect_or_rejection_reason}"
            )
        if receipt.phase != MemoryPhase.CONSULT.value:
            raise MemoryAdviceError(
                f"CONSULT phase mismatch: {receipt.phase!r}"
            )
        if receipt.operation != MemoryOperation.SEARCH.value:
            raise MemoryAdviceError(
                f"CONSULT operation mismatch: {receipt.operation!r}"
            )
        if receipt.scope != self.snapshot.scope:
            raise MemoryAdviceError("CONSULT receipt scope is not bound to the snapshot")
        selected = tuple(receipt.selected_targets)
        if len(selected) > (self.advice_limit or self.snapshot.query_max_results):
            raise MemoryAdviceError("CONSULT selected too many records")
        # The real broker's SEARCH receipt does not populate
        # before_content_hashes; only validate when the receipt supplies
        # one for the selected id. This is the contract the broker
        # enforces (writes emit hashes, reads/search do not).
        before_hashes = dict(receipt.before_content_hashes)
        excerpts: list[str] = []
        selected_hashes: list[tuple[str, str]] = []
        selected_contents: list[tuple[str, str]] = []
        for record_id in selected:
            resolved = self.record_resolver(record_id)
            if resolved is None:
                raise MemoryAdviceError("selected memory record could not be resolved")
            if not isinstance(resolved, MemoryRecord):
                raise MemoryAdviceError("record resolver must return governed MemoryRecord metadata")
            if resolved.id != record_id:
                raise MemoryAdviceError("record resolver returned a different id")
            if resolved.scope != self.snapshot.scope:
                raise MemoryAdviceError("selected memory record is outside the governed scope")
            if resolved.lifecycle_state not in self.snapshot.query_lifecycle_states:
                raise MemoryAdviceError(
                    f"record {record_id} lifecycle is not in snapshot query_lifecycle_states"
                )
            if resolved.sensitivity not in self.snapshot.allowed_sensitivities:
                raise MemoryAdviceError("selected memory record sensitivity exceeds policy")
            try:
                assert_secret_safe(
                    resolved.content,
                    location=f"memory record {record_id} content",
                )
            except PortableIntegrityError as exc:
                raise MemoryAdviceError(
                    f"selected memory record {record_id} is not safe for advice: {exc}"
                ) from exc
            # The broker's hash convention is content_hash(record.content).
            # If the receipt voluntarily supplied a hash for this id, it
            # MUST match the resolved record's current content hash.
            current_hash = record_content_hash(resolved.content)
            supplied_hash = before_hashes.get(record_id)
            if supplied_hash is not None and supplied_hash != current_hash:
                raise MemoryAdviceError(
                    f"record {record_id} current content hash does not match the CONSULT receipt"
                )
            selected_hashes.append((record_id, current_hash))
            selected_contents.append((record_id, resolved.content))
            if estimate_tokens(resolved.content) > self.snapshot.compression_max_tokens:
                raise MemoryAdviceError("selected memory record exceeds snapshot compression bound")
            excerpts.append(
                f"[governed memory record {record_id}; untrusted advice]\n{resolved.content}"
            )
        if sum(estimate_tokens(excerpt) for excerpt in excerpts) > self.snapshot.context_budget_tokens:
            raise MemoryAdviceError("rendered memory advice exceeds snapshot context budget")
        copied = task.model_copy(deep=True)
        copied.advice = list(copied.advice) + excerpts
        return (
            copied,
            receipt,
            selected,
            tuple(selected_hashes),
            tuple(selected_contents),
            tuple(receipt.selected_targets),
            tuple(receipt.before_content_hashes),
        )

    async def run(self, task: Task) -> WorkerResult:
        if not self.memory_enabled:
            outer_digest = self._task_digest_full(task)
            base_digest = self._task_digest_base(task)
            # No-memory mode: no broker call, no advice injection, no
            # receipts.
            self._last_evidence = MemoryRunEvidence(
                consult_receipt_hash=None,
                consult_receipt_selected_targets=(),
                consult_receipt_before_content_hashes=(),
                log_receipt_hash=None,
                selected_record_ids=(),
                selected_record_hashes=(),
                selected_record_contents=(),
                advice_digest=_digest(list(task.advice)),
                advice_changed=False,
                inner_task_digest=outer_digest,
                outer_task_digest=outer_digest,
                base_task_digest=base_digest,
                current_run_receipts=(),
                outer_task=task.model_dump(mode="json"),
                inner_task=task.model_dump(mode="json"),
            )
            return await self.inner.run(task.model_copy(deep=True))
        outer_digest = self._task_digest_full(task)
        base_digest = self._task_digest_base(task)
        receipt_start = len(self._receipts)
        copied, consult, selected, selected_hashes, selected_contents, consult_targets, consult_before = self._consult(task)
        inner_digest = self._task_digest_full(copied)
        result = await self.inner.run(copied)
        log: MemoryActionReceipt | None = None
        if self.observation_selector is not None:
            observation = self.observation_selector(task, result)
            if observation is not None:
                if not isinstance(observation, str) or not observation.strip():
                    raise MemoryAdviceError("governed LOG observation is empty")
                try:
                    assert_secret_safe(
                        observation,
                        location="governed LOG observation",
                    )
                except PortableIntegrityError as exc:
                    raise MemoryAdviceError(
                        f"governed LOG observation is not safe: {exc}"
                    ) from exc
                action = MemoryAction(
                    operation=MemoryOperation.CREATE_CANDIDATE, phase=MemoryPhase.LOG,
                    scope=self.snapshot.scope, payload={"content": observation},
                )
                raw_log = self.broker.invoke(
                    action, context_id=self.context_id, context={"task_id": task.id},
                )
                log = self._coerce_receipt(raw_log)
                self._receipts.append(log)
                if not log.accepted:
                    raise MemoryAdviceError(
                        f"governed LOG was rejected: {log.effect_or_rejection_reason}"
                    )
                # LOG must be phase=log and operation=create_candidate.
                if log.phase != MemoryPhase.LOG.value:
                    raise MemoryAdviceError(
                        f"LOG phase mismatch: {log.phase!r}"
                    )
                if log.operation != MemoryOperation.CREATE_CANDIDATE.value:
                    raise MemoryAdviceError(
                        f"LOG operation mismatch: {log.operation!r}"
                    )
        current_run_receipts = tuple(
            r.content_hash for r in self._receipts[receipt_start:]
        )
        self._last_evidence = MemoryRunEvidence(
            consult_receipt_hash=consult.content_hash,
            consult_receipt_selected_targets=consult_targets,
            consult_receipt_before_content_hashes=consult_before,
            log_receipt_hash=log.content_hash if log is not None else None,
            selected_record_ids=selected,
            selected_record_hashes=selected_hashes,
            selected_record_contents=selected_contents,
            advice_digest=_digest(list(copied.advice)),
            advice_changed=copied.advice != task.advice,
            inner_task_digest=inner_digest,
            outer_task_digest=outer_digest,
            base_task_digest=base_digest,
            current_run_receipts=current_run_receipts,
            outer_task=task.model_dump(mode="json"),
            inner_task=copied.model_dump(mode="json"),
        )
        return result


__all__ = ["MemoryAdviceError", "MemoryAwareRunner", "MemoryRunEvidence"]
