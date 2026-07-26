# META-9 Kimi K3 frozen-diff review brief

You are the user-selected independent read-only reviewer. Do not edit or create
files, commit, push, write memory, mutate the workplan or Linear, access secrets,
or run destructive commands.

Repository: `/private/tmp/meta-harness-meta-9`
Card: `TASK-20260714-007` / Linear `META-9`
Base commit: `86329987f94fa6402c76df6d594c25b8ebc71ef3`
Frozen head commit: `d70412b9b9d45404459443ed2b85a75fd535e826`
Diff: `86329987f94fa6402c76df6d594c25b8ebc71ef3..d70412b9b9d45404459443ed2b85a75fd535e826`
Frozen definition: `.agents/meta9-definition.json`
Build contract: `.agents/meta9-build-spec.md`

Read `AGENTS.md`, `docs/PROJECT_CHARTER.md`, the frozen definition and build
contract, then review only the exact committed diff above. Working-tree review
briefs and review artifacts are outside the frozen implementation and are not
part of the diff. Do not rely on an unstaged or moving worktree.

Acceptance criteria:

1. Exactly three cells compare no external memory, base scaffold, and optimized
   scaffold `H`; evaluator, cases, runner/config, portfolio/`W`, ordered
   repetitions/seeds, budget, and protected evaluator authority are frozen.
2. Per-cell self-hashing manifests bind exact blueprint/workflow/scaffold
   snapshots and immutable evaluation reports; optimized lineage names base and
   rollback is the exact base snapshot.
3. All approved-target, transfer, replay-retention, privacy, safety, and
   efficiency views retain exact case/repetition/report/receipt/metric evidence
   without disclosing sealed holdout assertions, outputs, digests, or verifier
   details.
4. The evaluator derives summaries, variance, deltas, eligibility, conditional
   `W_mem` status, closest protected result, unresolved gap, and rollback.
   Caller-authored decision fields, mandatory downgrades, nested mutation,
   report-ref tampering, budget breaches, missing views/cells, lineage drift,
   equal outcomes, unverified mandatory outcomes, and aggregate compensation for
   a mandatory regression fail closed.
5. Eligibility means at least one approved-target case improves and no mandatory
   case in any required view regresses or is unverified. Status is pending human
   promotion only; no promotion, activation, deploy, worker, prompt, Web API, or
   pointer authority is introduced.
6. Repeated search-set evaluation without held-out evidence is rejected.
7. The result is deterministic, deeply immutable across nested refs/metrics,
   self-hashing, create-only, and re-resolves every report before persistence.
8. The deterministic fixture is an integration proof, not a real-model
   scientific improvement claim.

Charter invariants under review: stages 3–4; evidence before learning;
full-fidelity evaluation; frozen H/E/W comparisons; evaluator non-self-approval;
best protected over latest; bounded authority; exact rollback; honest
termination.

Test evidence on the frozen head:

- Focused: `95 passed, 1 xfailed in 1.29s`
- Full: `1796 passed, 4 skipped, 1 xfailed, 733 warnings in 162.93s`
- Workplan: `155 passed, 0 failed`
- `git diff --check 86329987f94fa6402c76df6d594c25b8ebc71ef3`: clean

Look especially for trust-boundary bypasses, self-hash or nested-mutation holes,
caller-forged mandatory/decision state, incomplete report re-resolution,
holdout leakage, non-frozen axes, incomplete budget accounting, invalid
eligibility logic, false authority, store corruption/overwrite gaps, and tests
that merely restate the implementation.

Return exactly:

```text
VERDICT: APPROVE | REVISE

FINDINGS:
- P0|P1|P2|P3 <short title>
  Evidence: <file:line and observed contract>
  Impact: <concrete failure>
  Required change: <minimal correction>

NO-FINDINGS: <state "none" if there are no findings>
```

Every finding requires file-and-line evidence. Do not report style preferences.
