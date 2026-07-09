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


def test_code_proposer_errors_clearly_without_a_coding_cli(tmp_path, monkeypatch):
    """No coding CLI on PATH -> a clear SystemExit naming the supported CLIs,
    not a crash deep inside the loop."""
    import metaharness.harness as harness_pkg

    monkeypatch.setattr(harness_pkg, "available_clis", lambda: {})
    args = SimpleNamespace(
        suite="math", rounds=1, k=1, proposer="code", local=False,
        endpoint=None, root=str(tmp_path), max_tokens=None, max_cost=None,
    )
    with pytest.raises(SystemExit, match="coding CLI"):
        cli._run_optimize(args)
