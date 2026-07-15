"""SQLite storage primitives for development-only remote workplan state."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


SCHEMA_VERSION = 2


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS backend_state (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    epoch INTEGER NOT NULL,
    backend TEXT NOT NULL,
    snapshot_hash TEXT
);
INSERT OR IGNORE INTO backend_state(singleton, epoch, backend)
VALUES (1, 1, 'remote');

CREATE TABLE IF NOT EXISTS cards (
    card_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    status TEXT NOT NULL,
    owner TEXT,
    revision INTEGER NOT NULL,
    definition_json TEXT NOT NULL,
    definition_hash TEXT NOT NULL,
    authority_grant_json TEXT NOT NULL DEFAULT '{}',
    repository_id TEXT NOT NULL DEFAULT '',
    source_revision TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 0,
    fencing_generation INTEGER NOT NULL DEFAULT 0,
    attention_required INTEGER NOT NULL DEFAULT 0,
    revalidation_required INTEGER NOT NULL DEFAULT 0,
    integrated_by TEXT,
    integrated_commit TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS dependencies (
    card_id TEXT NOT NULL REFERENCES cards(card_id) ON DELETE CASCADE,
    dependency_id TEXT NOT NULL,
    PRIMARY KEY(card_id, dependency_id)
);

CREATE TABLE IF NOT EXISTS card_paths (
    card_id TEXT NOT NULL REFERENCES cards(card_id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    PRIMARY KEY(card_id, path)
);

CREATE TABLE IF NOT EXISTS path_reservations (
    card_id TEXT NOT NULL REFERENCES cards(card_id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    fencing_token INTEGER NOT NULL,
    PRIMARY KEY(card_id, path)
);
CREATE INDEX IF NOT EXISTS reservations_path_idx ON path_reservations(path);

CREATE TABLE IF NOT EXISTS credentials (
    credential_id TEXT PRIMARY KEY,
    actor TEXT NOT NULL,
    secret_hash TEXT NOT NULL UNIQUE,
    scopes_json TEXT NOT NULL,
    issued_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    revoked_at INTEGER
);

CREATE TABLE IF NOT EXISTS claims (
    claim_id TEXT PRIMARY KEY,
    card_id TEXT NOT NULL REFERENCES cards(card_id),
    owner TEXT NOT NULL,
    fencing_token INTEGER NOT NULL,
    definition_hash TEXT NOT NULL,
    claimed_at INTEGER NOT NULL,
    heartbeat_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    closed_at INTEGER,
    close_reason TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS one_open_claim_per_card
ON claims(card_id) WHERE closed_at IS NULL;
CREATE INDEX IF NOT EXISTS open_claim_owner
ON claims(owner) WHERE closed_at IS NULL;

CREATE TABLE IF NOT EXISTS worktree_bindings (
    claim_id TEXT PRIMARY KEY REFERENCES claims(claim_id),
    card_id TEXT NOT NULL REFERENCES cards(card_id),
    fencing_token INTEGER NOT NULL,
    repository_id TEXT NOT NULL,
    worktree_path TEXT NOT NULL,
    branch TEXT NOT NULL,
    base_commit TEXT NOT NULL,
    bound_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS claim_bundles (
    claim_id TEXT NOT NULL REFERENCES claims(claim_id),
    fencing_token INTEGER NOT NULL,
    bundle_json TEXT NOT NULL,
    bundle_hash TEXT NOT NULL UNIQUE,
    created_at INTEGER NOT NULL,
    PRIMARY KEY(claim_id, fencing_token)
);
CREATE TRIGGER IF NOT EXISTS claim_bundles_immutable_update
BEFORE UPDATE ON claim_bundles BEGIN SELECT RAISE(ABORT, 'claim bundles are immutable'); END;
CREATE TRIGGER IF NOT EXISTS claim_bundles_immutable_delete
BEFORE DELETE ON claim_bundles BEGIN SELECT RAISE(ABORT, 'claim bundles are immutable'); END;

CREATE TABLE IF NOT EXISTS receipts (
    receipt_id INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id TEXT NOT NULL REFERENCES cards(card_id),
    kind TEXT NOT NULL,
    actor TEXT NOT NULL,
    revision_from INTEGER NOT NULL,
    revision_to INTEGER NOT NULL,
    fencing_token INTEGER,
    at INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    previous_hash TEXT,
    receipt_hash TEXT NOT NULL UNIQUE
);
CREATE TRIGGER IF NOT EXISTS receipts_immutable_update
BEFORE UPDATE ON receipts BEGIN SELECT RAISE(ABORT, 'receipts are immutable'); END;
CREATE TRIGGER IF NOT EXISTS receipts_immutable_delete
BEFORE DELETE ON receipts BEGIN SELECT RAISE(ABORT, 'receipts are immutable'); END;

CREATE TABLE IF NOT EXISTS webhook_inbox (
    delivery_id TEXT PRIMARY KEY,
    event_timestamp_ms INTEGER NOT NULL,
    received_at_ms INTEGER NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS projection_outbox (
    outbox_id INTEGER PRIMARY KEY AUTOINCREMENT,
    dedupe_key TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at_ms INTEGER NOT NULL,
    attempted_at_ms INTEGER,
    sent_at_ms INTEGER,
    error_code TEXT,
    error_message TEXT,
    needs_attention INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS projection_attention (
    attention_id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    details_json TEXT NOT NULL,
    created_at_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS backend_receipts (
    receipt_id INTEGER PRIMARY KEY AUTOINCREMENT,
    epoch_from INTEGER NOT NULL,
    epoch_to INTEGER NOT NULL,
    backend_from TEXT NOT NULL,
    backend_to TEXT NOT NULL,
    snapshot_hash TEXT NOT NULL,
    actor TEXT NOT NULL,
    at INTEGER NOT NULL,
    previous_hash TEXT,
    receipt_hash TEXT NOT NULL UNIQUE
);
CREATE TRIGGER IF NOT EXISTS backend_receipts_immutable_update
BEFORE UPDATE ON backend_receipts BEGIN SELECT RAISE(ABORT, 'backend receipts are immutable'); END;
CREATE TRIGGER IF NOT EXISTS backend_receipts_immutable_delete
BEFORE DELETE ON backend_receipts BEGIN SELECT RAISE(ABORT, 'backend receipts are immutable'); END;
"""


class SQLiteStore:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        connection = self.connect()
        try:
            connection.executescript(SCHEMA)
            card_columns = {row[1] for row in connection.execute("PRAGMA table_info(cards)")}
            if "authority_grant_json" not in card_columns:
                connection.execute(
                    "ALTER TABLE cards ADD COLUMN authority_grant_json TEXT NOT NULL DEFAULT '{}'"
                )
            if "repository_id" not in card_columns:
                connection.execute(
                    "ALTER TABLE cards ADD COLUMN repository_id TEXT NOT NULL DEFAULT ''"
                )
            backend_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(backend_state)")
            }
            if "snapshot_hash" not in backend_columns:
                connection.execute("ALTER TABLE backend_state ADD COLUMN snapshot_hash TEXT")
            connection.execute(
                "INSERT OR REPLACE INTO schema_meta(key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            connection.commit()
        finally:
            connection.close()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.db_path, timeout=30.0, isolation_level=None, check_same_thread=False
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            yield connection
        finally:
            connection.close()
