"""Deterministic findings: the optimizer's outcome distilled into
one-decision actionables for the console.

No LLM anywhere in here — every finding is a rendering of verified facts
(ledger scores, held-out gate numbers, the loop's own stop reasons), per the
"never trust self-assessment" principle. An advisory LLM layer can sit on top,
but these rows must stand on evidence alone.
"""
from __future__ import annotations

from typing import Any, Optional

from metaharness.optimization.ledger import CandidateLedger


def _param_delta(seed_params, cand_params) -> dict[str, list[Any]]:
    """{knob: [seed value, candidate value]} for every knob that changed."""
    before, after = seed_params.model_dump(), cand_params.model_dump()
    return {k: [before[k], after[k]] for k in after if after[k] != before[k]}


def derive_findings(ledger: CandidateLedger, report: Optional[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ordered findings, most actionable first. Each row:
    {kind, story, delta, evidence, recommended}."""
    findings: list[dict[str, Any]] = []
    report = report or {}
    evaluated = ledger.evaluated()
    if not evaluated:
        return _stop_finding(report, findings)  # a crash-before-seed must still show
    seed = ledger.get(str(report.get("seed_id") or "")) or evaluated[0]
    gate = report.get("gate") or {}

    # 0. a promotion parked at the human gate — the user's one decision
    pending = ledger.pending_info()
    if pending:
        cand = ledger.get(pending["candidate"])
        pgate = pending.get("gate") or {}
        if cand is not None and cand.params is not None and seed.params is not None:
            findings.append({
                "kind": "pending",
                "story": (
                    f"{cand.id} ({cand.hypothesis}) answered "
                    f"{float(pgate.get('overall_candidate', 0)):.0%} reliably on "
                    f"questions it never saw vs "
                    f"{float(pgate.get('overall_incumbent', 0)):.0%} today — waiting "
                    f"for your approval."
                ),
                "delta": _param_delta(seed.params, cand.params),
                "evidence": f"held-out gate GO · {pgate.get('wins', 0)}W/"
                            f"{pgate.get('losses', 0)}L/{pgate.get('ties', 0)}T",
                "recommended": "approve or reject on the card",
            })

    # 1. the promotion — the headline change, with its held-out evidence
    if report.get("promoted") and ledger.promoted_info():
        cand = ledger.get(ledger.promoted_info()["candidate"])
        if cand is not None and cand.params is not None and seed.params is not None:
            findings.append({
                "kind": "promotion",
                "story": (
                    f"{cand.id} is now in use: {cand.hypothesis}. On questions it never "
                    f"saw during the search it answered "
                    f"{float(gate.get('overall_candidate', 0)):.0%} reliably vs "
                    f"{float(gate.get('overall_incumbent', 0)):.0%} before."
                ),
                "delta": _param_delta(seed.params, cand.params),
                "evidence": f"held-out gate GO · {gate.get('wins', 0)}W/"
                            f"{gate.get('losses', 0)}L/{gate.get('ties', 0)}T",
                "recommended": "already applied",
            })

    # 2. dead ends — evaluated candidates that fell off the Pareto frontier
    frontier_ids = {c.id for c in ledger.frontier()}
    for c in evaluated:
        if c.id in frontier_ids or c.scores is None:
            continue
        findings.append({
            "kind": "not_worth_it",
            "story": (
                f"{c.id} ({c.hypothesis}) used {c.scores.tokens_total:,} tokens for "
                f"pass^{c.scores.k} {c.scores.pass_hat_k:.2f} — a cheaper setup does the "
                f"same or better, so this one was dropped."
            ),
            "delta": {},
            "evidence": "dominated on the pass-vs-cost frontier",
            "recommended": "skip",
        })

    # 3. coverage gaps — the gate's own thin-sample warnings, verbatim
    for reason in gate.get("reasons") or []:
        if "too thin" in reason:
            findings.append({
                "kind": "coverage",
                "story": f"The verdict rests on thin evidence: {reason}.",
                "delta": {},
                "evidence": reason,
                "recommended": "add held-out questions to this suite",
            })

    # 4. how the search ended, when it ended abnormally
    return _stop_finding(report, findings)


def _stop_finding(report: dict[str, Any], findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if report.get("stopped") in ("budget", "no-proposal", "error"):
        note = (report.get("notes") or [""])[0]
        story = {
            "budget": "The search ran out of budget — raise the cap to keep exploring.",
            "no-proposal": f"The search stopped by itself: {note}",
            "error": f"The search crashed — {note}",
        }[report["stopped"]]
        findings.append({
            "kind": "info",
            "story": story,
            "delta": {},
            "evidence": note,
            "recommended": "rerun with a higher budget" if report["stopped"] == "budget" else "none",
        })
    return findings
