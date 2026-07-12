"""Worker registry: which workers the orchestrator knows, and which key vouches
for each one.

Registration is a challenge-response ceremony, the same shape as proving you own
an SSH key: the registry hands out a one-time nonce, the worker signs
{worker_id, public_key, nonce} with its private key, and the registry admits the
worker only if that signature verifies under the presented public key. From then
on, any message claiming to come from that worker can be checked against the
registered key — authenticity, exactly like a verified commit.
"""
from __future__ import annotations

import secrets
import time
from typing import Any, Optional

from pydantic import BaseModel, Field

from metaharness.identity.canonical import canonical_bytes
from metaharness.identity.keys import verify


class WorkerRecord(BaseModel):
    """One admitted worker. `public_key_b64` is the identity anchor."""

    worker_id: str
    display_name: str = ""
    public_key_b64: str
    tiers: list[str] = Field(default_factory=list)
    task_types: list[str] = Field(default_factory=list)
    roles: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    host: str = ""
    active: bool = True
    registered_at: float = 0.0
    key_rotations: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class RegistrationChallenge(BaseModel):
    worker_id: str
    nonce: str
    issued_at: float


def registration_payload(worker_id: str, public_key_b64: str, nonce: str) -> bytes:
    """The exact bytes a worker signs to complete registration."""
    return canonical_bytes(
        {"kind": "registration", "worker_id": worker_id, "public_key": public_key_b64, "nonce": nonce}
    )


def rotation_payload(worker_id: str, new_public_key_b64: str) -> bytes:
    """The exact bytes a worker signs (with its *current* key) to rotate to a new key."""
    return canonical_bytes(
        {"kind": "key_rotation", "worker_id": worker_id, "new_public_key": new_public_key_b64}
    )


class RegistryError(Exception):
    """Registration or lookup failed for a stated reason."""


class WorkerRegistry:
    """In-memory registry of admitted workers.

    Flow: `begin_registration` → worker signs `registration_payload` →
    `complete_registration`. Nonces are single-use and expire, so a captured
    registration message can't be replayed later.
    """

    def __init__(self, challenge_ttl_s: float = 300.0) -> None:
        self._workers: dict[str, WorkerRecord] = {}
        self._challenges: dict[str, RegistrationChallenge] = {}
        self._challenge_ttl_s = challenge_ttl_s

    # -- registration ceremony -------------------------------------------------

    def begin_registration(self, worker_id: str) -> RegistrationChallenge:
        existing = self._workers.get(worker_id)
        if existing is not None and existing.active:
            raise RegistryError(f"worker {worker_id!r} is already registered")
        # a retired (deactivated) id may be re-admitted with a fresh key: the
        # ceremony runs again in full, and the old record's rotation count
        # carries over so re-admission stays visible in the audit trail
        challenge = RegistrationChallenge(
            worker_id=worker_id, nonce=secrets.token_hex(16), issued_at=time.time()
        )
        self._challenges[worker_id] = challenge
        return challenge

    def complete_registration(
        self,
        worker_id: str,
        public_key_b64: str,
        signature_b64: str,
        *,
        display_name: str = "",
        tiers: Optional[list[str]] = None,
        task_types: Optional[list[str]] = None,
        roles: Optional[list[str]] = None,
        capabilities: Optional[list[str]] = None,
        host: str = "",
        metadata: Optional[dict[str, Any]] = None,
        now: Optional[float] = None,
    ) -> WorkerRecord:
        at = now if now is not None else time.time()
        challenge = self._challenges.get(worker_id)
        if challenge is None:
            raise RegistryError(f"no pending challenge for worker {worker_id!r}")
        if at - challenge.issued_at > self._challenge_ttl_s:
            del self._challenges[worker_id]
            raise RegistryError(f"challenge for {worker_id!r} expired")
        payload = registration_payload(worker_id, public_key_b64, challenge.nonce)
        if not verify(public_key_b64, payload, signature_b64):
            raise RegistryError(
                f"registration signature for {worker_id!r} does not verify under the presented key"
            )
        del self._challenges[worker_id]  # single-use
        previous = self._workers.get(worker_id)
        record = WorkerRecord(
            worker_id=worker_id,
            display_name=display_name or worker_id,
            public_key_b64=public_key_b64,
            tiers=tiers or [],
            task_types=task_types or [],
            roles=roles or [],
            capabilities=capabilities or [],
            host=host,
            registered_at=at,
            key_rotations=(previous.key_rotations + 1) if previous else 0,
            metadata=metadata or {},
        )
        self._workers[worker_id] = record
        return record

    # -- lookups ---------------------------------------------------------------

    def get(self, worker_id: str) -> Optional[WorkerRecord]:
        return self._workers.get(worker_id)

    def all(self) -> list[WorkerRecord]:
        return list(self._workers.values())

    def is_active(self, worker_id: str) -> bool:
        record = self._workers.get(worker_id)
        return bool(record and record.active)

    # -- ongoing authenticity checks --------------------------------------------

    def verify_message(self, worker_id: str, message: bytes, signature_b64: str) -> bool:
        """True iff `message` was signed by the key registered for `worker_id`
        and the worker is currently active."""
        record = self._workers.get(worker_id)
        if record is None or not record.active:
            return False
        return verify(record.public_key_b64, message, signature_b64)

    def rotate_key(self, worker_id: str, new_public_key_b64: str, signature_b64: str) -> WorkerRecord:
        """Swap a worker's key. The rotation request must be signed by the
        *current* registered key — continuity of identity, like signing a new
        GPG key with the old one."""
        record = self._workers.get(worker_id)
        if record is None:
            raise RegistryError(f"worker {worker_id!r} is not registered")
        payload = rotation_payload(worker_id, new_public_key_b64)
        if not verify(record.public_key_b64, payload, signature_b64):
            raise RegistryError(f"key rotation for {worker_id!r} not signed by current key")
        record.public_key_b64 = new_public_key_b64
        record.key_rotations += 1
        return record

    def deactivate(self, worker_id: str) -> None:
        record = self._workers.get(worker_id)
        if record is None:
            raise RegistryError(f"worker {worker_id!r} is not registered")
        record.active = False

    def reactivate(self, worker_id: str) -> None:
        record = self._workers.get(worker_id)
        if record is None:
            raise RegistryError(f"worker {worker_id!r} is not registered")
        record.active = True
