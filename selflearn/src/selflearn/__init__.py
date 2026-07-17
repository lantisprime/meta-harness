"""selflearn — standalone self-learning knowledge system.

Acquire knowledge from sources, verify it externally, gate it with generated
evals, retrieve it into prompts, and learn from verified outcomes. Host
integration happens exclusively through the five ports in
:mod:`selflearn.ports`; artifacts are plain files.

This package has zero imports from any host harness.
"""
from selflearn.contracts import (
    Asset,
    CandidateEntry,
    ContractError,
    EntrySource,
    GapSignal,
    Probe,
    ProcedureStep,
    Provenance,
    PublishDecision,
    SourceDocument,
    SourceRef,
    TaskOutcome,
)
from selflearn.ports import (
    EmbeddingPort,
    ExecutionPort,
    ExecutionResult,
    IdentityPort,
    JsonlProvenance,
    ModelIdIdentity,
    ModelPort,
    ProvenancePort,
)
from selflearn.store import PackStore, StoredEntry, StoreError

__version__ = "0.1.0"

__all__ = [
    "Asset", "CandidateEntry", "ContractError", "EntrySource", "GapSignal",
    "Probe", "ProcedureStep", "Provenance", "PublishDecision",
    "SourceDocument", "SourceRef", "TaskOutcome",
    "EmbeddingPort", "ExecutionPort", "ExecutionResult", "IdentityPort",
    "JsonlProvenance", "ModelIdIdentity", "ModelPort", "ProvenancePort",
    "PackStore", "StoredEntry", "StoreError",
    "__version__",
]
