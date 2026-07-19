"""Durable, append-only workflow run events.

New runs write one versioned canonical stream.  ``entries()`` is an explicit
projection onto the historical ``JournalEntry`` vocabulary so existing package,
harvest, and web readers remain compatible without duplicate on-disk records.
"""
from __future__ import annotations

import contextlib
import json
import math
import os
import stat
import time
from pathlib import Path
from typing import Any, Iterator, Literal, Optional

import fcntl
from pydantic import BaseModel, Field, field_validator, model_validator


CANONICAL_KINDS = frozenset({
    "run.started", "step.ready", "step.started",
    "attempt.assigned", "attempt.started",
    "tool.requested", "tool.completed",
    "verification.started", "verification.completed",
    "approval.required", "approval.resolved",
    "step.completed", "step.failed", "step.skipped",
    "run.completed", "run.failed",
    # META-19: the live context manifest journaled per attempt round — the
    # authoritative "what did the model see" record. Attempt-scoped (the
    # executor sink merges the attempt number `n`), no legacy projection.
    "context.manifest",
})


class RunEvent(BaseModel):
    """One event in the canonical v1 run stream."""

    schema_version: Literal[1] = 1
    seq: int = Field(ge=0)
    at: float
    kind: str
    run_id: str = Field(min_length=1)
    snapshot_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    step_id: Optional[str] = None
    attempt_id: Optional[str] = None
    payload: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "forbid"}

    @field_validator("at")
    @classmethod
    def _finite_timestamp(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("event timestamp must be finite")
        return value

    @field_validator("kind")
    @classmethod
    def _known_kind(cls, value: str) -> str:
        if value not in CANONICAL_KINDS:
            raise ValueError(f"unknown canonical event kind {value!r}")
        return value

    @model_validator(mode="after")
    def _required_identity_fields(self) -> "RunEvent":
        if self.kind.startswith("run."):
            if self.step_id is not None or self.attempt_id is not None:
                raise ValueError("run events cannot carry step/attempt IDs")
        elif not self.step_id:
            raise ValueError(f"{self.kind} requires a step ID")
        if self.kind.startswith(("attempt.", "tool.", "verification.")):
            if not self.attempt_id:
                raise ValueError(f"{self.kind} requires an attempt ID")
        return self


class JournalEntry(BaseModel):
    """Historical event shape returned by :meth:`Journal.entries`.

    It intentionally keeps the old permissive defaults because archived
    journals with omitted ``step_id``/``payload`` remain valid inputs.
    """

    seq: int
    at: float
    kind: str
    run_id: str
    step_id: Optional[str] = None
    payload: dict[str, Any] = Field(default_factory=dict)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number {value}")


def _parse_line(line: str, *, line_no: int) -> dict[str, Any]:
    try:
        value = json.loads(
            line,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"invalid journal JSON at line {line_no}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"journal line {line_no} must be a JSON object")
    return value


def _legacy_projection(event: RunEvent) -> Optional[JournalEntry]:
    kind = event.kind
    payload = dict(event.payload)
    projected: Optional[str]
    if kind in {"run.started", "step.started"}:
        projected = kind
    elif kind == "verification.completed":
        projected = "step.attempt"
    elif kind == "approval.required":
        projected = "hitl.requested"
    elif kind == "approval.resolved":
        projected = "hitl.resolved"
    elif kind in {"step.completed", "step.failed", "step.skipped"}:
        projected = kind
    elif kind in {"run.completed", "run.failed"}:
        projected = "run.finished"
        payload.setdefault("status", "completed" if kind == "run.completed" else "failed")
    else:
        projected = None
    if projected is None:
        return None
    return JournalEntry(
        seq=event.seq,
        at=event.at,
        kind=projected,
        run_id=event.run_id,
        step_id=event.step_id,
        payload=payload,
    )


def _validate_canonical_history(events: list[RunEvent]) -> None:
    if not events:
        return
    if events[0].kind != "run.started":
        raise ValueError("canonical journal must begin with run.started")
    if sum(event.kind == "run.started" for event in events) != 1:
        raise ValueError("canonical journal must contain one run.started")
    terminals = [event for event in events if event.kind in {"run.completed", "run.failed"}]
    if len(terminals) > 1:
        raise ValueError("canonical journal has duplicate run terminal events")
    if terminals and events[-1] is not terminals[0]:
        raise ValueError("canonical journal has events after its run terminal")
    step_terminals: set[str] = set()
    for event in events:
        if event.kind in {"step.completed", "step.failed", "step.skipped"}:
            assert event.step_id is not None
            if event.step_id in step_terminals:
                raise ValueError(
                    f"canonical journal has duplicate terminal for step {event.step_id!r}"
                )
            step_terminals.add(event.step_id)


class Journal:
    """A strict durable journal with a legacy compatibility mode.

    Engine-created journals are canonical.  Direct callers using ``append``
    keep the historical shape, which is useful for archived fixtures.  A file
    may never mix the two formats.
    """

    def __init__(self, path: Optional[str | Path] = None, *, canonical: bool = False) -> None:
        self._records: list[RunEvent | JournalEntry] = []
        self._path = Path(path) if path else None
        self._canonical = canonical
        self._run_id: Optional[str] = None
        self._snapshot_digest: Optional[str] = None
        self._lock_fd: Optional[int] = None

    @property
    def is_canonical(self) -> bool:
        return self._canonical

    def initialize(
        self, run_id: str, payload: dict[str, Any], *, snapshot_digest: str
    ) -> RunEvent:
        """Create canonical seq-0 ``run.started`` and bind stream identity."""
        if not self._canonical:
            raise ValueError("initialize is only valid for canonical journals")
        if self._records:
            raise ValueError("journal is already initialized")
        return self.append_event(
            "run.started", run_id, payload=payload, snapshot_digest=snapshot_digest
        )

    def append_event(
        self,
        kind: str,
        run_id: str,
        step_id: Optional[str] = None,
        payload: Optional[dict[str, Any]] = None,
        *,
        snapshot_digest: Optional[str] = None,
        attempt_id: Optional[str] = None,
    ) -> RunEvent | JournalEntry | None:
        if not self._canonical:
            # A resumed historical stream stays historical; never mix formats.
            # Events with no old equivalent are intentionally absent there.
            projected_kind: Optional[str]
            projected_payload = dict(payload or {})
            if kind in {"run.started", "step.started"}:
                projected_kind = kind
            elif kind == "verification.completed":
                projected_kind = "step.attempt"
            elif kind == "approval.required":
                projected_kind = "hitl.requested"
            elif kind == "approval.resolved":
                projected_kind = "hitl.resolved"
            elif kind in {"step.completed", "step.failed", "step.skipped"}:
                projected_kind = kind
            elif kind in {"run.completed", "run.failed"}:
                projected_kind = "run.finished"
                projected_payload.setdefault(
                    "status", "completed" if kind == "run.completed" else "failed"
                )
            else:
                projected_kind = None
            if projected_kind is None:
                return None
            return self.append(
                projected_kind, run_id, step_id=step_id, payload=projected_payload
            )
        with self.transaction(refresh=self._lock_fd is None):
            digest = snapshot_digest or self._snapshot_digest
            if digest is None:
                raise ValueError("canonical events require a snapshot digest")
            if self._run_id is not None and run_id != self._run_id:
                raise ValueError(
                    f"journal is bound to run {self._run_id!r}, not {run_id!r}"
                )
            if self._snapshot_digest is not None and digest != self._snapshot_digest:
                raise ValueError("snapshot digest changed within journal")
            event = RunEvent(
                seq=len(self._records), at=time.time(), kind=kind,
                run_id=run_id, snapshot_digest=digest, step_id=step_id,
                attempt_id=attempt_id, payload=payload or {},
            )
            if event.seq == 0 and event.kind != "run.started":
                raise ValueError("canonical journal must begin with run.started")
            if event.seq > 0 and event.kind == "run.started":
                raise ValueError("canonical journal may contain only one run.started")
            _validate_canonical_history([
                *[item for item in self._records if isinstance(item, RunEvent)],
                event,
            ])
            self._write_record(event)
            self._records.append(event)
            self._run_id = run_id
            self._snapshot_digest = digest
            return event

    def append(
        self,
        kind: str,
        run_id: str,
        step_id: Optional[str] = None,
        payload: Optional[dict[str, Any]] = None,
    ) -> JournalEntry:
        """Append a historical-format record (compatibility fixtures only)."""
        if self._canonical:
            raise ValueError("canonical journals require append_event")
        with self.transaction(refresh=self._lock_fd is None):
            if self._run_id is not None and run_id != self._run_id:
                raise ValueError(
                    f"journal is bound to run {self._run_id!r}, not {run_id!r}"
                )
            entry = JournalEntry(
                seq=len(self._records), at=time.time(), kind=kind,
                run_id=run_id, step_id=step_id, payload=payload or {},
            )
            self._write_record(entry)
            self._records.append(entry)
            self._run_id = run_id
            return entry

    def events(self, kind: Optional[str] = None) -> list[RunEvent | JournalEntry]:
        records = list(self._records)
        if kind is not None:
            records = [event for event in records if event.kind == kind]
        return records

    def entries(self, kind: Optional[str] = None) -> list[JournalEntry]:
        if self._canonical:
            projected = [
                entry for event in self._records
                if isinstance(event, RunEvent)
                for entry in [_legacy_projection(event)] if entry is not None
            ]
            # Legacy consumers expect their own contiguous sequence, not gaps
            # created by canonical events that have no historical equivalent.
            projected = [entry.model_copy(update={"seq": seq})
                         for seq, entry in enumerate(projected)]
        else:
            projected = [entry for entry in self._records
                         if isinstance(entry, JournalEntry)]
        if kind is not None:
            projected = [entry for entry in projected if entry.kind == kind]
        return projected

    def __len__(self) -> int:
        return len(self._records)

    def _lock_path(self) -> Path:
        assert self._path is not None
        return self._path.with_name(self._path.name + ".lock")

    def acquire(self, *, refresh: bool = True) -> None:
        if self._path is None or self._lock_fd is not None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(self._lock_path(), flags, 0o600)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            raise ValueError("journal lock path is not a regular file")
        fcntl.flock(fd, fcntl.LOCK_EX)
        self._lock_fd = fd
        try:
            if refresh and self._path.exists():
                self._load_into_self()
        except Exception:
            self.release()
            raise

    def release(self) -> None:
        if self._lock_fd is None:
            return
        fd, self._lock_fd = self._lock_fd, None
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    @contextlib.contextmanager
    def transaction(self, *, refresh: bool = True) -> Iterator["Journal"]:
        owned = self._lock_fd is None
        if owned:
            self.acquire(refresh=refresh)
        try:
            yield self
        finally:
            if owned:
                self.release()

    def _write_record(self, record: RunEvent | JournalEntry) -> None:
        if self._path is None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        existed = self._path.exists()
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(self._path, flags, 0o600)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ValueError("journal path is not a regular file")
            data = (json.dumps(
                record.model_dump(mode="json"), sort_keys=True,
                ensure_ascii=False, allow_nan=False,
            ) + "\n").encode("utf-8")
            view = memoryview(data)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise OSError("short write appending journal")
                view = view[written:]
            os.fsync(fd)
        finally:
            os.close(fd)
        if not existed:
            dir_fd = os.open(self._path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)

    @classmethod
    def load(cls, path: str | Path) -> "Journal":
        journal = cls(path=path)
        # Readers take the same sidecar lock as writers, so they never mistake
        # an append in progress for a truncated/corrupt terminal record.
        with journal.transaction(refresh=True):
            pass
        return journal

    def _load_into_self(self) -> None:
        assert self._path is not None
        try:
            st = self._path.lstat()
        except FileNotFoundError:
            self._records = []
            self._run_id = None
            self._snapshot_digest = None
            return
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
            raise ValueError("journal path must be a regular non-symlink file")
        raw = self._path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise ValueError("journal has a truncated final record")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("journal is not valid UTF-8") from exc
        parsed = [_parse_line(line, line_no=n)
                  for n, line in enumerate(text.splitlines(), 1)]
        if not parsed:
            self._records = []
            self._run_id = None
            self._snapshot_digest = None
            return
        canonical_flags = ["schema_version" in value for value in parsed]
        if any(canonical_flags) and not all(canonical_flags):
            raise ValueError("journal mixes canonical and legacy records")
        canonical = all(canonical_flags)
        records: list[RunEvent | JournalEntry] = []
        for expected_seq, value in enumerate(parsed):
            model = RunEvent if canonical else JournalEntry
            try:
                record = model.model_validate(value)
            except Exception as exc:
                raise ValueError(
                    f"invalid journal record at line {expected_seq + 1}: {exc}"
                ) from exc
            if record.seq != expected_seq:
                raise ValueError(
                    f"journal sequence gap at line {expected_seq + 1}: "
                    f"expected {expected_seq}, got {record.seq}"
                )
            records.append(record)
        run_ids = {record.run_id for record in records}
        if len(run_ids) != 1:
            raise ValueError("journal contains mixed run IDs")
        self._canonical = canonical
        self._records = records
        self._run_id = records[0].run_id
        if canonical:
            events = [event for event in records if isinstance(event, RunEvent)]
            _validate_canonical_history(events)
            digests = {event.snapshot_digest for event in events}
            if len(digests) != 1:
                raise ValueError("snapshot digest changed within journal")
            digest = events[0].snapshot_digest
            payload_digest = events[0].payload.get("snapshot_digest")
            if payload_digest is not None and payload_digest != digest:
                raise ValueError("run.started payload snapshot digest mismatch")
            self._snapshot_digest = digest
        else:
            self._snapshot_digest = None
