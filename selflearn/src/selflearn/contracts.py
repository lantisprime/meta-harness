"""Frozen contract value objects exchanged between selflearn modules.

These are the only things modules pass to each other; no module imports
another module's internals. Mutation happens via ``dataclasses.replace`` —
the objects themselves stay immutable so a contract can never be edited in
flight by a downstream consumer.

Design source: docs/self-learning-specialist-agents-plan.md (meta-harness),
including the simulation findings of 2026-07-17 (``TaskOutcome.step_id``,
schema-tolerant sources, corroboration independence by registrable domain).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

ENTRY_KINDS = ("knowledge", "skill", "workflow")
ENTRY_STATUSES = ("candidate", "published", "deprecated")
SOURCE_TIERS = ("official", "primary", "community", "unknown")
PROBE_KINDS = ("recall", "application", "skill", "golden_run")
CHECK_KINDS = ("deterministic", "judge", "execution")
EXTRACTIONS = ("text", "vision")
GAP_KINDS = ("coverage", "quality", "staleness")
VERDICTS = ("pass", "fail")


class ContractError(ValueError):
    """A contract object was constructed with invalid data. Always loud."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


@dataclass(frozen=True)
class SourceRef:
    """A reference to acquire: URL, file path URI, or plugin-specific locator."""

    uri: str
    hint: str = ""

    def __post_init__(self) -> None:
        _require(bool(self.uri), "SourceRef.uri must be non-empty")


@dataclass(frozen=True)
class Provenance:
    """Where a SourceDocument came from, exactly."""

    url: str
    fetched_at: str
    sha256: str
    plugin: str
    plugin_version: str
    locator: str = ""

    def __post_init__(self) -> None:
        _require(bool(self.url), "Provenance.url must be non-empty")
        _require(len(self.sha256) == 64, "Provenance.sha256 must be a hex sha256")
        _require(bool(self.plugin), "Provenance.plugin must be non-empty")


@dataclass(frozen=True)
class Asset:
    """A non-text artifact (image) tagged for the vision extraction path."""

    kind: str                 # figure | chart | equation
    ref: str                  # path or URI of the stored image
    transcript_context: str = ""

    def __post_init__(self) -> None:
        _require(self.kind in ("figure", "chart", "equation"),
                 f"Asset.kind {self.kind!r} invalid")


@dataclass(frozen=True)
class SourceDocument:
    """The normalized envelope every acquisition plugin must emit."""

    ref: SourceRef
    blocks: tuple[str, ...]
    chunks: tuple[str, ...]
    assets: tuple[Asset, ...]
    provenance: Provenance
    tier: str = "unknown"

    def __post_init__(self) -> None:
        _require(self.tier in SOURCE_TIERS, f"tier {self.tier!r} invalid")
        _require(bool(self.blocks) or bool(self.chunks) or bool(self.assets),
                 "SourceDocument must carry text blocks, chunks, or assets")


@dataclass(frozen=True)
class EntrySource:
    """A citation on an entry: one fetched source with its evidence tier."""

    url: str
    fetched_at: str
    sha256: str
    tier: str
    locator: str = ""         # timestamp range / page number (simulation finding)

    def __post_init__(self) -> None:
        _require(self.tier in SOURCE_TIERS, f"tier {self.tier!r} invalid")

    @property
    def domain(self) -> str:
        """Registrable-domain key for corroboration independence
        (simulation finding 2: independence = distinct registrable domains)."""
        if "://" in self.url:
            return self.url.split("/")[2].lower()
        return self.url.split("/")[0].lower()


@dataclass(frozen=True)
class ProcedureStep:
    """One step of a workflow-kind entry's machine-readable procedure."""

    id: str
    objective: str
    task_type: str
    tools: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()
    check: tuple[tuple[str, Any], ...] = ()   # frozen key/value pairs

    def __post_init__(self) -> None:
        _require(bool(self.id), "ProcedureStep.id must be non-empty")
        _require(bool(self.objective), "ProcedureStep.objective must be non-empty")

    def check_dict(self) -> dict[str, Any]:
        return dict(self.check)


@dataclass(frozen=True)
class CandidateEntry:
    """A distilled entry awaiting verification. Not knowledge yet."""

    id: str
    pack: str
    kind: str
    body: str
    claims: tuple[str, ...]
    sources: tuple[EntrySource, ...]
    topic: str
    task_types: tuple[str, ...] = ()
    procedure: tuple[ProcedureStep, ...] = ()
    skill_check: tuple[tuple[str, Any], ...] = ()
    extraction: str = "text"
    quarantined: bool = False
    quarantine_reason: str = ""

    def __post_init__(self) -> None:
        _require(bool(self.id), "CandidateEntry.id must be non-empty")
        _require(bool(self.pack), "CandidateEntry.pack must be non-empty")
        _require(self.kind in ENTRY_KINDS, f"kind {self.kind!r} invalid")
        _require(bool(self.body), "CandidateEntry.body must be non-empty")
        _require(bool(self.sources), "CandidateEntry needs at least one source")
        _require(self.extraction in EXTRACTIONS,
                 f"extraction {self.extraction!r} invalid")
        if self.kind == "workflow":
            _require(bool(self.procedure), "workflow entry needs a procedure")
            seen: set[str] = set()
            for step in self.procedure:
                for dep in step.depends_on:
                    _require(dep in seen,
                             f"step {step.id!r} depends on {dep!r} which is not "
                             "an earlier step")
                seen.add(step.id)

    def independent_domains(self) -> set[str]:
        return {s.domain for s in self.sources}


@dataclass(frozen=True)
class Probe:
    """A generated eval item derived from an entry."""

    id: str
    entry_id: str
    kind: str
    question: str
    expected: str
    check_kind: str
    validated: bool = False
    validated_by: str = ""

    def __post_init__(self) -> None:
        _require(self.kind in PROBE_KINDS, f"probe kind {self.kind!r} invalid")
        _require(self.check_kind in CHECK_KINDS,
                 f"check_kind {self.check_kind!r} invalid")


@dataclass(frozen=True)
class PublishDecision:
    """The verification module's verdict on a candidate entry."""

    entry_id: str
    publish: bool
    basis: tuple[str, ...]
    identity_basis: str
    strict_mode: bool = False

    def __post_init__(self) -> None:
        _require(bool(self.basis), "PublishDecision.basis must state its grounds")


@dataclass(frozen=True)
class GapSignal:
    """Learning-module output: a proposed (never auto-run) acquisition topic."""

    pack: str
    topic: str
    kind: str
    evidence: str

    def __post_init__(self) -> None:
        _require(self.kind in GAP_KINDS, f"gap kind {self.kind!r} invalid")


@dataclass(frozen=True)
class TaskOutcome:
    """The host-supplied learning event: one externally verified task result.

    ``step_id`` (simulation finding 4) lets workflow-entry implication target
    the failing step's definition instead of the whole entry. ``topic`` is
    assigned by the host's deterministic labeler (simulation finding 3); an
    empty topic means 'unlabeled' and is excluded from gap joins.
    """

    task_id: str
    task_type: str
    topic: str
    verdict: str
    injected: tuple[str, ...]
    applied: tuple[str, ...] = ()
    failure_mode: str = ""
    implicated: tuple[str, ...] = ()
    step_id: str = ""

    def __post_init__(self) -> None:
        _require(self.verdict in VERDICTS, f"verdict {self.verdict!r} invalid")
        _require(not (self.verdict == "pass" and self.implicated),
                 "a passing outcome cannot implicate entries")
