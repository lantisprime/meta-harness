from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic import ValidationError

from metaharness.blueprints import ArtifactRef
from metaharness.core.types import Task, Tier, Verdict, WorkerResult
from metaharness.evals.models import (
    EvalAssertion,
    EvalCase,
    EvalCaseProposal,
    EvalPolicy,
    EvalSuiteContent,
    EvalToolBinding,
)
from metaharness.evals.store import (
    EvalSuiteAlreadyExistsError,
    EvalSuiteArchivedError,
    EvalSuiteCorruptionError,
    EvalSuiteRevisionConflictError,
    EvalSuiteStore,
    EvalSuiteStoreError,
    InvalidEvalSuiteRevisionError,
)
from metaharness.evals.verifiers import verify_output


def case(case_id: str, *, source: str = "authored") -> EvalCase:
    return EvalCase(
        id=case_id,
        name=f"Case {case_id}",
        context={"prompt": "ordinary prose with enough detail"},
        output_step="answer",
        assertion=EvalAssertion(success_check={"equals": "ok"}),
        tags=("smoke", "core"),
        source=source,
    )


def content(name: str = "Safety suite") -> EvalSuiteContent:
    return EvalSuiteContent(
        name=name,
        description="A strict suite.",
        development_cases=[case("development")],
        validation_cases=[case("validation")],
        holdout_cases=[case("sealed")],
        policy=EvalPolicy(
            tool_bindings=[
                EvalToolBinding(
                    tool="files",
                    binding="temp-workspace",
                    isolation="disposable_workspace",
                    non_production=True,
                )
            ]
        ),
    )


@pytest.mark.parametrize(
    "raw",
    [
        {},
        {"success_check": None},
        {"success_check": {"equals": 1}, "rubric": "quality"},
        {"output_schema": {"type": "string"}, "rubric": "quality"},
    ],
)
def test_assertion_requires_exactly_one_non_null_route(raw):
    with pytest.raises(ValidationError, match="exactly one"):
        EvalAssertion.model_validate(raw)


def test_assertion_rejects_value_hazards_but_accepts_each_route():
    with pytest.raises(ValidationError, match="unsafe success_check"):
        EvalAssertion(success_check={"equals": float("inf")})
    assert EvalAssertion(success_check={"equals": None}).success_check == {"equals": None}
    assert EvalAssertion(output_schema={"type": "object"}).output_schema == {
        "type": "object"
    }
    assert EvalAssertion(rubric="  Clear and correct.  ").rubric == "Clear and correct."


@pytest.mark.parametrize(
    ("check", "message"),
    [
        ({}, "nonempty"),
        ({"wat": 1}, "unknown"),
        ({"equals": 1, "contains": "1"}, "exactly one"),
        ({"one_of": []}, "nonempty list"),
        ({"one_of": "yes"}, "nonempty list"),
        ({"contains": ""}, "nonempty string"),
        ({"contains": "   "}, "nonempty string"),
        ({"contains": "x", "tol": 0.1}, "only with equals"),
        ({"equals": 1, "tol": "0.1"}, "finite nonnegative"),
        ({"equals": 1, "tol": -0.1}, "unsafe success_check"),
    ],
)
def test_success_check_is_closed_and_meaningful(check, message):
    with pytest.raises(ValidationError, match=message):
        EvalAssertion(success_check=check)


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ({"success_check": {}}, "nonempty"),
        ({"output_schema": {}}, "nonempty"),
        ({"output_schema": {"type": "not-a-json-schema-type"}}, "valid JSON Schema"),
        ({"rubric": ""}, "nonblank"),
        ({"rubric": "   "}, "nonblank"),
    ],
)
def test_assertion_routes_reject_tautologies(raw, message):
    with pytest.raises(ValidationError, match=message):
        EvalAssertion.model_validate(raw)


@pytest.mark.parametrize(
    "check",
    [{"one_of": "a"}, {"one_of": []}, {"contains": ""}, {"contains": "   "}],
)
def test_existing_verifier_never_passes_malformed_tautological_checks(check):
    task = Task(success_check=check)
    result = WorkerResult(
        task_id=task.id,
        worker_id="test",
        tier=Tier.SMALL,
        model="test",
        output="a",
        raw_text="a",
    )
    assert verify_output(task, result).verdict is Verdict.UNVERIFIED


@pytest.mark.parametrize(
    ("schema", "output"),
    [
        ({"const": "required"}, "wrong"),
        ({"type": "string", "minLength": 5}, "tiny"),
    ],
)
def test_runtime_verifier_enforces_full_draft_2020_12_constraints(schema, output):
    task = Task(output_schema=schema)
    result = WorkerResult(
        task_id=task.id,
        worker_id="test",
        tier=Tier.SMALL,
        model="test",
        output=output,
    )
    verification = verify_output(task, result)
    assert verification.verdict is Verdict.FAIL
    assert verification.scorer == "schema"


def test_case_normalizes_tags_and_rejects_unsafe_context():
    assert case("safe").tags == ("core", "smoke")
    with pytest.raises(ValidationError, match="unique"):
        EvalCase(
            id="dup", name="Dup", assertion=EvalAssertion(rubric="r"), tags=("x", "x")
        )
    with pytest.raises(ValidationError, match="binding markers"):
        EvalCase(
            id="binding",
            name="Binding",
            context={"token": {"binding": "prod-token"}},
            assertion=EvalAssertion(rubric="r"),
        )
    with pytest.raises(ValidationError, match="binding markers"):
        EvalCase(
            id="binding-extra",
            name="Binding extra",
            context={"payload": {"binding": "prod-token", "literal": "smuggled"}},
            assertion=EvalAssertion(rubric="r"),
        )
    with pytest.raises(ValidationError, match="sensitive context key"):
        EvalCase(
            id="nested-literal",
            name="Nested literal",
            context={"api_key": {"nested": "not-even-token-shaped"}},
            assertion=EvalAssertion(rubric="r"),
        )
    with pytest.raises(ValidationError, match="sensitive context key"):
        EvalCase(
            id="literal",
            name="Literal",
            context={"api_key": "do-not-store-this"},
            assertion=EvalAssertion(rubric="r"),
        )
    assert EvalCase(
        id="prose",
        name="Prose",
        context={"paragraph": "Discuss password rotation without including credentials."},
        assertion=EvalAssertion(rubric="r"),
    )


def test_assertion_secret_scan_preserves_schema_property_names_and_ordinary_prose():
    schema = EvalAssertion(
        output_schema={
            "type": "object",
            "properties": {"api_key": {"type": "string"}},
        }
    )
    assert "api_key" in schema.output_schema["properties"]
    binding_property = EvalAssertion(
        output_schema={
            "type": "object",
            "properties": {"binding": {"type": "string"}},
        }
    )
    assert "binding" in binding_property.output_schema["properties"]
    assert EvalAssertion(rubric="Discuss password rotation in ordinary prose.")
    with pytest.raises(ValidationError, match="credential material"):
        EvalAssertion(success_check={"equals": "sk-live-abcdefghijk"})
    with pytest.raises(ValidationError, match="credential material"):
        EvalAssertion(rubric="Bearer abcdefghijklmnop")
    with pytest.raises(ValidationError, match="binding markers"):
        EvalAssertion(
            output_schema={
                "type": "object",
                "metadata": {"binding": "prod", "extra": True},
            }
        )
    with pytest.raises(ValidationError, match="binding markers"):
        EvalAssertion(
            success_check={
                "equals": {"binding": "prod", "literal": "smuggled"}
            }
        )


def test_policy_is_fail_closed_and_bounded():
    policy = EvalPolicy()
    assert (policy.k, policy.judge_allowed, policy.hitl_mode) == (3, False, "block")
    assert policy.tool_bindings == []
    with pytest.raises(ValidationError):
        EvalPolicy(k=8)
    with pytest.raises(ValidationError):
        EvalPolicy(judge_allowed=True)
    with pytest.raises(ValidationError):
        EvalPolicy(max_cost_usd=float("inf"))
    with pytest.raises(ValidationError):
        EvalToolBinding(
            tool="mail", binding="mail", isolation="fixture", non_production=False
        )


def test_case_ids_must_be_unique_across_frozen_splits():
    with pytest.raises(ValidationError, match="across splits"):
        EvalSuiteContent(
            name="Duplicate", development_cases=[case("same")], holdout_cases=[case("same")]
        )


def test_store_publishes_immutable_versions_and_public_hides_holdout(tmp_path):
    store = EvalSuiteStore(tmp_path / "state")
    draft = store.create_draft("safety", content(), owner="alice", now=10.0)
    version1 = store.publish("safety", expected_revision=draft.revision, now=20.0)
    version1_path = store.versions_root / "safety" / "versions" / "1.json"
    original = version1_path.read_bytes()

    public = store.get_version("safety", 1)
    public_json = public.model_dump(mode="json")
    assert "holdout_cases" not in public_json
    assert public.holdout_count == 1
    assert len(public.holdout_digest) == 64
    assert "sealed" not in json.dumps(public_json)

    trusted = store.get_version_for_evaluation("safety", 1)
    assert trusted.holdout_cases[0].id == "sealed"
    assert b'"holdout_cases"' in original

    store.create_draft_from_version(version1.ref, owner="alice", now=30.0)
    updated = store.update_draft(
        "safety", content("Safety suite v2"), expected_revision=1, now=31.0
    )
    version2 = store.publish("safety", expected_revision=updated.revision, now=40.0)
    assert version2.version == 2
    assert version1_path.read_bytes() == original
    assert [item.version for item in store.list_versions("safety")] == [1, 2]


def test_old_base_draft_publishes_new_version_after_newer_latest(tmp_path):
    store = EvalSuiteStore(tmp_path)
    store.create_draft("suite", content("v1"), owner="a")
    v1 = store.publish("suite", expected_revision=1)
    store.create_draft_from_version(v1.ref, owner="a")
    store.update_draft("suite", content("v2"), expected_revision=1)
    store.publish("suite", expected_revision=2)
    store.create_draft("suite", content("from old v1"), owner="a", base_version=1)
    v3 = store.publish("suite", expected_revision=1)
    assert (v3.version, v3.name) == (3, "from old v1")


@pytest.mark.parametrize("bad", [True, False, "1", 1.0, 0, -1, None])
@pytest.mark.parametrize("operation", ["update", "publish"])
def test_revision_is_strict(tmp_path, bad, operation):
    store = EvalSuiteStore(tmp_path)
    store.create_draft("suite", content(), owner="a")
    with pytest.raises(InvalidEvalSuiteRevisionError):
        if operation == "update":
            store.update_draft("suite", content("changed"), expected_revision=bad)
        else:
            store.publish("suite", expected_revision=bad)


def test_revision_conflict_preserves_draft(tmp_path):
    store = EvalSuiteStore(tmp_path)
    store.create_draft("suite", content(), owner="a")
    with pytest.raises(EvalSuiteRevisionConflictError):
        store.update_draft("suite", content("changed"), expected_revision=2)
    assert store.get_draft("suite").revision == 1


def test_publish_recovers_after_catalog_write_failure(tmp_path, monkeypatch):
    store = EvalSuiteStore(tmp_path)
    store.create_draft("suite", content(), owner="a")
    real_replace = store._atomic_replace
    failed = False

    def fail_once(path, model):
        nonlocal failed
        if path == store._catalog_path("suite") and not failed:
            failed = True
            raise OSError("injected")
        return real_replace(path, model)

    monkeypatch.setattr(store, "_atomic_replace", fail_once)
    with pytest.raises(OSError, match="injected"):
        store.publish("suite", expected_revision=1, now=20.0)
    immutable = store._version_path("suite", 1).read_bytes()
    assert store.publish("suite", expected_revision=1, now=999.0).version == 1
    assert store._version_path("suite", 1).read_bytes() == immutable


def test_concurrent_publish_produces_one_version(tmp_path):
    store = EvalSuiteStore(tmp_path)
    store.create_draft("suite", content(), owner="a")

    def publish():
        try:
            return store.publish("suite", expected_revision=1).version
        except EvalSuiteStoreError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: publish(), range(2)))
    assert 1 in results
    assert [item.version for item in store.list_versions("suite")] == [1]


def test_path_traversal_symlink_and_identity_are_rejected(tmp_path):
    store = EvalSuiteStore(tmp_path / "state")
    with pytest.raises((ValidationError, ValueError)):
        store.create_draft("../escape", content(), owner="a")

    outside = tmp_path / "outside"
    outside.mkdir()
    store.root.mkdir()
    store.catalog_root.symlink_to(outside, target_is_directory=True)
    with pytest.raises(EvalSuiteStoreError, match="symlink"):
        store.create_draft("suite", content(), owner="a")

    clean = EvalSuiteStore(tmp_path / "clean")
    clean.create_draft("suite", content(), owner="a")
    path = clean.drafts_root / "suite.json"
    raw = json.loads(path.read_text())
    raw["id"] = "other"
    path.write_text(json.dumps(raw))
    with pytest.raises(EvalSuiteCorruptionError, match="identity"):
        clean.get_draft("suite")


def test_archive_restore_and_proposal_inertness(tmp_path):
    store = EvalSuiteStore(tmp_path)
    store.create_draft("suite", content(), owner="a")
    store.publish("suite", expected_revision=1)
    store.archive("suite", at=5.0)
    with pytest.raises(EvalSuiteArchivedError):
        store.create_draft_from_version(ArtifactRef(id="suite", version=1), owner="a")
    store.restore("suite")
    assert store.get_catalog_entry("suite").archived_at is None

    proposal = EvalCaseProposal(
        id="regression-one",
        blueprint_ref=ArtifactRef(id="blueprint", version=2),
        source_run_ids=["run-123"],
        cases=[case("candidate", source="production-regression")],
        redaction_report={"reviewed": False},
        created_at=10.0,
        updated_at=10.0,
    )
    store.create_proposal(proposal)
    assert store.get_proposal("regression-one").status == "proposed"
    assert store.get_version_for_evaluation("suite", 1).holdout_cases[0].id == "sealed"
    store.set_proposal_status("regression-one", "accepted", now=20.0)
    assert store.get_proposal("regression-one").status == "accepted"
    # Acceptance is review metadata only: it neither edits nor publishes a suite.
    assert [item.version for item in store.list_versions("suite")] == [1]


def test_proposal_create_is_immutable_and_identity_bound(tmp_path):
    store = EvalSuiteStore(tmp_path)
    proposal = EvalCaseProposal(
        id="candidate",
        blueprint_ref=ArtifactRef(id="blueprint", version=1),
        cases=[],
        created_at=1.0,
        updated_at=1.0,
    )
    store.create_proposal(proposal)
    with pytest.raises(EvalSuiteAlreadyExistsError):
        store.create_proposal(proposal)


def test_create_proposal_revalidates_mutated_nested_content(tmp_path):
    def proposal() -> EvalCaseProposal:
        return EvalCaseProposal(
            id="candidate",
            blueprint_ref=ArtifactRef(id="blueprint", version=1),
            source_run_ids=["run-1"],
            cases=[case("candidate")],
            created_at=1.0,
            updated_at=1.0,
        )

    leaked = proposal()
    leaked.cases[0].context["api_key"] = "sk-live-abcdefghijk"
    with pytest.raises(ValidationError):
        EvalSuiteStore(tmp_path / "leaked").create_proposal(leaked)

    duplicate = proposal()
    duplicate.cases.append(duplicate.cases[0].model_copy(deep=True))
    with pytest.raises(ValidationError, match="unique"):
        EvalSuiteStore(tmp_path / "duplicate").create_proposal(duplicate)

    bad_ref = proposal()
    bad_ref.blueprint_ref.id = "../escape"
    with pytest.raises(ValidationError):
        EvalSuiteStore(tmp_path / "bad-ref").create_proposal(bad_ref)

    redaction_leak = proposal()
    redaction_leak.redaction_report["raw"] = "gho_abcdefghijklmno"
    with pytest.raises(ValidationError, match="credential material"):
        EvalSuiteStore(tmp_path / "redaction-leak").create_proposal(redaction_leak)


def test_proposal_source_and_redaction_metadata_are_secret_scanned():
    common = {
        "id": "candidate",
        "blueprint_ref": ArtifactRef(id="blueprint", version=1),
        "cases": [],
        "created_at": 1.0,
        "updated_at": 1.0,
    }
    with pytest.raises(ValidationError, match="credential material"):
        EvalCaseProposal(**common, source_run_ids=["gho_abcdefghijklmno"])
    with pytest.raises(ValidationError, match="credential material"):
        EvalCaseProposal(**common, redaction_report={"raw": "xoxb-abcdefghijklmno"})
    with pytest.raises(ValidationError, match="sensitive context key"):
        EvalCaseProposal(
            **common,
            redaction_report={"fields": {"api_key": {"removed": True}}},
        )
    assert EvalCaseProposal(
        **common,
        source_run_ids=["run-ordinary"],
        redaction_report={"summary": "Passwords were removed during review."},
    )


@pytest.mark.parametrize("filename", ["latest.json", "01.json", "0.json", "1.txt"])
def test_list_versions_rejects_noncanonical_filenames(tmp_path, filename):
    store = EvalSuiteStore(tmp_path / filename.replace(".", "-"))
    store.create_draft("suite", content(), owner="a")
    store.publish("suite", expected_revision=1)
    directory = store.versions_root / "suite" / "versions"
    (directory / filename).write_text("{}", encoding="utf-8")
    with pytest.raises(EvalSuiteCorruptionError, match="filename"):
        store.list_versions("suite")


def test_list_versions_rejects_corrupt_canonical_json(tmp_path):
    store = EvalSuiteStore(tmp_path)
    store.create_draft("suite", content(), owner="a")
    store.publish("suite", expected_revision=1)
    (store.versions_root / "suite" / "versions" / "1.json").write_text(
        "not-json", encoding="utf-8"
    )
    with pytest.raises(EvalSuiteCorruptionError, match="invalid EvalSuiteVersion"):
        store.list_versions("suite")


def test_list_versions_cleans_only_own_atomic_temp_shape(tmp_path):
    store = EvalSuiteStore(tmp_path)
    store.create_draft("suite", content(), owner="a")
    store.publish("suite", expected_revision=1)
    directory = store.versions_root / "suite" / "versions"
    stranded = directory / ".1.json.deadbeef.tmp"
    stranded.write_text("partial", encoding="utf-8")
    assert [version.version for version in store.list_versions("suite")] == [1]
    assert not stranded.exists()

    unrecognized = directory / ".latest.json.deadbeef.tmp"
    unrecognized.write_text("partial", encoding="utf-8")
    with pytest.raises(EvalSuiteCorruptionError, match="filename"):
        store.list_versions("suite")
    assert unrecognized.exists()
