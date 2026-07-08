"""Worker identity: keys, registration, capability tokens, and provenance."""
from metaharness.identity.keys import KeyPair, public_from_b64, verify
from metaharness.identity.provenance import ChainCheck, ProvenanceEntry, ProvenanceLog
from metaharness.identity.registry import (
    RegistrationChallenge,
    RegistryError,
    WorkerRecord,
    WorkerRegistry,
    registration_payload,
    rotation_payload,
)
from metaharness.identity.tokens import (
    CapabilityToken,
    TokenCheck,
    TokenIssuer,
    TokenPayload,
    scope_covers,
    validate_token,
)

__all__ = [
    "KeyPair",
    "public_from_b64",
    "verify",
    "WorkerRegistry",
    "WorkerRecord",
    "RegistrationChallenge",
    "RegistryError",
    "registration_payload",
    "rotation_payload",
    "TokenIssuer",
    "TokenPayload",
    "CapabilityToken",
    "TokenCheck",
    "scope_covers",
    "validate_token",
    "ProvenanceLog",
    "ProvenanceEntry",
    "ChainCheck",
]
