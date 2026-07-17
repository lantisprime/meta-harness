"""meta-harness adapter for the standalone ``selflearn`` library (M1 stub).

This package is the ONLY place harness and library meet
(docs/self-learning-specialist-agents-plan.md, decision 11). As milestones
land it will bind the five selflearn ports to harness machinery:

- ``ModelPort``      -> runner layer / tier router          (M3+)
- ``EmbeddingPort``  -> ``OpenAICompatEmbedding`` (adapter.py, M2: any
  OpenAI-compatible /v1/embeddings endpoint)
- ``ExecutionPort``  -> evals/execution.py sandbox          (M4)
- ``ProvenancePort`` -> hash-chained provenance log         (M4)
- ``IdentityPort``   -> Ed25519 worker identities           (M5)

M2 surface: ``AgentConfig.knowledge_packs``, ``make_knowledge_hints``
(a ``playbook_hints``-shaped advice callable for TaskExecutor), and the
embedding binding.
"""
from __future__ import annotations

from pathlib import Path

DEFAULT_KNOWLEDGE_ROOT = Path.home() / ".metaharness" / "knowledge"


class SelflearnUnavailable(RuntimeError):
    """selflearn is not installed in this environment."""


def require_selflearn():
    """Import and return the selflearn package, or fail loudly with the fix."""
    try:
        import selflearn
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise SelflearnUnavailable(
            "the selflearn library is not installed; run "
            "`pip install -e ./selflearn` from the repository root") from exc
    return selflearn


def open_store(root: Path | None = None):
    """Open the harness's knowledge store at the default location."""
    selflearn = require_selflearn()
    return selflearn.PackStore(root or DEFAULT_KNOWLEDGE_ROOT)


from metaharness.knowledge.adapter import (  # noqa: E402
    KNOWLEDGE_ARCHETYPES,
    OpenAICompatEmbedding,
    make_knowledge_hints,
)
from metaharness.knowledge.planning import plan_from_knowledge  # noqa: E402
from metaharness.knowledge.tools import knowledge_tools  # noqa: E402

__all__ = ["DEFAULT_KNOWLEDGE_ROOT", "SelflearnUnavailable", "require_selflearn",
           "open_store", "KNOWLEDGE_ARCHETYPES", "OpenAICompatEmbedding",
           "make_knowledge_hints", "knowledge_tools", "plan_from_knowledge"]
