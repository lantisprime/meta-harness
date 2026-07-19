"""Domain expertise must precede specialist self-improvement (META-12)."""
from dataclasses import fields

import pytest

from selflearn.advisor import suggest_specialist_improvement
from selflearn.contracts import (
    CandidateEntry,
    ContractError,
    EntrySource,
    Probe,
    PublishDecision,
    TaskOutcome,
)
from selflearn.learning import (
    DomainReadinessReport,
    EvaluationCriterion,
    EvaluationItemResult,
    EvaluationSplits,
    ExpertExample,
    FailureCluster,
    ImprovementPolicy,
    ImprovementTrial,
    Learner,
    assess_domain_readiness,
    evaluate_improvement_trial,
    snapshot_baseline,
)
from selflearn.specialist import SpecialistSpec, load_spec, save_spec
from selflearn.store import PackStore
from selflearn.verification.suite import ProbeResult, SuiteResult


SRC = EntrySource(
    url="https://docs.example.org/domain",
    fetched_at="2026-07-01T00:00:00Z",
    sha256="0" * 64,
    tier="official",
)


def _policy(*, criterion=None, examples=(), splits=None, **kw):
    criterion = criterion or EvaluationCriterion(
        id="label-choice",
        description="predicted label equals the expert label",
        failure_mode="wrong-label",
        check_kind="deterministic",
        probe_ids=("p-label",),
        anchors=("exact label equality",),
        approved_by="expert@example.org",
    )
    examples = examples or (
        ExpertExample(
            id="ex-order",
            criterion_id=criterion.id,
            expected="order",
            rationale="an explicit purchase request is an order",
        ),
    )
    splits = splits or EvaluationSplits(
        fit=("fit-1", "fit-2"),
        validation=("val-1",),
        test=("test-1",),
    )
    base = dict(
        domain_expert="expert@example.org",
        optimizer_identity="optimizer-agent",
        evaluator_identity="frozen-evaluator-v1",
        criteria=(criterion,),
        expert_examples=examples,
        splits=splits,
        target_validation_score=0.92,
        max_iterations=15,
        plateau_rounds=3,
    )
    base.update(kw)
    return ImprovementPolicy(**base)


def _published_store(tmp_path, *, probe=None, baseline=True):
    store = PackStore(tmp_path / "store")
    entry = CandidateEntry(
        id="entry-labels",
        pack="classification",
        kind="knowledge",
        body="Choose the most specific supported label.",
        claims=("specific labels win when supported",),
        sources=(SRC,),
        topic="label-choice",
    )
    store.add_candidate(entry)
    probe = probe or Probe(
        id="p-label",
        entry_id=entry.id,
        kind="recall",
        question="Which label applies?",
        expected="order",
        check_kind="deterministic",
        validated=True,
        validated_by="independent-validator",
    )
    store.publish(
        entry.id,
        PublishDecision(
            entry_id=entry.id,
            publish=True,
            basis=("independent verification",),
            identity_basis="validator identity",
        ),
        probes=(probe,),
    )
    if baseline:
        result = SuiteResult(
            model_id="serving-model-v1", pack="classification", injected=True,
            results=[ProbeResult(probe.id, probe.kind, True)],
        )
        snapshot_baseline(store, "classification", result)
    return store


def _result(item_id, passed, evaluator="frozen-evaluator-v1"):
    return EvaluationItemResult(
        item_id=item_id,
        passed=passed,
        evidence=f"frozen evaluator evidence for {item_id}",
        evaluator_identity=evaluator,
        failure_mode="wrong-label" if not passed else "",
    )


def _trial(dominant, *, iteration, fit, validation,
           target_cluster=None, evaluator="frozen-evaluator-v1"):
    return ImprovementTrial(
        iteration=iteration,
        target_cluster=target_cluster or dominant.id,
        evaluator_identity=evaluator,
        fit_results=tuple(_result(item_id, passed)
                          for item_id, passed in fit),
        validation_results=tuple(_result(item_id, passed)
                                 for item_id, passed in validation),
    )


def test_split_contract_rejects_leakage_and_seals_test_access():
    with pytest.raises(ContractError, match="overlap"):
        EvaluationSplits(fit=("same",), validation=("same",), test=("t",))
    splits = EvaluationSplits(fit=("f",), validation=("v",), test=("t",))
    assert splits.items_for("fit") == ("f",)
    assert splits.items_for("validation") == ("v",)
    with pytest.raises(ContractError, match="sealed"):
        splits.items_for("test")
    assert splits.items_for("final_test") == ("t",)


def test_policy_requires_external_evaluator_and_bounded_stops():
    with pytest.raises(ContractError, match="distinct"):
        _policy(optimizer_identity="same", evaluator_identity="same")
    with pytest.raises(ContractError, match="max_iterations"):
        _policy(max_iterations=0)
    with pytest.raises(ContractError, match="plateau_rounds"):
        _policy(max_iterations=2, plateau_rounds=3)


def test_missing_policy_is_explicitly_not_ready(tmp_path):
    store = _published_store(tmp_path)
    spec = SpecialistSpec(name="classifier", packs=("classification",))
    report = assess_domain_readiness(spec, store)
    assert isinstance(report, DomainReadinessReport)
    assert not report.ready
    assert report.reasons == (
        "specialist has no improvement_policy; retrieval may run but "
        "self-improvement is not ready",
    )


def test_policy_without_domain_evidence_is_not_ready(tmp_path):
    store = _published_store(tmp_path)
    policy = ImprovementPolicy(
        domain_expert="expert@example.org",
        optimizer_identity="optimizer-agent",
        evaluator_identity="frozen-evaluator-v1",
        criteria=(),
        expert_examples=(),
        splits=EvaluationSplits(
            fit=("fit-1",), validation=("val-1",), test=("test-1",),
        ),
        target_validation_score=0.9,
        max_iterations=3,
        plateau_rounds=2,
    )
    report = assess_domain_readiness(
        SpecialistSpec(
            name="classifier", packs=("classification",),
            improvement_policy=policy,
        ),
        store,
    )
    assert not report.ready
    assert "improvement policy has no evaluation criteria" in report.reasons
    assert "improvement policy has no domain-expert examples" in report.reasons


def test_readiness_rejects_weak_judge_and_missing_baseline(tmp_path):
    probe = Probe(
        id="p-label", entry_id="entry-labels", kind="application",
        question="How good is this?", expected="4", check_kind="judge",
        validated=True, validated_by="independent-validator",
    )
    store = _published_store(tmp_path, probe=probe, baseline=False)
    criterion = EvaluationCriterion(
        id="label-choice",
        description="judge quality on a five point scale",
        failure_mode="wrong-label",
        check_kind="judge",
        probe_ids=("p-label",),
        anchors=("5 means good",),
        approved_by="expert@example.org",
    )
    spec = SpecialistSpec(
        name="classifier", packs=("classification",),
        improvement_policy=_policy(criterion=criterion),
    )
    report = assess_domain_readiness(spec, store)
    assert not report.ready
    assert report.weak_criteria == ("label-choice",)
    assert any("unanchored judge" in reason for reason in report.reasons)
    assert any("no frozen suite baseline" in reason for reason in report.reasons)


def test_readiness_rejects_probe_validated_by_optimizer(tmp_path):
    probe = Probe(
        id="p-label", entry_id="entry-labels", kind="recall",
        question="Which label applies?", expected="order",
        check_kind="deterministic", validated=True,
        validated_by="optimizer-agent",
    )
    store = _published_store(tmp_path, probe=probe)
    report = assess_domain_readiness(
        SpecialistSpec(
            name="classifier", packs=("classification",),
            improvement_policy=_policy(),
        ),
        store,
    )
    assert not report.ready
    assert any("validated by the optimizer" in reason
               for reason in report.reasons)


def test_ready_specialist_has_high_signal_evidence_and_yaml_round_trip(tmp_path):
    store = _published_store(tmp_path)
    spec = SpecialistSpec(
        name="classifier",
        packs=("classification",),
        task_types=("classify",),
        improvement_policy=_policy(),
    )
    report = spec.assess_improvement(store)
    assert report.ready
    assert report.high_signal_criteria == ("label-choice",)
    assert (report.fit_items, report.validation_items, report.test_items) == (2, 1, 1)
    path = tmp_path / "classifier.yaml"
    save_spec(spec, path)
    assert load_spec(path) == spec


def test_failure_clusters_target_largest_verified_pattern(tmp_path):
    store = _published_store(tmp_path)
    learner = Learner(store)
    for i in range(3):
        learner.observe(TaskOutcome(
            task_id=f"wrong-{i}", task_type="classify", topic="label-choice",
            verdict="fail", injected=("entry-labels",),
            implicated=("entry-labels",), failure_mode="wrong-label",
        ))
    learner.observe(TaskOutcome(
        task_id="format-1", task_type="classify", topic="label-choice",
        verdict="fail", injected=("entry-labels",),
        implicated=("entry-labels",), failure_mode="bad-format",
    ))
    clusters = learner.failure_clusters("classification")
    assert [(c.failure_mode, c.count) for c in clusters] == [
        ("wrong-label", 3), ("bad-format", 1),
    ]
    assert clusters[0].id == "label-choice:wrong-label"


def test_trial_acceptance_is_validation_only_and_bounded():
    policy = _policy()
    dominant = FailureCluster(
        topic="label-choice", failure_mode="wrong-label", count=8,
        task_ids=("t1", "t2"),
    )
    # Fit improves dramatically, but validation does not: reject.
    rejected = evaluate_improvement_trial(
        policy,
        _trial(
            dominant, iteration=1,
            fit=(("fit-1", True), ("fit-2", True)),
            validation=(("val-1", False),),
        ),
        dominant_cluster=dominant,
        best_validation_results=(_result("val-1", True),),
        stagnant_rounds=0,
    )
    assert not rejected.eligible and rejected.best_validation_score == 1.0
    assert "validation" in rejected.reason

    accepted = evaluate_improvement_trial(
        policy,
        _trial(
            dominant, iteration=2,
            fit=(("fit-1", True), ("fit-2", False)),
            validation=(("val-1", True),),
        ),
        dominant_cluster=dominant,
        best_validation_results=(_result("val-1", False),),
        stagnant_rounds=1,
    )
    assert accepted.eligible and accepted.stop
    assert accepted.best_validation_score == 1.0
    assert "target" in accepted.reason
    # The candidate contract deliberately cannot carry final-test evidence.
    trial_fields = {f.name for f in fields(ImprovementTrial)}
    assert "test_score" not in trial_fields
    assert "test_results" not in trial_fields


def test_trial_rejects_wrong_cluster_and_stops_on_plateau():
    policy = _policy(plateau_rounds=2)
    dominant = FailureCluster(
        topic="label-choice", failure_mode="wrong-label", count=8,
        task_ids=("t1",),
    )
    decision = evaluate_improvement_trial(
        policy,
        _trial(
            dominant, iteration=2,
            target_cluster="other:bad-format",
            fit=(("fit-1", True), ("fit-2", True)),
            validation=(("val-1", True),),
        ),
        dominant_cluster=dominant,
        best_validation_results=(_result("val-1", False),),
        stagnant_rounds=1,
    )
    assert not decision.eligible and decision.stop
    assert "dominant failure cluster" in decision.reason
    assert "plateau" in decision.reason


def test_trial_rejects_aggregate_gain_that_masks_item_regression():
    policy = _policy(splits=EvaluationSplits(
        fit=("fit-1", "fit-2"),
        validation=("val-1", "val-2", "val-3"),
        test=("test-1",),
    ))
    dominant = FailureCluster(
        topic="label-choice", failure_mode="wrong-label", count=8,
        task_ids=("t1",),
    )
    decision = evaluate_improvement_trial(
        policy,
        _trial(
            dominant, iteration=1,
            fit=(("fit-1", True), ("fit-2", True)),
            validation=(
                ("val-1", False), ("val-2", True), ("val-3", True),
            ),
        ),
        dominant_cluster=dominant,
        best_validation_results=(
            _result("val-1", True),
            _result("val-2", False),
            _result("val-3", False),
        ),
        stagnant_rounds=0,
    )
    assert not decision.eligible
    assert "per-item validation regression" in decision.reason
    assert decision.best_validation_score == pytest.approx(1 / 3)


def test_trial_requires_frozen_evaluator_and_exact_split_items():
    policy = _policy()
    dominant = FailureCluster(
        topic="label-choice", failure_mode="wrong-label", count=2,
        task_ids=("t1",),
    )
    with pytest.raises(ContractError, match="frozen evaluator"):
        evaluate_improvement_trial(
            policy,
            _trial(
                dominant, iteration=1, evaluator="optimizer-agent",
                fit=(("fit-1", True), ("fit-2", True)),
                validation=(("val-1", True),),
            ),
            dominant_cluster=dominant,
            best_validation_results=(_result("val-1", False),),
            stagnant_rounds=0,
        )
    with pytest.raises(ContractError, match="frozen split"):
        evaluate_improvement_trial(
            policy,
            _trial(
                dominant, iteration=1,
                fit=(("fit-1", True), ("unexpected", True)),
                validation=(("val-1", True),),
            ),
            dominant_cluster=dominant,
            best_validation_results=(_result("val-1", False),),
            stagnant_rounds=0,
        )


def test_specialist_advisor_is_read_only_and_readiness_gated(tmp_path):
    store = _published_store(tmp_path)
    no_policy = SpecialistSpec(name="classifier", packs=("classification",))
    before = sorted(p.read_bytes() for p in store.root.rglob("*") if p.is_file())
    suggestion = suggest_specialist_improvement(no_policy, store)
    after = sorted(p.read_bytes() for p in store.root.rglob("*") if p.is_file())
    assert suggestion.priority == 2
    assert "not ready" in suggestion.action
    assert before == after

    ready = SpecialistSpec(
        name="classifier", packs=("classification",),
        improvement_policy=_policy(),
    )
    suggestion = suggest_specialist_improvement(ready, store)
    assert suggestion.priority == 7
    assert "bounded improvement" in suggestion.action
    assert "sealed test" in suggestion.reason
    assert suggestion.command == ""
