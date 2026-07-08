# Session Handoff — meta-harness (2026-07-09, session 6 final)

## State: Meta-Harness optimizer + full web surface + live-testing fixes on main.
310/310 tests. Latest commits: 9f6a64b (LLM proposer + prompt directives),
901b313 (add_coverage suite extension), 7ff4d91 (report records target model),
plus dda205e/5f9ddc2/... (live-testing fixes). Server :8321 runs latest build
(--local; MiniMax-M3 small, codex/pi mid [pi holds the slot], deepseek frontier).

## Live user-testing found 3 real bugs (all fixed + regression-tested)
1. strip_think (local.py): MiniMax <think> blocks failed EVERY correct answer
   (classify pass^3 0.00). The AI advisor itself diagnosed this from raw traces.
2. ToolOffload prose parsing (enrichment.py): real models answer "The
   expression is: 17*23+9", not {"program":...} — offload always failed.
3. Just-started searches invisible for minutes (suite dir didn't exist yet).
Tainted ledgers archived in ~/.metaharness/optimization-archive-20260709/.

## Shipped since last handoff
- Freshness stamps + per-suite plain-language summary (also on Home).
- Arrangeable console cards (hover ‹ ›, localStorage).
- Advisor actions execute (open_settings/prefill_goal navigate; add_coverage
  works: frontier agent generates harder questions, validated hard — math
  recomputed via sandbox, wrong-domain/unscoreable dropped — persisted in
  <suite>/extra_tasks.json, merged into all future searches web+CLI).
- Proposer picker on Tune button: rule | llm (frontier agent over raw traces).
- RuleProposer proposes additive prompt directives on near-miss format fails.
- Report records target_model (swapping tier models made ledgers misleading).

## Next steps (priority order)
1. **Multi-worker per tier**: router pool per tier, per-task-type pick from
   capability matrix (user has 2 mid agents; last-configured silently wins now).
2. Code-space search (coding-agent proposer over the ledger).
3. Charge LLMProposer/advisor tokens to Budget.
4. Advisor placements: run-ledger failure explainer, matrix advisor, Settings
   prompt drafter (designed in mockup artifact 28edffa2, not built).
5. Suites from real run journals; carried: Issues #1/#2, gemma-vs-qwen.

Codex mechanism: ~/Developer/projects/episodic-memory/scripts/second-opinion.mjs
(--storage episodic --dispatch, background, >2min). 9 review rounds this session.
Episodic: em-search "live testing think blocks". Reset context now.
