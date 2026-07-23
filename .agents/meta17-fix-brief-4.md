# META-17 Fix Brief 4 — GLM-5.2 gate round 3 findings on frozen head dbf85e9

Gate artifact: `.review-store/meta17-glm-gate-review-dbf85e9.txt` (REJECT, 0 P0, 3 P1,
7 P2, 9 P3). Invariants still confirmed holding; findings now live in untested edge paths.
All dispositions ACCEPT. Same §1 writable set, same hard rules (no commit/stash/push, no
memory/Linear/workplan writes, stdlib only). Build spec remains the contract.

## P1

**F4-1 (compiler.py check rendering — ACCEPT).** `json.dumps(check_list)` renders Python
`True`/`False`/`None` as `true`/`false`/`null` — invalid Python in the restricted sandbox
(SAFE_BUILTINS has none of those names). The D2-canonical spelling
`check=(("approval", True),)` (build-spec §3 D2) therefore yields
`"check": [["approval", true]]` → NameError at module exec; such a workflow can never pass
cross-validation. Replace `json.dumps(check_list)` with `repr(check_list)` (bool/None stay
Python literals; lists/tuples/strings render as valid Python). Citing test: compile AND
exec a workflow carrying a bool check value (`check=(("approval", True),)`) and a None
check value — both exec cleanly and the approval predicate still resolves True.

**F4-2 (testgen.py comment injection — ACCEPT).** Model-supplied `name`/`expect` are
interpolated raw into `#` comment lines; a newline payload
(`name="t\n    __import__('os').system('id') #"`) escapes the comment into executable code
at function-body indent. `RealSandboxExecutionPort` execs the test source with full
builtins → real RCE. Escape EVERY model string in comments via `json.dumps` (renders
newlines as `\n`, keeping the comment one line and inert). Also restrict
`RealSandboxExecutionPort`'s exec namespace so `__import__` is unavailable (drop it from
the test double's builtins). Citing test: a newline-injection payload in `name` and
`expect` → the rendered source compiles AND execs with no observable side effect (no
import, no exception from injected code).

**F4-3 (runtime.py exec outside try — ACCEPT).** `exec(source, globals_ns)` and the
ApprovalRequired/StepCheckFailed class resolution (~:203-218) are OUTSIDE the try block
starting at ~:220. Any exec failure (the F4-1 NameError, a malformed source that passes
`_ast_preflight`, or a missing/non-Exception class) propagates uncaught, leaving a partial
journal (runtime.start with no runtime.finish). Move exec + class resolution INSIDE a try
that normalizes to `RuntimeCompError` + a `runtime.finish`/refusal journal event. Citing
test: a tampered source that passes `_ast_preflight` but fails at exec (e.g. references an
undefined name) → RuntimeCompError + a finish/refusal journal event (no orphan
start-without-finish).

## P2

**F4-4 (runtime drift check unguarded — ACCEPT).** `entry = self.store.get(entry_id)` and
the hash recompute are unguarded; a missing entry raises `StoreError` that propagates
uncaught. Wrap them: missing/unreadable entry → `RuntimeCompError` + journal
`executor.stale-spec`/`executor.unverifiable` (mirror the gate's FIX-4 fail-closed).
Citing test: runtime.run on an entry deleted after activation → RuntimeCompError + journal.

**F4-5 (doctor.py non-dict JSON — ACCEPT).** `data = json.loads(...)` then
`data.get("records", [])`; if the file is valid JSON but not a dict (e.g. a bare list),
`data.get` raises AttributeError (not in the except). Add `isinstance(data, dict)` check →
`executor.registry-corrupt` finding (fixable=False); `run_doctor` must not abort. Citing
test: `registry.json` = `[1,2,3]` → finding, doctor completes.

**F4-6 (gate swap-no-baseline consistency — ACCEPT).** Swap-without-baseline currently
constructs a `verdict="rejected"` receipt, journals `gate.swap-no-baseline`, then raises
`GateError` WITHOUT transitioning the quarantined record → provenance says "rejected"
while the registry holds "quarantined". Make it a precondition failure: raise `GateError` +
journal `gate.error` (no misleading "rejected" receipt; the candidate honestly stays
quarantined and is retryable after a baseline snapshot). Citing test: swap without
baseline.json → GateError + zero registry transitions + record still quarantined.

**F4-7 (gate pack mismatch — ACCEPT).** Validate `candidate.spec.pack ==
self.registry.pack` at evaluate top; mismatch → rejected receipt, no sandbox. Citing test.

**F4-8 (approver == COMPILER_ID — invariant 1 — ACCEPT).** The gate checks the suite
author but never the approver; `ApprovalRecord(approver=COMPILER_ID, strict_mode=True)`
activates — letting the compiler identity approve its own output. Reject activation if
`approval.approver == COMPILER_ID` → rejected receipt ("compiler cannot approve its own
output"), no transition. Citing test: ApprovalRecord(approver=COMPILER_ID, strict_mode=True)
→ rejected.

**F4-9 (gate _journal_refusal omits fields — ACCEPT).** The refused-path journal omits
`sandbox_output` (and the approval structure) while `_journal` includes them. Include the
FULL receipt in every journal path (sandbox_output, approval). Citing test: refused-path
provenance event carries sandbox_output.

## P3 — cheap accepts

**F4-10.** `models.py` hash fields (`spec_hash`, `executor_hash`, `suite_hash`): validate
hex chars (`re.fullmatch(r"[0-9a-f]{64}", v)`), not just `len == 64` — messages say
"64-hex sha256".

**F4-11.** Single injected clock in `gate.py`: non-refusal journals should use `decided_at`
(the injected decision time), not `self.clock().isoformat()`; remove the unused clock
stored on the gate or use it everywhere consistently.

**F4-12.** `registry.transition`: if `record.record_id` is not present in the registry
file, raise RegistryError (no silent no-op that returns an unpersisted new_record).

**F4-13.** `doctor` orphan/missing checks: flag stray `.py` directly under `exec_dir/` (not
only subdirs); flag an ACTIVE record whose `record.path` source file is missing
(`executor.missing-source`, fixable=False).

**F4-14.** `doctor` flags a double-active registry (two active records for one entry) as
`executor.double-active` (fixable=False) — the activate/supersede crash-window mitigation.

**F4-15.** `doctor` flags leftover `*.py.tmp` / `registry.json.tmp` files as
`executor.stale-tmp` (fixable=False).

## P3 — rejected (accepted limitation; document in docstrings)

- FIX-6 `tracking_handler` deviation: `completed_steps` = steps whose checks PASSED is
  the intended completion semantics; document in the runtime docstring (NOT a defect).
- activate→supersede atomicity crash window (two active records): accepted; F4-14 is the
  detection mitigation.

## Verify

After fixes: the three compilation test files, full `selflearn/tests`, root `tests`,
`node --test scripts/workplan.test.mjs`, `git diff --check e487dba`. Report real numbers.
