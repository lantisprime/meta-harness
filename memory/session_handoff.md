# Session Handoff — meta-harness (2026-07-08, session 4)

## State: v0.4 shipped & pushed. 247/247 tests. 8 commits on main (…→ e8fc46f).
Server :8321 IS running v0.4 (restarted this session, smoke-tested).

## What happened
Ran handoff item #1: SE template e2e with real workers (deepseek-v4-pro,
pi-cli/codex-cli, MiniMax-M3). Goal itself shipped (GET /health works, 23
node tests pass) but BOTH runs (ec3559b, afd3ce2) were false-negative
failures: judge graded final chat text, not the workspace. Follow-up planner
also fell back silently once (flaky). Fixed all of it as v0.4, plus the
user's three feature asks (packaging, humanized output, tabbed steps).
Plan was codex-reviewed (second-opinion.mjs, verdict HOLD → 6 findings all
ACCEPTed and folded in). User waived approval gates mid-session.

## v0.4 (one concern per commit)
- step.attempt journaling + judge.error/run.advance_error (diagnosability)
- planner fallback_reason through APIs/provenance/wizard
- WorkerResult.workspace_root stamped by runners (never inferred)
- evals/evidence.py + judge prompt evidence block ("files are ground truth")
- humanizeOutput: escape-first GFM subset + <details> JSON tree (XSS-tested);
  esc() covers single quotes
- Tabbed Run/Done screens (data-step-id delegation, auto-follow, pin)
- GET /api/runs/{id}/package → capped zip + Done-screen download button

## Next steps
1. Re-run SE template e2e on v0.4 — confirm judge now passes narration+files
   (test/health.test.js still missing in ~/.metaharness/workspaces/shared).
2. Issues filed: #1 execution-based verify for code_edit, #2 CLI timeout
   exposure — good next implementations.
3. gemma vs qwen eval (sdlc_capability_suite ready) — carried over.
4. Bounded loops (repeat_until); multi-worker per tier — carried over.

Episodic: em-search "v0.4". Reset context now.
