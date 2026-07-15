"""META-5: executable negative tests for current invalid-input handling.

Every test here exercises behavior that src/metaharness/context enforces
*today*. These are not aspirational: each corresponds to an "enforced" case
in tests/fixtures/meta5/corpus.json and must keep passing as long as the
contract holds. For genuinely absent contracts (traversal guards, envelope
scope isolation, artifact-ref packaging completeness), see
test_memory_skill_boundaries.py.
"""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from metaharness.context import (
    ContextManifest,
    ContextManifestEntry,
    ContextScope,
    ContextSourceKind,
    ContextTrust,
    Sensitivity,
    content_hash,
    fit_messages_with_receipt,
)

from tests.adversarial._meta5_support import cases_for, make_source


def test_corpus_is_well_formed_and_covers_every_required_category():
    from tests.adversarial._meta5_support import load_corpus

    corpus = load_corpus()
    required_categories = {
        "unknown_operations",
        "traversal",
        "cross_scope_access",
        "immutable_evidence_rewrites",
        "premature_logging",
        "lossy_compression",
        "activation_or_tombstoning",
        "specialist_task_actions",
        "unreceipted_mutation",
        "task_action_training_targets",
        "hw_confounding",
        "repeated_set_promotion",
        "incompatible_reuse",
        "unhealthy_fallback",
        "incomplete_packaging",
        "evaluator_non_self_approval",
    }
    assert required_categories.issubset(set(corpus["categories"]))
    seen_categories = {case["category"] for case in corpus["cases"]}
    assert required_categories.issubset(seen_categories)

    seen_ids = [case["id"] for case in corpus["cases"]]
    assert len(seen_ids) == len(set(seen_ids)), "case ids must be unique"

    requirement_ids = [case["requirement_id"] for case in corpus["cases"] if case["requirement_id"]]
    for req_id in requirement_ids:
        assert req_id.startswith("META5-MEM-"), req_id
    # requirement ids may be shared by more than one case (e.g. traversal
    # covers both source_id and artifact_ref), but every absent case must
    # name one, and there must be at least as many distinct requirements as
    # absent categories that have no enforced counterpart in this corpus.
    assert len(set(requirement_ids)) >= 15

    for case in corpus["cases"]:
        if case["status"] == "enforced":
            assert case["requirement_id"] is None, case["id"]
        if case["status"] == "absent":
            assert case["requirement_id"] is not None, case["id"]
            assert case["suite"] == "test_memory_skill_boundaries", case["id"]


@pytest.mark.parametrize("case", cases_for("test_context_invalid_inputs", "unknown_operations"), ids=lambda c: c["id"])
def test_unknown_operations_are_rejected(case):
    if case["id"] == "unknown-operation-source-extra-field":
        with pytest.raises(ValidationError):
            make_source(unknown_field="delete_everything")
    elif case["id"] == "unknown-operation-manifest-entry-surface":
        with pytest.raises(ValidationError):
            ContextManifestEntry(
                stable_id="s",
                surface="delete",
                payload_json=json.dumps({}, separators=(",", ":")),
                source_kind=ContextSourceKind.GOAL,
                trust=ContextTrust.INSTRUCTION,
                sensitivity=Sensitivity.PUBLIC,
                source_hash="sha256:" + "1" * 64,
                selected_hash=content_hash({}),
            )
    elif case["id"] == "unknown-operation-source-kind-enum":
        with pytest.raises(ValidationError):
            make_source(kind="wipe_memory")
    else:
        pytest.fail(f"unhandled case id {case['id']}")


@pytest.mark.parametrize("case", cases_for("test_context_invalid_inputs", "cross_scope_access"), ids=lambda c: c["id"])
def test_cross_scope_nesting_is_rejected(case):
    if case["id"] == "cross-scope-task-requires-run":
        with pytest.raises(ValidationError):
            ContextScope(project_id="meta-harness", task_id="task-1")
    elif case["id"] == "cross-scope-attempt-requires-task":
        with pytest.raises(ValidationError):
            ContextScope(project_id="meta-harness", run_id="run-1", attempt_id="attempt-1")
    else:
        pytest.fail(f"unhandled case id {case['id']}")


def test_scope_rejects_blank_identifiers_as_unknown_operation_surface():
    with pytest.raises(ValidationError):
        ContextScope(project_id="")
    with pytest.raises(ValidationError):
        make_source(source_id="")


def test_source_rejects_malformed_content_hash_shape():
    with pytest.raises(ValidationError):
        make_source(content_hash="not-a-sha256", high_water_mark=None)
    with pytest.raises(ValidationError):
        make_source(content_hash="sha256:" + "z" * 64)


@pytest.mark.parametrize(
    "case",
    cases_for("test_context_invalid_inputs", "incomplete_packaging"),
    ids=lambda c: c["id"],
)
def test_incomplete_packaging_manifest_alignment_is_rejected(case):
    assert case["id"] == "incomplete-packaging-manifest-alignment"
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
    assert len(manifest_dict["entries"]) == len(manifest_dict["compression_receipts"]) >= 1
    manifest_dict["compression_receipts"] = manifest_dict["compression_receipts"][:-1]
    manifest_dict["manifest_hash"] = content_hash(
        {key: value for key, value in manifest_dict.items() if key != "manifest_hash"}
    )

    with pytest.raises(ValidationError):
        ContextManifest.model_validate(manifest_dict)


def test_manifest_entry_stable_ids_must_be_unique():
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
    assert len(manifest_dict["entries"]) >= 2
    manifest_dict["entries"][1]["stable_id"] = manifest_dict["entries"][0]["stable_id"]
    manifest_dict["compression_receipts"][1]["stable_id"] = manifest_dict["compression_receipts"][0]["stable_id"]
    manifest_dict["manifest_hash"] = content_hash(
        {key: value for key, value in manifest_dict.items() if key != "manifest_hash"}
    )

    with pytest.raises(ValidationError):
        ContextManifest.model_validate(manifest_dict)
