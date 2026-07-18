"""The five host ports. Everything host-specific in selflearn goes through
these Protocols; everything else is self-contained plain files.

meta-harness binds: runner layer/tier router (ModelPort), its embedding
endpoint (EmbeddingPort), the evals/execution.py sandbox (ExecutionPort),
the hash-chained provenance log (ProvenancePort), Ed25519 worker identities
(IdentityPort). Standalone defaults ship here for the ports that have a
sane one: JSONL provenance and model-id identity (weaker — and it says so).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ModelPort(Protocol):
    """One completion call for every LLM step (distill, judge, evalgen, vision)."""

    model_id: str

    def complete(self, role: str, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
        """Return a structured result for the given worker role."""
        ...


@runtime_checkable
class EmbeddingPort(Protocol):
    """Vectors for semantic retrieval, keyed by embedder id.

    Vectors are never portable across embedder ids — the store flags every
    mismatched vector for re-index on swap.
    """

    embedder_id: str

    def embed(self, texts: list[str]) -> list[tuple[float, ...]]: ...


@dataclass(frozen=True)
class ExecutionResult:
    ok: bool
    output: str = ""


@runtime_checkable
class ExecutionPort(Protocol):
    """Sandboxed execution for skill ``check:`` blocks and executable probes.

    A host without a sandbox must leave this unbound; consumers refuse
    executable checks loudly instead of skipping them silently.
    """

    def run_check(self, check: dict[str, Any]) -> ExecutionResult: ...


@runtime_checkable
class ProvenancePort(Protocol):
    """Append-only event sink."""

    def append(self, event: dict[str, Any]) -> None: ...


@runtime_checkable
class IdentityPort(Protocol):
    """Answers 'are these two workers distinct?' for probe author/validator
    separation. ``basis`` is recorded on every publish decision so a weaker
    standalone basis is visible in the audit trail."""

    basis: str

    def distinct(self, worker_a: Any, worker_b: Any) -> bool: ...


# ---------------------------------------------------------------------------
# Standalone defaults
# ---------------------------------------------------------------------------

class JsonlProvenance:
    """Standalone ProvenancePort: append-only local JSONL file."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, event: dict[str, Any]) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, sort_keys=True) + "\n")


class OpenAICompatEmbedding:
    """EmbeddingPort over any OpenAI-compatible /embeddings endpoint.

    THE embedding client — hosts and the CLI share this one implementation
    (review finding: two divergent clients emitted the same embedder_id, so
    behavioral differences were invisible to reindex_needed)."""

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
        import urllib.request

        req = urllib.request.Request(
            f"{self.base_url}/embeddings",
            data=json.dumps({"model": self.model, "input": texts}).encode(),
            headers={"Content-Type": "application/json",
                     **({"Authorization": f"Bearer {self.api_key}"}
                        if self.api_key else {})})
        with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
            payload = json.loads(resp.read())
        data = sorted(payload["data"], key=lambda d: d["index"])
        if len(data) != len(texts):
            raise RuntimeError(
                f"embedding endpoint returned {len(data)} vectors for "
                f"{len(texts)} inputs")
        return [tuple(d["embedding"]) for d in data]


class ModelIdIdentity:
    """Standalone IdentityPort: compares model ids. Weaker than a signed
    worker identity, and the recorded basis says so."""

    basis = "model-id (standalone default; weaker than signed worker identity)"

    def distinct(self, worker_a: Any, worker_b: Any) -> bool:
        a = getattr(worker_a, "model_id", None)
        b = getattr(worker_b, "model_id", None)
        if a is None or b is None:
            raise ValueError("workers must expose model_id for the standalone "
                             "identity check")
        return a != b
