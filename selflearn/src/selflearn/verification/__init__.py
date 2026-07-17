"""Verification module. M4 ships reputability/corroboration, citations,
skill-check execution, and the judge fallback in strict mode; evalgen and
the eval gate land in M5."""
from selflearn.verification.verifier import (
    CorroborationRule,
    VerificationError,
    VerificationReport,
    Verifier,
)

__all__ = ["CorroborationRule", "VerificationError", "VerificationReport",
           "Verifier"]
