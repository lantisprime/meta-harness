"""Self-correction: ACE playbook, grounded reflection, MAST clustering, two-speed loop."""
from metaharness.correction.learning import CURATION_TEMPLATES, LearningLoop
from metaharness.correction.mast import FailureStats, classify_failure
from metaharness.correction.playbook import Playbook, PlaybookBullet
from metaharness.correction.reflexion import grounded_reflector

__all__ = [
    "Playbook", "PlaybookBullet",
    "grounded_reflector",
    "classify_failure", "FailureStats",
    "LearningLoop", "CURATION_TEMPLATES",
]
