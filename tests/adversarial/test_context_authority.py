"""META-5: executable negative tests for provenance/authority and immutability.

Covers who may assert what about a piece of context: sources cannot be
mutated after construction, sections cannot misrepresent their source's
hash/trust/sensitivity, and a manifest's redacted evidence snapshot cannot
be rewritten independently of the hash that attests it. These are all
currently-enforced invariants (see tests/fixtures/meta5/corpus.json,
suite=test_context_authority, status=enforced).
"""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from metaharness.context import (
    ContextManifest,
    ContextSourceKind,
    ContextTrust,
    Sensitivity,
    content_hash,
    fit_messages_with_receipt,
)

from tests.adversarial._meta5_support import cases_for, make_scope, make_section, make_source


@pytest.mark.parametrize(
    "case",
    cases_for("test_context_authority", "immutable_evidence_rewrites"),
    ids=lambda c: c["id"],
)
def test_immutable_evidence_rewrites_are_rejected(case):
    if case["id"] == "immutable-frozen-attribute-mutation":
        scope = make_scope()
        with pytest.raises(ValidationError):
            scope.project_id = "attacker-controlled"
        source = make_source()
        with pytest.raises(ValidationError):
            source.content_hash = "sha256:" + "9" * 64
        section = make_section()
        with pytest.raises(ValidationError):
            section.content = "tampered content"
    elif case["id"] == "immutable-envelope-content-hash-tamper":
        receipt = fit_messages_with_receipt(
            [
                {"role": "system", "content": "system contract"},
                {"role": "user", "content": "do the task"},
            ],
            budget_tokens=1_000,
            model_id="m",
            harness_version="h",
        )
        envelope_dict = json.loads(receipt.envelope.model_dump_json())
        envelope_dict["content_hash"] = "sha256:" + "0" * 64
        from metaharness.context import ContextEnvelope

        with pytest.raises(ValidationError):
            ContextEnvelope.model_validate(envelope_dict)
    elif case["id"] == "immutable-manifest-redacted-envelope-tamper":
        receipt = fit_messages_with_receipt(
            [
                {"role": "system", "content": "system contract"},
                {"role": "user", "content": "do the task"},
            ],
            budget_tokens=1_000,
            model_id="m",
            harness_version="h",
        )
        manifest_dict = json.loads(receipt.manifest.model_dump_json())
        tampered = json.loads(manifest_dict["redacted_envelope_json"])
        tampered["policy_version"] = "attacker-rewritten-policy"
        tampered["content_hash"] = content_hash(
            {key: value for key, value in tampered.items() if key != "content_hash"}
        )
        manifest_dict["redacted_envelope_json"] = json.dumps(
            tampered, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        manifest_dict["manifest_hash"] = content_hash(
            {key: value for key, value in manifest_dict.items() if key != "manifest_hash"}
        )
        with pytest.raises(ValidationError):
            ContextManifest.model_validate(manifest_dict)
    else:
        pytest.fail(f"unhandled case id {case['id']}")


def test_section_cannot_claim_a_trust_or_sensitivity_the_source_never_granted():
    verified_source = make_source(
        source_id="verified-fact",
        kind=ContextSourceKind.POPULATION_FINDING,
        trust=ContextTrust.VERIFIED_FACT,
        sensitivity=Sensitivity.RESTRICTED,
    )
    with pytest.raises(ValidationError):
        make_section(
            source=verified_source,
            trust=ContextTrust.INSTRUCTION,
            sensitivity=verified_source.sensitivity,
        )
    with pytest.raises(ValidationError):
        make_section(
            source=verified_source,
            trust=verified_source.trust,
            sensitivity=Sensitivity.PUBLIC,
        )


def test_untrusted_evaluator_receipt_source_cannot_be_laundered_as_an_instruction():
    receipt_source = make_source(
        source_id="evaluator-verdict",
        kind=ContextSourceKind.EVALUATOR_RECEIPT,
        trust=ContextTrust.UNTRUSTED_EVIDENCE,
        sensitivity=Sensitivity.INTERNAL,
    )
    with pytest.raises(ValidationError):
        make_section(
            source=receipt_source,
            trust=ContextTrust.INSTRUCTION,
            sensitivity=receipt_source.sensitivity,
        )


@pytest.mark.parametrize(
    "case",
    cases_for("test_context_authority", "premature_logging", status="enforced"),
    ids=lambda c: c["id"],
)
def test_premature_logging_entry_receipt_alignment_is_rejected(case):
    assert case["id"] == "premature-logging-entry-receipt-alignment"
    receipt = fit_messages_with_receipt(
        [
            {"role": "system", "content": "system contract"},
            {"role": "user", "content": "do the task"},
        ],
        budget_tokens=1_000,
        model_id="m",
        harness_version="h",
    )
    manifest_dict = json.loads(receipt.manifest.model_dump_json())
    assert len(manifest_dict["compression_receipts"]) >= 2
    manifest_dict["compression_receipts"] = list(reversed(manifest_dict["compression_receipts"]))
    manifest_dict["manifest_hash"] = content_hash(
        {key: value for key, value in manifest_dict.items() if key != "manifest_hash"}
    )

    with pytest.raises(ValidationError):
        ContextManifest.model_validate(manifest_dict)


def test_secret_sensitivity_source_still_obeys_fetchable_artifact_ref_pairing():
    with pytest.raises(ValidationError):
        make_source(
            source_id="secret-material",
            kind=ContextSourceKind.IMMUTABLE_ARTIFACT,
            trust=ContextTrust.UNTRUSTED_EVIDENCE,
            sensitivity=Sensitivity.SECRET,
            fetchable=True,
            artifact_ref=None,
        )
