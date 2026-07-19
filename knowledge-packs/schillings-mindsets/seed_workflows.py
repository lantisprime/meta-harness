"""Seed Benoit Schillings' reasoning procedures as workflow/skill entries.

The Markdown files beside this script carry the mental models as prose
(knowledge-kind, seedable with ``selflearn seed-kb``). This script encodes
the *procedural* mental models — the repeatable reasoning moves — as
workflow-kind entries with machine-readable ProcedureStep chains, plus
skill-kind entries for the judgment dispositions, so a specialist can be
handed not just what Schillings believes but the steps of how he thinks.

Source: "Software engineering is not about writing code", Benoit Schillings
(VP of Research, Google DeepMind), AI Engineer World's Fair.
https://www.youtube.com/watch?v=1P1hJ36rxM0

Usage:
    python seed_workflows.py --store ~/.metaharness/knowledge [--publish]
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

from selflearn.contracts import CandidateEntry, EntrySource, ProcedureStep
from selflearn.store.packstore import PackStore
from selflearn.store.seed import _maybe_publish

PACK = "schillings-mindsets"
VIDEO = "https://www.youtube.com/watch?v=1P1hJ36rxM0"
FETCHED = "2026-07-19T00:00:00Z"


def _src(t: int) -> tuple[EntrySource, ...]:
    url = f"{VIDEO}&t={t}s"
    return (EntrySource(url=url, fetched_at=FETCHED,
                        sha256=hashlib.sha256(url.encode()).hexdigest(),
                        tier="primary", locator=f"t={t}s"),)


WORKFLOWS = [
    CandidateEntry(
        id="wf-schillings-bottleneck-era-analysis",
        pack=PACK, kind="workflow", topic="bottleneck-era-framing",
        task_types=("analysis", "architecture-review", "planning"),
        body="Schillings' era analysis: periodize a practice by its binding "
             "constraint, check whether that constraint still holds, and "
             "re-derive the process from the new scarcity instead of "
             "patching rituals the old scarcity left behind.",
        claims=("Every engineering practice is an adaptation to a scarce "
                "resource; when the resource stops being scarce the practice "
                "must be re-derived, not preserved",),
        sources=_src(262),
        procedure=(
            ProcedureStep(
                id="name-constraint",
                objective="Name the resource this practice assumes is scarce "
                          "or expensive (compute, human working memory, "
                          "code-writing effort, attention)",
                task_type="analysis"),
            ProcedureStep(
                id="check-constraint",
                objective="Check whether that resource is still scarce, or "
                          "trending to ~zero cost",
                task_type="analysis", depends_on=("name-constraint",),
                check=(("evidence", "cost trend cited, not assumed"),)),
            ProcedureStep(
                id="locate-new-bottleneck",
                objective="If the constraint dissolved, locate where system "
                          "pressure relocated (e.g. writing -> validation "
                          "and specification)",
                task_type="analysis", depends_on=("check-constraint",)),
            ProcedureStep(
                id="rederive-process",
                objective="Re-derive the process from the new bottleneck; "
                          "list which existing rituals no longer earn their "
                          "cost",
                task_type="planning", depends_on=("locate-new-bottleneck",),
                check=(("output", "kept/dropped list with reasons"),)),
        )),
    CandidateEntry(
        id="wf-schillings-self-play-curriculum",
        pack=PACK, kind="workflow", topic="self-play-curriculum",
        task_types=("self-improvement", "learning", "eval-design"),
        body="Schillings' locked-room loop: improvement without external "
             "material is self-generated challenges you can verify, judged "
             "honestly, escalated continuously. Continuous verification + "
             "reinforcement optimization + infinite sandbox.",
        claims=("Skill growth after training data runs out = generate "
                "verifiable challenges slightly beyond current ability and "
                "close the loop on them",),
        sources=_src(589),
        procedure=(
            ProcedureStep(
                id="generate-challenge",
                objective="Generate a challenge slightly beyond current "
                          "demonstrated ability in the target domain",
                task_type="learning"),
            ProcedureStep(
                id="attach-verification",
                objective="Attach a strict, cheap check the attempt can be "
                          "judged by (compile, run, measure, proof) BEFORE "
                          "attempting",
                task_type="eval-design", depends_on=("generate-challenge",),
                check=(("verifiable", True),)),
            ProcedureStep(
                id="attempt-and-judge",
                objective="Attempt the challenge and judge the result "
                          "against the check, including architecture "
                          "quality, not only output correctness",
                task_type="learning", depends_on=("attach-verification",)),
            ProcedureStep(
                id="escalate",
                objective="On success raise difficulty; on failure generate "
                          "a nearer variant; repeat — the loop is the "
                          "curriculum",
                task_type="learning", depends_on=("attempt-and-judge",)),
        )),
    CandidateEntry(
        id="wf-schillings-red-queen-upstream",
        pack=PACK, kind="workflow", topic="red-queen-upstream-shift",
        task_types=("analysis", "architecture-review", "security"),
        body="Schillings' escape from non-terminating fix loops: recognize "
             "the red-queen shape (every fix improves the generator of the "
             "next problem), refuse to optimize inside it, and move the "
             "intervention upstream of generation (correct-by-construction, "
             "secure-by-default).",
        claims=("A detect-and-fix loop whose fixes deepen the next round's "
                "problems is non-terminating; the exit is upstream of "
                "generation, and its difficulty is the price of "
                "termination",),
        sources=_src(697),
        procedure=(
            ProcedureStep(
                id="detect-loop-shape",
                objective="Check whether each fix round improves the process "
                          "that generates the next problem (arms race) or "
                          "genuinely shrinks the problem pool",
                task_type="analysis"),
            ProcedureStep(
                id="refuse-inside-optimization",
                objective="If it is an arms race, stop investing in faster "
                          "iterations of the loop — speed inside a red-queen "
                          "race holds position, it does not gain ground",
                task_type="analysis", depends_on=("detect-loop-shape",)),
            ProcedureStep(
                id="move-upstream",
                objective="Relocate the intervention upstream of generation: "
                          "make the artifact correct/secure by construction "
                          "instead of detected-and-patched",
                task_type="planning", depends_on=("refuse-inside-optimization",),
                check=(("intervention_point", "pre-generation"),)),
            ProcedureStep(
                id="verify-loop-drain",
                objective="Verify the downstream loop volume actually drops; "
                          "if not, the intervention was not upstream enough",
                task_type="verification", depends_on=("move-upstream",)),
        )),
    CandidateEntry(
        id="wf-schillings-open-ended-eval-design",
        pack=PACK, kind="workflow", topic="open-ended-loss-functions",
        task_types=("eval-design", "verification"),
        body="Schillings' never-ending eval: replace binary pass/fail with a "
             "continuous unbounded metric whose loss includes solution "
             "complexity (compression: compressed size + source size), keep "
             "verification trivially cheap, and read progress from the "
             "metric's slope.",
        claims=("Binary evals saturate and get gamed; unbounded metrics "
                "with complexity inside the loss force genuine novelty",),
        sources=_src(804),
        procedure=(
            ProcedureStep(
                id="pick-unbounded-metric",
                objective="Choose a continuous metric with no attainable "
                          "ceiling for the capability under test",
                task_type="eval-design"),
            ProcedureStep(
                id="price-in-complexity",
                objective="Include the solution's own complexity in the loss "
                          "(e.g. + size of source) so memorization and bloat "
                          "score worse than insight",
                task_type="eval-design", depends_on=("pick-unbounded-metric",)),
            ProcedureStep(
                id="keep-check-cheap",
                objective="Ensure one honest evaluation stays trivially "
                          "cheap and deterministic (decompress, diff, "
                          "measure)",
                task_type="eval-design", depends_on=("pick-unbounded-metric",),
                check=(("verification_cost", "trivial"),)),
            ProcedureStep(
                id="track-slope",
                objective="Report progress as the metric's trend over "
                          "attempts, never as a pass rate",
                task_type="verification",
                depends_on=("price-in-complexity", "keep-check-cheap")),
        )),
]

SKILLS = [
    CandidateEntry(
        id="sk-schillings-resistance-as-signal",
        pack=PACK, kind="skill", topic="resistance-as-signal",
        task_types=("self-improvement", "review"),
        body="Treat your instinctive dismissal of a new abstraction layer "
             "('that's not real engineering') as a recurring, historically "
             "wrong bias — compilers, garbage collection, vibe coding — and "
             "convert the scorn into a hands-on test before ruling.",
        claims=("Expert scorn at a new abstraction layer has a poor "
                "historical base rate and should trigger a trial, not a "
                "verdict",),
        sources=_src(144),
        skill_check=(("on_dismissal", "name the historical analogue and "
                                      "run one real task on the new layer"),)),
    CandidateEntry(
        id="sk-schillings-domain-tractability",
        pack=PACK, kind="skill", topic="domain-tractability-test",
        task_types=("planning", "analysis"),
        body="Rank domains to learn or automate by data abundance times "
             "verification cheapness — the feedback-loop economics that made "
             "code the right 2018 bet — and re-check the ranking as those "
             "properties move.",
        claims=("Progress compounds fastest where one honest try-verify "
                "iteration is cheapest and data is most abundant",),
        sources=_src(482),
        skill_check=(("before_committing", "state the cost of one honest "
                                           "verify iteration"),)),
    CandidateEntry(
        id="sk-schillings-horizon-scoping",
        pack=PACK, kind="skill", topic="horizon-scoping",
        task_types=("planning",),
        body="Declare both edges of the time window your reasoning is valid "
             "in (his team: one month to one year) — the near edge is a "
             "job-identity boundary, the far edge an epistemic honesty "
             "boundary — and refuse work belonging to a different horizon.",
        claims=("Predictions and plans are only meaningful inside an "
                "explicitly declared horizon window",),
        sources=_src(60),
        skill_check=(("on_plan", "both horizon edges stated and defended"),)),
    CandidateEntry(
        id="sk-schillings-bias-audit",
        pack=PACK, kind="skill", topic="gold-we-cannot-see",
        task_types=("review", "research"),
        body="Treat solution-plausibility feelings as a trained prior with "
             "known blind spots ('trained to survive in the jungle, not do "
             "quantum computing'); deliberately consult differently-biased "
             "viewpoints and expect breakthroughs in already-explored "
             "territory.",
        claims=("The feeling that an approach is natural is evidence about "
                "your training distribution, only weakly about the solution "
                "space",),
        sources=_src(1128),
        skill_check=(("on_confident_rejection", "obtain one differently-"
                                                "biased viewpoint first"),)),
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--store", required=True)
    ap.add_argument("--publish", action="store_true",
                    help="pre-gate publish (bulk-seed basis); default holds "
                         "entries as candidates for verification")
    args = ap.parse_args()
    store = PackStore(Path(args.store).expanduser())
    with store.deferred_persist():
        for entry in WORKFLOWS + SKILLS:
            _maybe_publish(store, entry, args.publish)
    state = "published (pre-gate)" if args.publish else "candidates"
    print(f"seeded {len(WORKFLOWS)} workflow + {len(SKILLS)} skill entries "
          f"into pack {PACK!r} as {state}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
