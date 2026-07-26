# META-33 frozen-diff review brief

You are the mandatory independent second-opinion reviewer for Meta-Harness
META-33 / `TASK-20260726-025`.

Review the immutable diff:

- Base: `b3d538974dd6bb24e30fcf23a31a2d4e48ecb79d`
- Head: `8bc19f18d654621d0b6c480739e849f9daa99fde`
- Command: `git diff b3d538974dd6bb24e30fcf23a31a2d4e48ecb79d..8bc19f18d654621d0b6c480739e849f9daa99fde`

Read-only review only. Do not edit files, create files, commit, push, write
episodic memory, mutate Linear/workplan state, install dependencies, or run
destructive commands.

## Acceptance criteria

1. `tests/fixtures/meta5/corpus.json` records
   `repeated-set-promotion-gate` as enforced, clears the obsolete outstanding
   requirement ID under the corpus convention, and accurately names/describes
   the shipped narrow inert `PromotionGate`.
2. `tests/adversarial/test_memory_skill_boundaries.py` no longer counts
   META5-MEM-011 among absent contracts or claims the promotion module is
   unimplemented.
3. A focused assertion makes the corpus status and enforced MEM-011 behavior
   disagree loudly if stale absence bookkeeping returns.
4. MEM-009 remains the only genuinely absent strict-xfail contract.
5. No production code or META-9 protected evaluation, eligibility, pending
   human promotion, activation, deployment, evaluator, merge, credential, or
   runtime authority changes.
6. Diff remains confined to the two requested test/fixture files.

## Charter invariants

This correction advances product-loop stage 3 (rehearse and evaluate) by making
the evaluator corpus accurately represent shipped evidence boundaries. Preserve
full-fidelity evidence, evaluator non-self-approval, determinism-is-not-
correctness, bounded authority, human promotion, and honest capability claims.
Do not treat bookkeeping as scientific or production evidence.

## Test evidence

From the frozen source head with
`PYTHONPATH=/private/tmp/meta-harness-meta33/src` and the repository Python 3.14
environment:

`pytest -q tests/adversarial/test_memory_skill_boundaries.py tests/adversarial/test_context_invalid_inputs.py tests/test_protected_h_ablation.py`

Result: `48 passed, 1 xfailed in 0.83s`. The xfail is the intentionally absent
MEM-009 training-target contract. `git diff --check` against the exact base was
clean.

## Required output

Return:

- Verdict: `APPROVE` or `REJECT`.
- Findings classified `P0`, `P1`, `P2`, or `P3`.
- File-and-line evidence for every finding.
- Explicit confirmation whether any P0/P1 exists.
- Note any limitation or test-evidence gap.

Do not infer edits outside the frozen diff and do not accept claims without
grounding them in the cited files.
