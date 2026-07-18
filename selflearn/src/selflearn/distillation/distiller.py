"""Distiller: the one LLM step between sources and candidates.

SchemaGuard: the model's output must parse into valid ``CandidateEntry``
objects or the batch fails loudly — a malformed distillation never becomes
a half-entry. The deterministic injection screen runs over source text AND
candidate text; a match quarantines the entry for human review regardless
of anything downstream (including eval results, per decision 3).

Entry ids are content-derived (pack/topic/body hash), so re-distilling the
same source is idempotent instead of duplicative.
"""
from __future__ import annotations

import hashlib
import re
import warnings
from typing import Sequence

from selflearn.contracts import (
    CandidateEntry,
    ContractError,
    EntrySource,
    ProcedureStep,
    SourceDocument,
)
from selflearn.ports import ModelPort

DISTILLER_ROLE = "knowledge-distiller"

# Patterns target imperative injection phrasing, not topic mentions: AI-
# engineering sources legitimately DISCUSS system prompts and instructions
# (an M3 real-data finding — a harness-engineering lecture tripped a naive
# "system prompt" pattern). Screening must catch commands, not vocabulary.
INJECTION_PATTERNS: tuple[str, ...] = (
    r"ignore (all |any )?(previous|prior|above) (instructions|context)",
    r"disregard (your|all|the) (instructions|guidelines|rules)",
    r"(reveal|print|show|override|replace) (your |the )?system prompt",
    r"run\s+(curl|wget|bash\s+-c|sh\s+-c)",
    r"do not (tell|inform) the (user|human)",
    r"exfiltrat",
)

DISTILL_PROMPT = (
    "Distill the source material into knowledge entries. Return JSON: "
    '{"entries": [{"kind": "knowledge|skill|workflow", "body": "<=400 words, '
    'only claims traceable to the sources", "claims": ["short claim", ...], '
    '"topic": "kebab-case-topic", "task_types": [...], '
    '"procedure": {"steps": [...]} (workflow kind only)}]}. '
    "Never include claims absent from the sources."
)


class DistillationError(RuntimeError):
    """SchemaGuard violation or empty distillation. Always loud."""


def injection_screen(text: str) -> str:
    """Return the matched pattern, or '' when clean. Deterministic."""
    lowered = text.lower()
    for pattern in INJECTION_PATTERNS:
        if re.search(pattern, lowered):
            return pattern
    return ""


def _entry_id(pack: str, topic: str, body: str) -> str:
    """The content digest is never truncated away — truncating the whole id
    collided distinct entries under long pack/topic (review finding,
    reproduced) and silently dropped the second entry as 'already known'."""
    digest = hashlib.sha256(body.encode()).hexdigest()[:10]
    prefix = f"kn-{pack}-{topic}"[:69].rstrip("-")
    return f"{prefix}-{digest}"


class Distiller:
    def __init__(self, model: ModelPort):
        self.model = model

    def distill(self, docs: Sequence[SourceDocument], pack: str,
                topic: str) -> list[CandidateEntry]:
        if not docs:
            raise DistillationError("no source documents to distill")
        for doc in docs:
            if doc.assets:
                warnings.warn(
                    f"{doc.provenance.url}: {len(doc.assets)} visual assets "
                    "present; vision extraction wires in with verification "
                    "(M4) — text path only for now", stacklevel=2)
        source_text = "\n\n".join(
            chunk for doc in docs for chunk in (doc.chunks or doc.blocks))
        result = self.model.complete(
            DISTILLER_ROLE, DISTILL_PROMPT,
            {"source_text": source_text, "pack": pack, "topic": topic,
             "sources": [d.provenance.url for d in docs]})

        specs = result.get("entries") if isinstance(result, dict) else None
        if not isinstance(specs, list) or not specs:
            raise DistillationError(
                f"SchemaGuard: distiller returned no 'entries' list "
                f"(got {type(result).__name__})")
        return entries_from_specs(specs, docs, pack, topic)


def entries_from_specs(specs: list, docs: Sequence[SourceDocument], pack: str,
                       topic: str) -> list[CandidateEntry]:
    """SchemaGuard + injection screen over raw entry specs. Shared by the
    Distiller and by hosts whose *worker* produced the specs directly (the
    harness distill phase submits its output through this same gate)."""
    source_text = "\n\n".join(
        chunk for doc in docs for chunk in (doc.chunks or doc.blocks))
    sources = tuple(EntrySource(
        url=d.provenance.url, fetched_at=d.provenance.fetched_at,
        sha256=d.provenance.sha256, tier=d.tier,
        locator=d.provenance.locator) for d in docs)
    source_hit = injection_screen(source_text)

    entries: list[CandidateEntry] = []
    for spec in specs:
        if not isinstance(spec, dict):
            raise DistillationError(f"SchemaGuard: entry spec is "
                                    f"{type(spec).__name__}, not object")
        body = str(spec.get("body", "")).strip()
        entry_topic = str(spec.get("topic") or topic).strip() or topic
        hit = source_hit or injection_screen(
            body + " " + " ".join(map(str, spec.get("claims", []))))
        # SchemaGuard covers procedure parsing too (review finding: a step
        # missing 'objective' or a bare-list procedure — the natural JSON
        # shape — escaped as raw ContractError/AttributeError). Both shapes
        # are accepted: {"steps": [...]} and [...].
        try:
            raw_procedure = spec.get("procedure") or {}
            steps = (raw_procedure if isinstance(raw_procedure, list)
                     else raw_procedure.get("steps", []))
            procedure = tuple(
                ProcedureStep(
                    id=str(s.get("id", "")), objective=str(s.get("objective", "")),
                    task_type=str(s.get("task_type", "")),
                    tools=tuple(s.get("tools", [])),
                    depends_on=tuple(s.get("depends_on", [])),
                    check=tuple(sorted(dict(s.get("check", {})).items())))
                for s in steps)
            entry = CandidateEntry(
                id=_entry_id(pack, entry_topic, body),
                pack=pack, kind=str(spec.get("kind", "knowledge")),
                body=body, topic=entry_topic,
                claims=tuple(str(c) for c in spec.get("claims", [])),
                task_types=tuple(spec.get("task_types", [])),
                sources=sources, procedure=procedure,
                quarantined=bool(hit),
                quarantine_reason=f"injection screen: /{hit}/" if hit else "")
        except (ContractError, AttributeError, TypeError, ValueError) as exc:
            raise DistillationError(f"SchemaGuard: invalid entry from "
                                    f"distiller: {exc}")
        entries.append(entry)
    return entries
