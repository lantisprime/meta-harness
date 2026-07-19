# Proposal: mental-model acquisition as a learning-module capability

**Status:** proposal (2026-07-19). Prototype shipped as
`knowledge-packs/schillings-mindsets/` (10 knowledge entries +
`seed_workflows.py` encoding 4 workflow-kind + 4 skill-kind entries).

## The idea

Add a knowledge-acquisition mode that analyzes **how a great practitioner
thinks** — from talks, interviews, writing — and recreates their mental
models as first-class store entries: prose models as `knowledge`, repeatable
reasoning procedures as `workflow` (ProcedureStep chains), judgment
dispositions as `skill` (with `skill_check`). The extraction target is the
expert's *reasoning moves*, attributed and timestamp-cited, not verifiable
world-facts — which is why a visionary keynote that would fail fact
corroboration is still a first-rate source.

The pipeline already exists end to end; this proposal names it as a
capability and closes three gaps:

```
yt-distill (transcript + slides, timestamped)
  → mental-model distillation (the new step: identify recurring reasoning
    moves, encode each as knowledge/workflow/skill entries)
  → seed as candidates → verification gates → specialist retrieval
```

## Why this is the point of META-12

META-12's contracts are already shaped like a formalized expert:

- `ImprovementPolicy.domain_expert` — an identity whose judgment anchors the
  campaign. A mental-model pack is that judgment made explicit and durable.
- `ExpertExample` (expected + rationale) — exactly what a distilled
  mental-model entry contains: what the expert would decide, and the
  reasoning move behind it. Extracted models can *generate* expert examples
  (e.g. Schillings' red-queen escape yields examples for "when do you stop
  patching and move upstream").
- `EvaluationCriterion` (anchors, approved_by) — the expert's standards.
  Schillings' open-ended-eval workflow is literally a criterion-design
  procedure; his "evals refactoring" slide (binary pass/fail → continuous
  scores) is a direct roadmap item for the pack suite.

So the loop closes: **acquire** an expert's mental models → **encode** them
as workflow/skill entries → they seed the `ImprovementPolicy` (criteria +
expert examples) that META-12 requires before a specialist may self-improve
→ the improvement campaign optimizes the specialist *toward the expert's way
of judging*, bounded by the frozen splits. Domain readiness stops being just
a gate and becomes acquirable: when `assess_domain_readiness` says "no
expert evidence," a mental-model acquisition run is the proposed fix.

## The six pillars of an expert's mental model

Each pillar answers a different question; a recreation missing a pillar
fails in a characteristic way (right derivation / wrong ranking, right rule
/ wrong moment, etc.):

| # | Pillar | Question it answers | Detection signature in sources | Contract shape | Schillings coverage |
|---|---|---|---|---|---|
| 1 | **Logic** | How do they derive conclusions? | Recurring warrants across independent decisions | anchors (`knowledge`) + `workflow` chains | ✅ strong (2 anchors, 4 workflows) |
| 2 | **Values** | What does "good" mean to them? | Choices between *viable* alternatives; what they sacrifice for what | `EvaluationCriterion` anchors, preference pairs | ◐ partial (complexity-in-loss, termination over speed) |
| 3 | **Perception** | What do they notice first, in what representation? | First reactions, metaphors, what they ask before answering | `skill` entries (NOTICE slot) | ◐ partial (visual-spatial, loop-shape salience) |
| 4 | **Repertoire** | What cases have they lived through? | War stories with rationales; the exceptions they cite | `ExpertExample` (expected + rationale) | ✗ thin (keynote carries few full cases) |
| 5 | **Epistemics** | How do they know what they know? | Dated predictions, declared horizons, handling of being wrong | `skill` entries with calibration checks | ✅ strong (horizon, resistance-as-signal, bias audit) |
| 6 | **Execution** | When/how does knowledge deploy? | Live traces: reviews, pairing, decision logs | TRIGGER/STOP slots, `task_types`, step checks | ✗ thin (needs trace-grade sources) |
| 7 | **Simulation** | Does the assembled model *predict* the expert? | Not extracted — constructed from pillars 1–6, validated against held-out decisions | probes + suite + regression baseline (META-12 splits) | ✗ not built (holdout test run by hand once: anchor V → chemistry) |

Pillar 7 is categorically different: pillars 1–6 are extracted components;
simulation is the runnable closure that validates them. Assemble the
pillars into an executable persona, feed it situations the expert never
discussed, and score predictive agreement against their actual (held-out)
decisions. Disagreements localize to a pillar — right conclusion via wrong
derivation indicts logic; right options, wrong ranking indicts values; a
rule fired at the wrong trigger indicts execution — so validation doubles
as per-assumption falsification (does anchor V really generate his
decisions, or did we over-fit one talk?). Because experts keep deciding,
every new source is fresh ground truth: re-running the simulation against
it is the persona's regression suite, the same baseline discipline the
store already applies to packs, and the mechanism that keeps a mental
model from going stale while the human keeps evolving.

Pillar-level coverage is the acquisition planner's map: the pack's next
runs should target repertoire (long-form interviews, BeOS retrospectives)
and execution (recorded reviews/pairing), since logic and epistemics are
already well-evidenced. Pillars 1–6 also imply distinct probe families:
logic probes (derive a held-out position), value probes (rank alternatives
as the expert would), perception probes (what's salient in this scenario),
repertoire probes (nearest precedent + rationale), epistemic probes (state
the horizon/confidence), execution probes (does the procedure fire at the
right trigger).

## What the harness does with determined pillars

Five behavior changes, keyed to the harness lifecycle. Fast-loop actions
(injection, marking) run automatically; everything that adopts, launches,
or acquires stays advisory — proposed with evidence, human-started.

1. **Task time — pillar-shaped injection.** The injection block gains
   structure: perception entries first (what to notice), the matching
   logic `workflow` as the analysis procedure (NOTICE→MOVE→CHECK→STOP),
   values as ranking constraints, repertoire cases as precedents. Uses
   existing `task_types` + retrieval scoring; the change is injection
   ordering/role.
2. **Review time — persona as consultable seat.** Pillar 7 runs as a
   second-opinion advisor, answering with coverage-qualified confidence
   ("anchor-V territory, two sources" vs "execution pillar thin, no
   precedent") — the persona knows where it is hollow.
3. **Improvement time — META-12 instantiation.** Values →
   `EvaluationCriterion`, repertoire → `ExpertExample`, simulation suite →
   frozen evaluator + baseline. A determined pillar set is the proposed
   fix when `assess_domain_readiness` reports missing expert evidence;
   the campaign itself remains human-started.
4. **Learning time — pillars on trial.** Injected pillar entries are mark
   targets: verified failures implicating a reasoning move fire quality
   signals on *that move*. New expert sources enter as held-out ground
   truth first; falling predictive agreement = overfit extraction or the
   expert changed their mind — person-level staleness.
5. **Acquisition time — pillar gaps generate the queue.** Per-pillar
   coverage feeds the advisor like topic coverage does today
   ("execution: thin → sweep the patent corpus"), EFE-ranked beside the
   existing gap kinds.

## Pillar-driven acquisition from reputable published sources

Pillar coverage gaps must drive *targeted sweeps of published, reputable
sources* — not more mining of the same talk. Per-pillar source strategy:

| Pillar gap | Source types to sweep | Reputability handling |
|---|---|---|
| Repertoire | Long-form interviews, oral histories (ethw.org), published book interviews, retrospectives | Extend `ReputabilityPolicy` per expert: publishers/archives hosting the text (e.g. birdhouse.org hosts the published BeOS Bible interview) promoted to `primary` with justification |
| Execution | Patents (USPTO via patents.justia.com — official record of *how* they solved problems), recorded reviews/pairing, design docs | Patents = `official`; the patent record is the expert's execution trace at scale |
| Values / Logic | Papers (arXiv, ResearchGate profile), essays, technical blog posts under their own name | Academic archives already `official` in `DEFAULT_POLICY` |
| Epistemics | Dated predictions in press interviews; later sources showing reversals | Press = `community`+; a reversal needs both endpoints cited |

Mechanics, reusing what exists: the sweep is an acquisition run whose
queries are generated from (expert × pillar) templates; every fetched
source gets `ReputabilityPolicy.tier_for()`; the existing corroboration
rule already enforces that an `unknown`-tier source cannot be the sole
support of a published entry, so enrichment from personal archives is
possible but must be corroborated. Each new source also feeds pillar 7:
its decisions join the held-out ground-truth set before its content is
used for extraction — new material is a test first, training data second.

### Completeness: what determines "enough" per pillar

Volume never determines completeness; function does. Criteria, strongest
first — each lower one is a proxy used until the ones above it are
runnable:

1. **Predictive sufficiency (master criterion).** A pillar is complete
   when another source stops improving the persona's predictive agreement
   on held-out decisions — a learning curve over sources; stop at the
   plateau. Requires pillar 7.
2. **Saturation.** Per-pillar novelty rate (new anchors/rules/cases per
   source); K consecutive dry sources → saturated. Same loop-until-dry /
   backoff idiom the learning module already uses. Note saturation is
   per-pillar per-source: the BeOS interview was high-novelty for
   repertoire, zero-novelty (pure corroboration) for logic.
3. **Triangulation minima.** Anchors need ≥N independent derivations;
   entries need ≥2 independent sources (same-person corroboration). A
   pillar of single-source entries is populated, not complete.
4. **Slot-fill rate.** Fraction of entries with TRIGGER and STOP
   evidenced, not just MOVE — deployment conditions are the expertise.
5. **Task-relative coverage.** Completeness is a pillar × task-domain
   matrix (parallel to the topic coverage map), never a scalar —
   repertoire can be saturated for OS-architecture and empty for ML-era
   decisions.
6. **Contradiction resolution.** Conflicting extracted values usually
   mean an unextracted regime boundary (conditionality), not an error;
   unresolved contradictions hold the pillar below complete.
7. **Declared source ceilings.** Pillars that cannot complete from
   published material (execution without traces) get an explicit
   "bounded by source availability" cap, so the planner distinguishes
   "keep sweeping" from "no sweep can fix this."

Mechanics reuse the store's evidence math: pillar evidence thinness =
Beta posterior variance (`laplace_variance` / `uncertainty_for`), firing
the same `uncertainty`-kind signal as thin topics; next-pillar selection
is EFE-ranked by expected agreement gain per candidate source. Output
shape: a **pillar readiness report** — the per-pillar analogue of
META-12's `DomainReadinessReport` (sources, independence, slot-fill,
novelty trend, variance, predictive agreement, ceiling flag).

**Demonstrated (2026-07-19):** a first repertoire sweep for Schillings
found the Henry Bortman BeOS Bible interview (birdhouse.org — situation/
decision/reasoning triples from the BeOS years) and 40+ patents
(patents.justia.com); extracted as pack entries 13–14. The interview
independently corroborates anchor structure from a source 25 years before
the keynote — e.g. "speed is mostly a perception thing" is the
cost/perception analysis of anchor C applied in 1998.

## Extraction schema: logic, ways of thinking, knowledge execution

*(The three layers below describe where material is visible in sources;
the six pillars above describe the model's anatomy. Layer "logic" = pillar
1; "ways of thinking" spans pillars 3+5; "knowledge execution" = pillar 6
with pillar 4 as its raw material.)*

What "the expert's mental model" decomposes into — three layers, each hiding
in a different place in the source material and landing in a different
contract shape:

| Layer | What it is | Where it shows in artifacts | Contract shape |
|---|---|---|---|
| **Logic** | Inference moves: evidence → conclusion (constraint periodization, economic flip, loop-shape recognition) | Worked arguments the expert performs out loud | `workflow` (ProcedureStep chain) |
| **Ways of thinking** | Representations and attention: what format they think in, what they notice first, what their taste rejects instantly | Metaphors, asides, reactions — mostly tacit, rarely stated as principle | `skill` (+ `skill_check` trigger) |
| **Knowledge execution** | Conditional deployment: *when* which knowledge activates, how it merges with the live situation, how they verify mid-flight, when they stop | Case walkthroughs and decisions — weakest in polished talks, strongest in traces (reviews, postmortems, live coding) | `task_types` binding + step `check`s + retrieval conditions |

To keep extraction honest across all three layers, every mental-model entry
— whatever its kind — is distilled through one five-slot template:

- **TRIGGER** — the situation pattern that activates this model
  (→ `task_types`, `skill_check` key, topic)
- **NOTICE** — the evidence the expert attends to first, and what a novice
  would wrongly attend to (→ first ProcedureStep, or the skill body)
- **MOVE** — the reasoning/action sequence (→ ProcedureStep chain)
- **CHECK** — how the expert verifies mid-flight that the move is working
  (→ per-step `check` pairs)
- **STOP** — the termination/escalation rule: when the model does not apply
  or the expert abandons the line (→ final step check, or an explicit
  anti-trigger in the body)

A distillation that fills MOVE but leaves TRIGGER/STOP empty has extracted a
textbook, not an expert — the deployment conditions *are* the expertise.
Layer 3 is also the honest limit of talk-mining: keynotes demonstrate logic
and reveal thinking styles, but execution mostly needs trace-grade sources
(recorded reviews, pairing sessions, decision logs). The acquisition mode
should record per-entry which layers the source actually evidenced, so
coverage gaps ("we have Schillings' logic but not his execution") are
queryable and drive the next acquisition — the same coverage-map discipline
the store already applies to topics.

## Logic acquisition (layer 1, focused)

**Definition.** An expert's logic is a *compact generator of their
decisions*: the smallest set of anchors + inference rules + control flow
that re-derives what they actually decided across independent situations.

- **Anchors** ("logical thinking anchors") — propositions the expert argues
  *from* but never argues *for*. Detection signature: a premise recurring in
  the derivations of many unrelated decisions, stated without justification
  but used as justification for everything else. Toulmin terms: the
  recurring warrants/backings.
- **Inference rules** — licensed moves transforming anchor + situation
  facts into a conclusion ("cost → ~0 ⇒ processes priced on that cost are
  obsolete"; "fix loop re-arms itself ⇒ relocate upstream").
- **Control flow** — the order of moves: what is established first, what
  branches on what, where verification happens. This is the procedural
  execution of the analysis, and it is what ProcedureStep chains encode.

**Method (decision-derivation-triangulation):**

1. **Decision inventory.** From each source (talk, interview, paper,
   review), list decisions-with-stakes, each timestamped/cited: bets made,
   reversals, predictions with dates, designs chosen over alternatives.
   Decisions, not statements — a statement shows vocabulary; a decision
   shows the logic under load.
2. **Derivation reconstruction.** For each decision, reconstruct the
   argument: situation facts + which premise + which move ⇒ this
   conclusion (argument mining / Toulmin mapping).
3. **Cross-decision triangulation.** Cluster premises recurring across
   derivations of *independent* decisions. A premise appearing in ≥N
   derivations is an anchor (this instantiates the proposal's same-person
   corroboration rule: corroboration across decisions, not across domains).
   Moves recurring across derivations are the rule set; recurring orderings
   are the control flow.
4. **Holdout prediction (the gate).** Re-derive a decision that was held
   out of extraction using only the anchors + rules. Predictive agreement —
   not prose resemblance — is the publish criterion for a logic entry, and
   is mechanically checkable as a probe.

**Worked example (this talk).** Two anchors generate most of Schillings'
conclusions: **V** — *cheap verification is the master resource* (derives
the 2018 code bet, the chemistry/biology expansion, the self-play pivot,
and the compression eval), and **C** — *cost structure determines process
structure* (derives the eras analysis, correct-by-construction, and
"make writing code harder" language design). V extracted from the
code-only sections predicts his chemistry position before he states it —
the holdout test passing on real data.

**Store mapping.** Anchors → `knowledge` entries whose claims are the
anchor proposition and whose body lists the derivations citing it (id
convention `anchor-<expert>-<slug>`); rules + control flow → `workflow`
entries (already prototyped); the holdout test → probes: "given this
situation the expert never discussed, derive the position" with the
expert's actual (held-out) decision as `expected`.

## Faithfulness: verifying generated entries against their sources

(Elaborates gap 2 below.) The existing gate already gives three layers —
deterministic structure (quarantine absolute, hashed citations,
independent-domain corroboration), sandboxed `skill_check` execution, and
judged claim-support entailment ("outside knowledge does not count"),
with strict-mode human publish. Mental-model entries add a faithfulness
ladder on top, cheap → expensive:

1. **Locator-pinned entailment** — judge each claim against the span its
   own citation names (the chunk at `t=…s`), not caller-supplied
   excerpts; makes citation-drift checkable per claim.
2. **Deterministic quote/locator checks** — literal quotes fuzzy-matched
   against the transcript (catches misquotes without a model); locators
   bounded by the source's real span (catches hallucinated timestamps).
3. **Per-step provenance for workflows** — each ProcedureStep needs a
   supporting span or an explicit `inferred` mark; the evidenced-step
   fraction is the workflow's faithfulness score, published as a declared
   evidence class (the vision-extraction pattern). Closes the silent
   step-interpolation hole.
4. **Stance + scope rubric** — asserting vs reporting vs hypothetical
   (quoting ≠ believing); claim generality must not exceed span scope
   (said-about-2018-code ≠ believes-universally).
5. **Anti-cherry-picking sweep** — adversarially search the same corpus
   for contradicting spans; a hit signals a missing regime boundary
   (completeness criterion 6), and ignoring a known contradiction voids
   fidelity.
6. **Behavioral reproduction (strongest)** — extracted logic must
   re-derive the expert's actual held-out decisions, delivered as
   evalgen probes with `expected` = the real decision; pillar 7 doing
   verification duty. Prose that cannot regenerate decisions is not a
   model.

Identity rule (per the probe self-validation fix, de7df24): the
faithfulness judge must not be the distiller; provenance records both
identities.

## Gaps to close (in order)

1. **Distillation prompt/mode.** `Distiller` today extracts facts from
   sources. Add a mental-model mode: "identify the recurring reasoning moves
   this person demonstrates; for each, name the trigger, the steps, and the
   check" — output mapped onto knowledge/workflow/skill kinds. The
   schillings pack is the hand-made reference output for evaluating it.
2. **Verification for attributed entries.** Claims have the form "X reasons
   this way," verified against the timestamped source (does the cited span
   support the attributed move?), not against world-truth. Needs a
   corroboration rule variant: same-person corroboration across independent
   talks/writings instead of independent-domain corroboration.
3. **Probe generation for workflow entries.** A mental-model workflow is
   testable: give the specialist a scenario and check whether it applies the
   procedure (e.g. names the binding constraint before proposing process
   changes). These probes become the `EvaluationCriterion.probe_ids` for
   META-12 policies.

## Prototype inventory (reference output)

| Entry kind | Count | Examples |
|---|---|---|
| knowledge | 10 | bottleneck-era framing; economics-first reasoning; resistance-as-signal; gold-we-cannot-see |
| workflow | 4 | bottleneck-era analysis; self-play curriculum; red-queen upstream shift; open-ended eval design |
| skill | 4 | resistance-as-signal; domain-tractability test; horizon scoping; bias audit |

Seed: `selflearn seed-kb knowledge-packs/schillings-mindsets --pack
schillings-mindsets --store <store>` plus `python
knowledge-packs/schillings-mindsets/seed_workflows.py --store <store>`.
