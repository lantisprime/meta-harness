# Session Handoff — meta-harness (2026-07-08)

## State: COMPLETE, live-validated. 144/144 tests.
All pillars built and exercised by the user against real local models: identity
(Ed25519 registry/tokens/hash-chained provenance), Runner layer + enrichment
(ToolOffload/SelfConsistency/SchemaGuard/SelfCritique), OpenAICompatWorker (LM
Studio/Ollama; response_format + reasoning_content quirks handled), router +
CapabilityMatrix, durable journaled WorkflowEngine (HITL, crash-resume), planner
(goal→WorkflowSpec + deterministic derived checks), two-speed learning (Reflexion
+ MAST clusters + auto-curated persisted playbook), pass^k eval gate, wizard WebUI
(Agents→Goal→Plan→Run→Done + Console pill; styled after structure-discovery-lab).

## Run it
`.venv/bin/metaharness serve --local --pick small=gemma-4-26b --pick mid=qwen3-coder-30b --pick frontier=qwen3.6-35b-a3b`
(:8321; `--critique` optional). Persistence: `~/.metaharness/{playbook,matrix,failures}.json` + `journals/`
(write-through, loaded at boot, interrupted runs auto-advanced). 16 journaled runs exist.

## Key decisions/lessons
- Verified-only learning: matrix/clusters learn from checkable outcomes; UNVERIFIED never counts.
- Checks derivable only when ground truth is in the goal (labels, arithmetic,
  quoted literals — planner-derived deterministically); data-hidden truth → eval harness.
- Failures must be loud: bad refs/crashes fail runs visibly (regression-tested);
  never silent "running".
- External-role framing for reflections (arXiv 2606.05976); reflection never leaks equals-answers.
- tests/test_wiring.py = integration sweep with text-answering workers; add to it
  whenever a new cross-component wire appears.

## Next steps (all offered, none started)
1. First git commit + README (repo has NO history; commit trailer: Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>).
2. Eval suite gemma vs qwen (labeled tasks, k=3) → matrix at scale + go/no-go report.
3. Post-step HITL (approve output content, not just execution).
4. Matrix per-worker (not per-model) if same model serves two tiers.

## User prefs this session
Monitoring subagents on haiku (saved to global memory). Wizard UI over dense
dashboards. Persist everything learned.
