"""Linear projection boundary for the remote development workplan.

Linear is deliberately a projection: this module has no operation capable of
claiming a card, changing canonical ownership, or issuing fencing tokens.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import hmac
import json
import re
import time
from typing import Any, Callable, Mapping, Protocol, Sequence
from urllib import error as urlerror
from urllib import request as urlrequest
from uuid import UUID


DEFAULT_WEBHOOK_TOLERANCE_MS = 60_000
DEFAULT_OUTBOX_LEASE_MS = 60_000
LINEAR_GRAPHQL_URL = "https://api.linear.app/graphql"
ATTENTION_ERROR_CODES = frozenset(
    {
        "oauth_revoked",
        "oauth_unauthorized",
        "team_forbidden",
        "team_not_found",
    }
)
PUBLIC_ACTIVITY_FIELDS = frozenset(
    {
        "subject_id",
        "action",
        "canonical_revision",
        "occurred_at_ms",
        "actor",
        "receipt_hash",
    }
)
PROTECTED_ISSUE_FIELDS = frozenset(
    {
        "title",
        "description",
        "priority",
        "labels",
        "labelIds",
        "addedLabelIds",
        "removedLabelIds",
        "teamId",
        "definitionHash",
    }
)
PUBLIC_ACTIVITY_ACTIONS = frozenset(
    {
        "qualify",
        "claim",
        "lease_expired",
        "definition_changed",
        "bind_worktree",
        "checkpoint",
        "block",
        "resume",
        "submit",
        "integrate",
        "accept",
        "revalidate",
        "requeue",
        "reassign",
        "cancel",
    }
)
_PUBLIC_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,254}$")
_SECRET_VALUE = re.compile(
    r"(?i)(?:authorization\s*:|bearer\s+[a-z0-9._~+/=-]+|"
    r"(?:secret|password|access[_ -]?token|api[_ -]?key)\s*[:=]\s*\S+|"
    r"(?:ghp_|github_pat_|xox[baprs]-|sk-)[a-z0-9_-]{8,})"
)


class WebhookVerificationError(ValueError):
    """A webhook cannot be authenticated or is outside the replay window."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class LinearTransportError(RuntimeError):
    """A classified failure returned by the Linear transport boundary."""

    def __init__(self, code: str, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class VerifiedWebhook:
    delivery_id: str
    timestamp_ms: int
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class InboxWrite:
    inserted: bool
    reordered: bool = False


@dataclass(frozen=True)
class InboxReceipt:
    delivery_id: str
    duplicate: bool
    reordered: bool


@dataclass(frozen=True)
class ProjectionActivity:
    """Allowlisted, secret-free activity persisted to the committed outbox."""

    subject_id: str
    action: str
    canonical_revision: int
    occurred_at_ms: int
    actor: str | None = None
    receipt_hash: str | None = None

    def __post_init__(self) -> None:
        _validate_public_activity(self.payload())

    def payload(self) -> dict[str, Any]:
        # Fixed fields are intentional. Arbitrary metadata can accidentally copy
        # credentials or internal request headers into a visible Linear activity.
        return {key: value for key, value in asdict(self).items() if value is not None}

    @property
    def dedupe_key(self) -> str:
        material = json.dumps(self.payload(), sort_keys=True, separators=(",", ":"))
        return "activity:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class OutboxItem:
    id: int
    dedupe_key: str
    kind: str
    payload_json: str
    attempts: int = 0


@dataclass(frozen=True)
class DispatchReport:
    sent: int = 0
    pending: int = 0
    attention: int = 0


@dataclass(frozen=True)
class DesiredProjection:
    subject_id: str
    title: str
    state: str
    team_id: str
    canonical_revision: int

    def payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReconciliationReport:
    subject_id: str
    drift_fields: tuple[str, ...] = field(default_factory=tuple)
    repair_enqueued: bool = False
    attention_recorded: bool = False


class LinearProjectionStore(Protocol):
    """Persistence contract implemented by the canonical gateway store.

    Canonical transition activities are inserted by the gateway core inside
    the same database transaction as state and receipt. The enqueue operation
    here is used only for projection repair messages.
    """

    def record_linear_webhook(
        self,
        *,
        delivery_id: str,
        event_timestamp_ms: int,
        received_at_ms: int,
        payload_json: str,
        card_id: str | None,
        protected_change: bool,
    ) -> InboxWrite: ...

    def enqueue_projection_outbox(
        self,
        *,
        dedupe_key: str,
        kind: str,
        payload_json: str,
        created_at_ms: int,
    ) -> bool: ...

    def claim_projection_outbox(
        self, *, now_ms: int, limit: int, lease_ms: int
    ) -> Sequence[OutboxItem]: ...

    def mark_projection_sent(
        self, *, item_id: int, expected_attempt: int, sent_at_ms: int
    ) -> bool: ...

    def mark_projection_pending(
        self,
        *,
        item_id: int,
        expected_attempt: int,
        error_code: str,
        error_message: str,
        attempted_at_ms: int,
        needs_attention: bool,
    ) -> bool: ...

    def record_projection_attention(
        self,
        *,
        kind: str,
        subject_id: str,
        details_json: str,
        created_at_ms: int,
    ) -> None: ...


class LinearClient(Protocol):
    def project_card(
        self, payload: Mapping[str, Any], *, idempotency_key: str
    ) -> None: ...

    def project_activity(
        self, payload: Mapping[str, Any], *, idempotency_key: str
    ) -> None: ...

    def fetch_card(self, subject_id: str) -> Mapping[str, Any] | None: ...


@dataclass(frozen=True)
class HTTPResult:
    status: int
    body: bytes


class HTTPTransport(Protocol):
    def post(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        body: bytes,
        timeout_seconds: float,
    ) -> HTTPResult: ...


class UrllibHTTPTransport:
    """Small stdlib transport which never logs request headers or bodies."""

    def post(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        body: bytes,
        timeout_seconds: float,
    ) -> HTTPResult:
        request = urlrequest.Request(url, data=body, headers=dict(headers), method="POST")
        try:
            with urlrequest.urlopen(request, timeout=timeout_seconds) as response:
                return HTTPResult(response.status, response.read())
        except urlerror.HTTPError as exc:
            return HTTPResult(exc.code, exc.read())
        except urlerror.URLError as exc:
            raise LinearTransportError("network_error", "Linear request failed") from exc


class LinearGraphQLClient:
    """Operational OAuth client for the narrow Linear projection contract."""

    def __init__(
        self,
        *,
        access_token: str,
        transport: HTTPTransport | None = None,
        endpoint: str = LINEAR_GRAPHQL_URL,
        timeout_seconds: float = 4.0,
        max_comment_pages: int = 100,
    ) -> None:
        if not access_token:
            raise ValueError("Linear OAuth access token is required")
        self._access_token = access_token
        self._transport = transport or UrllibHTTPTransport()
        self._endpoint = endpoint
        self._timeout_seconds = timeout_seconds
        self._max_comment_pages = max_comment_pages

    def _execute(
        self,
        query: str,
        variables: Mapping[str, Any],
        *,
        idempotency_key: str | None = None,
    ) -> Mapping[str, Any]:
        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
        }
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key
        result = self._transport.post(
            url=self._endpoint,
            headers=headers,
            body=json.dumps(
                {"query": query, "variables": variables},
                separators=(",", ":"),
            ).encode("utf-8"),
            timeout_seconds=self._timeout_seconds,
        )
        if result.status == 401:
            raise LinearTransportError("oauth_unauthorized", "Linear OAuth rejected", retryable=False)
        if result.status == 403:
            raise LinearTransportError("team_forbidden", "Linear team access denied", retryable=False)
        if result.status == 429 or result.status >= 500:
            raise LinearTransportError("linear_unavailable", "Linear is temporarily unavailable")
        try:
            document = json.loads(result.body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LinearTransportError("invalid_response", "Linear returned invalid JSON") from exc
        errors = document.get("errors") if isinstance(document, dict) else None
        if errors:
            codes = {
                str(error.get("extensions", {}).get("code", "")).upper()
                for error in errors
                if isinstance(error, dict)
            }
            if codes & {"AUTHENTICATION_ERROR", "UNAUTHENTICATED"}:
                raise LinearTransportError("oauth_unauthorized", "Linear OAuth rejected", retryable=False)
            if codes & {"FORBIDDEN", "PERMISSION_DENIED"}:
                raise LinearTransportError("team_forbidden", "Linear team access denied", retryable=False)
            if "RATELIMITED" in codes:
                raise LinearTransportError("linear_unavailable", "Linear rate limit reached")
            raise LinearTransportError("graphql_error", "Linear GraphQL operation failed", retryable=False)
        if result.status < 200 or result.status >= 300:
            raise LinearTransportError("linear_request_failed", "Linear request failed", retryable=False)
        data = document.get("data") if isinstance(document, dict) else None
        if not isinstance(data, dict):
            raise LinearTransportError("invalid_response", "Linear response omitted data")
        return data

    @staticmethod
    def _revision_from_comments(comments: Mapping[str, Any] | None) -> int | None:
        nodes = comments.get("nodes", []) if isinstance(comments, dict) else []
        prefix = "<!-- meta-harness-revision:"
        revisions = []
        for node in nodes:
            body = node.get("body") if isinstance(node, dict) else None
            if isinstance(body, str) and prefix in body:
                value = body.split(prefix, 1)[1].split(" -->", 1)[0]
                if value.isdigit():
                    revisions.append(int(value))
        return max(revisions, default=None)

    def _comment_page(
        self, issue_id: str, *, before: str | None
    ) -> Mapping[str, Any]:
        data = self._execute(
            """query ProjectionComments($id:String!,$before:String){issue(id:$id){"""
            """comments(last:50,before:$before){nodes{body} """
            """pageInfo{hasPreviousPage startCursor}}}}""",
            {"id": issue_id, "before": before},
        )
        issue = data.get("issue")
        if not isinstance(issue, dict) or not isinstance(issue.get("comments"), dict):
            raise LinearTransportError("invalid_response", "Linear comments payload is invalid")
        return issue["comments"]

    def _has_comment_marker(self, issue_id: str, marker: str) -> bool:
        before = None
        seen_cursors: set[str] = set()
        for _page_number in range(self._max_comment_pages):
            comments = self._comment_page(issue_id, before=before)
            nodes = comments.get("nodes", [])
            if any(marker in str(node.get("body", "")) for node in nodes):
                return True
            page_info = comments.get("pageInfo") or {}
            if not page_info.get("hasPreviousPage"):
                return False
            cursor = page_info.get("startCursor")
            if not isinstance(cursor, str) or not cursor or cursor in seen_cursors:
                raise LinearTransportError(
                    "invalid_response", "Linear comment pagination did not advance"
                )
            seen_cursors.add(cursor)
            before = cursor
        raise LinearTransportError(
            "comment_scan_limit",
            "Linear comment history exceeds safe idempotency scan bound",
            retryable=False,
        )

    def fetch_card(self, subject_id: str) -> Mapping[str, Any] | None:
        data = self._execute(
            """query ProjectedIssue($id:String!){issue(id:$id){id title updatedAt """
            """state{id name} team{id}}}""",
            {"id": subject_id},
        )
        issue = data.get("issue")
        if issue is None:
            return None
        if not isinstance(issue, dict):
            raise LinearTransportError("invalid_response", "Linear issue payload is invalid")
        comments = self._comment_page(subject_id, before=None)
        return {
            "subject_id": subject_id,
            "title": issue.get("title"),
            "state": (issue.get("state") or {}).get("id"),
            "team_id": (issue.get("team") or {}).get("id"),
            "canonical_revision": self._revision_from_comments(comments),
            "updatedAt": issue.get("updatedAt"),
        }

    def project_card(
        self, payload: Mapping[str, Any], *, idempotency_key: str
    ) -> None:
        issue_id = str(payload["subject_id"])
        update = {
            "title": payload["title"],
            "stateId": payload["state"],
            "teamId": payload["team_id"],
        }
        data = self._execute(
            """mutation ProjectIssue($id:String!,$input:IssueUpdateInput!){"""
            """issueUpdate(id:$id,input:$input){success}}""",
            {"id": issue_id, "input": update},
            idempotency_key=idempotency_key,
        )
        if not (data.get("issueUpdate") or {}).get("success"):
            raise LinearTransportError("projection_rejected", "Linear issue update was rejected")
        self.project_activity(
            {
                "subject_id": issue_id,
                "action": "revalidate",
                "canonical_revision": payload["canonical_revision"],
                "occurred_at_ms": int(time.time_ns() // 1_000_000),
            },
            idempotency_key=idempotency_key + ":revision",
        )

    def project_activity(
        self, payload: Mapping[str, Any], *, idempotency_key: str
    ) -> None:
        issue_id = str(payload["subject_id"])
        marker = f"<!-- meta-harness-idempotency:{idempotency_key} -->"
        if self._has_comment_marker(issue_id, marker):
            return
        body = (
            f"Meta-Harness `{payload['action']}` at canonical revision "
            f"{payload['canonical_revision']}.\n\n"
            f"<!-- meta-harness-revision:{payload['canonical_revision']} -->\n{marker}"
        )
        data = self._execute(
            """mutation ProjectActivity($input:CommentCreateInput!){"""
            """commentCreate(input:$input){success}}""",
            {"input": {"issueId": issue_id, "body": body}},
            idempotency_key=idempotency_key,
        )
        if not (data.get("commentCreate") or {}).get("success"):
            raise LinearTransportError("projection_rejected", "Linear comment was rejected")


def _header(headers: Mapping[str, str], name: str) -> str | None:
    wanted = name.casefold()
    return next((value for key, value in headers.items() if key.casefold() == wanted), None)


def _protected_issue_change(payload: Mapping[str, Any]) -> tuple[str | None, bool]:
    if payload.get("type") != "Issue" or payload.get("action") != "update":
        return None, False
    data = payload.get("data")
    updated_from = payload.get("updatedFrom")
    if not isinstance(data, dict) or not isinstance(updated_from, dict):
        return None, False
    card_id = data.get("identifier") or data.get("id")
    return (
        str(card_id) if card_id is not None else None,
        bool(PROTECTED_ISSUE_FIELDS.intersection(updated_from)),
    )


def _validate_public_activity(payload: Mapping[str, Any]) -> dict[str, Any]:
    unknown = set(payload) - PUBLIC_ACTIVITY_FIELDS
    if unknown:
        raise LinearTransportError(
            "unsafe_projection_payload",
            f"activity contains non-public fields: {', '.join(sorted(unknown))}",
            retryable=False,
        )
    required = {"subject_id", "action", "canonical_revision", "occurred_at_ms"}
    if not required <= set(payload):
        raise LinearTransportError(
            "unsafe_projection_payload",
            "activity is missing required public fields",
            retryable=False,
        )
    if not isinstance(payload["subject_id"], str) or not _PUBLIC_ID.fullmatch(
        payload["subject_id"]
    ):
        raise LinearTransportError(
            "unsafe_projection_payload", "activity subject is invalid", retryable=False
        )
    if payload["action"] not in PUBLIC_ACTIVITY_ACTIONS:
        raise LinearTransportError(
            "unsafe_projection_payload", "activity action is invalid", retryable=False
        )
    if not isinstance(payload["canonical_revision"], int) or payload["canonical_revision"] < 0:
        raise LinearTransportError(
            "unsafe_projection_payload", "activity revision is invalid", retryable=False
        )
    if not isinstance(payload["occurred_at_ms"], int) or payload["occurred_at_ms"] < 0:
        raise LinearTransportError(
            "unsafe_projection_payload", "activity timestamp is invalid", retryable=False
        )
    actor = payload.get("actor")
    if actor is not None and (not isinstance(actor, str) or not _PUBLIC_ID.fullmatch(actor)):
        raise LinearTransportError(
            "unsafe_projection_payload", "activity actor is invalid", retryable=False
        )
    receipt_hash = payload.get("receipt_hash")
    if receipt_hash is not None and (
        not isinstance(receipt_hash, str)
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", receipt_hash)
    ):
        raise LinearTransportError(
            "unsafe_projection_payload", "activity receipt hash is invalid", retryable=False
        )
    for key, value in payload.items():
        if isinstance(value, str) and _SECRET_VALUE.search(value):
            raise LinearTransportError(
                "unsafe_projection_payload",
                f"activity field {key} resembles credential material",
                retryable=False,
            )
    return dict(payload)


def verify_webhook(
    *,
    raw_body: bytes,
    headers: Mapping[str, str],
    secret: bytes | str,
    now_ms: int,
    tolerance_ms: int = DEFAULT_WEBHOOK_TOLERANCE_MS,
) -> VerifiedWebhook:
    """Verify Linear's raw-body HMAC, delivery UUID, and bounded timestamp."""

    delivery_id = _header(headers, "Linear-Delivery")
    signature = _header(headers, "Linear-Signature")
    timestamp_text = _header(headers, "Linear-Timestamp")
    if not delivery_id or not signature or not timestamp_text:
        raise WebhookVerificationError("missing_headers", "required Linear headers missing")
    try:
        UUID(delivery_id)
    except ValueError as exc:
        raise WebhookVerificationError("invalid_delivery", "Linear-Delivery is not a UUID") from exc
    try:
        timestamp_ms = int(timestamp_text)
    except ValueError as exc:
        raise WebhookVerificationError("invalid_timestamp", "Linear-Timestamp is not milliseconds") from exc
    if tolerance_ms < 0 or abs(now_ms - timestamp_ms) > tolerance_ms:
        raise WebhookVerificationError("stale_timestamp", "webhook is outside the replay window")

    secret_bytes = secret.encode("utf-8") if isinstance(secret, str) else secret
    expected = hmac.new(secret_bytes, raw_body, hashlib.sha256).hexdigest()
    # compare_digest is timing-safe for equal-typed inputs and also rejects a
    # malformed/non-hex signature without parsing it into an integer.
    if len(signature) != len(expected) or not hmac.compare_digest(signature, expected):
        raise WebhookVerificationError("invalid_signature", "webhook signature mismatch")
    try:
        payload = json.loads(raw_body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WebhookVerificationError("invalid_json", "webhook body is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise WebhookVerificationError("invalid_payload", "webhook body must be an object")
    body_timestamp = payload.get("webhookTimestamp")
    try:
        body_timestamp_ms = int(body_timestamp)
    except (TypeError, ValueError) as exc:
        raise WebhookVerificationError(
            "invalid_timestamp", "signed webhookTimestamp is required in milliseconds"
        ) from exc
    if abs(now_ms - body_timestamp_ms) > tolerance_ms:
        raise WebhookVerificationError(
            "stale_timestamp", "payload webhookTimestamp is outside the replay window"
        )
    # Ordering must use the timestamp covered by the raw-body signature. The
    # header remains an independent freshness bound but is not trusted as data.
    return VerifiedWebhook(delivery_id, body_timestamp_ms, payload)


class LinearProjectionAdapter:
    def __init__(
        self,
        *,
        store: LinearProjectionStore,
        client: LinearClient,
        webhook_secret: bytes | str,
        clock_ms: Callable[[], int] | None = None,
        tolerance_ms: int = DEFAULT_WEBHOOK_TOLERANCE_MS,
        outbox_lease_ms: int = DEFAULT_OUTBOX_LEASE_MS,
    ) -> None:
        self.store = store
        self.client = client
        self.webhook_secret = webhook_secret
        self.clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)
        self.tolerance_ms = tolerance_ms
        self.outbox_lease_ms = outbox_lease_ms

    def receive_webhook(self, *, raw_body: bytes, headers: Mapping[str, str]) -> InboxReceipt:
        now_ms = self.clock_ms()
        verified = verify_webhook(
            raw_body=raw_body,
            headers=headers,
            secret=self.webhook_secret,
            now_ms=now_ms,
            tolerance_ms=self.tolerance_ms,
        )
        card_id, protected_change = _protected_issue_change(verified.payload)
        result = self.store.record_linear_webhook(
            delivery_id=verified.delivery_id,
            event_timestamp_ms=verified.timestamp_ms,
            received_at_ms=now_ms,
            payload_json=json.dumps(verified.payload, sort_keys=True, separators=(",", ":")),
            card_id=card_id,
            protected_change=protected_change,
        )
        return InboxReceipt(
            delivery_id=verified.delivery_id,
            duplicate=not result.inserted,
            reordered=result.reordered,
        )

    def dispatch(self, *, limit: int = 100) -> DispatchReport:
        now_ms = self.clock_ms()
        sent = pending = attention = 0
        for item in self.store.claim_projection_outbox(
            now_ms=now_ms, limit=limit, lease_ms=self.outbox_lease_ms
        ):
            try:
                payload = json.loads(item.payload_json)
                if item.kind == "activity":
                    self.client.project_activity(
                        _validate_public_activity(payload), idempotency_key=item.dedupe_key
                    )
                elif item.kind == "card_repair":
                    self.client.project_card(payload, idempotency_key=item.dedupe_key)
                else:
                    raise LinearTransportError("unsupported_projection", item.kind, retryable=False)
            except LinearTransportError as exc:
                needs_attention = exc.code in ATTENTION_ERROR_CODES or not exc.retryable
                self.store.mark_projection_pending(
                    item_id=item.id,
                    expected_attempt=item.attempts,
                    error_code=exc.code,
                    error_message="Linear projection failed",
                    attempted_at_ms=now_ms,
                    needs_attention=needs_attention,
                )
                pending += 1
                attention += int(needs_attention)
            except Exception as exc:  # transport implementations need not share exception types
                self.store.mark_projection_pending(
                    item_id=item.id,
                    expected_attempt=item.attempts,
                    error_code="transport_error",
                    error_message="Linear projection transport failed",
                    attempted_at_ms=now_ms,
                    needs_attention=False,
                )
                pending += 1
            else:
                self.store.mark_projection_sent(
                    item_id=item.id, expected_attempt=item.attempts, sent_at_ms=now_ms
                )
                sent += 1
        return DispatchReport(sent=sent, pending=pending, attention=attention)

    def record_oauth_revoked(self, *, app_id: str, delivery_id: str) -> None:
        """Surface revoked credentials without touching canonical ownership."""

        self.store.record_projection_attention(
            kind="oauth_revoked",
            subject_id=app_id,
            details_json=json.dumps({"delivery_id": delivery_id}, sort_keys=True),
            created_at_ms=self.clock_ms(),
        )

    def reconcile(self, desired: DesiredProjection) -> ReconciliationReport:
        try:
            observed = self.client.fetch_card(desired.subject_id)
        except LinearTransportError as exc:
            now_ms = self.clock_ms()
            self.store.record_projection_attention(
                kind=exc.code,
                subject_id=desired.subject_id,
                details_json=json.dumps(
                    {"error_code": exc.code, "retryable": exc.retryable},
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                created_at_ms=now_ms,
            )
            return ReconciliationReport(
                subject_id=desired.subject_id,
                attention_recorded=True,
            )
        desired_payload = desired.payload()
        if observed is None:
            drift = ("missing",)
        else:
            drift = tuple(
                field_name
                for field_name in ("title", "state", "team_id", "canonical_revision")
                if observed.get(field_name) != desired_payload[field_name]
            )
        if not drift:
            return ReconciliationReport(subject_id=desired.subject_id)

        now_ms = self.clock_ms()
        details = {
            "drift_fields": list(drift),
            "observed_revision": observed.get("canonical_revision") if observed else None,
            "desired_revision": desired.canonical_revision,
        }
        self.store.record_projection_attention(
            kind="manual_state_drift",
            subject_id=desired.subject_id,
            details_json=json.dumps(details, sort_keys=True, separators=(",", ":")),
            created_at_ms=now_ms,
        )
        observed_version = None if observed is None else (
            observed.get("updated_at") or observed.get("updatedAt")
        )
        if observed_version is None:
            observed_version = hashlib.sha256(
                json.dumps(observed, sort_keys=True, default=str).encode("utf-8")
                if observed is not None
                else b"missing"
            ).hexdigest()[:16]
        else:
            observed_version = hashlib.sha256(
                str(observed_version).encode("utf-8")
            ).hexdigest()[:16]
        repair_key = (
            f"repair:{desired.subject_id}:{desired.canonical_revision}:{observed_version}"
        )
        repair_enqueued = self.store.enqueue_projection_outbox(
            dedupe_key=repair_key,
            kind="card_repair",
            payload_json=json.dumps(desired_payload, sort_keys=True, separators=(",", ":")),
            created_at_ms=now_ms,
        )
        return ReconciliationReport(
            subject_id=desired.subject_id,
            drift_fields=drift,
            repair_enqueued=repair_enqueued,
            attention_recorded=True,
        )
