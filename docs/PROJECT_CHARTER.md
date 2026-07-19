# Meta-Harness Project Charter

Status: canonical project mission and enhancement decision framework.

This charter is the stable cornerstone of Meta-Harness. Architecture documents,
implementation plans, roadmaps, and individual features must serve this mission.
A change that conflicts with the charter requires an explicit charter decision;
it must not silently redefine the repository through implementation drift.

## Mission

Meta-Harness is the control plane for creating, recommending, managing,
correcting, evolving, packaging, and deploying goal-specific agent harnesses.
It manages each harness as an instrumented system that can diagnose failures,
generate repairs, improve its evaluators, tune available open model weights, and
retain or roll back the strongest independently validated version.

Verified solutions become reusable `SolutionTemplate` artifacts that other
compatible harnesses can instantiate, specialize, and re-evaluate. The
strategic target is a self-contained evolving harness release that carries a
versioned model portfolio, learning loop, sensors, evaluators, context, memory,
and rollback machinery required to improve without continuous manual code
editing.

The system optimizes toward independently measured goal progress. It must report
the closest protected result and unresolved gap when the full goal is not
achieved, rather than converting proxy improvement into a success claim.

Self-contained improvement does not mean self-authorized correctness or
deployment. The deployed harness may autonomously collect evidence, diagnose,
generate code, rehearse, train weights, evolve evaluator candidates, and select
an eligible best snapshot inside its constraint envelope. Protected validity,
permission expansion, release promotion, and production deployment remain
outside the candidate's editable authority.

## The Product Loop

Every major capability should strengthen at least one stage of this loop without
weakening the others:

1. **Specify the goal.** Convert the objective into a versioned
   `GoalFamilyProfile` with acceptance criteria, constraints, risks, available
   verifiers, budgets, task/environment distributions, and stopping conditions.
2. **Generate and recommend the harness.** Generate executable, inspectable
   harness source code—agents, workflow, prompts, context and memory policy,
   tools, sensors, evaluators, tests, and packaging—and recommend the best
   protected candidate for the declared goal.
3. **Rehearse and evaluate.** Exercise frozen candidates over sampled variants,
   repository or simulation fixtures, future batches, held-out ID/OOD transfer,
   replay/retention, mandatory guards, cost, and domain-specific verification.
4. **Manage, correct, and evolve.** Use localized sensor/evaluator evidence to
   manage each deployed harness and evolve three separately versioned planes:
   - `H`: harness code, prompts, context, memory, retrieval, tools, graph, and
     runtime topology;
   - `E`: scheduled evaluator candidates learned from sampled evidence but
     independently meta-evaluated and unable to approve themselves; and
   - `W`: immutable checkpoints, adapters, or deltas for the trainable
     open-weight members of the portfolio, learned from governed evidence and
     compared with `H` and `E` controlled.
5. **Create reusable solution templates.** Distill scoped episodic, semantic,
   and procedural memory plus verified `SolutionPattern` evidence into
   parameterized `SolutionTemplate` artifacts. Different harnesses may reuse
   their code, graph, prompt, tool, evaluator-obligation, test, and packaging
   fragments only after compatibility and negative-transfer checks followed by
   independent re-evaluation.
6. **Package and operate.** Build a reproducible, secret-free
   `HarnessReleaseBundle` containing the exact generated code, graph,
   dependencies, policies, sensors, evaluators, model or weight references,
   provenance, health checks, target adapter, and rollback parent. A
   self-contained evolving profile additionally carries its exact
   `ModelPortfolioSnapshot`, open `WeightSnapshot` members where present,
   training recipes, evidence sampler, evaluator-evolution schedule, H/E/W
   coordinator, update workspace, budgets, stop policy, and best-snapshot
   rollback logic. Deployment, monitoring, promotion, and rollback remain
   explicit, versioned operations.

Self-contained describes ownership of the complete harness control and
improvement loop, not locality of every model. Two primary portfolio modes are
supported:

- **hybrid:** frontier/hosted models and open-weight models collaborate in
  different roles with versioned routing, inference parameters, fallbacks, and
  budgets; and
- **open-only:** every model member is an open-weight snapshot and the release
  may run fully locally or offline when its tools and data permit.

Hosted/frontier weights are not trainable by Meta-Harness. Their role
assignment, routing, prompts, context, tools, and inference parameters evolve as
`H`; only open-weight members evolve as `W`. A hosted-only portfolio can still
self-correct its harness but cannot claim weight self-evolution.

## Code Generation Is a Core Actuator

Meta-Harness does not stop at suggesting prompts or drawing workflow diagrams.
It generates code in three bounded contexts:

- harness construction for a new goal family;
- domain-artifact generation by a running harness, such as a software patch;
  and
- localized harness repair after sensor/evaluator evidence identifies a failing
  surface.

Generated code is an immutable candidate, not authority. Every file must retain
generator, context, memory, retrieved-pattern, tool, dependency, build, test,
and parent provenance. It cannot modify its evaluator, mandatory sensors,
permissions, secrets, promotion state, or deployment pointer. It is executed or
packaged only after isolation and protected evaluation.

## Non-Negotiable Invariants

1. **Evidence before learning.** Raw model output and correlation are evidence,
   not truth, reward, memory, or causal attribution by themselves.
2. **Full-fidelity evaluation.** Preserve per-item, per-repetition response and
   artifact evidence. Aggregate scores, IRT profiles, residuals, and tiny
   benchmarks are derived diagnostic views, never replacements for protected
   cases or mandatory guards.
3. **Frozen comparisons.** Assess immutable candidates. Change one primary
   `H`, `E`, or `W` axis at a time for first-order attribution; label and design
   joint experiments explicitly.
4. **Evaluator non-self-approval.** An evaluator may evolve between assessment
   epochs, but one immutable evaluator snapshot governs each comparable epoch,
   and a candidate evaluator cannot define or approve its own validity.
5. **Determinism is not correctness.** Reproducible code and tool execution make
   evidence auditable; independent domain verification establishes correctness.
6. **Best protected over latest.** A newer harness, evaluator, or checkpoint
   cannot replace a stronger validated historical snapshot by recency or proxy
   reward alone.
7. **Bounded authority.** Generation and adaptation cannot mint permissions,
   widen data scope, expose secrets, bypass approval, or mutate protected state.
8. **Reversible operation.** Candidates, memories, weights, evaluators, releases,
   and deployment pointers have immutable lineage and exact rollback parents.
9. **Honest termination.** Stop on goal, plateau, oscillation, regression,
   forgetting, safety, validity, compute, data, or wall-time limits and retain
   the closest eligible result plus the unresolved gap.

## Enhancement Decision Gate

Every non-trivial proposal, plan, or pull request must answer:

1. Which product-loop stage does this improve?
2. What measurable goal-family outcome or enabling invariant improves?
3. What exact generated or versioned artifact changes?
4. Which sensors and immutable receipts will show whether it worked?
5. Which evaluator has adequate authority, and how is self-approval prevented?
6. Which future, transfer, replay, safety, and efficiency views test it?
7. If `H`, `E`, or `W` changes, what remains frozen for attribution?
8. How are provenance, permissions, secrets, packaging, monitoring, and rollback
   preserved?
9. What is the stop condition, and how is partial progress reported?

A proposal that cannot identify a product-loop contribution or a necessary
invariant is out of scope unless the charter is explicitly revised first.

## Document Roles

- This charter defines **why the repository exists and how enhancements are
  admitted**.
- [`architecture.md`](architecture.md) describes the current system design.
- [`context-memory-self-improving-harness-plan.md`](context-memory-self-improving-harness-plan.md)
  defines the staged context, memory, rehearsal, evaluation, H/E/W learning,
  code-generation, release, and observability program.
- [`harness-blueprints-overhaul.md`](harness-blueprints-overhaul.md) defines the
  blueprint authoring, portable packaging, and deployment experience.
- The code and tests remain the source of truth for current implemented
  behavior; plans must not be described as already shipped.
