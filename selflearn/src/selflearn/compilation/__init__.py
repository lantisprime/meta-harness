"""Workflow compilation: compile learned workflow entries to executables.

This module provides:
- models: frozen dataclasses for specs, candidates, receipts
- compiler: deterministic workflow-to-Python compiler
- testgen: independent test author for cross-validation
- gate: cross-validation gate with sandbox execution
- registry: pack-local executor registry
- runtime: executor runtime for running compiled workflows
"""
from selflearn.compilation.models import (
    ApprovalRecord,
    ExecutorCandidate,
    ExecutorRecord,
    ExecutorSpec,
    EXECUTOR_STATUSES,
    IndependentTestSuite,
    canonical_json,
    canonical_procedure_hash,
    content_hash,
    CrossValidationReceipt,
)
from selflearn.compilation.compiler import (
    CompilerError,
    COMPILER_ID,
    WorkflowCompiler,
    is_approval_step,
)
from selflearn.compilation.runtime import (
    ExecutorRuntime,
    RuntimeCompError,
    RunResult,
    _make_restricted_globals,
)
from selflearn.compilation.testgen import (
    TestAuthorError,
    WorkflowTestAuthor,
    AUTHOR_ROLE,
)
from selflearn.compilation.gate import (
    CrossValidationGate,
    GateError,
)
from selflearn.compilation.registry import (
    ExecutorRegistry,
    RegistryError,
)

__all__ = [
    "ApprovalRecord",
    "ExecutorCandidate",
    "ExecutorRecord",
    "ExecutorSpec",
    "EXECUTOR_STATUSES",
    "IndependentTestSuite",
    "canonical_json",
    "canonical_procedure_hash",
    "content_hash",
    "CrossValidationReceipt",
    "CompilerError",
    "COMPILER_ID",
    "WorkflowCompiler",
    "is_approval_step",
    "RuntimeCompError",
    "_make_restricted_globals",
    "ExecutorRuntime",
    "RunResult",
    "TestAuthorError",
    "WorkflowTestAuthor",
    "AUTHOR_ROLE",
    "CrossValidationGate",
    "GateError",
    "ExecutorRegistry",
    "RegistryError",
]
