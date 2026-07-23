# META-17 Fix Brief 3 — GLM-5.2 gate round 2 findings on frozen head ffe33cc

Gate artifact: `.review-store/meta17-glm-gate-review-ffe33cc.txt` (REJECT, 0 P0, 1 P1,
5 P2, 4 P3). Invariants confirmed holding; the findings are tightening. All dispositions
ACCEPT. Same §1 writable set, same hard rules (no commit/stash/push, no
memory/Linear/workplan writes, stdlib only). Build spec remains the contract.

## P1

**F3-1 (doctor.py orphan path-base + missing test — ACCEPT).** `recorded_paths` holds
store-root-relative paths (`<pack>/executors/<id>/<hash>.py`, as written by
`registry.write_candidate`) but the disk walk compares `str(source_file.relative_to(pack_dir))`
→ `executors/<id>/<hash>.py`, so the membership test is ALWAYS False → every registered
executor is falsely flagged `executor.orphan-source` and `DoctorReport.ok` is False on any
pack that has gone through activation. Fix the comparison to use the SAME base (e.g.
`relative_to(pack_dir.parent)` so both sides are `<pack>/executors/...`). Add the
previously-absent citing test: a pack with a registered AND activated executor run through
`run_doctor` returns NO orphan finding and `report.ok is True` (the spec §5
"leaves a clean pack silent" case).

## P2

**F3-2 (gate "unregistered candidate" guard is dead code — ACCEPT-WITH-MOD).**
`evaluate` calls `registry.write_candidate(candidate)` (which atomically adds the
quarantined record) BEFORE looking up the quarantined record, so the `if record is None`
guard is unreachable and its citing test is absent. Per the invariant the gate must
satisfy — activation binds to a registry record whose spec_hash + executor_hash match the
candidate — adopt the simpler design: **evaluate registers the candidate as quarantined
(`write_candidate`, idempotent) and then transitions THAT record**, and REMOVE the dead
"unregistered → rejected" guard and its unreachable-test requirement. Document in the
gate docstring that evaluate registers-then-evaluates (no silent activation of an unbound
candidate occurs; the hash-bound record IS the binding). Add a test that proves the binding:
activation's `registry.active_for(id).executor_hash == candidate.executor_hash` and
`.spec_hash == candidate.spec_hash` (already partially tested — make it explicit as the
substitute for the removed guard).

**F3-3 (AST preflight misses getattr-dunder escape — ACCEPT).** The preflight blocks
`ast.Attribute` dunder access but not `getattr(obj, "__bases__")` (a Call with a string).
Generated compiler output never calls `getattr`/`hasattr`, so DROP `getattr` and
`hasattr` from SAFE_BUILTINS entirely (closes the string-call vector) and keep the
preflight rejecting dunder-Attribute access. Citing test: hand-written source containing
`getattr(type(()), "__bases__")` or `().__class__.__subclasses__()` is refused (NameError
or preflight rejection) BEFORE any execution.

**F3-4 (approval step_id not validated — ACCEPT).** In `WorkflowTestAuthor` plan
validation, `approval`-kind entries must validate `step_id` is a non-empty string AND an
actual step id in the spec (same rule as `check`/`failure-path`). Citing tests:
`{"kind":"approval","step_id":"nonexistent"}` → TestAuthorError; empty step_id →
TestAuthorError.

**F3-5 (rendered check-kind test is incoherent with executor check semantics — ACCEPT).**
The rendered check-kind test's handler returns `{'checks':{<step_id>:<expect>}}`, but the
executor evaluates the step's ACTUAL declared `check` pairs (keys may differ from step_id;
`status` expects the declared value, not 'ok'). Make the rendered check test SPEC-AWARE:
at render time read `STEPS[step_id]['check']` and build a handler that satisfies each
declared check (status → declared value; other keys → declared value), then assert the step
landed in `completed`. Citing test: a spec whose step has `check=(("status","pass"),)` —
the rendered check test passes when the handler honors the check and FAILS (StepCheckFailed)
when the handler violates it, exercising real check enforcement through the sandbox double.

**F3-6 (D6 provenance is opt-in, not mandatory — ACCEPT).** Make `provenance` a REQUIRED
positional arg on `WorkflowCompiler.compile` and `WorkflowTestAuthor.author_suite`
(remove the Optional default) and always append the compile / test-author events (entry_id,
spec_hash, executor/suite hash, compiler/author id, identity basis, timestamp via the
injected clock). Citing test: a fake ProvenancePort captures ≥1 compile event and ≥1
test-author event.

## P3 — cheap accepts

**F3-7.** Document `RunResult.status` vocabulary in the runtime docstring:
`completed | failed | awaiting_approval | error` (the `error` status is the F2-6
host-handler-failure path — evidence-faithful, entry not implicated, outcomes=()).

**F3-8.** D6 clock/refusal consistency: `_journal_refusal` and every runtime journal event
must use the run's injected `now` (the same string `start`/`finish`/`approval-stop` use);
also journal the no-active-record refusal (currently raises without journaling).

**F3-9.** Remove unused imports: `registry.py` `content_hash`/`canonical_json`; `gate.py`
`dataclass`/`Path` (if unused).

**F3-10.** `IndependentTestSuite.suite_hash` currently hashes only `test_source`, so
`author_id`/`identity_basis` are mutable without invalidating the hash. Recompute
`suite_hash` over `canonical_json(test_source, author_id, identity_basis)` in
`WorkflowTestAuthor.author_suite` and update `IndependentTestSuite.__post_init__` so the
required shape still holds (64-hex). Update any test asserting a literal suite_hash.

## Verify

After fixes: the three compilation test files, full `selflearn/tests`, root `tests`,
`node --test scripts/workplan.test.mjs`, `git diff --check e487dba`. Report real numbers.
