"""Typed memory-skill substrate: append-only records and a shadow broker.

The package depends only on stdlib + pydantic + ``metaharness.context``. It has
no runtime-worker imports and does not participate in live prompt assembly.
"""
from metaharness.memory.broker import (
    MemoryAction,
    MemoryActionBroker,
    MemoryActionOutcome,
    MemoryActionReceipt,
    MemoryCognitiveSkillSnapshot,
    MemoryLifecycleProposal,
    MemoryOperation,
    MemoryPhase,
    MemoryPhaseContract,
    MemoryProposalKind,
)
from metaharness.memory.records import (
    ActivationState,
    LifecycleState,
    MemoryKind,
    MemoryMutationReceipt,
    MemoryRecord,
    normalize_text,
)
from metaharness.memory.scaffold import (
    consult_memory,
    log_observation,
    scaffold_consult,
    scaffold_log,
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
    "MemoryAction",
    "MemoryActionBroker",
    "MemoryActionOutcome",
    "MemoryActionReceipt",
    "MemoryCognitiveSkillSnapshot",
    "MemoryKind",
    "MemoryLifecycleProposal",
    "MemoryMutationReceipt",
    "MemoryOperation",
    "MemoryPhase",
    "MemoryPhaseContract",
    "MemoryProposalKind",
    "MemoryRecord",
    "MemoryStore",
    "ProceduralMemoryStore",
    "SemanticMemoryStore",
    "UnreceiptedMutationError",
    "WorkingMemoryStore",
    "consult_memory",
    "log_observation",
    "normalize_text",
    "scaffold_consult",
    "scaffold_log",
]
