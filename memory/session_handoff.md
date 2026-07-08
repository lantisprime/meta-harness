# Session Handoff — meta-harness (2026-07-08, session 2)

## State: v0.2 SHIPPED. 193/193 tests, CI green, 6 commits pushed.
Repo: github.com/lantisprime/meta-harness (PRIVATE; branch protection deferred —
Pro-gated; ready-to-apply config lives in the episodic workplan episode).

## Shipped this session
- **Config store** (`config.py`+`factory.py`): providers/agents/MCP →
  `~/.metaharness/config.json` (0600, obfuscated keys, masked over HTTP);
  configured agents rebuilt at boot before --local discovery.
- **Coding agents** (`harness/coding.py`): pi/codex/opencode/claude headless in
  jailed workspaces; all 4 detected locally. Harness can implement plans.
- **Tools+MCP** (`tools/`): registry w/ adaptive per-step subsets (cap 7),
  jailed file tools, MCP stdio+HTTP loader (`pip install -e '.[mcp]'`);
  OpenAICompatWorker tool-call loop; planner detects per-step tools.
- **Context mgmt** (`context.py`): per-tier budgets, on-the-fly pruning (tool
  obs first, edges never), loud digests.
- **Workflow types** (`workflows/templates.py`): software_engineering =
  agentic SDLC (explore→specify⛔→plan⛔→implement→verify→review⛔) + research;
  deterministic instantiation, `workflow_type` on /api/plans.
- **SDLC eval** (`evals/sdlc.py`): 11 deterministic per-phase probes, pass^k.
- **UI**: Settings view; provider + agent wizards (system-prompt archetypes);
  workflow-type pills; tool badges in plan review; MCP add form.

## Verification done
Full pytest suite; node --check on dashboard JS; live HTTP smoke of the whole
config flow (provider CRUD, persisted agent add/retire, template plan).
NOT yet done: SE template run with a REAL coding CLI agent; browser-visual pass.

## Next steps
1. Run SE template end-to-end with real local models + a coding CLI agent.
2. gemma vs qwen eval at scale (sdlc_capability_suite + run_suite/compare_suites).
3. Post-step HITL (approve output content, not just execution).
4. Branch protection when repo goes public / Pro.
5. MCP hot-reload endpoint (servers currently load only at startup).

Knowledge base: 3 new files (agentic-sdlc, coding-agent-clis-mcp,
context-engineering). Episodic workplan episode: 20260708-085401-….
