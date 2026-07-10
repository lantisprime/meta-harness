"""CLI argument-parsing and proposer-selection coverage for `metaharness
optimize`, including the code-space proposer (--proposer code)."""
from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from metaharness import cli


def test_optimize_accepts_code_proposer(monkeypatch):
    """`--proposer code` parses into args and dispatches to _run_optimize."""
    captured: dict = {}
    monkeypatch.setattr(cli, "_run_optimize", lambda args: captured.update(vars(args)))
    monkeypatch.setattr(sys, "argv",
                        ["metaharness", "optimize", "--proposer", "code", "--suite", "math"])
    cli.main()
    assert captured["proposer"] == "code"
    assert captured["suite"] == "math"


def test_optimize_rejects_unknown_proposer(monkeypatch):
    """An out-of-vocabulary proposer is rejected by argparse (exit 2)."""
    monkeypatch.setattr(sys, "argv", ["metaharness", "optimize", "--proposer", "psychic"])
    with pytest.raises(SystemExit):
        cli.main()


def test_optimize_parses_max_wall_s_flag(monkeypatch):
    """--max-wall-s parses into args for the optimize command, same as the
    existing --proposer flag test."""
    captured: dict = {}
    monkeypatch.setattr(cli, "_run_optimize", lambda args: captured.update(vars(args)))
    monkeypatch.setattr(sys, "argv",
                        ["metaharness", "optimize", "--max-wall-s", "12.5"])
    cli.main()
    assert captured["max_wall_s"] == 12.5


def test_optimize_max_wall_s_reaches_the_budget_constructor(tmp_path, monkeypatch):
    """--max-wall-s must reach the Budget() built inside _run_optimize, not
    just parse into args. `--proposer code` with no coding CLI on PATH stops
    the function (SystemExit) right after Budget is constructed (cli.py: the
    Budget block precedes the coding-CLI check), so this observes the real
    constructor call without running the optimizer."""
    import metaharness.harness as harness_pkg
    from metaharness.core.budget import Budget as RealBudget

    captured: dict = {}

    class SpyBudget(RealBudget):
        def __init__(self, **kw):
            captured.update(kw)
            super().__init__(**kw)

    # _run_optimize does `from metaharness.core.budget import Budget` locally
    # at call time, so the patch target is the source module, not cli.Budget.
    monkeypatch.setattr("metaharness.core.budget.Budget", SpyBudget)
    monkeypatch.setattr(harness_pkg, "available_clis", lambda: {})
    args = SimpleNamespace(
        suite="math", rounds=1, k=1, proposer="code", local=False,
        endpoint=None, root=str(tmp_path), max_tokens=None, max_cost=None,
        max_wall_s=9.5,
    )
    with pytest.raises(SystemExit, match="coding CLI"):
        cli._run_optimize(args)
    assert captured.get("max_wall_s") == 9.5


def test_optimize_zero_wall_cap_still_builds_a_capped_budget(tmp_path, monkeypatch):
    """Regression (issue-#5 panel round 2, convergent P2: codex+kimi+Claude):
    the truthy guard `if args.max_tokens or args.max_cost or args.max_wall_s`
    made an explicit `--max-wall-s 0` (and 0 for the other two flags) build NO
    Budget at all — no cap, no spend accounting, silently. The `is not None`
    guard must construct a Budget with max_wall_s == 0.0 that stops on the
    first charge carrying positive latency."""
    import metaharness.harness as harness_pkg
    from metaharness.core.budget import Budget as RealBudget, BudgetExceeded

    captured: dict = {}

    class SpyBudget(RealBudget):
        def __init__(self, **kw):
            captured["kwargs"] = kw
            captured["instance"] = self
            super().__init__(**kw)

    monkeypatch.setattr("metaharness.core.budget.Budget", SpyBudget)
    monkeypatch.setattr(harness_pkg, "available_clis", lambda: {})
    args = SimpleNamespace(
        suite="math", rounds=1, k=1, proposer="code", local=False,
        endpoint=None, root=str(tmp_path), max_tokens=None, max_cost=None,
        max_wall_s=0.0,
    )
    with pytest.raises(SystemExit, match="coding CLI"):
        cli._run_optimize(args)
    budget = captured.get("instance")
    assert budget is not None, "a 0 cap must still construct a Budget"
    assert budget.max_wall_s == 0.0
    with pytest.raises(BudgetExceeded):
        budget.charge(wall_s=0.1)  # first positive-latency charge stops the run


def test_serve_parses_max_wall_s_into_state_budget(monkeypatch):
    """--max-wall-s upgrades the served harness's shared Budget in place, the
    same pattern the existing --max-cost-usd/--max-tokens flags use."""
    import uvicorn

    captured_state: dict = {}
    orig_build = cli._build_mock_state

    def spy_build():
        state = orig_build()
        captured_state["state"] = state
        return state

    monkeypatch.setattr(cli, "_build_mock_state", spy_build)
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: None)
    monkeypatch.setattr(sys, "argv", ["metaharness", "serve", "--max-wall-s", "42.5"])
    cli.main()
    assert captured_state["state"].budget.max_wall_s == 42.5


def test_code_proposer_errors_clearly_without_a_coding_cli(tmp_path, monkeypatch):
    """No coding CLI on PATH -> a clear SystemExit naming the supported CLIs,
    not a crash deep inside the loop."""
    import metaharness.harness as harness_pkg

    monkeypatch.setattr(harness_pkg, "available_clis", lambda: {})
    args = SimpleNamespace(
        suite="math", rounds=1, k=1, proposer="code", local=False,
        endpoint=None, root=str(tmp_path), max_tokens=None, max_cost=None,
        max_wall_s=None,
    )
    with pytest.raises(SystemExit, match="coding CLI"):
        cli._run_optimize(args)
