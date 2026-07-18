"""Suite regression: a pack update must not silently degrade its specialist.

``snapshot_baseline`` records the pack suite's pass rate after a known-good
state; ``check_regression`` compares a fresh run against it and fails loud —
a knowledge change that hurts the suite is a rollback candidate regardless
of how good its marks look (the plan's honesty rule: marks steer ranking,
the suite decides what a pack version may claim).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from selflearn.store.packstore import PackStore, StoreError
from selflearn.verification.suite import SuiteResult

BASELINE_FILE = "baseline.json"


@dataclass(frozen=True)
class RegressionReport:
    pack: str
    baseline_model: str
    baseline_rate: float
    current_rate: float
    baseline_total: int
    current_total: int

    @property
    def delta(self) -> float:
        return self.current_rate - self.baseline_rate

    def ok(self, tolerance: float = 0.0) -> bool:
        return self.delta >= -tolerance

    def summary(self) -> str:
        verdict = "OK" if self.ok() else "REGRESSION"
        return (f"[{verdict}] pack={self.pack}: baseline "
                f"{self.baseline_rate:.0%}/{self.baseline_total} probes -> "
                f"current {self.current_rate:.0%}/{self.current_total} "
                f"(delta {self.delta:+.0%})")


def _baseline_path(store: PackStore, pack: str) -> Path:
    path = store.root / pack / "evals" / BASELINE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def snapshot_baseline(store: PackStore, pack: str, result: SuiteResult) -> Path:
    if result.total == 0:
        raise StoreError(f"refusing to snapshot an empty suite for {pack!r}")
    path = _baseline_path(store, pack)
    path.write_text(json.dumps({
        "pack": pack, "model_id": result.model_id,
        "pass_rate": result.pass_rate, "total": result.total,
        "injected": result.injected}, indent=1, sort_keys=True))
    return path


def check_regression(store: PackStore, pack: str,
                     current: SuiteResult) -> RegressionReport:
    path = _baseline_path(store, pack)
    if not path.exists():
        raise StoreError(
            f"no suite baseline for pack {pack!r}; snapshot one after a "
            "known-good state before checking regression")
    baseline = json.loads(path.read_text())
    if baseline.get("model_id") != current.model_id:
        raise StoreError(
            f"baseline was recorded with model {baseline.get('model_id')!r} "
            f"but the current run used {current.model_id!r}; paired "
            "comparison requires the same model")
    return RegressionReport(
        pack=pack, baseline_model=baseline["model_id"],
        baseline_rate=float(baseline["pass_rate"]),
        current_rate=current.pass_rate,
        baseline_total=int(baseline["total"]), current_total=current.total)
