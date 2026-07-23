# META-17 Fix Brief 2 — GLM-5.2 gate findings on frozen head 85c6701

Gate artifact: `.review-store/meta17-glm-gate-review-85c6701.txt` (verdict REJECT, 5 P1 /
10 P2 / 9 P3). Dispositions below are the implementing seat's classification; apply every
ACCEPT / ACCEPT-WITH-MOD with a citing test. Same §1 writable set, same hard rules (no
commit/stash/push, no memory/Linear/workplan writes). The build spec remains the contract.

## P1s — all ACCEPT

**F2-1 (P1, testgen.py:242,257 — ACCEPT).** Unbalanced parens in the approval and
failure-path render templates make every suite containing those kinds a SyntaxError
(reproduced by the orchestrator: `compile()` of a rendered approval suite fails). Fix the
templates; add citing tests that render plans with approval AND failure-path kinds and
`compile()` the output.

**F2-2 (P1, gate.py:224-232 — ACCEPT).** D7 inversion: the old active record is
superseded BEFORE the new one activates (zero-active crash window). Reorder: activate the
new record first, then supersede the old. Fix the swap test to assert D7 order (activate
→ supersede), not the reversed one.

**F2-3 (P1, gate.py receipt lineage — ACCEPT-WITH-MOD).** Records get ad-hoc
`receipt_id` labels (`rejected:/activated:/superseded:<hash16>`) while the
CrossValidationReceipt self-hashes afterwards — record→receipt binding is severed.
Restructure every gate path as: (1) construct the CrossValidationReceipt first
(receipt_id="" → self-hash); (2) perform registry transitions using
`receipt.receipt_id` (a swap's supersede transition uses the SAME activation receipt id —
that receipt produced both transitions); (3) journal the FULL receipt (all fields, not
just its id) in the provenance event. Citing test: after activation,
`registry.active_for(id).receipt_id` equals the returned receipt's id and the provenance
event carries the full receipt.

**F2-4 (P1, doctor.py:230-232 — ACCEPT).** FIX-8 not implemented: bare
`except Exception: pass` remains, and its citing test exercises the dangling-entry branch
instead (masking). Replace with `Finding("executor.unverifiable", where, cause,
fixable=False)`; rewrite the citing test so the entry file EXISTS but is unparseable /
procedure-uncomputable, and assert `executor.unverifiable`.

## P2s

**F2-5 (gate stale check gated behind `if active:` — ACCEPT).** Make the current-entry
check unconditional: entry missing/unreadable/hash-mismatch → rejected receipt, no
sandbox, regardless of any active record.

**F2-6 (runtime generic-except implicates the entry on host bugs — ACCEPT-WITH-MOD).**
A `step_handler` exception is a HOST failure, not entry evidence: return
`RunResult(status="error", outcomes=(), completed_steps=<from _COMPLETED>)` and journal
`runtime.handler-error`. Only `StepCheckFailed` produces fail+implicated TaskOutcome.
Citing test: handler raising RuntimeError → outcomes == (), no marks feed possible.

**F2-7 (D6: compile / test-author / quarantine not journaled — ACCEPT).**
`WorkflowCompiler.compile` and `WorkflowTestAuthor.author_suite` take optional
`provenance` + `clock` and append `compile` / `test-author` events (entry_id, spec_hash,
executor/suite hash, compiler/author id, identity basis, timestamp) when bound.
`ExecutorRegistry` takes an optional `provenance` + `clock`; adding the quarantined
record appends a `quarantined` event. Citing test with a fake ProvenancePort.

**F2-8 (gate never re-checks suite-author identity — ACCEPT).** In `evaluate`, before
the sandbox: `suite.author_id == COMPILER_ID` OR empty `suite.identity_basis` →
rejected receipt ("suite author identity collision / unrecorded basis"), no sandbox run.
Citing test: construct `IndependentTestSuite(author_id=COMPILER_ID, ...)` directly →
rejected, zero sandbox calls.

**F2-9 (registry corrupt file returns [] — ACCEPT).** Read paths raise `RegistryError`
on an unparseable registry file (absent file = empty, never created on read).

**F2-10 (order-test expect parsing — ACCEPT).** `expect` for order tests: try
`json.loads` (JSON array) else comma-split; validate every id ∈ spec step ids
(violation → TestAuthorError); render the list via json.dumps. Citing test:
`expect="s1,s2"` renders a list literal that compares equal to executed order.

**F2-11 (sandbox double is a no-op — ACCEPT).** Add one end-to-end test whose
ExecutionPort double REALLY executes: exec `executor_source` (same restricted-globals
helper as the runtime), exec `test_source` with a `load_executor` wrapper, return
`ExecutionResult(ok=..., output=...)`. Route the happy-path gate test through it so the
rendered suite + executor actually run.

**F2-12 (injection probe never execs — ACCEPT).** Extend the compiler injection probe:
exec the generated source and prove the hostile objective payload stays an inert string
in STEPS (no code effect).

**F2-13 (orphan executor sources undetectable — ACCEPT-WITH-MOD).** True two-file
transactions don't exist; add a doctor check `executor.orphan-source` (fixable=False)
for `executors/<id>/<hash>.py` files with no registry record. Document the crash window
in registry.py.

## P3s accepted into this round (cheap)

**F2-14.** `_journal_refusal`: use the run's injected `now`, not `self.clock()` (single
clock per run).
**F2-15.** gate: reuse `self.registry.store` instead of constructing a new `PackStore`
(mkdir side effect); move the `execution is None` GateError to the TOP of evaluate and
journal precondition failures as `gate.error` events.
**F2-16.** runtime: drop `id` from SAFE_BUILTINS (leaks addresses); add an AST preflight
before exec — reject sources referencing dunder attributes other than `__init__` or any
use of `__builtins__` (machine-templated output never needs them; blocks the
`().__class__.__bases__` escape class). Citing test: hand-written source containing
`().__class__.__bases__[0].__subclasses__()` is refused before exec.
**F2-17.** testgen `__init__`: convert a raising `identity.distinct` into
TestAuthorError (wrap, keep cause).
**F2-18.** compiler: non-JSON-serializable check values raise `CompilerError`, not bare
TypeError.
**F2-19.** Clean the happy-path test's redundant source rewrite / active→active
transition; rely on gate wiring only.

## P3 dispositions (recorded, no code)

- `_COMPLETED` records a step after its checks pass (not at handler invocation):
  REJECTED as a defect — that IS the completion semantics; document in runtime docstring.
- Soft-whitelist residual vectors beyond F2-16 (`getattr` chains on allowed builtins):
  DEFERRED — generated code is machine-templated from escaped contract data; the
  sandbox + strict approval is the containment boundary; note in runtime docstring.

## Verify

After fixes: the three compilation test files, full `selflearn/tests`, root `tests`,
`node --test scripts/workplan.test.mjs`, `git diff --check e487dba`. Report real numbers.
