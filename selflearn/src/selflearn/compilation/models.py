"""Compilation module data models.

These are the frozen value objects exchanged between compilation components.
Mirror the self-verifying receipt idiom from metaharness.memory.records.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from selflearn.contracts import ContractError, ProcedureStep

EXECUTOR_STATUSES = ("quarantined", "active", "rejected", "superseded")


def canonical_json(obj: Any) -> str:
    """Canonical JSON serialization for deterministic hashing."""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def content_hash(text: str) -> str:
    """SHA256 content hash, returns 64-char hex string."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical_procedure_hash(procedure: tuple[ProcedureStep, ...]) -> str:
    """Compute deterministic hash over a procedure's canonical representation.

    The canonical form preserves: step order, check pairs in declaration order,
    and all fields. This is the spec hash function used everywhere in compilation.
    """
    step_list = []
    for step in procedure:
        step_dict = {
            "id": step.id,
            "objective": step.objective,
            "task_type": step.task_type,
            "tools": list(step.tools),
            "depends_on": list(step.depends_on),
            "check": [list(pair) for pair in step.check],
        }
        step_list.append(step_dict)
    return content_hash(canonical_json(step_list))


@dataclass(frozen=True)
class ExecutorSpec:
    """Specification for a compiled workflow executor."""

    entry_id: str
    pack: str
    spec_hash: str  # 64-hex sha256
    procedure: tuple[ProcedureStep, ...]

    def __post_init__(self) -> None:
        from selflearn.contracts import _require

        _require(bool(self.entry_id), "ExecutorSpec.entry_id must be non-empty")
        _require(bool(self.pack), "ExecutorSpec.pack must be non-empty")
        _require(len(self.spec_hash) == 64,
                 "ExecutorSpec.spec_hash must be a 64-hex sha256")
        _require(bool(self.procedure), "ExecutorSpec.procedure must be non-empty")


@dataclass(frozen=True)
class ExecutorCandidate:
    """A compiled workflow executor candidate."""

    spec: ExecutorSpec
    source: str  # generated Python source
    executor_hash: str  # sha256 of source
    compiled_at: str  # ISO timestamp
    compiler_id: str  # identifies the compiler version

    def __post_init__(self) -> None:
        from selflearn.contracts import _require

        _require(len(self.executor_hash) == 64,
                 "ExecutorCandidate.executor_hash must be a 64-hex sha256")
        _require(bool(self.source), "ExecutorCandidate.source must be non-empty")


@dataclass(frozen=True)
class IndependentTestSuite:
    """Test suite authored by an independent test author."""

    spec_hash: str
    test_source: str  # generated test Python source
    suite_hash: str  # sha256 of test_source
    author_id: str
    identity_basis: str
    authored_at: str  # ISO timestamp

    def __post_init__(self) -> None:
        from selflearn.contracts import _require

        _require(len(self.spec_hash) == 64,
                 "IndependentTestSuite.spec_hash must be a 64-hex sha256")
        _require(len(self.suite_hash) == 64,
                 "IndependentTestSuite.suite_hash must be a 64-hex sha256")
        _require(bool(self.test_source),
                 "IndependentTestSuite.test_source must be non-empty")
        _require(bool(self.author_id),
                 "IndependentTestSuite.author_id must be non-empty")


@dataclass(frozen=True)
class ApprovalRecord:
    """Record of an approval decision."""

    approver: str  # non-empty
    basis: str
    strict_mode: bool
    approved_at: str  # ISO timestamp

    def __post_init__(self) -> None:
        from selflearn.contracts import _require

        _require(bool(self.approver), "ApprovalRecord.approver must be non-empty")
        _require(bool(self.basis), "ApprovalRecord.basis must be non-empty")
        # Activation requires strict_mode approval
        _require(self.strict_mode is True,
                 "ApprovalRecord: strict_mode must be True for activation")


@dataclass(frozen=True)
class CrossValidationReceipt:
    """Receipt from the cross-validation gate.

    Self-hashing: receipt_id is computed from canonical JSON of all fields
    except receipt_id itself. __post_init__ recomputes and rejects mismatch.
    """

    receipt_id: str
    spec_hash: str
    executor_hash: str
    suite_hash: str
    sandbox_ok: bool
    sandbox_output: str  # truncated to 2000 chars
    approval: ApprovalRecord | None
    verdict: str  # "activated" | "rejected" | "refused"
    reason: str
    decided_at: str  # ISO timestamp

    def __post_init__(self) -> None:
        from selflearn.contracts import _require

        _require(self.verdict in ("activated", "rejected", "refused"),
                 f"CrossValidationReceipt.verdict must be one of "
                 f"activated/rejected/refused, got {self.verdict!r}")
        _require(len(self.spec_hash) == 64,
                 "CrossValidationReceipt.spec_hash must be 64-hex")
        _require(len(self.executor_hash) == 64,
                 "CrossValidationReceipt.executor_hash must be 64-hex")
        _require(len(self.suite_hash) == 64,
                 "CrossValidationReceipt.suite_hash must be 64-hex")
        _require(len(self.sandbox_output) <= 2000,
                 "CrossValidationReceipt.sandbox_output truncated to 2000")

        # Self-hash verification: recompute from canonical JSON minus receipt_id
        material = {
            "spec_hash": self.spec_hash,
            "executor_hash": self.executor_hash,
            "suite_hash": self.suite_hash,
            "sandbox_ok": self.sandbox_ok,
            "sandbox_output": self.sandbox_output,
            "approval": {
                "approver": self.approval.approver,
                "basis": self.approval.basis,
                "strict_mode": self.approval.strict_mode,
                "approved_at": self.approval.approved_at,
            } if self.approval else None,
            "verdict": self.verdict,
            "reason": self.reason,
            "decided_at": self.decided_at,
        }
        computed = content_hash(canonical_json(material))

        # If receipt_id was empty, compute it
        if not self.receipt_id:
            object.__setattr__(self, 'receipt_id', computed)
        elif self.receipt_id != computed:
            raise ContractError(
                f"CrossValidationReceipt receipt_id mismatch: "
                f"expected {computed!r}, got {self.receipt_id!r}")


@dataclass(frozen=True)
class ExecutorRecord:
    """Persistent record of an executor's state in the registry.

    Self-hashing: record_id is computed from canonical JSON of all fields
    except record_id itself.
    """

    record_id: str
    entry_id: str
    pack: str
    spec_hash: str
    executor_hash: str
    status: str  # EXECUTOR_STATUSES
    path: str  # relative to store root
    receipt_id: str
    updated_at: str  # ISO timestamp

    def __post_init__(self) -> None:
        from selflearn.contracts import _require

        _require(self.status in EXECUTOR_STATUSES,
                 f"ExecutorRecord.status must be one of {EXECUTOR_STATUSES}")
        _require(bool(self.entry_id), "ExecutorRecord.entry_id must be non-empty")
        _require(bool(self.pack), "ExecutorRecord.pack must be non-empty")
        _require(len(self.spec_hash) == 64,
                 "ExecutorRecord.spec_hash must be 64-hex")
        _require(len(self.executor_hash) == 64,
                 "ExecutorRecord.executor_hash must be 64-hex")
        _require(bool(self.path), "ExecutorRecord.path must be non-empty")
        _require(bool(self.receipt_id), "ExecutorRecord.receipt_id must be non-empty")

        # Self-hash verification: recompute from canonical JSON minus record_id
        material = {
            "entry_id": self.entry_id,
            "pack": self.pack,
            "spec_hash": self.spec_hash,
            "executor_hash": self.executor_hash,
            "status": self.status,
            "path": self.path,
            "receipt_id": self.receipt_id,
            "updated_at": self.updated_at,
        }
        computed = content_hash(canonical_json(material))

        # If record_id was empty, compute it
        if not self.record_id:
            object.__setattr__(self, 'record_id', computed)
        elif self.record_id != computed:
            raise ContractError(
                f"ExecutorRecord record_id mismatch: "
                f"expected {computed!r}, got {self.record_id!r}")
