"""M2 adapter surface: bind selflearn retrieval into the harness advice flow.

``TaskExecutor`` already accepts a ``playbook_hints`` callable that feeds
advice strings into task boundaries. ``make_knowledge_hints`` builds the
knowledge-pack twin of that callable from declarative specialist specs, so
serve wiring is one extra constructor argument — no executor changes.

``OpenAICompatEmbedding`` binds selflearn's EmbeddingPort to any
OpenAI-compatible ``/v1/embeddings`` endpoint (LM Studio, Ollama, remote
providers) — the platform-agnostic embedder the plan requires. Retrieval
without it degrades loudly to keyword mode inside selflearn.
"""
from __future__ import annotations

from typing import Callable, Optional, Sequence

import httpx

from metaharness.core.types import Task


# Worker-agent archetype system prompts for the acquisition workflow roles
# (plan decision 9). Registered as AgentConfig.system_prompt presets; the
# router still decides which model serves each role.
KNOWLEDGE_ARCHETYPES: dict[str, str] = {
    "knowledge-scout": (
        "You are a knowledge scout. Turn a research goal into a syllabus: "
        "subtopics, the key questions each must answer, and a source-type "
        "strategy per subtopic (official docs, papers, lectures). Propose "
        "concrete source refs (URLs, arXiv ids, search queries). Scale "
        "effort to the stated budget. Output JSON only; never fetch "
        "anything yourself."),
    "knowledge-distiller": (
        "You are a knowledge distiller. Given source material, produce "
        "compact entries whose every claim is traceable to the sources — "
        "never add facts from memory. Prefer few high-value entries over "
        "many thin ones. Output JSON matching the requested schema; bodies "
        "under 400 words."),
}


class OpenAICompatEmbedding:
    """selflearn EmbeddingPort over an OpenAI-compatible embeddings endpoint."""

    def __init__(self, base_url: str, model: str, api_key: str = "",
                 timeout_s: float = 30.0):
        if not base_url or not model:
            raise ValueError("OpenAICompatEmbedding needs base_url and model")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout_s = timeout_s
        self.embedder_id = f"openai-compat:{model}"

    def embed(self, texts: list[str]) -> list[tuple[float, ...]]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        with httpx.Client(timeout=self.timeout_s) as client:
            resp = client.post(f"{self.base_url}/embeddings",
                               headers=headers,
                               json={"model": self.model, "input": texts})
            resp.raise_for_status()
            payload = resp.json()
        data = sorted(payload["data"], key=lambda d: d["index"])
        if len(data) != len(texts):
            raise RuntimeError(
                f"embedding endpoint returned {len(data)} vectors for "
                f"{len(texts)} inputs")
        return [tuple(d["embedding"]) for d in data]


def record_qualification(matrix, qualification, task_types=("general",)) -> None:
    """Record a selflearn QualificationResult as capability evidence.

    The matrix keys evidence by (model, task_type); a pack qualification
    lands as evidence for the task types the pack's specialists serve, so
    routing learns which models are worth handing that domain."""
    from metaharness.core.types import TaskType

    for task_type in task_types:
        matrix.record(qualification.model_id, TaskType(task_type),
                      qualification.qualified)


def make_knowledge_hints(
    store,
    specs: Sequence,
    embedder=None,
    index: bool = True,
) -> Callable[[Task], list[str]]:
    """Build a ``playbook_hints``-shaped callable from specialist specs.

    ``store`` is a selflearn PackStore and ``specs`` are selflearn
    SpecialistSpec objects (typed loosely here so this module imports even
    when selflearn is absent; construction requires it via the retriever).
    Each call retrieves per the first spec serving the task's type and
    renders one fenced injection block, or returns no advice.
    """
    from selflearn.retrieval import Retriever, render_injection_block

    retriever = Retriever(store, embedder)
    if index and embedder is not None:
        for spec in specs:
            for pack in spec.packs:
                retriever.index(pack)

    def hints(task: Task) -> list[str]:
        task_type = getattr(task.task_type, "value", str(task.task_type))
        spec = next((s for s in specs if s.serves(task_type)), None)
        if spec is None:
            return []
        results = retriever.retrieve(list(spec.packs), task.objective,
                                     k=spec.retrieval_k,
                                     budget_tokens=spec.retrieval_budget_tokens)
        block = render_injection_block(results)
        return [] if block.empty else [block.text]

    return hints
