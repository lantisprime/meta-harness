# META-8 Independent Frozen-Diff Review (AGENTS.md gate) — head 3c14884

You are the independent frozen-diff reviewer (read-only). You MUST NOT edit,
stage, commit, or run any mutating command. Every finding requires
file-and-line evidence and a P0/P1/P2/P3 severity. Finish with an explicit
verdict: APPROVE, APPROVE-WITH-MOD, or REJECT.

## Frozen commits

- Base (last gated head): `439321b39a89f0e1e9a4b0ab0e1a1e0f8a2b1c3d` — use the
  actual ref `439321b`.
- Head (review THIS immutable commit): `3c14884`
- Repo/worktree: `/private/tmp/meta-harness-meta-8`
- Branch: `dev/meta-8-scheduling-heartbeats-policy`

Review exactly `git diff 439321b..3c14884` (equivalently `git show 3c14884`).
Do not review the moving worktree, and do not re-litigate anything already
settled at or before `439321b`.

## Why this review exists (important context)

`439321b` received a GLM-5.2 re-review with verdict **APPROVE-WITH-MOD**: all 6
Codex P1s and all 7 GLM P2/P3s were confirmed genuinely fixed. That reviewer
raised one new P2 observation (the F3 denominator / batch-concentration metric)
and asked the implementing seat to classify P2-1 and the P3s.

`3c14884` was then authored by the implementing seat but **never committed** —
it was found uncommitted in the worktree during an unrelated control-root
recovery and committed unedited by a different session. It has therefore never
been reviewed by anyone. That is the entire purpose of this gate.

Prior artifacts you may read for context (do not re-review them):
`.review-store/meta8-codex-review-3290a35.txt`,
`.review-store/meta8-glm-gate-review-3290a35.txt`,
`.review-store/meta8-glm-rereview-439321b.txt` (all in
`/Users/charltondho/Developer/projects/meta-harness`).

## What 3c14884 claims to do

Introduce an explicit root-activation bootstrap in the policy-evolution path:

- `PolicyActivationReceipt` gains an `is_root: bool = False` field with
  validator-enforced invariants: `is_root=True` requires
  `parent_policy_hash is None` and `activated_sequence == 0`; `is_root=False`
  requires a non-None `parent_policy_hash`.
- `SearchPolicyEvolver` mints exactly one `is_root=True` bootstrap activation at
  construction, built from four synthetic PASSED `PolicyValidationReceipt`s
  (SCHEMA/STATIC/SIMULATION/SHADOW) with a zero descriptor hash.
- That bootstrap receipt is exposed via a separate `root_activation` property
  rather than being appended to `activation_receipts`, explicitly so existing
  tests' expectations about the `activation_receipts` sequence stay unchanged.
- Accompanying changes in `heartbeat.py`, `scheduler.py`, and four test modules.

## Focus areas — be adversarial about these specifically

1. **Synthetic PASSED validation receipts.** The bootstrap mints four receipts
   asserting SCHEMA/STATIC/SIMULATION/SHADOW all PASSED without any validation
   actually running. Does this create a path by which a policy can obtain
   activation authority it did not earn? Can `root_activation` (or its
   contained receipts, or their hashes) be replayed, injected, or otherwise
   used to satisfy the four-receipt activation gate for a NON-root policy?
   Trace `validation_receipt_hashes` reuse carefully.
2. **The zero descriptor hash** (`sha256:` + 64 zeros) on the SIMULATION and
   SHADOW bootstrap receipts. Is a zero/sentinel descriptor distinguishable
   everywhere it matters from a real descriptor, or can it collide with or
   masquerade as a validated population descriptor?
3. **Deliberately hiding the receipt from `activation_receipts`.** The code
   comment states the receipt is kept out of that sequence so pre-existing test
   contracts remain valid. Assess this honestly: is the separate `root_activation`
   property a sound design boundary, or is it test-shaped concealment that leaves
   an audit trail incomplete — i.e. is there any consumer, receipt chain,
   rollback path, or doctor/audit sweep that walks `activation_receipts` and will
   now silently miss the root activation?
4. **Rollback / lineage integrity.** Does an `is_root` activation interact
   correctly with rollback eligibility and the `_known_snapshots` bootstrap? Can
   a rollback target the root in a way that was previously impossible?
5. **Hash binding.** `is_root` is a new regular (non-excluded) field — confirm it
   is actually bound into `activation_hash` and that no receipt now binds fewer
   fields than it claims to.
6. **Determinism.** Identical inputs must produce identical bytes. Confirm no
   time, randomness, uuid, or set/dict-ordering dependence enters via the new code.

## Charter invariants that must hold

- Evaluator non-self-approval — no component may self-approve; generated or
  bootstrapped artifacts must not gain activation, promotion, or evaluator
  authority.
- Bounded authority — heartbeats and policy candidates produce untrusted
  candidates and proposals only, never authority changes.
- Full-fidelity evidence and reversible lineage — receipts are self-hashing,
  append-only, and complete enough to reconstruct what happened.
- Honest termination — fail closed; no silent swallowing of rejections.
- H-only change: `E` and `W` remain frozen. No edits outside
  `src/metaharness/discovery/` and the four owned test modules.

## Test evidence at 3c14884 (verify these claims, do not trust them)

- Discovery slice (`tests/test_discovery_scheduling.py`,
  `tests/test_discovery_policy.py`, `tests/test_discovery_heartbeats.py`,
  `tests/adversarial/test_discovery_policy_boundaries.py`): **140 passed**
- Full suite: **1837 passed, 2 xfailed**
- `git diff --check`: clean

Run commands with:
`/Users/charltondho/Developer/projects/meta-harness/.venv/bin/python -m pytest`

Assess whether the NEW tests in this diff genuinely constrain the new behavior
or merely assert it. Specifically: is there a test that would FAIL if the
`is_root` invariants were removed, and one that would FAIL if a bootstrap
receipt were accepted as a non-root activation?

## Required output

1. Verdict: APPROVE / APPROVE-WITH-MOD / REJECT.
2. Findings, each with severity (P0/P1/P2/P3), `file:line` evidence, and a
   concrete failure scenario. No finding without evidence.
3. An explicit statement, per charter invariant above, whether it holds.
4. Explicitly state whether `3c14884` regresses anything that was fixed at
   `439321b`.
