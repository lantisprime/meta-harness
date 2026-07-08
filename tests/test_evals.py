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
