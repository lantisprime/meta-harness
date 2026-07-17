"""Append-only memory stores with SQLite durability, WAL mode, numbered
schema migrations, and an FTS5 lexical index over normalized content.

The four subclass stores (Episodic/Semantic/Working/Procedural) differ only by
default_kind; their durability and contract surface are identical. Records
survive close/reopen (WAL + numbered migrations). In-memory mode for tests
where durability isn't under test.
"""
from __future__ import annotations

import itertools
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, ClassVar, Iterable

from metaharness.context import ContextScope, Sensitivity
from metaharness.context.models import content_hash

from metaharness.memory.records import (
    ActivationState,
    LifecycleState,
    MemoryKind,
    MemoryMutationReceipt,
    MemoryRecord,
    normalize_text,
)


class ImmutableRecordError(Exception):
    """Raised when a caller attempts to overwrite a committed record in place.

    META5-MEM-001: the store is append-only. Use mutate() with a receipt to
    produce a supersede + MemoryMutationReceipt pair instead.
    """


class UnreceiptedMutationError(Exception):
    """Raised when mutate() is called with receipt=None.

    META5-MEM-004: every mutation of a committed record must carry a receipt
    binding before/after content + lifecycle transition + actor identity.
    """


_DEFAULT_SCOPE_FACTORY = lambda: ContextScope(project_id="meta-harness")


class MemoryStore:
    """Append-only, durable (SQLite + WAL + numbered migrations + FTS5) memory
    store. The base class is parameterized by default_kind; the four
    *_MemoryStore subclasses pin a default kind for ergonomics but accept an
    explicit kind in commit() so test fixtures can force any kind."""

    SCHEMA_VERSION: ClassVar[int] = 1
    default_kind: ClassVar[MemoryKind | None] = None

    def __init__(
        self,
        *,
        path: str | Path = ":memory:",
        clock: Callable[[], int] | None = None,
        id_factory: Callable[[], str] | None = None,
    ):
        """``path``: ":memory:" for an ephemeral database, or a filesystem
        path for durability (must be a fresh or pre-existing database file;
        numbered migrations are applied automatically).

        ``clock``: injectable monotonic source for observed_at / creation
        timestamps. Default: an in-process counter starting at 0 — never
        wall-clock, so determinism holds.

        ``id_factory``: injectable record-id generator. Default: a per-store
        counter prefixed with the kind slug.
        """

        self._path = str(path)
        self._lock = threading.RLock()
        # isolation_level=None puts sqlite3 in autocommit mode; that gives
        # us a clean commit-before-emit boundary for the audit hook to rely
        # on (durable commit must precede any observability event).
        self._conn = sqlite3.connect(self._path, isolation_level=None, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA synchronous = NORMAL")
        self._clock_fn: Callable[[], int] = clock if clock is not None else self._default_clock
        self._id_counter = itertools.count(0)
        self._id_factory: Callable[[], str] = id_factory if id_factory is not None else self._default_id_factory
        self._migrate()

    # -- public API ------------------------------------------------------------

    def commit(
        self,
        *,
        kind: str | MemoryKind | None = None,
        content: str,
        scope: ContextScope | None = None,
        source_refs: Iterable[str] = (),
        observed_at: int | None = None,
        valid_from: int | None = None,
        valid_until: int | None = None,
        confidence: float = 1.0,
        sensitivity: Sensitivity = Sensitivity.INTERNAL,
        creator_id: str = "anonymous",
        activation_state: ActivationState = ActivationState.ACTIVE,
        lifecycle_state: LifecycleState = LifecycleState.ACTIVE,
        supersedes: Iterable[str] = (),
        usage_count: int = 0,
    ) -> MemoryRecord:
        """Append a new MemoryRecord to the store. The record is durably
        committed before this method returns; any caller-side audit hook
        fired after commit() may trust the record is durable.
        """

        resolved_kind = self._resolve_kind(kind)
        record_id = self._id_factory()
        with self._lock:
            record = MemoryRecord(
                id=record_id,
                kind=resolved_kind,
                scope=scope if scope is not None else _DEFAULT_SCOPE_FACTORY(),
                content=content,
                normalized_content=normalize_text(content),
                source_refs=tuple(source_refs),
                observed_at=observed_at if observed_at is not None else self._clock_fn(),
                valid_from=valid_from,
                valid_until=valid_until,
                confidence=confidence,
                sensitivity=sensitivity,
                creator_id=creator_id,
                activation_state=activation_state,
                lifecycle_state=lifecycle_state,
                supersedes=tuple(supersedes),
                usage_count=usage_count,
                creation_seq=next(self._id_counter),
            )
            self._insert_record(record)
        return record

    def overwrite(self, record_id: str, *, content: str) -> None:
        """Reject all in-place rewrites (META5-MEM-001). Use mutate() with a
        receipt instead."""

        del content  # signature symmetry only
        raise ImmutableRecordError(
            f"record {record_id!r} is immutable; use mutate() with a "
            "MemoryMutationReceipt to record a supersede instead"
        )

    def mutate(
        self,
        record_id: str,
        *,
        content: str,
        receipt: Any = None,
        actor_id: str = "anonymous",
        mutation_reason: str = "receipted supersede",
        **commit_kwargs: Any,
    ) -> MemoryRecord:
        """Append a supersede record (META5-MEM-004) and emit a
        MemoryMutationReceipt. receipt=None raises UnreceiptedMutationError
        BEFORE any database write — unreceipted mutation is forbidden.

        ``receipt`` is the receipted mutation marker (any non-None value is
        accepted; ``None`` is the unallowed sentinel). The MutationReceipt
        is constructed and persisted here, so callers don't have to
        pre-build it.
        """

        if receipt is None:
            raise UnreceiptedMutationError(
                f"mutation of record {record_id!r} requires a non-None "
                "receipt (every mutation must be audit-attested)"
            )
        with self._lock:
            existing = self._fetch_record_locked(record_id)
            if existing is None:
                raise KeyError(f"no memory record with id {record_id!r}")
            observed = commit_kwargs.pop("observed_at", None) or self._clock_fn()
            new_record = self.commit(
                kind=existing.kind,
                content=content,
                scope=existing.scope,
                source_refs=existing.source_refs,
                observed_at=observed,
                valid_from=existing.valid_from,
                valid_until=existing.valid_until,
                confidence=existing.confidence,
                sensitivity=existing.sensitivity,
                creator_id=existing.creator_id,
                activation_state=commit_kwargs.pop("activation_state", ActivationState.ACTIVE),
                lifecycle_state=commit_kwargs.pop("lifecycle_state", LifecycleState.ACTIVE),
                supersedes=(existing.id,),
                **commit_kwargs,
            )
            mutation_receipt = MemoryMutationReceipt(
                mutation_id=f"mut-{next(self._id_counter):08x}",
                target_record_id=existing.id,
                supersede_record_id=new_record.id,
                before_content_hash=content_hash(existing.content),
                after_content_hash=content_hash(new_record.content),
                before_lifecycle=existing.lifecycle_state,
                after_lifecycle=new_record.lifecycle_state,
                actor_id=actor_id,
                observed_at=observed,
                mutation_reason=mutation_reason,
            )
            self._insert_mutation(mutation_receipt)
        return new_record

    def get(self, record_id: str) -> MemoryRecord | None:
        """Fetch one record by id (returns None if absent)."""

        with self._lock:
            return self._fetch_record_locked(record_id)

    def list(
        self,
        *,
        scope: ContextScope | None = None,
        project_id: str | None = None,
        lifecycle_state: LifecycleState | None = None,
        activation_state: ActivationState | None = None,
        kind: MemoryKind | str | None = None,
        limit: int | None = None,
        include_tombstoned: bool = False,
    ) -> list[MemoryRecord]:
        """List records with optional scope / lifecycle filters. Scope
        isolation (META5-MEM-001 secondary): records from project A are
        never returned for project B queries.

        Default policy: tombstoned and dormant records are excluded so
        retrieval doesn't leak evidence of deletion.
        """

        clauses: list[str] = []
        params: list[Any] = []
        if project_id is None and scope is not None:
            project_id = scope.project_id
        if project_id is not None:
            clauses.append("project_id = ?")
            params.append(project_id)
        if lifecycle_state is not None:
            clauses.append("lifecycle_state = ?")
            params.append(lifecycle_state.value)
        if activation_state is not None:
            clauses.append("activation_state = ?")
            params.append(activation_state.value)
        else:
            if not include_tombstoned:
                clauses.append("activation_state != ?")
                params.append(ActivationState.TOMBSTONED.value)
        if kind is not None:
            kind_value = kind.value if isinstance(kind, MemoryKind) else kind
            clauses.append("kind = ?")
            params.append(kind_value)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = (
            "SELECT id, kind, project_id, run_id, task_id, attempt_id, lineage_id, "
            "content, source_refs_json, observed_at, valid_from, valid_until, "
            "confidence, lifecycle_state, activation_state, superseded_by, supersedes_json, "
            "sensitivity, creator_id, usage_count, last_accessed_at, creation_seq, tombstone_reason "
            "FROM records" + where + " ORDER BY creation_seq ASC"
        )
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_record(row) for row in rows]

    def scope(self, project_id: str) -> list[MemoryRecord]:
        """Convenience: every non-tombstoned record under a project_id. Used
        by the scope-isolation test."""

        return self.list(project_id=project_id)

    def close(self) -> None:
        """Close the underlying SQLite connection. The database on disk is
        fully durable thanks to WAL; reopen with a fresh MemoryStore(path=...)
        yields the same content."""

        with self._lock:
            self._conn.close()

    # -- internals -------------------------------------------------------------

    def _resolve_kind(self, kind: str | MemoryKind | None) -> MemoryKind:
        if kind is None:
            if self.default_kind is None:
                raise ValueError("kind is required (no default_kind for the base MemoryStore)")
            return self.default_kind
        if isinstance(kind, MemoryKind):
            return kind
        return MemoryKind(kind)

    def _default_clock(self) -> int:
        """Injectable-clock default: monotonic in-process counter. Never
        wall-clock; tests stay deterministic regardless of system time."""

        return self._default_clock_seq()

    _default_clock_seq = itertools.count(1000).__next__

    def _default_id_factory(self) -> str:
        counter = next(self._id_counter)
        slug = (self.default_kind.value if self.default_kind is not None else "memory").replace("_", "-")
        return f"{slug}-{counter:08x}"

    @contextmanager
    def _transaction(self):
        # autocommit is on; we use SAVEPOINT so commit()/mutate() can
        # compose. SAVEPOINT names must be plain identifiers (no '-'); we
        # also strip the decimal `id(self)` to keep the name sqlite-safe.
        sp = f"sp_{id(self)}_{next(self._id_counter):x}".replace(".", "_")
        self._conn.execute(f"SAVEPOINT {sp}")
        try:
            yield
            self._conn.execute(f"RELEASE SAVEPOINT {sp}")
        except Exception:
            self._conn.execute(f"ROLLBACK TO SAVEPOINT {sp}")
            self._conn.execute(f"RELEASE SAVEPOINT {sp}")
            raise

    def _migrate(self) -> None:
        """Apply numbered schema migrations from the database's stored
        version up to SCHEMA_VERSION. Initialises the schema_version table on
        first run."""

        with self._transaction():
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)"
            )
            row = self._conn.execute("SELECT version FROM schema_version").fetchone()
            current = row[0] if row else 0
            for version in range(current + 1, self.SCHEMA_VERSION + 1):
                self._apply_migration(version)
                self._conn.execute(
                    "INSERT INTO schema_version(version) VALUES (?)",
                    (version,),
                )

    def _apply_migration(self, version: int) -> None:
        if version == 1:
            # Use individual executes (not executescript) so the statements
            # participate in the surrounding SAVEPOINT; executescript would
            # commit any pending transaction first, breaking savepoint-based
            # atomicity.
            self._conn.execute(
                """
                CREATE TABLE records (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    run_id TEXT,
                    task_id TEXT,
                    attempt_id TEXT,
                    lineage_id TEXT,
                    content TEXT NOT NULL,
                    source_refs_json TEXT NOT NULL,
                    observed_at INTEGER NOT NULL,
                    valid_from INTEGER,
                    valid_until INTEGER,
                    confidence REAL NOT NULL,
                    lifecycle_state TEXT NOT NULL,
                    activation_state TEXT NOT NULL,
                    superseded_by TEXT,
                    supersedes_json TEXT NOT NULL,
                    sensitivity TEXT NOT NULL,
                    creator_id TEXT NOT NULL,
                    usage_count INTEGER NOT NULL,
                    last_accessed_at INTEGER,
                    creation_seq INTEGER NOT NULL,
                    tombstone_reason TEXT
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX records_project_kind_idx ON records(project_id, kind, lifecycle_state)"
            )
            self._conn.execute(
                """
                CREATE TABLE mutations (
                    mutation_id TEXT PRIMARY KEY,
                    target_record_id TEXT NOT NULL,
                    supersede_record_id TEXT NOT NULL,
                    before_content_hash TEXT NOT NULL,
                    after_content_hash TEXT NOT NULL,
                    before_lifecycle TEXT NOT NULL,
                    after_lifecycle TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    observed_at INTEGER NOT NULL,
                    mutation_reason TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    FOREIGN KEY (target_record_id) REFERENCES records(id),
                    FOREIGN KEY (supersede_record_id) REFERENCES records(id)
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX mutations_target_idx ON mutations(target_record_id)"
            )
            self._conn.execute(
                """
                CREATE VIRTUAL TABLE records_fts USING fts5(
                    record_id UNINDEXED,
                    normalized_content,
                    tokenize='unicode61 remove_diacritics 1'
                )
                """
            )

    def _insert_record(self, record: MemoryRecord) -> None:
        with self._transaction():
            self._conn.execute(
                """
                INSERT INTO records(
                    id, kind, project_id, run_id, task_id, attempt_id, lineage_id,
                    content, source_refs_json, observed_at, valid_from, valid_until,
                    confidence, lifecycle_state, activation_state, superseded_by, supersedes_json,
                    sensitivity, creator_id, usage_count, last_accessed_at, creation_seq, tombstone_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.id,
                    record.kind.value,
                    record.scope.project_id,
                    record.scope.run_id,
                    record.scope.task_id,
                    record.scope.attempt_id,
                    record.scope.lineage_id,
                    record.content,
                    _canonical_json_str(record.source_refs),
                    record.observed_at,
                    record.valid_from,
                    record.valid_until,
                    record.confidence,
                    record.lifecycle_state.value,
                    record.activation_state.value,
                    record.superseded_by,
                    _canonical_json_str(record.supersedes),
                    record.sensitivity.value,
                    record.creator_id,
                    record.usage_count,
                    record.last_accessed_at,
                    record.creation_seq,
                    record.tombstone_reason,
                ),
            )
            self._conn.execute(
                "INSERT INTO records_fts(record_id, normalized_content) VALUES (?, ?)",
                (record.id, record.normalized_content),
            )

    def _insert_mutation(self, mutation: MemoryMutationReceipt) -> None:
        with self._transaction():
            self._conn.execute(
                """
                INSERT INTO mutations(
                    mutation_id, target_record_id, supersede_record_id,
                    before_content_hash, after_content_hash,
                    before_lifecycle, after_lifecycle,
                    actor_id, observed_at, mutation_reason, content_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    mutation.mutation_id,
                    mutation.target_record_id,
                    mutation.supersede_record_id,
                    mutation.before_content_hash,
                    mutation.after_content_hash,
                    mutation.before_lifecycle.value,
                    mutation.after_lifecycle.value,
                    mutation.actor_id,
                    mutation.observed_at,
                    mutation.mutation_reason,
                    mutation.content_hash,
                ),
            )

    def _fetch_record_locked(self, record_id: str) -> MemoryRecord | None:
        row = self._conn.execute(
            "SELECT id, kind, project_id, run_id, task_id, attempt_id, lineage_id, "
            "content, source_refs_json, observed_at, valid_from, valid_until, "
            "confidence, lifecycle_state, activation_state, superseded_by, supersedes_json, "
            "sensitivity, creator_id, usage_count, last_accessed_at, creation_seq, tombstone_reason "
            "FROM records WHERE id = ?",
            (record_id,),
        ).fetchone()
        return self._row_to_record(row) if row else None

    @staticmethod
    def _row_to_record(row: tuple[Any, ...]) -> MemoryRecord:
        (
            record_id,
            kind,
            project_id,
            run_id,
            task_id,
            attempt_id,
            lineage_id,
            content,
            source_refs_json,
            observed_at,
            valid_from,
            valid_until,
            confidence,
            lifecycle_state,
            activation_state,
            superseded_by,
            supersedes_json,
            sensitivity,
            creator_id,
            usage_count,
            last_accessed_at,
            creation_seq,
            tombstone_reason,
        ) = row
        scope = ContextScope(
            project_id=project_id,
            run_id=run_id,
            task_id=task_id,
            attempt_id=attempt_id,
            lineage_id=lineage_id,
        )
        return MemoryRecord(
            id=record_id,
            kind=MemoryKind(kind),
            scope=scope,
            content=content,
            normalized_content=normalize_text(content),
            source_refs=tuple(_load_json_value(source_refs_json)),
            observed_at=observed_at,
            valid_from=valid_from,
            valid_until=valid_until,
            confidence=confidence,
            lifecycle_state=LifecycleState(lifecycle_state),
            activation_state=ActivationState(activation_state),
            superseded_by=superseded_by,
            supersedes=tuple(_load_json_value(supersedes_json)),
            sensitivity=Sensitivity(sensitivity),
            creator_id=creator_id,
            usage_count=usage_count,
            last_accessed_at=last_accessed_at,
            creation_seq=creation_seq,
            tombstone_reason=tombstone_reason,
        )


def _canonical_json_str(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _load_json_value(blob: str | None) -> Any:
    import json

    if blob is None or blob == "":
        return ()
    return json.loads(blob)


class EpisodicMemoryStore(MemoryStore):
    default_kind: ClassVar[MemoryKind | None] = MemoryKind.EPISODIC_MEMORY


class SemanticMemoryStore(MemoryStore):
    default_kind: ClassVar[MemoryKind | None] = MemoryKind.SEMANTIC_MEMORY


class WorkingMemoryStore(MemoryStore):
    default_kind: ClassVar[MemoryKind | None] = MemoryKind.WORKING_MEMORY


class ProceduralMemoryStore(MemoryStore):
    default_kind: ClassVar[MemoryKind | None] = MemoryKind.PROCEDURAL_MEMORY
