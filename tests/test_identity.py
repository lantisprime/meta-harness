"""Identity layer tests: keys, registry ceremony, capability tokens, provenance chain.

These are real-artifact tests — they exercise actual Ed25519 signatures and real
hash chains, and assert that alterations are caught, not just that happy paths run.
"""
from __future__ import annotations

import pytest

from metaharness.identity import (
    ChainCheck,
    KeyPair,
    ProvenanceLog,
    RegistryError,
    TokenIssuer,
    WorkerRegistry,
    registration_payload,
    rotation_payload,
    scope_covers,
    validate_token,
    verify,
)


# -- keys ----------------------------------------------------------------------


def test_sign_and_verify_roundtrip():
    kp = KeyPair.generate()
    sig = kp.sign(b"hello")
    assert verify(kp.public_b64(), b"hello", sig)


def test_verify_rejects_altered_message_and_wrong_key():
    kp, other = KeyPair.generate(), KeyPair.generate()
    sig = kp.sign(b"hello")
    assert not verify(kp.public_b64(), b"hellO", sig)
    assert not verify(other.public_b64(), b"hello", sig)
    assert not verify(kp.public_b64(), b"hello", "not-base64!!")


def test_keypair_private_roundtrip():
    kp = KeyPair.generate()
    restored = KeyPair.from_private_b64(kp.private_b64())
    assert restored.public_b64() == kp.public_b64()
    assert verify(kp.public_b64(), b"x", restored.sign(b"x"))


# -- registry ------------------------------------------------------------------


def _register(registry: WorkerRegistry, worker_id: str, kp: KeyPair, **kw):
    challenge = registry.begin_registration(worker_id)
    payload = registration_payload(worker_id, kp.public_b64(), challenge.nonce)
    return registry.complete_registration(worker_id, kp.public_b64(), kp.sign(payload), **kw)


def test_registration_ceremony_admits_worker():
    registry = WorkerRegistry()
    kp = KeyPair.generate()
    record = _register(registry, "w1", kp, display_name="Worker One", tiers=["small"])
    assert record.public_key_b64 == kp.public_b64()
    assert registry.is_active("w1")
    assert registry.get("w1").display_name == "Worker One"


def test_registration_rejects_signature_from_different_key():
    registry = WorkerRegistry()
    kp, impostor = KeyPair.generate(), KeyPair.generate()
    challenge = registry.begin_registration("w1")
    payload = registration_payload("w1", kp.public_b64(), challenge.nonce)
    with pytest.raises(RegistryError, match="does not verify"):
        registry.complete_registration("w1", kp.public_b64(), impostor.sign(payload))


def test_registration_nonce_is_single_use():
    registry = WorkerRegistry()
    kp = KeyPair.generate()
    challenge = registry.begin_registration("w1")
    payload = registration_payload("w1", kp.public_b64(), challenge.nonce)
    sig = kp.sign(payload)
    registry.complete_registration("w1", kp.public_b64(), sig)
    with pytest.raises(RegistryError):
        registry.complete_registration("w1", kp.public_b64(), sig)


def test_registration_challenge_expires():
    registry = WorkerRegistry(challenge_ttl_s=10.0)
    kp = KeyPair.generate()
    challenge = registry.begin_registration("w1")
    payload = registration_payload("w1", kp.public_b64(), challenge.nonce)
    with pytest.raises(RegistryError, match="expired"):
        registry.complete_registration(
            "w1", kp.public_b64(), kp.sign(payload), now=challenge.issued_at + 11.0
        )


def test_duplicate_registration_rejected():
    registry = WorkerRegistry()
    _register(registry, "w1", KeyPair.generate())
    with pytest.raises(RegistryError, match="already registered"):
        registry.begin_registration("w1")


def test_verify_message_only_for_registered_active_worker():
    registry = WorkerRegistry()
    kp = KeyPair.generate()
    _register(registry, "w1", kp)
    msg = b"result payload"
    sig = kp.sign(msg)
    assert registry.verify_message("w1", msg, sig)
    assert not registry.verify_message("w1", msg + b"x", sig)
    assert not registry.verify_message("ghost", msg, sig)
    registry.deactivate("w1")
    assert not registry.verify_message("w1", msg, sig)
    registry.reactivate("w1")
    assert registry.verify_message("w1", msg, sig)


def test_key_rotation_requires_current_key():
    registry = WorkerRegistry()
    old, new, impostor = KeyPair.generate(), KeyPair.generate(), KeyPair.generate()
    _register(registry, "w1", old)
    payload = rotation_payload("w1", new.public_b64())
    with pytest.raises(RegistryError, match="not signed by current key"):
        registry.rotate_key("w1", new.public_b64(), impostor.sign(payload))
    record = registry.rotate_key("w1", new.public_b64(), old.sign(payload))
    assert record.key_rotations == 1
    # messages now verify under the new key only
    assert registry.verify_message("w1", b"m", new.sign(b"m"))
    assert not registry.verify_message("w1", b"m", old.sign(b"m"))


# -- tokens --------------------------------------------------------------------


def test_token_issue_and_validate():
    issuer = TokenIssuer()
    token = issuer.issue("w1", ["task:execute", "tier:small"], task_id="task_1")
    check = validate_token(
        token, issuer.public_b64(), required_scope="task:execute",
        subject="w1", task_id="task_1",
    )
    assert check.ok, check.reason


def test_token_expiry():
    issuer = TokenIssuer()
    token = issuer.issue("w1", ["task:execute"], ttl_s=60, now=1000.0)
    assert validate_token(token, issuer.public_b64(), now=1030.0).ok
    check = validate_token(token, issuer.public_b64(), now=1061.0)
    assert not check.ok and "expired" in check.reason


def test_token_tamper_detected():
    issuer = TokenIssuer()
    token = issuer.issue("w1", ["tier:small"])
    token.payload.scopes = ["tier:small", "tier:frontier"]  # escalate after issue
    check = validate_token(token, issuer.public_b64(), required_scope="tier:frontier")
    assert not check.ok and "signature" in check.reason


def test_token_from_other_issuer_rejected():
    real, other = TokenIssuer(), TokenIssuer()
    token = other.issue("w1", ["task:execute"])
    check = validate_token(token, real.public_b64())
    assert not check.ok


def test_token_scope_and_subject_and_task_binding():
    issuer = TokenIssuer()
    token = issuer.issue("w1", ["task:execute"], task_id="task_A")
    pub = issuer.public_b64()
    assert not validate_token(token, pub, required_scope="task:cancel").ok
    assert not validate_token(token, pub, subject="w2").ok
    assert not validate_token(token, pub, task_id="task_B").ok


def test_token_revocation():
    issuer = TokenIssuer()
    token = issuer.issue("w1", ["task:execute"])
    issuer.revoke(token.payload.token_id)
    check = validate_token(
        token, issuer.public_b64(), revoked={token.payload.token_id}
    )
    assert not check.ok and "revoked" in check.reason


def test_issuer_check_applies_its_private_revocation_set():
    issuer = TokenIssuer()
    token = issuer.issue("w1", ["task:execute"], task_id="task_A")
    assert issuer.check(
        token,
        required_scopes=["task:execute"],
        subject="w1",
        task_id="task_A",
    ).ok
    issuer.revoke(token.payload.token_id)
    check = issuer.check(token, required_scopes=["task:execute"])
    assert not check.ok and check.reason == "token revoked"


def test_issuer_check_requires_exact_task_binding():
    """META-18: an unbound token (task_id=None) MUST fail when the dispatch
    gate demands a specific task. The looser `validate_token` semantics
    (legacy) are not appropriate here — the executor gate needs exactness."""
    issuer = TokenIssuer()
    unbound = issuer.issue("w1", ["task:execute"])  # task_id=None
    check = issuer.check(
        unbound, required_scopes=["task:execute"], task_id="task_X",
    )
    assert not check.ok and "task" in check.reason
    # exact match is accepted
    bound = issuer.issue("w1", ["task:execute"], task_id="task_X")
    assert issuer.check(
        bound, required_scopes=["task:execute"], task_id="task_X",
    ).ok
    # different task_id rejected
    assert not issuer.check(
        bound, required_scopes=["task:execute"], task_id="task_Y",
    ).ok


def test_scope_wildcards():
    assert scope_covers("task:*", "task:execute")
    assert scope_covers("task:execute", "task:execute")
    assert not scope_covers("task:execute", "task:*")
    assert not scope_covers("tier:*", "task:execute")


# -- provenance ----------------------------------------------------------------


def _chain_with_two_workers():
    registry = WorkerRegistry()
    kp1, kp2 = KeyPair.generate(), KeyPair.generate()
    _register(registry, "w1", kp1)
    _register(registry, "w2", kp2)
    log = ProvenanceLog()
    log.append("w1", "task.assigned", {"task_id": "t1"}, keypair=kp1)
    log.append("w1", "task.completed", {"task_id": "t1", "verdict": "pass"}, keypair=kp1)
    log.append("w2", "task.assigned", {"task_id": "t2"}, keypair=kp2)
    resolve = lambda wid: (registry.get(wid).public_key_b64 if registry.get(wid) else None)
    return registry, log, resolve, (kp1, kp2)


def test_provenance_chain_verifies_intact():
    _, log, resolve, _ = _chain_with_two_workers()
    check = log.verify_chain(resolve)
    assert check.ok and check.checked == 3


def test_provenance_detects_altered_detail():
    _, log, resolve, _ = _chain_with_two_workers()
    log.entries()  # copies; mutate the real one
    log._entries[1].detail["verdict"] = "fail"
    check = log.verify_chain(resolve)
    assert not check.ok and check.problem_index == 1 and "altered" in check.reason


def test_provenance_detects_relinked_chain():
    """Re-hashing an altered entry without fixing the next link breaks the chain."""
    _, log, resolve, (kp1, _) = _chain_with_two_workers()
    entry = log._entries[1]
    entry.detail["verdict"] = "fail"
    from metaharness.identity.canonical import sha256_hex

    entry.entry_hash = sha256_hex(entry.body_bytes())
    entry.signature_b64 = kp1.sign(entry.entry_hash.encode())
    check = log.verify_chain(resolve)
    assert not check.ok and check.problem_index == 2 and "hash link" in check.reason


def test_provenance_detects_entry_signed_by_wrong_actor():
    registry, log, resolve, (_, kp2) = _chain_with_two_workers()
    # append an entry claiming to be w1 but signed with w2's key
    log.append("w1", "task.completed", {"task_id": "t9"}, keypair=kp2)
    check = log.verify_chain(resolve)
    assert not check.ok and check.problem_index == 3 and "signature" in check.reason


def test_provenance_detects_removed_entry():
    _, log, resolve, _ = _chain_with_two_workers()
    del log._entries[1]
    check = log.verify_chain(resolve)
    assert not check.ok and check.problem_index == 1


def test_provenance_unknown_actor():
    _, log, _, (kp1, _) = _chain_with_two_workers()
    log.append("ghost", "task.assigned", {}, keypair=kp1)
    check = log.verify_chain(lambda wid: None)
    assert not check.ok
    assert "unknown actor" in check.reason


def test_provenance_jsonl_roundtrip(tmp_path):
    _, log, resolve, _ = _chain_with_two_workers()
    path = tmp_path / "provenance.jsonl"
    log.to_jsonl(path)
    restored = ProvenanceLog.from_jsonl(path)
    assert len(restored) == 3
    check = restored.verify_chain(resolve)
    assert check.ok
    assert restored.head_hash() == log.head_hash()


def test_retired_worker_id_can_be_readmitted():
    """Retire -> re-add with the same id runs the full ceremony again with a
    fresh key; an ACTIVE duplicate is still rejected. Rotation count carries
    over so re-admission is visible in the audit trail."""
    from metaharness.identity.keys import KeyPair
    from metaharness.identity.registry import (
        RegistryError, WorkerRegistry, registration_payload,
    )

    registry = WorkerRegistry()

    def admit(worker_id, keypair):
        challenge = registry.begin_registration(worker_id)
        payload = registration_payload(worker_id, keypair.public_b64(), challenge.nonce)
        return registry.complete_registration(
            worker_id, keypair.public_b64(), keypair.sign(payload))

    first = admit("bot", KeyPair.generate())
    assert first.key_rotations == 0
    with pytest.raises(RegistryError, match="already registered"):
        registry.begin_registration("bot")

    registry.deactivate("bot")
    fresh_key = KeyPair.generate()
    second = admit("bot", fresh_key)
    assert second.active and second.key_rotations == 1
    assert second.public_key_b64 == fresh_key.public_b64()
    assert registry.verify_message("bot", b"hello", fresh_key.sign(b"hello"))
