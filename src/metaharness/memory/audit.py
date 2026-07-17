"""Commit-then-log audit boundary for memory writes (META5-MEM-005).

The contract: the durable SQLite COMMIT must complete BEFORE any
observability event describing the write is emitted. The default :class:
``metaharness.memory.stores.MemoryStore`` already commits durably inside
``commit()``. ``CommitOrderedMemoryStore`` here adds a post-commit audit hook
that fires only after the underlying write is durable.

Bind a sink via :func:`bind_sink`; events emitted from the store are then
dispatched to your sink. Tests use this to verify the order contract.
"""
from __future__ import annotations

import threading
from typing import Any, Callable

from metaharness.memory.stores import MemoryStore


_sink_lock = threading.Lock()
_sink: Callable[[str, dict[str, Any]], None] | None = None


def bind_sink(fn: Callable[[str, dict[str, Any]], None]) -> Callable[[], None]:
    """Register ``fn`` as the process-wide memory audit sink. Returns a
    callable that resets the sink — invoke it from finally blocks to avoid
    leaking the sink between tests.

    META5-MEM-005: this is the same shape as the existing
    ``metaharness.observability.run_events.bind_run_event_sink``; the
    audit hook here is memory-scoped, so a memory write through
    :class:`CommitOrderedMemoryStore` cannot trigger an audit event before
    its own COMMIT completes.
    """

    global _sink
    if not callable(fn):
        raise TypeError("bind_sink requires a callable")
    with _sink_lock:
        _sink = fn

    def _reset() -> None:
        global _sink
        with _sink_lock:
            if _sink is fn:
                _sink = None

    return _reset


def reset_sink() -> None:
    """Clear the process-wide sink (for tests that don't want residual
    handlers from a prior test)."""

    global _sink
    with _sink_lock:
        _sink = None


def _emit(event_type: str, payload: dict[str, Any]) -> None:
    sink = _sink
    if sink is not None:
        sink(event_type, payload)


class CommitOrderedMemoryStore(MemoryStore):
    """MemoryStore subclass whose ``commit()`` emits an audit event AFTER
    the underlying SQLite COMMIT completes. The emitted payload carries
    ``commit_state == "committed"`` so consumers can rely on durability.

    The audit event is dispatched with a copy of the record id and scope so
    post-commit downstream code (WebUI provenance, harness optimizers) can
    consume the record without consulting the store.
    """

    default_kind = None

    def commit(self, **kwargs: Any):
        record = super().commit(**kwargs)
        # super().commit() returned — the underlying WAL flush is durable.
        # Any failure here would only affect the audit hook, not durability.
        _emit(
            "memory.commit",
            {
                "commit_state": "committed",
                "record_id": record.id,
                "kind": record.kind.value,
                "scope": record.scope.model_dump(mode="json"),
                "observed_at": record.observed_at,
            },
        )
        return record

    def mutate(self, *args: Any, **kwargs: Any):
        # Mutation also emits an audit event with commit_state=committed,
        # only after the super().mutate() SQLite writes have been committed.
        receipted = kwargs.get("receipt")
        new_record = super().mutate(*args, **kwargs)
        _emit(
            "memory.mutation_committed",
            {
                "commit_state": "committed",
                "record_id": new_record.id,
                "mutation_receipt_provided": receipted is not None,
                "scope": new_record.scope.model_dump(mode="json"),
                "observed_at": new_record.observed_at,
            },
        )
        return new_record
