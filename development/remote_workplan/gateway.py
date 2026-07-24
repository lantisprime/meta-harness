"""Transactional cross-host authority for development workplan claims.

All state-changing operations acquire a SQLite ``BEGIN IMMEDIATE`` transaction.
Linear and MCP are adapters around this authority; neither is an ownership
source.  No Meta-Harness runtime package is imported here.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import posixpath
import re
import secrets
import sqlite3
import time
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any, Callable, Iterable, Mapping, Sequence
from uuid import uuid4

from .models import InboxWriteResult, IssuedCredential, OutboxRecord
from .store import SQLiteStore


ACTIVE_STATUSES = frozenset({"claimed", "in_progress", "review", "verifying", "blocked"})
TERMINAL_STATUSES = frozenset({"done", "cancelled"})
ALL_SCOPES = frozenset(
    {
        "qualify", "claim", "bind", "heartbeat", "checkpoint", "block", "resume",
        "submit", "integrate", "accept", "revalidate", "requeue", "reassign",
        "cancel", "backend_epoch", "list",
    }
)
COORDINATOR_SCOPES = frozenset(
    {"qualify", "integrate", "accept", "revalidate", "requeue", "reassign", "cancel", "backend_epoch"}
)
REQUIRED_DEFINITION_FIELDS = frozenset(
    {
        "sourceRevision", "definitionHash", "worktreePath", "branch", "baseCommit",
        "currentHead", "plane", "frozenAxes", "budget", "stopCondition",
        "evaluatorAuthority", "acceptanceCommands", "nextCheckpoint",
        "allowedOwnerNamespaces",
    }
)
EXACT_AUTHORITY_GRANT = {
    "executionCoordination": True,
    "evaluator": False,
    "merge": False,
    "promotion": False,
    "deployment": False,
    "credentials": False,
    "runtime": False,
}
BACKEND_NAMES = frozenset({"remote", "filesystem"})


class GatewayError(RuntimeError):
    """A stable, deterministic failure suitable for remote facade mapping."""

    def __init__(self, code: str, message: str, current_revision: int | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.current_revision = current_revision

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.current_revision is not None:
            result["current_revision"] = self.current_revision
        return result


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def compute_definition_hash(definition: Mapping[str, Any]) -> str:
    material = dict(definition)
    material.pop("definitionHash", None)
    material.pop("definition_hash", None)
    return _sha256(_canonical(material))


def normalize_path(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GatewayError("invalid_path", "owned path must be a non-empty string")
    candidate = value.strip()
    if "\\" in candidate:
        raise GatewayError("invalid_path", "owned paths must use POSIX separators")
    if candidate.startswith("/") or (len(candidate) >= 2 and candidate[1] == ":"):
        raise GatewayError("invalid_path", "owned path must be repository-relative")
    if any(part in {"", ".", ".."} for part in candidate.split("/")):
        raise GatewayError("invalid_path", "owned path must not contain empty or dot segments")
    normalized = posixpath.normpath(candidate)
    if normalized in {"", ".", ".."} or normalized.startswith("../"):
        raise GatewayError("invalid_path", "owned path escapes or names the repository root")
    if any(part in {"", ".", ".."} for part in PurePosixPath(normalized).parts):
        raise GatewayError("invalid_path", "owned path is not normalized")
    return normalized


def _overlaps(left: str, right: str) -> bool:
    return left == right or left.startswith(right + "/") or right.startswith(left + "/")


class RemoteWorkplanGateway:
    """Reference SQLite remote gateway.

    ``clock`` returns seconds since epoch (or a :class:`datetime`) and is
    injectable for deterministic expiry tests. Public methods are keyword-only
    except the database path constructor argument.
    """

    def __init__(self, db_path: str, clock: Callable[[], int | float | datetime] | None = None):
        self.store = SQLiteStore(db_path)
        self._clock = clock or time.time

    def _now(self) -> int:
        value = self._clock()
        return int(value.timestamp() if isinstance(value, datetime) else value)

    @staticmethod
    def _credential_hash(credential: str) -> str:
        return hashlib.sha256(credential.encode("utf-8")).hexdigest()

    def issue_host_credential(
        self, *, actor: str, scopes: Iterable[str], ttl_seconds: int = 300
    ) -> dict[str, Any]:
        if not actor or actor.count(":") < 2:
            raise GatewayError("invalid_actor", "actor must include namespace, host, and session")
        normalized_scopes = tuple(sorted(set(scopes)))
        if not normalized_scopes or not set(normalized_scopes) <= ALL_SCOPES:
            raise GatewayError("invalid_scope", "credential scopes contain an unsupported action")
        if ttl_seconds <= 0:
            raise GatewayError("invalid_ttl", "credential ttl must be positive")
        now = self._now()
        secret = "rwg_" + secrets.token_urlsafe(32)
        credential_id = str(uuid4())
        with self.store.transaction() as db:
            db.execute(
                "INSERT INTO credentials VALUES (?, ?, ?, ?, ?, ?, NULL)",
                (credential_id, actor, self._credential_hash(secret), _canonical(normalized_scopes), now, now + ttl_seconds),
            )
        return IssuedCredential(secret, credential_id, actor, normalized_scopes, now + ttl_seconds).to_dict()

    def revoke_host_credential(self, *, credential: str) -> None:
        with self.store.transaction() as db:
            cursor = db.execute(
                "UPDATE credentials SET revoked_at=? WHERE secret_hash=? AND revoked_at IS NULL",
                (self._now(), self._credential_hash(credential)),
            )
            if cursor.rowcount != 1:
                raise GatewayError("invalid_credential", "credential is unknown or revoked")

    def _authorize(self, db: sqlite3.Connection, credential: str, scope: str, *, coordinator: bool = False) -> str:
        row = db.execute(
            "SELECT * FROM credentials WHERE secret_hash=?", (self._credential_hash(credential),)
        ).fetchone()
        now = self._now()
        if row is None or row["revoked_at"] is not None:
            raise GatewayError("invalid_credential", "credential is unknown or revoked")
        if row["expires_at"] <= now:
            raise GatewayError("credential_expired", "credential has expired")
        if scope not in json.loads(row["scopes_json"]):
            raise GatewayError("scope_denied", f"credential does not allow {scope}")
        actor = row["actor"]
        if coordinator and not actor.startswith("coordinator:"):
            raise GatewayError("coordinator_required", f"{scope} requires coordinator authority")
        return actor

    @staticmethod
    def _card(db: sqlite3.Connection, card_id: str) -> sqlite3.Row:
        row = db.execute("SELECT * FROM cards WHERE card_id=?", (card_id,)).fetchone()
        if row is None:
            raise GatewayError("card_not_found", f"card {card_id} does not exist")
        return row

    @staticmethod
    def _expected(card: sqlite3.Row, expected_revision: int) -> None:
        if card["revision"] != expected_revision:
            raise GatewayError(
                "stale_revision",
                f"expected revision {expected_revision}, current revision is {card['revision']}",
                card["revision"],
            )

    @staticmethod
    def _definition(card: sqlite3.Row, expected_definition_hash: str | None) -> None:
        if expected_definition_hash is not None and not hmac.compare_digest(
            card["definition_hash"], expected_definition_hash
        ):
            raise GatewayError("definition_changed", "card definition hash no longer matches")

    @staticmethod
    def _validate_definition_contract(definition: Mapping[str, Any]) -> str:
        missing = sorted(REQUIRED_DEFINITION_FIELDS - set(definition))
        if missing:
            raise GatewayError("invalid_definition", "definition missing required fields: " + ", ".join(missing))
        extra = sorted(set(definition) - REQUIRED_DEFINITION_FIELDS)
        if extra:
            raise GatewayError("invalid_definition", "definition contains unsupported fields: " + ", ".join(extra))
        canonical_hash = compute_definition_hash(definition)
        if definition["definitionHash"] != canonical_hash:
            raise GatewayError("definition_hash_mismatch", "declared definition hash is not canonical")
        scalar_fields = (
            "sourceRevision", "worktreePath", "branch", "baseCommit", "currentHead",
            "budget", "stopCondition", "evaluatorAuthority", "nextCheckpoint",
        )
        if any(not isinstance(definition[field], str) or not definition[field].strip() for field in scalar_fields):
            raise GatewayError("invalid_definition", "required definition strings must be non-empty")
        if definition["plane"] != "development":
            raise GatewayError("invalid_definition", "remote workplan cards must use the development plane")
        if definition["frozenAxes"] != ["H", "E", "W"]:
            raise GatewayError("invalid_definition", "definition must freeze H, E, and W")
        worktree_path = definition["worktreePath"]
        if (
            not worktree_path.startswith("/")
            or "\\" in worktree_path
            or posixpath.normpath(worktree_path) != worktree_path
        ):
            raise GatewayError(
                "invalid_definition", "worktreePath must be an absolute normalized POSIX path"
            )
        for field in ("acceptanceCommands", "allowedOwnerNamespaces"):
            value = definition[field]
            if not isinstance(value, list) or not value or any(not isinstance(item, str) or not item for item in value):
                raise GatewayError("invalid_definition", f"{field} must be a non-empty string list")
        namespaces = definition["allowedOwnerNamespaces"]
        if len(namespaces) != len(set(namespaces)) or not set(namespaces) <= {"codex", "claude", "dev-orchestrator", "pi"}:
            raise GatewayError("invalid_definition", "allowedOwnerNamespaces must be a unique supported subset")
        return canonical_hash

    def _bundle_material(self, db: sqlite3.Connection, card: sqlite3.Row, claim: sqlite3.Row) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "card_id": card["card_id"],
            "title": card["title"],
            "definition": json.loads(card["definition_json"]),
            "definition_hash": card["definition_hash"],
            "source_revision": card["source_revision"],
            "authority_grant": json.loads(card["authority_grant_json"]),
            "repository_id": card["repository_id"],
            "paths": self._paths(db, card["card_id"]),
            "dependencies": self._dependencies(db, card["card_id"]),
            "claim_id": claim["claim_id"],
            "owner": claim["owner"],
            "fencing_token": claim["fencing_token"],
        }

    def _store_claim_bundle(self, db: sqlite3.Connection, card: sqlite3.Row, claim: sqlite3.Row) -> dict[str, Any]:
        bundle = self._bundle_material(db, card, claim)
        bundle_hash = _sha256(_canonical(bundle))
        db.execute(
            "INSERT INTO claim_bundles(claim_id,fencing_token,bundle_json,bundle_hash,created_at) VALUES(?,?,?,?,?)",
            (claim["claim_id"], claim["fencing_token"], _canonical(bundle), bundle_hash, self._now()),
        )
        bundle["bundle_hash"] = bundle_hash
        return bundle

    def _validate_claim_bundle(self, db: sqlite3.Connection, card: sqlite3.Row, claim: sqlite3.Row) -> dict[str, Any]:
        row = db.execute(
            "SELECT bundle_json,bundle_hash FROM claim_bundles WHERE claim_id=? AND fencing_token=?",
            (claim["claim_id"], claim["fencing_token"]),
        ).fetchone()
        if row is None or _sha256(row["bundle_json"]) != row["bundle_hash"]:
            raise GatewayError("claim_bundle_invalid", "immutable claim bundle is missing or corrupt", card["revision"])
        bundle = json.loads(row["bundle_json"])
        if bundle != self._bundle_material(db, card, claim):
            raise GatewayError("claim_bundle_invalid", "claim bundle no longer matches canonical state", card["revision"])
        bundle["bundle_hash"] = row["bundle_hash"]
        return bundle

    def _receipt(
        self, db: sqlite3.Connection, *, card_id: str, kind: str, actor: str,
        revision_from: int, revision_to: int, fencing_token: int | None,
        payload: Mapping[str, Any] | None = None,
    ) -> str:
        previous = db.execute(
            "SELECT receipt_hash FROM receipts WHERE card_id=? ORDER BY receipt_id DESC LIMIT 1", (card_id,)
        ).fetchone()
        previous_hash = previous[0] if previous else None
        at = self._now()
        body = {
            "card_id": card_id, "kind": kind, "actor": actor,
            "revision_from": revision_from, "revision_to": revision_to,
            "fencing_token": fencing_token, "at": at,
            "payload": dict(payload or {}), "previous_hash": previous_hash,
        }
        receipt_hash = _sha256(_canonical(body))
        db.execute(
            "INSERT INTO receipts(card_id,kind,actor,revision_from,revision_to,fencing_token,at,payload_json,previous_hash,receipt_hash) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (card_id, kind, actor, revision_from, revision_to, fencing_token, at, _canonical(payload or {}), previous_hash, receipt_hash),
        )
        activity = {
            "subject_id": card_id,
            "action": kind,
            "canonical_revision": revision_to,
            "occurred_at_ms": at * 1000,
            "actor": actor,
            "receipt_hash": receipt_hash,
        }
        db.execute(
            "INSERT OR IGNORE INTO projection_outbox(dedupe_key,kind,payload_json,created_at_ms) VALUES(?,?,?,?)",
            ("receipt:" + receipt_hash, "activity", _canonical(activity), at * 1000),
        )
        return receipt_hash

    @staticmethod
    def _paths(db: sqlite3.Connection, card_id: str) -> list[str]:
        return [row[0] for row in db.execute("SELECT path FROM card_paths WHERE card_id=? ORDER BY path", (card_id,))]

    @staticmethod
    def _dependencies(db: sqlite3.Connection, card_id: str) -> list[str]:
        return [row[0] for row in db.execute("SELECT dependency_id FROM dependencies WHERE card_id=? ORDER BY dependency_id", (card_id,))]

    def _card_dict(self, db: sqlite3.Connection, card: sqlite3.Row) -> dict[str, Any]:
        return {
            "card_id": card["card_id"], "title": card["title"], "status": card["status"],
            "owner": card["owner"], "revision": card["revision"],
            "definition_hash": card["definition_hash"], "source_revision": card["source_revision"],
            "priority": card["priority"], "paths": self._paths(db, card["card_id"]),
            "dependencies": self._dependencies(db, card["card_id"]),
            "fencing_token": card["fencing_generation"],
            "attention_required": bool(card["attention_required"]),
            "revalidation_required": bool(card["revalidation_required"]),
        }

    def list_cards(self, *, credential: str | None = None) -> list[dict[str, Any]]:
        with self.store.read() as db:
            if credential is not None:
                self._authorize(db, credential, "list")
            rows = db.execute("SELECT * FROM cards ORDER BY priority, card_id").fetchall()
            return [self._card_dict(db, row) for row in rows]

    def add_card(self, **kwargs: Any) -> dict[str, Any]:
        kwargs.setdefault("ready", False)
        return self.qualify_card(**kwargs)

    def qualify_card(
        self, *, card_id: str, title: str, paths: Sequence[str], definition: Mapping[str, Any],
        dependencies: Sequence[str] = (), priority: int = 0, credential: str,
        expected_revision: int | None = None, ready: bool = True,
        authority_grant: Mapping[str, Any] | None = None,
        repository_id: str,
    ) -> dict[str, Any]:
        normalized_paths = sorted({normalize_path(path) for path in paths})
        if not normalized_paths:
            raise GatewayError("invalid_paths", "a qualified card needs at least one owned path")
        definition_hash = self._validate_definition_contract(definition)
        if dict(authority_grant or {}) != EXACT_AUTHORITY_GRANT:
            raise GatewayError("invalid_authority_grant", "authority grant must match bounded development semantics")
        if not isinstance(repository_id, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", repository_id):
            raise GatewayError("invalid_repository", "repository identity must be a coordinator-frozen sha256 fingerprint")
        source_revision = str(definition.get("sourceRevision", definition.get("source_revision", "")))
        if not source_revision:
            raise GatewayError("invalid_definition", "definition needs sourceRevision")
        now = self._now()
        with self.store.transaction() as db:
            actor = self._authorize(db, credential, "qualify", coordinator=True)
            existing = db.execute("SELECT * FROM cards WHERE card_id=?", (card_id,)).fetchone()
            status = "ready" if ready else "backlog"
            if ready:
                for dependency in sorted(set(dependencies)):
                    dependency_row = db.execute("SELECT status FROM cards WHERE card_id=?", (dependency,)).fetchone()
                    if dependency_row is None or dependency_row["status"] != "done":
                        raise GatewayError("dependency_unmet", f"dependency {dependency} is not Done")
                reservations = db.execute("SELECT card_id,path FROM path_reservations WHERE card_id<>?", (card_id,)).fetchall()
                for path in normalized_paths:
                    for reservation in reservations:
                        if _overlaps(path, reservation["path"]):
                            raise GatewayError("path_conflict", f"path {path} conflicts with card {reservation['card_id']}")
            if existing is None:
                if expected_revision not in (None, 0):
                    raise GatewayError("stale_revision", "new card revision is 0", 0)
                db.execute(
                    "INSERT INTO cards(card_id,title,status,owner,revision,definition_json,definition_hash,authority_grant_json,repository_id,source_revision,priority,created_at,updated_at) VALUES(?,?,?,NULL,1,?,?,?,?,?,?,?,?)",
                    (card_id, title, status, _canonical(definition), definition_hash, _canonical(authority_grant), repository_id, source_revision, priority, now, now),
                )
                revision_from, revision_to = 0, 1
            else:
                if existing["status"] not in {"backlog", "ready"}:
                    raise GatewayError("invalid_transition", "only backlog or ready cards can be requalified", existing["revision"])
                if expected_revision is None:
                    raise GatewayError("expected_revision_required", "requalification needs expected_revision", existing["revision"])
                self._expected(existing, expected_revision)
                revision_from, revision_to = existing["revision"], existing["revision"] + 1
                db.execute(
                    "UPDATE cards SET title=?,status=?,revision=?,definition_json=?,definition_hash=?,authority_grant_json=?,repository_id=?,source_revision=?,priority=?,updated_at=? WHERE card_id=?",
                    (title, status, revision_to, _canonical(definition), definition_hash, _canonical(authority_grant), repository_id, source_revision, priority, now, card_id),
                )
                db.execute("DELETE FROM card_paths WHERE card_id=?", (card_id,))
                db.execute("DELETE FROM dependencies WHERE card_id=?", (card_id,))
                db.execute("DELETE FROM path_reservations WHERE card_id=?", (card_id,))
            db.executemany("INSERT INTO card_paths(card_id,path) VALUES(?,?)", ((card_id, path) for path in normalized_paths))
            db.executemany("INSERT INTO dependencies(card_id,dependency_id) VALUES(?,?)", ((card_id, dep) for dep in sorted(set(dependencies))))
            if ready:
                db.executemany("INSERT INTO path_reservations(card_id,path,fencing_token) VALUES(?,?,0)", ((card_id, path) for path in normalized_paths))
            receipt_hash = self._receipt(db, card_id=card_id, kind="qualify", actor=actor, revision_from=revision_from, revision_to=revision_to, fencing_token=None, payload={"definition_hash": definition_hash, "status": status})
            card = self._card(db, card_id)
            result = self._card_dict(db, card)
            result["receipt_hash"] = receipt_hash
            return result

    def _expiry_preflight(self, card_id: str) -> None:
        """Persist attention before raising; never transfer or close ownership."""
        expired_revision: int | None = None
        with self.store.transaction() as db:
            card = self._card(db, card_id)
            claim = db.execute("SELECT * FROM claims WHERE card_id=? AND closed_at IS NULL", (card_id,)).fetchone()
            if claim is not None and claim["expires_at"] <= self._now():
                if not card["attention_required"]:
                    revision = card["revision"] + 1
                    db.execute("UPDATE cards SET attention_required=1,revision=?,updated_at=? WHERE card_id=?", (revision, self._now(), card_id))
                    self._receipt(db, card_id=card_id, kind="lease_expired", actor="gateway:expiry:sweep", revision_from=card["revision"], revision_to=revision, fencing_token=card["fencing_generation"], payload={"claim_id": claim["claim_id"]})
                    expired_revision = revision
                else:
                    expired_revision = card["revision"]
        if expired_revision is not None:
            raise GatewayError("fence_expired", "claim lease expired; coordinator action is required", expired_revision)

    def _active_claim(self, db: sqlite3.Connection, card: sqlite3.Row, fencing_token: int, actor: str) -> sqlite3.Row:
        claim = db.execute("SELECT * FROM claims WHERE card_id=? AND closed_at IS NULL", (card["card_id"],)).fetchone()
        if claim is None:
            raise GatewayError("claim_not_active", "card has no active claim", card["revision"])
        if claim["owner"] != actor:
            raise GatewayError("owner_mismatch", "credential actor does not own the claim", card["revision"])
        if claim["fencing_token"] != fencing_token or card["fencing_generation"] != fencing_token:
            raise GatewayError("stale_fence", "fencing token is stale", card["revision"])
        self._validate_claim_bundle(db, card, claim)
        if card["revalidation_required"]:
            raise GatewayError("definition_changed", "claim is frozen pending coordinator revalidation", card["revision"])
        return claim

    def _coordinator_fence(self, db: sqlite3.Connection, card: sqlite3.Row, fencing_token: int) -> sqlite3.Row:
        claim = db.execute("SELECT * FROM claims WHERE card_id=? AND closed_at IS NULL", (card["card_id"],)).fetchone()
        if claim is None:
            raise GatewayError("claim_not_active", "coordinator transition requires an active claim", card["revision"])
        if fencing_token != claim["fencing_token"] or fencing_token != card["fencing_generation"]:
            raise GatewayError("stale_fence", "fencing token is stale", card["revision"])
        self._validate_claim_bundle(db, card, claim)
        return claim

    @staticmethod
    def _lineage_value(value: str, field: str) -> None:
        if not isinstance(value, str) or not value or value.strip() != value or any(character.isspace() for character in value):
            raise GatewayError("invalid_lineage", f"{field} must be a non-empty whitespace-free value")

    def _validate_lineage(
        self, db: sqlite3.Connection, card: sqlite3.Row, claim: sqlite3.Row, *,
        repository_id: str, branch: str, base_commit: str, head_commit: str,
        ancestry: Mapping[str, Any],
    ) -> sqlite3.Row:
        for field, value in (("repository_id", repository_id), ("branch", branch), ("base_commit", base_commit), ("head_commit", head_commit)):
            self._lineage_value(value, field)
        binding = db.execute("SELECT * FROM worktree_bindings WHERE claim_id=?", (claim["claim_id"],)).fetchone()
        if binding is None:
            raise GatewayError("worktree_not_bound", "bind the claimed worktree before progress or submit")
        expected = json.loads(card["definition_json"])
        if (
            binding["repository_id"] != repository_id
            or binding["branch"] != branch
            or binding["base_commit"] != base_commit
            or binding["worktree_path"] != expected["worktreePath"]
            or branch != expected["branch"]
            or base_commit != expected["baseCommit"]
        ):
            raise GatewayError("lineage_mismatch", "repository, worktree, branch, or base commit differs from the frozen definition")
        if (
            set(ancestry) != {"base_commit", "head_commit", "is_descendant", "verification"}
            or ancestry.get("base_commit") != base_commit
            or ancestry.get("head_commit") != head_commit
            or ancestry.get("is_descendant") is not True
            or not isinstance(ancestry.get("verification"), str)
            or not ancestry["verification"]
        ):
            raise GatewayError("invalid_lineage", "head lineage requires structured descendant evidence")
        return binding

    def claim(
        self, *, card_id: str, expected_revision: int, expected_definition_hash: str,
        credential: str, lease_seconds: int = 120,
    ) -> dict[str, Any]:
        if lease_seconds <= 0:
            raise GatewayError("invalid_lease", "claim lease must be positive")
        with self.store.transaction() as db:
            actor = self._authorize(db, credential, "claim")
            if actor.startswith("coordinator:"):
                raise GatewayError("worker_required", "coordinator credentials cannot claim coding work")
            card = self._card(db, card_id)
            self._expected(card, expected_revision)
            self._definition(card, expected_definition_hash)
            if card["status"] != "ready":
                raise GatewayError("card_not_ready", "only a Ready card can be claimed", card["revision"])
            definition = json.loads(card["definition_json"])
            namespace = actor.split(":", 1)[0]
            if namespace not in definition["allowedOwnerNamespaces"]:
                raise GatewayError("owner_namespace_denied", f"owner namespace {namespace} is not allowed")
            active = db.execute("SELECT card_id FROM claims WHERE owner=? AND closed_at IS NULL", (actor,)).fetchone()
            if active is not None:
                raise GatewayError("wip_limit", f"owner already holds {active['card_id']}")
            for dependency in self._dependencies(db, card_id):
                dep = db.execute("SELECT status FROM cards WHERE card_id=?", (dependency,)).fetchone()
                if dep is None or dep["status"] != "done":
                    raise GatewayError("dependency_unmet", f"dependency {dependency} is not Done")
            paths = self._paths(db, card_id)
            reservations = db.execute("SELECT card_id,path FROM path_reservations WHERE card_id<>?", (card_id,)).fetchall()
            for path in paths:
                for reservation in reservations:
                    if _overlaps(path, reservation["path"]):
                        raise GatewayError("path_conflict", f"path {path} conflicts with card {reservation['card_id']}")
            fence = card["fencing_generation"] + 1
            claim_id, now = str(uuid4()), self._now()
            db.execute("UPDATE cards SET status='claimed',owner=?,revision=revision+1,fencing_generation=?,attention_required=0,updated_at=? WHERE card_id=?", (actor, fence, now, card_id))
            db.execute("INSERT INTO claims VALUES(?,?,?,?,?,?,?,?,NULL,NULL)", (claim_id, card_id, actor, fence, card["definition_hash"], now, now, now + lease_seconds))
            updated = db.execute("UPDATE path_reservations SET fencing_token=? WHERE card_id=?", (fence, card_id)).rowcount
            if updated != len(paths):
                raise GatewayError("reservation_lost", "Ready path reservations are incomplete")
            revision = card["revision"] + 1
            claim_row = db.execute("SELECT * FROM claims WHERE claim_id=?", (claim_id,)).fetchone()
            bundle = self._store_claim_bundle(db, card, claim_row)
            receipt_hash = self._receipt(db, card_id=card_id, kind="claim", actor=actor, revision_from=card["revision"], revision_to=revision, fencing_token=fence, payload={"claim_id": claim_id, "definition_hash": card["definition_hash"], "paths": paths})
            bundle["receipt_hash"] = receipt_hash
            return {"claim_id": claim_id, "card_id": card_id, "owner": actor, "fencing_token": fence, "revision": revision, "expires_at": now + lease_seconds, "task_bundle": bundle, "receipt_hash": receipt_hash}

    def _transition_owner(
        self, *, scope: str, card_id: str, expected_revision: int, fencing_token: int,
        credential: str, allowed: set[str], target: str, kind: str,
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._expiry_preflight(card_id)
        with self.store.transaction() as db:
            actor = self._authorize(db, credential, scope)
            card = self._card(db, card_id)
            self._expected(card, expected_revision)
            claim = self._active_claim(db, card, fencing_token, actor)
            if card["status"] not in allowed:
                raise GatewayError("invalid_transition", f"cannot {kind} from {card['status']}", card["revision"])
            if kind in {"checkpoint", "submit"}:
                assert payload is not None
                self._validate_lineage(
                    db, card, claim,
                    repository_id=str(payload["repository_id"]), branch=str(payload["branch"]),
                    base_commit=str(payload["base_commit"]), head_commit=str(payload["head_commit"]),
                    ancestry=payload["ancestry"],
                )
            if kind == "resume" and db.execute("SELECT COUNT(*) FROM path_reservations WHERE card_id=?", (card_id,)).fetchone()[0] == 0:
                paths = self._paths(db, card_id)
                reservations = db.execute("SELECT card_id,path FROM path_reservations WHERE card_id<>?", (card_id,)).fetchall()
                for path in paths:
                    for reservation in reservations:
                        if _overlaps(path, reservation["path"]):
                            raise GatewayError("path_conflict", f"path {path} conflicts with card {reservation['card_id']}")
                db.executemany("INSERT INTO path_reservations(card_id,path,fencing_token) VALUES(?,?,?)", ((card_id, path, fencing_token) for path in paths))
            if kind == "block" and payload is not None and not payload.get("retain_paths", True):
                db.execute("DELETE FROM path_reservations WHERE card_id=?", (card_id,))
            revision = card["revision"] + 1
            db.execute("UPDATE cards SET status=?,revision=?,updated_at=? WHERE card_id=?", (target, revision, self._now(), card_id))
            receipt_hash = self._receipt(db, card_id=card_id, kind=kind, actor=actor, revision_from=card["revision"], revision_to=revision, fencing_token=fencing_token, payload=payload)
            return {"card_id": card_id, "status": target, "revision": revision, "fencing_token": fencing_token, "receipt_hash": receipt_hash}

    def bind_worktree(
        self, *, card_id: str, expected_revision: int, fencing_token: int, credential: str,
        repository_id: str, worktree_path: str, branch: str, base_commit: str,
    ) -> dict[str, Any]:
        if not all((repository_id, worktree_path, branch, base_commit)):
            raise GatewayError("invalid_worktree", "complete repository/worktree lineage is required")
        self._expiry_preflight(card_id)
        with self.store.transaction() as db:
            actor = self._authorize(db, credential, "bind")
            card = self._card(db, card_id); self._expected(card, expected_revision)
            claim = self._active_claim(db, card, fencing_token, actor)
            prior = db.execute("SELECT * FROM worktree_bindings WHERE claim_id=?", (claim["claim_id"],)).fetchone()
            definition = json.loads(card["definition_json"])
            if worktree_path != definition["worktreePath"] or branch != definition["branch"] or base_commit != definition["baseCommit"]:
                raise GatewayError("lineage_mismatch", "worktree, branch, and base commit must match the frozen definition")
            if repository_id != card["repository_id"]:
                raise GatewayError("repository_mismatch", "repository identity differs from the coordinator-frozen fingerprint")
            for field, value in (("repository_id", repository_id), ("branch", branch), ("base_commit", base_commit)):
                self._lineage_value(value, field)
            values = (repository_id, worktree_path, branch, base_commit)
            if prior is not None:
                if tuple(prior[key] for key in ("repository_id", "worktree_path", "branch", "base_commit")) == values:
                    return {"card_id": card_id, "revision": card["revision"], "fencing_token": fencing_token, "receipt_hash": None, "idempotent": True}
                raise GatewayError("worktree_already_bound", "claim is already bound to different lineage")
            revision = card["revision"] + 1; now = self._now()
            db.execute("INSERT INTO worktree_bindings VALUES(?,?,?,?,?,?,?,?)", (claim["claim_id"], card_id, fencing_token, *values, now))
            db.execute("UPDATE cards SET status='in_progress',revision=?,updated_at=? WHERE card_id=?", (revision, now, card_id))
            receipt_hash = self._receipt(db, card_id=card_id, kind="bind_worktree", actor=actor, revision_from=card["revision"], revision_to=revision, fencing_token=fencing_token, payload={"repository_id": repository_id, "worktree_path": worktree_path, "branch": branch, "base_commit": base_commit})
            return {"card_id": card_id, "status": "in_progress", "revision": revision, "fencing_token": fencing_token, "receipt_hash": receipt_hash}

    def heartbeat(self, *, card_id: str, fencing_token: int, credential: str, lease_seconds: int = 120) -> dict[str, Any]:
        if lease_seconds <= 0:
            raise GatewayError("invalid_lease", "heartbeat lease must be positive")
        self._expiry_preflight(card_id)
        with self.store.transaction() as db:
            actor = self._authorize(db, credential, "heartbeat")
            card = self._card(db, card_id); claim = self._active_claim(db, card, fencing_token, actor)
            now = self._now()
            db.execute("UPDATE claims SET heartbeat_at=?,expires_at=? WHERE claim_id=?", (now, now + lease_seconds, claim["claim_id"]))
            return {"card_id": card_id, "revision": card["revision"], "fencing_token": fencing_token, "expires_at": now + lease_seconds}

    def checkpoint(self, *, card_id: str, expected_revision: int, fencing_token: int, credential: str, checkpoint: str, evidence: Sequence[str] = (), repository_id: str, branch: str, base_commit: str, head_commit: str, ancestry: Mapping[str, Any]) -> dict[str, Any]:
        if not checkpoint:
            raise GatewayError("invalid_checkpoint", "checkpoint must be non-empty")
        return self._transition_owner(scope="checkpoint", card_id=card_id, expected_revision=expected_revision, fencing_token=fencing_token, credential=credential, allowed={"in_progress"}, target="in_progress", kind="checkpoint", payload={"checkpoint": checkpoint, "evidence": list(evidence), "repository_id": repository_id, "branch": branch, "base_commit": base_commit, "head_commit": head_commit, "ancestry": dict(ancestry)})

    update = checkpoint

    def block(self, *, card_id: str, expected_revision: int, fencing_token: int, credential: str, reason: str, retain_paths: bool = True) -> dict[str, Any]:
        if not reason:
            raise GatewayError("invalid_reason", "block reason must be non-empty")
        result = self._transition_owner(scope="block", card_id=card_id, expected_revision=expected_revision, fencing_token=fencing_token, credential=credential, allowed={"claimed", "in_progress", "review", "verifying"}, target="blocked", kind="block", payload={"reason": reason, "retain_paths": retain_paths})
        return result

    def resume(self, *, card_id: str, expected_revision: int, fencing_token: int, credential: str) -> dict[str, Any]:
        return self._transition_owner(scope="resume", card_id=card_id, expected_revision=expected_revision, fencing_token=fencing_token, credential=credential, allowed={"blocked"}, target="in_progress", kind="resume")

    def submit(self, *, card_id: str, expected_revision: int, fencing_token: int, credential: str, head_commit: str, evidence: Sequence[str], repository_id: str, branch: str, base_commit: str, ancestry: Mapping[str, Any]) -> dict[str, Any]:
        if not head_commit or not evidence:
            raise GatewayError("evidence_required", "submit needs head commit and evidence")
        return self._transition_owner(scope="submit", card_id=card_id, expected_revision=expected_revision, fencing_token=fencing_token, credential=credential, allowed={"in_progress"}, target="review", kind="submit", payload={"head_commit": head_commit, "evidence": list(evidence), "repository_id": repository_id, "branch": branch, "base_commit": base_commit, "ancestry": dict(ancestry)})

    def _coordinator_transition(self, *, scope: str, card_id: str, expected_revision: int, credential: str, allowed: set[str], target: str, kind: str, payload: Mapping[str, Any] | None = None, close_claim: bool = False, release_paths: bool = False, fencing_token: int | None = None) -> dict[str, Any]:
        if fencing_token is not None:
            self._expiry_preflight(card_id)
        with self.store.transaction() as db:
            actor = self._authorize(db, credential, scope, coordinator=True)
            card = self._card(db, card_id); self._expected(card, expected_revision)
            if fencing_token is not None:
                self._coordinator_fence(db, card, fencing_token)
            if card["status"] not in allowed:
                raise GatewayError("invalid_transition", f"cannot {kind} from {card['status']}", card["revision"])
            if actor == card["owner"]:
                raise GatewayError("self_approval", "claim owner cannot perform coordinator approval")
            if kind == "accept":
                integration = db.execute("SELECT payload_json FROM receipts WHERE card_id=? AND kind='integrate' ORDER BY receipt_id DESC LIMIT 1", (card_id,)).fetchone()
                if integration is None:
                    raise GatewayError("integration_required", "acceptance requires an immutable integration receipt")
                integration_payload = json.loads(integration["payload_json"])
                if not integration_payload.get("integrated_commit") or not integration_payload.get("ancestry", {}).get("is_ancestor"):
                    raise GatewayError("integration_required", "integration receipt lacks structured ancestry evidence")
            revision = card["revision"] + 1; now = self._now(); fence = card["fencing_generation"] or None
            db.execute("UPDATE cards SET status=?,revision=?,updated_at=? WHERE card_id=?", (target, revision, now, card_id))
            if kind == "integrate" and payload is not None:
                db.execute("UPDATE cards SET integrated_by=?,integrated_commit=? WHERE card_id=?", (actor, payload["integrated_commit"], card_id))
            if close_claim:
                db.execute("UPDATE claims SET closed_at=?,close_reason=? WHERE card_id=? AND closed_at IS NULL", (now, kind, card_id))
                if target in {"ready", "done", "cancelled"}:
                    db.execute("UPDATE cards SET owner=NULL WHERE card_id=?", (card_id,))
            if release_paths:
                db.execute("DELETE FROM path_reservations WHERE card_id=?", (card_id,))
            if kind == "requeue":
                paths = self._paths(db, card_id)
                reservations = db.execute("SELECT card_id,path FROM path_reservations WHERE card_id<>?", (card_id,)).fetchall()
                for path in paths:
                    for reservation in reservations:
                        if _overlaps(path, reservation["path"]):
                            raise GatewayError("path_conflict", f"path {path} conflicts with card {reservation['card_id']}")
                db.execute("DELETE FROM path_reservations WHERE card_id=?", (card_id,))
                db.executemany("INSERT INTO path_reservations(card_id,path,fencing_token) VALUES(?,?,0)", ((card_id, path) for path in paths))
            receipt_hash = self._receipt(db, card_id=card_id, kind=kind, actor=actor, revision_from=card["revision"], revision_to=revision, fencing_token=fence, payload=payload)
            return {"card_id": card_id, "status": target, "revision": revision, "fencing_token": fence, "receipt_hash": receipt_hash}

    def integrate(self, *, card_id: str, expected_revision: int, credential: str, fencing_token: int, integrated_commit: str, review_head: str, ancestry: Mapping[str, Any], evidence: Sequence[str]) -> dict[str, Any]:
        if not integrated_commit or not evidence:
            raise GatewayError("evidence_required", "integration needs commit and evidence")
        if set(ancestry) != {"review_head", "integrated_commit", "is_ancestor", "verification"} or ancestry.get("review_head") != review_head or ancestry.get("integrated_commit") != integrated_commit or ancestry.get("is_ancestor") is not True or not isinstance(ancestry.get("verification"), str) or not ancestry["verification"]:
            raise GatewayError("invalid_ancestry", "integration requires exact structured ancestry evidence")
        with self.store.read() as db:
            submitted = db.execute("SELECT payload_json FROM receipts WHERE card_id=? AND kind='submit' ORDER BY receipt_id DESC LIMIT 1", (card_id,)).fetchone()
            if submitted is None or json.loads(submitted["payload_json"]).get("head_commit") != review_head:
                raise GatewayError("review_head_mismatch", "integration review head differs from immutable submit receipt")
        result = self._coordinator_transition(scope="integrate", card_id=card_id, expected_revision=expected_revision, credential=credential, allowed={"review"}, target="verifying", kind="integrate", payload={"integrated_commit": integrated_commit, "review_head": review_head, "ancestry": dict(ancestry), "evidence": list(evidence)}, fencing_token=fencing_token)
        return result

    def accept(self, *, card_id: str, expected_revision: int, credential: str, fencing_token: int, evidence: Sequence[str]) -> dict[str, Any]:
        if not evidence:
            raise GatewayError("evidence_required", "acceptance needs independent evidence")
        return self._coordinator_transition(scope="accept", card_id=card_id, expected_revision=expected_revision, credential=credential, allowed={"verifying"}, target="done", kind="accept", payload={"evidence": list(evidence)}, close_claim=True, release_paths=True, fencing_token=fencing_token)

    def cancel(self, *, card_id: str, expected_revision: int, credential: str, reason: str) -> dict[str, Any]:
        if not reason: raise GatewayError("invalid_reason", "cancel reason must be non-empty")
        return self._coordinator_transition(scope="cancel", card_id=card_id, expected_revision=expected_revision, credential=credential, allowed=set(ACTIVE_STATUSES) | {"ready", "backlog"}, target="cancelled", kind="cancel", payload={"reason": reason}, close_claim=True, release_paths=True)

    def requeue(self, *, card_id: str, expected_revision: int, credential: str, reason: str) -> dict[str, Any]:
        if not reason: raise GatewayError("invalid_reason", "requeue reason must be non-empty")
        return self._coordinator_transition(scope="requeue", card_id=card_id, expected_revision=expected_revision, credential=credential, allowed=set(ACTIVE_STATUSES), target="ready", kind="requeue", payload={"reason": reason}, close_claim=True, release_paths=False)

    def reassign(self, *, card_id: str, expected_revision: int, credential: str, new_owner: str, lease_seconds: int = 120, reason: str) -> dict[str, Any]:
        if not new_owner or new_owner.startswith("coordinator:") or lease_seconds <= 0:
            raise GatewayError("invalid_owner", "reassignment needs a worker owner and positive lease")
        if not reason.strip():
            raise GatewayError("invalid_reason", "reassignment reason must be non-empty")
        with self.store.transaction() as db:
            actor = self._authorize(db, credential, "reassign", coordinator=True)
            card = self._card(db, card_id); self._expected(card, expected_revision)
            if card["status"] not in ACTIVE_STATUSES:
                raise GatewayError("invalid_transition", "only an active card can be reassigned", card["revision"])
            if db.execute("SELECT 1 FROM claims WHERE card_id=? AND closed_at IS NULL", (card_id,)).fetchone() is None:
                raise GatewayError("claim_not_active", "reassignment requires an active claim", card["revision"])
            if db.execute("SELECT 1 FROM claims WHERE owner=? AND closed_at IS NULL", (new_owner,)).fetchone():
                raise GatewayError("wip_limit", "new owner already has an active claim")
            now = self._now(); fence = card["fencing_generation"] + 1; claim_id = str(uuid4()); revision = card["revision"] + 1
            db.execute("UPDATE claims SET closed_at=?,close_reason='reassign' WHERE card_id=? AND closed_at IS NULL", (now, card_id))
            db.execute("UPDATE cards SET status='claimed',owner=?,revision=?,fencing_generation=?,attention_required=0,updated_at=? WHERE card_id=?", (new_owner, revision, fence, now, card_id))
            db.execute("INSERT INTO claims VALUES(?,?,?,?,?,?,?,?,NULL,NULL)", (claim_id, card_id, new_owner, fence, card["definition_hash"], now, now, now + lease_seconds))
            if new_owner.split(":", 1)[0] not in json.loads(card["definition_json"])["allowedOwnerNamespaces"]:
                raise GatewayError("owner_namespace_denied", "new owner namespace is not allowed")
            paths = self._paths(db, card_id)
            reservations = db.execute("SELECT card_id,path FROM path_reservations WHERE card_id<>?", (card_id,)).fetchall()
            for path in paths:
                for reservation in reservations:
                    if _overlaps(path, reservation["path"]):
                        raise GatewayError("path_conflict", f"path {path} conflicts with card {reservation['card_id']}")
            db.execute("DELETE FROM path_reservations WHERE card_id=?", (card_id,))
            db.executemany("INSERT INTO path_reservations(card_id,path,fencing_token) VALUES(?,?,?)", ((card_id, path, fence) for path in paths))
            refreshed_card = self._card(db, card_id)
            refreshed_claim = db.execute("SELECT * FROM claims WHERE claim_id=?", (claim_id,)).fetchone()
            self._store_claim_bundle(db, refreshed_card, refreshed_claim)
            receipt_hash = self._receipt(db, card_id=card_id, kind="reassign", actor=actor, revision_from=card["revision"], revision_to=revision, fencing_token=fence, payload={"new_owner": new_owner, "reason": reason, "claim_id": claim_id})
            return {"card_id": card_id, "status": "claimed", "owner": new_owner, "claim_id": claim_id, "fencing_token": fence, "revision": revision, "expires_at": now + lease_seconds, "receipt_hash": receipt_hash}

    def revalidate(self, *, card_id: str, expected_revision: int, credential: str, definition: Mapping[str, Any]) -> dict[str, Any]:
        new_hash = self._validate_definition_contract(definition)
        with self.store.transaction() as db:
            actor = self._authorize(db, credential, "revalidate", coordinator=True)
            card = self._card(db, card_id); self._expected(card, expected_revision)
            revision = card["revision"] + 1
            claim = db.execute("SELECT * FROM claims WHERE card_id=? AND closed_at IS NULL", (card_id,)).fetchone()
            fence = card["fencing_generation"]
            if claim is not None:
                if claim["owner"].split(":", 1)[0] not in definition["allowedOwnerNamespaces"]:
                    raise GatewayError("owner_namespace_denied", "active owner is not allowed by revalidated definition")
                fence += 1
            db.execute("UPDATE cards SET definition_json=?,definition_hash=?,source_revision=?,revalidation_required=0,revision=?,fencing_generation=?,updated_at=? WHERE card_id=?", (_canonical(definition), new_hash, definition["sourceRevision"], revision, fence, self._now(), card_id))
            task_bundle = None
            if claim is not None:
                db.execute("UPDATE claims SET definition_hash=?,fencing_token=? WHERE claim_id=?", (new_hash, fence, claim["claim_id"]))
                db.execute("UPDATE path_reservations SET fencing_token=? WHERE card_id=?", (fence, card_id))
                db.execute("UPDATE worktree_bindings SET fencing_token=? WHERE claim_id=?", (fence, claim["claim_id"]))
                refreshed_card = self._card(db, card_id)
                refreshed_claim = db.execute("SELECT * FROM claims WHERE claim_id=?", (claim["claim_id"],)).fetchone()
                task_bundle = self._store_claim_bundle(db, refreshed_card, refreshed_claim)
            receipt_hash = self._receipt(db, card_id=card_id, kind="revalidate", actor=actor, revision_from=card["revision"], revision_to=revision, fencing_token=fence or None, payload={"definition_hash": new_hash, "fencing_token": fence})
            result = {"card_id": card_id, "status": card["status"], "revision": revision, "fencing_token": fence, "definition_hash": new_hash, "receipt_hash": receipt_hash}
            if task_bundle is not None:
                task_bundle["receipt_hash"] = receipt_hash
                result["task_bundle"] = task_bundle
            return result

    def backend_epoch(self, *, credential: str | None = None, expected_epoch: int | None = None, backend: str | None = None, reconciled_snapshot_hash: str | None = None) -> dict[str, Any]:
        if backend is None:
            with self.store.read() as db:
                row = db.execute("SELECT epoch,backend FROM backend_state WHERE singleton=1").fetchone()
                return {"epoch": row["epoch"], "backend": row["backend"]}
        if credential is None: raise GatewayError("credential_required", "backend switch requires a credential")
        if backend not in BACKEND_NAMES:
            raise GatewayError("invalid_backend", "backend must be remote or filesystem")
        if not isinstance(reconciled_snapshot_hash, str) or len(reconciled_snapshot_hash) != 71 or not reconciled_snapshot_hash.startswith("sha256:") or any(character not in "0123456789abcdef" for character in reconciled_snapshot_hash[7:]):
            raise GatewayError("snapshot_required", "backend switch requires a reconciled sha256 snapshot hash")
        with self.store.transaction() as db:
            actor = self._authorize(db, credential, "backend_epoch", coordinator=True)
            row = db.execute("SELECT epoch,backend FROM backend_state WHERE singleton=1").fetchone()
            if expected_epoch != row["epoch"]:
                raise GatewayError("stale_epoch", f"current backend epoch is {row['epoch']}")
            if db.execute("SELECT 1 FROM claims WHERE closed_at IS NULL LIMIT 1").fetchone():
                raise GatewayError("active_claims", "backend cannot switch with active claims")
            if backend == row["backend"]:
                raise GatewayError("backend_unchanged", "backend epoch switch must change backend")
            now = self._now()
            previous = db.execute("SELECT receipt_hash FROM backend_receipts ORDER BY receipt_id DESC LIMIT 1").fetchone()
            material = {"epoch_from": row["epoch"], "epoch_to": row["epoch"] + 1, "backend_from": row["backend"], "backend_to": backend, "snapshot_hash": reconciled_snapshot_hash, "actor": actor, "at": now, "previous_hash": previous[0] if previous else None}
            receipt_hash = _sha256(_canonical(material))
            db.execute("UPDATE backend_state SET epoch=epoch+1,backend=?,snapshot_hash=? WHERE singleton=1", (backend, reconciled_snapshot_hash))
            db.execute("INSERT INTO backend_receipts(epoch_from,epoch_to,backend_from,backend_to,snapshot_hash,actor,at,previous_hash,receipt_hash) VALUES(?,?,?,?,?,?,?,?,?)", (row["epoch"], row["epoch"] + 1, row["backend"], backend, reconciled_snapshot_hash, actor, now, material["previous_hash"], receipt_hash))
            activity = {"action": "backend_epoch", "epoch": row["epoch"] + 1, "backend": backend, "snapshot_hash": reconciled_snapshot_hash, "receipt_hash": receipt_hash}
            db.execute("INSERT INTO projection_outbox(dedupe_key,kind,payload_json,created_at_ms) VALUES(?,?,?,?)", ("backend:" + receipt_hash, "activity", _canonical(activity), now * 1000))
            return {"epoch": row["epoch"] + 1, "backend": backend, "snapshot_hash": reconciled_snapshot_hash, "receipt_hash": receipt_hash}

    def receipts(self, *, card_id: str) -> list[dict[str, Any]]:
        with self.store.read() as db:
            rows = db.execute("SELECT * FROM receipts WHERE card_id=? ORDER BY receipt_id", (card_id,)).fetchall()
            return [{"kind": row["kind"], "actor": row["actor"], "revision_from": row["revision_from"], "revision_to": row["revision_to"], "fencing_token": row["fencing_token"], "at": row["at"], "payload": json.loads(row["payload_json"]), "previous_hash": row["previous_hash"], "receipt_hash": row["receipt_hash"]} for row in rows]

    # Linear projection persistence. These methods never mutate ownership.
    def record_webhook_delivery(self, *, delivery_id: str, event_timestamp_ms: int, received_at_ms: int, payload_json: str) -> InboxWriteResult:
        try:
            payload = json.loads(payload_json)
        except json.JSONDecodeError:
            payload = {}
        data = payload.get("data", {}) if isinstance(payload, dict) else {}
        updated = payload.get("updatedFrom", {}) if isinstance(payload, dict) else {}
        protected_fields = {"title", "description", "priority", "labels", "teamId", "definitionHash"}
        protected_change = (
            payload.get("type") == "Issue"
            and payload.get("action") == "update"
            and isinstance(updated, dict)
            and bool(protected_fields.intersection(updated))
        )
        card_id = data.get("identifier") or data.get("id") if isinstance(data, dict) else None
        return self.record_linear_webhook(
            delivery_id=delivery_id, event_timestamp_ms=event_timestamp_ms,
            received_at_ms=received_at_ms, payload_json=payload_json,
            card_id=card_id, protected_change=protected_change,
        )

    def record_linear_webhook(self, *, delivery_id: str, event_timestamp_ms: int, received_at_ms: int, payload_json: str, card_id: str | None, protected_change: bool) -> InboxWriteResult:
        with self.store.transaction() as db:
            latest = db.execute("SELECT MAX(event_timestamp_ms) FROM webhook_inbox").fetchone()[0]
            try:
                db.execute("INSERT INTO webhook_inbox VALUES(?,?,?,?)", (delivery_id, event_timestamp_ms, received_at_ms, payload_json))
            except sqlite3.IntegrityError:
                return InboxWriteResult(False, False)
            if protected_change and card_id:
                card = db.execute("SELECT * FROM cards WHERE card_id=?", (card_id,)).fetchone()
                claim = db.execute("SELECT 1 FROM claims WHERE card_id=? AND closed_at IS NULL", (card_id,)).fetchone()
                if card is not None and claim is not None and not card["revalidation_required"]:
                    revision = card["revision"] + 1
                    db.execute("UPDATE cards SET revalidation_required=1,attention_required=1,revision=?,updated_at=? WHERE card_id=?", (revision, self._now(), card_id))
                    self._receipt(db, card_id=card_id, kind="definition_changed", actor="linear:webhook:projection", revision_from=card["revision"], revision_to=revision, fencing_token=card["fencing_generation"], payload={"delivery_id": delivery_id})
            return InboxWriteResult(True, latest is not None and event_timestamp_ms < latest)

    def enqueue_projection_outbox(self, *, dedupe_key: str, kind: str, payload_json: str, created_at_ms: int) -> bool:
        with self.store.transaction() as db:
            cursor = db.execute("INSERT OR IGNORE INTO projection_outbox(dedupe_key,kind,payload_json,created_at_ms) VALUES(?,?,?,?)", (dedupe_key, kind, payload_json, created_at_ms))
            return cursor.rowcount == 1

    def claim_projection_outbox(self, *, now_ms: int, limit: int, lease_ms: int = 60_000) -> list[OutboxRecord]:
        if limit <= 0: return []
        if lease_ms <= 0:
            raise GatewayError("invalid_lease", "projection lease must be positive")
        with self.store.transaction() as db:
            db.execute("UPDATE projection_outbox SET status='pending' WHERE status='dispatching' AND attempted_at_ms<=?", (now_ms - lease_ms,))
            rows = db.execute("SELECT * FROM projection_outbox WHERE status='pending' ORDER BY outbox_id LIMIT ?", (limit,)).fetchall()
            ids = [row["outbox_id"] for row in rows]
            if ids:
                db.executemany("UPDATE projection_outbox SET status='dispatching',attempts=attempts+1,attempted_at_ms=? WHERE outbox_id=?", ((now_ms, item_id) for item_id in ids))
            return [OutboxRecord(row["outbox_id"], row["dedupe_key"], row["kind"], row["payload_json"], row["attempts"] + 1) for row in rows]

    def mark_projection_sent(self, *, item_id: int, expected_attempt: int, sent_at_ms: int) -> bool:
        with self.store.transaction() as db:
            cursor = db.execute(
                "UPDATE projection_outbox SET status='sent',sent_at_ms=?,needs_attention=0 "
                "WHERE outbox_id=? AND status='dispatching' AND attempts=?",
                (sent_at_ms, item_id, expected_attempt),
            )
            return cursor.rowcount == 1

    def mark_projection_pending(self, *, item_id: int, expected_attempt: int, error_code: str, error_message: str, attempted_at_ms: int, needs_attention: bool) -> bool:
        with self.store.transaction() as db:
            cursor = db.execute(
                "UPDATE projection_outbox SET status='pending',error_code=?,error_message=?,attempted_at_ms=?,needs_attention=? "
                "WHERE outbox_id=? AND status='dispatching' AND attempts=?",
                (error_code, error_message, attempted_at_ms, int(needs_attention), item_id, expected_attempt),
            )
            return cursor.rowcount == 1

    def record_projection_attention(self, *, kind: str, subject_id: str, details_json: str, created_at_ms: int) -> None:
        with self.store.transaction() as db:
            db.execute("INSERT INTO projection_attention(kind,subject_id,details_json,created_at_ms) VALUES(?,?,?,?)", (kind, subject_id, details_json, created_at_ms))

    def desired_projection(self, *, card_id: str) -> dict[str, Any] | None:
        with self.store.read() as db:
            row = db.execute("SELECT * FROM cards WHERE card_id=?", (card_id,)).fetchone()
            return None if row is None else self._card_dict(db, row)
