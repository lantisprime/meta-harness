"""Executor runtime: runs compiled workflow executors in a restricted sandbox.

This module provides the runtime environment for executing generated executors
with a strict whitelist of builtins.
"""
from __future__ import annotations

import ast
import builtins
import json

# Whitelist of safe builtins per D3 — NO eval exec compile open input __import__
# globals locals vars setattr delattr breakpoint exit quit help memoryview.
SAFE_BUILTINS = {
    # Types
    "bool", "int", "float", "str", "bytes", "list", "dict", "tuple", "set",
    "frozenset", "type", "range", "slice", "complex", "object",
    # Exceptions
    "BaseException", "Exception", "StopIteration", "StopAsyncIteration",
    "ArithmeticError", "LookupError", "ValueError", "TypeError", "KeyError",
    "OSError", "RuntimeError", "SyntaxError", "IndentationError", "IndexError",
    # Constants
    "True", "False", "None",
    # Functional helpers
    "abs", "all", "any", "bin", "callable", "chr", "divmod", "enumerate",
    "filter", "hash", "hex", "isinstance",
    "issubclass", "iter", "len", "map", "max", "min", "next", "oct", "ord",
    "pow", "repr", "reversed", "round", "sorted", "staticmethod", "sum",
    "super", "tuple", "zip", "__build_class__",
    # NOTE: `id` intentionally omitted (F2-16) — leaks memory addresses.
    # NOTE: `getattr`/`hasattr` intentionally omitted (F3-3) — they can be
    # used to access dunder attributes by string (e.g. getattr(obj, '__bases__')).
}


def _ast_preflight(source: str) -> None:
    """Reject sources containing dunder escapes or __builtins__ access.

    F2-16: generated executors are machine-templated from escaped contract
    data and never need dunder attributes (other than __init__) or
    __builtins__.  This blocks the ().__class__.__bases__[0].__subclasses__()
    escape class before exec.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise RuntimeCompError(f"executor source is not valid Python: {exc}") from exc

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            # Only __init__ is allowed; all other dunders are forbidden.
            if node.attr.startswith("__") and node.attr != "__init__":
                raise RuntimeCompError(
                    f"executor source contains forbidden dunder access: {node.attr}"
                )
        if isinstance(node, ast.Name) and node.id == "__builtins__":
            raise RuntimeCompError(
                "executor source contains forbidden __builtins__ reference"
            )


def _make_restricted_globals(extra_globals: dict | None = None) -> dict:
    """Create a restricted globals dict for sandboxed exec.

    The whitelist is D3-minimal: generated executors need none of the removed
    dangerous entries (eval, exec, compile, open, input, __import__, globals,
    locals, vars, setattr, delattr, breakpoint, exit, quit, help, memoryview).
    json is injected directly into the globals (not builtins) so generated
    code never needs to import it and __import__ is never exposed.
    """
    import json as _json  # injected; generated code uses json directly
    safe = {name: getattr(builtins, name) for name in SAFE_BUILTINS
            if hasattr(builtins, name)}

    # Inject json directly into globals — generated code uses it without import
    # This avoids needing __import__ in the builtins whitelist.
    result = {"__builtins__": safe, "json": _json}

    # Add required Python names for module execution
    result["__name__"] = "__main__"
    result["__doc__"] = None

    # Add any extra globals
    if extra_globals:
        result.update(extra_globals)

    return result


# Runtime errors
class RuntimeCompError(RuntimeError):
    """Runtime compilation error - named to avoid collision with builtin."""
    pass


from dataclasses import dataclass
from typing import Any, Callable

from selflearn.compilation.models import (
    ExecutorRecord,
    canonical_procedure_hash,
)
from selflearn.compilation.registry import ExecutorRegistry
from selflearn.contracts import TaskOutcome
from selflearn.ports import ProvenancePort
from selflearn.store.packstore import PackStore


@dataclass(frozen=True)
class RunResult:
    """Result of running an executor.

    status vocabulary:
    - completed: all steps finished and checks passed
    - failed: a step check failed (produces a fail TaskOutcome)
    - awaiting_approval: blocked at an approval step (no outcome produced)
    - error: host handler failure or runtime precondition refusal
             (entry not implicated; outcomes = ())
    """
    status: str  # "completed" | "failed" | "awaiting_approval" | "error"
    completed_steps: tuple[str, ...]
    outcomes: tuple[TaskOutcome, ...]
    at_step: str = ""  # For awaiting_approval status


class ExecutorRuntime:
    """Runtime for executing compiled workflow executors."""

    def __init__(self, registry: ExecutorRegistry, store: PackStore,
                 provenance: ProvenancePort, clock: callable):
        self.registry = registry
        self.store = store
        self.provenance = provenance
        self.clock = clock

    def run(self, entry_id: str, *, task_id: str, topic: str,
            task_type: str, step_handler: Callable, now: str) -> RunResult:
        """Run an active executor for an entry.

        Args:
            entry_id: The entry to run
            task_id: The task ID for outcomes
            topic: The topic for outcomes
            task_type: The task type
            step_handler: Callable(step_id, step_data) -> dict
            now: ISO timestamp

        Returns:
            RunResult with status, completed steps, and outcomes

        Raises:
            RuntimeCompError: If no active executor or drift detected
        """
        # Activation enforcement: must have ACTIVE record
        active = self.registry.active_for(entry_id)
        if active is None:
            self._journal_refusal(entry_id, "executor.no-active",
                                 f"no active executor for {entry_id}",
                                 now=now)
            raise RuntimeCompError(
                f"No active executor for entry {entry_id}")

        # Drift check: spec_hash must match current procedure
        # F4-4: guard store access / procedure recompute so a missing or
        # unreadable entry is treated as an unverifiable executor.
        try:
            entry = self.store.get(entry_id)
            current_hash = canonical_procedure_hash(entry.cand.procedure)
        except Exception as e:
            self._journal_refusal(entry_id, "executor.unverifiable",
                                 f"cannot verify current spec for {entry_id}: {e}",
                                 now=now)
            raise RuntimeCompError(
                f"Executor for {entry_id} is unverifiable: {e}")
        if active.spec_hash != current_hash:
            self._journal_refusal(entry_id, "executor.stale-spec",
                                 f"active spec {active.spec_hash} != current {current_hash}",
                                 now=now)
            raise RuntimeCompError(
                f"Executor for {entry_id} is stale (spec hash mismatch)")

        # Load and verify executor source
        from pathlib import Path
        exec_path = Path(self.store.root) / active.path

        # Bounded-authority check: a tampered registry.path cannot widen the
        # read surface outside the store root.
        #
        # Every operation that can fail sits inside a guard, and each failure
        # is journalled under the kind that honestly describes it: a
        # containment breach is `executor.path-escape`, while a path that
        # cannot be resolved or read is `executor.path-unreadable`. Collapsing
        # both into one kind would record a mere read failure as an escape
        # attempt, corrupting the evidence taxonomy for any consumer that
        # buckets by kind. The read comes from the same resolved path that was
        # validated, so there is no check/use split.
        try:
            store_root_resolved = Path(self.store.root).resolve()
            exec_path_resolved = exec_path.resolve()
            contained = exec_path_resolved.is_relative_to(store_root_resolved)
        except OSError as exc:
            self._journal_refusal(entry_id, "executor.path-unreadable",
                                 f"cannot resolve executor path {active.path!r}: {exc}",
                                 now=now)
            raise RuntimeCompError(
                f"Cannot validate executor path {active.path!r}") from exc

        if not contained:
            self._journal_refusal(entry_id, "executor.path-escape",
                                 f"executor path {active.path!r} escapes store root",
                                 now=now)
            raise RuntimeCompError(
                f"Executor path {active.path!r} escapes store root")

        # exists() must be guarded too. On CPython < 3.13 Path.exists() only
        # swallows ENOENT/ENOTDIR/EBADF/ELOOP and re-raises everything else --
        # notably EACCES/EPERM/EIO, reachable on a contained path whose final
        # component is stat-denied, since resolve(strict=False) need not stat
        # the tail. This package supports >=3.10, so an unguarded call would
        # let a raw OSError escape run() unjournalled on 3.10-3.12 even though
        # 3.13+ (where exists() delegates to os.path.exists) swallows it.
        try:
            missing = not exec_path_resolved.exists()
        except OSError as exc:
            self._journal_refusal(entry_id, "executor.path-unreadable",
                                 f"cannot stat executor path {active.path!r}: {exc}",
                                 now=now)
            raise RuntimeCompError(
                f"Cannot read executor path {active.path!r}") from exc

        if missing:
            raise RuntimeCompError(f"Executor source missing: {active.path}")

        # UnicodeDecodeError is a ValueError, not an OSError, so it needs
        # catching explicitly: a locale-drifted or corrupted executor file
        # would otherwise escape run() raw and unjournalled -- the same class
        # of breach as the resolve() gap, in a different exception family.
        try:
            source = exec_path_resolved.read_text()
        except (OSError, UnicodeDecodeError) as exc:
            self._journal_refusal(entry_id, "executor.path-unreadable",
                                 f"cannot read executor path {active.path!r}: {exc}",
                                 now=now)
            raise RuntimeCompError(
                f"Cannot read executor path {active.path!r}") from exc

        import hashlib
        actual_hash = hashlib.sha256(source.encode()).hexdigest()
        if actual_hash != active.executor_hash:
            self._journal_refusal(entry_id, "executor.tampered",
                                 f"hash mismatch: {actual_hash} != {active.executor_hash}",
                                 now=now)
            raise RuntimeCompError(
                f"Executor for {entry_id} has been tampered with")

        # F2-16: AST preflight before exec
        _ast_preflight(source)

        # Journal run start
        self.provenance.append({
            "kind": "runtime.start",
            "entry_id": entry_id,
            "spec_hash": active.spec_hash,
            "actor": "executor-runtime",
            "timestamp": now,
        })

        # Execute in restricted sandbox
        globals_ns = _make_restricted_globals()

        # F4-3: exec and class resolution are isolated from the run step.
        # Any failure before the executor is fully loaded normalizes to
        # RuntimeCompError and emits a paired finish/refusal event.
        try:
            exec(source, globals_ns)
            ApprovalRequired = globals_ns.get("ApprovalRequired")
            if ApprovalRequired is None or not isinstance(ApprovalRequired, type) \
                    or not issubclass(ApprovalRequired, Exception):
                raise RuntimeCompError("invalid executor: ApprovalRequired not found or not an Exception class")
            StepCheckFailed = globals_ns.get("StepCheckFailed")
            if StepCheckFailed is None or not isinstance(StepCheckFailed, type) \
                    or not issubclass(StepCheckFailed, Exception):
                raise RuntimeCompError("invalid executor: StepCheckFailed not found or not an Exception class")
            run_fn = globals_ns["run"]
        except RuntimeCompError as exc:
            self.provenance.append({
                "kind": "runtime.executor-error",
                "entry_id": entry_id,
                "spec_hash": active.spec_hash,
                "reason": f"executor load or validation failed: {exc}",
                "actor": "executor-runtime",
                "timestamp": now,
            })
            self.provenance.append({
                "kind": "runtime.finish",
                "entry_id": entry_id,
                "spec_hash": active.spec_hash,
                "status": "error",
                "actor": "executor-runtime",
                "timestamp": now,
            })
            raise
        except Exception as e:
            self.provenance.append({
                "kind": "runtime.executor-error",
                "entry_id": entry_id,
                "spec_hash": active.spec_hash,
                "reason": f"executor load failed: {e}",
                "actor": "executor-runtime",
                "timestamp": now,
            })
            self.provenance.append({
                "kind": "runtime.finish",
                "entry_id": entry_id,
                "spec_hash": active.spec_hash,
                "status": "error",
                "actor": "executor-runtime",
                "timestamp": now,
            })
            raise RuntimeCompError(
                f"Executor for {entry_id} failed to load: {e}") from e

        completed = []
        outcomes = ()
        at_step = ""
        status = "failed"

        try:
            # FIX-6: step_handler is passed directly; generated code uses
            # module-level _COMPLETED (survives exception) to record completed steps.
            result = run_fn(step_handler)
            completed = list(globals_ns.get("_COMPLETED", []))

            # Success outcome
            outcomes = (TaskOutcome(
                task_id=task_id,
                task_type=task_type,
                topic=topic,
                verdict="pass",
                injected=(entry_id,),
            ),)

            status = "completed"

        except ApprovalRequired as e:
            # FIX-2: ApprovalRequired is NOT a failure outcome
            # No TaskOutcome is produced; awaiting human approval produces no evidence
            # FIX-6: _COMPLETED survives exception; read it from globals_ns
            at_step = getattr(e, "step_id", "unknown")
            completed = list(globals_ns.get("_COMPLETED", []))
            outcomes = ()
            status = "awaiting_approval"

            # Journal approval stop
            self.provenance.append({
                "kind": "runtime.approval-stop",
                "entry_id": entry_id,
                "spec_hash": active.spec_hash,
                "at_step": at_step,
                "actor": "executor-runtime",
                "timestamp": now,
            })

        except StepCheckFailed as e:
            failing_step = getattr(e, "step_id", "unknown")
            completed = list(globals_ns.get("_COMPLETED", []))
            outcomes = (TaskOutcome(
                task_id=task_id,
                task_type=task_type,
                topic=topic,
                verdict="fail",
                injected=(entry_id,),
                implicated=(entry_id,),
                step_id=failing_step,
                failure_mode="executor-step-check",
            ),)
            status = "failed"

        except Exception as e:
            # F2-6: a step_handler exception is a host failure, not entry evidence.
            outcomes = ()
            status = "error"
            self.provenance.append({
                "kind": "runtime.handler-error",
                "entry_id": entry_id,
                "spec_hash": active.spec_hash,
                "reason": str(e),
                "actor": "executor-runtime",
                "timestamp": now,
            })

        # Journal run finish
        self.provenance.append({
            "kind": "runtime.finish",
            "entry_id": entry_id,
            "spec_hash": active.spec_hash,
            "status": status,
            "actor": "executor-runtime",
            "timestamp": now,
        })

        return RunResult(
            status=status,
            completed_steps=tuple(completed),
            outcomes=outcomes,
            at_step=at_step,
        )

    def _journal_refusal(self, entry_id: str, kind: str, reason: str, *, now: str) -> None:
        """Journal a runtime refusal using the run's injected clock."""
        self.provenance.append({
            "kind": kind,
            "entry_id": entry_id,
            "actor": "executor-runtime",
            "reason": reason,
            "timestamp": now,
        })
