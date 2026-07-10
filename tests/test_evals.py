"""Eval gate tests: pass^k vs pass@1, suite running, paired go/no-go gating,
sign test math, capability matrix side effects."""
from __future__ import annotations

import pytest

from metaharness.core.types import Task, TaskType, Tier
from metaharness.evals import compare_suites, run_suite, sign_test_p
from metaharness.harness import MockLLMWorker
from metaharness.routing import CapabilityMatrix


def suite_tasks(n_per_type: int = 4) -> list[Task]:
    tasks = []
    for i in range(n_per_type):
        tasks.append(Task(
            task_type=TaskType.CLASSIFY,
            objective=f"classify #{i}",
            inputs={"labels": ["a", "b"]},
            success_check={"equals": "a"},
        ))
        tasks.append(Task(
            task_type=TaskType.ARITHMETIC,
            objective=f"compute #{i}",
            inputs={"expression": f"{i}+{i}"},
            success_check={"equals": 2 * i},
        ))
    return tasks


async def test_run_suite_pass_hat_k_stricter_than_pass_at_1():
    flaky = MockLLMWorker("w", Tier.MID, seed=13)  # mid: classify .95, arithmetic .75
    suite = await run_suite(flaky, suite_tasks(6), k=4)
    by_type = suite.by_type()
    arith = by_type["arithmetic"]
    assert arith.pass_hat_k <= arith.pass_at_1
    assert 0 < suite.overall_pass_hat_k() < 1


async def test_run_suite_rejects_unscoreable_tasks():
    with pytest.raises(ValueError, match="no checkable signal"):
        await run_suite(MockLLMWorker("w", Tier.MID), [Task(objective="vibes")], k=2)


async def test_run_suite_feeds_capability_matrix():
    matrix = CapabilityMatrix()
    worker = MockLLMWorker("w", Tier.SMALL, seed=3)
    await run_suite(worker, suite_tasks(2), k=3, matrix=matrix)
    assert matrix.samples("mock-small", TaskType.CLASSIFY) == 6  # 2 tasks x k=3


def test_sign_test_math():
    assert sign_test_p(0, 0) == 1.0
    assert sign_test_p(5, 5) == pytest.approx(1.0, abs=0.3)
    assert sign_test_p(10, 0) < 0.01
    assert sign_test_p(9, 1) < 0.05
    assert sign_test_p(6, 4) > 0.05


async def test_gate_go_for_better_candidate():
    tasks = suite_tasks(5)
    incumbent = await run_suite(MockLLMWorker("inc", Tier.SMALL, model="incumbent-s", seed=1), tasks, k=3)
    candidate = await run_suite(MockLLMWorker("cand", Tier.FRONTIER, model="candidate-f", seed=2), tasks, k=3)
    report = compare_suites(incumbent, candidate)
    assert report.go, report.reasons
    assert report.overall_candidate >= report.overall_incumbent


async def test_gate_no_go_for_worse_candidate():
    tasks = suite_tasks(6)
    incumbent = await run_suite(MockLLMWorker("inc", Tier.FRONTIER, model="incumbent-f", seed=1), tasks, k=3)
    candidate = await run_suite(MockLLMWorker("cand", Tier.SMALL, model="candidate-s", seed=2), tasks, k=3)
    report = compare_suites(incumbent, candidate)
    assert not report.go
    assert report.reasons and any("regressed" in r for r in report.reasons)


async def test_gate_no_go_for_per_type_regression_despite_overall_win():
    """Candidate is great at classify but collapses on arithmetic → no-go."""
    tasks = suite_tasks(6)
    incumbent = await run_suite(
        MockLLMWorker("inc", Tier.MID, model="incumbent", seed=1), tasks, k=3
    )
    lopsided = MockLLMWorker(
        "cand", Tier.MID, model="candidate", seed=2,
        skills={TaskType.CLASSIFY: 1.0, TaskType.ARITHMETIC: 0.05},
    )
    candidate = await run_suite(lopsided, tasks, k=3)
    report = compare_suites(incumbent, candidate)
    assert not report.go
    assert any("arithmetic" in r for r in report.reasons)


async def test_gate_requires_paired_design():
    t1, t2 = suite_tasks(2), suite_tasks(2)  # different task ids
    incumbent = await run_suite(MockLLMWorker("a", Tier.MID, seed=1), t1, k=2)
    candidate = await run_suite(MockLLMWorker("b", Tier.MID, seed=2), t2, k=2)
    with pytest.raises(ValueError, match="identical task list"):
        compare_suites(incumbent, candidate)


async def test_gate_flags_thin_coverage():
    tasks = suite_tasks(2)  # only 2 per type < min 3
    incumbent = await run_suite(MockLLMWorker("a", Tier.FRONTIER, model="m1", seed=1), tasks, k=2)
    candidate = await run_suite(MockLLMWorker("b", Tier.FRONTIER, model="m2", seed=2), tasks, k=2)
    report = compare_suites(incumbent, candidate)
    assert any("too thin" in r for r in report.reasons)


def test_values_equal_coerces_llm_text_answers():
    """Regression (run_231b92100340, 2026-07-08): real workers answer in text;
    '3767' must satisfy an expected 3767 — all three tiers 'failed' arithmetic
    over a type mismatch."""
    from metaharness.evals.verifiers import _values_equal

    assert _values_equal("3767", 3767)
    assert _values_equal(" 3767 ", 3767)
    assert _values_equal("3767.", 3767)      # trailing sentence period
    assert _values_equal("3.5", 3.5)
    assert not _values_equal("3768", 3767)
    assert not _values_equal("about 3767", 3767)
    assert not _values_equal("true", True)   # bool guard stays strict


def test_verify_output_unscoreable_ground_truth_is_unverified():
    """Issue #9 (codex P1 / GLM / kimi): the verifier is the real boundary. A
    ground-truth value that overflows float() (a 401-digit equals/one_of member)
    or a tol above MAX_TOL used to crash math.isclose / silently corrupt scoring;
    it now returns UNVERIFIED (least-bad — never a fake PASS, never false-blame).
    A huge MODEL OUTPUT against a normal check is the got-side guard: non-match
    FAIL, never a crash."""
    from metaharness.core.types import Verdict, WorkerResult
    from metaharness.evals.verifiers import verify_output

    def _result(task, output):
        return WorkerResult(task_id=task.id, worker_id="w", tier=Tier.SMALL,
                            model="m", output=output, raw_text=str(output))

    def _task(success_check):
        return Task(task_type=TaskType.ARITHMETIC, objective="x",
                    inputs={"expression": "x"}, success_check=success_check)

    bigint = _task({"equals": 10 ** 400})           # was OverflowError crash
    assert verify_output(bigint, _result(bigint, "1")).verdict == Verdict.UNVERIFIED
    bigtol = _task({"equals": 5, "tol": 10 ** 400})
    assert verify_output(bigtol, _result(bigtol, "5")).verdict == Verdict.UNVERIFIED
    bigmember = _task({"one_of": [10 ** 400]})
    assert verify_output(bigmember, _result(bigmember, "1")).verdict == Verdict.UNVERIFIED
    # got-side guard: a 401-digit model OUTPUT vs a normal check is a non-match, not a crash
    normal = _task({"equals": 5})
    assert verify_output(normal, _result(normal, "1" + "0" * 400)).verdict == Verdict.FAIL
    # tol=0 means EXACT match — the scoreable_tol guard must NOT widen it to the 1e-9
    # default (a value inside 1e-9 but not exact must still FAIL).
    exact = _task({"equals": 5, "tol": 0})
    assert verify_output(exact, _result(exact, "5")).verdict == Verdict.PASS
    assert verify_output(exact, _result(exact, "5.0000000004")).verdict == Verdict.FAIL
    # Issue #9 panel (opus/codex/kimi/GLM): non-finite float ground truth. inf equals used to
    # PASS any "inf" output (silent corruption); nan equals FAILed every output (false blame).
    inf_eq = _task({"equals": float("inf")})
    assert verify_output(inf_eq, _result(inf_eq, "inf")).verdict == Verdict.UNVERIFIED
    nan_eq = _task({"equals": float("nan")})
    assert verify_output(nan_eq, _result(nan_eq, "0")).verdict == Verdict.UNVERIFIED
    inf_member = _task({"one_of": [float("inf"), 1]})
    assert verify_output(inf_member, _result(inf_member, "inf")).verdict == Verdict.UNVERIFIED
    # tol cap boundary AT THE VERIFIER (both gates share scoreable_tol): 1.0 scores, 1.5 → UNVERIFIED.
    assert verify_output(_task({"equals": 5, "tol": 1.0}),
                         _result(_task({"equals": 5}), "5")).verdict == Verdict.PASS
    over = _task({"equals": 5, "tol": 1.5})
    assert verify_output(over, _result(over, "5")).verdict == Verdict.UNVERIFIED
    # got-side: a huge-int model OUTPUT OBJECT (not a string) vs a normal check exercises the
    # math.isclose OverflowError guard → non-match FAIL, never a crash.
    normal2 = _task({"equals": 5})
    assert verify_output(normal2, _result(normal2, 10 ** 400)).verdict == Verdict.FAIL


# -- Issue #10: check_value_problems (source-side intake-boundary gate) -----------


def test_check_value_problems_empty_for_benign_checks():
    from metaharness.evals.verifiers import check_value_problems

    assert check_value_problems(None) == []
    assert check_value_problems({}) == []
    assert check_value_problems({"contains": "x"}) == []
    assert check_value_problems({"equals": 5}) == []
    assert check_value_problems({"equals": 5, "tol": 0.5}) == []
    assert check_value_problems({"equals": 5, "tol": 1.0}) == []  # cap boundary, inclusive
    assert check_value_problems({"one_of": ["low", "high"]}) == []
    assert check_value_problems({"one_of": [1, 2.0]}) == []
    # strings are benign here (verify_output already treats them as non-numeric,
    # per Issue #9) even when they spell a hazard as text
    assert check_value_problems({"equals": "inf"}) == []
    assert check_value_problems({"equals": "1e999"}) == []
    # bool guard: True/False are not treated as numeric
    assert check_value_problems({"equals": True}) == []
    assert check_value_problems({"one_of": [True, False]}) == []


def test_check_value_problems_total_over_non_dict_checks():
    """Issue-#10 panel P1 (codex): the planner calls this on RAW LLM output
    before model_validate — a non-dict success_check must return [] (shape is
    model_validate's job downstream), never raise AttributeError, or the
    planner's fallback contract regresses into a 500."""
    from metaharness.evals.verifiers import check_value_problems

    assert check_value_problems("oops") == []
    assert check_value_problems(["equals", 1]) == []
    assert check_value_problems(42) == []
    assert check_value_problems(True) == []


def test_check_value_problems_names_value_hazards():
    from metaharness.evals.verifiers import check_value_problems

    equals_problems = check_value_problems({"equals": float("inf")})
    assert len(equals_problems) == 1 and "equals" in equals_problems[0]

    tol_problems = check_value_problems({"equals": 5, "tol": 1e308})
    assert len(tol_problems) == 1 and "tol" in tol_problems[0]

    one_of_problems = check_value_problems({"one_of": [1, float("nan")]})
    assert len(one_of_problems) == 1 and "one_of" in one_of_problems[0]

    # a huge-int equals overflows float() the same as a non-finite float
    bigint_problems = check_value_problems({"equals": 10 ** 400})
    assert len(bigint_problems) == 1 and "equals" in bigint_problems[0]

    # both hazards named when both are present
    both = check_value_problems({"equals": 10 ** 400, "tol": -1})
    assert len(both) == 2


def test_workflow_spec_still_accepts_hazardous_historical_checks():
    """Issue #10 compat (codex P1 on plan review): the value gate lives at
    check_value_problems / the API intake boundary and the planner, NOT in
    dsl.py — a model-level validator would make historical journals unreadable
    and break replay (harvest.py:183, workflows/engine.py:253 both re-validate
    persisted specs via WorkflowSpec.model_validate). model_validate must keep
    accepting a hazardous check completely unchanged."""
    from metaharness.workflows.dsl import WorkflowSpec

    spec = WorkflowSpec.model_validate({"name": "x", "steps": [
        {"id": "a", "objective": "o", "success_check": {"tol": 1e308}}]})
    assert spec.steps[0].success_check == {"tol": 1e308}
