from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
from uuid import uuid4

import httpx
import pytest

from development.remote_workplan.http_app import create_app
from development.remote_workplan.gateway import GatewayError, RemoteWorkplanGateway
from development.remote_workplan.linear import (
    DesiredProjection,
    InboxWrite,
    HTTPResult,
    LinearGraphQLClient,
    LinearProjectionAdapter,
    LinearTransportError,
    OutboxItem,
    ProjectionActivity,
    WebhookVerificationError,
    verify_webhook,
)
from tests.development.test_remote_workplan_gateway import (
    ALL_COORDINATOR,
    ALL_WORKER,
    AUTHORITY_GRANT,
    REPOSITORY_ID,
    definition,
)


NOW = 1_800_000_000_000
SECRET = b"webhook-secret"


def signed(body: bytes, *, delivery: str | None = None, timestamp: int = NOW):
    return {
        "Linear-Delivery": delivery or str(uuid4()),
        "Linear-Signature": hmac.new(SECRET, body, hashlib.sha256).hexdigest(),
        "Linear-Timestamp": str(timestamp),
    }


class FakeStore:
    def __init__(self):
        self.inbox = {}
        self.latest = {}
        self.outbox = []
        self.sent = []
        self.pending = []
        self.attention = []
        self.next_id = 1
        self.claims = []

    def record_linear_webhook(self, **values):
        delivery_id = values["delivery_id"]
        if delivery_id in self.inbox:
            return InboxWrite(inserted=False)
        payload = json.loads(values["payload_json"])
        resource = payload.get("data", {}).get("id", payload.get("type", "global"))
        previous = self.latest.get(resource, -1)
        reordered = values["event_timestamp_ms"] < previous
        self.latest[resource] = max(previous, values["event_timestamp_ms"])
        self.inbox[delivery_id] = values
        return InboxWrite(inserted=True, reordered=reordered)

    def enqueue_projection_outbox(self, **values):
        if any(row.dedupe_key == values["dedupe_key"] for row in self.outbox):
            return False
        self.outbox.append(
            OutboxItem(
                id=self.next_id,
                dedupe_key=values["dedupe_key"],
                kind=values["kind"],
                payload_json=values["payload_json"],
            )
        )
        self.next_id += 1
        return True

    def claim_projection_outbox(self, **_values):
        self.claims.append(_values)
        return [row for row in self.outbox if row.id not in self.sent][: _values["limit"]]

    def mark_projection_sent(self, **values):
        self.sent.append(values["item_id"])

    def mark_projection_pending(self, **values):
        self.pending.append(values)

    def record_projection_attention(self, **values):
        self.attention.append(values)


class FakeClient:
    def __init__(self):
        self.activities = []
        self.cards = []
        self.observed = {}
        self.failure = None

    def project_activity(self, payload, *, idempotency_key):
        if self.failure:
            raise self.failure
        self.activities.append((payload, idempotency_key))

    def project_card(self, payload, *, idempotency_key):
        if self.failure:
            raise self.failure
        self.cards.append((payload, idempotency_key))

    def fetch_card(self, subject_id):
        return self.observed.get(subject_id)


@pytest.fixture
def boundary():
    store = FakeStore()
    client = FakeClient()
    adapter = LinearProjectionAdapter(
        store=store, client=client, webhook_secret=SECRET, clock_ms=lambda: NOW
    )
    return adapter, store, client


def test_verifies_hmac_over_exact_raw_body_and_timestamp():
    body = b'{"type":"Issue","webhookTimestamp":1800000000000}'
    result = verify_webhook(
        raw_body=body, headers=signed(body), secret=SECRET, now_ms=NOW
    )
    assert result.payload["type"] == "Issue"

    reformatted = json.dumps(json.loads(body), indent=2).encode()
    with pytest.raises(WebhookVerificationError, match="signature mismatch"):
        verify_webhook(
            raw_body=reformatted, headers=signed(body), secret=SECRET, now_ms=NOW
        )


@pytest.mark.parametrize(
    ("headers", "code"),
    [
        ({}, "missing_headers"),
        (
            signed(b"{}", delivery="not-a-uuid"),
            "invalid_delivery",
        ),
        (
            signed(b"{}", timestamp=NOW - 60_001),
            "stale_timestamp",
        ),
    ],
)
def test_rejects_missing_invalid_or_replayed_headers(headers, code):
    with pytest.raises(WebhookVerificationError) as caught:
        verify_webhook(raw_body=b"{}", headers=headers, secret=SECRET, now_ms=NOW)
    assert caught.value.code == code


def test_rejects_stale_body_webhook_timestamp_even_with_fresh_header():
    body = json.dumps({"webhookTimestamp": NOW - 60_001}).encode()
    with pytest.raises(WebhookVerificationError) as caught:
        verify_webhook(raw_body=body, headers=signed(body), secret=SECRET, now_ms=NOW)
    assert caught.value.code == "stale_timestamp"


def test_requires_signed_body_timestamp_and_uses_it_for_ordering():
    missing = b'{"type":"Issue"}'
    with pytest.raises(WebhookVerificationError) as caught:
        verify_webhook(raw_body=missing, headers=signed(missing), secret=SECRET, now_ms=NOW)
    assert caught.value.code == "invalid_timestamp"

    body = json.dumps({"type": "Issue", "webhookTimestamp": NOW - 10}).encode()
    verified = verify_webhook(
        raw_body=body,
        headers=signed(body, timestamp=NOW - 59_000),
        secret=SECRET,
        now_ms=NOW,
    )
    assert verified.timestamp_ms == NOW - 10


def test_unique_delivery_inbox_deduplicates_and_marks_reordered(boundary):
    adapter, store, _client = boundary
    first = json.dumps(
        {"type": "Issue", "data": {"id": "META-2"}, "webhookTimestamp": NOW}
    ).encode()
    first_id = str(uuid4())
    receipt = adapter.receive_webhook(
        raw_body=first, headers=signed(first, delivery=first_id, timestamp=NOW)
    )
    duplicate = adapter.receive_webhook(
        raw_body=first, headers=signed(first, delivery=first_id, timestamp=NOW)
    )
    older_id = str(uuid4())
    older = json.dumps(
        {"type": "Issue", "data": {"id": "META-2"}, "webhookTimestamp": NOW - 1}
    ).encode()
    reordered = adapter.receive_webhook(
        raw_body=older, headers=signed(older, delivery=older_id, timestamp=NOW - 1)
    )
    assert receipt.duplicate is False
    assert duplicate.duplicate is True
    assert reordered.reordered is True
    assert len(store.inbox) == 2


def test_protected_issue_change_is_routed_to_atomic_freeze_store_api(boundary):
    adapter, store, _client = boundary
    body = json.dumps(
        {
            "type": "Issue",
            "action": "update",
            "data": {"id": "uuid", "identifier": "META-2"},
            "updatedFrom": {"labelIds": ["old-label"], "stateId": "old-state"},
            "webhookTimestamp": NOW,
        }
    ).encode()
    delivery = str(uuid4())
    first = adapter.receive_webhook(
        raw_body=body, headers=signed(body, delivery=delivery)
    )
    duplicate = adapter.receive_webhook(
        raw_body=body, headers=signed(body, delivery=delivery)
    )
    assert first.duplicate is False
    assert duplicate.duplicate is True
    stored = store.inbox[delivery]
    assert stored["card_id"] == "META-2"
    assert stored["protected_change"] is True


def test_status_only_issue_change_does_not_request_claim_freeze(boundary):
    adapter, store, _client = boundary
    body = json.dumps(
        {
            "type": "Issue",
            "action": "update",
            "data": {"identifier": "META-2"},
            "updatedFrom": {"stateId": "old-state"},
            "webhookTimestamp": NOW,
        }
    ).encode()
    receipt = adapter.receive_webhook(raw_body=body, headers=signed(body))
    assert store.inbox[receipt.delivery_id]["protected_change"] is False


def claimed_gateway(tmp_path):
    gateway = RemoteWorkplanGateway(str(tmp_path / "claimed.sqlite"), clock=lambda: NOW / 1000)
    coordinator = gateway.issue_host_credential(
        actor="coordinator:control:test", scopes=ALL_COORDINATOR, ttl_seconds=10_000
    )["credential"]
    worker = gateway.issue_host_credential(
        actor="codex:host:test", scopes=ALL_WORKER, ttl_seconds=10_000
    )["credential"]
    ready = gateway.qualify_card(
        card_id="META-2",
        title="META-2",
        paths=("development/remote_workplan",),
        definition=definition(),
        dependencies=(),
        credential=coordinator,
        authority_grant=AUTHORITY_GRANT,
        repository_id=REPOSITORY_ID,
    )
    claim = gateway.claim(
        card_id="META-2",
        expected_revision=ready["revision"],
        expected_definition_hash=ready["definition_hash"],
        credential=worker,
    )
    return gateway, worker, claim


def protected_issue_body():
    return json.dumps(
        {
            "type": "Issue",
            "action": "update",
            "data": {"identifier": "META-2"},
            "updatedFrom": {"description": "old definition"},
            "webhookTimestamp": NOW,
        }
    ).encode()


def test_real_gateway_atomically_freezes_claim_once_for_protected_webhook(tmp_path):
    gateway, worker, claim = claimed_gateway(tmp_path)
    client = FakeClient()
    adapter = LinearProjectionAdapter(
        store=gateway, client=client, webhook_secret=SECRET, clock_ms=lambda: NOW
    )
    body = protected_issue_body()
    delivery = str(uuid4())
    assert adapter.receive_webhook(
        raw_body=body, headers=signed(body, delivery=delivery)
    ).duplicate is False
    assert adapter.receive_webhook(
        raw_body=body, headers=signed(body, delivery=delivery)
    ).duplicate is True
    card = gateway.list_cards()[0]
    assert card["owner"] == "codex:host:test"
    assert card["revalidation_required"] is True
    assert [receipt["kind"] for receipt in gateway.receipts(card_id="META-2")].count(
        "definition_changed"
    ) == 1
    with pytest.raises(GatewayError) as frozen:
        gateway.heartbeat(
            card_id="META-2",
            fencing_token=claim["fencing_token"],
            credential=worker,
        )
    assert getattr(frozen.value, "code", None) == "definition_changed"
    assert adapter.dispatch().sent == 3
    assert [payload["action"] for payload, _key in client.activities] == [
        "qualify",
        "claim",
        "definition_changed",
    ]


def test_protected_webhook_rolls_back_inbox_and_freeze_when_receipt_outbox_fails(tmp_path):
    gateway, _worker, _claim = claimed_gateway(tmp_path)
    adapter = LinearProjectionAdapter(
        store=gateway, client=FakeClient(), webhook_secret=SECRET, clock_ms=lambda: NOW
    )
    with gateway.store.transaction() as db:
        db.execute(
            "CREATE TRIGGER reject_webhook_outbox BEFORE INSERT ON projection_outbox "
            "BEGIN SELECT RAISE(ABORT, 'projection rejected'); END"
        )
    body = protected_issue_body()
    with pytest.raises(sqlite3.IntegrityError, match="projection rejected"):
        adapter.receive_webhook(raw_body=body, headers=signed(body))
    with gateway.store.read() as db:
        assert db.execute("SELECT COUNT(*) FROM webhook_inbox").fetchone()[0] == 0
        card = db.execute(
            "SELECT revalidation_required,revision FROM cards WHERE card_id='META-2'"
        ).fetchone()
    assert tuple(card) == (0, 2)


def test_committed_activity_is_allowlisted_secret_free_and_idempotent(boundary):
    adapter, store, _client = boundary
    activity = ProjectionActivity(
        subject_id="META-2",
        action="claim",
        canonical_revision=3,
        occurred_at_ms=NOW,
        actor="codex",
        receipt_hash="sha256:" + "a" * 64,
    )
    assert store.enqueue_projection_outbox(
        dedupe_key=activity.dedupe_key,
        kind="activity",
        payload_json=json.dumps(activity.payload()),
        created_at_ms=NOW,
    ) is True
    assert store.enqueue_projection_outbox(
        dedupe_key=activity.dedupe_key,
        kind="activity",
        payload_json=json.dumps(activity.payload()),
        created_at_ms=NOW,
    ) is False
    payload = json.loads(store.outbox[0].payload_json)
    assert payload == activity.payload()
    serialized = store.outbox[0].payload_json.casefold()
    assert "token" not in serialized
    assert "secret" not in serialized
    assert "authorization" not in serialized


def test_dispatch_marks_success_sent(boundary):
    adapter, store, client = boundary
    activity = ProjectionActivity("META-2", "checkpoint", 4, NOW)
    store.enqueue_projection_outbox(
        dedupe_key=activity.dedupe_key,
        kind="activity",
        payload_json=json.dumps(activity.payload()),
        created_at_ms=NOW,
    )
    report = adapter.dispatch()
    assert report.sent == 1
    assert store.sent == [1]
    assert client.activities[0][0]["canonical_revision"] == 4
    assert client.activities[0][1] == activity.dedupe_key
    assert store.claims == [{"now_ms": NOW, "limit": 100, "lease_ms": 60_000}]


@pytest.mark.parametrize("code", ["oauth_revoked", "team_forbidden"])
def test_oauth_and_team_failures_stay_pending_and_need_attention(boundary, code):
    adapter, store, client = boundary
    activity = ProjectionActivity("META-2", "submit", 5, NOW)
    store.enqueue_projection_outbox(
        dedupe_key=activity.dedupe_key,
        kind="activity",
        payload_json=json.dumps(activity.payload()),
        created_at_ms=NOW,
    )
    client.failure = LinearTransportError(code, "projection denied", retryable=False)
    report = adapter.dispatch()
    assert report == type(report)(sent=0, pending=1, attention=1)
    assert store.sent == []
    assert store.pending[0]["needs_attention"] is True
    assert store.pending[0]["error_code"] == code


@pytest.mark.parametrize("action", ["Authorization: Bearer super-secret", "deploy", ""])
def test_activity_rejects_noncanonical_values(action):
    with pytest.raises(LinearTransportError) as caught:
        ProjectionActivity("META-2", action, 4, NOW)
    assert caught.value.code == "unsafe_projection_payload"


def test_oauth_revoked_webhook_only_records_projection_attention(boundary):
    adapter, store, _client = boundary
    adapter.record_oauth_revoked(app_id="app-1", delivery_id="delivery-1")
    assert store.attention[0]["kind"] == "oauth_revoked"
    assert store.outbox == []


def test_reconciliation_permission_failure_records_attention_without_repair(boundary):
    adapter, store, client = boundary
    desired = DesiredProjection("META-2", "Remote gateway", "Review", "team", 10)
    client.failure = LinearTransportError("team_forbidden", "team access removed", retryable=False)

    def denied(_subject_id):
        raise client.failure

    client.fetch_card = denied
    result = adapter.reconcile(desired)
    assert result.attention_recorded is True
    assert store.attention[0]["kind"] == "team_forbidden"
    assert store.outbox == []


def test_reconciliation_emits_deduplicated_repair_and_attention(boundary):
    adapter, store, client = boundary
    desired = DesiredProjection("META-2", "Remote gateway", "In Progress", "team", 9)
    client.observed["META-2"] = {
        "title": "manually renamed",
        "state": "Done",
        "team_id": "team",
        "canonical_revision": 8,
        "updatedAt": "2026-07-15T01:00:00Z",
    }
    result = adapter.reconcile(desired)
    repeated = adapter.reconcile(desired)
    assert result.drift_fields == ("title", "state", "canonical_revision")
    assert result.repair_enqueued is True
    assert repeated.repair_enqueued is False
    assert len(store.outbox) == 1
    assert json.loads(store.outbox[0].payload_json) == desired.payload()
    assert store.attention[0]["kind"] == "manual_state_drift"

    client.observed["META-2"]["updatedAt"] = "2026-07-15T02:00:00Z"
    recurring = adapter.reconcile(desired)
    assert recurring.repair_enqueued is True
    assert len(store.outbox) == 2


def test_reconciliation_is_noop_when_projection_matches(boundary):
    adapter, store, client = boundary
    desired = DesiredProjection("META-2", "Remote gateway", "Review", "team", 10)
    client.observed["META-2"] = desired.payload()
    result = adapter.reconcile(desired)
    assert result.drift_fields == ()
    assert store.outbox == []
    assert store.attention == []


@pytest.mark.asyncio
async def test_http_health_and_durable_webhook_ack(boundary):
    adapter, store, _client = boundary
    app = create_app(adapter)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        health = await client.get("/health")
        body = json.dumps({"type": "Issue", "webhookTimestamp": NOW}).encode()
        webhook = await client.post("/webhooks/linear", content=body, headers=signed(body))
    assert health.json() == {"status": "ok", "role": "projection"}
    assert webhook.status_code == 200
    assert webhook.json()["accepted"] is True
    assert len(store.inbox) == 1


@pytest.mark.asyncio
async def test_http_rejects_invalid_signature(boundary):
    adapter, _store, _client = boundary
    app = create_app(adapter)
    transport = httpx.ASGITransport(app=app)
    body = b"{}"
    headers = signed(body)
    headers["Linear-Signature"] = "0" * 64
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/webhooks/linear", content=body, headers=headers)
    assert response.status_code == 401
    assert response.json() == {"accepted": False, "error": "invalid_signature"}


def test_adapter_protocol_matches_sqlite_gateway(tmp_path):
    gateway = RemoteWorkplanGateway(str(tmp_path / "gateway.sqlite"), clock=lambda: NOW / 1000)
    client = FakeClient()
    adapter = LinearProjectionAdapter(
        store=gateway, client=client, webhook_secret=SECRET, clock_ms=lambda: NOW
    )
    body = json.dumps(
        {"type": "Issue", "data": {"id": "META-2"}, "webhookTimestamp": NOW}
    ).encode()
    first = adapter.receive_webhook(raw_body=body, headers=signed(body))
    duplicate = adapter.receive_webhook(
        raw_body=body,
        headers=signed(body, delivery=first.delivery_id),
    )
    assert duplicate.duplicate is True

    activity = ProjectionActivity("META-2", "checkpoint", 2, NOW)
    assert gateway.enqueue_projection_outbox(
        dedupe_key=activity.dedupe_key,
        kind="activity",
        payload_json=json.dumps(activity.payload()),
        created_at_ms=NOW,
    ) is True
    assert adapter.dispatch().sent == 1
    assert client.activities == [(activity.payload(), activity.dedupe_key)]


def test_adapter_recovers_stale_dispatch_lease_with_same_idempotency_key(tmp_path):
    gateway = RemoteWorkplanGateway(str(tmp_path / "gateway.sqlite"), clock=lambda: NOW / 1000)
    client = FakeClient()
    activity = ProjectionActivity("META-2", "checkpoint", 2, NOW)
    gateway.enqueue_projection_outbox(
        dedupe_key=activity.dedupe_key,
        kind="activity",
        payload_json=json.dumps(activity.payload()),
        created_at_ms=NOW,
    )
    crashed_claim = gateway.claim_projection_outbox(
        now_ms=NOW, limit=1, lease_ms=60_000
    )
    assert len(crashed_claim) == 1

    adapter = LinearProjectionAdapter(
        store=gateway,
        client=client,
        webhook_secret=SECRET,
        clock_ms=lambda: NOW + 60_001,
        outbox_lease_ms=60_000,
    )
    assert adapter.dispatch().sent == 1
    assert client.activities == [(activity.payload(), activity.dedupe_key)]


class RecordingTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, **values):
        self.calls.append(values)
        return self.responses.pop(0)


def graphql_result(data, status=200):
    return HTTPResult(status, json.dumps({"data": data}).encode())


def test_graphql_client_fetches_projected_issue_with_revision_marker():
    transport = RecordingTransport(
        [
            graphql_result(
                {
                    "issue": {
                        "id": "issue-id",
                        "title": "Remote gateway",
                        "updatedAt": "2026-07-15T03:00:00Z",
                        "state": {"id": "state-id", "name": "Review"},
                        "team": {"id": "team-id"},
                    }
                }
            ),
            graphql_result(
                {
                    "issue": {
                        "comments": {
                            "nodes": [
                                {"body": "<!-- meta-harness-revision:7 -->"},
                                {"body": "<!-- meta-harness-revision:12 -->"},
                            ],
                            "pageInfo": {"hasPreviousPage": False, "startCursor": "a"},
                        }
                    }
                }
            ),
        ]
    )
    client = LinearGraphQLClient(access_token="oauth-secret", transport=transport)
    card = client.fetch_card("META-2")
    assert card == {
        "subject_id": "META-2",
        "title": "Remote gateway",
        "state": "state-id",
        "team_id": "team-id",
        "canonical_revision": 12,
        "updatedAt": "2026-07-15T03:00:00Z",
    }
    request = json.loads(transport.calls[0]["body"])
    assert request["variables"] == {"id": "META-2"}
    assert transport.calls[0]["headers"]["Authorization"] == "Bearer oauth-secret"


def test_graphql_activity_retry_uses_stable_key_and_detects_existing_marker():
    key = "receipt:sha256:abc"
    marker = f"<!-- meta-harness-idempotency:{key} -->"
    transport = RecordingTransport(
        [
            graphql_result({"issue": {"comments": {"nodes": []}}}),
            graphql_result({"commentCreate": {"success": True}}),
            graphql_result(
                {"issue": {"comments": {"nodes": [{"body": marker}]}}}
            ),
        ]
    )
    client = LinearGraphQLClient(access_token="oauth-secret", transport=transport)
    activity = ProjectionActivity("META-2", "checkpoint", 12, NOW).payload()
    client.project_activity(activity, idempotency_key=key)
    client.project_activity(activity, idempotency_key=key)
    assert len(transport.calls) == 3
    create_call = transport.calls[1]
    assert create_call["headers"]["Idempotency-Key"] == key
    assert marker in json.loads(create_call["body"])["variables"]["input"]["body"]


def test_graphql_activity_scans_older_comment_pages_before_retrying():
    key = "receipt:sha256:older"
    marker = f"<!-- meta-harness-idempotency:{key} -->"
    transport = RecordingTransport(
        [
            graphql_result(
                {
                    "issue": {
                        "comments": {
                            "nodes": [{"body": "newer unrelated"}],
                            "pageInfo": {"hasPreviousPage": True, "startCursor": "cursor-2"},
                        }
                    }
                }
            ),
            graphql_result(
                {
                    "issue": {
                        "comments": {
                            "nodes": [{"body": marker}],
                            "pageInfo": {"hasPreviousPage": False, "startCursor": "cursor-1"},
                        }
                    }
                }
            ),
        ]
    )
    client = LinearGraphQLClient(access_token="oauth-secret", transport=transport)
    client.project_activity(
        ProjectionActivity("META-2", "checkpoint", 12, NOW).payload(),
        idempotency_key=key,
    )
    assert len(transport.calls) == 2
    second_variables = json.loads(transport.calls[1]["body"])["variables"]
    assert second_variables["before"] == "cursor-2"


def test_graphql_card_repair_updates_issue_and_projects_revision_marker():
    transport = RecordingTransport(
        [
            graphql_result({"issueUpdate": {"success": True}}),
            graphql_result({"issue": {"comments": {"nodes": []}}}),
            graphql_result({"commentCreate": {"success": True}}),
        ]
    )
    client = LinearGraphQLClient(access_token="oauth-secret", transport=transport)
    client.project_card(
        DesiredProjection("META-2", "Remote gateway", "state-id", "team-id", 12).payload(),
        idempotency_key="repair:key",
    )
    update = json.loads(transport.calls[0]["body"])["variables"]
    assert update == {
        "id": "META-2",
        "input": {
            "title": "Remote gateway",
            "stateId": "state-id",
            "teamId": "team-id",
        },
    }
    assert transport.calls[0]["headers"]["Idempotency-Key"] == "repair:key"
    assert transport.calls[2]["headers"]["Idempotency-Key"] == "repair:key:revision"


@pytest.mark.parametrize(
    ("result", "code", "retryable"),
    [
        (HTTPResult(401, b"{}"), "oauth_unauthorized", False),
        (HTTPResult(403, b"{}"), "team_forbidden", False),
        (
            HTTPResult(
                400,
                json.dumps(
                    {"errors": [{"message": "do not echo oauth-secret", "extensions": {"code": "RATELIMITED"}}]}
                ).encode(),
            ),
            "linear_unavailable",
            True,
        ),
    ],
)
def test_graphql_client_classifies_failures_without_secret_text(result, code, retryable):
    transport = RecordingTransport([result])
    client = LinearGraphQLClient(access_token="oauth-secret", transport=transport)
    with pytest.raises(LinearTransportError) as caught:
        client.fetch_card("META-2")
    assert caught.value.code == code
    assert caught.value.retryable is retryable
    assert "oauth-secret" not in str(caught.value)
