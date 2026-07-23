# META-17 Independent Frozen-Diff Review (AGENTS.md gate) — head 4a8069b

You are the independent frozen-diff reviewer (read-only). You MUST NOT edit,
stage, commit, or run any mutating command. Every finding requires
file-and-line evidence and a P0/P1/P2/P3 severity. Finish with an explicit
verdict: APPROVE, APPROVE-WITH-MOD, or REJECT.

## Frozen commits

- Base (last APPROVED head): `89572ff`
- Head (review THIS immutable commit): `4a8069b`
- Repo/worktree: `/private/tmp/meta-harness-meta-17`
- Branch: `dev/meta-17-workflow-compilation`

Review exactly `git diff 89572ff..4a8069b` (equivalently `git show 4a8069b`).
Do not re-litigate anything settled at or before `89572ff`.

## Why this review exists

`89572ff` received a GLM-5.2 gate verdict of **APPROVE** (0 P0, 0 P1, 4 P2
non-blocking, 5 P3). `4a8069b` was committed AFTER that approval as a "P2 tidy"
and was never separately reviewed. It is the current head of PR #55.

It is labelled a tidy, but it is not purely cosmetic — it adds a new runtime
security control. Review it on its merits, not on its label.

Prior artifact for context (do not re-review it):
`/Users/charltondho/Developer/projects/meta-harness/.review-store/meta17-glm-gate-review-89572ff.txt`

## What 4a8069b contains

Two distinct changes:

**(1) A path-containment guard** in
`selflearn/src/selflearn/compilation/runtime.py` (~line 182): before loading
executor source, resolve both the store root and the executor path and refuse if
the executor path is not relative to the store root, journalling an
`executor.path-escape` refusal. `OSError` during validation is also converted
into a journalled refusal plus `RuntimeCompError`.

**(2) A rename**: `TestAuthorError` → `WorkflowTestAuthorError` across
`testgen.py`, `compilation/__init__.py`, `selflearn/__init__.py`, and tests,
with a backward-compatible module-level alias `TestAuthorError =
WorkflowTestAuthorError` deliberately kept OUT of `__all__`.

Plus new tests in `selflearn/tests/test_compilation_runtime.py` and updates in
`selflearn/tests/test_compilation_gate.py`.

## Focus areas — be adversarial about these specifically

1. **Is the path-containment guard actually sound?** `Path.resolve()` +
   `is_relative_to()` — consider symlinks (resolved on both sides?), the store
   root itself being a symlink, `..` traversal, absolute `active.path` values,
   empty/`.`/whitespace paths, and TOCTOU between the containment check and the
   subsequent `exec_path.exists()` / read. Does the guard run before every read
   of executor source, or is there a second load path that bypasses it?
2. **Is the guard in the right place?** It sits in `ExecutorRuntime`. Is there
   any other consumer that reads `registry.path` (gate, registry, doctor) and
   remains unguarded — i.e. is this a complete fix or a point patch?
3. **Refusal journalling.** Confirm `_journal_refusal` is called with a
   consistent reason code and that the refusal path cannot itself raise before
   journalling. Confirm the raise is fail-closed in every branch.
4. **The backward-compatible alias.** `TestAuthorError` is kept as a module
   alias outside `__all__`. Is that a genuine compatibility affordance or dead
   code? Does anything still reference the old name? Does keeping it create two
   catchable names for the same condition in a way that could let a caller's
   `except` clause miss? Note it is exported from `testgen` but removed from the
   package `__all__` — assess whether that split is coherent.
5. **Does the rename miss any site?** Docstrings, error messages, comments, and
   any string-based references.
6. **Test quality.** Do the new runtime tests actually exercise the escape path
   with a genuinely escaping path (not merely a nonexistent one), and would they
   FAIL if the guard were deleted?

## Charter invariants that must hold

- Bounded authority — generated executors are quarantined by default and the
  runtime must never widen its own read surface.
- Evaluator non-self-approval — the test author must remain identity-distinct
  from the compiler and must never see executor bytes.
- Full-fidelity evidence — refusals are journalled, not silently swallowed.
- Honest termination — fail closed on any ambiguity.
- Reversible lineage — executors stay content-hash-bound to their procedure.

## Test evidence claimed at 4a8069b (verify, do not trust)

- compilation tests: **107**
- selflearn suite: **331 passed + 1 env-skip (pypdf)**
- root suite: **1631 passed, 1 skipped, 2 xfailed**
- `node --test scripts/workplan.test.mjs`: 115/115 (pre-dates the later
  host-alias change; not part of this diff)
- `git diff --check e487dba`: clean

Run selflearn tests with the selflearn venv if present, otherwise
`/Users/charltondho/Developer/projects/meta-harness/.venv/bin/python -m pytest`.

## Required output

1. Verdict: APPROVE / APPROVE-WITH-MOD / REJECT.
2. Findings, each with severity (P0/P1/P2/P3), `file:line` evidence, and a
   concrete failure scenario. No finding without evidence.
3. An explicit statement, per charter invariant above, whether it holds.
4. Explicitly state whether `4a8069b` regresses anything that was approved at
   `89572ff`.
