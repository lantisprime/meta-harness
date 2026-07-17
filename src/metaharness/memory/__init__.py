"""Typed memory-skill substrate: append-only records, receipted mutations,
specialist task-action contracts, and circuit-breaker health signals.

The memory package closes META5-MEM-001, 004, 005, 007, 008, 013. It depends
only on stdlib + pydantic + ``metaharness.context`` (per the build spec's
hard-boundary rule). No imports from ``metaharness.harness`` / runtime workers.
"""
from metaharness.memory.records import (
    ActivationState,
    LifecycleState,
    MemoryKind,
    MemoryMutationReceipt,
    MemoryRecord,
    normalize_text,
)
from metaharness.memory.stores import (
    EpisodicMemoryStore,
    ImmutableRecordError,
    MemoryStore,
    ProceduralMemoryStore,
    SemanticMemoryStore,
    UnreceiptedMutationError,
    WorkingMemoryStore,
)

__all__ = [
    "ActivationState",
    "EpisodicMemoryStore",
    "ImmutableRecordError",
    "LifecycleState",
    "MemoryKind",
    "MemoryMutationReceipt",
    "MemoryRecord",
    "MemoryStore",
    "ProceduralMemoryStore",
    "SemanticMemoryStore",
    "UnreceiptedMutationError",
    "WorkingMemoryStore",
    "normalize_text",
]
