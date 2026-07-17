"""Distillation module: SourceDocuments -> schema-guarded CandidateEntries."""
from selflearn.distillation.distiller import (
    INJECTION_PATTERNS,
    DistillationError,
    Distiller,
    entries_from_specs,
    injection_screen,
)

__all__ = ["INJECTION_PATTERNS", "DistillationError", "Distiller",
           "entries_from_specs", "injection_screen"]
