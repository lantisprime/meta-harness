"""Verification and eval gating."""
from metaharness.evals.gate import (
    EvalTaskResult,
    GateReport,
    SuiteResult,
    TypeDelta,
    compare_suites,
    run_suite,
    sign_test_p,
)
from metaharness.evals.execution import (
    ExecutionCheck,
    discover_execution_check,
    verify_code_edit_execution,
)
from metaharness.evals.sdlc import sdlc_capability_suite, summarize_by_phase
from metaharness.evals.verifiers import authenticity_failure, verify_output
__all__ = [
    "verify_output", "authenticity_failure",
    "ExecutionCheck", "discover_execution_check", "verify_code_edit_execution",
    "run_suite", "SuiteResult", "EvalTaskResult",
    "compare_suites", "GateReport", "TypeDelta", "sign_test_p",
    "sdlc_capability_suite", "summarize_by_phase",
    "EvalAssertion", "EvalCase", "EvalCaseProposal", "EvalPolicy",
    "EvalSuiteContent", "EvalSuiteDraft", "EvalSuitePublic",
    "EvalSuiteVersion", "EvalToolBinding", "EvalSuiteStore",
    "EvalMetrics", "EvalAttemptResult", "EvalCaseResult", "EvaluationReport",
    "EvaluationReportRef",
    "TuningProposal", "EvaluationReportStore", "TuningProposalStore",
    "ExactSuiteEvaluator", "IsolatedCaseExecution", "SandboxedCaseRunner",
    "create_tuning_proposal", "apply_tuning_proposal_to_draft",
]


def __getattr__(name: str):
    """Lazy artifact exports avoid the core -> evals -> blueprints import cycle."""
    model_names = {
        "EvalAssertion", "EvalCase", "EvalCaseProposal", "EvalPolicy",
        "EvalSuiteContent", "EvalSuiteDraft", "EvalSuitePublic",
        "EvalSuiteVersion", "EvalToolBinding",
    }
    if name in model_names:
        from metaharness.evals import models

        return getattr(models, name)
    if name == "EvalSuiteStore":
        from metaharness.evals.store import EvalSuiteStore

        return EvalSuiteStore
    artifact_names = {
        "EvalMetrics", "EvalAttemptResult", "EvalCaseResult",
        "EvaluationReport", "EvaluationReportRef", "TuningProposal",
    }
    if name in artifact_names:
        from metaharness.evals import artifacts

        return getattr(artifacts, name)
    if name in {"EvaluationReportStore", "TuningProposalStore"}:
        from metaharness.evals import artifact_store

        return getattr(artifact_store, name)
    if name in {
        "ExactSuiteEvaluator", "IsolatedCaseExecution", "SandboxedCaseRunner",
    }:
        from metaharness.evals import evaluator

        return getattr(evaluator, name)
    if name in {"create_tuning_proposal", "apply_tuning_proposal_to_draft"}:
        from metaharness.evals import tuning

        return getattr(tuning, name)
    raise AttributeError(name)
