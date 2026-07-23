"""Executor registry: pack-local storage for compiled executors.

Provides atomic writes, self-verifying records, and status transitions.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from selflearn.compilation.models import (
    ExecutorCandidate,
    ExecutorRecord,
    EXECUTOR_STATUSES,
)
from selflearn.ports import ProvenancePort
from selflearn.store.packstore import PackStore


class RegistryError(RuntimeError):
    """Error in registry operations."""
    pass


# Legal status transitions:
# None -> quarantined (new candidate via write_candidate)
# quarantined -> active (approval)
# quarantined -> rejected (gate failure)
# active -> superseded (new activation)
# superseded -> (nothing)
#
# F2-13 note: true two-file transactions (source + registry) do not exist.
# A crash between writing the source and appending the registry leaves an
# orphan source file.  The doctor flags such orphans as `executor.orphan-source`.
ILLEGAL_TRANSITIONS = {
    "active": {"quarantined", "rejected"},
    "rejected": {"active", "quarantined"},
    "superseded": {"active", "quarantined", "rejected"},
}


class ExecutorRegistry:
    """Pack-local executor registry with atomic writes."""

    def __init__(
        self,
        store_root: Path,
        pack: str,
        provenance: Optional[ProvenancePort] = None,
        clock: Optional[Callable[[], Any]] = None,
    ):
        self.store_root = Path(store_root)
        self.pack = pack
        self.store = PackStore(self.store_root)
        self.provenance = provenance
        self.clock = clock
        self._registry_path = self.store_root / pack / "executors" / "registry.json"
        self._executors_dir = self.store_root / pack / "executors"

    def _ensure_registry(self) -> dict:
        """Ensure registry file exists, return parsed content.

        FIX-9: Only write paths call this to create the file.
        Read paths (record_for, active_for) return {} when absent.
        """
        if not self._registry_path.exists():
            self._executors_dir.mkdir(parents=True, exist_ok=True)
            self._registry_path.write_text(json.dumps({"records": []}))
        try:
            return json.loads(self._registry_path.read_text())
        except (json.JSONDecodeError, IOError) as e:
            raise RegistryError(f"Registry corrupt: {e}")

    def _write_registry(self, data: dict) -> None:
        """Atomic write of registry using tmp + os.replace."""
        self._executors_dir.mkdir(parents=True, exist_ok=True)
        tmp = self._registry_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=1))
        os.replace(tmp, self._registry_path)

    def record_for(self, entry_id: str, status: str | None = None) -> list[ExecutorRecord]:
        """Get all records for an entry, optionally filtered by status.

        F2-9: read path — does not create registry file.
        F2-9: malformed/unreadable registry file -> RegistryError.
        """
        if not self._registry_path.exists():
            return []
        try:
            data = json.loads(self._registry_path.read_text())
        except (json.JSONDecodeError, IOError) as e:
            # F2-9: raise on corrupt file, don't silently return []
            raise RegistryError(f"Registry corrupt: {e}")
        records = []
        for r in data.get("records", []):
            if r.get("entry_id") != entry_id:
                continue
            if status and r.get("status") != status:
                continue
            try:
                records.append(ExecutorRecord(**r))
            except Exception as e:
                # FIX-9: malformed record -> RegistryError, never silently skip
                raise RegistryError(
                    f"Corrupt record for {entry_id}: {e}")
        return records

    def active_for(self, entry_id: str) -> ExecutorRecord | None:
        """Get the active record for an entry.

        F2-9: read path — does not create registry file.
        """
        records = self.record_for(entry_id, status="active")
        return records[0] if records else None

    def is_stale(self, entry_id: str, current_spec_hash: str) -> bool:
        """Check if the active executor is stale."""
        active = self.active_for(entry_id)
        if active is None:
            return False
        return active.spec_hash != current_spec_hash

    def transition(self, record: ExecutorRecord, new_status: str,
                    receipt_id: str, *, updated_at: str) -> ExecutorRecord:
        """Transition a record to a new status.

        Args:
            record: Current record
            new_status: Target status
            receipt_id: ID of the receipt causing this transition
            updated_at: ISO timestamp

        Returns:
            New record with updated status

        Raises:
            RegistryError: If transition is illegal
        """
        old_status = record.status

        # Check legal transitions
        if new_status in ILLEGAL_TRANSITIONS.get(old_status, set()):
            raise RegistryError(
                f"Illegal transition: {old_status} -> {new_status}")

        # Build new record — record_id auto-computed
        new_record = ExecutorRecord(
            record_id="",
            entry_id=record.entry_id,
            pack=record.pack,
            spec_hash=record.spec_hash,
            executor_hash=record.executor_hash,
            status=new_status,
            path=record.path,
            receipt_id=receipt_id,
            updated_at=updated_at,
        )

        # Update registry
        data = self._ensure_registry()
        if not any(r.get("record_id") == record.record_id for r in data.get("records", [])):
            raise RegistryError(
                f"Record {record.record_id[:16]}... not found in registry; "
                f"cannot transition {record.entry_id} from {old_status}")
        new_records = []
        for r in data.get("records", []):
            if r.get("record_id") == record.record_id:
                new_records.append({
                    "record_id": new_record.record_id,
                    "entry_id": new_record.entry_id,
                    "pack": new_record.pack,
                    "spec_hash": new_record.spec_hash,
                    "executor_hash": new_record.executor_hash,
                    "status": new_record.status,
                    "path": new_record.path,
                    "receipt_id": new_record.receipt_id,
                    "updated_at": new_record.updated_at,
                })
            else:
                new_records.append(r)
        data["records"] = new_records
        self._write_registry(data)

        return new_record

    def write_candidate(
        self,
        candidate: ExecutorCandidate,
        *,
        reason: str = "candidate-written",
    ) -> Path:
        """Write candidate executor and atomically add quarantined record.

        FIX-3: atomically writes source AND adds quarantined ExecutorRecord.
        If a quarantined record already exists for the same spec_hash+executor_hash,
        this is idempotent (no duplicate record).

        Args:
            candidate: The candidate to write

        Returns:
            Path to the written file
        """
        entry_dir = self._executors_dir / candidate.spec.entry_id
        entry_dir.mkdir(parents=True, exist_ok=True)
        path = entry_dir / f"{candidate.spec.spec_hash}.py"

        if path.exists():
            existing = path.read_text(encoding="utf-8")
            if existing != candidate.source:
                raise RegistryError(
                    f"Refusing to overwrite {path} with different content")
            # Idempotent: source already written, add record if absent
        else:
            # FIX-9: write source via tmp + os.replace (atomic)
            tmp = path.with_suffix(".py.tmp")
            # utf-8 pinned: content_hash() hashes text.encode("utf-8"),
            # so locale-dependent file I/O would break the hash contract
            # for any non-ASCII source under a non-utf-8 locale.
            tmp.write_text(candidate.source, encoding="utf-8")
            os.replace(tmp, path)

        # FIX-3: add quarantined record (idempotent — skip if already present)
        self._add_quarantined_record(candidate, str(path.relative_to(self.store_root)))

        # F2-7: journal quarantine event when provenance is bound
        if self.provenance is not None:
            timestamp = self.clock().isoformat() if self.clock is not None else candidate.compiled_at
            self.provenance.append({
                "kind": "quarantined",
                "entry_id": candidate.spec.entry_id,
                "spec_hash": candidate.spec.spec_hash,
                "executor_hash": candidate.executor_hash,
                "actor": "executor-registry",
                "reason": reason,
                "timestamp": timestamp,
            })

        return path

    def _add_quarantined_record(self, candidate: ExecutorCandidate,
                                 relative_path: str) -> None:
        """Add a quarantined record for the candidate if not already present."""
        # Check if quarantined record already exists
        existing = self.record_for(candidate.spec.entry_id, status="quarantined")
        for r in existing:
            if r.executor_hash == candidate.executor_hash:
                return  # Already present, idempotent

        # Build quarantined record with documented receipt_id convention
        receipt_id = f"compile:{candidate.executor_hash}"
        record = ExecutorRecord(
            record_id="",
            entry_id=candidate.spec.entry_id,
            pack=candidate.spec.pack,
            spec_hash=candidate.spec.spec_hash,
            executor_hash=candidate.executor_hash,
            status="quarantined",
            path=relative_path,
            receipt_id=receipt_id,
            updated_at="",
        )

        # Add to registry
        data = self._ensure_registry()
        data["records"].append({
            "record_id": record.record_id,
            "entry_id": record.entry_id,
            "pack": record.pack,
            "spec_hash": record.spec_hash,
            "executor_hash": record.executor_hash,
            "status": record.status,
            "path": record.path,
            "receipt_id": record.receipt_id,
            "updated_at": record.updated_at,
        })
        self._write_registry(data)

    def add_record(self, record: ExecutorRecord) -> None:
        """Add a new record to the registry (for test use)."""
        data = self._ensure_registry()
        data["records"].append({
            "record_id": record.record_id,
            "entry_id": record.entry_id,
            "pack": record.pack,
            "spec_hash": record.spec_hash,
            "executor_hash": record.executor_hash,
            "status": record.status,
            "path": record.path,
            "receipt_id": record.receipt_id,
            "updated_at": record.updated_at,
        })
        self._write_registry(data)
