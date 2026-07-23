# META-17 Fix Brief 1 — orchestrator review of the first build

Frozen-contract fixes only; the build spec (`.agents/meta17-build-spec.md`) remains the
authority. Same §1 writable file set, same §7 hard rules (no commit/stash/push, no
memory/Linear/workplan writes). Every fix needs a citing test in the existing three test
files. Verify after each section with:
`cd /private/tmp/meta-harness-meta-17 && PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider selflearn/tests/test_compilation.py selflearn/tests/test_compilation_gate.py selflearn/tests/test_compilation_runtime.py`

## FIX-1 (P1) runtime.py — strip the builtins whitelist to D3

`SAFE_BUILTINS` currently includes `eval`, `exec`, `open`, `input`, `__import__`,
`locals`, `vars`, `memoryview`, `setattr` (and contradicts its own docstring). Remove
every dangerous entry. Allowed: plain types (`bool int float str bytes list dict tuple
set frozenset range slice object type`), common exceptions, `True False None`,
iteration/functional helpers (`len iter next enumerate zip map filter sorted reversed
sum min max abs all any repr str.format`-free `format hash isinstance issubclass
callable chr ord hex oct bin pow round divmod print`), `staticmethod`, `super`,
`__build_class__`, `getattr`, `hasattr`. NOT allowed: `eval exec compile open input
__import__ globals locals vars setattr delattr breakpoint exit quit help memoryview`.
Keep `__name__`/`__doc__` injection. Delete the "full compatibility" weasel comment —
the generated code needs none of these.
Citing test: generated executor whose step_handler attempts `open(...)`,
`__import__('os')`, and `eval('1')` inside a check path (e.g. a hostile objective string
that survived as literal — prove it stays inert) fails with NameError.

## FIX-2 (P1) runtime.py — approval stop is NOT a failure outcome

On `ApprovalRequired` the runtime must return `RunResult(status="awaiting_approval",
completed_steps=<steps actually run>, outcomes=(), at_step=step_id)` plus the journal
event. It must NOT construct a TaskOutcome at all — awaiting human approval produces no
pass/fail evidence and must never harm the entry (current code implicates the entry,
which would feed harmful marks for a normal human gate).
Citing test: approval stop yields outcomes == () and apply_outcome is never invocable on
the result (nothing to feed).

## FIX-3 (P1) gate.py — wire the registry state machine

Today `evaluate` only journals; nothing ever becomes active or rejected.
- `registry.write_candidate(candidate)` must ALSO atomically add the quarantined
  `ExecutorRecord` (source write + record add in one logical step; if the record already
  exists for the same spec_hash+executor_hash, idempotent). The quarantined record's
  `receipt_id` is `f"compile:{executor_hash}"` (documented convention: the compile event
  produces quarantine).
- `gate.evaluate` REQUIRES an existing quarantined record matching the candidate's
  spec_hash + executor_hash; absent/mismatched → rejected receipt
  ("unregistered candidate"), no sandbox run.
- verdict "rejected" → `transition(record, "rejected", receipt.receipt_id, ...)`.
- verdict "activated" → first supersede the old active record
  (`transition(old_active, "superseded", ...)` AFTER the new record activates),
  `transition(record, "active", ...)`. Swap without baseline.json still raises GateError
  BEFORE any transition (no partial swap).
Citing tests: full happy path ends with `registry.active_for(entry_id)` returning the
record and the runtime able to run it; rejection leaves no active record and status
"rejected"; swap leaves exactly one active and one superseded; evaluate without prior
registration → rejected, zero sandbox calls.

## FIX-4 (P1) gate.py — stale-spec check fails closed

`except Exception: pass` around the store read deletes the guard. Replace with: entry
missing/unreadable/hash-uncomputable → rejected receipt ("spec unverifiable: <cause>"),
journaled, no sandbox run. Only a matching hash proceeds.
Citing test: delete the entry file after compile → evaluate → rejected, zero sandbox calls.

## FIX-5 (P1) testgen.py — escape everything; validate the plan

- Every model-supplied string rendered into test source MUST go through `json.dumps`
  (or repr): `name`, `step_id`, `expect` — no raw concatenation/format into code.
- Fix the `.format(name, ", e")` bug (renders a 3-tuple) and the empty-`expect`
  `expected_order` NameError path.
- Plan validation: `name`/`step_id`/`expect` must be strings; for kinds
  check/approval/failure-path, `step_id` MUST be an actual step id in the spec; "order"
  tests require non-empty expect listing spec step ids. Violations → TestAuthorError.
Citing tests: plan containing `step_id = "x' or True #"` renders an inert literal
(compiled test source parses + contains the escaped form); plan referencing a
nonexistent step → TestAuthorError; order test without expect → TestAuthorError.

## FIX-6 (P2) runtime.py — ApprovalRequired resolution + completed-step fidelity

- Resolve `ApprovalRequired = globals_ns.get("ApprovalRequired")` ONCE after exec;
  missing or non-exception → RuntimeCompError("invalid executor"). Same for
  `StepCheckFailed`. Never `except globals.get(...)` inline.
- Wrap the handler: `def tracking_handler(sid, sdata): completed.append(sid); return
  step_handler(sid, sdata)` and pass THAT to generated `run`, so `completed_steps` on
  failed/awaiting paths reflects steps that actually executed.
Citing test: 3-step procedure failing at step 2 → RunResult.completed_steps == ("s1",).

## FIX-7 (P2) gate.py — single hashing path

Delete `_compute_receipt_id`; construct every `CrossValidationReceipt` with
`receipt_id=""` and let models.py self-hash. The model is the only hasher.

## FIX-8 (P2) doctor.py — unverifiable entry is a finding, not silence

Replace the bare `except Exception: pass` with a Finding
`executor.unverifiable` (fixable=False) naming the entry and cause.

## FIX-9 (P2) registry.py — reads must not mutate; malformed fails closed

- `_ensure_registry` split: read paths (`record_for`, `active_for`, `is_stale`) return
  empty when the file is absent, never create it. Only write paths create.
- `record_for`: a record that fails `ExecutorRecord(**r)` construction → RegistryError
  (authority bindings are never silently skipped).
- `write_candidate`: write source via tmp + `os.replace` like `_write_registry`.
Citing tests: `active_for` on a pack with no executors leaves no `executors/` dir on
disk; hand-corrupt one record field → RegistryError.

## FIX-10 (P3) cleanup

- Move `FakeExecutionResult` out of gate.py into the test file that uses it.
- Generated executor module: add real `SPEC_HASH = "..."` and `ENTRY_ID = "..."`
  assignments (spec requires constants, not only comments).
- Drop `compiled_at` from the generated source body (keep it on ExecutorCandidate):
  identical procedure must hash to identical executor bytes.

When done: run the full §6 step-6 sequence (selflearn suite, root `tests`,
`node --test scripts/workplan.test.mjs`, `git diff --check e487dba`) and report real
numbers.
