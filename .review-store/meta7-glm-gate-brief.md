# META-7 Mandatory Independent Second-Opinion Review (AGENTS.md gate)

You are the independent frozen-diff reviewer (Pi / NeuralWatt GLM-5.2,
read-only). You must not edit, stage, commit, or run any mutating command.
Every finding requires file-and-line evidence and a P0/P1/P2/P3 severity.
Finish with an explicit verdict: APPROVE, APPROVE-WITH-MOD, or REJECT.

## Frozen commits

- Base: b13a2fecb53b7372b5d46a0b94254e820034ef45 (main)
- Head (review THIS immutable commit): c6b25ed28950a36ae9db30a6fdbf0ec25d7beb9b
- Branch: dev/meta-7-discovery-kernel, worktree /private/tmp/meta-harness-meta-7
- Scope: exactly 12 new files — src/metaharness/discovery/{__init__,models,
  lineage,knowledge,contexts,supervisor}.py and six discovery test modules.
  Review `git diff b13a2fe...c6b25ed` (equivalently `git show c6b25ed`).

## What this implements (card TASK-20260714-005 / Linear META-7)

H-only isolated discovery MVP advancing charter product-loop stages 2–4:
frozen self-hashed campaign/attempt/lineage/knowledge models; exact-parent
lineage git worktrees with durable boundary-carrying receipts and
quarantine-first replay recovery; append-only scoped knowledge hub with
issuance-verified scope provenance (supervisor is the only assignment issuer);
role-specific context manifests with cross-lineage use receipts; bounded FIFO
campaign supervisor with durable intent/outcome journaling, a 13-row
producer/evidence table driving terminal replay validation, round-currency and
contradiction guards, and exactly-once terminal receipts.

## Charter invariants that must hold (docs/PROJECT_CHARTER.md)

Evaluator non-self-approval; bounded authority (no promotion/deploy/evaluator-
write/memory-activation/weight-training/permission-expansion authority
constructible); full-fidelity evidence (honest over-budget wall times never
clamped); reversible lineage (durable receipts are truth; recovery quarantines
rather than guesses/deletes); honest termination (no claimed capability that
is not implemented; proxy-only evidence explicitly labeled); fail-closed
validation before any external or query-visible mutation; E and W frozen; no
existing product file changed.

## Acceptance evidence at this exact commit

Focused discovery suite 365 passed; context/memory regression slice 245
passed; full repository suite 1697 passed, 2 xfailed (the intentional
META5-MEM-009/011 strict xfails); node workplan suite 115/115;
`git diff --check` clean. Five pre-commit panel rounds (kimi-k2.7-code,
GLM-5.2, codex, MiniMax-M3 behavioral probes) with all P0/P1 findings fixed
and re-verified; artifacts under .review-store/meta7-*-{8,9,10,11,12}*.

## Known accepted limitations (deliberate, documented in module docstrings —
assess whether each is acceptable, but do not treat documentation itself as a
defect): composer identity params (compose_explorer/optimizer_context) are
caller-asserted pending a live renderer; issuance verifiers prove issuance,
not cryptographic non-repudiation, against in-process attackers;
_commit_worktree cannot un-create a git commit when post-commit validation
fails (durable state is protected, worktree reconciliation is the caller's).

## Deliverable

Verdict + ordered findings (P0..P3, file:line, concrete failure scenario each,
CONFIRMED/PLAUSIBLE). State explicitly if there are no P0/P1 findings.
