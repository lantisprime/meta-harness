"""Scoped capability tokens — signed permission slips issued by the orchestrator.

A worker doesn't get blanket authority; it gets a short-lived token naming exactly
what it may do ("task:execute", "tier:small") and for which task. The token is a
signed payload, same family as a JWT: anyone holding the issuer's public key can
confirm it's genuine, unexpired, and covers the requested scope. This keeps the
principal/agent relationship explicit and auditable.
"""
from __future__ import annotations

import time
import uuid
from typing import Optional

from pydantic import BaseModel, Field

from metaharness.identity.canonical import canonical_bytes
from metaharness.identity.keys import KeyPair, verify


class TokenPayload(BaseModel):
    token_id: str = Field(default_factory=lambda: f"tok_{uuid.uuid4().hex[:12]}")
    subject: str                       # worker_id the token is issued to
    scopes: list[str] = Field(default_factory=list)
    task_id: Optional[str] = None      # bind the token to one task, if given
    issued_at: float = 0.0
    expires_at: float = 0.0

    def signing_bytes(self) -> bytes:
        return canonical_bytes({"kind": "capability_token", **self.model_dump()})


class CapabilityToken(BaseModel):
    payload: TokenPayload
    issuer_public_b64: str
    signature_b64: str


class TokenCheck(BaseModel):
    ok: bool
    reason: str = ""


class TokenIssuer:
    """The orchestrator-side mint. Holds the issuer keypair; workers and services
    hold only `public_b64()` for verification."""

    def __init__(self, keypair: Optional[KeyPair] = None) -> None:
        self._keypair = keypair or KeyPair.generate()
        self._revoked: set[str] = set()

    def public_b64(self) -> str:
        return self._keypair.public_b64()

    def issue(
        self,
        subject: str,
        scopes: list[str],
        *,
        ttl_s: float = 600.0,
        task_id: Optional[str] = None,
        now: Optional[float] = None,
    ) -> CapabilityToken:
        at = now if now is not None else time.time()
        payload = TokenPayload(
            subject=subject,
            scopes=sorted(scopes),
            task_id=task_id,
            issued_at=at,
            expires_at=at + ttl_s,
        )
        return CapabilityToken(
            payload=payload,
            issuer_public_b64=self.public_b64(),
            signature_b64=self._keypair.sign(payload.signing_bytes()),
        )

    def revoke(self, token_id: str) -> None:
        self._revoked.add(token_id)

    def is_revoked(self, token_id: str) -> bool:
        return token_id in self._revoked


def scope_covers(granted: str, required: str) -> bool:
    """A granted scope covers a required one if it matches exactly, or is a
    wildcard prefix: "task:*" covers "task:execute"."""
    if granted == required:
        return True
    if granted.endswith(":*"):
        return required.startswith(granted[:-1])
    return False


def validate_token(
    token: CapabilityToken,
    issuer_public_b64: str,
    *,
    required_scope: Optional[str] = None,
    subject: Optional[str] = None,
    task_id: Optional[str] = None,
    revoked: Optional[set[str]] = None,
    now: Optional[float] = None,
) -> TokenCheck:
    """Full check: genuine issuer, untampered payload, unexpired, right subject,
    right task binding, scope coverage, not revoked. Returns a reasoned verdict
    instead of raising — an invalid token is a normal answer."""
    at = now if now is not None else time.time()
    if token.issuer_public_b64 != issuer_public_b64:
        return TokenCheck(ok=False, reason="token names a different issuer")
    if not verify(issuer_public_b64, token.payload.signing_bytes(), token.signature_b64):
        return TokenCheck(ok=False, reason="signature does not verify (tampered or wrong key)")
    if at > token.payload.expires_at:
        return TokenCheck(ok=False, reason="token expired")
    if revoked and token.payload.token_id in revoked:
        return TokenCheck(ok=False, reason="token revoked")
    if subject is not None and token.payload.subject != subject:
        return TokenCheck(ok=False, reason=f"token subject is {token.payload.subject!r}, not {subject!r}")
    if task_id is not None and token.payload.task_id not in (None, task_id):
        return TokenCheck(ok=False, reason=f"token bound to task {token.payload.task_id!r}, not {task_id!r}")
    if required_scope is not None and not any(
        scope_covers(s, required_scope) for s in token.payload.scopes
    ):
        return TokenCheck(ok=False, reason=f"no scope covers {required_scope!r}")
    return TokenCheck(ok=True, reason="valid")
