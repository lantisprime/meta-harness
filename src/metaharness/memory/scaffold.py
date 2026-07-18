"""Deterministic scaffold-only LOG and CONSULT functions.

These H functions construct typed actions and route them through the shadow
broker. They make no model calls and do not participate in live prompt fitting.
"""
from __future__ import annotations

from typing import Any, Mapping

from metaharness.context import ContextScope, Sensitivity
from metaharness.memory.broker import (
    MemoryAction,
    MemoryActionBroker,
    MemoryActionReceipt,
    MemoryOperation,
    MemoryPhase,
)
from metaharness.memory.records import LifecycleState, MemoryKind


def scaffold_log(
    *,
    broker: MemoryActionBroker,
    scope: ContextScope,
    content: str,
    kind: MemoryKind | str = MemoryKind.EPISODIC_MEMORY,
    source_record_ids: tuple[str, ...] = (),
    sensitivity: Sensitivity | str = Sensitivity.INTERNAL,
    confidence: float = 1.0,
    context_id: str = "scaffold-log",
    context: Mapping[str, Any] | None = None,
    context_hash: str | None = None,
) -> MemoryActionReceipt:
    """Post-observation LOG: append one candidate through the broker."""

    resolved_kind = kind.value if isinstance(kind, MemoryKind) else kind
    resolved_sensitivity = sensitivity.value if isinstance(sensitivity, Sensitivity) else sensitivity
    action = MemoryAction(
        operation=MemoryOperation.CREATE_CANDIDATE,
        phase=MemoryPhase.LOG,
        scope=scope,
        payload={
            "kind": resolved_kind,
            "content": content,
            "source_record_ids": list(source_record_ids),
            "sensitivity": resolved_sensitivity,
            "confidence": confidence,
        },
    )
    return broker.invoke(
        action,
        context_id=context_id,
        context=context,
        context_hash=context_hash,
    )


def scaffold_consult(
    *,
    broker: MemoryActionBroker,
    scope: ContextScope,
    query: str,
    kind: MemoryKind | str | None = None,
    limit: int | None = None,
    lifecycle_filters: tuple[LifecycleState | str, ...] = (
        LifecycleState.CANDIDATE,
        LifecycleState.ACTIVE,
    ),
    context_id: str = "scaffold-consult",
    context: Mapping[str, Any] | None = None,
    context_hash: str | None = None,
) -> MemoryActionReceipt:
    """Pre-action CONSULT: deterministic lexical ranking under scope/budget."""

    payload: dict[str, Any] = {
        "query": query,
        "lifecycle_filters": [
            state.value if isinstance(state, LifecycleState) else state
            for state in lifecycle_filters
        ],
    }
    if kind is not None:
        payload["kind"] = kind.value if isinstance(kind, MemoryKind) else kind
    if limit is not None:
        payload["limit"] = limit
    action = MemoryAction(
        operation=MemoryOperation.SEARCH,
        phase=MemoryPhase.CONSULT,
        scope=scope,
        payload=payload,
    )
    return broker.invoke(
        action,
        context_id=context_id,
        context=context,
        context_hash=context_hash,
    )


log_observation = scaffold_log
consult_memory = scaffold_consult


__all__ = [
    "consult_memory",
    "log_observation",
    "scaffold_consult",
    "scaffold_log",
]
