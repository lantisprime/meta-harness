"""DiscoveryKnowledgeHub: append-only, scope-visible discovery evidence.

No artifact of any kind may self-grant `INSTRUCTION`/`VERIFIED_FACT` trust
through the ordinary `append` path; `NOTE`/`SKILL_CANDIDATE` are additionally
always untrusted candidates that can never carry an active lifecycle.
`REVIEWED_PROJECT` scope is not reachable at all in this MVP:
`promote_to_reviewed_project` is fail-closed-disabled (META-7 has no
external review broker to verify a review actually happened), so the scope
value and `review_receipt_id` field remain only as a typed seam for a future
real implementation. Nothing here mutates a stored artifact: supersession
always appends a new artifact naming its predecessor.

Callers of `query`/`read` must handle TWO distinct exception types (META-7
pre-commit fix brief #9, F8/MiniMax): `KnowledgeAccessError` (a `PermissionError`)
for a scope-visibility rejection (unknown/invisible artifact, unauthorized
cross-scope claim) and `KnowledgeError` (a `ValueError`) for a structural/
input rejection (unknown artifact_id, malformed evidence, policy violation).
`append`'s cross-scope write authorization failures raise `KnowledgeError`,
not `KnowledgeAccessError` — writes are input validation, not a visibility
decision over already-stored state.

Residual trust boundary (deliberately retained): the issuance verifier this
hub trusts (see `DiscoveryKnowledgeHub(issuance_verifier=...)`) only proves
that a given `DiscoveryAssignment` was actually issued by whatever issued
it — typically a real `CampaignSupervisor` via `make_issuance_verifier()`.
It does NOT defend against an in-process attacker with direct access to a
live supervisor's internals forging its bookkeeping, nor against a verifier
callable that itself lies; the MVP's security model is "no caller can widen
scope by mere self-assertion," not "no code in this process can ever lie."
"""
from __future__ import annotations

import itertools
from enum import Enum
from typing import Any, Callable, Literal, Sequence, Union

from pydantic import Field, ValidationError, model_validator

from metaharness.context import ContextTrust, Sensitivity, content_hash
from metaharness.discovery.models import (
    SHA256_PATTERN,
    DiscoveryAssignment,
    DiscoveryLineageReceipt,
    DiscoveryResourceReceipt,
    DiscoveryTerminalReceipt,
    FrozenModel,
    _self_verifying,
)

_SENSITIVITY_ORDER = {
    Sensitivity.PUBLIC: 0,
    Sensitivity.INTERNAL: 1,
    Sensitivity.RESTRICTED: 2,
    Sensitivity.SECRET: 3,
}
_SECRET_MARKERS = ("credentials", "password", "raw_secret", "secret", "token")
_UNTRUSTED_KIND_TRUSTS = frozenset({ContextTrust.UNTRUSTED_EVIDENCE, ContextTrust.GENERATED_SUMMARY})

# The closed set of discovery receipt types ordinary knowledge provenance may
# cite as evidence — the explicit in-scope types needed by tests/consumers,
# never an arbitrary structurally-duck-typed object.
_EVIDENCE_RECEIPT_TYPES = (DiscoveryLineageReceipt, DiscoveryResourceReceipt, DiscoveryTerminalReceipt)
EvidenceReceipt = Union[DiscoveryLineageReceipt, DiscoveryResourceReceipt, DiscoveryTerminalReceipt]


def _revalidate_evidence_receipt(receipt: Any) -> EvidenceReceipt:
    """Reject anything that isn't one of the closed, typed discovery receipt
    types (no structural/fabricated `_ReceiptLike` duck-typing), then
    revalidate it from its own JSON dump — `model_copy(update=...)` bypasses
    validation entirely, so a stale-hash object must fail here, not be
    trusted as-is."""

    for receipt_type in _EVIDENCE_RECEIPT_TYPES:
        if isinstance(receipt, receipt_type):
            try:
                return receipt_type.model_validate(receipt.model_dump(mode="json"))
            except ValidationError as exc:
                raise KnowledgeError(f"source evidence receipt failed self-hash revalidation: {exc}") from exc
    raise KnowledgeError(
        f"source evidence receipt of type {type(receipt).__name__!r} is not a recognized "
        "discovery receipt type (DiscoveryLineageReceipt/DiscoveryResourceReceipt/"
        "DiscoveryTerminalReceipt) — structural/fabricated objects are rejected"
    )


class KnowledgeError(ValueError):
    """A knowledge-hub write/read was rejected — fail closed, never guessed around."""


class KnowledgeAccessError(PermissionError):
    """A read/query was rejected by scope visibility rules."""


class DiscoveryKnowledgeKind(str, Enum):
    EVALUATED_ATTEMPT = "evaluated_attempt"
    NOTE = "note"
    SKILL_CANDIDATE = "skill_candidate"
    SYNTHESIS = "synthesis"
    CONNECTION = "connection"
    CONTRADICTION = "contradiction"
    OPEN_QUESTION = "open_question"


_UNTRUSTED_KINDS = frozenset({DiscoveryKnowledgeKind.NOTE, DiscoveryKnowledgeKind.SKILL_CANDIDATE})


class DiscoveryKnowledgeScope(str, Enum):
    PRIVATE = "private"
    LINEAGE = "lineage"
    ISLAND = "island"
    CAMPAIGN = "campaign"
    REVIEWED_PROJECT = "reviewed_project"


class DiscoveryKnowledgeLifecycle(str, Enum):
    CANDIDATE = "candidate"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"
    TOMBSTONED = "tombstoned"


class DiscoveryKnowledgeRequester(FrozenModel):
    schema_version: Literal[1] = 1
    creator_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    campaign_id: str = Field(min_length=1)
    lineage_id: str | None = Field(default=None, min_length=1)
    island_id: str | None = Field(default=None, min_length=1)


class DiscoveryKnowledgeArtifact(FrozenModel):
    schema_version: Literal[1] = 1
    artifact_id: str = Field(min_length=1)
    kind: DiscoveryKnowledgeKind
    project_id: str = Field(min_length=1)
    campaign_id: str = Field(min_length=1)
    lineage_id: str | None = Field(default=None, min_length=1)
    island_id: str | None = Field(default=None, min_length=1)
    creator_id: str = Field(min_length=1)
    content: str = Field(min_length=1)
    source_attempt_id: str | None = Field(default=None, min_length=1)
    source_evidence_receipt_ids: tuple[str, ...] = ()
    scope: DiscoveryKnowledgeScope
    trust: ContextTrust
    lifecycle: DiscoveryKnowledgeLifecycle
    sensitivity: Sensitivity
    supersedes: tuple[str, ...] = ()
    review_receipt_id: str | None = Field(default=None, min_length=1)
    sequence: int = Field(ge=0)
    artifact_hash: str = Field(default="", pattern=SHA256_PATTERN)

    @model_validator(mode="wrap")
    @classmethod
    def _verify_hash(cls, data: Any, handler: Callable[[Any], Any]) -> "DiscoveryKnowledgeArtifact":
        return _self_verifying(data, handler, "artifact_hash", "artifact_hash mismatch")

    @model_validator(mode="after")
    def _validate(self) -> "DiscoveryKnowledgeArtifact":
        if self.artifact_id in self.supersedes:
            raise ValueError("an artifact cannot supersede itself")
        if self.scope == DiscoveryKnowledgeScope.LINEAGE and self.lineage_id is None:
            raise ValueError("lineage-scoped artifacts require lineage_id")
        if self.scope == DiscoveryKnowledgeScope.ISLAND and self.island_id is None:
            raise ValueError("island-scoped artifacts require island_id")
        if self.scope == DiscoveryKnowledgeScope.REVIEWED_PROJECT and self.review_receipt_id is None:
            raise ValueError(
                "reviewed-project scope requires an external review_receipt_id "
                "(unavailable through the ordinary worker append path)"
            )
        if self.scope != DiscoveryKnowledgeScope.REVIEWED_PROJECT and self.review_receipt_id is not None:
            raise ValueError("review_receipt_id may only be set on reviewed-project scope artifacts")
        if self.kind in _UNTRUSTED_KINDS:
            if self.trust not in _UNTRUSTED_KIND_TRUSTS:
                raise ValueError(
                    f"{self.kind.value} artifacts are always untrusted candidates and "
                    "cannot claim instruction/verified_fact trust"
                )
            if self.lifecycle == DiscoveryKnowledgeLifecycle.ACTIVE:
                raise ValueError(
                    f"{self.kind.value} artifacts cannot carry an active lifecycle "
                    "(agent-authored notes/skills stay dormant candidates)"
                )
            if self.scope == DiscoveryKnowledgeScope.REVIEWED_PROJECT:
                raise ValueError(
                    f"{self.kind.value} artifacts cannot claim reviewed-project visibility"
                )
        return self


class DiscoveryKnowledgeSummary(FrozenModel):
    schema_version: Literal[1] = 1
    artifact_id: str = Field(min_length=1)
    kind: DiscoveryKnowledgeKind
    scope: DiscoveryKnowledgeScope
    trust: ContextTrust
    lifecycle: DiscoveryKnowledgeLifecycle
    sensitivity: Sensitivity
    content_hash: str = Field(pattern=SHA256_PATTERN)


class DiscoveryKnowledgeAppendReceipt(FrozenModel):
    schema_version: Literal[1] = 1
    receipt_id: str = Field(min_length=1)
    artifact_id: str = Field(min_length=1)
    campaign_id: str = Field(min_length=1)
    sequence: int = Field(ge=0)
    observed_at: int = Field(ge=0)
    receipt_hash: str = Field(default="", pattern=SHA256_PATTERN)

    @model_validator(mode="wrap")
    @classmethod
    def _verify_hash(cls, data: Any, handler: Callable[[Any], Any]) -> "DiscoveryKnowledgeAppendReceipt":
        return _self_verifying(data, handler, "receipt_hash", "receipt_hash mismatch")


class DiscoveryKnowledgeUseReceipt(FrozenModel):
    schema_version: Literal[1] = 1
    receipt_id: str = Field(min_length=1)
    campaign_id: str = Field(min_length=1)
    consumer_id: str = Field(min_length=1)
    query_stable_id: str = Field(pattern=SHA256_PATTERN)
    artifact_ids: tuple[str, ...] = ()
    sequence: int = Field(ge=0)
    observed_at: int = Field(ge=0)
    receipt_hash: str = Field(default="", pattern=SHA256_PATTERN)

    @model_validator(mode="wrap")
    @classmethod
    def _verify_hash(cls, data: Any, handler: Callable[[Any], Any]) -> "DiscoveryKnowledgeUseReceipt":
        return _self_verifying(data, handler, "receipt_hash", "receipt_hash mismatch")


IssuanceVerifier = Callable[[DiscoveryAssignment], bool]


def _revalidated_assignment(consumer_assignment: DiscoveryAssignment) -> DiscoveryAssignment | None:
    """Round-trip through JSON to reject a stale-hash (`model_copy`-mutated)
    assignment; returns None rather than raising so read-path callers can
    fail closed to invisible instead of an exception."""

    try:
        return DiscoveryAssignment.model_validate(consumer_assignment.model_dump(mode="json"))
    except ValidationError:
        return None


def _verify_scope_claim(
    *,
    issuance_verifier: IssuanceVerifier | None,
    consumer_assignment: DiscoveryAssignment | None,
    campaign_id: str,
    lineage_id: str | None,
) -> bool:
    """True only when `consumer_assignment` is self-hash-valid, ISSUANCE-
    VERIFIED by the hub's injected verifier (never merely self-hashed — a
    self-hashed `DiscoveryAssignment` proves integrity, not issuance; anyone
    with model access can mint one), AND matches the claimed campaign_id
    (and lineage_id, when given). No verifier configured means cross-scope
    (LINEAGE/CAMPAIGN) reads and writes are unavailable entirely — fail
    closed (META-7 pre-commit fix brief #9, F3)."""

    if issuance_verifier is None or consumer_assignment is None:
        return False
    try:
        assignment = DiscoveryAssignment.model_validate(consumer_assignment.model_dump(mode="json"))
    except ValidationError as exc:
        # A malformed/tampered (stale-hash) assignment is a harder failure
        # than "this genuine assignment just doesn't authorize the claim" —
        # raise rather than silently deny, so forged evidence is caught
        # loudly instead of blending into ordinary scope-mismatch denial.
        raise KnowledgeAccessError(f"consumer_assignment failed self-hash revalidation: {exc}") from exc
    if not issuance_verifier(assignment):
        return False
    if assignment.campaign_id != campaign_id:
        return False
    if lineage_id is not None and assignment.lineage_id != lineage_id:
        return False
    return True


def _visible(
    artifact: DiscoveryKnowledgeArtifact,
    requester: DiscoveryKnowledgeRequester,
    *,
    issuance_verifier: IssuanceVerifier | None,
    consumer_assignment: DiscoveryAssignment | None,
) -> bool:
    if artifact.project_id != requester.project_id:
        return False
    if artifact.scope == DiscoveryKnowledgeScope.REVIEWED_PROJECT:
        return artifact.review_receipt_id is not None
    if artifact.campaign_id != requester.campaign_id:
        return False
    if artifact.scope == DiscoveryKnowledgeScope.PRIVATE:
        # Same-creator visibility needs no cross-scope issuance proof —
        # private-scope reads/writes still work with no verifier configured.
        return artifact.creator_id == requester.creator_id
    if artifact.scope == DiscoveryKnowledgeScope.ISLAND:
        # Island membership has no independent issuer available in this MVP
        # (islands/migration execution is explicitly out of scope) -- a
        # caller-claimed island_id can never grant visibility.
        return False
    # CAMPAIGN and LINEAGE both require a verified-issued assignment
    # matching the claimed campaign_id (residual B: CAMPAIGN reads used to
    # trust a bare requester.campaign_id claim with no proof at all).
    campaign_authorized = _verify_scope_claim(
        issuance_verifier=issuance_verifier,
        consumer_assignment=consumer_assignment,
        campaign_id=requester.campaign_id,
        lineage_id=None,
    )
    if artifact.scope == DiscoveryKnowledgeScope.CAMPAIGN:
        return campaign_authorized
    if artifact.scope == DiscoveryKnowledgeScope.LINEAGE:
        if artifact.lineage_id is None or requester.lineage_id is None:
            return False
        lineage_authorized = _verify_scope_claim(
            issuance_verifier=issuance_verifier,
            consumer_assignment=consumer_assignment,
            campaign_id=requester.campaign_id,
            lineage_id=requester.lineage_id,
        )
        return lineage_authorized and artifact.lineage_id == requester.lineage_id
    return False


class DiscoveryKnowledgeHub:
    def __init__(
        self,
        *,
        project_id: str,
        max_sensitivity: Sensitivity = Sensitivity.RESTRICTED,
        clock: Callable[[], int] | None = None,
        id_source: Callable[[], str] | None = None,
        issuance_verifier: IssuanceVerifier | None = None,
    ) -> None:
        self._project_id = project_id
        self._max_sensitivity = max_sensitivity
        self._clock = clock or (lambda: 0)
        self._counter = itertools.count(0)
        self._id_source = id_source or (lambda: f"know-{next(self._counter):08x}")
        self._sequence = itertools.count(0)
        self._artifacts: dict[str, DiscoveryKnowledgeArtifact] = {}
        # Issuance verifier (META-7 pre-commit fix brief #9, F3): the ONLY
        # thing this hub trusts to distinguish a REAL, supervisor-issued
        # `DiscoveryAssignment` from one anyone with model access could mint
        # (self-hashing proves integrity, never issuance). No verifier
        # configured means cross-scope (LINEAGE/CAMPAIGN) reads and writes
        # are unavailable entirely; PRIVATE-scope reads/writes are
        # unaffected either way. See `CampaignSupervisor.make_issuance_verifier`.
        self._issuance_verifier = issuance_verifier

    def _next_sequence(self) -> int:
        return next(self._sequence)

    def _require_verified_write_assignment(
        self,
        consumer_assignment: DiscoveryAssignment | None,
        *,
        scope: DiscoveryKnowledgeScope,
        campaign_id: str,
        lineage_id: str | None,
    ) -> DiscoveryAssignment | None:
        """LINEAGE/CAMPAIGN-scoped writes require a verified-issued
        assignment matching the claimed campaign_id (and lineage_id, for
        LINEAGE) — never a bare caller-asserted string (kimi#2: append used
        to publish into a victim lineage on a bare string; kimi#5: an
        arbitrary campaign_id could be claimed for any cross-scope write).
        Returns the revalidated assignment (for reuse by the supersedes
        visibility check) or raises; PRIVATE/ISLAND scope writes are
        unaffected and return None without requiring anything."""

        if scope not in (DiscoveryKnowledgeScope.LINEAGE, DiscoveryKnowledgeScope.CAMPAIGN):
            return None
        if self._issuance_verifier is None:
            raise KnowledgeError(
                f"{scope.value}-scoped writes are unavailable: no issuance verifier is "
                "configured for this hub (fail closed)"
            )
        if consumer_assignment is None:
            raise KnowledgeError(
                f"{scope.value}-scoped writes require a consumer_assignment proving "
                f"verified issuance for campaign_id {campaign_id!r}"
            )
        assignment = _revalidated_assignment(consumer_assignment)
        if assignment is None:
            raise KnowledgeError("consumer_assignment failed self-hash revalidation")
        if not self._issuance_verifier(assignment):
            raise KnowledgeError("consumer_assignment is not a verified-issued assignment (fail closed)")
        if assignment.campaign_id != campaign_id:
            raise KnowledgeError(
                f"consumer_assignment campaign_id {assignment.campaign_id!r} does not match "
                f"the claimed campaign_id {campaign_id!r}"
            )
        if scope == DiscoveryKnowledgeScope.LINEAGE and assignment.lineage_id != lineage_id:
            raise KnowledgeError(
                f"consumer_assignment lineage_id {assignment.lineage_id!r} does not match "
                f"the claimed lineage_id {lineage_id!r}"
            )
        return assignment

    def append(
        self,
        *,
        artifact_id: str,
        kind: DiscoveryKnowledgeKind,
        project_id: str,
        campaign_id: str,
        creator_id: str,
        content: str,
        scope: DiscoveryKnowledgeScope,
        trust: ContextTrust,
        sensitivity: Sensitivity,
        lineage_id: str | None = None,
        island_id: str | None = None,
        source_attempt_id: str | None = None,
        source_evidence_receipts: Sequence[Any] = (),
        supersedes: Sequence[str] = (),
        consumer_assignment: DiscoveryAssignment | None = None,
    ) -> tuple[DiscoveryKnowledgeArtifact, DiscoveryKnowledgeAppendReceipt]:
        if project_id != self._project_id:
            raise KnowledgeError(
                f"cross-project append rejected: hub is scoped to {self._project_id!r}, "
                f"got {project_id!r}"
            )
        if artifact_id in self._artifacts:
            raise KnowledgeError(f"artifact_id {artifact_id!r} already exists (append-only)")
        if scope == DiscoveryKnowledgeScope.REVIEWED_PROJECT:
            raise KnowledgeError(
                "reviewed-project scope is not reachable through append(); "
                "META-7 has no external review broker seam, so this path is "
                "fail-closed-disabled entirely (see promote_to_reviewed_project)"
            )
        if trust in (ContextTrust.INSTRUCTION, ContextTrust.VERIFIED_FACT):
            raise KnowledgeError(
                f"ordinary append() can never self-grant {trust.value!r} trust for "
                f"any artifact kind (only externally reviewed evidence could ever "
                "carry it, and META-7 has no such path)"
            )
        if _SENSITIVITY_ORDER[sensitivity] > _SENSITIVITY_ORDER[self._max_sensitivity]:
            raise KnowledgeError(
                f"sensitivity {sensitivity.value!r} exceeds this hub's policy ceiling "
                f"{self._max_sensitivity.value!r} (fail closed)"
            )
        lowered = content.lower()
        if any(marker in lowered for marker in _SECRET_MARKERS) and sensitivity != Sensitivity.SECRET:
            raise KnowledgeError(
                "content looks secret-shaped but sensitivity is not SECRET (fail closed)"
            )

        # LINEAGE/CAMPAIGN-scoped writes require a verified-issued
        # assignment matching the claimed campaign_id/lineage_id (META-7
        # pre-commit fix brief #9, F3) — never a bare caller-asserted
        # string. Reused below for the supersedes visibility check so a
        # write's own supersede-authority is judged by the SAME verified
        # identity, not a second, separately-asserted one.
        verified_write_assignment = self._require_verified_write_assignment(
            consumer_assignment, scope=scope, campaign_id=campaign_id, lineage_id=lineage_id
        )

        new_write_requester = DiscoveryKnowledgeRequester(
            creator_id=creator_id,
            project_id=project_id,
            campaign_id=campaign_id,
            lineage_id=lineage_id,
            island_id=island_id,
        )
        for source_id in supersedes:
            prior = self._artifacts.get(source_id)
            if prior is None:
                raise KnowledgeError(f"supersedes references unknown artifact {source_id!r} (orphan)")
            if not _visible(
                prior,
                new_write_requester,
                issuance_verifier=self._issuance_verifier,
                consumer_assignment=verified_write_assignment,
            ):
                raise KnowledgeError(
                    f"supersedes references artifact {source_id!r}, which is not visible "
                    "to a requester constructed from this write's own "
                    "project/campaign/creator/lineage/island scope"
                )

        revalidated_receipts = [_revalidate_evidence_receipt(receipt) for receipt in source_evidence_receipts]
        for receipt in revalidated_receipts:
            if receipt.campaign_id != campaign_id:
                raise KnowledgeError(
                    f"source evidence receipt {receipt.receipt_id!r} belongs to a foreign "
                    "campaign (cross-campaign leakage)"
                )
            receipt_lineage_id = getattr(receipt, "lineage_id", None)
            if (
                receipt_lineage_id is not None
                and scope in (DiscoveryKnowledgeScope.PRIVATE, DiscoveryKnowledgeScope.LINEAGE)
                and receipt_lineage_id != lineage_id
            ):
                raise KnowledgeError(
                    f"source evidence receipt {receipt.receipt_id!r} belongs to a foreign "
                    "lineage (cross-lineage leakage)"
                )

        lifecycle = DiscoveryKnowledgeLifecycle.CANDIDATE

        artifact = DiscoveryKnowledgeArtifact(
            artifact_id=artifact_id,
            kind=kind,
            project_id=project_id,
            campaign_id=campaign_id,
            lineage_id=lineage_id,
            island_id=island_id,
            creator_id=creator_id,
            content=content,
            source_attempt_id=source_attempt_id,
            source_evidence_receipt_ids=tuple(r.receipt_id for r in revalidated_receipts),
            scope=scope,
            trust=trust,
            lifecycle=lifecycle,
            sensitivity=sensitivity,
            supersedes=tuple(supersedes),
            sequence=self._next_sequence(),
        )

        # Precompute and validate the append receipt BEFORE publishing any
        # query-visible state: an injected id_source/clock port that raises,
        # or that yields a value the receipt's own constraints reject, must
        # fail closed before `self._artifacts` is mutated — not after (a
        # half-published artifact would otherwise become permanently
        # unrecoverable under append-only, since a retry with the same
        # artifact_id would be rejected as a duplicate).
        receipt = DiscoveryKnowledgeAppendReceipt(
            receipt_id=self._id_source(),
            artifact_id=artifact_id,
            campaign_id=campaign_id,
            sequence=artifact.sequence,
            observed_at=self._clock(),
        )
        self._artifacts[artifact_id] = artifact
        return artifact, receipt

    def promote_to_reviewed_project(
        self,
        *,
        artifact_id: str,
        new_artifact_id: str,
        review_receipt_id: str,
        promoter_id: str,
    ) -> tuple[DiscoveryKnowledgeArtifact, DiscoveryKnowledgeAppendReceipt]:
        """Fail-closed-disabled (META-7 pre-commit fix brief #9).

        META-7 explicitly excludes promotion. The prior implementation
        accepted an arbitrary `review_receipt_id`/`promoter_id` string with
        no verification that any review had actually happened — a fake
        verifier that could mint REVIEWED_PROJECT visibility on say-so.
        `DiscoveryKnowledgeScope.REVIEWED_PROJECT` and
        `DiscoveryKnowledgeArtifact.review_receipt_id` remain as the typed
        seam a real external review broker could target later; this method
        unconditionally refuses until that broker exists.
        """

        raise KnowledgeError(
            "promote_to_reviewed_project is fail-closed-disabled: META-7 has "
            "no external review broker to verify review_receipt_id against, "
            "so reviewed-project visibility can never be minted from this hub"
        )

    def query(
        self,
        *,
        requester: DiscoveryKnowledgeRequester,
        kinds: Sequence[DiscoveryKnowledgeKind] | None = None,
        consumer_assignment: DiscoveryAssignment | None = None,
    ) -> tuple[tuple[DiscoveryKnowledgeSummary, ...], DiscoveryKnowledgeUseReceipt]:
        kind_filter = tuple(sorted({k.value for k in kinds})) if kinds else ()
        visible = [
            artifact
            for artifact in self._artifacts.values()
            if _visible(
                artifact, requester, issuance_verifier=self._issuance_verifier, consumer_assignment=consumer_assignment
            )
            and (not kind_filter or artifact.kind.value in kind_filter)
        ]
        visible.sort(key=lambda a: a.artifact_id)
        summaries = tuple(
            DiscoveryKnowledgeSummary(
                artifact_id=a.artifact_id,
                kind=a.kind,
                scope=a.scope,
                trust=a.trust,
                lifecycle=a.lifecycle,
                sensitivity=a.sensitivity,
                content_hash=content_hash(a.content),
            )
            for a in visible
        )
        query_stable_id = content_hash(
            {
                "requester": requester.model_dump(mode="json"),
                "kinds": list(kind_filter),
            }
        )
        receipt = DiscoveryKnowledgeUseReceipt(
            receipt_id=self._id_source(),
            campaign_id=requester.campaign_id,
            consumer_id=requester.creator_id,
            query_stable_id=query_stable_id,
            artifact_ids=tuple(a.artifact_id for a in visible),
            sequence=self._next_sequence(),
            observed_at=self._clock(),
        )
        return summaries, receipt

    def read(
        self,
        artifact_id: str,
        *,
        requester: DiscoveryKnowledgeRequester,
        consumer_assignment: DiscoveryAssignment | None = None,
    ) -> tuple[DiscoveryKnowledgeArtifact, DiscoveryKnowledgeUseReceipt]:
        artifact = self._artifacts.get(artifact_id)
        if artifact is None:
            raise KnowledgeError(f"unknown artifact {artifact_id!r}")
        if not _visible(
            artifact, requester, issuance_verifier=self._issuance_verifier, consumer_assignment=consumer_assignment
        ):
            raise KnowledgeAccessError(
                f"artifact {artifact_id!r} is not visible to requester {requester.creator_id!r} "
                f"(scope={artifact.scope.value})"
            )
        query_stable_id = content_hash({"read": artifact_id, "requester": requester.model_dump(mode="json")})
        receipt = DiscoveryKnowledgeUseReceipt(
            receipt_id=self._id_source(),
            campaign_id=requester.campaign_id,
            consumer_id=requester.creator_id,
            query_stable_id=query_stable_id,
            artifact_ids=(artifact_id,),
            sequence=self._next_sequence(),
            observed_at=self._clock(),
        )
        return artifact, receipt
