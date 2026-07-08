"""Hash-chained, signed provenance log — a ledger of who did what, in order.

Each entry commits to the previous entry's hash (like git commits) and is signed
by the actor that performed the action (like signed commits). Verifying the chain
answers two questions with certainty:

1. Integrity — has any recorded entry been altered or removed? (hash chain)
2. Authenticity — did each action really come from the actor it names? (signatures
   checked against the worker registry's keys)

This is the audit backbone the WebUI's provenance viewer renders.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable, Optional

from pydantic import BaseModel, Field

from metaharness.identity.canonical import canonical_bytes, sha256_hex
from metaharness.identity.keys import KeyPair, verify

GENESIS_HASH = "0" * 64


class ProvenanceEntry(BaseModel):
    index: int
    actor_id: str
    action: str                        # e.g. "task.assigned", "task.completed"
    detail: dict[str, Any] = Field(default_factory=dict)
    at: float
    prev_hash: str
    entry_hash: str
    signature_b64: str

    def body_bytes(self) -> bytes:
        """The bytes the hash and signature commit to — everything except the
        hash and signature themselves."""
        return canonical_bytes(
            {
                "index": self.index,
                "actor_id": self.actor_id,
                "action": self.action,
                "detail": self.detail,
                "at": self.at,
                "prev_hash": self.prev_hash,
            }
        )


class ChainCheck(BaseModel):
    ok: bool
    checked: int = 0
    problem_index: Optional[int] = None
    reason: str = ""


class ProvenanceLog:
    """Append-only in-memory chain with optional JSONL persistence."""

    def __init__(self) -> None:
        self._entries: list[ProvenanceEntry] = []

    def __len__(self) -> int:
        return len(self._entries)

    def entries(self) -> list[ProvenanceEntry]:
        return list(self._entries)

    def head_hash(self) -> str:
        return self._entries[-1].entry_hash if self._entries else GENESIS_HASH

    def append(
        self,
        actor_id: str,
        action: str,
        detail: Optional[dict[str, Any]] = None,
        *,
        keypair: KeyPair,
        now: Optional[float] = None,
    ) -> ProvenanceEntry:
        """Record an action. The actor signs the entry with its own key, so the
        entry can later be checked against the registry."""
        at = now if now is not None else time.time()
        entry = ProvenanceEntry(
            index=len(self._entries),
            actor_id=actor_id,
            action=action,
            detail=detail or {},
            at=at,
            prev_hash=self.head_hash(),
            entry_hash="",
            signature_b64="",
        )
        entry.entry_hash = sha256_hex(entry.body_bytes())
        entry.signature_b64 = keypair.sign(entry.entry_hash.encode("ascii"))
        self._entries.append(entry)
        return entry

    def verify_chain(
        self, resolve_public_b64: Callable[[str], Optional[str]]
    ) -> ChainCheck:
        """Walk the whole chain. `resolve_public_b64` maps an actor_id to its
        registered public key (usually `lambda wid: registry.get(wid).public_key_b64`).
        Reports the first entry that fails and why."""
        prev = GENESIS_HASH
        for i, entry in enumerate(self._entries):
            if entry.index != i:
                return ChainCheck(ok=False, checked=i, problem_index=i, reason="index gap or reorder")
            if entry.prev_hash != prev:
                return ChainCheck(ok=False, checked=i, problem_index=i, reason="broken hash link")
            if sha256_hex(entry.body_bytes()) != entry.entry_hash:
                return ChainCheck(ok=False, checked=i, problem_index=i, reason="entry hash mismatch (contents altered)")
            public = resolve_public_b64(entry.actor_id)
            if public is None:
                return ChainCheck(ok=False, checked=i, problem_index=i, reason=f"unknown actor {entry.actor_id!r}")
            if not verify(public, entry.entry_hash.encode("ascii"), entry.signature_b64):
                return ChainCheck(ok=False, checked=i, problem_index=i, reason="signature does not verify")
            prev = entry.entry_hash
        return ChainCheck(ok=True, checked=len(self._entries), reason="chain intact")

    # -- persistence -------------------------------------------------------------

    def to_jsonl(self, path: str | Path) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            for entry in self._entries:
                fh.write(json.dumps(entry.model_dump(), sort_keys=True) + "\n")

    @classmethod
    def from_jsonl(cls, path: str | Path) -> "ProvenanceLog":
        log = cls()
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    log._entries.append(ProvenanceEntry.model_validate(json.loads(line)))
        return log
