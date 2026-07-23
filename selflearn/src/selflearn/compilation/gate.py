"""Cross-validation gate: evaluates executors against test suites.

The gate enforces:
- Identity separation between test author and compiler
- Sandbox execution of tests
- Strict-mode approval requirement for activation
- Regression baseline check for executor swaps
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from selflearn.compilation.models import (
    ApprovalRecord,
    CrossValidationReceipt,
    ExecutorCandidate,
    ExecutorRecord,
    IndependentTestSuite,
    canonical_procedure_hash,
)
from selflearn.compilation.registry import ExecutorRegistry
from selflearn.compilation.compiler import COMPILER_ID
from selflearn.ports import ExecutionPort, ProvenancePort

# Public alias for tests importing gate.store (F2-15 reuse)


class _RegistryBackedStore:
    """Thin wrapper exposing PackStore get() via registry.store."""

    def __init__(self, registry: ExecutorRegistry):
        self._registry = registry

    def get(self, entry_id: str):
        return self._registry.store.get(entry_id)


def _store_via_registry(registry: ExecutorRegistry):
    return _RegistryBackedStore(registry)


# Gate errors
class GateError(RuntimeError):
    """Error during gate evaluation."""
    pass


class CrossValidationGate:
    """Cross-validation gate for workflow executors."""

    def __init__(self, execution: ExecutionPort | None, registry: ExecutorRegistry,
                 provenance: ProvenancePort, clock: callable):
        self.execution = execution
        self.registry = registry
        self.provenance = provenance
        self.clock = clock

    def evaluate(self, candidate: ExecutorCandidate, suite: IndependentTestSuite,
                 approval: ApprovalRecord | None, *, decided_at: str) -> CrossValidationReceipt:
        """Evaluate a candidate against a test suite.

        F2-3: restructure every gate path as:
        1. construct CrossValidationReceipt first (receipt_id="" -> self-hash)
        2. perform registry transitions using receipt.receipt_id
        3. journal the FULL receipt in provenance

        F2-5: stale spec check is unconditional (no if active: guard)

        F2-8: identity collision check before sandbox

        Args:
            candidate: The compiled executor candidate
            suite: The independent test suite
            approval: Optional approval record (strict_mode required for activation)
            decided_at: ISO timestamp of the decision

        Returns:
            CrossValidationReceipt with verdict and reasoning

        Raises:
            GateError: If execution port is unbound or precondition fails
        """
        entry_id = candidate.spec.entry_id

        # F2-15: move execution is None check to top
        if self.execution is None:
            receipt = CrossValidationReceipt(
                receipt_id="",
                spec_hash=candidate.spec.spec_hash,
                executor_hash=candidate.executor_hash,
                suite_hash=suite.suite_hash,
                sandbox_ok=False,
                sandbox_output="",
                approval=None,
                verdict="rejected",
                reason="CrossValidationGate requires ExecutionPort",
                decided_at=decided_at,
            )
            self._journal("gate.error", candidate, receipt)
            raise GateError(
                "CrossValidationGate requires ExecutionPort; refusing to run "
                "without sandbox (verifier.py:150-154 convention)")

        # F2-8: identity collision check before sandbox
        if suite.author_id == COMPILER_ID or not suite.identity_basis:
            receipt = CrossValidationReceipt(
                receipt_id="",
                spec_hash=candidate.spec.spec_hash,
                executor_hash=candidate.executor_hash,
                suite_hash=suite.suite_hash,
                sandbox_ok=False,
                sandbox_output="",
                approval=None,
                verdict="rejected",
                reason="suite author identity collision / unrecorded basis",
                decided_at=decided_at,
            )
            self._journal("gate.identity-collision", candidate, receipt)
            return receipt

        # Path: spec hash mismatch -> rejected without sandbox
        if candidate.spec.spec_hash != suite.spec_hash:
            receipt = CrossValidationReceipt(
                receipt_id="",
                spec_hash=candidate.spec.spec_hash,
                executor_hash=candidate.executor_hash,
                suite_hash=suite.suite_hash,
                sandbox_ok=False,
                sandbox_output="",
                approval=None,
                verdict="rejected",
                reason="suite/spec hash mismatch",
                decided_at=decided_at,
            )
            self._journal("gate.spec-mismatch", candidate, receipt)
            return receipt

        # F2-5: unconditional stale spec check - entry missing/unreadable/hash-mismatch
        # -> rejected receipt, no sandbox, regardless of any active record.
        # F2-15: reuse registry-bound store instead of constructing a new PackStore.
        try:
            store = _store_via_registry(self.registry)
            current_entry = store.get(entry_id)
            current_hash = canonical_procedure_hash(current_entry.cand.procedure)

            if candidate.spec.spec_hash != current_hash:
                receipt = CrossValidationReceipt(
                    receipt_id="",
                    spec_hash=candidate.spec.spec_hash,
                    executor_hash=candidate.executor_hash,
                    suite_hash=suite.suite_hash,
                    sandbox_ok=False,
                    sandbox_output="",
                    approval=None,
                    verdict="rejected",
                    reason="stale spec - entry procedure has changed",
                    decided_at=decided_at,
                )
                self._journal("gate.stale-spec", candidate, receipt)
                return receipt
        except Exception as e:
            # F2-4: unverifiable entry -> rejected, no sandbox
            receipt = CrossValidationReceipt(
                receipt_id="",
                spec_hash=candidate.spec.spec_hash,
                executor_hash=candidate.executor_hash,
                suite_hash=suite.suite_hash,
                sandbox_ok=False,
                sandbox_output="",
                approval=None,
                verdict="rejected",
                reason=f"spec unverifiable: {e}",
                decided_at=decided_at,
            )
            self._journal("gate.spec-unverifiable", candidate, receipt)
            return receipt

        # Check for existing active executor (for swap detection)
        active = self.registry.active_for(entry_id)

        # Write candidate first (adds quarantined record atomically)
        self.registry.write_candidate(candidate)

        # Find existing quarantined record for this executor
        existing_records = self.registry.record_for(entry_id, status="quarantined")
        record = None
        for r in existing_records:
            if r.executor_hash == candidate.executor_hash:
                record = r
                break

        if record is None:
            # Unregistered candidate -> rejected
            receipt = CrossValidationReceipt(
                receipt_id="",
                spec_hash=candidate.spec.spec_hash,
                executor_hash=candidate.executor_hash,
                suite_hash=suite.suite_hash,
                sandbox_ok=False,
                sandbox_output="",
                approval=None,
                verdict="rejected",
                reason="unregistered candidate",
                decided_at=decided_at,
            )
            self._journal("gate.unregistered-candidate", candidate, receipt)
            return receipt

        # Sandbox execution
        sandbox_result = self.execution.run_check({
            "kind": "workflow-cross-validation",
            "executor_source": candidate.source,
            "test_source": suite.test_source,
            "executor_hash": candidate.executor_hash,
            "suite_hash": suite.suite_hash,
        })

        if not sandbox_result.ok:
            # F2-3: construct receipt FIRST, then transition with its receipt_id
            receipt = CrossValidationReceipt(
                receipt_id="",
                spec_hash=candidate.spec.spec_hash,
                executor_hash=candidate.executor_hash,
                suite_hash=suite.suite_hash,
                sandbox_ok=False,
                sandbox_output=sandbox_result.output[:2000],
                approval=None,
                verdict="rejected",
                reason=f"sandbox failure: {sandbox_result.output[:200]}",
                decided_at=decided_at,
            )
            # Use receipt.receipt_id for the transition
            self.registry.transition(
                record, "rejected", receipt_id=receipt.receipt_id, updated_at=decided_at
            )
            self._journal("gate.sandbox-fail", candidate, receipt)
            return receipt

        # Path: sandbox ok + no approval -> refused (stays quarantined)
        if approval is None:
            receipt = CrossValidationReceipt(
                receipt_id="",
                spec_hash=candidate.spec.spec_hash,
                executor_hash=candidate.executor_hash,
                suite_hash=suite.suite_hash,
                sandbox_ok=True,
                sandbox_output=sandbox_result.output[:2000],
                approval=None,
                verdict="refused",
                reason="sandbox pass awaits strict-mode approval",
                decided_at=decided_at,
            )
            # F2-14: journal refusal with run's injected now, not self.clock()
            self._journal_refusal(candidate, receipt, decided_at)
            return receipt

        # Path: sandbox ok + approval -> activated
        # F2-2: swap requires baseline; check before any transitions
        if active:
            baseline_path = (
                self.registry.store_root / candidate.spec.pack
                / "evals" / "baseline.json"
            )
            if not baseline_path.exists():
                receipt = CrossValidationReceipt(
                    receipt_id="",
                    spec_hash=candidate.spec.spec_hash,
                    executor_hash=candidate.executor_hash,
                    suite_hash=suite.suite_hash,
                    sandbox_ok=True,
                    sandbox_output=sandbox_result.output[:2000],
                    approval=approval,
                    verdict="rejected",
                    reason=f"Executor swap for entry {entry_id} requires baseline.json",
                    decided_at=decided_at,
                )
                self._journal("gate.swap-no-baseline", candidate, receipt)
                raise GateError(
                    f"Executor swap for entry {entry_id} requires "
                    f"baseline.json in pack {candidate.spec.pack} (regression gate)")

        # F2-3: construct CrossValidationReceipt FIRST (receipt_id='' -> self-hashes)
        receipt = CrossValidationReceipt(
            receipt_id="",
            spec_hash=candidate.spec.spec_hash,
            executor_hash=candidate.executor_hash,
            suite_hash=suite.suite_hash,
            sandbox_ok=True,
            sandbox_output=sandbox_result.output[:2000],
            approval=approval,
            verdict="activated",
            reason="sandbox pass with strict-mode approval",
            decided_at=decided_at,
        )

        # F2-2: activate new FIRST (zero-active crash window eliminated)
        self.registry.transition(
            record, "active", receipt_id=receipt.receipt_id, updated_at=decided_at
        )

        # F2-2: THEN supersede old (using SAME receipt id per F2-3)
        if active:
            self.registry.transition(
                active, "superseded", receipt_id=receipt.receipt_id, updated_at=decided_at
            )

        # F2-3: journal the FULL receipt (all fields)
        self._journal("gate.activated", candidate, receipt)
        return receipt

    def _journal(self, kind: str, candidate: ExecutorCandidate,
                  receipt: CrossValidationReceipt) -> None:
        """Journal a gate event to provenance with full receipt."""
        self.provenance.append({
            "kind": kind,
            "entry_id": candidate.spec.entry_id,
            "spec_hash": candidate.spec.spec_hash,
            "actor": "cross-validation-gate",
            "receipt_id": receipt.receipt_id,
            "verdict": receipt.verdict,
            "reason": receipt.reason,
            "timestamp": self.clock().isoformat(),
            # F2-3: journal the FULL receipt
            "receipt": {
                "spec_hash": receipt.spec_hash,
                "executor_hash": receipt.executor_hash,
                "suite_hash": receipt.suite_hash,
                "sandbox_ok": receipt.sandbox_ok,
                "sandbox_output": receipt.sandbox_output,
                "approval": {
                    "approver": receipt.approval.approver,
                    "basis": receipt.approval.basis,
                    "strict_mode": receipt.approval.strict_mode,
                    "approved_at": receipt.approval.approved_at,
                } if receipt.approval else None,
                "verdict": receipt.verdict,
                "reason": receipt.reason,
                "decided_at": receipt.decided_at,
            },
        })

    def _journal_refusal(self, candidate: ExecutorCandidate,
                         receipt: CrossValidationReceipt, now: str) -> None:
        """F2-14: journal refusal with the run's injected now, not self.clock()."""
        self.provenance.append({
            "kind": "gate.awaiting-approval",
            "entry_id": candidate.spec.entry_id,
            "spec_hash": candidate.spec.spec_hash,
            "actor": "cross-validation-gate",
            "receipt_id": receipt.receipt_id,
            "verdict": receipt.verdict,
            "reason": receipt.reason,
            "timestamp": now,  # F2-14: use injected now, not self.clock()
            "receipt": {
                "spec_hash": receipt.spec_hash,
                "executor_hash": receipt.executor_hash,
                "suite_hash": receipt.suite_hash,
                "sandbox_ok": receipt.sandbox_ok,
                "verdict": receipt.verdict,
                "reason": receipt.reason,
                "decided_at": receipt.decided_at,
            },
        })
