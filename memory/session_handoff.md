# Session Handoff — meta-harness (2026-07-08, session 3)

## State: v0.3 shipped. 229/229 tests, CI green, 17 commits on main.
Repo: github.com/lantisprime/meta-harness (private; branch protection still
Pro-gated). **Live server on :8321 was intentionally NOT restarted** (user was
testing) — restart to pick up everything from "custom workflows" onward:
`pkill -f "metaharness serve"` then
`.venv/bin/metaharness serve --local --port 8321`.

## Shipped this session (all Playwright-tested, 16 browser tests)
- **Subscription providers**: SubscriptionWorker rides signed-in Claude Code /
  Codex CLIs (both verified live: OK in 3.1s/5.5s). Kind `subscription_cli`.
- **Model pickers**: POST /api/probe (keys never in URLs) lists providers'
  real models; visible filter-as-you-type pick-lists (datalists looked empty);
  /api/cli_models reads Pi's models.json + opencode.jsonc registries.
- **Providers**: NeuralWatt + MiniMax added to catalog.
- **Custom workflows**: step-builder wizard (Objective→Type&tools→Verify&gate);
  every plan (LLM/template/custom) editable inline + YAML mode;
  POST /api/workflows/validate.
- **Branching (option 1)**: StepSpec.when {step, equals/contains/one_of,
  negate}; engine skips + cascades + journals `step.skipped`; checked before
  HITL gates; survives resume. Planner prompt teaches it.
- **Run follow-up**: Done screen → "Run again" or POST /api/runs/{id}/followup
  (planner sees per-step digest incl. NO-SHIP findings → remediation plan into
  editable review; approval = running it).
- **Step judge**: evals/judge.py — UNVERIFIED outputs graded by most capable
  runner vs step contract; FAIL → retry loop; scorer="judge";
  wire(judge=True) default; PLANNING exempt; unparseable → UNVERIFIED.
- **Bug fixes from live use**: stale base_url leak in wizard Test; reserved
  worker ids; retired-id re-admission (registry key_rotations+1); display-model
  placeholder leaking into CLI argv (-m codex-cli); approval 409 flash
  (optimistic gate UI); renderSettings refetch churn.

## Next steps
1. Restart :8321; run SE template end-to-end with real models + coding CLI.
2. gemma vs qwen eval (sdlc_capability_suite ready).
3. Bounded loops (repeat_until) — option 2, deferred.
4. Multi-worker per tier / per-worker matrix.
5. Branch protection when repo public/Pro.

Episodic episode revised: see em-search "workplan". Reset context now.
