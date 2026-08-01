# META-34 build specification — real protected scaffold-H prerequisite

Base: `24a489134f9fd0e9a5780fbe3110a650f91ed013`

Atomic card: `TASK-20260726-026`, definition
`sha256:725e6fa9cb67b2a80687fd046d645f7fdd815064b37bf1de26570e9e2336d712`.

## Role and authority

You are the implementation builder. Edit only the reserved paths recorded in
`.agents/meta34-definition.json` and the atomic card. Do not commit, push,
mutate Linear/workplan state, write episodic memory, install dependencies, run
the real protected campaign, inspect `/private/tmp/meta34-protected`, or touch
the primary worktree. Tests and source edits are allowed in this claimed
worktree. The orchestrator owns the campaign spec, protected inputs, commits,
review, integration, and acceptance.

This change advances product-loop stages 1 and 3 and prepares a conditional
stage-4 gate. Preserve evidence-before-learning, full-fidelity evaluation,
frozen one-axis attribution, evaluator non-self-approval, bounded authority,
best-protected-over-latest, exact rollback, and honest termination.

## Required implementation

### 1. Memory-aware inference wrapper

Add a narrowly scoped Runner wrapper in
`src/metaharness/harness/memory.py`, exported from `harness/__init__.py`.

- The wrapper owns an inner `Runner`, one exact
  `MemoryCognitiveSkillSnapshot`, one `MemoryActionBroker`, and an explicit
  record-content resolver supplied by the caller.
- Before the task-model call, issue a deterministic CONSULT using the task
  objective and visible inputs. Resolve only the receipt's selected target IDs.
  Render bounded, labeled memory excerpts into a deep copy of `Task.advice`.
  Advice remains generated/untrusted-derived; never place memory in boundaries,
  system instructions, tools, output schema, or hidden inputs.
- After the inner result returns, optionally issue a deterministic LOG for
  caller-supplied observation content. The wrapper must not infer that success
  makes a memory action correct. Default to no LOG unless an explicit
  observation selector returns governed content.
- Emit/retain immutable CONSULT/LOG receipts. Never rewrite the inner result's
  domain output, grant a task action through the broker, or mint lifecycle,
  promotion, evaluator, deployment, credential, or permission authority.
- No-memory mode must make no broker call and inject no memory.
- Fail closed or fall back exactly as the frozen snapshot declares when
  retrieval/receipt/content resolution is invalid. Secret-like content must not
  enter advice.
- The H axis must be load-bearing: changing the snapshot/retrieval policy must
  be able to change selected memory and therefore model-visible advice.

### 2. Protected campaign contracts and CLI

Add `src/metaharness/evals/h_campaign.py`, exported narrowly from
`evals/__init__.py`.

Build the smallest generic execution/verification surface that consumes a
pre-frozen JSON spec and separately supplied protected input:

- strict, frozen, self-hashing models for the goal-family/case/run
  precommitment, including exact local model ID+digest, base URL limited to
  loopback or the pinned local runtime host, inference parameters, evaluator ref+digest+authority, W refs,
  environment digest, case split/view/mandatory/approved-target membership,
  repetition seeds, per-cell equal budgets, a development-only optimized-H
  selection declaration, base+optimized snapshot hashes, and rollback hash;
- reject any spec whose optimized snapshot is not a child of base, whose
  rollback is not base, whose non-H fields differ across cells, whose required
  views/canonical three cells are incomplete, whose protected input digest does
  not match, or whose approved/mandatory/holdout designations are missing;
- `run` must execute actual `OpenAICompatWorker` calls through the new wrapper,
  preserve raw response/WorkerResult metadata and memory receipts, score using
  only deterministic pre-registered checks, construct/store real
  `EvaluationReport`s, derive existing protected-H rows/result through
  `evaluate_protected_h_ablation`, and write create-only evidence beneath an
  explicit evidence root;
- `verify` must perform no model call. It reloads the spec, public evidence
  manifest, reports/result, re-resolves every digest/ref, re-derives the
  protected verdict, and fails on mismatch;
- sealed holdout is one-shot: its inputs arrive only through the separately
  supplied protected file, never appear in the public spec/evidence projection,
  and are never available to development selection. Raw protected evidence may
  remain only in the protected evidence root; the repo artifact is a
  secret-safe digest/projection.
- terminal statuses are only `eligible_pending_human_promotion` or
  `ineligible` with closest protected result/unresolved gap. No method may
  promote, activate, deploy, mutate an active pointer, or start W.

Prefer composition with existing `EvaluationReportStore`,
`HAblationResultStore`, `ProtectedHAblationCampaign`,
`build_protected_evidence_row`, and `evaluate_protected_h_ablation`. Do not
duplicate or weaken their validation.

### 3. Tests

Add `tests/test_real_h_campaign.py` and only necessary additive adversarial
coverage in the reserved existing file. Tests must be hermetic with fake
Runners; they must not call Ollama/OpenRouter.

Minimum negative/behavioral coverage:

- no-memory mode performs no broker call and injects no advice;
- CONSULT selected content appears only in `Task.advice`, with receipts bound;
- inner task output remains authoritative and unmodified;
- governed LOG is opt-in and produces an immutable receipt; raw task success
  alone does not auto-label/log;
- secret/cross-scope/unresolved selected records fail closed without leakage;
- removing/bypassing the wrapper makes the three cell inputs byte-identical and
  the load-bearing campaign guard rejects the run;
- malformed/changed model digest, evaluator, W ref, environment, seed, budget,
  case/view/mandatory set, parent/rollback, or protected-input digest rejects
  before inference;
- optimized policy selected with validation/holdout evidence rejects;
- aggregate gain cannot hide one mandatory regression;
- verify is model-call-free and rejects any edited response, receipt, report,
  manifest, or result;
- API/CLI exposes no promotion/activation/deployment/W-start surface.

### 4. Documentation and artifacts

Update the bounded architecture section to distinguish:

- META-9 contract/fixture;
- META-34 real-execution surface and evidence gate;
- a real run's actual status (which the orchestrator will fill after the run);
- the required later human promotion decision before META-10.

Do not claim the campaign passed, that W is implemented, or that META-10 is
authorized.

The orchestrator will prepare `.agents/meta34-campaign-spec.json`,
`.agents/meta34-evidence-manifest.json`, the GLM brief, and review artifact
after the implementation and real run. You may leave those reserved paths
untouched except for this build spec and definition.

## Verification

Run the focused tests you add plus:

```text
.venv/bin/python -m pytest -q tests/test_real_h_campaign.py tests/test_protected_h_ablation.py tests/test_memory_broker.py tests/adversarial/test_memory_skill_boundaries.py
git diff --check 24a489134f9fd0e9a5780fbe3110a650f91ed013
```

Do not run the full suite until the focused slice is green. Report changed
files, exact commands/results, and any unresolved blocker. Do not commit.
