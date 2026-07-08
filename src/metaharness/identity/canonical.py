"""Canonical JSON serialization for signing.

A signature is only meaningful if both sides serialize the payload identically.
Canonical form here: sorted keys, no whitespace, UTF-8. Every signed structure in
the identity layer (registrations, tokens, provenance entries) goes through this
one function, so there is exactly one place a mismatch could hide.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_bytes(obj: Any) -> bytes:
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    ).encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
