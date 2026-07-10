# Session Handoff — meta-harness (2026-07-12, session 13)

## State: FIVE issues shipped, pushed, closed. main == origin/main. Clean close-out.
Post-handoff addendum (same session): the authoritative playbook was revised to **v10**
(episode `20260710-025825-...-acf7`, supersedes v9) adding the SEAT-LIVENESS RULE to
section 10; tag search verified single-hit; canonical retrieval measured 25,626 bytes —
well under the 50KB cap.
- `fc038c4` — issue #9 (last session's working-tree carryover): committed, pushed, #9 closed.
- Filed #10 (source-side check gating, the #9 scout follow-up) — then BUILT and closed it same session.
- `c2b8d20` — batch commit for #4, #5, #6, #7, #10 (all auto-closed on push). 25 files,
  +1511/−124. Tests 426 → 489, all green. Only #1 and #2 remain open on GitHub.
- ONE batch commit, not five: the issues interleave hunks in app.py / test_advisor.py /
  test_optimization.py; per-issue splitting needed hunk surgery on a verified tree — the
  commit message carries full per-issue sections instead. Deliberate trade-off.

## What shipped (per issue, all panel-reviewed)
- **#7**: fcntl.flock choke point `append_extras()` in suites.py (lock spans fresh-load→
  merge→atomic-save), same-dir tempfile + os.replace (0644 preserved), both writers
  converted (harvest CLI; coverage endpoint via asyncio.to_thread). Thread-contention +
  injected-interleave + gather-overlap tests.
- **#4**: PAGE_ACTION_POLICY per page in advisor.py; mutating actions need params.suite ∈
  SUITE_NAMES (the vocabulary the EXECUTING endpoints accept — NOT _suites() dir listing,
  codex plan-review P1); malformed model output (non-dict params, non-str/unhashable
  action) drops silently, never 500s.
- **#5 + Budget.max_wall_s**: charge-always/fail-truthfully at advisor/proposers/executor
  (loop.py was the precedent); executor does deterministic-verify → charge → judge (no
  judge spend past cap); judge runner spend charged via make_judge(budget=); wall budget =
  sum of WorkerResult.latency_s; --max-wall-s on optimize AND serve; zero-value cap flags
  honored (is-not-None guard).
- **#6**: playbook/failures advise contexts emit stable projections (no pb_ ids /
  timestamps); deprecated bullets selected by recency, PRESENTED in content order.
- **#10**: planner drops unscoreable auto-derived arithmetic equals (1e999, 1e300*1e300);
  `check_value_problems()` (total over any input shape) gates /api/workflows/validate +
  /api/runs (named 422s) and plan_workflow (drops hazardous LLM checks). Historical
  journals + engine replay DELIBERATELY untouched (model-level validation would break them).

## Playbook run (full process per issue, per user's explicit choice)
Scout (2× sonnet Explore) → orchestrator spec → codex plan review (2 HOLDs repaired →
BUILD; caught 3 pre-build P1s) → SONNET builders (all five — verified, see roster memory)
→ 4-seat frozen-diff panels (codex + Claude sonnet + GLM-5.2 + kimi via tmux drivers) →
triage → fixes by ORIGINAL builders via SendMessage → MiniMax-M3 behavioral verify
(10/10 probes PASS, probe at /tmp/verify-brief/probe.py).

Panel value this session — 3 probe-confirmed P1s + 2 P2s, ALL invisible to a green suite:
executor reorder let authenticity failures poison the routing capability matrix (Claude
seat); planner value-gate 500 on non-dict success_check, breaking the fallback contract
(codex); deprecated-sort time.time() collision jitter breaking byte-stability, 7/8 repro
(Claude seat); --max-wall-s 0 silently uncapped (3-seat convergence); proposer
garbage-output masking after the reorder (codex; fixed symmetrically in LLMProposer).
Builder pushback highlight: #6 builder REJECTED the orchestrator's prescribed tie-break
with failing-test evidence and shipped a better fix (select-vs-present split).

## Process notes / gotchas (this session)
- Reserved sockets drive-codex-mh-s9 / drive-pi-kimi-s9 were OWNED BY A LIVE SIBLING
  session (episodic-memory repo) — used fresh session-scoped sockets drive-codex-mh-s13 /
  drive-pi-kimi-s13 instead; drive-pi-glm-s9 was free (later reused for the MiniMax-M3
  verify seat). Checked with `tmux -L <sock> ls` first, never killed anything.
- pi dialog storms handled by an auto-approver loop with a strict read-only/scratch-space
  allowlist; repo writes + git mutations escalated for manual look-then-approve.
- NEW MEMORY: whole-pane `grep "Working|esc to interrupt"` busy-detection FALSE-POSITIVES
  once a seat's report quotes those words — both final seats sat finished while the watcher
  said WORKING; the user's "are you sure?" exposed it. Liveness = sample the status-bar
  token counters twice (~25s apart); identical counters = idle.
  ([[pi-busy-detection-grep-false-positive]])

## Deferred (small, non-blocking; consider filing)
- Judge cost/tokens reach the shared Budget but NOT outcome.total_cost_usd (per-task
  report undercounts judged attempts).
- Advisor budget-exhausted message says "token budget exhausted" even when the cost or
  wall cap tripped (message includes the real cap detail, just awkward).
- check_value_problems ignores tuple one_of members (unreachable via JSON — 2 seats
  agreed theoretical).
- Top-5 deprecated-bullet SELECTION (not order) can differ across states on an exact
  timestamp collision at the boundary — inherent to wall-clock recency semantics.
- tuning branch cand.model_dump() still leaks volatile candidate fields (same class as #6,
  different subsystem).

## Next steps
1. Remaining open issues: #2 (coding-CLI timeout default + per-worker UI exposure),
   #1 (execution-based verification for code_edit steps).
2. Optionally file the deferred items above.

Working tree after close-out: only .gitignore (pre-existing session noise) + untracked
.agents/.claude/.review-store/uv.lock remain uncommitted, same as session start.
