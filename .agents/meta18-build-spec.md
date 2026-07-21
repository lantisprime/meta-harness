# META-18 builder brief

## Role and immutable scope

You are the implementation seat. Work only in `/private/tmp/meta-harness-meta-18`; do not commit, merge, promote, deploy, or change H/E/W. Read `AGENTS.md`, `docs/PROJECT_CHARTER.md`, `.agents/meta18-definition.json`, and the exact authoritative orchestration episode before editing. The uncommitted tests in `tests/test_identity.py`, `tests/test_executor.py`, and `tests/test_coding.py` were drafted by the orchestrator before role-lock was re-established. Treat them as untrusted failing-first material: validate, amend, or replace them.

Product-loop stage: managed execution/rehearsal and verification (stages 3-4). Invariant: bounded authority—generation receives only explicit pre-dispatch authorization and an honestly described execution boundary; verification remains independent and post-hoc.

Owned paths only:

- `src/metaharness/harness/coding.py`
- `src/metaharness/harness/subscription.py`
- `src/metaharness/harness/isolation.py` (new)
- `src/metaharness/core/executor.py`
- `src/metaharness/identity/tokens.py`
- `src/metaharness/web/state.py`
- `tests/test_coding.py`
- `tests/test_executor.py`
- `tests/test_identity.py`
- `docs/architecture.md`

Do not expand into `workflows/journal.py`, `harness/__init__.py`, or any other path. If a requested design would require that, preserve compatibility within the owned paths and report the constraint.

## Scout synthesis

### 1. Capability tokens: route -> authorize -> assign -> spawn

Current `TaskExecutor.execute` routes at `core/executor.py:181-205`, constructs assignment at `206-214`, emits `attempt.assigned`/`attempt.started` at `215-216`, then calls `runner.run` at `223`. `identity/tokens.py:55-76` can issue signed, expiring, task-bound tokens; `95-125` validates one required scope, subject, task, expiry, signature, and an externally supplied revocation set. `web/state.py:35-45` already owns a `TokenIssuer`, but `wire()` at `192-203` does not pass it to the executor.

Implement the smallest compatible pre-dispatch gate:

- Add `TokenIssuer.check(...)` that applies the issuer's private revocation set and validates **all** required scopes. Preserve `validate_token(..., required_scope=...)` compatibility; do not weaken wildcard semantics.
- `TaskExecutor` accepts an optional issuer and always has an active issuer (a private default if none is supplied). `HarnessState.wire()` passes `self.issuer` so revocation is live in the canonical web composition.
- After `Router.decide()` has selected the exact `worker_id`/tier but before any `attempt.assigned`, `attempt.started`, event-sink binding, or `runner.run`, mint a short-lived token bound to:
  - subject = selected worker ID
  - task ID = current task ID (must be exact, not unbound)
  - scopes = exactly `task:execute`, `tier:<decision tier>`, `task_type:<task type>`
- Immediately validate signature, expiry, revocation, exact subject/task, and all three scopes through the same issuer. A custom/malicious issuer can return an invalid token; fail closed.
- On denial: runner call count remains zero; no assignment/start/verification event; no Attempt; no capability-matrix sample or learning evidence; final verdict FAIL; signed provenance records `task.authorization_denied` with task ID, attempt number, selected worker/tier/task type, and reason. Do not include the raw token or signature.
- On success: add a redacted authorization object to the existing `attempt.assigned` payload (token ID, subject, task ID, scopes, expiry; no signature/private material). This is durable capability evidence without adding an unknown canonical event kind.
- Do **not** emit new `attempt.authorized` or `attempt.authorization_denied` run-event kinds: `workflows/journal.py:22-34` rejects unknown kinds, and that file is outside META-18 ownership. Amend the untrusted tests accordingly. Direct denial evidence is provenance + failed outcome; workflow-level failure remains produced by the existing engine from the outcome.
- Preserve routing, retries, timeouts, budget charging, authenticity verification, evaluator behavior, and observer behavior for authorized attempts.

Required negative tests should cover revoked, expired, wrong subject, wrong task, and missing/wrong scope. Use a controlled issuer subclass or clock so each invalid case is checked at the real executor gate. Assert zero runner calls, no `attempt.assigned`/`attempt.started`, no matrix sample, and reasoned provenance. Valid-path test must assert the authorization object is present in `attempt.assigned`, exact-bound, and occurs before `attempt.started`.

### 2. Workspace lease: resolved path, non-blocking, cross-process

Current `CodingAgentWorker._workspace_for` chooses/creates a directory at `harness/coding.py:422-428`; `_execute` then assembles context and spawns directly at `430-490`. Add a synchronous context manager in new `harness/isolation.py` and hold it across the full attempt, including context assembly, spawn, communication, termination, parsing, and result construction.

- Key the lease by `Path.resolve()` so aliases/symlinks cannot bypass it.
- Use a process-local guarded holder set **and** POSIX `fcntl.flock(..., LOCK_EX | LOCK_NB)` on a stable lockfile outside the workspace, keyed by a SHA-256 digest of the resolved path. Never delete the lockfile on release (avoid unlink/reacquire inode races).
- Store safe holder metadata in the locked file (resolved workspace, worker ID, task ID, PID, acquired time) for a reasoned error. Never trust metadata as lock authority; `flock` is authoritative.
- Collision raises `WorkspaceClaimError` immediately; `BaseRunner.run` converts it into a `WorkerResult.error`. Error text must truthfully say the resolved workspace already has an active coding worker.
- Release process-local and OS locks in `finally` on success, failure, timeout, or cancellation. Do not auto-create/switch/delete branches or git worktrees.
- Apply the lease to every `CodingAgentWorker` execution, including subscription read-only inspection: the frozen requirement forbids concurrent mutation **or inspection** of one active checkout. Distinct explicitly supplied worktree paths resolve to distinct keys and can run concurrently.

Tests:

- two overlapping workers on the same workspace: exactly one reaches its stub subprocess; loser returns a reasoned `WorkspaceClaimError` result;
- cross-process child holds the lease and parent acquisition fails promptly;
- symlink alias collides with real path (where supported);
- distinct explicit directories/worktrees acquire independently;
- lease is released after success and after runner failure/cancellation.

Avoid a 30-second test sleeper; use a short bounded child hold and deterministic ready handshake/cleanup.

### 3. Honest execution-boundary contract

Add a frozen machine-readable `ExecutionBoundary` and pure `execution_boundary_for(cli, sandbox=...)` in `harness/isolation.py`. Required classifications:

- Codex CODE_EDIT/default `workspace-write`: `kind=cli_native`, `write_scope=workspace`.
- subscription Codex `read-only`: `kind=cli_native`, `write_scope=none`.
- subscription Claude `read-only` (plan mode + Read/Glob/Grep): `kind=cli_native`, `write_scope=none`.
- Pi, OpenCode, and edit-capable Claude: `kind=operator_trusted`, `write_scope=unbounded_by_harness`.

For every classification: authenticated CLI network access remains available; `harness_os_sandbox=false`. Never describe operator-trusted execution as sandboxed. Reject unknown CLI/sandbox combinations rather than inventing guarantees.

Select the boundary before argv/spawn. Put its JSON-compatible representation in the already emitted `context.manifest` payload before the subprocess call, and ensure it matches the exact adapter argv. This reuses an owned, canonical, pre-spawn event path instead of inventing a journal kind. Tests must bind the run-event sink, assert boundary evidence precedes stub spawn, and compare classification against actual argv flags.

### 4. Documentation and non-regression

Update `docs/architecture.md` §3.3/§4/scorecard to distinguish:

- worker-generation boundary: CLI-native restrictions or operator trust plus exclusive workspace lease;
- verifier boundary: existing network-denied OS sandbox;
- explicit worktrees are supported when supplied; the harness still does not create them;
- capability tokens are now an active pre-dispatch gate.

Do not claim the lease is a filesystem jail, credential strip, network deny, container, or OS sandbox.

## Verification

Run in order and report exact output:

1. `.venv/bin/pytest -q tests/test_identity.py tests/test_executor.py tests/test_coding.py`
2. `.venv/bin/pytest -q tests/test_web.py tests/test_workflows.py tests/test_optimization.py`
3. `.venv/bin/pytest -q`
4. `node --test scripts/workplan.test.mjs`
5. `git diff --check`

Before handoff, inspect `git diff --stat`, `git diff --name-only`, and the full diff. Do not commit.
