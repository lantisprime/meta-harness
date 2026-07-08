"""Filesystem candidate ledger — the paper's population `D` on disk.

One directory per candidate holding params, scores, rationale, and RAW eval
traces. Raw is load-bearing: the paper's central ablation (scores-only 34.6,
scores+summaries 34.9, raw traces 50.0 median accuracy) shows summaries
compress away the diagnostic detail the proposer needs — so traces are stored
verbatim and handed to proposers verbatim; `digest_text` never touches them.

Layout under root/:
    candidates/<cid>/candidate.json   params, scores, hypothesis, lineage
    candidates/<cid>/rationale.md     the proposer's hypothesis, human-readable
    candidates/<cid>/traces.jsonl     one raw row per eval attempt
    promoted.json                     params that passed the promotion gate
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel

from metaharness.core.types import now
from metaharness.optimization.params import HarnessParams


class CandidateScores(BaseModel):
    pass_hat_k: float
    pass_at_1: float
    tokens_in: int
    tokens_out: int
    cost_usd: float
    tasks: int
    k: int

    @property
    def tokens_total(self) -> int:
        return self.tokens_in + self.tokens_out


class Candidate(BaseModel):
    id: str
    parent: Optional[str] = None
    hypothesis: str = ""
    status: Literal["evaluated", "rejected"] = "evaluated"
    params: Optional[HarnessParams] = None
    scores: Optional[CandidateScores] = None
    rejected_reason: Optional[str] = None
    created_at: float = 0.0


class CandidateLedger:
    """Append-only store of every candidate ever proposed — including rejected
    ones, so the proposer learns from bad proposals the way the paper's
    proposer learned from regressions."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        (self.root / "candidates").mkdir(parents=True, exist_ok=True)
        self._candidates: list[Candidate] = []
        self._load()

    # -- persistence ---------------------------------------------------------

    def _load(self) -> None:
        dirs = sorted((self.root / "candidates").iterdir()) if (self.root / "candidates").exists() else []
        for d in dirs:
            meta = d / "candidate.json"
            if meta.is_file():
                self._candidates.append(
                    Candidate.model_validate_json(meta.read_text(encoding="utf-8"))
                )

    def next_id(self) -> str:
        return f"c{len(self._candidates) + 1:04d}"

    def record(self, candidate: Candidate, traces: Optional[list[dict[str, Any]]] = None) -> Candidate:
        if any(c.id == candidate.id for c in self._candidates):
            raise ValueError(f"candidate id already recorded: {candidate.id}")
        if candidate.created_at == 0.0:
            candidate.created_at = now()
        cdir = self.root / "candidates" / candidate.id
        cdir.mkdir(parents=True, exist_ok=True)
        (cdir / "candidate.json").write_text(candidate.model_dump_json(indent=1), encoding="utf-8")
        (cdir / "rationale.md").write_text(
            f"# {candidate.id} (parent: {candidate.parent or '—'}, {candidate.status})\n\n"
            f"{candidate.hypothesis}\n"
            + (f"\nRejected: {candidate.rejected_reason}\n" if candidate.rejected_reason else ""),
            encoding="utf-8",
        )
        if traces:
            with (cdir / "traces.jsonl").open("w", encoding="utf-8") as fh:
                for row in traces:
                    fh.write(json.dumps(row, default=str) + "\n")
        self._candidates.append(candidate)
        return candidate

    # -- queries ---------------------------------------------------------------

    def candidates(self) -> list[Candidate]:
        return list(self._candidates)

    def get(self, cid: str) -> Optional[Candidate]:
        return next((c for c in self._candidates if c.id == cid), None)

    def evaluated(self) -> list[Candidate]:
        return [c for c in self._candidates if c.status == "evaluated" and c.scores is not None]

    def frontier(self) -> list[Candidate]:
        """Pareto frontier over (maximize pass^k, minimize total tokens) — the
        paper keeps a frontier, not a greedy incumbent."""
        pool = self.evaluated()
        front: list[Candidate] = []
        for c in pool:
            dominated = any(
                o is not c
                and o.scores.pass_hat_k >= c.scores.pass_hat_k
                and o.scores.tokens_total <= c.scores.tokens_total
                and (
                    o.scores.pass_hat_k > c.scores.pass_hat_k
                    or o.scores.tokens_total < c.scores.tokens_total
                )
                for o in pool
            )
            if not dominated:
                front.append(c)
        return front

    def best(self) -> Optional[Candidate]:
        """Highest pass^k on the frontier; ties go to fewer tokens."""
        front = self.frontier()
        if not front:
            return None
        return max(front, key=lambda c: (c.scores.pass_hat_k, -c.scores.tokens_total))

    def traces(self, cid: str) -> list[dict[str, Any]]:
        path = self.root / "candidates" / cid / "traces.jsonl"
        if not path.is_file():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]

    def failure_traces(self, cid: str, limit: int = 6) -> list[dict[str, Any]]:
        """Raw failing attempt rows, verbatim — never digested (see module doc)."""
        fails = [row for row in self.traces(cid) if row.get("verdict") == "fail"]
        return fails[:limit]

    # -- promotion ---------------------------------------------------------------

    def promote(self, cid: str) -> Path:
        candidate = self.get(cid)
        if candidate is None or candidate.params is None:
            raise ValueError(f"cannot promote unknown/rejected candidate: {cid}")
        path = self.root / "promoted.json"
        path.write_text(
            json.dumps(
                {
                    "candidate": cid,
                    "promoted_at": now(),
                    "params": candidate.params.model_dump(),
                },
                indent=1,
            ),
            encoding="utf-8",
        )
        return path

    def promoted_params(self) -> Optional[HarnessParams]:
        path = self.root / "promoted.json"
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return HarnessParams.model_validate(data["params"])
