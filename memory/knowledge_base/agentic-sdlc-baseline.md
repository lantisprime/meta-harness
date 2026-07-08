---
topic: Agentic SDLC baseline (2025-2026)
fetched: 2026-07-08
summary: >
  Canonical agentic SDLC synthesized from GitHub Spec Kit, Amazon Kiro, Anthropic
  Claude Code best practices, OpenAI Codex best practices, and agentic-SDLC vendor
  writeups. 8-phase baseline (Context -> Explore -> Specify -> Plan -> Decompose ->
  Implement -> Verify -> Review/Ship) with per-phase gates; benchmark capability map
  (SWE-bench Verified/Pro, Terminal-Bench, Aider polyglot); failure-mode taxonomy for
  small models; per-phase metrics for a pass^k eval harness.
urls:
  - https://code.claude.com/docs/en/best-practices
  - https://github.com/github/spec-kit/blob/main/spec-driven.md
  - https://kiro.dev/docs/specs/
  - https://developers.openai.com/codex/learn/best-practices
  - https://www.coderabbit.ai/guides/agentic-sdlc
  - https://openai.com/index/introducing-swe-bench-verified/
  - https://arxiv.org/abs/2509.16941
  - https://www.tbench.ai/
  - https://aider.chat/docs/leaderboards/
  - https://arxiv.org/pdf/2512.07497
---

# The Agentic SDLC (2025–2026): Baseline Workflow for Testing Agentic-Coding Models

## Synthesis

There is no single ratified standard, but by 2025–2026 the major vendors converged on
the same shape: **specification/plan artifacts before code, verifiable "done" criteria
per phase, automated verification loops inside phases, and human approval gates between
them.** Three named phase models dominate:

- **GitHub Spec Kit (spec-driven development):** Constitution → `/specify` → `/plan` →
  `/tasks` → implement. Each phase emits a reviewable artifact (`spec.md`, `plan.md`,
  `tasks.md`); pre-implementation "gates" (simplicity, anti-abstraction,
  integration-first) plus forced `[NEEDS CLARIFICATION]` markers block progress until
  ambiguity is resolved. Test-first is constitutional: no implementation before tests.
- **Amazon Kiro:** Requirements (`requirements.md`, user stories + EARS acceptance
  criteria) → Design (`design.md`, architecture + diagrams + test strategy) → Tasks
  (`tasks.md`, discrete trackable items executed in dependency "waves"). Explicit
  **approval gates between phases**; "Quick Plan" mode that skips gates is called out
  as the exception, confirming gated is the default.
- **Anthropic Claude Code:** Explore → Plan → Implement → Commit, with plan mode
  enforcing read-only exploration and a human-editable plan before code. Its core
  doctrine: *give the agent a check it can run* — a pass/fail signal (tests, build,
  lint, screenshot diff) closes the loop so the agent iterates until green instead of
  stopping at "looks done." Escalation ladder for gating: prompt-level check →
  goal condition → deterministic Stop hook → fresh-context adversarial review subagent.
- **OpenAI Codex** frames every task as Goal / Context / Constraints / **Done-when**
  (a verifiable completion condition), with durable rules in AGENTS.md (the agent is
  trained to run tests named there before finishing) and a TDD pattern: commit failing
  tests first, then implement with an explicit "don't modify the tests" constraint.
- **Whole-lifecycle writeups** (CodeRabbit, Microsoft/Azure, Forbes/PwC) extend this to
  Plan → Develop → Test → Review → Deploy/Operate, with human judgment concentrated at
  intent validation, architecture approval, and pre-deploy sign-off; review is
  identified as the primary bottleneck.

### Recommended baseline agentic SDLC (for a test harness)

| # | Phase | Input | Output | Verification gate | Human-gated? |
|---|-------|-------|--------|-------------------|--------------|
| 0 | **Context setup** | repo | rules file (CLAUDE.md/AGENTS.md/constitution): test cmds, conventions, constraints | static; exists before work | yes (owner-authored) |
| 1 | **Explore / Localize** | task + repo | context summary: relevant files, patterns, root cause hypothesis | read-only mode enforced; cited files exist and are relevant | no |
| 2 | **Specify** | intent + exploration | spec with user stories + testable acceptance criteria; out-of-scope list; no unresolved `[NEEDS CLARIFICATION]` | ambiguity check; every requirement testable | **yes (default)** |
| 3 | **Plan** | spec | technical plan: files to change, interfaces, test strategy, risks | plan review (optionally second-opinion in fresh context); consistency with spec + constitution | **yes (default)** |
| 4 | **Decompose** | plan | ordered task list, each with its own done-when; dependency graph | mechanical: every task small, verifiable, dependency order valid | no |
| 5 | **Implement** (per task) | task | code + tests written alongside (or failing-test-first) | automated: tests pass, lint/typecheck green, locked tests unmodified | no (agent loop) |
| 6 | **Verify (E2E)** | full diff | evidence: full-suite results, e2e run, screenshots/logs | all FAIL→PASS criteria met, zero PASS→PASS regressions; agent shows evidence, not assertions | no |
| 7 | **Review + Ship** | diff + plan | dispositioned findings; commit + PR | fresh-context adversarial review of diff vs plan; CI green | **yes (merge approval)** |

Default human gates: spec approval (2), plan approval (3), merge (7). Phases 5–6 are
where autonomy lives; their gates must be machine-checkable or the human becomes the
verification loop (the canonical anti-pattern every vendor warns about).

## Benchmarks → capability map

| Benchmark | Verifies | Capability isolated |
|---|---|---|
| **SWE-bench Verified** (500 human-validated GitHub issues; 93 annotators filtered 68.3% of original for underspecified issues/unfair tests) | FAIL_TO_PASS tests pass, PASS_TO_PASS don't regress | localization + repo-level bug-fix; Python-only |
| **SWE-bench Pro** (1,865 long-horizon, multi-file, contamination-resistant enterprise tasks; frontier ~23% vs ~70%+ on Verified) | held-out test suites | long-horizon multi-file engineering; failure clustering shows Opus-class fails on semantics (wrong solution 35.9%), Sonnet-class on context overflow (35.6%) + endless file reading (17%) |
| **Terminal-Bench 2.x** (~89 containerized tasks; Stanford/Laude, vendor-neutral) | per-task test scripts in container | long-horizon shell/tool execution, environment ops, sustained state |
| **Aider polyglot** (225 Exercism exercises, 6 languages) | unit tests + edit-format well-formedness; pass1/pass2 | precise instructed editing across languages (counters Python overfit); format compliance |

Coverage note: no single benchmark spans the whole SDLC — Verified/Pro test phases 1+5+6,
Terminal-Bench tests tool execution, Aider tests the edit primitive. None test spec/plan
quality or gate compliance, which is exactly the gap a phase-based harness fills.

## Failure modes (where small/local models break)

From arXiv 2512.07497, SWE-bench Pro trajectories, and tool-calling studies:

- **Small (<7B):** near-zero tool invocation, confabulated answers instead of tool calls,
  malformed calls/edit formats, catastrophic multi-step chain failure, compound
  cascading errors. Same-size models can differ by ~89pp on knowing when *not* to call
  a tool (ToolFailBench).
- **Mid-size:** context overflow, endless-read loops, state-tracking loss (repeating or
  forgetting prior actions), poor error recovery (retrying the identical failing action).
- **Frontier:** semantic wrong-solutions, edge cases, suboptimal step ordering; plus
  premature success claims and test-gaming (deleting/weakening tests) — why "locked
  tests" and evidence-not-assertion gates exist.
- **Long-horizon specific:** reliability decays with duration; pass@1 hides it — hence
  pass^k (all k runs succeed) as the gating metric.

## What to measure per phase (pass^k harness metrics)

All metrics machine-checkable; grade each phase independently and end-to-end; report
pass^k (k≥4) not pass@1.

- **Explore/Localize:** file- and function-level localization recall vs gold patch;
  read-only compliance (zero writes in plan mode); reads-to-localization count;
  context-overflow / endless-read incidence.
- **Specify:** fraction of acceptance criteria that are mechanically testable; ambiguity
  detection rate on seeded-underspecified tasks (does it ask vs guess); scope leakage.
- **Plan:** overlap of planned file set with gold-patch file set (precision/recall);
  plan includes a verification step; later plan-adherence (share of edits within
  planned files).
- **Decompose:** every task has a done-when; dependency-order validity; task size
  distribution.
- **Implement:** FAIL_TO_PASS resolution rate; PASS_TO_PASS regression rate; edit
  well-formedness (Aider-style format compliance); locked-test-modification rate
  (must be 0); tool-call validity rate; iterations-to-green.
- **Verify:** evidence provision (test output actually shown/ran vs asserted);
  premature-completion rate (claims done while checks fail); root-cause vs
  symptom-suppression (error silenced vs fixed).
- **Review/Ship:** self-review finding validity (precision of reported gaps against
  seeded bugs); finding-disposition discipline; commit hygiene.
- **Process compliance (cross-phase):** gate-skip rate (code edits before plan
  approval); phase-ordering violations; recovery rate after injected tool errors;
  turns/tokens to completion (long-horizon efficiency).

## Sources

- https://code.claude.com/docs/en/best-practices — Anthropic's canonical Explore→Plan→Implement→Commit workflow; "give Claude a check it can run" as the core gating doctrine; escalation from prompt-check to Stop hook to adversarial review subagent.
- https://github.com/github/spec-kit/blob/main/spec-driven.md — Spec-driven methodology: constitution + Specify/Plan/Tasks/Implement, pre-implementation gates, forced `[NEEDS CLARIFICATION]`, test-first mandate; spec-as-source-of-truth philosophy.
- https://github.com/github/spec-kit — Spec Kit toolkit: templates/CLI/prompts, works across 30+ agents; Spec→Plan→Tasks→Implement with human review of artifacts before code generation.
- https://kiro.dev/docs/specs/ — Kiro three-phase spec workflow (requirements.md / design.md / tasks.md) with approval gates between phases; dependency-wave parallel task execution.
- https://developers.openai.com/codex/learn/best-practices — Codex task anatomy Goal/Context/Constraints/Done-when; AGENTS.md as durable rules the agent runs tests from; failing-tests-first TDD with immutable tests.
- https://www.coderabbit.ai/guides/agentic-sdlc — Whole-lifecycle 5-phase agentic SDLC (Plan/Develop/Test/Review/Deploy-Operate); human judgment at intent, architecture, and pre-deploy; review as the bottleneck.
- https://openai.com/index/introducing-swe-bench-verified/ — SWE-bench Verified: 500 tasks, 93 annotators, 68.3% of original filtered for underspecification/unfair tests; FAIL_TO_PASS + PASS_TO_PASS grading. (403 on direct fetch; corroborated via swebench.com/verified.html and secondary summaries.)
- https://arxiv.org/abs/2509.16941 — SWE-bench Pro: 1,865 long-horizon multi-file contamination-resistant tasks; failure-mode clustering per model (semantic errors vs context overflow vs endless reading).
- https://www.tbench.ai/ — Terminal-Bench: containerized long-horizon terminal tasks verified by per-task test scripts; vendor-neutral (Stanford/Laude); isolates sustained shell/tool execution.
- https://aider.chat/docs/leaderboards/ — Aider polyglot: 225 Exercism exercises across 6 languages; measures instructed-edit correctness and edit-format well-formedness (pass1/pass2).
- https://arxiv.org/pdf/2512.07497 — Agentic failure taxonomy: planning, tool misuse, state tracking, recovery, output formatting; small models fail on fundamentals/compound cascades, large models on subtle semantics.
- https://arxiv.org/html/2603.29231v1 — Reliability-science framing for long-horizon agents: pass@1 insufficient; duration-bucketed reliability metrics motivate pass^k gating.
