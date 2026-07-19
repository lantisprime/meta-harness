"""Declarative specialist spec: packs + archetype + routing constraints.

A specialist is never a model binding (decision: platform agnosticism).
Which LLM serves the role is the host's routing decision at runtime; any
model that passes the packs' eval suites is eligible. The spec is a plain
YAML/JSON document so it survives host and model swaps unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from selflearn.contracts import ContractError
from selflearn.learning.improvement import (
    DomainReadinessReport,
    ImprovementPolicy,
    assess_domain_readiness,
)


@dataclass(frozen=True)
class SpecialistSpec:
    name: str
    packs: tuple[str, ...]
    archetype_prompt: str = ""
    description: str = ""
    task_types: tuple[str, ...] = ()      # which task types this specialist serves
    min_tier: str = ""                    # routing constraint, host vocabulary
    retrieval_k: int = 5
    retrieval_budget_tokens: int = 1200
    improvement_policy: ImprovementPolicy | None = None

    def __post_init__(self) -> None:
        if not self.name:
            raise ContractError("SpecialistSpec.name must be non-empty")
        if not self.packs:
            raise ContractError(
                f"specialist {self.name!r} must bind at least one pack")
        if self.retrieval_k <= 0 or self.retrieval_budget_tokens <= 0:
            raise ContractError("retrieval_k and retrieval_budget_tokens must "
                                "be positive")

    def serves(self, task_type: str) -> bool:
        return not self.task_types or task_type in self.task_types

    def assess_improvement(self, store) -> DomainReadinessReport:
        """Check, without mutation, whether bounded improvement may start."""
        return assess_domain_readiness(self, store)

    def to_dict(self) -> dict:
        data = {"name": self.name, "packs": list(self.packs),
                "archetype_prompt": self.archetype_prompt,
                "description": self.description,
                "task_types": list(self.task_types), "min_tier": self.min_tier,
                "retrieval_k": self.retrieval_k,
                "retrieval_budget_tokens": self.retrieval_budget_tokens}
        if self.improvement_policy is not None:
            data["improvement_policy"] = self.improvement_policy.to_dict()
        return data


def save_spec(spec: SpecialistSpec, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(spec.to_dict(), sort_keys=False,
                                   allow_unicode=True))


def load_spec(path: Path) -> SpecialistSpec:
    path = Path(path)
    if not path.exists():
        raise ContractError(f"specialist spec {path} does not exist")
    try:
        data = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise ContractError(f"corrupt specialist spec {path}: {exc}")
    if not isinstance(data, dict):
        raise ContractError(f"specialist spec {path} is not a mapping")
    return SpecialistSpec(
        name=data.get("name", ""),
        packs=tuple(data.get("packs", [])),
        archetype_prompt=data.get("archetype_prompt", ""),
        description=data.get("description", ""),
        task_types=tuple(data.get("task_types", [])),
        min_tier=data.get("min_tier", ""),
        retrieval_k=int(data.get("retrieval_k", 5)),
        retrieval_budget_tokens=int(data.get("retrieval_budget_tokens", 1200)),
        improvement_policy=(
            ImprovementPolicy.from_dict(data["improvement_policy"])
            if data.get("improvement_policy") is not None else None),
    )
