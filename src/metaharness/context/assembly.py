"""Legacy-compatible fitting plus deterministic typed shadow receipts."""
from __future__ import annotations

import re
from typing import Any, Iterable

from pydantic import BaseModel, ConfigDict

from metaharness.context.models import (
    CompressionAction,
    CompressionReceipt,
    ContextEnvelope,
    ContextManifest,
    ContextManifestEntry,
    ContextScope,
    ContextSection,
    ContextSectionType,
    ContextSourceKind,
    ContextSourceRef,
    ContextTrust,
    ContextVersionBindings,
    Sensitivity,
    canonical_json,
    content_hash,
)
from metaharness.core.types import Tier
from metaharness.tools.registry import digest_text

TIER_CONTEXT_BUDGET: dict[Tier, int] = {
    Tier.SMALL: 8_000,
    Tier.MID: 16_000,
    Tier.FRONTIER: 32_000,
}
GENERATION_RESERVE = 0.25

TIER_SECTION_WEIGHTS: dict[Tier, dict[ContextSectionType, int]] = {
    Tier.SMALL: {
        ContextSectionType.SYSTEM_INSTRUCTIONS: 24,
        ContextSectionType.TASK_CONTRACT: 24,
        ContextSectionType.TOOL_SCHEMAS: 8,
        ContextSectionType.WORKFLOW_STATE: 10,
        ContextSectionType.PRIOR_OUTPUTS: 10,
        ContextSectionType.MEMORY: 7,
        ContextSectionType.POPULATION_FINDINGS: 4,
        ContextSectionType.VERIFIER_FEEDBACK: 8,
        ContextSectionType.RESPONSE_CONTRACT: 5,
    },
    Tier.MID: {
        ContextSectionType.SYSTEM_INSTRUCTIONS: 20,
        ContextSectionType.TASK_CONTRACT: 21,
        ContextSectionType.TOOL_SCHEMAS: 9,
        ContextSectionType.WORKFLOW_STATE: 10,
        ContextSectionType.PRIOR_OUTPUTS: 14,
        ContextSectionType.MEMORY: 9,
        ContextSectionType.POPULATION_FINDINGS: 5,
        ContextSectionType.VERIFIER_FEEDBACK: 7,
        ContextSectionType.RESPONSE_CONTRACT: 5,
    },
    Tier.FRONTIER: {
        ContextSectionType.SYSTEM_INSTRUCTIONS: 16,
        ContextSectionType.TASK_CONTRACT: 18,
        ContextSectionType.TOOL_SCHEMAS: 10,
        ContextSectionType.WORKFLOW_STATE: 10,
        ContextSectionType.PRIOR_OUTPUTS: 18,
        ContextSectionType.MEMORY: 11,
        ContextSectionType.POPULATION_FINDINGS: 7,
        ContextSectionType.VERIFIER_FEEDBACK: 6,
        ContextSectionType.RESPONSE_CONTRACT: 4,
    },
}


def estimate_tokens(text: str) -> int:
    return len(text) // 4 + 1


def messages_tokens(messages: list[dict[str, Any]]) -> int:
    return sum(estimate_tokens(str(message.get("content") or "")) for message in messages)


def budget_for(tier: Tier, override: int | None = None) -> int:
    budget = override or TIER_CONTEXT_BUDGET.get(tier, 8_000)
    return int(budget * (1 - GENERATION_RESERVE))


def allocate_section_budgets(
    section_types: list[ContextSectionType],
    budget_tokens: int,
    tier: Tier,
) -> list[int]:
    """Allocate the whole budget by tier using deterministic largest remainder."""
    if budget_tokens < 0:
        raise ValueError("budget_tokens must be nonnegative")
    if not section_types:
        return []
    weights = TIER_SECTION_WEIGHTS[tier]
    raw_weights = [weights[section_type] for section_type in section_types]
    total_weight = sum(raw_weights)
    numerators = [budget_tokens * weight for weight in raw_weights]
    allocations = [numerator // total_weight for numerator in numerators]
    remaining = budget_tokens - sum(allocations)
    order = sorted(
        range(len(section_types)),
        key=lambda index: (-(numerators[index] % total_weight), index),
    )
    for index in order[:remaining]:
        allocations[index] += 1
    return allocations


def fit_messages(messages: list[dict[str, Any]], budget_tokens: int) -> list[dict[str, Any]]:
    """Preserve the pre-contract fitting behavior exactly for existing callers."""
    if messages_tokens(messages) <= budget_tokens:
        return messages
    result = [dict(message) for message in messages]
    prunable = [
        index
        for index, message in enumerate(result)
        if not (index == 0 and message.get("role") == "system")
        and index != len(result) - 1
    ]

    def priority(index: int) -> tuple[int, int]:
        role_rank = 0 if result[index].get("role") == "tool" else 1
        return role_rank, -len(str(result[index].get("content") or ""))

    for index in sorted(prunable, key=priority):
        if messages_tokens(result) <= budget_tokens:
            break
        text = str(result[index].get("content") or "")
        overshoot = messages_tokens(result) - budget_tokens
        target_chars = max(400, len(text) - overshoot * 4 - 200)
        if target_chars < len(text):
            result[index]["content"] = digest_text(text, target_chars)
    return result


class ContextFitReceipt(BaseModel):
    """Internal sidecar: live messages plus the safe durable shadow receipt."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    messages: list[dict[str, Any]]
    redacted_messages: list[dict[str, Any]]
    envelope: ContextEnvelope
    manifest: ContextManifest


_BUILTIN_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[^\s,;]+"),
)


def _redact_string(value: str, redaction_values: tuple[str, ...]) -> tuple[str, int]:
    redacted = value
    count = 0
    for secret in redaction_values:
        occurrences = redacted.count(secret)
        if occurrences:
            redacted = redacted.replace(secret, "[REDACTED]")
            count += occurrences
    for pattern in _BUILTIN_SECRET_PATTERNS:
        def replace(match: re.Match[str]) -> str:
            nonlocal count
            count += 1
            prefix = match.group(1) if match.lastindex else ""
            return prefix + "[REDACTED]"

        redacted = pattern.sub(replace, redacted)
    return redacted, count


def _redact(value: Any, redaction_values: tuple[str, ...]) -> tuple[Any, int]:
    if isinstance(value, str):
        return _redact_string(value, redaction_values)
    if isinstance(value, list):
        output, total = [], 0
        for item in value:
            clean, count = _redact(item, redaction_values)
            output.append(clean)
            total += count
        return output, total
    if isinstance(value, dict):
        output, total = {}, 0
        for key in sorted(value):
            clean_key, key_count = _redact_string(key, redaction_values)
            if clean_key != key:
                clean_key = f"[REDACTED_KEY_{len(output)}]"
            clean, count = _redact(value[key], redaction_values)
            output[clean_key] = clean
            total += key_count + count
        return output, total
    return value, 0


def _message_contract(index: int, total: int, role: str) -> tuple[ContextSectionType, ContextSourceKind, ContextTrust]:
    if index == 0 and role == "system":
        return (
            ContextSectionType.SYSTEM_INSTRUCTIONS,
            ContextSourceKind.PROTECTED_INSTRUCTIONS,
            ContextTrust.INSTRUCTION,
        )
    if role == "tool":
        return (
            ContextSectionType.PRIOR_OUTPUTS,
            ContextSourceKind.IMMUTABLE_ARTIFACT,
            ContextTrust.UNTRUSTED_EVIDENCE,
        )
    if role == "assistant":
        return (
            ContextSectionType.PRIOR_OUTPUTS,
            ContextSourceKind.LIVE_RUN_STATE,
            ContextTrust.UNTRUSTED_EVIDENCE,
        )
    if index == total - 1:
        return (
            ContextSectionType.RESPONSE_CONTRACT,
            ContextSourceKind.RESPONSE_CONTRACT,
            ContextTrust.INSTRUCTION,
        )
    return ContextSectionType.TASK_CONTRACT, ContextSourceKind.GOAL, ContextTrust.INSTRUCTION


def _make_envelope(
    sections: list[ContextSection],
    *,
    policy_version: str,
    model_id: str,
    versions: ContextVersionBindings,
) -> ContextEnvelope:
    values = {
        "schema_version": 1,
        "policy_version": policy_version,
        "model_id": model_id,
        "versions": versions,
        "sections": tuple(sections),
    }
    hash_material = {
        **values,
        "versions": versions.model_dump(mode="json"),
        "sections": [section.model_dump(mode="json") for section in sections],
    }
    return ContextEnvelope(**values, content_hash=content_hash(hash_material))


def fit_messages_with_receipt(
    messages: list[dict[str, Any]],
    budget_tokens: int,
    *,
    model_id: str,
    harness_version: str,
    policy_version: str = "context-shadow-v1",
    memory_snapshot_version: str | None = None,
    evaluator_version: str = "not-bound:shadow",
    weight_snapshot_version: str | None = None,
    evidence_snapshot_version: str = "not-bound:shadow",
    candidate_version: str = "live-prompt:shadow",
    parent_candidate_version: str | None = None,
    model_portfolio_version: str = "not-bound:shadow",
    project_id: str = "meta-harness",
    tier: Tier = Tier.SMALL,
    redaction_values: Iterable[str] = (),
    tool_schemas: list[dict[str, Any]] | None = None,
) -> ContextFitReceipt:
    """Fit exactly as before and build a deterministic, redacted sidecar receipt."""
    originals = [dict(message) for message in messages]
    fitted = fit_messages(messages, budget_tokens)
    fitted = [dict(message) for message in fitted]
    redaction_tuple = tuple(sorted(
        {value for value in redaction_values if value},
        key=lambda value: (-len(value), value),
    ))
    redacted_messages: list[dict[str, Any]] = []
    sections: list[ContextSection] = []
    receipts: list[CompressionReceipt] = []
    entries: list[ContextManifestEntry] = []
    redaction_count = 0
    tool_schema_tokens = 0

    contracts = [
        _message_contract(index, len(fitted), str(message.get("role") or "unknown"))
        for index, message in enumerate(fitted)
    ]
    if tool_schemas:
        contracts.append(
            (
                ContextSectionType.TOOL_SCHEMAS,
                ContextSourceKind.TOOL_POLICY_SCHEMA,
                ContextTrust.INSTRUCTION,
            )
        )
    section_budgets = allocate_section_budgets(
        [contract[0] for contract in contracts], budget_tokens, tier
    )

    for index, (original, selected) in enumerate(zip(originals, fitted, strict=True)):
        role = str(selected.get("role") or "unknown")
        section_type, source_kind, trust = contracts[index]
        original_content = str(original.get("content") or "")
        selected_content = str(selected.get("content") or "")
        before_hash, after_hash = content_hash(original_content), content_hash(selected_content)
        action = CompressionAction.NONE if before_hash == after_hash else CompressionAction.HEAD_TAIL
        reason = "within allocated budget" if action is CompressionAction.NONE else "legacy deterministic budget fitting"
        stable_id = f"message-{index:04d}-{role}"
        source = ContextSourceRef(
            source_id=stable_id,
            kind=source_kind,
            scope=ContextScope(project_id=project_id),
            trust=trust,
            content_hash=before_hash,
            selection_reason="existing live prompt message observed in shadow mode",
            sensitivity=Sensitivity.INTERNAL,
            fetchable=False,
        )
        original_tokens = estimate_tokens(original_content)
        final_tokens = estimate_tokens(selected_content)
        sections.append(
            ContextSection(
                section_type=section_type,
                stable_id=stable_id,
                source=source,
                source_hash=before_hash,
                trust=trust,
                content=selected_content,
                original_tokens=original_tokens,
                selected_tokens=original_tokens,
                compressed_tokens=final_tokens,
                budget_tokens=section_budgets[index],
                ordering_priority=index,
                sensitivity=Sensitivity.INTERNAL,
                compression_action=action,
                omission_reason=None if action is CompressionAction.NONE else reason,
            )
        )
        receipts.append(
            CompressionReceipt(
                stable_id=stable_id,
                action=action,
                before_hash=before_hash,
                after_hash=after_hash,
                original_tokens=original_tokens,
                final_tokens=final_tokens,
                reason=reason,
            )
        )
        redacted_message, count = _redact(selected, redaction_tuple)
        redaction_count += count
        redacted_messages.append(redacted_message)
        entries.append(
            ContextManifestEntry(
                stable_id=stable_id,
                surface="message",
                payload_json=canonical_json(redacted_message),
                source_kind=source_kind,
                trust=trust,
                sensitivity=Sensitivity.INTERNAL,
                source_hash=before_hash,
                selected_hash=content_hash(redacted_message),
                redacted=count > 0,
            )
        )

    if tool_schemas:
        stable_id = "tool-schemas"
        schema_payload = [dict(schema) for schema in tool_schemas]
        schema_hash = content_hash(schema_payload)
        redacted_schemas, count = _redact(schema_payload, redaction_tuple)
        redaction_count += count
        source = ContextSourceRef(
            source_id=stable_id,
            kind=ContextSourceKind.TOOL_POLICY_SCHEMA,
            scope=ContextScope(project_id=project_id),
            trust=ContextTrust.INSTRUCTION,
            content_hash=schema_hash,
            selection_reason="minimum task-authorized tool schemas observed in shadow mode",
            sensitivity=Sensitivity.INTERNAL,
            fetchable=False,
        )
        schema_tokens = estimate_tokens(canonical_json(schema_payload))
        tool_schema_tokens = schema_tokens
        sections.append(
            ContextSection(
                section_type=ContextSectionType.TOOL_SCHEMAS,
                stable_id=stable_id,
                source=source,
                source_hash=schema_hash,
                trust=ContextTrust.INSTRUCTION,
                content=canonical_json(schema_payload),
                original_tokens=schema_tokens,
                selected_tokens=schema_tokens,
                compressed_tokens=schema_tokens,
                budget_tokens=section_budgets[-1],
                ordering_priority=len(sections),
                sensitivity=Sensitivity.INTERNAL,
            )
        )
        receipts.append(
            CompressionReceipt(
                stable_id=stable_id,
                action=CompressionAction.NONE,
                before_hash=schema_hash,
                after_hash=schema_hash,
                original_tokens=schema_tokens,
                final_tokens=schema_tokens,
                reason="within allocated budget",
            )
        )
        entries.append(
            ContextManifestEntry(
                stable_id=stable_id,
                surface="tool_schemas",
                payload_json=canonical_json(redacted_schemas),
                source_kind=ContextSourceKind.TOOL_POLICY_SCHEMA,
                trust=ContextTrust.INSTRUCTION,
                sensitivity=Sensitivity.INTERNAL,
                source_hash=schema_hash,
                selected_hash=content_hash(redacted_schemas),
                redacted=count > 0,
            )
        )

    versions = ContextVersionBindings(
        model_portfolio_version=model_portfolio_version,
        harness_version=harness_version,
        evaluator_version=evaluator_version,
        weight_snapshot_version=weight_snapshot_version,
        memory_snapshot_version=memory_snapshot_version,
        evidence_snapshot_version=evidence_snapshot_version,
        candidate_version=candidate_version,
        parent_candidate_version=parent_candidate_version,
    )
    envelope = _make_envelope(
        sections,
        policy_version=policy_version,
        model_id=model_id,
        versions=versions,
    )
    redacted_envelope, _ = _redact(envelope.model_dump(mode="json"), redaction_tuple)
    redacted_envelope_material = {
        key: value for key, value in redacted_envelope.items() if key != "content_hash"
    }
    redacted_envelope["content_hash"] = content_hash(redacted_envelope_material)
    redacted_envelope_json = canonical_json(redacted_envelope)
    manifest_values = {
        "schema_version": 1,
        "policy_version": policy_version,
        "model_id": model_id,
        "versions": versions,
        "envelope_hash": envelope.content_hash,
        "redacted_envelope_hash": content_hash(redacted_envelope),
        "redacted_envelope_json": redacted_envelope_json,
        "entries": tuple(entries),
        "compression_receipts": tuple(receipts),
        "source_candidates_considered": tuple(entry.stable_id for entry in entries),
        "visibility_decisions": tuple(f"{entry.stable_id}:included" for entry in entries),
        "deliberate_omissions": (),
        "artifact_refs": (),
        "budget_used_tokens": messages_tokens(fitted) + tool_schema_tokens,
        "budget_limit_tokens": budget_tokens,
        "redaction_count": redaction_count,
    }
    serializable = {
        **manifest_values,
        "versions": versions.model_dump(mode="json"),
        "entries": [entry.model_dump(mode="json") for entry in entries],
        "compression_receipts": [receipt.model_dump(mode="json") for receipt in receipts],
    }
    manifest = ContextManifest(
        **manifest_values,
        manifest_hash=content_hash(serializable),
    )
    return ContextFitReceipt(
        messages=fitted,
        redacted_messages=redacted_messages,
        envelope=envelope,
        manifest=manifest,
    )
