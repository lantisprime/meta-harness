"""Verification & evals module: reputability/corroboration, citations,
skill-check execution, judge fallback (M4) + evalgen, second-model probe
validation, the eval gate, suite runner, and model qualification (M5)."""
from selflearn.verification.evalgen import EvalGen, EvalGenError, EvalGenReport
from selflearn.verification.suite import (
    BOOTSTRAP_MIN_SUITE,
    QualificationResult,
    SuiteResult,
    eval_gated_decision,
    qualify_model,
    run_pack_suite,
    run_probe,
)
from selflearn.verification.verifier import (
    CorroborationRule,
    VerificationError,
    VerificationReport,
    Verifier,
)

__all__ = ["CorroborationRule", "VerificationError", "VerificationReport",
           "Verifier", "EvalGen", "EvalGenError", "EvalGenReport",
           "BOOTSTRAP_MIN_SUITE", "QualificationResult", "SuiteResult",
           "eval_gated_decision", "qualify_model", "run_pack_suite",
           "run_probe"]
