"""META-5: executable negative/positive tests for canonical determinism,
redaction, and reconstruction behavior currently enforced by
src/metaharness/context.

Covers: canonical_json/content_hash stability, repeat-call determinism of
fit_messages_with_receipt, deterministic section-budget allocation,
compression-receipt hash/action consistency, the content_hash XOR
high_water_mark identity rule (hw_confounding, enforced layer), redaction of
nested structures and secret-shaped keys, and manifest reconstruction
round-tripping.
"""
from __future__ import annotations

import copy
import json

import pytest
from pydantic import ValidationError

from metaharness.context import (
    CompressionAction,
    CompressionReceipt,
    ContextManifest,
    allocate_section_budgets,
    content_hash,
    fit_messages_with_receipt,
)
from metaharness.context.models import canonical_json
from metaharness.core.types import Tier

from tests.adversarial._meta5_support import cases_for, make_source


def test_canonical_json_is_key_order_independent_and_stable():
    a = {"z": 1, "a": {"y": 2, "x": [3, 2, 1]}}
    b = {"a": {"x": [3, 2, 1], "y": 2}, "z": 1}
    assert canonical_json(a) == canonical_json(b)
    assert content_hash(a) == content_hash(b)
    assert canonical_json(a) == canonical_json(json.loads(canonical_json(a)))


def test_content_hash_changes_with_any_semantic_difference():
    base = {"role": "user", "content": "hello"}
    mutated = {"role": "user", "content": "Hello"}
    assert content_hash(base) != content_hash(mutated)
    assert content_hash("hello") != content_hash({"content": "hello"})


def test_fit_receipt_manifest_hash_is_reproducible_across_independent_calls():
    secret = "unique-redaction-marker-77421"
    messages = [
        {"role": "system", "content": "contract " * 20},
        {"role": "user", "content": f"detail {secret} " * 50},
        {"role": "user", "content": "final response contract"},
    ]
    first = fit_messages_with_receipt(
        copy.deepcopy(messages),
        budget_tokens=400,
        model_id="m",
        harness_version="h",
        redaction_values=[secret],
        tool_schemas=[{"type": "function", "function": {"name": "read", "parameters": {}}}],
    )
    second = fit_messages_with_receipt(
        copy.deepcopy(messages),
        budget_tokens=400,
        model_id="m",
        harness_version="h",
        redaction_values=[secret],
        tool_schemas=[{"type": "function", "function": {"name": "read", "parameters": {}}}],
    )
    assert first.manifest.manifest_hash == second.manifest.manifest_hash
    assert first.envelope.content_hash == second.envelope.content_hash
    assert first.manifest.model_dump_json() == second.manifest.model_dump_json()


@pytest.mark.parametrize("tier", [Tier.SMALL, Tier.MID, Tier.FRONTIER])
def test_section_budget_allocation_is_deterministic_and_exhaustive(tier):
    from metaharness.context import ContextSectionType

    sections = [
        ContextSectionType.SYSTEM_INSTRUCTIONS,
        ContextSectionType.TASK_CONTRACT,
        ContextSectionType.MEMORY,
        ContextSectionType.RESPONSE_CONTRACT,
        ContextSectionType.TOOL_SCHEMAS,
    ]
    first = allocate_section_budgets(sections, 977, tier)
    second = allocate_section_budgets(sections, 977, tier)
    assert first == second
    assert sum(first) == 977
    assert all(value >= 0 for value in first)


@pytest.mark.parametrize(
    "case",
    cases_for("test_context_determinism", "lossy_compression", status="enforced"),
    ids=lambda c: c["id"],
)
def test_compression_receipt_action_must_agree_with_hash_change(case):
    assert case["id"] == "lossy-compression-receipt-hash-consistency"
    same_hash = "sha256:" + "1" * 64
    other_hash = "sha256:" + "2" * 64
    with pytest.raises(ValidationError):
        CompressionReceipt(
            stable_id="s",
            action=CompressionAction.NONE,
            before_hash=same_hash,
            after_hash=other_hash,
            original_tokens=10,
            final_tokens=10,
            reason="claims no change but hash changed",
        )
    with pytest.raises(ValidationError):
        CompressionReceipt(
            stable_id="s",
            action=CompressionAction.HEAD_TAIL,
            before_hash=same_hash,
            after_hash=same_hash,
            original_tokens=10,
            final_tokens=4,
            reason="claims compression but hash unchanged",
        )


@pytest.mark.parametrize(
    "case",
    cases_for("test_context_determinism", "hw_confounding", status="enforced"),
    ids=lambda c: c["id"],
)
def test_source_identity_is_exactly_one_of_pinned_hash_or_live_watermark(case):
    assert case["id"] == "hw-confounding-source-identity-xor"
    with pytest.raises(ValidationError):
        make_source(content_hash=None, high_water_mark=None)
    with pytest.raises(ValidationError):
        make_source(content_hash="sha256:" + "1" * 64, high_water_mark="event-seq:42")
    live_only = make_source(content_hash=None, high_water_mark="event-seq:42")
    assert live_only.content_hash is None
    assert live_only.high_water_mark == "event-seq:42"


def test_redaction_covers_nested_lists_dicts_and_secret_shaped_keys_without_touching_live_messages():
    secret = "sk-super-secret-value-0000000000"
    messages = [
        {"role": "system", "content": "system"},
        {
            "role": "user",
            "content": json.dumps(
                {
                    "notes": ["fine", f"leaked={secret}"],
                    secret: "value-under-a-secret-key",
                }
            ),
        },
    ]
    original = copy.deepcopy(messages)
    receipt = fit_messages_with_receipt(
        messages,
        budget_tokens=10_000,
        model_id="m",
        harness_version="h",
        redaction_values=[secret],
    )
    assert messages == original
    persisted = receipt.manifest.model_dump_json()
    assert secret not in persisted
    assert receipt.manifest.redaction_count >= 2


def test_manifest_round_trips_messages_tool_schemas_and_redacted_envelope():
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "task"},
    ]
    schemas = [{"type": "function", "function": {"name": "read", "parameters": {}}}]
    receipt = fit_messages_with_receipt(
        messages,
        budget_tokens=1_000,
        model_id="m",
        harness_version="h",
        tool_schemas=schemas,
    )
    reloaded = ContextManifest.model_validate_json(receipt.manifest.model_dump_json())
    assert reloaded.reconstruct_messages() == messages
    assert reloaded.reconstruct_tool_schemas() == schemas
    assert reloaded.reconstruct_redacted_envelope().content_hash == reloaded.envelope_hash
