"""Shared helpers for META-5 adversarial context tests.

Not a test module itself (no test_ prefix): builds default-but-overridable
context contract objects and loads the machine-readable case corpus so the
four test_context_*.py / test_memory_skill_boundaries.py modules can stay
declarative.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from metaharness.context import (
    ContextScope,
    ContextSection,
    ContextSectionType,
    ContextSourceKind,
    ContextSourceRef,
    ContextTrust,
    Sensitivity,
    CompressionAction,
)

CORPUS_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "meta5" / "corpus.json"


def load_corpus() -> dict[str, Any]:
    return json.loads(CORPUS_PATH.read_text())


def cases_for(suite: str, category: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
    cases = [case for case in load_corpus()["cases"] if case["suite"] == suite]
    if category is not None:
        cases = [case for case in cases if case["category"] == category]
    if status is not None:
        cases = [case for case in cases if case["status"] == status]
    return cases


def make_scope(**changes: Any) -> ContextScope:
    values: dict[str, Any] = {"project_id": "meta-harness"}
    values.update(changes)
    return ContextScope(**values)


def make_source(**changes: Any) -> ContextSourceRef:
    values: dict[str, Any] = {
        "source_id": "repo-instructions",
        "kind": ContextSourceKind.PROTECTED_INSTRUCTIONS,
        "scope": make_scope(),
        "trust": ContextTrust.INSTRUCTION,
        "content_hash": "sha256:" + "1" * 64,
        "selection_reason": "required repository contract",
        "sensitivity": Sensitivity.PUBLIC,
        "fetchable": False,
    }
    values.update(changes)
    return ContextSourceRef(**values)


def make_section(**changes: Any) -> ContextSection:
    source = changes.pop("source", make_source())
    values: dict[str, Any] = {
        "section_type": ContextSectionType.SYSTEM_INSTRUCTIONS,
        "stable_id": "system-contract",
        "source": source,
        "source_hash": source.content_hash,
        "trust": source.trust,
        "content": "You are a bounded worker.",
        "original_tokens": 7,
        "selected_tokens": 7,
        "compressed_tokens": 7,
        "budget_tokens": 100,
        "ordering_priority": 0,
        "sensitivity": source.sensitivity,
        "compression_action": CompressionAction.NONE,
    }
    values.update(changes)
    return ContextSection(**values)
