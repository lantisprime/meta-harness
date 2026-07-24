from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3

import pytest

from development.remote_workplan import GatewayError, RemoteWorkplanGateway
from development.remote_workplan.gateway import compute_definition_hash


ALL_WORKER = {
    "claim", "bind", "heartbeat", "checkpoint", "block", "resume", "submit", "list"
}
ALL_COORDINATOR = {
    "qualify", "integrate", "accept", "revalidate", "requeue", "reassign",
    "cancel", "backend_epoch", "list",
}
AUTHORITY_GRANT = {
    "executionCoordination": True,
    "evaluator": False,
    "merge": False,
    "promotion": False,
    "deployment": False,
    "credentials": False,
    "runtime": False,
}
REPOSITORY_ID = "sha256:" + "a" * 64


class Clock:
    def __init__(self, value: int = 1_000):
        self.value = value

    def __call__(self) -> int:
        return self.value


@pytest.fixture
def setup(tmp_path):
    clock = Clock()
    gateway = RemoteWorkplanGateway(str(tmp_path / "gateway.sqlite3"), clock=clock)
    coordinator = gateway.issue_host_credential(
        actor="coordinator:control:run", scopes=ALL_COORDINATOR, ttl_seconds=10_000
    )["credential"]
    host_a = gateway.issue_host_credential(
        actor="codex:host-a:seat", scopes=ALL_WORKER, ttl_seconds=10_000
    )["credential"]
    host_b = gateway.issue_host_credential(
        actor="claude:host-b:seat", scopes=ALL_WORKER, ttl_seconds=10_000
    )["credential"]
    return gateway, clock, coordinator, host_a, host_b


def definition(source="git:abc"):
    result = {
        "sourceRevision": source,
        "worktreePath": "/work/meta-2",
        "branch": "dev/meta-2",
        "baseCommit": "abc",
        "currentHead": "abc",
        "plane": "development",
        "frozenAxes": ["H", "E", "W"],
        "budget": "bounded",
        "stopCondition": "tests pass",
        "evaluatorAuthority": "independent coordinator",
        "acceptanceCommands": ["pytest -q tests/development"],
        "nextCheckpoint": "gateway tests",
        "allowedOwnerNamespaces": ["codex", "claude"],
    }
    result["definitionHash"] = compute_definition_hash(result)
    return result


def qualify(gateway, coordinator, card_id="META-2", paths=("development/remote_workplan",), dependencies=()):
    return gateway.qualify_card(
        card_id=card_id,
        title=card_id,
        paths=paths,
        definition=definition(),
        dependencies=dependencies,
        credential=coordinator,
        authority_grant=AUTHORITY_GRANT,
        repository_id=REPOSITORY_ID,
    )


def test_credentials_are_hashed_scoped_and_short_lived(setup):
    gateway, clock, coordinator, host_a, _ = setup
    raw = host_a
    with gateway.store.read() as db:
        stored = db.execute("SELECT secret_hash FROM credentials WHERE actor LIKE 'codex:%'").fetchone()[0]
    assert raw not in stored
    with pytest.raises(GatewayError, match="does not allow qualify") as denied:
        qualify(gateway, host_a)
    assert denied.value.code == "scope_denied"
    clock.value = 20_000
    with pytest.raises(GatewayError) as expired:
        gateway.list_cards(credential=coordinator)
    assert expired.value.code == "credential_expired"


def test_two_connections_racing_yield_one_winner_and_one_fence(setup):
    gateway, _, coordinator, host_a, host_b = setup
    ready = qualify(gateway, coordinator)

    def claim(credential):
        contender = RemoteWorkplanGateway(gateway.store.db_path, clock=gateway._clock)
        try:
            return contender.claim(
                card_id="META-2",
                expected_revision=ready["revision"],
                expected_definition_hash=ready["definition_hash"],
                credential=credential,
            )
        except GatewayError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, (host_a, host_b)))
    winners = [result for result in results if isinstance(result, dict)]
    losers = [result for result in results if isinstance(result, GatewayError)]
    assert len(winners) == len(losers) == 1
    assert winners[0]["fencing_token"] == 1
    assert losers[0].code in {"stale_revision", "card_not_ready"}
    assert gateway.list_cards()[0]["owner"] == winners[0]["owner"]


def test_paths_are_cross_platform_normalized_and_parent_child_exclusive(setup):
    gateway, _, coordinator, host_a, host_b = setup
    with pytest.raises(GatewayError) as invalid:
        qualify(gateway, coordinator, paths=(r"development\remote_workplan",))
    assert invalid.value.code == "invalid_path"
    qualify(gateway, coordinator, paths=("development/remote_workplan",))
    with pytest.raises(GatewayError) as conflict:
        qualify(gateway, coordinator, card_id="META-3", paths=("development/remote_workplan/seat",))
    assert conflict.value.code == "path_conflict"


def test_dependency_and_one_card_wip_fail_closed(setup):
    gateway, _, coordinator, host_a, _ = setup
    qualify(gateway, coordinator, card_id="BASE", paths=("base",))
    with pytest.raises(GatewayError) as unmet:
        qualify(gateway, coordinator, card_id="NEXT", paths=("next",), dependencies=("BASE",))
    assert unmet.value.code == "dependency_unmet"
    base = gateway.list_cards()[0]
    gateway.claim(card_id="BASE", expected_revision=1, expected_definition_hash=base["definition_hash"], credential=host_a)
    other = qualify(gateway, coordinator, card_id="OTHER", paths=("other",))
    with pytest.raises(GatewayError) as wip:
        gateway.claim(card_id="OTHER", expected_revision=1, expected_definition_hash=other["definition_hash"], credential=host_a)
    assert wip.value.code == "wip_limit"


def test_task_bundle_and_bind_receipt_are_secret_free(setup):
    gateway, _, coordinator, host_a, _ = setup
    ready = qualify(gateway, coordinator)
    claim = gateway.claim(card_id="META-2", expected_revision=1, expected_definition_hash=ready["definition_hash"], credential=host_a)
    serialized = json.dumps(claim["task_bundle"])
    assert host_a not in serialized and coordinator not in serialized
    bound = gateway.bind_worktree(
        card_id="META-2", expected_revision=2, fencing_token=claim["fencing_token"],
        credential=host_a, repository_id=REPOSITORY_ID, worktree_path="/work/meta-2",
        branch="dev/meta-2", base_commit="abc",
    )
    assert bound["status"] == "in_progress"
    assert gateway.receipts(card_id="META-2")[-1]["kind"] == "bind_worktree"


def test_bind_rejects_repository_other_than_coordinator_frozen_identity(setup):
    gateway, _, coordinator, host_a, _ = setup
    ready = qualify(gateway, coordinator)
    claim = gateway.claim(
        card_id="META-2",
        expected_revision=ready["revision"],
        expected_definition_hash=ready["definition_hash"],
        credential=host_a,
    )
    with pytest.raises(GatewayError) as mismatch:
        gateway.bind_worktree(
            card_id="META-2",
            expected_revision=claim["revision"],
            fencing_token=claim["fencing_token"],
            credential=host_a,
            repository_id="sha256:" + "b" * 64,
            worktree_path="/work/meta-2",
            branch="dev/meta-2",
            base_commit="abc",
        )
    assert mismatch.value.code == "repository_mismatch"


def test_expiry_marks_attention_but_does_not_reassign(setup):
    gateway, clock, coordinator, host_a, host_b = setup
    ready = qualify(gateway, coordinator)
    claim = gateway.claim(card_id="META-2", expected_revision=1, expected_definition_hash=ready["definition_hash"], credential=host_a, lease_seconds=5)
    clock.value += 6
    with pytest.raises(GatewayError) as expired:
        gateway.heartbeat(card_id="META-2", fencing_token=claim["fencing_token"], credential=host_a)
    assert expired.value.code == "fence_expired"
    card = gateway.list_cards()[0]
    assert card["attention_required"] is True
    assert card["owner"] == "codex:host-a:seat"
    with pytest.raises(GatewayError) as second:
        gateway.claim(card_id="META-2", expected_revision=card["revision"], expected_definition_hash=card["definition_hash"], credential=host_b)
    assert second.value.code == "card_not_ready"


def test_coordinator_reassign_increments_fence_and_stale_owner_cannot_mutate(setup):
    gateway, _, coordinator, host_a, host_b = setup
    ready = qualify(gateway, coordinator)
    claim = gateway.claim(card_id="META-2", expected_revision=1, expected_definition_hash=ready["definition_hash"], credential=host_a)
    reassigned = gateway.reassign(card_id="META-2", expected_revision=2, credential=coordinator, new_owner="claude:host-b:seat", reason="host lost")
    assert reassigned["fencing_token"] == claim["fencing_token"] + 1
    with pytest.raises(GatewayError) as stale:
        gateway.heartbeat(card_id="META-2", fencing_token=claim["fencing_token"], credential=host_a)
    assert stale.value.code in {"owner_mismatch", "stale_fence"}
    assert gateway.heartbeat(card_id="META-2", fencing_token=reassigned["fencing_token"], credential=host_b)["fencing_token"] == 2


def test_submit_integrate_accept_requires_distinct_coordinator_authority(setup):
    gateway, _, coordinator, host_a, _ = setup
    ready = qualify(gateway, coordinator)
    claim = gateway.claim(card_id="META-2", expected_revision=1, expected_definition_hash=ready["definition_hash"], credential=host_a)
    gateway.bind_worktree(card_id="META-2", expected_revision=2, fencing_token=1, credential=host_a, repository_id=REPOSITORY_ID, worktree_path="/work/meta-2", branch="dev/meta-2", base_commit="abc")
    head_ancestry = {"base_commit": "abc", "head_commit": "def", "is_descendant": True, "verification": "git merge-base --is-ancestor abc def"}
    submitted = gateway.submit(card_id="META-2", expected_revision=3, fencing_token=1, credential=host_a, head_commit="def", evidence=("tests pass",), repository_id=REPOSITORY_ID, branch="dev/meta-2", base_commit="abc", ancestry=head_ancestry)
    ancestry = {"review_head": "def", "integrated_commit": "fed", "is_ancestor": True, "verification": "git merge-base --is-ancestor def fed"}
    integrated = gateway.integrate(card_id="META-2", expected_revision=submitted["revision"], credential=coordinator, fencing_token=1, integrated_commit="fed", review_head="def", ancestry=ancestry, evidence=("review accept",))
    accepted = gateway.accept(card_id="META-2", expected_revision=integrated["revision"], credential=coordinator, fencing_token=1, evidence=("verification pass",))
    assert accepted["status"] == "done"
    assert gateway.list_cards()[0]["owner"] is None
    with gateway.store.read() as db:
        assert db.execute("SELECT COUNT(*) FROM path_reservations").fetchone()[0] == 0


def test_receipts_are_hash_chained_and_database_immutable(setup):
    gateway, _, coordinator, _, _ = setup
    qualify(gateway, coordinator)
    receipts = gateway.receipts(card_id="META-2")
    assert receipts[0]["previous_hash"] is None
    with gateway.store.transaction() as db:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            db.execute("UPDATE receipts SET kind='tampered'")


def test_backend_switch_requires_zero_active_claims(setup):
    gateway, _, coordinator, host_a, _ = setup
    ready = qualify(gateway, coordinator)
    gateway.claim(card_id="META-2", expected_revision=1, expected_definition_hash=ready["definition_hash"], credential=host_a)
    with pytest.raises(GatewayError) as active:
        gateway.backend_epoch(credential=coordinator, expected_epoch=1, backend="filesystem", reconciled_snapshot_hash="sha256:" + "a" * 64)
    assert active.value.code == "active_claims"


def test_transition_receipt_and_projection_outbox_commit_or_rollback_together(setup):
    gateway, _, coordinator, host_a, _ = setup
    ready = qualify(gateway, coordinator)
    claim = gateway.claim(card_id="META-2", expected_revision=1, expected_definition_hash=ready["definition_hash"], credential=host_a)
    with gateway.store.read() as db:
        assert db.execute("SELECT COUNT(*) FROM projection_outbox").fetchone()[0] == 2
        owner_before = db.execute("SELECT owner FROM cards WHERE card_id='META-2'").fetchone()[0]
    item = gateway.claim_projection_outbox(now_ms=1_000_000, limit=1)[0]
    gateway.mark_projection_pending(item_id=item.id, expected_attempt=item.attempts, error_code="oauth_revoked", error_message="revoked", attempted_at_ms=1_000_001, needs_attention=True)
    assert gateway.list_cards()[0]["owner"] == owner_before

    other_db = gateway.store.db_path + ".rollback"
    isolated = RemoteWorkplanGateway(other_db, clock=gateway._clock)
    coord = isolated.issue_host_credential(actor="coordinator:control:rollback", scopes=ALL_COORDINATOR, ttl_seconds=100)["credential"]
    with isolated.store.transaction() as db:
        db.execute("CREATE TRIGGER reject_outbox BEFORE INSERT ON projection_outbox BEGIN SELECT RAISE(ABORT, 'projection rejected'); END")
    with pytest.raises(sqlite3.IntegrityError, match="projection rejected"):
        qualify(isolated, coord, card_id="ROLLBACK", paths=("rollback",))
    assert isolated.list_cards() == []
    with isolated.store.read() as db:
        assert db.execute("SELECT COUNT(*) FROM receipts").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM projection_outbox").fetchone()[0] == 0


def test_reclaimed_projection_attempt_fences_late_dispatcher(setup):
    gateway, _, coordinator, _, _ = setup
    qualify(gateway, coordinator)
    first = gateway.claim_projection_outbox(now_ms=1_000_000, limit=1, lease_ms=100)[0]
    second = gateway.claim_projection_outbox(now_ms=1_000_101, limit=1, lease_ms=100)[0]
    assert second.id == first.id
    assert second.attempts == first.attempts + 1
    assert gateway.mark_projection_sent(
        item_id=first.id, expected_attempt=first.attempts, sent_at_ms=1_000_102
    ) is False
    assert gateway.mark_projection_sent(
        item_id=second.id, expected_attempt=second.attempts, sent_at_ms=1_000_103
    ) is True


def test_block_release_and_resume_reacquire_are_atomic(setup):
    gateway, _, coordinator, host_a, host_b = setup
    ready = qualify(gateway, coordinator)
    claim = gateway.claim(card_id="META-2", expected_revision=1, expected_definition_hash=ready["definition_hash"], credential=host_a)
    blocked = gateway.block(card_id="META-2", expected_revision=2, fencing_token=1, credential=host_a, reason="waiting", retain_paths=False)
    competing = qualify(gateway, coordinator, card_id="META-3", paths=("development/remote_workplan/child",))
    gateway.claim(card_id="META-3", expected_revision=1, expected_definition_hash=competing["definition_hash"], credential=host_b)
    with pytest.raises(GatewayError) as conflict:
        gateway.resume(card_id="META-2", expected_revision=blocked["revision"], fencing_token=claim["fencing_token"], credential=host_a)
    assert conflict.value.code == "path_conflict"
    assert next(card for card in gateway.list_cards() if card["card_id"] == "META-2")["status"] == "blocked"


def test_requeue_restores_ready_reservation_with_zero_fence(setup):
    gateway, _, coordinator, host_a, _ = setup
    ready = qualify(gateway, coordinator)
    gateway.claim(card_id="META-2", expected_revision=1, expected_definition_hash=ready["definition_hash"], credential=host_a)
    requeued = gateway.requeue(card_id="META-2", expected_revision=2, credential=coordinator, reason="fresh seat")
    assert requeued["status"] == "ready"
    with gateway.store.read() as db:
        rows = db.execute("SELECT fencing_token FROM path_reservations WHERE card_id='META-2'").fetchall()
    assert rows and {row[0] for row in rows} == {0}


def test_qualification_requires_exact_frozen_contract_authority_and_namespace(setup):
    gateway, _, coordinator, _, host_b = setup
    incomplete = definition()
    incomplete.pop("budget")
    incomplete["definitionHash"] = compute_definition_hash(incomplete)
    with pytest.raises(GatewayError) as missing:
        gateway.qualify_card(card_id="BAD", title="bad", paths=("bad",), definition=incomplete, credential=coordinator, authority_grant=AUTHORITY_GRANT, repository_id=REPOSITORY_ID)
    assert missing.value.code == "invalid_definition"
    with pytest.raises(GatewayError) as authority:
        gateway.qualify_card(card_id="BAD", title="bad", paths=("bad",), definition=definition(), credential=coordinator, authority_grant={**AUTHORITY_GRANT, "merge": True}, repository_id=REPOSITORY_ID)
    assert authority.value.code == "invalid_authority_grant"

    restricted = definition()
    restricted["allowedOwnerNamespaces"] = ["codex"]
    restricted["definitionHash"] = compute_definition_hash(restricted)
    ready = gateway.qualify_card(card_id="RESTRICTED", title="restricted", paths=("restricted",), definition=restricted, credential=coordinator, authority_grant=AUTHORITY_GRANT, repository_id=REPOSITORY_ID)
    with pytest.raises(GatewayError) as namespace:
        gateway.claim(card_id="RESTRICTED", expected_revision=1, expected_definition_hash=ready["definition_hash"], credential=host_b)
    assert namespace.value.code == "owner_namespace_denied"


def test_pi_credential_claims_pi_only_card_and_preserves_owner(setup):
    gateway, _, coordinator, _, _ = setup
    pi_actor = "pi:host-c:seat"
    issued = gateway.issue_host_credential(
        actor=pi_actor, scopes=ALL_WORKER, ttl_seconds=10_000
    )
    pi_definition = definition()
    pi_definition["allowedOwnerNamespaces"] = ["pi"]
    pi_definition["definitionHash"] = compute_definition_hash(pi_definition)
    ready = gateway.qualify_card(
        card_id="PI-ONLY", title="pi only", paths=("pi-only",),
        definition=pi_definition, credential=coordinator,
        authority_grant=AUTHORITY_GRANT, repository_id=REPOSITORY_ID,
    )

    claimed = gateway.claim(
        card_id="PI-ONLY", expected_revision=ready["revision"],
        expected_definition_hash=ready["definition_hash"],
        credential=issued["credential"],
    )

    assert issued["actor"] == pi_actor
    assert claimed["owner"] == pi_actor
    assert claimed["task_bundle"]["owner"] == pi_actor
    assert gateway.list_cards()[0]["owner"] == pi_actor
    assert gateway.receipts(card_id="PI-ONLY")[-1]["actor"] == pi_actor


def test_pi_claim_is_denied_when_frozen_namespaces_exclude_pi(setup):
    gateway, _, coordinator, _, _ = setup
    pi_credential = gateway.issue_host_credential(
        actor="pi:host-c:seat", scopes=ALL_WORKER, ttl_seconds=10_000
    )["credential"]
    restricted = definition()
    restricted["allowedOwnerNamespaces"] = ["codex"]
    restricted["definitionHash"] = compute_definition_hash(restricted)
    ready = gateway.qualify_card(
        card_id="CODEX-ONLY", title="codex only", paths=("codex-only",),
        definition=restricted, credential=coordinator,
        authority_grant=AUTHORITY_GRANT, repository_id=REPOSITORY_ID,
    )

    with pytest.raises(GatewayError, match="owner namespace pi is not allowed") as denied:
        gateway.claim(
            card_id="CODEX-ONLY", expected_revision=ready["revision"],
            expected_definition_hash=ready["definition_hash"],
            credential=pi_credential,
        )

    assert denied.value.code == "owner_namespace_denied"
    card_after = gateway.list_cards()[0]
    assert card_after["status"] == "ready"
    assert card_after["owner"] is None


def test_qualification_rejects_unknown_owner_namespace(setup):
    gateway, _, coordinator, _, _ = setup
    invalid_definition = definition()
    invalid_definition["allowedOwnerNamespaces"] = ["bogus"]
    invalid_definition["definitionHash"] = compute_definition_hash(invalid_definition)

    with pytest.raises(GatewayError) as invalid:
        gateway.qualify_card(
            card_id="BOGUS", title="bogus", paths=("bogus",),
            definition=invalid_definition, credential=coordinator,
            authority_grant=AUTHORITY_GRANT, repository_id=REPOSITORY_ID,
        )

    assert invalid.value.code == "invalid_definition"
    assert invalid.value.message == "allowedOwnerNamespaces must be a unique supported subset"
    assert gateway.list_cards() == []


def test_reassign_to_pi_obeys_frozen_owner_namespaces(setup):
    gateway, _, coordinator, host_a, _ = setup
    allowed_definition = definition()
    allowed_definition["allowedOwnerNamespaces"] = ["codex", "pi"]
    allowed_definition["definitionHash"] = compute_definition_hash(allowed_definition)
    allowed = gateway.qualify_card(
        card_id="PI-ALLOWED", title="pi allowed", paths=("pi-allowed",),
        definition=allowed_definition, credential=coordinator,
        authority_grant=AUTHORITY_GRANT, repository_id=REPOSITORY_ID,
    )
    original = gateway.claim(
        card_id="PI-ALLOWED", expected_revision=allowed["revision"],
        expected_definition_hash=allowed["definition_hash"], credential=host_a,
    )
    pi_owner = "pi:host-c:reassigned"
    reassigned = gateway.reassign(
        card_id="PI-ALLOWED", expected_revision=original["revision"],
        credential=coordinator, new_owner=pi_owner, reason="move to pi seat",
    )
    assert reassigned["owner"] == pi_owner
    assert next(
        card for card in gateway.list_cards() if card["card_id"] == "PI-ALLOWED"
    )["owner"] == pi_owner

    restricted_definition = definition()
    restricted_definition["allowedOwnerNamespaces"] = ["codex"]
    restricted_definition["definitionHash"] = compute_definition_hash(restricted_definition)
    restricted = gateway.qualify_card(
        card_id="PI-DENIED", title="pi denied", paths=("pi-denied",),
        definition=restricted_definition, credential=coordinator,
        authority_grant=AUTHORITY_GRANT, repository_id=REPOSITORY_ID,
    )
    restricted_claim = gateway.claim(
        card_id="PI-DENIED", expected_revision=restricted["revision"],
        expected_definition_hash=restricted["definition_hash"], credential=host_a,
    )

    with pytest.raises(GatewayError) as denied:
        gateway.reassign(
            card_id="PI-DENIED", expected_revision=restricted_claim["revision"],
            credential=coordinator, new_owner="pi:host-c:denied",
            reason="attempt disallowed pi move",
        )

    assert denied.value.code == "owner_namespace_denied"
    assert denied.value.message == "new owner namespace is not allowed"
    denied_card = next(
        card for card in gateway.list_cards() if card["card_id"] == "PI-DENIED"
    )
    assert denied_card["owner"] == "codex:host-a:seat"
    assert denied_card["revision"] == restricted_claim["revision"]


def test_checkpoint_requires_bound_exact_structured_lineage(setup):
    gateway, _, coordinator, host_a, _ = setup
    ready = qualify(gateway, coordinator)
    gateway.claim(card_id="META-2", expected_revision=1, expected_definition_hash=ready["definition_hash"], credential=host_a)
    ancestry = {"base_commit": "abc", "head_commit": "def", "is_descendant": True, "verification": "git merge-base --is-ancestor abc def"}
    with pytest.raises(GatewayError) as before_bind:
        gateway.checkpoint(card_id="META-2", expected_revision=2, fencing_token=1, credential=host_a, checkpoint="work", repository_id=REPOSITORY_ID, branch="dev/meta-2", base_commit="abc", head_commit="def", ancestry=ancestry)
    assert before_bind.value.code == "invalid_transition"
    with pytest.raises(GatewayError) as mismatch:
        gateway.bind_worktree(card_id="META-2", expected_revision=2, fencing_token=1, credential=host_a, repository_id=REPOSITORY_ID, worktree_path="/wrong", branch="dev/meta-2", base_commit="abc")
    assert mismatch.value.code == "lineage_mismatch"
    gateway.bind_worktree(card_id="META-2", expected_revision=2, fencing_token=1, credential=host_a, repository_id=REPOSITORY_ID, worktree_path="/work/meta-2", branch="dev/meta-2", base_commit="abc")
    invalid = {**ancestry, "is_descendant": False}
    with pytest.raises(GatewayError) as bad_head:
        gateway.checkpoint(card_id="META-2", expected_revision=3, fencing_token=1, credential=host_a, checkpoint="work", repository_id=REPOSITORY_ID, branch="dev/meta-2", base_commit="abc", head_commit="def", ancestry=invalid)
    assert bad_head.value.code == "invalid_lineage"
    checkpoint = gateway.checkpoint(card_id="META-2", expected_revision=3, fencing_token=1, credential=host_a, checkpoint="work", repository_id=REPOSITORY_ID, branch="dev/meta-2", base_commit="abc", head_commit="def", ancestry=ancestry)
    assert checkpoint["revision"] == 4


def test_integrate_enforces_frozen_review_head_live_fence_and_ancestry(setup):
    gateway, _, coordinator, host_a, _ = setup
    ready = qualify(gateway, coordinator)
    gateway.claim(card_id="META-2", expected_revision=1, expected_definition_hash=ready["definition_hash"], credential=host_a)
    gateway.bind_worktree(card_id="META-2", expected_revision=2, fencing_token=1, credential=host_a, repository_id=REPOSITORY_ID, worktree_path="/work/meta-2", branch="dev/meta-2", base_commit="abc")
    head_ancestry = {"base_commit": "abc", "head_commit": "def", "is_descendant": True, "verification": "verified"}
    submitted = gateway.submit(card_id="META-2", expected_revision=3, fencing_token=1, credential=host_a, head_commit="def", evidence=("tests",), repository_id=REPOSITORY_ID, branch="dev/meta-2", base_commit="abc", ancestry=head_ancestry)
    ancestry = {"review_head": "def", "integrated_commit": "fed", "is_ancestor": True, "verification": "verified"}
    with pytest.raises(GatewayError) as wrong_head:
        gateway.integrate(card_id="META-2", expected_revision=submitted["revision"], credential=coordinator, fencing_token=1, integrated_commit="fed", review_head="other", ancestry={**ancestry, "review_head": "other"}, evidence=("review",))
    assert wrong_head.value.code == "review_head_mismatch"
    with pytest.raises(GatewayError) as stale:
        gateway.integrate(card_id="META-2", expected_revision=submitted["revision"], credential=coordinator, fencing_token=0, integrated_commit="fed", review_head="def", ancestry=ancestry, evidence=("review",))
    assert stale.value.code == "stale_fence"
    with pytest.raises(GatewayError) as invalid:
        gateway.integrate(card_id="META-2", expected_revision=submitted["revision"], credential=coordinator, fencing_token=1, integrated_commit="fed", review_head="def", ancestry={**ancestry, "is_ancestor": False}, evidence=("review",))
    assert invalid.value.code == "invalid_ancestry"


def test_integrate_and_accept_fail_after_expiry_and_accept_reads_receipt(setup):
    gateway, clock, coordinator, host_a, _ = setup
    ready = qualify(gateway, coordinator)
    gateway.claim(card_id="META-2", expected_revision=1, expected_definition_hash=ready["definition_hash"], credential=host_a, lease_seconds=5)
    gateway.bind_worktree(card_id="META-2", expected_revision=2, fencing_token=1, credential=host_a, repository_id=REPOSITORY_ID, worktree_path="/work/meta-2", branch="dev/meta-2", base_commit="abc")
    head_ancestry = {"base_commit": "abc", "head_commit": "def", "is_descendant": True, "verification": "verified"}
    submitted = gateway.submit(card_id="META-2", expected_revision=3, fencing_token=1, credential=host_a, head_commit="def", evidence=("tests",), repository_id=REPOSITORY_ID, branch="dev/meta-2", base_commit="abc", ancestry=head_ancestry)
    ancestry = {"review_head": "def", "integrated_commit": "fed", "is_ancestor": True, "verification": "verified"}
    clock.value += 6
    with pytest.raises(GatewayError) as expired:
        gateway.integrate(card_id="META-2", expected_revision=submitted["revision"], credential=coordinator, fencing_token=1, integrated_commit="fed", review_head="def", ancestry=ancestry, evidence=("review",))
    assert expired.value.code == "fence_expired"


def test_revalidate_rotates_fence_and_returns_refreshed_frozen_bundle(setup):
    gateway, _, coordinator, host_a, _ = setup
    ready = qualify(gateway, coordinator)
    claim = gateway.claim(card_id="META-2", expected_revision=1, expected_definition_hash=ready["definition_hash"], credential=host_a)
    revised = definition("git:def")
    revised["currentHead"] = "def"
    revised["definitionHash"] = compute_definition_hash(revised)
    refreshed = gateway.revalidate(card_id="META-2", expected_revision=2, credential=coordinator, definition=revised)
    assert refreshed["fencing_token"] == 2
    assert refreshed["task_bundle"]["fencing_token"] == 2
    assert refreshed["task_bundle"]["definition_hash"] == revised["definitionHash"]
    with pytest.raises(GatewayError) as stale:
        gateway.heartbeat(card_id="META-2", fencing_token=claim["fencing_token"], credential=host_a)
    assert stale.value.code == "stale_fence"
    assert gateway.heartbeat(card_id="META-2", fencing_token=2, credential=host_a)["fencing_token"] == 2


def test_backend_epoch_requires_named_backend_snapshot_and_audits_transition(setup):
    gateway, _, coordinator, _, _ = setup
    with pytest.raises(GatewayError) as invalid:
        gateway.backend_epoch(credential=coordinator, expected_epoch=1, backend="auto", reconciled_snapshot_hash="sha256:" + "a" * 64)
    assert invalid.value.code == "invalid_backend"
    switched = gateway.backend_epoch(credential=coordinator, expected_epoch=1, backend="filesystem", reconciled_snapshot_hash="sha256:" + "a" * 64)
    assert switched["epoch"] == 2 and switched["receipt_hash"].startswith("sha256:")
    with gateway.store.read() as db:
        assert db.execute("SELECT COUNT(*) FROM backend_receipts").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM projection_outbox WHERE dedupe_key LIKE 'backend:%'").fetchone()[0] == 1


def test_protected_webhook_freezes_once_and_stale_outbox_lease_recovers(setup):
    gateway, _, coordinator, host_a, _ = setup
    ready = qualify(gateway, coordinator)
    gateway.claim(card_id="META-2", expected_revision=1, expected_definition_hash=ready["definition_hash"], credential=host_a)
    payload = json.dumps({"type": "Issue", "action": "update", "updatedFrom": {"description": "old"}, "data": {"identifier": "META-2"}})
    first = gateway.record_webhook_delivery(delivery_id="delivery-1", event_timestamp_ms=1, received_at_ms=2, payload_json=payload)
    duplicate = gateway.record_webhook_delivery(delivery_id="delivery-1", event_timestamp_ms=1, received_at_ms=3, payload_json=payload)
    assert first.inserted is True and duplicate.inserted is False
    card = gateway.list_cards()[0]
    assert card["revalidation_required"] is True and card["revision"] == 3
    item = gateway.claim_projection_outbox(now_ms=100_000, limit=1, lease_ms=10_000)[0]
    reclaimed = gateway.claim_projection_outbox(now_ms=110_001, limit=1, lease_ms=10_000)[0]
    assert reclaimed.id == item.id and reclaimed.attempts == item.attempts + 1
