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
