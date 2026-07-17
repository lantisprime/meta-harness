"""M2 knowledge adapter: AgentConfig binding + knowledge hints callable +
embedding port binding. Exercises the real selflearn library end-to-end."""
import hashlib
import math
import re

import pytest

from metaharness.config import AgentConfig
from metaharness.core.types import Task, TaskType
from metaharness.knowledge import make_knowledge_hints
from metaharness.knowledge.adapter import OpenAICompatEmbedding

selflearn = pytest.importorskip("selflearn")
from selflearn import PackStore, SpecialistSpec  # noqa: E402
from selflearn.contracts import CandidateEntry, EntrySource, PublishDecision  # noqa: E402


class HashEmbedder:
    embedder_id = "hash-v1"

    def embed(self, texts):
        out = []
        for t in texts:
            v = [0.0] * 64
            for tok in re.findall(r"[a-z0-9]{3,}", t.lower()):
                v[int(hashlib.md5(tok.encode()).hexdigest(), 16) % 64] += 1.0
            n = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append(tuple(x / n for x in v))
        return out


SRC = EntrySource(url="https://docs.example.org/x", fetched_at="t",
                  sha256="0" * 64, tier="official")


@pytest.fixture()
def store(tmp_path):
    s = PackStore(tmp_path / "knowledge")
    e = CandidateEntry(id="kn-fastapi-lifespan", pack="fastapi", kind="knowledge",
                       body="FastAPI lifespan context manager replaces on_event "
                            "startup shutdown handlers.",
                       claims=("lifespan replaces on_event",), sources=(SRC,),
                       topic="lifespan")
    s.add_candidate(e)
    s.publish(e.id, PublishDecision(entry_id=e.id, publish=True, basis=("t",),
                                    identity_basis="m"))
    return s


def test_agent_config_accepts_knowledge_packs():
    cfg = AgentConfig(worker_id="w1", knowledge_packs=["fastapi", "security"])
    assert cfg.knowledge_packs == ["fastapi", "security"]
    assert AgentConfig(worker_id="w2").knowledge_packs == []   # default empty


def test_knowledge_hints_injects_for_served_task(store):
    spec = SpecialistSpec(name="fastapi-dev", packs=("fastapi",),
                          task_types=("code_edit",))
    hints = make_knowledge_hints(store, [spec], embedder=HashEmbedder())
    task = Task(task_type=TaskType.CODE_EDIT,
                objective="add startup lifecycle handling with lifespan")
    advice = hints(task)
    assert len(advice) == 1
    assert "kn-fastapi-lifespan" in advice[0]
    assert "field notes" in advice[0]


def test_knowledge_hints_empty_when_no_spec_serves(store):
    spec = SpecialistSpec(name="fastapi-dev", packs=("fastapi",),
                          task_types=("code_edit",))
    hints = make_knowledge_hints(store, [spec], embedder=HashEmbedder())
    assert hints(Task(task_type=TaskType.SUMMARIZE, objective="lifespan")) == []


def test_knowledge_hints_flow_into_executor_advice(store):
    """The callable is plug-compatible with TaskExecutor.playbook_hints."""
    from metaharness.core.executor import TaskExecutor

    spec = SpecialistSpec(name="fastapi-dev", packs=("fastapi",))
    hints = make_knowledge_hints(store, [spec], embedder=HashEmbedder())
    task = Task(task_type=TaskType.CODE_EDIT,
                objective="use lifespan for startup shutdown")
    advice = hints(task)
    executor = TaskExecutor.__new__(TaskExecutor)   # advice-merge unit only
    merged = executor._attempt_task(task, advice)
    boundaries = " ".join(merged.boundaries)
    assert "kn-fastapi-lifespan" in boundaries


def test_embedding_binding_validates_config():
    with pytest.raises(ValueError, match="base_url and model"):
        OpenAICompatEmbedding(base_url="", model="")
    emb = OpenAICompatEmbedding(base_url="http://127.0.0.1:1234/v1",
                                model="nomic-embed-text")
    assert emb.embedder_id == "openai-compat:nomic-embed-text"
