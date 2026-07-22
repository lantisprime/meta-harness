"""RoleContextPolicy: role-specific, receipt-backed context source selection.

Builds a frozen `DiscoveryRoleContextManifest` recording every considered
source and whether it was included, withheld, or omitted — never a rendered
prompt. Nothing here occupies a system/tool/response instruction slot (that
is `metaharness.context.live`'s job, and role views are explicitly not wired
into it); this module only decides and receipts *which* `ContextSourceRef`s
a role may see.

- Explorer: fresh conversation (no inherited `WORKING_MEMORY`/`PARENT_LINEAGE`),
  goal/constraints, a selected parent worktree, scoped memory, and a compact
  novelty briefing whose population findings must carry an explicit
  cross-lineage use receipt.
- Optimizer: only the direct parent's worktree/history (kind's `scope.lineage_id`
  must be the optimizer's own lineage or its declared direct parent); never
  `POPULATION_FINDING` and never a cross-lineage use receipt.
- Scheduler: only compact `POPULATION_FINDING`/`EVALUATOR_RECEIPT` summaries;
  never raw conversation or worktree-shaped sources.

`cross_lineage_receipt` derives its consumer identity from an issuance-
VERIFIED `DiscoveryAssignment` (META-7 pre-commit fix brief #9, F3), never a
bare parameter. KNOWN LIMITATION, deliberately scoped out of this pass:
`compose_explorer_context`/`compose_optimizer_context` still take bare
`project_id`/`campaign_id`/`lineage_id`/`parent_lineage_id` parameters
rather than deriving them from a verified assignment. A caller can still
assert "I am lineage X" to `compose_explorer_context` without independent
proof (bounded by the fact that any resulting manifest's cross-lineage
receipts, if any, ARE now tied to real issuance via `cross_lineage_receipt`,
and by `optimizer`'s own real-source lineage checks against the CLAIMED
lineage_id/parent_lineage_id — so the residual risk is a mis-labeled
manifest, not a widened one, unless the caller also controls what
`ContextSourceRef`s exist). Closing this fully requires reworking both
composers' signatures and the ~30 tests built around bare-parameter
composition; out of proportion for this batch (see brief-9 report,
disposition ACCEPT-WITH-MOD on F3's composer clause). Recommended as a
dedicated follow-up.
"""
from __future__ import annotations

import itertools
from enum import Enum
from typing import Any, Callable, Literal, Sequence

from pydantic import Field, ValidationError, model_validator

from metaharness.context import ContextSourceKind, ContextTrust
from metaharness.discovery.models import (
    SHA256_PATTERN,
    DiscoveryAssignment,
    DiscoveryRole,
    FrozenModel,
    _self_verifying,
)
from metaharness.context.models import ContextSourceRef


class DiscoveryContextRole(str, Enum):
    EXPLORER = "explorer"
    OPTIMIZER = "optimizer"
    SCHEDULER = "scheduler"


class ContextDecision(str, Enum):
    INCLUDED = "included"
    WITHHELD = "withheld"
    OMITTED = "omitted"


_UNTRUSTED_SOURCE_TRUSTS = frozenset({ContextTrust.UNTRUSTED_EVIDENCE, ContextTrust.GENERATED_SUMMARY})

_EXPLORER_FORBIDDEN_INCLUDED_KINDS = frozenset(
    {ContextSourceKind.WORKING_MEMORY, ContextSourceKind.PARENT_LINEAGE}
)
_OPTIMIZER_FORBIDDEN_INCLUDED_KINDS = frozenset(
    {ContextSourceKind.WORKING_MEMORY, ContextSourceKind.POPULATION_FINDING}
)
_OPTIMIZER_LINEAGE_SCOPED_KINDS = frozenset(
    {ContextSourceKind.CANDIDATE_WORKTREE, ContextSourceKind.PARENT_LINEAGE}
)
_SCHEDULER_ALLOWED_INCLUDED_KINDS = frozenset(
    {ContextSourceKind.POPULATION_FINDING, ContextSourceKind.EVALUATOR_RECEIPT}
)
_ASSIGNMENT_ROLE_TO_CONTEXT_ROLE = {
    DiscoveryRole.EXPLORER: DiscoveryContextRole.EXPLORER,
    DiscoveryRole.OPTIMIZER: DiscoveryContextRole.OPTIMIZER,
}

IssuanceVerifier = Callable[[DiscoveryAssignment], bool]


class RoleContextError(ValueError):
    """A role-context composition was rejected — fail closed, never guessed around."""


class DiscoveryContextEntry(FrozenModel):
    schema_version: Literal[1] = 1
    source: ContextSourceRef
    decision: ContextDecision
    reason: str = Field(min_length=1)


class DiscoveryCrossLineageUseReceipt(FrozenModel):
    schema_version: Literal[1] = 1
    receipt_id: str = Field(min_length=1)
    role: DiscoveryContextRole
    project_id: str = Field(min_length=1)
    campaign_id: str = Field(min_length=1)
    consumer_lineage_id: str = Field(min_length=1)
    source_lineage_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    justification: str = Field(min_length=1)
    sequence: int = Field(ge=0)
    receipt_hash: str = Field(default="", pattern=SHA256_PATTERN)

    @model_validator(mode="wrap")
    @classmethod
    def _verify_hash(cls, data: Any, handler: Callable[[Any], Any]) -> "DiscoveryCrossLineageUseReceipt":
        return _self_verifying(data, handler, "receipt_hash", "receipt_hash mismatch")

    @model_validator(mode="after")
    def _validate(self) -> "DiscoveryCrossLineageUseReceipt":
        if self.consumer_lineage_id == self.source_lineage_id:
            raise ValueError("a cross-lineage use receipt requires two distinct lineages")
        return self


class DiscoveryRoleContextManifest(FrozenModel):
    schema_version: Literal[1] = 1
    manifest_id: str = Field(min_length=1)
    role: DiscoveryContextRole
    project_id: str = Field(min_length=1)
    campaign_id: str = Field(min_length=1)
    lineage_id: str | None = Field(default=None, min_length=1)
    entries: tuple[DiscoveryContextEntry, ...]
    cross_lineage_receipts: tuple[DiscoveryCrossLineageUseReceipt, ...] = ()
    sequence: int = Field(ge=0)
    manifest_hash: str = Field(default="", pattern=SHA256_PATTERN)

    @model_validator(mode="wrap")
    @classmethod
    def _verify_hash(cls, data: Any, handler: Callable[[Any], Any]) -> "DiscoveryRoleContextManifest":
        return _self_verifying(data, handler, "manifest_hash", "manifest_hash mismatch")

    @model_validator(mode="after")
    def _validate(self) -> "DiscoveryRoleContextManifest":
        source_ids = [entry.source.source_id for entry in self.entries]
        if len(set(source_ids)) != len(source_ids):
            raise ValueError("each source_id may appear at most once in a role-context manifest")

        # Every entry — included, withheld, or omitted; any kind; any role;
        # receipted or not — must be bound to the manifest's own project. A
        # cross-lineage receipt excuses crossing a LINEAGE boundary, never a
        # PROJECT boundary.
        for entry in self.entries:
            if entry.source.scope.project_id != self.project_id:
                raise ValueError(
                    f"entry {entry.source.source_id!r} has scope.project_id "
                    f"{entry.source.scope.project_id!r}, which does not match the "
                    f"manifest project_id {self.project_id!r} (foreign project)"
                )

        receipt_source_ids = [receipt.source_id for receipt in self.cross_lineage_receipts]
        if len(set(receipt_source_ids)) != len(receipt_source_ids):
            raise ValueError("cross_lineage_receipts must not name the same source_id twice (duplicate receipt)")

        # A cross-lineage use receipt is only meaningful for a manifest with a
        # single consumer lineage (explorer) pulling in a finding that
        # originated elsewhere. The scheduler has no single consumer lineage
        # (lineage_id is None) — population-wide findings are its native
        # input, not a cross-lineage exception.
        if self.lineage_id is not None:
            included_foreign_findings = {
                entry.source.source_id
                for entry in self.entries
                if entry.decision == ContextDecision.INCLUDED
                and entry.source.kind == ContextSourceKind.POPULATION_FINDING
                and entry.source.scope.lineage_id not in (None, self.lineage_id)
            }
        else:
            included_foreign_findings = set()

        receipted_source_ids = set(receipt_source_ids)
        missing = included_foreign_findings - receipted_source_ids
        if missing:
            raise ValueError(
                f"included population findings without a cross-lineage use receipt: {sorted(missing)}"
            )
        surplus = receipted_source_ids - included_foreign_findings
        if surplus:
            raise ValueError(
                "cross_lineage_receipts reference source_id(s) that are not an "
                f"included foreign population finding (orphan/surplus receipt): {sorted(surplus)}"
            )

        entries_by_source_id = {entry.source.source_id: entry for entry in self.entries}
        for receipt in self.cross_lineage_receipts:
            entry = entries_by_source_id[receipt.source_id]
            if receipt.role != self.role:
                raise ValueError(
                    f"cross-lineage receipt for {receipt.source_id!r} names role "
                    f"{receipt.role.value!r}, expected {self.role.value!r} (wrong role)"
                )
            if receipt.consumer_lineage_id != self.lineage_id:
                raise ValueError(
                    f"cross-lineage receipt for {receipt.source_id!r} names "
                    f"consumer_lineage_id {receipt.consumer_lineage_id!r}, expected "
                    f"{self.lineage_id!r} (wrong consumer)"
                )
            if receipt.source_lineage_id != entry.source.scope.lineage_id:
                raise ValueError(
                    f"cross-lineage receipt for {receipt.source_id!r} names "
                    f"source_lineage_id {receipt.source_lineage_id!r}, but the source's "
                    f"actual scope.lineage_id is {entry.source.scope.lineage_id!r} "
                    "(wrong source lineage)"
                )
            if receipt.project_id != self.project_id or entry.source.scope.project_id != self.project_id:
                raise ValueError(
                    f"cross-lineage receipt for {receipt.source_id!r} does not match "
                    f"the manifest project_id {self.project_id!r} (foreign project)"
                )
            if receipt.campaign_id != self.campaign_id:
                raise ValueError(
                    f"cross-lineage receipt for {receipt.source_id!r} names campaign_id "
                    f"{receipt.campaign_id!r}, expected {self.campaign_id!r} (foreign campaign)"
                )

        for entry in self.entries:
            if (
                entry.decision == ContextDecision.INCLUDED
                and entry.source.kind == ContextSourceKind.POPULATION_FINDING
                and entry.source.trust not in _UNTRUSTED_SOURCE_TRUSTS
            ):
                raise ValueError(
                    "included population-finding sources must carry untrusted_evidence "
                    "or generated_summary trust, never instruction/verified_fact"
                )
        return self


class RoleContextPolicy:
    def __init__(
        self,
        *,
        id_source: Callable[[], str] | None = None,
        issuance_verifier: IssuanceVerifier | None = None,
    ) -> None:
        self._counter = itertools.count(0)
        self._id_source = id_source or (lambda: f"rcm-{next(self._counter):08x}")
        self._sequence = itertools.count(0)
        # Issuance verifier (META-7 pre-commit fix brief #9, F3): a
        # self-hashed `DiscoveryAssignment` proves integrity, not issuance
        # — anyone with model access can mint one. No verifier configured
        # means `cross_lineage_receipt` is unavailable entirely (fail
        # closed). See `CampaignSupervisor.make_issuance_verifier`.
        self._issuance_verifier = issuance_verifier

    def _next_sequence(self) -> int:
        return next(self._sequence)

    def cross_lineage_receipt(
        self,
        *,
        consumer_assignment: DiscoveryAssignment,
        project_id: str,
        source: ContextSourceRef,
        justification: str,
    ) -> DiscoveryCrossLineageUseReceipt:
        """Mint a cross-lineage use receipt from INDEPENDENTLY ISSUED AND
        VERIFIED identity only (META-7 pre-commit fix brief #9, F3,
        extending brief #8's P1-2): the consumer's own lineage/campaign/role
        are derived from a real, freshly revalidated, ISSUANCE-VERIFIED
        `DiscoveryAssignment` — never a bare caller-asserted string, and
        never a merely self-hashed one either (that only proves integrity,
        not that the supervisor actually issued it) — and the source's
        lineage/id are derived directly from the `ContextSourceRef` actually
        being cited. Neither can be used to claim a scope wider than what
        the assignment and source themselves attest to."""

        try:
            assignment = DiscoveryAssignment.model_validate(consumer_assignment.model_dump(mode="json"))
        except ValidationError as exc:
            raise RoleContextError(f"consumer_assignment failed self-hash revalidation: {exc}") from exc
        if self._issuance_verifier is None:
            raise RoleContextError(
                "cross_lineage_receipt is unavailable: no issuance verifier is configured "
                "for this policy (fail closed)"
            )
        if not self._issuance_verifier(assignment):
            raise RoleContextError("consumer_assignment is not a verified-issued assignment (fail closed)")
        role = _ASSIGNMENT_ROLE_TO_CONTEXT_ROLE.get(assignment.role)
        if role is None:
            raise RoleContextError(
                f"assignment role {assignment.role!r} cannot mint a cross-lineage use receipt"
            )
        if source.scope.lineage_id is None:
            raise RoleContextError("source has no lineage_id to attribute a cross-lineage receipt to")
        return DiscoveryCrossLineageUseReceipt(
            receipt_id=self._id_source(),
            role=role,
            project_id=project_id,
            campaign_id=assignment.campaign_id,
            consumer_lineage_id=assignment.lineage_id,
            source_lineage_id=source.scope.lineage_id,
            source_id=source.source_id,
            justification=justification,
            sequence=self._next_sequence(),
        )

    def compose_explorer_context(
        self,
        *,
        project_id: str,
        campaign_id: str,
        lineage_id: str,
        entries: Sequence[DiscoveryContextEntry],
        cross_lineage_receipts: Sequence[DiscoveryCrossLineageUseReceipt] = (),
    ) -> DiscoveryRoleContextManifest:
        for entry in entries:
            if entry.decision == ContextDecision.INCLUDED and entry.source.kind in _EXPLORER_FORBIDDEN_INCLUDED_KINDS:
                raise RoleContextError(
                    f"explorer context cannot include {entry.source.kind.value} "
                    "(fresh conversation: never inherited/raw conversation history)"
                )
        return self._build(
            DiscoveryContextRole.EXPLORER, project_id, campaign_id, lineage_id, entries, cross_lineage_receipts
        )

    def compose_optimizer_context(
        self,
        *,
        project_id: str,
        campaign_id: str,
        lineage_id: str,
        parent_lineage_id: str | None,
        entries: Sequence[DiscoveryContextEntry],
    ) -> DiscoveryRoleContextManifest:
        for entry in entries:
            if entry.decision != ContextDecision.INCLUDED:
                continue
            if entry.source.kind in _OPTIMIZER_FORBIDDEN_INCLUDED_KINDS:
                raise RoleContextError(
                    f"optimizer context cannot include {entry.source.kind.value} "
                    "(direct-parent worktree/history only)"
                )
            if entry.source.kind in _OPTIMIZER_LINEAGE_SCOPED_KINDS:
                allowed_lineages = {lineage_id, parent_lineage_id}
                if entry.source.scope.lineage_id not in allowed_lineages:
                    raise RoleContextError(
                        f"optimizer context source {entry.source.source_id!r} belongs to lineage "
                        f"{entry.source.scope.lineage_id!r}, not the direct parent "
                        f"{parent_lineage_id!r} (no sibling/cousin/ambient campaign transcripts)"
                    )
        return self._build(
            DiscoveryContextRole.OPTIMIZER, project_id, campaign_id, lineage_id, entries, cross_lineage_receipts=()
        )

    def compose_scheduler_context(
        self,
        *,
        project_id: str,
        campaign_id: str,
        entries: Sequence[DiscoveryContextEntry],
    ) -> DiscoveryRoleContextManifest:
        for entry in entries:
            if entry.decision != ContextDecision.INCLUDED:
                continue
            if entry.source.kind not in _SCHEDULER_ALLOWED_INCLUDED_KINDS:
                raise RoleContextError(
                    f"scheduler context cannot include {entry.source.kind.value} "
                    "(compact population summaries/receipts only, never raw "
                    "conversations or worktree bytes)"
                )
        return self._build(
            DiscoveryContextRole.SCHEDULER, project_id, campaign_id, None, entries, cross_lineage_receipts=()
        )

    def _build(
        self,
        role: DiscoveryContextRole,
        project_id: str,
        campaign_id: str,
        lineage_id: str | None,
        entries: Sequence[DiscoveryContextEntry],
        cross_lineage_receipts: Sequence[DiscoveryCrossLineageUseReceipt],
    ) -> DiscoveryRoleContextManifest:
        return DiscoveryRoleContextManifest(
            manifest_id=self._id_source(),
            role=role,
            project_id=project_id,
            campaign_id=campaign_id,
            lineage_id=lineage_id,
            entries=tuple(entries),
            cross_lineage_receipts=tuple(cross_lineage_receipts),
            sequence=self._next_sequence(),
        )
