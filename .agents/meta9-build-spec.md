# TASK-20260714-007 / META-9 — protected scaffold-H ablation

Card: `TASK-20260714-007` / Linear `META-9`.
Base: `86329987f94fa6402c76df6d594c25b8ebc71ef3`.
Branch: `dev/meta-9-protected-evaluation-h-ablation`.

## Charter gate

This slice advances product-loop stage 3 (rehearse and evaluate immutable
candidates) and gates stage 4 (managed scaffold `H` evolution). It must preserve:

- frozen first-order comparison: `E`, task cases, repetitions/seeds, runner,
  task-model/`W` identity, and budget stay identical while only scaffold `H`
  changes;
- evaluator non-self-approval: candidate-supplied data cannot write the protected
  verdict, activation state, or deployment pointer;
- full-fidelity evidence: decisions retain exact report, case, repetition,
  memory-receipt, metric, and lineage references;
- strict case-level protection: aggregate gains cannot cancel a mandatory-case
  regression;
- best protected over latest, bounded authority, human promotion, exact rollback,
  and honest termination.

## Existing anchors

- `MemoryCognitiveSkillSnapshot` is the immutable scaffold-policy snapshot and
  already carries a self-hash and optional parent snapshot hash.
- `MemoryActionReceipt` is the immutable action-evidence receipt.
- `EvaluationReport` preserves exact per-case/repetition results, immutable
  evaluator and workflow digests, totals, and sealed-holdout non-disclosure.
- `EvaluationReportStore` is create-only and crash-safe.
- `tests/adversarial/test_memory_skill_boundaries.py` contains strict-xfail
  META5-MEM-011, which requires a promotion boundary to reject evidence obtained
  by repeatedly evaluating the same search set without held-out evidence.

## Writable slice

Product and test files:

- `src/metaharness/evals/ablation.py`
- `src/metaharness/evals/artifact_store.py`
- `src/metaharness/evals/__init__.py`
- `src/metaharness/memory/promotion.py`
- `src/metaharness/memory/__init__.py`
- `tests/test_protected_h_ablation.py`
- `tests/adversarial/test_memory_skill_boundaries.py`
- `docs/architecture.md`

Run records:

- `.agents/meta9-definition.json`
- `.agents/meta9-build-spec.md`
- `.agents/meta9-review-brief-kimi.md`
- `.agents/meta9-review-brief-glm.md`
- `.review-store/meta9-kimi-review.txt`
- `.review-store/meta9-glm-5.2-review.txt`

No other path may change in the implementation commit. Workplan closeout and
session handoff records belong to later coordinator-only commits.

## Required design

### 1. Generic memory promotion-boundary primitive

Add `metaharness.memory.promotion` with:

- immutable strict `Evidence` containing at least `search_set_id`,
  `evaluation_count`, and `held_out_evaluation_count`;
- `SearchSetLeakageError`;
- `PromotionGate.decide(evidence)`.

The gate must reject any evidence with repeated evaluation of a search set and no
held-out evaluation. Invalid negative/fractional counts and empty identifiers must
fail validation. The returned value, if any, is an inert decision/evidence value;
the module must expose no active-pointer mutation, worker, evaluator, merge,
deployment, or credential authority.

Remove only META5-MEM-011's strict-xfail marker after its body passes unchanged in
substance. MEM-009 remains xfail.

### 2. Immutable three-cell scaffold-H campaign contract

Add strict immutable models in `metaharness.evals.ablation` for exactly these cells:

1. `no_external_memory`
2. `base_scaffold`
3. `optimized_scaffold`

The contract must bind:

- exact immutable `E` reference and digest;
- task/case-set digest;
- runner identity and task-model/portfolio/`W` identity;
- ordered repetition/seed schedule;
- a common token, cost, and wall-time envelope;
- the three candidate cells;
- base scaffold snapshot hash;
- optimized scaffold snapshot hash with parent equal to the base hash;
- exact rollback target equal to the base scaffold snapshot;
- required protected views.

Each cell must also carry an immutable, self-hashing
`ProtectedRunContextManifest` produced on the protected-evaluator side before
comparison. The manifest binds:

- cell name and exact blueprint/workflow refs and digests;
- the cell's exact scaffold-H snapshot hash (absent only for no-memory);
- exact evaluator ref/digest and case-set digest;
- runner identity plus a hash of the exact runner configuration;
- task-model portfolio and `W` snapshot refs/digests;
- ordered repetition/seed schedule;
- the common budget envelope;
- exact evaluation-report refs;
- protected evaluator authority identity; and
- its own content digest.

The evaluator must compare the three manifests and fail unless every frozen-axis
field is identical. The per-cell fields allowed to differ are only cell identity,
blueprint/workflow `H` surface, scaffold-H snapshot, evaluation-report refs, and
the manifest's own content digest. Task-model/portfolio/`W`, runner configuration,
seeds, repetitions, `E`, cases, budget, and protected evaluator authority must be
identical across all three manifests. They therefore have explicit per-cell
witnesses rather than one campaign-level assertion. A manifest is inert evidence,
never approval or activation authority.

`no_external_memory` has no memory snapshot and no memory receipts. Base and
optimized cells name their exact `MemoryCognitiveSkillSnapshot` hashes. Only `H`
may vary; mismatched evaluator, task set, runner, task-model/`W`, schedule, or budget
must fail closed.

Required protected views are:

- `approved_target`
- `transfer`
- `replay_retention`
- `privacy`
- `safety`
- `efficiency`

Every campaign must contain all six. Each evidence row binds one exact case and
view to the three cell outcomes, their exact immutable `EvaluationReport` refs and
case results, aggregate metrics derived from attempt metrics, and the relevant
memory-action receipt hashes. Mandatory cases are explicit.

Evidence-row construction must resolve every report ref through the immutable
`EvaluationReportStore`. It verifies report ID/content digest, split, evaluator
digest, runner identity, exact case membership, repetition count, and derived
metrics against the persisted report. Neither a caller-supplied ref nor copied
case data is a reference truth. `HAblationResultStore` must require the same report
store and repeat these cross-artifact checks before persistence.

Do not disclose holdout assertions, outputs, verifier details, or per-case digests
that existing `EvaluationReport` intentionally seals. The campaign may retain the
report ID/content digest and protected pass/fail outcome without copying sealed
payloads.

### 3. Protected comparison and decision

The evaluator-side comparison must derive, never accept from the candidate:

- per-cell/per-view pass rate;
- per-cell/per-view outcome variance across repetitions;
- per-cell/per-view token, cost, and latency totals;
- case-level optimized-versus-base deltas;
- improved approved-target case IDs;
- regressed mandatory case IDs;
- eligibility of the optimized `H`;
- whether the conditional `W_mem` lane is unblocked;
- closest protected result and unresolved gap when ineligible;
- the exact rollback snapshot.

Eligibility requires both:

1. at least one approved-target case improves from base to optimized; and
2. no mandatory case in any of the six required protected views regresses.

An equal result is not an improvement. An unverified result cannot count as an
improvement and must block eligibility when the case is mandatory. Aggregate gains
cannot compensate for one mandatory regression.

Repeated search-set evidence without held-out evidence must be rejected through the
memory promotion boundary before the campaign can become eligible.

The result is `eligible_pending_human_promotion` or `ineligible`; never `promoted`.
There must be no `promote`, `activate`, `deploy`, or active-pointer method.

### 4. Immutable persistence

Add a create-only `HAblationResultStore` using the existing immutable store
mechanics. A result must be self-hashing and persistence must reject:

- a content-digest mismatch;
- duplicate overwrite with different bytes;
- tampered report references;
- broken optimized→base parent lineage;
- rollback that does not resolve to the exact base snapshot;
- absent required views;
- budget mismatch or exceeded common envelope.

The same deterministic inputs must yield the same content digest. Volatile artifact
ID and creation timestamp, if present, must not affect that digest, matching the
existing `EvaluationReport` convention.

### 5. Demonstrated ablation fixture

Tests must build one complete deterministic three-cell campaign through the public
API. It must:

- use exact `EvaluationReport` fixtures with multiple repetitions;
- show base over no-memory on at least one case;
- show optimized over base on at least one approved target;
- retain all six required views;
- bind base and optimized memory receipt hashes;
- compute nonzero variance in at least one repeated stochastic fixture;
- produce `eligible_pending_human_promotion`;
- persist and reload byte-equivalently from `HAblationResultStore`.

This is a protected contract/evaluator integration proof, not a scientific claim
that a real model or production harness improved. Documentation must say so.

The META5 corpus entry for MEM-011 remains stale in this card: it still describes
`metaharness.memory.promotion` as absent. The file is outside the frozen path
reservation. The behavioral test becomes enforced here; a bounded follow-up must
update the machine-readable corpus metadata without expanding this card's owned
slice.

## Required negative tests

Tests-first coverage must prove rejection of:

1. repeated search-set evaluation with no held-out evidence;
2. missing any of the three cells;
3. missing any required protected view;
4. mismatched `E`, task set, runner, `W`, repetition/seed schedule, or budget,
   exercised through per-cell protected run-context manifests rather than a
   campaign-level assertion;
5. optimized snapshot not parent-bound to base;
6. rollback target not equal to base;
7. memory receipts on no-memory or absent required receipts on memory cells;
8. report-ref digest mismatch, wrong case ID, split/view mismatch, or sealed-data
   disclosure;
9. aggregate improvement with one mandatory regression;
10. equal/no-improvement outcome;
11. mandatory unverified outcome;
12. token/cost/wall envelope breach;
13. content-digest tampering and immutable-store overwrite;
14. any candidate-authored `promoted`/activation state or authority expansion.

## Exclusions

Do not:

- wire the memory broker into live workers, prompt assembly, or the Web API;
- implement learned ranking, a search optimizer, `W_mem` training, evaluator
  evolution, discovery scheduling, or automatic candidate generation;
- change `EvaluationReport` sealed-holdout semantics;
- reuse `optimization.CandidateLedger.promote`;
- create a promotion or deployment pointer;
- change dependencies, schemas outside this new artifact family, or unrelated
  tests;
- claim a real-model scientific win from deterministic fixtures.

## Verification

Focused:

```text
.venv/bin/python -m pytest -q \
  tests/test_protected_h_ablation.py \
  tests/adversarial/test_memory_skill_boundaries.py \
  tests/test_exact_eval_tuning.py \
  tests/test_memory_broker.py
```

Regression:

```text
.venv/bin/python -m pytest -q
node --test scripts/workplan.test.mjs
git diff --check 86329987f94fa6402c76df6d594c25b8ebc71ef3
```

The exact implementation diff must be committed and frozen. Herdr-driven Pi with
`kimi-coding/k3` reviews the plan and frozen diff. Pi with NeuralWatt GLM-5.2 then
performs the repository-mandated frozen-diff gate. Every finding receives an
`ACCEPT`, `ACCEPT-WITH-MOD`, `REJECT`, `DEFER`, or `NEEDS-EVIDENCE` disposition.
Any accepted fix requires a new commit and fresh reviews. No unresolved P0/P1 may
advance.

## Stop conditions

Stop and fail closed on:

- any product/test edit outside the writable slice;
- any live-memory/prompt/worker activation;
- any changed `W` or mutable evaluator;
- any candidate ability to approve, promote, activate, merge, deploy, widen
  permissions, or access secrets;
- any hidden holdout disclosure;
- any aggregate-only gate or mandatory-case regression;
- any non-deterministic content digest for fixed inputs;
- any unresolved P0/P1 reviewer finding;
- any focused, full, workplan, or diff-check failure.
