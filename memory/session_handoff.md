# Session Handoff — meta-harness (2026-07-11, session 18)

## State: workplan item 2 regression complete; browser suite green; issues #16–#18 filed
- Restored the existing Playwright/Chromium environment without changing project files and
  ran the full browser suite: **38 passed**.
- Ran a bounded, real-worker Software Engineering template regression as
  `run_f1ed076f6ddc`. All six phases executed, the final run state was `completed`, and the
  package contains the workflow, complete journal, six phase outputs, manifest, and both
  workspace files. The ZIP integrity check passed; the generated calculator tests pass
  **5/5**.
- Exercised issue #11's timeout policy with real workers: attempts were MID timeout → same
  MID timeout → FRONTIER pass. Exactly one timeout-retry provenance event and one escalation
  were recorded; timeout failures did not become negative capability-matrix samples.
- The live audit exposed three reproducible product defects, filed before any product edit:
  - [#16](https://github.com/lantisprime/meta-harness/issues/16): resume loses unresolved
    HITL state and duplicates the approval request.
  - [#17](https://github.com/lantisprime/meta-harness/issues/17): Software Engineering
    approval gates pause before the spec/plan/review artifact exists.
  - [#18](https://github.com/lantisprime/meta-harness/issues/18): subscription workers read
    the global scratch workspace instead of the active run workspace.
- The run journal confirms #16 with four `hitl.requested` events for three configured gates.
  The package manifest confirms #18: implementation artifacts came from the run workspace,
  while subscription-backed phases recorded `subscription-scratch`; verify and review then
  reasoned coherently about the wrong filesystem.
- No product code was changed. Preserve the pre-existing `.gitignore` edit and untracked
  `.agents/`, `.claude/`, `.review-store/`, and `uv.lock`.
- Canonical episodic workplan: `20260710-230207-meta-harness-workplan-real-worker-se-reg-f7f1`.

## Next steps
1. Fix #16 first: it is a narrow durability invariant and makes every pending approval
   unreliable after restart.
2. Fix #17 next: model post-artifact approval semantics explicitly and keep rejection
   fail-closed for downstream phases.
3. Fix #18 after the gate semantics: give read-only subscription phases run-scoped workspace
   visibility without write access, then repeat the full real-worker regression.

---

# Session Handoff — meta-harness (2026-07-10, session 17)

## State: issue #14 SHIPPED via PR #15; CI green, warning removed, issue closed
- Filed [GitHub issue #14](https://github.com/lantisprime/meta-harness/issues/14) for the
  Node.js 20 Actions runtime deprecation warning.
- [PR #15](https://github.com/lantisprime/meta-harness/pull/15) was marked ready and
  squash-merged as `5fafb16` after annotation-free pull-request CI run #52 passed.
- The PR's `Closes #14` linkage closed GitHub issue #14 as completed.
- `.github/workflows/ci.yml` now uses the official Node 24-native majors:
  `actions/checkout@v6` and `actions/setup-python@v6`. The runner, Python 3.14 pin,
  dependency installation, and pytest command are unchanged.
- Validation: workflow YAML parses, both action-major references are asserted, and
  `git diff --check` is clean. PR CI run #51 passed with both v6 actions, and GitHub's
  check-annotations API returned an empty list: the Node.js 20 warning is gone.
- Preserve the pre-existing `.gitignore` edit and untracked `.agents/`, `.claude/`,
  `.review-store/`, and `uv.lock`; they are unrelated to issue #14.

## Next steps
1. Confirm final `main` CI also passes with an empty check-annotations list.
2. Start workplan item 2: restore Playwright E2E and run the real-worker Software Engineer
   template regression, filing concrete issues for any failures before editing product code.

---

# Session Handoff — meta-harness (2026-07-10, session 16)

## State: issue #11 SHIPPED via PR #13; CI green, issue closed, main synchronized
- [PR #13](https://github.com/lantisprime/meta-harness/pull/13) was marked ready and
  squash-merged as `2e4ac89` after pull-request CI run #47 passed.
- The PR's `Closes #11` linkage closed GitHub issue #11 as completed.
- Timeout FAILs now receive one retry on the exact same tier before that tier can be
  excluded and escalation can occur. The retry is explicitly pinned to the prior tier, so a
  changed affordability filter cannot silently route the grace attempt elsewhere.
- Timeout FAILs are operationally neutral and do not enter the capability matrix as negative
  model-skill evidence. A later PASS still records normal positive evidence; ordinary
  non-timeout verified FAILs retain immediate escalation behavior.
- The executor records `task.timeout_retry` provenance with the attempt, tier, and model.
- Final gates: **531 non-E2E tests passed**, focused executor tests **27 passed**,
  compileall and `git diff --check` clean. Browser E2E was unavailable because Playwright is
  not installed in the active venv; no UI code changed. Existing FastAPI lifespan
  deprecation warnings remain unchanged.

## Files in the issue #11 diff
- `src/metaharness/core/executor.py`: timeout-neutral matrix learning, one-shot exact-tier
  retry, repeat-timeout escalation, and provenance.
- `tests/test_executor.py`: timeout→PASS, timeout→timeout→escalate, exact-tier behavior after
  a budget charge, provenance, and unchanged ordinary FAIL escalation.
- `docs/architecture.md`: documents the timeout-aware cascade contract.
- Handoff updated here. Preserve the pre-existing `.gitignore` edit and untracked
  `.agents/`, `.claude/`, `.review-store/`, and `uv.lock`; they are not issue #11 work.

## Next steps
1. There are no open GitHub issues; choose the next roadmap item before starting new work.

---

# Session Handoff — meta-harness (2026-07-10, session 15)

## State: issue #1 SHIPPED via PR #12; CI green, issue closed, main synchronized
- [PR #12](https://github.com/lantisprime/meta-harness/pull/12) was marked ready and
  squash-merged as `4ca087f` after GitHub Actions CI run #42 passed.
- The PR's `Closes #1` linkage closed GitHub issue #1 as completed.
- Product diff: execution-based verification for `code_edit` attempts, plus the trust,
  budget, retry-feedback, journal, docs, and regression-test surfaces it requires.
- Final gates: **490 non-E2E passed**, **38 Playwright passed**, focused verifier/executor/
  trust/correction tests **92 passed**. `git diff --check` and compileall clean.
- Real macOS Seatbelt smoke test passed outside the managed outer sandbox: pytest ran while
  inherited secret env, network access, and writes outside the attested workspace were denied.

## What is implemented (#1)
- New `evals/execution.py`: deterministic discovery prefers pytest (real config/tests + an
  installed pytest runtime), then `package.json#scripts.test`; fixed argv, never worker
  narration as a command. Missing markers/runtime/isolation returns no signal and falls back
  to the evidence-fed rubric judge.
- OS isolation: macOS Seatbelt and Linux bubblewrap; no network; writes only to the attested
  workspace + credential-free scratch; scrubbed environment and deterministic PATH; 120s
  wall timeout; 64 KiB/stream memory cap; process-group cleanup including pipe-holding
  background descendants. Seatbelt backend was exercised for real; bubblewrap policy is unit-
  pinned but cannot be live-smoked on this Darwin host.
- Executor hierarchy: authenticity/schema → worker budget gate → execution check → existing
  deterministic/judge behavior. Execution PASS/FAIL is `scorer=execution`, feeds the
  capability matrix, drives retry/escalation, and takes precedence over narration/text checks.
  Execution wall time is separately charged to `Budget`; an over-budget worker never launches
  the suite. Test failure detail now reaches grounded reflection instead of being hidden by an
  incidental `equals` check.
- Trust boundary: worker-result signature **v2** covers `workspace_root` + `timed_out`.
  Historical v1 signatures remain verifiable, but their unsigned roots cannot select code for
  execution or evidence reads. Provenance records the signature version; attempt journals
  record verifier latency.

## Files in the issue #1 diff
- New: `src/metaharness/evals/execution.py`, `tests/test_execution.py`.
- Changed: core types/executor/budget, runner signing, verifier exports, grounded reflection,
  workflow attempt journaling, executor/harness/correction tests, README, architecture docs.
- Handoff updated here. Preserve the pre-existing `.gitignore` edit and untracked
  `.agents/`, `.claude/`, `.review-store/`, and `uv.lock`; they are not issue #1 product work.

## Next steps
1. Start #11: timeout-aware same-tier retry/escalation.

---

# Session Handoff — meta-harness (2026-07-10, session 14)

## State: issue #2 SHIPPED, pushed, closed. main == origin/main (fe69865). Clean close-out.
- `fe69865` — issue #2: per-worker timeout config + task-type-aware defaults + structured
  timeout journaling. 22 files, +560/−24. Tests 453 → 470 non-e2e, 36 → 38 Playwright,
  all green (orchestrator re-ran both suites itself after the fix batch).
- Filed **#11** (timeout FAIL triggers tier escalation — retry at a pricier tier for a
  time-limit failure; scout finding, deliberately out of #2's scope).
- Remaining open: **#1** (execution-based verification for code_edit steps) and #11.

## What shipped (#2, full playbook v10 run)
- `AgentConfig.timeout_s` / `AddWorkerRequest.timeout_s` — `Field(gt=0, le=86400,
  allow_inf_nan=False)`; factory passes to coding_cli / subscription_cli / openai_compat
  only when set; server forces None for mock.
- Task-type-aware defaults when unset: `BASE_TIMEOUT_S` (600 coding / 300 subscription)
  × `TASK_TYPE_TIMEOUT_FACTOR` (code_edit 3×) via `effective_timeout_s(task)`; explicit
  config value wins FLAT across task types (mirrors budget_for override precedent).
- Structured timeout: `WorkerTimeout` exc, `WorkerResult.timed_out` (UNSIGNED derived
  metadata — deliberately excluded from result_signing_bytes so old signatures stay
  valid), `MASTMode.TIMEOUT`, verify_output routes before TOOL_ERROR, httpx timeout
  caught ONLY around the model `_post` (not the tool round-trip). step.attempt +
  task.attempt payloads now carry failure_mode / latency_s / timed_out. TIMEOUT
  vocabulary in grounded_reflector, CURATION_TEMPLATES, classify_failure, MAST_PLAIN.
- Wizard: Advanced `<details>` block in step 2 (first numeric input in the wizard),
  hidden for mock + cleared on kind-switch + excluded from save + server-side guard
  (three layers, each pinned by a test), edit preload, summary line, settings card.

## Playbook run (per user's explicit "yes full playbook")
Scout (2× sonnet Explore) → orchestrator spec → codex plan review (HOLD → 6 findings
repaired → BUILD; caught the openai_compat structured-timeout parity P1 PRE-BUILD) →
sonnet builder (one seat, all 6 parts) → 4-seat frozen-diff panel (Claude sonnet agent +
codex gpt-5.5-high tmux + GLM-5.2 + kimi-k2.7-code via pi tmux drivers) → 7 deduped P2
fixes by the ORIGINAL builder via SendMessage → MiniMax-M3 behavioral verify (10/10
probes PASS, own harness under /tmp/verify-issue2/).

Panel value: 0 P1 (plan review had already eaten the P1 class), 7 P2s incl. 3-seat
convergence on Infinity-accepted validation, 2-seat convergence on the too-broad httpx
catch, GLM's mutation-survivability audit of the new MAST vocabulary (all 4 branches
untested), kimi's "the central `timeout=eff` line itself is unpinned" catch. Builder
pushback: Starlette can't serialize an inf echo in a 422 body — test asserts the real
property (fails loudly, never persists) instead of the panel's literal wire assertion.

## Process notes / gotchas (this session)
- Sockets: drive-codex-mh-s9 + drive-pi-kimi-s9 OWNED BY LIVE SIBLING (untouched).
  Used drive-codex-mh-s13 (codex), drive-pi-kimi-s13 (kimi), drive-pi-glm-s9 (GLM,
  then reused for the MiniMax-M3 verify seat). All stopped at close-out.
- codex CLI's configured default model `gpt-5.6-sol` 400s ("requires a newer version of
  Codex") on codex v0.143.0 — switch the seat via `/model` → gpt-5.5 + High effort.
  Menu Enters over tmux frequently need a second (sometimes third) press.
- Hit the playbook §6 zsh gotcha MYSELF: `set -- $pair` in a watcher loop doesn't
  word-split under zsh → every capture failed → false ALL-IDLE. Plain per-seat commands
  fixed it. The playbook rule is real; it also bites `for pair in ...; set -- $pair`.
- pi seats in read-only mode generated ~20 permission dialogs across the session (rg
  with pipes/||, pytest runs, /tmp probe writes) — all benign, look-then-approve each.
  A dialog-watcher that exits on "How should Pi handle" + spinner-char busy detection
  (NOT word-grep) worked reliably; twice-sampled token counters confirmed seat-done.

## Deferred / known-small (carry-over + new)
- Session-13 items still unfiled: judge cost not in outcome.total_cost_usd; advisor
  budget-exhausted message wording; tuple one_of members in check_value_problems;
  deprecated-bullet boundary collision; tuning cand.model_dump() volatile leak.
- NEW: `temperature` / `max_tokens` on AgentConfig are equally unconstrained (no
  ge/le) — same class as the #2 validation fix, pre-existing, not user-reachable via
  wizard yet; fix when those fields get exposed.

## Next steps
1. #1 (execution-based verification for code_edit steps) — the big one.
2. #11 (timeout-aware escalation) — small, scout+spec first.
3. Optionally file the session-13 deferred items.

Working tree after close-out: only .gitignore (pre-existing session noise) + untracked
.agents/.claude/.review-store/uv.lock remain uncommitted, same as session start.
