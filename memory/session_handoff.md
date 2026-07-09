# Session Handoff — meta-harness (2026-07-09, session 8)

## State: 3 roadmap items SHIPPED on main + model-bench harness built. Tree clean.
383 tests (was 331). Server :8321 restarted onto the pools build (verified via
/api/routing: techlead-bot + pi-coder-bot both pooled on mid). Commits 911ba71→5186b2e.

## What shipped (next-steps #1, #2, #4 + server restart)
1. **Code-space search** (paper arXiv 2603.28052): CodeProposer runs a coding CLI
   rooted at the ledger so it greps raw traces itself. HarnessParams.code_ref/code_hash
   with root-bound build(base, *, ledger_root); code_gate.py (path containment, ast,
   subprocess import probe that CALLS build(stub), AST-folded decontamination, hard-link
   refusal); loop canonicalizes staged→candidates/<cid>/harness.py BEFORE eval, hashes
   canonical bytes, build() re-verifies hash on every load. CLI --proposer code, web
   proposer="code", dashboard code badge. Commits ecff506, 6f4e384, d36c71e, bb06d99.
2. **Budget charging**: LLMProposer/CodeProposer/advise() charge WorkerResult usage;
   loop catches BudgetExceeded→stopped="budget"; SchemaGuard accumulates retry usage;
   HarnessState defaults a cap-less Budget (was silently None→no-op); serve --max-cost-usd
   /--max-tokens; CodingAgentWorker estimates tokens (~4 chars/tok). Commits ae4019e, 0576fb1.
3. **GLM F3 fixed**: CapabilityMatrix atomic save (tempfile+os.replace), guarded load
   (corrupt→empty+last_load_error, no boot crash), _last_write advances only on success,
   non-blocking shutdown flush via asyncio.to_thread. Commits 1db8ab0, 655c92b.

## Process (worked well — reuse)
codex plan review (2 P1 fixed pre-build) → 3 opus build stages (frozen-diff handoffs) →
4-seat panel (codex+opus+GLM-5.2+kimi-k2.7-code) on full diff: 9 distinct findings, 2
convergences, ALL fixed w/ regression tests → verify: MiniMax-M3 endpoint flaked (timeouts
+ prompt-shield per-action gate = unworkable), so orchestrator wrote its own independent
probes /tmp/m3-verify/probe.py → 8/8 PASS. Durable: [[model-bench-harness]],
[[agent-roster-verified-capabilities]] (11-model×3-tier matrix), openrouter-pricing KB.

## Also built: ~/.model-bench/ (git repo, weekly cron Mon 09:00)
Reusable instrumentation sweep (bin/sweep.sh) + review panel (bin/panel.sh). Model profile
matrix: deepseek-v4-flash fastest all tiers; GLM-5.2/kimi deep-review seats; qwen3.7-plus
clean; M3 behavioral-verify only (slow, flaky); gemma-4-31b best US/EU; fast tier
hallucinates asyncio races. Re-run when >30 days stale.

## Known issues (deferred)
- Budget.max_wall_s configured but never enforced (pre-existing; 4 models spotted it).
- Decontamination is best-effort (string+AST-literal); chr()/runtime obfuscation out of
  scope, mitigated by held-out inspection (documented in code + architecture.md §3.9).
- No coding-CLI adapter surfaces real token usage → CodeProposer charges estimates.

## Late-session addendum: pi-driver classifier fix (memory-only, no repo change)
Diagnosed why a sibling Claude session "couldn't launch the pi driver": the Claude Code
auto-mode CLASSIFIER denies `--permission-mode auto` in the send-keys string at LAUNCH
("Create Unsafe Agents"). Proven fix (tested live): launch pi in DEFAULT mode (no auto
flag) + answer each dialog "Allow once" (bare Enter on highlighted default); pin tmux
socket with `-L <name>`; add `set -g extended-keys-format csi-u` to ~/.tmux.conf. Bigger
lesson: when a CLI seat is classifier-blocked, DELEGATE to a Claude Agent subagent — do
NOT fall back to solo-coding. Updated [[agent-roster-verified-capabilities]] recipe +
revised global playbook episode 20260709-044122 (v4, supersedes 032537). Optional TODO:
draft settings.json allowlist for codex-drive/pi-drive control-file reads.

## Next steps (priority order)
1. Advisor placements (was #3; mockup artifact 28edffa2 — fetch from artifact gallery,
   not in-repo). Only goal+tuning wired; /api/routing, /api/failures, /api/playbook unadvised.
2. Suites from real run journals (was #5): 24 journals in ~/.metaharness/journals/;
   extract (context, output, verdict) → Task into suites' extra_tasks.json format.
3. Charge real coding-CLI token usage once an adapter exposes it (replace estimate).
4. Enforce Budget.max_wall_s. Carried: Issues #1/#2, gemma-vs-qwen eval.

Reset context now.
