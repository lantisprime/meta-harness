"""Small JSON-safe value objects for the remote development gateway."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class IssuedCredential:
    """A credential secret, returned exactly once by the issuance operation."""

    credential: str
    credential_id: str
    actor: str
    scopes: tuple[str, ...]
    expires_at: int

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["scopes"] = list(self.scopes)
        return result


@dataclass(frozen=True)
class ClaimGrant:
    claim_id: str
    card_id: str
    owner: str
    fencing_token: int
    revision: int
    expires_at: int
    task_bundle: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class InboxWriteResult:
    inserted: bool
    reordered: bool = False


@dataclass(frozen=True)
class OutboxRecord:
    id: int
    dedupe_key: str
    kind: str
    payload_json: str
    attempts: int
