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
from selflearn.ports import ExecutionPort, ProvenancePort

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

        FIX-3 wires the registry state machine:
        - write_candidate atomically adds quarantined record
        - evaluate REQUIRES existing quarantined record
        - verdict "rejected" -> transition to "rejected"
        - verdict "activated" -> supersede old active + activate new

        FIX-4 stale-spec check fails closed:
        - entry missing/unreadable/hash-uncomputable -> rejected, no sandbox
        - Only checked when there's an active executor (not on initial activation)

        Args:
            candidate: The compiled executor candidate
            suite: The independent test suite
            approval: Optional approval record (strict_mode required for activation)
            decided_at: ISO timestamp of the decision

        Returns:
            CrossValidationReceipt with verdict and reasoning

        Raises:
            GateError: If execution port is unbound
        """
        entry_id = candidate.spec.entry_id

        # FIX-3: Check for existing active executor (for stale-spec check)
        active = self.registry.active_for(entry_id)
        
        # FIX-4: Only check stale spec if there's an active executor
        if active:
            try:
                # Try to read the entry to verify it's still valid
                from selflearn.store.packstore import PackStore
                store = PackStore(self.registry.store_root)
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
                # FIX-4: unverifiable entry -> rejected, no sandbox run
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

        # Path 1: No execution port -> loud refusal
        if self.execution is None:
            raise GateError(
                "CrossValidationGate requires ExecutionPort; refusing to run "
                "without sandbox (verifier.py:150-154 convention)")

        # Path 2: spec hash mismatch -> rejected without sandbox
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

        # FIX-3: write candidate first (adds quarantined record atomically)
        self.registry.write_candidate(candidate)

        # FIX-3: find existing quarantined record for this executor
        existing_records = self.registry.record_for(entry_id, status="quarantined")
        record = None
        for r in existing_records:
            if r.executor_hash == candidate.executor_hash:
                record = r
                break

        if record is None:
            # FIX-3: should not happen since we just wrote it
            # but handle gracefully
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

        # Path 3: sandbox execution
        sandbox_result = self.execution.run_check({
            "kind": "workflow-cross-validation",
            "executor_source": candidate.source,
            "test_source": suite.test_source,
            "executor_hash": candidate.executor_hash,
            "suite_hash": suite.suite_hash,
        })

        if not sandbox_result.ok:
            # FIX-3: sandbox fail -> rejected
            receipt_id = f"rejected:{candidate.executor_hash[:16]}"
            self.registry.transition(
                record, "rejected", receipt_id=receipt_id, updated_at=decided_at
            )
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
            self._journal("gate.sandbox-fail", candidate, receipt)
            return receipt

        # Path 4: sandbox ok + no approval -> refused (stays quarantined)
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
            self._journal("gate.awaiting-approval", candidate, receipt)
            return receipt

        # Path 5: sandbox ok + approval -> activated
        # FIX-3: swap requires baseline; supersede old active BEFORE activating new
        if active:
            baseline_path = (
                self.registry.store_root / candidate.spec.pack
                / "evals" / "baseline.json"
            )
            if not baseline_path.exists():
                raise GateError(
                    f"Executor swap for entry {entry_id} requires "
                    f"baseline.json in pack {candidate.spec.pack} (regression gate)")
            # FIX-3: supersede old active
            old_receipt_id = f"superseded:{active.executor_hash[:16]}"
            self.registry.transition(
                active, "superseded", receipt_id=old_receipt_id, updated_at=decided_at
            )

        # FIX-3: activate new record
        receipt_id = f"activated:{candidate.executor_hash[:16]}"
        self.registry.transition(
            record, "active", receipt_id=receipt_id, updated_at=decided_at
        )

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

        self._journal("gate.activated", candidate, receipt)
        return receipt

    def _journal(self, kind: str, candidate: ExecutorCandidate,
                  receipt: CrossValidationReceipt) -> None:
        """Journal a gate event to provenance."""
        self.provenance.append({
            "kind": kind,
            "entry_id": candidate.spec.entry_id,
            "spec_hash": candidate.spec.spec_hash,
            "actor": "cross-validation-gate",
            "receipt_id": receipt.receipt_id,
            "verdict": receipt.verdict,
            "reason": receipt.reason,
            "timestamp": self.clock().isoformat(),
        })
