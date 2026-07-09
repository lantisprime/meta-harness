# Session Handoff — meta-harness (2026-07-09, session 7)

## State: Per-task model selection (next-step #1) SHIPPED on main.
Commit 36e0417. 331/331 tests (was 310). Working tree clean. Server :8321
still runs the PRE-pools build — restart it to get pools live.

## What shipped
Router holds per-tier POOLS (dict[Tier, list[Runner]]); tier escalation
unchanged; decide() picks the member with best matrix pass rate for the
task_type (tie: config order). ε-exploration (settings.explore_rate, 0.1)
on success_check tasks only routes the least-sampled other member so benched
models earn evidence; an explored FAIL retries the tier instead of escalating.
add_worker appends (re-add = in-place identity rotation, POST /api/workers
on live dup now 201+rotation, was 409); retire removes one member, tier keeps
serving. GET /api/routing = members + skills + routed tallies; dashboard tier
rows / Agents card / "Who's good at what" show pools + routed n×. Boot check
against real config: techlead-bot(codex-cli) AND pi-coder-bot(pi-cli) both
pooled on mid — the "pi holds the slot" eviction is gone.

## Process (worked well — reuse)
Orchestrated: 3 sonnet scouts → Fable plan → 2 opus build stages →
3-reviewer adversarial panel (opus/codex/GLM-5.2, 6 findings, zero overlap,
all fixed+regression-tested) → MiniMax-M3 independent behavioral verify via
pi in tmux. Durable: global episode 20260709-012621-tiered-multi-agent-
orchestration-playboo-1322; agent-roster-verified-capabilities.md in
auto-memory.

## Known issue (deliberately deferred)
GLM F3: CapabilityMatrix.record does synchronous write_text on the event
loop per observation, unwrapped — disk error crashes the run. Pre-existing.

## Next steps (priority order)
1. Code-space search (coding-agent proposer over the ledger).
2. Charge LLMProposer/advisor tokens to Budget.
3. Advisor placements (mockup artifact 28edffa2, not built).
4. Fix GLM F3 (async/batched matrix persistence).
5. Suites from real run journals; carried: Issues #1/#2, gemma-vs-qwen.

Reset context now.
