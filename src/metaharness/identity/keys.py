"""Ed25519 keypairs and detached signatures.

This is the authenticity primitive the trust layer is built on — the same category
as git commit-signing or signed JWTs. A worker harness holds a private key; the
orchestrator holds its public key. The orchestrator can then confirm that a message
or a registration genuinely came from that worker, and that it wasn't altered.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"))


@dataclass
class KeyPair:
    """An Ed25519 keypair. Public bytes are shared; private bytes stay with the owner."""

    private: Ed25519PrivateKey
    public: Ed25519PublicKey

    @classmethod
    def generate(cls) -> "KeyPair":
        priv = Ed25519PrivateKey.generate()
        return cls(private=priv, public=priv.public_key())

    def public_b64(self) -> str:
        raw = self.public.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        return _b64(raw)

    def private_b64(self) -> str:
        raw = self.private.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
        return _b64(raw)

    def sign(self, message: bytes) -> str:
        return _b64(self.private.sign(message))

    @classmethod
    def from_private_b64(cls, text: str) -> "KeyPair":
        priv = Ed25519PrivateKey.from_private_bytes(_unb64(text))
        return cls(private=priv, public=priv.public_key())


def public_from_b64(text: str) -> Ed25519PublicKey:
    return Ed25519PublicKey.from_public_bytes(_unb64(text))


def verify(public_b64: str, message: bytes, signature_b64: str) -> bool:
    """Return True iff `signature_b64` is a valid signature of `message` under the
    public key. Never raises — a bad signature is a normal, expected answer."""
    try:
        pub = public_from_b64(public_b64)
        pub.verify(_unb64(signature_b64), message)
        return True
    except (InvalidSignature, ValueError, Exception):
        return False
