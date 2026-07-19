"""META-19: the live context assembler.

Promotes the typed `ContextEnvelope` from a shadow sidecar to the single live
assembler both worker families consume. Callers DECLARE sections (`SectionDraft`);
the assembler enforces trust fail-closed, redacts BEFORE fitting, fits per
section, and returns EVERY transport surface (chat messages, tool schemas, flat
prompt, system prompt) already redacted — plus the `ContextEnvelope`/
`ContextManifest`. Because the sent bytes are DERIVED from the validated
envelope, divergence between "what the model saw" and the manifest is impossible
by construction.

Deterministic: no wall-clock, no randomness. Reuses assembly.py machinery
(redaction, section-budget allocation, envelope hashing, digest_text fitting) so
shadow and live share one redaction/fitting source of truth.
"""
from __future__ import annotations

from typing import Any, Iterable, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from metaharness.context.assembly import (
    _make_envelope,
    _redact,
    _redact_string,
    allocate_section_budgets,
    estimate_tokens,
    messages_tokens,
)
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
    FrozenModel,
    Sensitivity,
    canonical_json,
    content_hash,
)
from metaharness.core.types import Tier
from metaharness.tools.registry import digest_text

# Instruction slots: a draft occupying one of these section types MUST carry
# INSTRUCTION trust, and untrusted/generated content may never occupy one.
_INSTRUCTION_SLOTS = frozenset({
    ContextSectionType.SYSTEM_INSTRUCTIONS,
    ContextSectionType.RESPONSE_CONTRACT,
    ContextSectionType.TOOL_SCHEMAS,
})
# Protected edge sections the models.py validator forbids omitting.
_PROTECTED_SECTIONS = frozenset({
    ContextSectionType.SYSTEM_INSTRUCTIONS,
    ContextSectionType.RESPONSE_CONTRACT,
})
# Weakest-link ordering for aggregating a merged surface's trust (a rendered
# message may concatenate drafts of differing trust — e.g. the objective is an
# INSTRUCTION but task inputs are UNTRUSTED_EVIDENCE — so the surface entry
# reports the WEAKEST trust present: the surface contains untrusted bytes).
_TRUST_WEAKNESS = {
    ContextTrust.UNTRUSTED_EVIDENCE: 0,
    ContextTrust.GENERATED_SUMMARY: 1,
    ContextTrust.VERIFIED_FACT: 2,
    ContextTrust.INSTRUCTION: 3,
}
_SENSITIVITY_RANK = {
    Sensitivity.PUBLIC: 0,
    Sensitivity.INTERNAL: 1,
    Sensitivity.RESTRICTED: 2,
    Sensitivity.SECRET: 3,
}


class LiveContextViolation(Exception):
    """A live-context contract was violated — raised BEFORE any model call.

    Deterministic: pure assembly with no infrastructure luck, so a retry would
    fail identically. BaseRunner tags it as WorkerResult.error_kind =
    "context_contract" (META-19 F6).
    """


class SectionDraft(FrozenModel):
    """The caller-declared input to `assemble_live`: what one section IS.

    Token counts and hashes are computed by the assembler, never declared. The
    optional message-rendering hints (`role`, `tool_call_id`, `tool_calls`)
    let assistant tool-call turns and tool observations round-trip losslessly
    (F4) so round-N request bodies are exactly reconstructable.
    """

    section_type: ContextSectionType
    source_kind: ContextSourceKind
    stable_id: str = Field(min_length=1)
    trust: ContextTrust
    sensitivity: Sensitivity
    content: str = ""
    # FIX-2 (codex#2): role is a closed enum, not an open string — an arbitrary
    # value (or an untrusted draft claiming role="system") must not be able to
    # smuggle content into the system message. None renders as user.
    role: Optional[Literal["system", "user", "assistant", "tool"]] = None
    tool_call_id: Optional[str] = None
    tool_calls: Optional[list[dict[str, Any]]] = None


class LiveAssembly(BaseModel):
    """Every transport surface for one assembly, all redacted, plus provenance.

    `_body()` / CLI argv MUST consume THESE surfaces, never the raw originals —
    that is what makes redaction cover the bytes actually sent.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)
    messages: list[dict[str, Any]]
    tool_schemas: list[dict[str, Any]]
    prompt: str
    system_prompt: str
    envelope: ContextEnvelope
    manifest: ContextManifest


def _mergeable(role: str, draft: SectionDraft) -> bool:
    """system/user text drafts merge into one message; assistant/tool turns
    (structured extras) each stay a discrete message."""
    return role in ("system", "user") and not draft.tool_calls and not draft.tool_call_id


def _group_messages(rendered: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group rendered drafts into per-message groups, merging consecutive
    same-role text turns. Each group renders to exactly one chat message."""
    groups: list[dict[str, Any]] = []
    for item in rendered:
        erole = item["role"] or "user"
        if (groups and item["merge"] and groups[-1]["merge"]
                and groups[-1]["erole"] == erole):
            groups[-1]["items"].append(item)
        else:
            groups.append({"erole": erole, "merge": item["merge"], "items": [item]})
    return groups


def _render_group(group: dict[str, Any], key: str) -> dict[str, Any]:
    """Render one message group to a chat message using content variant `key`
    ('final' for the sent bytes, 'pre' for the pre-fit comparison)."""
    message: dict[str, Any] = {
        "role": group["erole"],
        "content": "\n\n".join(item[key] for item in group["items"]),
    }
    first = group["items"][0]
    if first["tool_calls"] is not None:  # only ever a single-item structured turn
        message["tool_calls"] = first["tool_calls"]
    if first["tool_call_id"] is not None:
        message["tool_call_id"] = first["tool_call_id"]
    return message


def assemble_live(
    drafts: list[SectionDraft],
    *,
    transport: Literal["chat", "cli"],
    budget_tokens: int,
    model_id: str,
    harness_version: str,
    tier: Tier,
    redaction_values: Iterable[str] = (),
    tool_schemas: Optional[list[dict[str, Any]]] = None,
    policy_version: str = "context-live-v1",
    memory_snapshot_version: Optional[str] = None,
    evaluator_version: str = "not-bound:live",
    weight_snapshot_version: Optional[str] = None,
    evidence_snapshot_version: str = "not-bound:live",
    candidate_version: str = "live-prompt",
    parent_candidate_version: Optional[str] = None,
    model_portfolio_version: str = "not-bound:live",
    project_id: str = "meta-harness",
) -> LiveAssembly:
    """Assemble one live context, fail-closed. Raises `LiveContextViolation`
    (pre model call) on any trust or budget contract breach.

    FIX-6 (codex#7 + opus P3-1/P3-2): `transport` names the surface the caller
    actually transmits — "chat" (message + tool_schemas) or "cli" (flat_prompt +
    system_prompt). The manifest attests ONLY the transmitted surfaces (never a
    view the adapter did not send), and `budget_used_tokens` reports that
    surface's total. The ContextEnvelope sections are identical across transports.

    F9 documented non-goal: never pass a '-breaking-' `harness_version` here —
    `ContextVersionBindings` demands `evidence_snapshot_version=None` for a
    breaking bump, but that field is non-nullable (models.py:103 vs :111). The
    live path binds concrete snapshot versions, so a breaking bump would raise a
    bare pydantic error; the frozen validator is intentionally left untouched.
    """
    # -- a. trust enforcement (fail closed) -----------------------------------
    seen_ids: set[str] = set()
    for draft in drafts:
        if draft.stable_id in seen_ids:
            raise LiveContextViolation(f"duplicate section stable_id {draft.stable_id!r}")
        seen_ids.add(draft.stable_id)
        if draft.section_type in _INSTRUCTION_SLOTS and draft.trust is not ContextTrust.INSTRUCTION:
            raise LiveContextViolation(
                f"instruction slot {draft.section_type.value!r} requires INSTRUCTION "
                f"trust, got {draft.trust.value!r} (section {draft.stable_id!r})"
            )
        if (
            draft.trust in (ContextTrust.UNTRUSTED_EVIDENCE, ContextTrust.GENERATED_SUMMARY)
            and draft.section_type in _INSTRUCTION_SLOTS
        ):
            raise LiveContextViolation(
                f"{draft.trust.value!r} content may never occupy instruction slot "
                f"{draft.section_type.value!r} (section {draft.stable_id!r})"
            )
        if (
            draft.source_kind is ContextSourceKind.EVALUATOR_RECEIPT
            and draft.trust is ContextTrust.INSTRUCTION
        ):
            # ContextSourceRef enforces this too; surface it as the typed live
            # violation rather than a bare pydantic error (spec 1a).
            raise LiveContextViolation(
                f"evaluator_receipt sources may never carry INSTRUCTION trust "
                f"(section {draft.stable_id!r})"
            )
        # FIX-2 (codex#2): only INSTRUCTION-trust content in an instruction slot
        # may render into the SYSTEM message. This closes the escalation where an
        # UNTRUSTED/GENERATED (or non-instruction) draft claims role="system" and
        # the model reads it as a system instruction. (Deviation from the batch's
        # literal "SYSTEM_INSTRUCTIONS only": RESPONSE_CONTRACT boundaries/output-
        # schema legitimately render into the system message for legacy parity, so
        # the gate admits any INSTRUCTION-trust instruction slot — the security
        # invariant "no untrusted bytes in the system message" is fully preserved.)
        if draft.role == "system" and not (
            draft.trust is ContextTrust.INSTRUCTION
            and draft.section_type in _INSTRUCTION_SLOTS
        ):
            raise LiveContextViolation(
                f"role='system' requires INSTRUCTION trust in an instruction slot; "
                f"section {draft.stable_id!r} is {draft.trust.value!r}/"
                f"{draft.section_type.value!r}"
            )

    redaction_tuple = tuple(sorted(
        {value for value in redaction_values if value},
        key=lambda value: (-len(value), value),
    ))

    # -- b. redaction on live content (before fitting) + c. per-section fit ----
    section_budgets = allocate_section_budgets(
        [draft.section_type for draft in drafts], budget_tokens, tier
    )
    sections: list[ContextSection] = []
    rendered: list[dict[str, Any]] = []
    redaction_count = 0
    for index, draft in enumerate(drafts):
        redacted_content, ccount = _redact_string(draft.content, redaction_tuple)
        redacted_tool_calls: Optional[list[dict[str, Any]]] = None
        if draft.tool_calls is not None:
            redacted_tool_calls, tcount = _redact(draft.tool_calls, redaction_tuple)
            ccount += tcount
        # FIX-4 (codex#5): tool_call_id is a model-generated string — redact it
        # like content/tool_calls before it reaches the wire or the manifest.
        redacted_tool_call_id: Optional[str] = None
        if draft.tool_call_id is not None:
            redacted_tool_call_id, idcount = _redact_string(draft.tool_call_id, redaction_tuple)
            ccount += idcount
        redaction_count += ccount

        budget = section_budgets[index]
        protected = draft.section_type in _PROTECTED_SECTIONS
        redacted_tokens = estimate_tokens(redacted_content)
        if redacted_tokens <= budget:
            final_content, action = redacted_content, CompressionAction.NONE
        elif budget <= 0 and not protected and redacted_content:
            # non-protected zero-budget section: omit honestly (spec 1c)
            final_content, action = "", CompressionAction.OMITTED
        else:
            # deterministic head/tail digest to the section's char budget; may
            # GROW a tiny input near the floor (pruning marker) — recorded
            # honestly as HEAD_TAIL only when the content actually changed.
            candidate = digest_text(redacted_content, max(1, budget * 4))
            if candidate == redacted_content:
                final_content, action = redacted_content, CompressionAction.NONE
            else:
                final_content, action = candidate, CompressionAction.HEAD_TAIL

        # original >= selected so the models.py count invariant holds even when
        # redaction's placeholder grows a tiny secret (selected = post-redaction).
        original_tokens = max(estimate_tokens(draft.content), redacted_tokens)
        selected_tokens = redacted_tokens
        if action is CompressionAction.OMITTED:
            compressed_tokens = 0
            reason: Optional[str] = "zero token budget after tier allocation"
        elif action is CompressionAction.HEAD_TAIL:
            compressed_tokens = estimate_tokens(final_content)
            reason = "deterministic head/tail budget fitting"
        else:
            compressed_tokens = selected_tokens
            reason = None

        content_h = content_hash(final_content)
        source = ContextSourceRef(
            source_id=draft.stable_id,
            kind=draft.source_kind,
            scope=ContextScope(project_id=project_id),
            trust=draft.trust,
            content_hash=content_h,
            selection_reason="live context assembly (META-19)",
            sensitivity=draft.sensitivity,
            fetchable=False,
        )
        sections.append(ContextSection(
            section_type=draft.section_type,
            stable_id=draft.stable_id,
            source=source,
            source_hash=content_h,
            trust=draft.trust,
            content=final_content,
            original_tokens=original_tokens,
            selected_tokens=selected_tokens,
            compressed_tokens=compressed_tokens,
            budget_tokens=budget,
            ordering_priority=index,
            sensitivity=draft.sensitivity,
            redaction_markers=("[REDACTED]",) if ccount else (),
            compression_action=action,
            omission_reason=reason,
        ))
        role = draft.role or "user"
        rendered.append({
            "draft": draft,
            "role": draft.role,
            "merge": _mergeable(role, draft),
            "pre": redacted_content,
            "final": final_content,
            "source_content": draft.content,
            "tool_calls": redacted_tool_calls,
            "tool_call_id": redacted_tool_call_id,
            "action": action,  # FIX-9: honest per-section fitting/omission fact
            "redacted": ccount > 0,
        })

    # -- d. envelope ----------------------------------------------------------
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
        sections, policy_version=policy_version, model_id=model_id, versions=versions,
    )

    # -- transport surfaces (all redacted) ------------------------------------
    groups = _group_messages(rendered)
    messages = [_render_group(group, "final") for group in groups]
    messages_pre = [_render_group(group, "pre") for group in groups]
    non_system = [item for item in rendered if (item["role"] or "user") != "system"]
    system_items = [item for item in rendered if item["role"] == "system"]
    prompt = "\n\n".join(item["final"] for item in non_system)
    prompt_pre = "\n\n".join(item["pre"] for item in non_system)
    system_prompt = "\n\n".join(item["final"] for item in system_items)
    system_prompt_pre = "\n\n".join(item["pre"] for item in system_items)

    redacted_schemas: list[dict[str, Any]] = []
    tool_schema_tokens = 0
    if tool_schemas:
        schema_payload = [dict(schema) for schema in tool_schemas]
        redacted_schemas, scount = _redact(schema_payload, redaction_tuple)
        redaction_count += scount
        tool_schema_tokens = estimate_tokens(canonical_json(redacted_schemas))

    # -- e. hard budget postcondition (F7, FIX-5, FIX-6) ----------------------
    # FIX-5 (codex#6): structured turns (tool_calls, tool_call_id) are real bytes
    # on the wire but invisible to messages_tokens — count them so oversized tool
    # arguments cannot slip past the budget. Overflow fails closed (no truncation
    # of structured turns).
    structured_tokens = 0
    for message in messages:
        if "tool_calls" in message:
            structured_tokens += estimate_tokens(canonical_json(message["tool_calls"]))
        if "tool_call_id" in message:
            structured_tokens += estimate_tokens(str(message["tool_call_id"]))
    chat_total = messages_tokens(messages) + structured_tokens + tool_schema_tokens
    cli_total = estimate_tokens(prompt) + estimate_tokens(system_prompt)
    # FIX-6: enforce the budget against the surface actually transmitted.
    transmitted_total = chat_total if transport == "chat" else cli_total
    if transmitted_total > budget_tokens:
        raise LiveContextViolation(
            f"assembled {transport} context exceeds hard budget: "
            f"{transmitted_total} > {budget_tokens} tokens "
            "(protected sections cannot be omitted to fit)"
        )

    # -- manifest entries + 1:1 compression receipts --------------------------
    # FIX-6: attest ONLY the transmitted surface — chat callers send messages
    # (+tool_schemas); cli callers send the flat prompt (+system prompt). The
    # envelope sections stay identical either way.
    entries: list[ContextManifestEntry] = []
    receipts: list[CompressionReceipt] = []

    def _receipt_reason(members: list[dict[str, Any]], action: CompressionAction) -> str:
        # FIX-9 (opus P3-3): a change driven by a zero-budget OMITTED member is
        # NOT head/tail digest fitting — report the honest cause.
        if action is CompressionAction.NONE:
            return "within allocated budget"
        if any(item["action"] is CompressionAction.OMITTED for item in members):
            return "section omitted at zero budget"
        return "deterministic head/tail budget fitting"

    def _string_surface_entry(surface: str, stable_id: str, pre: str, final: str,
                              members: list[dict[str, Any]]) -> None:
        before_hash, after_hash = content_hash(pre), content_hash(final)
        action = (CompressionAction.NONE if before_hash == after_hash
                  else CompressionAction.HEAD_TAIL)
        weakest = min(members, key=lambda item: _TRUST_WEAKNESS[item["draft"].trust])
        sensitivity = max(
            (item["draft"].sensitivity for item in members),
            key=lambda value: _SENSITIVITY_RANK[value],
        )
        entries.append(ContextManifestEntry(
            stable_id=stable_id,
            surface=surface,  # type: ignore[arg-type]
            payload_json=canonical_json(final),
            source_kind=weakest["draft"].source_kind,
            trust=weakest["draft"].trust,
            sensitivity=sensitivity,
            source_hash=content_hash("".join(item["source_content"] for item in members)),
            selected_hash=content_hash(final),
            redacted=any(item["redacted"] for item in members),
        ))
        receipts.append(CompressionReceipt(
            stable_id=stable_id,
            action=action,
            before_hash=before_hash,
            after_hash=after_hash,
            original_tokens=estimate_tokens(pre),
            final_tokens=estimate_tokens(final),
            reason=_receipt_reason(members, action),
        ))

    if transport == "chat":
        # message surfaces: one entry per rendered message. A merged message spans
        # drafts of possibly differing trust — report the weakest (untrusted wins).
        for group, message, message_pre in zip(groups, messages, messages_pre, strict=True):
            members = group["items"]
            weakest = min(members, key=lambda item: _TRUST_WEAKNESS[item["draft"].trust])
            sensitivity = max(
                (item["draft"].sensitivity for item in members),
                key=lambda value: _SENSITIVITY_RANK[value],
            )
            stable_id = f"message-{len(entries):04d}-{message['role']}"
            payload = {key: value for key, value in message.items()}
            payload_pre = {key: value for key, value in message_pre.items()}
            before_hash = content_hash(payload_pre)
            after_hash = content_hash(payload)
            action = (CompressionAction.NONE if before_hash == after_hash
                      else CompressionAction.HEAD_TAIL)
            source_hash = content_hash("".join(item["source_content"] for item in members))
            entries.append(ContextManifestEntry(
                stable_id=stable_id,
                surface="message",
                payload_json=canonical_json(payload),
                source_kind=weakest["draft"].source_kind,
                trust=weakest["draft"].trust,
                sensitivity=sensitivity,
                source_hash=source_hash,
                selected_hash=content_hash(payload),
                redacted=any(item["redacted"] for item in members),
            ))
            receipts.append(CompressionReceipt(
                stable_id=stable_id,
                action=action,
                before_hash=before_hash,
                after_hash=after_hash,
                original_tokens=estimate_tokens(str(message_pre.get("content") or "")),
                final_tokens=estimate_tokens(str(message.get("content") or "")),
                reason=_receipt_reason(members, action),
            ))

        if tool_schemas:
            schema_json = canonical_json(redacted_schemas)
            before = content_hash([dict(schema) for schema in tool_schemas])
            after = content_hash(redacted_schemas)
            # tool schemas are transported verbatim (never fitted); redaction may
            # differ them but that is not compression — record NONE with equal
            # hashes on the redacted payload to keep the receipt honest.
            entries.append(ContextManifestEntry(
                stable_id="tool-schemas",
                surface="tool_schemas",
                payload_json=schema_json,
                source_kind=ContextSourceKind.TOOL_POLICY_SCHEMA,
                trust=ContextTrust.INSTRUCTION,
                sensitivity=Sensitivity.INTERNAL,
                source_hash=after,
                selected_hash=content_hash(redacted_schemas),
                redacted=before != after,
            ))
            receipts.append(CompressionReceipt(
                stable_id="tool-schemas",
                action=CompressionAction.NONE,
                before_hash=after,
                after_hash=after,
                original_tokens=tool_schema_tokens,
                final_tokens=tool_schema_tokens,
                reason="tool schemas transported within allocated budget",
            ))
    else:  # transport == "cli": flat prompt (+system prompt when declared)
        _string_surface_entry("flat_prompt", "flat-prompt", prompt_pre, prompt,
                              non_system or rendered)
        if system_items:
            _string_surface_entry("system_prompt", "system-prompt", system_prompt_pre,
                                  system_prompt, system_items)

    # -- manifest -------------------------------------------------------------
    # sections are already redacted, so the envelope IS the redacted envelope.
    redacted_envelope = envelope.model_dump(mode="json")
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
        "deliberate_omissions": tuple(
            section.stable_id for section in sections
            if section.compression_action is CompressionAction.OMITTED
        ),
        "artifact_refs": (),
        # FIX-6 (opus P3-2): report the transmitted surface's total, not the chat
        # total for every transport.
        "budget_used_tokens": transmitted_total,
        "budget_limit_tokens": budget_tokens,
        "redaction_count": redaction_count,
    }
    serializable = {
        **manifest_values,
        "versions": versions.model_dump(mode="json"),
        "entries": [entry.model_dump(mode="json") for entry in entries],
        "compression_receipts": [receipt.model_dump(mode="json") for receipt in receipts],
    }
    manifest = ContextManifest(**manifest_values, manifest_hash=content_hash(serializable))

    return LiveAssembly(
        messages=messages,
        tool_schemas=redacted_schemas,
        prompt=prompt,
        system_prompt=system_prompt,
        envelope=envelope,
        manifest=manifest,
    )
