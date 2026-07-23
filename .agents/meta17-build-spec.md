# META-17 Build Spec — compile learned workflow entries to executors, cross-validated by independent tests

FROZEN at dispatch. Mid-build scope changes require the orchestrator to update this doc AND
explicitly re-sync the seat. Card: `TASK-20260723-017` (Linear META-17), definition hash
`sha256:badace0386ae213e1f49a512f3eea8d6b6d83f6918d1de48ac9ef704654d4957`.
Base `e487dba`, branch `dev/meta-17-workflow-compilation`, worktree `/private/tmp/meta-harness-meta-17`.

Charter trace: product-loop stages 4–5 (governed learning → reusable procedural knowledge).
The workflow entry remains the verified **spec**; the executor is a **derived artifact**.
Invariants honored: evaluator non-self-approval (independent test-author + sandbox +
strict-mode approval), bounded authority (quarantine-by-default, runtime produces evidence,
never writes marks), reversible lineage (hash-bound registry, receipts), honest termination
(fail closed everywhere).

## 1. Writable file set (EXACT — nothing else may be created or edited)

| File | Status |
|---|---|
| `selflearn/src/selflearn/compilation/__init__.py` | NEW |
| `selflearn/src/selflearn/compilation/models.py` | NEW |
| `selflearn/src/selflearn/compilation/compiler.py` | NEW |
| `selflearn/src/selflearn/compilation/testgen.py` | NEW |
| `selflearn/src/selflearn/compilation/gate.py` | NEW |
| `selflearn/src/selflearn/compilation/registry.py` | NEW |
| `selflearn/src/selflearn/compilation/runtime.py` | NEW |
| `selflearn/src/selflearn/doctor.py` | EDIT — add `_check_executors` + wire into `_doctor_pack` |
| `selflearn/src/selflearn/__init__.py` | EDIT — export compilation symbols only |
| `selflearn/tests/test_compilation.py` | NEW |
| `selflearn/tests/test_compilation_gate.py` | NEW |
| `selflearn/tests/test_compilation_runtime.py` | NEW |

No edits to: `contracts.py`, `ports.py`, `store/`, `learning/`, `verification/`, `retrieval/`,
`pipeline.py`, `advisor.py`, or anything outside `selflearn/`. If the build seems to require
one, STOP and report — that is a card stop condition, not a judgment call.

## 2. Read-only anchors (mirror these; do not edit)

- `selflearn/src/selflearn/contracts.py` — frozen-dataclass idiom, `_require` validation,
  `ContractError`. `ProcedureStep` (id, objective, task_type, tools, depends_on, check as
  frozen key/value pairs). `TaskOutcome` (verdict ∈ {"pass","fail"}; `implicated ⊆ injected ∪
  seeded_by`; `applied ⊆ injected`; a pass cannot implicate). `CandidateEntry` (workflow kind
  requires procedure; depends_on must reference an EARLIER step — topological order is
  already contract-guaranteed).
- `selflearn/src/selflearn/verification/evalgen.py:53-60` — constructor-time identity
  enforcement: `if not identity.distinct(author, validator): raise EvalGenError(... basis)`.
  Mirror this shape for the test-author.
- `selflearn/src/selflearn/ports.py` — `ModelPort.complete(role, prompt, context) -> dict`,
  `ExecutionPort.run_check(check: dict) -> ExecutionResult(ok, output)`,
  `ProvenancePort.append(event)`, `IdentityPort.distinct(a, b)` + `.basis`. Unbound
  ExecutionPort → refuse loudly (convention: `verification/verifier.py:150-154`).
- `selflearn/src/selflearn/learning/marks.py` — `apply_outcome(store, outcome)`; harmful
  marks need `implicated`; auto-deprecation. Runtime NEVER calls this — it returns outcomes.
- `selflearn/src/selflearn/learning/regression.py` — baseline file at
  `<store>/<pack>/evals/baseline.json`; `snapshot_baseline`/`check_regression`.
- `selflearn/src/selflearn/doctor.py:48-90` — `Finding(code, where, detail, fixable, fixed)`,
  `DoctorReport`, `run_doctor(root, fix)`. Per-pack checks hang off `_doctor_pack` (:149).
- `src/metaharness/memory/models.py` — self-verifying receipt idiom (frozen model whose id
  is a sha256 over its canonical serialization; `__post_init__` recomputes and rejects
  mismatch). Mirror it for `CrossValidationReceipt` and `ExecutorRecord`.

## 3. Design decisions (binding — deviation is a defect)

- **D1 — deterministic compiler, no model in the control spine.** `WorkflowCompiler` is a
  pure stdlib generator: procedure → Python source string. Rationale: the procedure is
  already machine-readable; a model adds only nondeterminism. The charter's generated-code
  invariant is honored via content-hash binding + quarantine + sandbox gate + receipts.
  A model-assisted compiler is a future H-candidate, out of scope.
- **D2 — approval-step predicate.** A step is approval-type iff
  `step.task_type == "approval"` OR `bool(step.check_dict().get("approval"))`.
  One predicate `is_approval_step(step)` in `compiler.py`; both spellings + absence tested.
- **D3 — executor is generated Python source, restricted-exec at runtime.** The compiler
  emits a self-contained module (stdlib only; the only import allowed is `json`) defining
  `SPEC_HASH`, `ENTRY_ID`, `ORDER`, `STEPS`, and `run(step_handler)` — plus
  `class ApprovalRequired(Exception)`. Generated source contains ONLY machine-templated
  code and escaped contract data (repr-style escaping; never `str.format`/f-string
  interpolation of contract text into code position). The runtime executes it via
  `exec` in a namespace whose `__builtins__` is a whitelist (no `__import__`, `open`,
  `eval`, `exec`, `compile`, `input`, `globals`, `locals`, `vars`, `getattr` on dunder,
  `breakpoint`, `exit`, `quit`, `help`, `memoryview`, `object` is fine). Activation gate
  runs BEFORE any exec (see D5) — quarantined/rejected executors are never exec'd.
- **D4 — runtime produces evidence; the learning loop consumes it.** `ExecutorRuntime.run`
  RETURNS `RunResult(status, completed_steps, outcomes)` where `outcomes` is a tuple of
  `TaskOutcome`. It never imports marks, never writes the store. Callers feed outcomes to
  `learning.marks.apply_outcome` — proven in tests.
- **D5 — pack-local registry, atomic, self-verifying.** Executor artifacts and
  `registry.json` live under `<store>/<pack>/executors/`:
  `executors/<entry_id>/<spec_hash>.py` (source), `executors/registry.json` (records).
  Writes are atomic (tmp file + `os.replace`). Every `ExecutorRecord` is self-hashed
  (id = sha256 over canonical fields minus id; mirrors memory substrate idiom).
- **D6 — provenance journaling on every transition.** Compile, test-author call, gate
  decision (pass/fail/activation/rejection/swap-refusal), runtime start/finish/
  approval-stop/drift-refusal — all appended through the injected `ProvenancePort` with
  `kind`, `entry_id`, `spec_hash`, actor/basis, and an ISO-8601 UTC timestamp from the
  INJECTED clock (never `datetime.now` inline). This satisfies the card's
  compiler/test-author provenance acceptance item within META-17 scope (META-13's
  module-wide instrumentation remains a separate card).
- **D7 — executor swap requires a regression baseline.** Activating an executor for an
  entry that already has an ACTIVE executor requires `<store>/<pack>/evals/baseline.json`
  to exist (the suite regression gate from `learning/regression.py`); missing → GateError.
  The old executor transitions to `superseded` only after the new one activates.

## 4. Module contracts

### `compilation/models.py`
- `EXECUTOR_STATUSES = ("quarantined", "active", "rejected", "superseded")`.
- `@dataclass(frozen=True) ExecutorSpec`: `entry_id`, `pack`, `spec_hash` (64-hex),
  `procedure` (tuple[ProcedureStep, ...]). `__post_init__` validates non-empty/hash shape.
- `@dataclass(frozen=True) ExecutorCandidate`: `spec: ExecutorSpec`, `source` (generated
  Python), `executor_hash` (sha256 of source), `compiled_at` (ISO), `compiler_id`.
- `@dataclass(frozen=True) IndependentTestSuite`: `spec_hash`, `test_source`,
  `suite_hash` (sha256 of test_source), `author_id`, `identity_basis`, `authored_at`.
- `@dataclass(frozen=True) ApprovalRecord`: `approver` (non-empty), `basis`,
  `strict_mode: bool`, `approved_at`. Valid only with `strict_mode is True` —
  construct with strict_mode False → ContractError (activation requires strict approval).
- `@dataclass(frozen=True) CrossValidationReceipt`: `receipt_id` (self-hash),
  `spec_hash`, `executor_hash`, `suite_hash`, `sandbox_ok: bool`, `sandbox_output`
  (truncated 2000), `approval: ApprovalRecord | None`, `verdict` ("activated" |
  "rejected" | "refused"), `reason`, `decided_at`. Self-hash mirrors memory idiom:
  canonical JSON (sort keys, separators) minus `receipt_id`; `__post_init__` recomputes
  and raises ContractError on mismatch. No wall-clock reads inside models — all
  timestamps are constructor inputs.
- `@dataclass(frozen=True) ExecutorRecord`: `record_id` (self-hash), `entry_id`, `pack`,
  `spec_hash`, `executor_hash`, `status`, `path` (relative to store root), `receipt_id`
  (the gate receipt that produced this status), `updated_at`.
- `canonical_procedure_hash(procedure) -> str`: sha256 over canonical JSON of the step
  list (sorted keys, tuples as lists, `check` pairs preserved in order). Deterministic;
  same procedure → same hash. THIS is the spec hash function; compiler, gate, registry,
  and doctor all import it from here (one definition, one clock rule analog).
- Helpers: `canonical_json(obj) -> str`, `content_hash(text) -> str`.

### `compilation/compiler.py`
- `COMPILER_ID = "deterministic-workflow-compiler:v1"` (the reserved compiler identity;
  any model_id equal to this is rejected in testgen — see below).
- `is_approval_step(step) -> bool` per D2.
- `class CompilerError(RuntimeError)`.
- `class WorkflowCompiler`: `compile(entry: StoredEntry | CandidateEntry, *, pack: str,
  compiled_at: str) -> ExecutorCandidate`. Requires `cand.kind == "workflow"` and
  non-empty procedure (else CompilerError). Generates the D3 module:
  - header comment carrying `SPEC_HASH` + `ENTRY_ID` + compiler id;
  - `STEPS`: dict literal step_id → {"objective", "task_type", "tools", "depends_on",
    "check"} with contract data escaped via `repr`-equivalent safe literal emission;
  - `ORDER`: tuple of step ids in declared order (already topologically valid);
  - `class ApprovalRequired(Exception)` carrying `step_id`;
  - `run(step_handler)`: iterates ORDER; approval step → `raise ApprovalRequired(step_id)`;
    else `result = step_handler(step_id, STEPS[step_id])`; evaluates each `check` pair as
    assertions against `result` (check semantics below); failed assertion →
    `raise StepCheckFailed(step_id, key)` (also generated); returns
    `{"completed": [...step ids...]}`.
  - check semantics (deterministic, documented in generated docstring): check key
    `"approval"` handled by D2 (never evaluated post-hoc); key `"status"` → requires
    `result["status"] == value`; any other key `k` → requires
    `result.get("checks", {}).get(k) == value`. `step_handler` returning non-dict →
    StepCheckFailed. Keep the interpreter trivial: == comparison only.
- `executor_hash = sha256(source)`; `spec_hash = canonical_procedure_hash(procedure)`.

### `compilation/testgen.py`
- `AUTHOR_ROLE = "workflow-test-author"`.
- `class TestAuthorError(RuntimeError)`.
- `class WorkflowTestAuthor(model: ModelPort, identity: IdentityPort)`: constructor
  enforces `identity.distinct(model, _COMPILER_MARKER)` where `_COMPILER_MARKER` exposes
  `model_id = COMPILER_ID` — a model equal to the compiler identity is rejected with the
  recorded basis (evalgen shape). Constructor raises TestAuthorError on violation.
- `author_suite(spec: ExecutorSpec, *, authored_at: str) -> IndependentTestSuite`:
  calls `model.complete(AUTHOR_ROLE, prompt, context)` where context contains ONLY
  `{entry_id, pack, spec_hash, procedure: canonical step list}` — NEVER executor source
  (assert in tests). Prompt asks for a JSON test plan: `{"tests": [{"name",
  "kind": "order"|"check"|"approval"|"failure-path", "step_id", "expect"}]}`.
  SchemaGuard-style validation: non-list/empty → TestAuthorError; kinds outside the
  vocabulary → TestAuthorError; a plan with NO "order" test or NO "approval" test when
  the spec HAS an approval step → TestAuthorError (coverage floor).
  The plan is RENDERED deterministically into a test script (stdlib only): the author
  proposes coverage, the machine renders — no model-authored executable bytes.
  Rendered script defines `run_tests(load_executor)` where `load_executor(path)` is
  supplied by the harness sandbox wrapper; each test calls it, drives `run(...)` with a
  stub handler, and plain-`assert`s expectations. `suite_hash = sha256(test_source)`.

### `compilation/gate.py`
- `class GateError(RuntimeError)`.
- `class CrossValidationGate(execution: ExecutionPort | None, registry: ExecutorRegistry,
  provenance: ProvenancePort, clock: callable -> datetime)`:
  - `evaluate(candidate, suite, approval: ApprovalRecord | None, *, decided_at) ->
    CrossValidationReceipt`. Behavior, in order, each refusal journaled with reason:
    1. `execution is None` → GateError (refuse loudly, verifier.py:150 convention).
    2. `candidate.spec.spec_hash != suite.spec_hash` → rejected receipt ("suite/spec
       binding mismatch"), status `rejected`. NO sandbox run.
    3. `candidate.spec.spec_hash != canonical_procedure_hash(current entry procedure)`
       (registry reads the store) → rejected ("stale spec").
    4. sandbox: `execution.run_check({"kind": "workflow-cross-validation",
       "executor_source": candidate.source, "test_source": suite.test_source,
       "executor_hash": candidate.executor_hash, "suite_hash": suite.suite_hash})`.
       `ok` False → rejected receipt with truncated output.
    5. sandbox ok + `approval is None` → receipt verdict "refused", reason
       "sandbox pass awaits strict-mode approval" — candidate STAYS quarantined.
    6. sandbox ok + approval → verdict "activated"; swap rule D7; registry transition.
    7. Every path journals via provenance and returns the receipt. The gate NEVER
       activates on sandbox pass alone, NEVER runs the sandbox for binding failures,
       NEVER catches ContractError from receipt construction (fail loud).

### `compilation/registry.py`
- `class RegistryError(RuntimeError)`.
- `class ExecutorRegistry(store_root: Path, pack: str)`:
  - `record_for(entry_id, status=None)`, `active_for(entry_id) -> ExecutorRecord | None`,
    `transition(record, new_status, receipt_id, *, updated_at) -> ExecutorRecord` (atomic
    write D5; rejects illegal transitions: active→quarantined, rejected→active without a
    new quarantined candidate cycle — legal set documented in module docstring:
    None→quarantined, quarantined→active|rejected, active→superseded, superseded→(nothing)),
    `write_candidate(candidate) -> Path` (executor source under D5 layout; refuses
    overwrite of an existing identical path with different bytes),
    `is_stale(entry_id, current_spec_hash) -> bool`.
  - `registry.json` corrupt/unparseable → RegistryError (never auto-repair).

### `compilation/runtime.py`
- `class RuntimeCompError(RuntimeError)` (name avoids collision with builtin).
- `class ExecutorRuntime(registry: ExecutorRegistry, store: PackStore,
  provenance: ProvenancePort, clock)`:
  - `run(entry_id, *, task_id, topic, task_type, step_handler, now) -> RunResult`.
  - Activation enforcement: no ACTIVE record → RuntimeCompError. Drift: record.spec_hash
    ≠ `canonical_procedure_hash(store.get(entry_id).cand.procedure)` → refuse + journal
    ("executor.stale-spec" refusal), RuntimeCompError.
  - Loads source from record.path, verifies bytes hash == record.executor_hash (tamper
    → refuse + journal), execs per D3 whitelist.
  - Drives `run(step_handler)`: on `ApprovalRequired` → RunResult(status
    ="awaiting_approval", completed_steps, at_step) + journal; on `StepCheckFailed` →
    outcome `TaskOutcome(task_id, task_type, topic, verdict="fail",
    injected=(entry_id,), implicated=(entry_id,), step_id=failing_id,
    failure_mode="executor-step-check")`, RunResult(status="failed", ...); on success →
    `TaskOutcome(..., verdict="pass", injected=(entry_id,))`, RunResult("completed").
  - Journals run start/finish/stop per D6. Never writes the store, never imports marks.

### `doctor.py` edit
- `_check_executors(pack_dir, fix, report)`: if `executors/registry.json` absent →
  return silently. Corrupt → Finding("executor.registry-corrupt", fixable=False).
  For each ACTIVE record: entry file missing → Finding("executor.dangling-entry",
  fixable=False); recomputed `canonical_procedure_hash` ≠ record.spec_hash →
  Finding("executor.stale-spec", fixable=False). NEVER auto-fixes (fix flag ignored for
  these codes). Wire into `_doctor_pack` after `_check_probes`.
- Import of `canonical_procedure_hash` from `selflearn.compilation.models` is the ONLY
  new import allowed in doctor.py.

### `selflearn/__init__.py` edit
- Export the new public names (models + compiler/testgen/gate/runtime/registry classes
  and error types) appended to the existing export lists; no reordering of existing names.

## 5. Test plan (failing tests FIRST for each behavior)

`test_compilation.py`: hash determinism + tamper sensitivity (any step field flip changes
spec_hash); compile of a 3-step procedure (order, depends_on preserved, checks emitted);
approval predicate both spellings + absence; generated source has NO model/supplied bytes
outside string literals (injection probe: objective containing `";\nimport os\nx="`
compiles to an inert literal); executor source is stdlib-only; `run()` drives a stub
handler in order and returns completed ids; check failure raises StepCheckFailed with the
right step; ApprovalRequired carries step_id.
`test_compilation_gate.py`: end-to-end happy path with a FAKE ExecutionPort (test double
in the test file, not testing.py) — quarantined → active with receipt fields bound;
identity collision (author model_id == COMPILER_ID) rejected at construction; suite/spec
hash mismatch rejected WITHOUT sandbox (fake port records zero calls); stale spec
rejected; sandbox fail → rejected + spec untouched + quarantined; sandbox pass + no
approval → refused + STILL quarantined; activation swap without baseline.json → GateError;
swap with baseline → old superseded; receipt tamper sweep (flip any field → ContractError);
every decision journaled (fake ProvenancePort captures ≥1 event per path with kind +
spec_hash); no ExecutionPort bound → GateError.
`test_compilation_runtime.py`: runtime refuses without ACTIVE record; refuses on drift;
refuses on executor-byte tamper; approval step → awaiting_approval hard stop (steps after
NOT executed); check failure → fail TaskOutcome with exact step_id + implicated binding;
success → pass TaskOutcome; outcomes feed `learning.marks.apply_outcome` against a real
tmp-dir PackStore (harmful marks land on the entry); doctor flags stale executor and
dangling entry and leaves a clean pack silent; registry transitions + atomic rewrite
(crash between tmp and replace leaves prior valid registry — simulated by monkeypatched
os.replace failure).

## 6. Build sequence (per-step verify; small diffs, no heredocs >20 lines)

Per-step verify: `cd /private/tmp/meta-harness-meta-17 && PYTHONDONTWRITEBYTECODE=1
.venv/bin/python -m pytest -q -p no:cacheprovider <files>`
1. models.py + its tests → `selflearn/tests/test_compilation.py -k "model or hash or tamper"`
2. compiler.py + tests → `selflearn/tests/test_compilation.py`
3. testgen.py + gate.py + tests → `selflearn/tests/test_compilation_gate.py`
4. registry.py + runtime.py + tests → `selflearn/tests/test_compilation_runtime.py`
5. doctor hook + `__init__.py` exports → `selflearn/tests/test_compilation_runtime.py -k doctor`
6. FULL: `selflearn/tests` (baseline 224 passed + 1 env-skip on `pypdf`; must end
   ≥224+new, same single skip) then `tests` (root suite, must be unchanged-green) then
   `node --test scripts/workplan.test.mjs` then `git diff --check e487dba`.

## 7. SEAT HARD RULES

- Work ONLY in `/private/tmp/meta-harness-meta-17`. Create/edit ONLY the §1 files.
- NO git commit, NO git stash, NO git push, NO branch operations. The orchestrator commits.
- NO writes to episodic memory, `.workplan/`, `WORKPLAN.md`, Linear, or any shared state.
- NO edits to existing files except the two listed EDIT files, exactly as scoped.
- No network, no subprocess, no new dependencies (stdlib + existing deps only).
- On ambiguity that would change a §3 decision or §1 file set: STOP and report, do not
  improvise. On any failing per-step verify: fix before proceeding; never claim green
  you did not just run.
- Report per step: files written, verify output (real captured numbers), deviations.
