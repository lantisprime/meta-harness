from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest
from pydantic import ValidationError

from metaharness.evals import evaluator as evaluator_mod
from metaharness.blueprints import (
    ArtifactRef,
    BlueprintCatalog,
    BlueprintContent,
    BlueprintCorruptionError,
    BlueprintNotFoundError,
    BlueprintStore,
)
from metaharness.evals.artifact_store import (
    EvalArtifactAlreadyExistsError,
    EvaluationReportStore,
    TuningProposalStore,
)
from metaharness.evals.artifacts import (
    EvalAttemptResult,
    EvaluationReport,
    EvaluationReportRef,
    TuningProposal,
)
from metaharness.evals.evaluator import (
    EvaluationError,
    EvalReferenceMismatchError,
    ExactSuiteEvaluator,
    SandboxedCaseRunner,
    UnsafeEvalRunnerError,
    _SandboxStartError,
)
from metaharness.evals.models import (
    EvalAssertion,
    EvalCase,
    EvalPolicy,
    EvalSuiteContent,
    EvalToolBinding,
)
from metaharness.evals.store import EvalSuiteStore
from metaharness.evals.tuning import (
    HumanApprovalRequiredError,
    TuningReferenceMismatchError,
    UnsafeTuningPatchError,
    apply_tuning_proposal_to_draft,
    create_tuning_proposal,
)
from metaharness.evals.execution import _system_sandbox as real_system_sandbox
from metaharness.workflows.dsl import StepSpec, WorkflowSpec
from metaharness.portable.integrity import canonical_json_bytes, sha256_hex


@pytest.fixture(autouse=True)
def _stable_unit_sandbox(monkeypatch, request):
    """Keep domain tests deterministic; one named test exercises real Seatbelt."""
    if request.node.name == "test_os_sandbox_blocks_ambient_write":
        return
    monkeypatch.setattr(
        "metaharness.evals.evaluator._system_sandbox",
        lambda command, _workspace, _scratch: (list(command), "test-isolation"),
    )


_RUNNER_CODE = r"""
import json, sys
request = json.load(sys.stdin)
task = request["task"]
tools = task["tools"]
if tools:
    answer = tools[0]
else:
    answer = {
        "visible prompt": "dev-answer",
        "validation prompt": "valid-answer",
        "private holdout prompt": "holdout-answer",
    }[task["inputs"]["prompt"]]
print(json.dumps({
    "output": answer,
    "raw_text": str(answer),
    "tokens_in": 2,
    "tokens_out": 1,
    "cost_usd": 0.01,
    "latency_s": 0.1,
}))
"""


def _runner(*, sealed: bool = False, code: str = _RUNNER_CODE) -> SandboxedCaseRunner:
    return SandboxedCaseRunner(
        runner_id="fixture-runner",
        argv=(sys.executable, "-c", code),
        sealed_holdout_access=sealed,
    )


def _case(case_id: str, prompt: str, answer: str) -> EvalCase:
    return EvalCase(
        id=case_id,
        name=f"Case {case_id}",
        context={"prompt": prompt},
        assertion=EvalAssertion(success_check={"equals": answer}),
        output_step="answer",
    )


def _published_pair(tmp_path, *, with_tool: bool = False, k: int = 2):
    root = tmp_path / "state"
    eval_store = EvalSuiteStore(root)
    suite_draft = eval_store.create_draft(
        "quality",
        EvalSuiteContent(
            name="Quality",
            development_cases=[
                _case(
                    "dev",
                    "visible prompt",
                    "eval-files" if with_tool else "dev-answer",
                )
            ],
            validation_cases=[_case("validation", "validation prompt", "valid-answer")],
            holdout_cases=[_case("sealed", "private holdout prompt", "holdout-answer")],
            policy=EvalPolicy(
                k=k,
                tool_bindings=(
                    [
                        EvalToolBinding(
                            tool="files",
                            binding="eval-files",
                            non_production=True,
                            isolation="deterministic_fake",
                        )
                    ]
                    if with_tool
                    else []
                ),
            ),
        ),
        owner="tester",
        now=1.0,
    )
    suite = eval_store.publish(
        "quality", expected_revision=suite_draft.revision, now=2.0
    )
    blueprint_store = BlueprintStore(root)
    draft = blueprint_store.create_draft(
        "owned-eval",
        BlueprintContent(
            name="Owned eval",
            workflow=WorkflowSpec(
                name="owned-eval",
                steps=[
                    StepSpec(
                        id="answer",
                        objective="Answer deterministically.",
                        inputs={"prompt": "$context.prompt"},
                        tools=["files"] if with_tool else [],
                    )
                ],
            ),
            eval_suites=[suite.ref],
        ),
        owner="tester",
        now=3.0,
    )
    blueprint = blueprint_store.publish(
        "owned-eval", expected_revision=draft.revision, now=4.0
    )
    evaluator = ExactSuiteEvaluator(
        BlueprintCatalog(blueprint_store), eval_store, _runner(), _runner(sealed=True)
    )
    return root, eval_store, blueprint_store, blueprint, suite, evaluator


def _store_report(root, report):
    store = EvaluationReportStore(root)
    store.create(report)
    return store, EvaluationReportRef(
        id=report.id,
        content_digest=report.content_digest,
        split=report.split,
    )


def test_exact_eval_replay_is_deterministic_and_provenance_is_immutable(tmp_path):
    root, _, _, blueprint, suite, evaluator = _published_pair(tmp_path)
    first = evaluator.evaluate(
        report_id="report-one",
        blueprint_ref=blueprint.ref,
        eval_ref=suite.ref,
        split="development",
        created_at=10.0,
    )
    second = evaluator.evaluate(
        report_id="report-two",
        blueprint_ref=blueprint.ref,
        eval_ref=suite.ref,
        split="development",
        created_at=99.0,
    )

    assert first.content_digest == second.content_digest
    assert first.blueprint_ref == blueprint.ref
    assert first.eval_ref == suite.ref
    assert len(first.blueprint_digest) == len(first.workflow_digest) == 64
    assert first.metrics.tokens_in == 4
    assert first.metrics.tokens_out == 2
    assert first.passed == 1 and first.failed == first.unverified == 0
    assert [attempt.repetition for attempt in first.cases[0].attempts] == [1, 2]

    store = EvaluationReportStore(root)
    stored = store.create(first)
    assert store.get(first.id) == stored
    with pytest.raises(EvalArtifactAlreadyExistsError):
        store.create(first)
    report_path = root / "evaluation-reports" / "report-one.json"
    tampered = json.loads(report_path.read_text())
    tampered["blueprint_ref"]["version"] = 99
    report_path.write_text(json.dumps(tampered))
    with pytest.raises(Exception, match="invalid EvaluationReport"):
        store.get(first.id)


def test_eval_fails_closed_on_mismatched_refs_and_unisolated_tools(tmp_path):
    _, eval_store, blueprint_store, blueprint, suite, evaluator = _published_pair(tmp_path)
    with pytest.raises(EvalReferenceMismatchError, match="not frozen"):
        other_draft = eval_store.create_draft(
            "other", EvalSuiteContent(name="Other"), owner="tester"
        )
        other = eval_store.publish("other", expected_revision=other_draft.revision)
        evaluator.evaluate(
            report_id="wrong-ref",
            blueprint_ref=blueprint.ref,
            eval_ref=other.ref,
            split="validation",
        )

    _, corrupt_evals, corrupt_blueprints, corrupt_bp, corrupt_suite, _ = (
        _published_pair(tmp_path / "corrupt-case")
    )
    blueprint_path = (
        corrupt_blueprints.versions_root
        / corrupt_bp.id
        / "versions"
        / f"{corrupt_bp.version}.json"
    )
    raw_blueprint = json.loads(blueprint_path.read_text())
    raw_blueprint["id"] = "wrong-id"
    blueprint_path.write_text(json.dumps(raw_blueprint))
    with pytest.raises(BlueprintCorruptionError, match="identity mismatch"):
        ExactSuiteEvaluator(
            BlueprintCatalog(corrupt_blueprints), corrupt_evals, _runner()
        ).evaluate(
            report_id="corrupt-ref", blueprint_ref=corrupt_bp.ref,
            eval_ref=corrupt_suite.ref, split="development",
        )

    _, eval_store2, blueprint_store2, blueprint2, suite2, _ = _published_pair(
        tmp_path / "tool-case", with_tool=True
    )
    trusted_suite = eval_store2.get_version_for_evaluation(suite2.id, suite2.version)
    trusted_suite.policy.tool_bindings = []
    # Persisted exact suite remains authoritative; removing its binding on disk
    # proves the evaluator refuses the now-unisolated workflow.
    suite_path = eval_store2.versions_root / suite2.id / "versions" / f"{suite2.version}.json"
    suite_path.write_text(trusted_suite.model_dump_json())
    with pytest.raises(UnsafeEvalRunnerError, match="lack declared isolated"):
        ExactSuiteEvaluator(
            BlueprintCatalog(blueprint_store2), eval_store2, _runner()
        ).evaluate(
            report_id="unbound", blueprint_ref=blueprint2.ref,
            eval_ref=suite2.ref, split="development",
        )


def test_os_sandbox_blocks_ambient_write(tmp_path):
    if evaluator_mod._system_sandbox is not real_system_sandbox:
        pytest.skip("real operating-system sandbox wrapper is not active")
    with tempfile.TemporaryDirectory(prefix="metaharness-sandbox-probe-") as root:
        workspace = Path(root) / "workspace"
        scratch = Path(root) / "scratch"
        workspace.mkdir()
        scratch.mkdir()
        wrapped = real_system_sandbox(
            (sys.executable, "-c", "print('sandbox probe')"),
            workspace,
            scratch,
        )
    if wrapped is None:
        pytest.skip("no supported operating-system sandbox wrapper is available")
    _argv, backend = wrapped
    if backend not in {"seatbelt", "bubblewrap"}:
        pytest.skip("real operating-system sandbox wrapper is not active")

    outside = tmp_path / "ambient-write.txt"
    _, eval_store, blueprint_store, blueprint, suite, _ = _published_pair(
        tmp_path / "sandbox", k=1
    )
    probe_code = f'''import json, pathlib, sys
request = json.load(sys.stdin)
try:
    pathlib.Path({str(outside)!r}).write_text("escaped")
except OSError:
    pass
print(json.dumps({{"output":"dev-answer"}}))
'''
    try:
        report = ExactSuiteEvaluator(
            BlueprintCatalog(blueprint_store), eval_store, _runner(code=probe_code)
        ).evaluate(
            report_id="sandbox-probe", blueprint_ref=blueprint.ref,
            eval_ref=suite.ref, split="development", created_at=1.0,
        )
    except (_SandboxStartError, UnsafeEvalRunnerError) as exc:
        pytest.skip(f"operating-system sandbox unavailable in test host: {exc}")
    assert report.passed == 1
    assert not outside.exists()


def test_runner_is_data_only_task_scoped_assertion_free_and_uses_eval_tool_ids(tmp_path):
    _, eval_store, blueprint_store, blueprint, suite, _ = _published_pair(
        tmp_path / "callback"
    )

    callback_write = tmp_path / "callback-write.txt"

    class SelfAttestedCallback:
        runner_id = "self-attested"

        def run(self, **_kwargs):
            callback_write.write_text("escaped")
            return {"output": "dev-answer"}

    with pytest.raises(UnsafeEvalRunnerError, match="data-only sandbox descriptor"):
        ExactSuiteEvaluator(
            BlueprintCatalog(blueprint_store), eval_store, SelfAttestedCallback()
        ).evaluate(
            report_id="callback-probe", blueprint_ref=blueprint.ref,
            eval_ref=suite.ref, split="development",
        )
    assert not callback_write.exists()

    _, tool_evals, tool_blueprints, tool_bp, tool_suite, tool_evaluator = (
        _published_pair(tmp_path / "tool-substitution", with_tool=True)
    )
    tool_report = tool_evaluator.evaluate(
        report_id="tool-probe", blueprint_ref=tool_bp.ref,
        eval_ref=tool_suite.ref, split="development", created_at=2.0,
    )
    assert tool_report.passed == 1
    assert tool_report.cases[0].attempts[0].output == "eval-files"
    assert tool_report.cases[0].attempts[0].output != "files"

    cheating_code = '''import json, sys
request = json.load(sys.stdin)
answer = request["assertion"]["success_check"]["equals"]
print(json.dumps({"output": answer}))
'''
    cheating_report = ExactSuiteEvaluator(
        BlueprintCatalog(tool_blueprints), tool_evals,
        _runner(code=cheating_code),
    ).evaluate(
        report_id="cheating-runner", blueprint_ref=tool_bp.ref,
        eval_ref=tool_suite.ref, split="development",
    )
    assert cheating_report.failed == 1
    assert cheating_report.passed == 0


def test_eval_runs_real_workflow_spine_and_uses_only_declared_output_step(tmp_path):
    root = tmp_path / "spine"
    eval_store = EvalSuiteStore(root)
    suite_draft = eval_store.create_draft(
        "spine-suite",
        EvalSuiteContent(
            name="Spine suite",
            development_cases=[EvalCase(
                id="spine-case",
                name="Spine case",
                context={"prompt": "visible prompt"},
                assertion=EvalAssertion(success_check={"equals": "final:seed"}),
                output_step="finish",
            )],
            validation_cases=[],
            holdout_cases=[],
            policy=EvalPolicy(k=1),
        ),
        owner="tester",
        now=1.0,
    )
    suite = eval_store.publish(
        "spine-suite", expected_revision=suite_draft.revision, now=2.0
    )
    blueprint_store = BlueprintStore(root)
    draft = blueprint_store.create_draft(
        "spine-blueprint",
        BlueprintContent(
            name="Spine blueprint",
            workflow=WorkflowSpec(name="spine", steps=[
                StepSpec(
                    id="seed", objective="seed",
                    inputs={"prompt": "$context.prompt"},
                ),
                StepSpec(
                    id="finish", objective="finish", depends_on=["seed"],
                    inputs={"upstream": "$steps.seed.output"},
                ),
            ]),
            eval_suites=[suite.ref],
        ),
        owner="tester",
        now=3.0,
    )
    blueprint = blueprint_store.publish(
        "spine-blueprint", expected_revision=draft.revision, now=4.0
    )
    code = r'''import json, sys
request = json.load(sys.stdin)
assert set(request) == {"schema_version", "blueprint_ref", "task", "case_id", "split", "repetition"}
task = request["task"]
if task["objective"] == "seed":
    output = "seed"
else:
    output = "final:" + task["inputs"]["upstream"]
print(json.dumps({"output": output, "raw_text": output}))
'''
    report = ExactSuiteEvaluator(
        BlueprintCatalog(blueprint_store), eval_store, _runner(code=code)
    ).evaluate(
        report_id="spine-report", blueprint_ref=blueprint.ref,
        eval_ref=suite.ref, split="development", created_at=5.0,
    )

    assert report.passed == 1
    assert report.cases[0].attempts[0].output == "final:seed"


def test_eval_rejects_missing_or_unknown_output_step(tmp_path):
    _, eval_store, blueprint_store, blueprint, suite, _ = _published_pair(tmp_path)
    stored = eval_store.get_version_for_evaluation(suite.id, suite.version)
    stored.development_cases[0].output_step = "missing"
    path = eval_store.versions_root / suite.id / "versions" / f"{suite.version}.json"
    path.write_text(stored.model_dump_json())

    with pytest.raises(EvaluationError, match="output_step.*not in workflow"):
        ExactSuiteEvaluator(
            BlueprintCatalog(blueprint_store), eval_store, _runner()
        ).evaluate(
            report_id="bad-output-step", blueprint_ref=blueprint.ref,
            eval_ref=suite.ref, split="development",
        )


def test_holdout_report_never_discloses_inputs_expected_outputs_or_details(tmp_path):
    root, eval_store, blueprint_store, blueprint, suite, evaluator = _published_pair(tmp_path)
    public_only = ExactSuiteEvaluator(
        BlueprintCatalog(blueprint_store), eval_store, _runner()
    )
    with pytest.raises(EvaluationError, match="cannot access sealed holdout"):
        public_only.evaluate(
            report_id="public-holdout", blueprint_ref=blueprint.ref,
            eval_ref=suite.ref, split="holdout",  # type: ignore[arg-type]
        )
    with pytest.raises(UnsafeEvalRunnerError, match="separately wired"):
        public_only.evaluate_sealed_holdout(
            report_id="missing-sealed-runner", blueprint_ref=blueprint.ref,
            eval_ref=suite.ref,
        )
    report = evaluator.evaluate_sealed_holdout(
        report_id="holdout-report",
        blueprint_ref=blueprint.ref,
        eval_ref=suite.ref,
        created_at=10.0,
    )
    serialized = report.model_dump_json()
    assert "private holdout prompt" not in serialized
    assert "holdout-answer" not in serialized
    assert '"assertion":null' in serialized
    assert '"assertion_digest":null' in serialized
    assert all(attempt.output is None and not attempt.detail for attempt in report.cases[0].attempts)
    EvaluationReportStore(root).create(report)
    persisted = (root / "evaluation-reports" / "holdout-report.json").read_text()
    assert "private holdout prompt" not in persisted
    assert "holdout-answer" not in persisted


def test_eval_report_rejects_secret_material_from_runner(tmp_path):
    _, eval_store, blueprint_store, blueprint, suite, _ = _published_pair(tmp_path)

    evaluator = ExactSuiteEvaluator(
        BlueprintCatalog(blueprint_store),
        eval_store,
        _runner(
            code='import json; value="sk-"+"live-abcdefghijk"; '
            'print(json.dumps({"output":value}))'
        ),
    )
    with pytest.raises(ValidationError, match="credential material"):
        evaluator.evaluate(
            report_id="secret-output", blueprint_ref=blueprint.ref,
            eval_ref=suite.ref, split="development",
        )


def test_persistent_attempt_fields_reject_nested_secret_wrappers_but_allow_prose():
    common = {"repetition": 1, "verdict": "pass", "scorer": "deterministic"}
    with pytest.raises(ValidationError, match="sensitive context key"):
        EvalAttemptResult(
            **common,
            output={"result": {"api_key": {"value": "not-even-token-shaped"}}},
        )
    with pytest.raises(ValidationError, match="sensitive context key"):
        EvalAttemptResult(
            **common,
            output={"authorization": {"scheme": "Bearer", "value": "wrapped"}},
        )
    with pytest.raises(ValidationError, match="binding markers"):
        EvalAttemptResult(
            **common,
            output={"credential": {"binding": "prod-token", "extra": "smuggled"}},
        )
    ordinary = EvalAttemptResult(
        **common,
        detail="Discuss password rotation without including credentials.",
        output={"summary": "OAuth tokens should be rotated regularly."},
    )
    assert ordinary.output["summary"].startswith("OAuth")


def test_tuning_proposal_is_inert_and_rejects_holdout_evidence(tmp_path):
    root, eval_store, blueprint_store, blueprint, suite, evaluator = _published_pair(tmp_path)
    visible = evaluator.evaluate(
        report_id="validation-report", blueprint_ref=blueprint.ref,
        eval_ref=suite.ref, split="validation", created_at=10.0,
    )
    holdout = evaluator.evaluate_sealed_holdout(
        report_id="sealed-report", blueprint_ref=blueprint.ref,
        eval_ref=suite.ref, created_at=11.0,
    )
    report_store, visible_ref = _store_report(root, visible)
    report_store.create(holdout)
    catalog = BlueprintCatalog(blueprint_store)
    proposal = create_tuning_proposal(
        proposal_id="proposal-one",
        blueprint_ref=blueprint.ref,
        eval_refs=blueprint.eval_suites,
        catalog=catalog,
        eval_store=eval_store,
        report_store=report_store,
        report_refs=[visible_ref],
        patches=[
            {"op": "set_step_objective", "step_id": "answer", "value": "Answer exactly."}
        ],
        rationale="Validation showed the delegation contract can be clearer.",
        created_at=12.0,
    )
    store = TuningProposalStore(root)
    store.create(proposal)
    assert store.get(proposal.id) == proposal
    assert not hasattr(store, "publish")
    assert not hasattr(store, "activate")
    forged_raw = visible.model_dump(mode="json")
    forged_raw["runner_id"] = "forged-runner"
    forged_payload = {
        key: value
        for key, value in forged_raw.items()
        if key not in {"id", "created_at", "content_digest"}
    }
    forged_raw["content_digest"] = sha256_hex(
        canonical_json_bytes(forged_payload)
    )
    forged_same_id = EvaluationReport.model_validate(forged_raw)
    assert forged_same_id.id == visible.id
    with pytest.raises(TuningReferenceMismatchError, match="stored content"):
        create_tuning_proposal(
            proposal_id="forged-evidence", blueprint_ref=blueprint.ref,
            eval_refs=blueprint.eval_suites, catalog=catalog,
            eval_store=eval_store, report_store=report_store,
            report_refs=[
                EvaluationReportRef(
                    id=forged_same_id.id,
                    content_digest=forged_same_id.content_digest,
                    split=forged_same_id.split,
                )
            ],
            patches=[{"op": "set_description", "value": "forged"}],
            rationale="Must resolve immutable evidence.",
        )
    with pytest.raises(ValidationError):
        EvaluationReportRef(
            id=holdout.id,
            content_digest=holdout.content_digest,
            split=holdout.split,
        )


def test_safe_patch_vocabulary_and_human_publication_gate(tmp_path):
    root, eval_store, blueprint_store, blueprint, suite, evaluator = _published_pair(tmp_path)
    report = evaluator.evaluate(
        report_id="dev-report", blueprint_ref=blueprint.ref,
        eval_ref=suite.ref, split="development", created_at=10.0,
    )
    report_store, report_ref = _store_report(root, report)
    catalog = BlueprintCatalog(blueprint_store)
    unknown_target = create_tuning_proposal(
        proposal_id="unknown-target", blueprint_ref=blueprint.ref,
        eval_refs=blueprint.eval_suites, catalog=catalog,
        eval_store=eval_store, report_store=report_store,
        report_refs=[report_ref],
        patches=[
            {"op": "set_step_objective", "step_id": "missing", "value": "No."}
        ],
        rationale="This proposal must fail before mutation.",
    )
    with pytest.raises(UnsafeTuningPatchError, match="unknown step"):
        apply_tuning_proposal_to_draft(
            unknown_target, catalog=catalog, owner="tester",
            base_version=blueprint.version, expected_revision=None,
            human_approved=True,
        )
    with pytest.raises(BlueprintNotFoundError):
        blueprint_store.get_draft(blueprint.id)
    with pytest.raises(ValidationError, match="union_tag_invalid"):
        create_tuning_proposal(
            proposal_id="unsafe-patch", blueprint_ref=blueprint.ref,
            eval_refs=blueprint.eval_suites, catalog=catalog,
            eval_store=eval_store, report_store=report_store,
            report_refs=[report_ref],
            patches=[{"op": "set_step_tools", "step_id": "answer", "value": ["smtp"]}],
            rationale="Escalate privileges.",
        )
    proposal = create_tuning_proposal(
        proposal_id="safe-patch", blueprint_ref=blueprint.ref,
        eval_refs=blueprint.eval_suites, catalog=catalog,
        eval_store=eval_store, report_store=report_store,
        report_refs=[report_ref],
        patches=[
            {"op": "set_description", "value": "Tuned but still unpublished."},
            {"op": "set_step_max_attempts", "step_id": "answer", "value": 2},
        ],
        rationale="Use the visible development result only.",
        created_at=11.0,
    )
    with pytest.raises(HumanApprovalRequiredError):
        apply_tuning_proposal_to_draft(
            proposal, catalog=catalog, owner="tester",
            base_version=blueprint.version, expected_revision=None,
            human_approved=False,
        )
    draft = apply_tuning_proposal_to_draft(
        proposal, catalog=catalog, owner="tester",
        base_version=blueprint.version, expected_revision=None,
        human_approved=True, now=20.0,
    )
    assert draft.base_version == blueprint.version
    assert draft.eval_suites == blueprint.eval_suites
    assert draft.description == "Tuned but still unpublished."
    assert draft.workflow.step("answer").max_attempts == 2
    # Applying creates a draft only. Existing immutable versions and catalog
    # pointers remain unchanged until the separate human publish action.
    assert blueprint_store.get_catalog_entry(blueprint.id).latest_version == 1
    assert blueprint_store.list_versions(blueprint.id) == [blueprint]
    assert blueprint_store.get_draft(blueprint.id) == draft
    followup = create_tuning_proposal(
        proposal_id="safe-followup", blueprint_ref=blueprint.ref,
        eval_refs=blueprint.eval_suites, catalog=catalog,
        eval_store=eval_store, report_store=report_store,
        report_refs=[report_ref],
        patches=[
            {"op": "set_step_objective", "step_id": "answer", "value": "Answer precisely."}
        ],
        rationale="Refine the same exact-base draft.", created_at=21.0,
    )
    updated = apply_tuning_proposal_to_draft(
        followup, catalog=catalog, owner="ignored-for-existing",
        base_version=blueprint.version, expected_revision=draft.revision,
        human_approved=True, now=22.0,
    )
    assert updated.revision == draft.revision + 1
    assert updated.workflow.step("answer").objective == "Answer precisely."
    assert updated.eval_suites == blueprint.eval_suites
    assert blueprint_store.get_catalog_entry(blueprint.id).latest_version == 1
    before_failed_update = blueprint_store.get_draft(blueprint.id)
    with pytest.raises(UnsafeTuningPatchError, match="unknown step"):
        apply_tuning_proposal_to_draft(
            unknown_target, catalog=catalog, owner="tester",
            base_version=blueprint.version,
            expected_revision=before_failed_update.revision,
            human_approved=True,
        )
    assert blueprint_store.get_draft(blueprint.id) == before_failed_update
    with pytest.raises(TuningReferenceMismatchError, match="base_version"):
        apply_tuning_proposal_to_draft(
            proposal, catalog=catalog, owner="tester", base_version=99,
            expected_revision=updated.revision, human_approved=True,
        )


def test_proposal_model_rejects_digest_tampering(tmp_path):
    root, eval_store, blueprint_store, blueprint, suite, evaluator = _published_pair(tmp_path)
    report = evaluator.evaluate(
        report_id="digest-report", blueprint_ref=blueprint.ref,
        eval_ref=suite.ref, split="development", created_at=1.0,
    )
    report_store, report_ref = _store_report(root, report)
    proposal = create_tuning_proposal(
        proposal_id="digest-proposal", blueprint_ref=blueprint.ref,
        eval_refs=blueprint.eval_suites, catalog=BlueprintCatalog(blueprint_store),
        eval_store=eval_store, report_store=report_store,
        report_refs=[report_ref],
        patches=[{"op": "set_description", "value": "Safe."}],
        rationale="Safe proposal.", created_at=2.0,
    )
    raw = proposal.model_dump(mode="python")
    raw["rationale"] = "Changed after digest."
    with pytest.raises(ValidationError, match="digest mismatch"):
        TuningProposal.model_validate(raw)
