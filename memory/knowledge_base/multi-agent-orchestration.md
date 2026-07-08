---
url: multiple (anthropic.com/engineering, langchain.com, learn.microsoft.com, databricks.com — see inline)
fetched: 2026-07-08
summary: Multi-agent orchestration landscape — LangGraph/MS Agent Framework/CrewAI/OpenAI Agents SDK/Claude Agent SDK/Google ADK; supervisor vs swarm vs durable workflow engines; Anthropic orchestrator-worker lessons; MCP/A2A interop; meta-harness pattern (Databricks Omnigent); workflow encoding, checkpointing, HITL gates.
---

# Multi-Agent Orchestration & Meta-Orchestrator Landscape — Research Notes (fetched 2026-07-08)

## 1. Major Frameworks

- **LangGraph** (v1.0 GA Oct 2025; Uber/LinkedIn/Klarna in prod): low-level graph runtime — nodes, conditional edges, shared typed state; supervisor/swarm/hierarchical as libraries; `Send` API for fan-out. Checkpointer saves state per "superstep" (Postgres/SQLite); **no persistence inside a node** (crash mid-node loses work). `interrupt()` for HITL. https://www.langchain.com/blog/langchain-langgraph-1dot0
- **Microsoft Agent Framework** (AutoGen + Semantic Kernel successor; 1.0 GA Apr 2026, Python+.NET): ChatAgent abstractions + **Workflows** (graph-native executors/edges, deterministic). Checkpointing serializes *pending requests* — restore re-emits `RequestInfoEvent`s = HITL mechanism. Native MCP, A2A. https://learn.microsoft.com/en-us/agent-framework/overview/
- **CrewAI**: **Crews** (role-based autonomous teams) + **Flows** (event-driven Python: `@start`/`@listen`/`@router`, `@persist`) — recommended pattern is deterministic Flow skeleton + autonomous Crew islands ("gradual autonomy"); Flows are the admission that pure autonomous crews aren't production-safe. Weaker durability than LangGraph/Temporal. https://docs.crewai.com/
- **OpenAI Agents SDK** (Mar 2025, Swarm successor): Agents, **Handoffs** (peer transfer, swarm-style), Guardrails, Tracing; agents-as-tools for supervisor style. No native durable execution — **Temporal integration** (GA Mar 2026): agent invocation = Activity, orchestration = Workflow. AgentKit visual builder exports to SDK code. https://openai.github.io/openai-agents-python/multi_agent/
- **Claude Agent SDK / Claude Code**: subagents with **context isolation as core design** (fresh context; in = prompt string, out = final message only); background by default; nest to 5 levels. **Workflow tool** moves orchestration into a JS script (`agent()`, `pipeline()`; 16 concurrent / 1,000 agents; resumable; **no mid-run user input** — stage-gated = one workflow per stage). Agent teams for long-running peers. https://code.claude.com/docs/en/agent-sdk/subagents ; https://code.claude.com/docs/en/workflows
- **Google ADK** (Apr 2025): strict hierarchy (one parent per agent); deterministic workflow agents (Sequential/Parallel/Loop) + LLM-driven `transfer_to_agent`; shared Session State "whiteboard"; first-class MCP; A2A sibling protocol; Vertex AI deploy. https://google.github.io/adk-docs/

## 2. Supervisor vs Swarm vs Durable Workflow Backbones

- **Supervisor**: hub-and-spoke; central LLM routes each subtask, validates outputs. Failures: single point of failure, context saturation after ~8–12 worker round trips, synchronous routing bottleneck.
- **Swarm/handoff**: distributed routing, peer-to-peer control transfer. Failures: agent drift over 8–10+ handoffs, duplicate work, O(n²) failure surface, hard to debug.
- Measured (Augment Code): swarm ≈ 7+ calls/~14k tokens vs supervisor ≈ 5 calls/~9k; supervisor's 20–40% coordination overhead offset by ~30% token reduction from deduped work. Heuristics: 1–3 roles → pipeline + terminal reviewer; 3–5 → flat supervisor (default); 5+ independent sub-teams → hybrid only after measuring. https://www.augmentcode.com/guides/swarm-vs-supervisor
- **Durable engines (Temporal/Inngest/Restate)**: event-sourced replay — every step journaled; crash → replay-skip. 2025 = durable execution crossed to early majority driven by agent infra. **Consensus architecture: durable engine for the macro layer (job lifecycle, retries, checkpoint/resume, HITL waits) + agent framework for the micro layer (reasoning loop inside a step) — "Temporal outside, LangGraph inside."** Use a workflow engine when the process is known in advance / must survive failures / needs audited HITL; supervisor when decomposition is unpredictable; swarm when routing is self-evident. https://www.inngest.com/blog/durable-execution-key-to-harnessing-ai-agents

## 3. Anthropic's Published Guidance

### "Building effective agents" (Dec 2024) — https://www.anthropic.com/engineering/building-effective-agents
- **Workflows** = predefined code paths; **agents** = LLM directs its own process. Five patterns: prompt chaining (+gates), routing, parallelization (sectioning/voting), **orchestrator-workers** (dynamic decomposition of unpredictable subtasks), evaluator-optimizer (needs clear criteria).
- Doctrine: start with one optimized LLM call; add complexity only when measured to help; simplicity, transparency of planning, carefully crafted **agent-computer interfaces** (tool docs matter as much as prompts).

### "How we built our multi-agent research system" (Jun 2025) — https://www.anthropic.com/engineering/multi-agent-research-system
- Lead agent (Opus) plans, spawns 3–5 parallel subagents (Sonnet); multi-agent beat single-agent Opus by **90.2%**; **token usage explained 80% of performance variance** — the real win is scaling reasoning tokens across separate context windows. Cost: multi-agent ≈ **15×** chat tokens.
- **Delegation must be explicit**: objective, output format, tool/source guidance, boundaries. Effort-scaling rules in the lead prompt (simple fact = 1 agent/3–10 calls; complex = 10+ subagents).
- Tool descriptions are load-bearing: a tool-testing agent rewriting flaky descriptions cut task time 40%.
- Reliability: **resume from checkpoints, don't restart**; surface tool failures to the model; **rainbow deployments** (agents are mid-run during deploys); trace decision patterns.
- Bottleneck: synchronous subagent execution — no mid-run steering; async is future work.
- **Scope warning: poor fit for dense-dependency domains where all agents need shared context — "like most coding."** Prefer decomposition into genuinely independent units (per-file/per-worktree).

## 4. Cross-Harness Interop: MCP, A2A, Meta-Harnesses

- **MCP**: agent↔tool/data; JSON-RPC client-server. Donated to **Agentic AI Foundation** (Linux Foundation) Dec 9, 2025 (Anthropic, Block, OpenAI co-founders). 10,000+ public servers, 97M+ monthly SDK downloads. June 2025 spec: MCP servers = OAuth resource servers, RFC 8707 Resource Indicators. https://www.anthropic.com/news/donating-the-model-context-protocol-and-establishing-of-the-agentic-ai-foundation
- **A2A**: agent↔agent across boundaries — Agent Cards (signed in v1.0), task lifecycle, artifacts, streaming. Linux Foundation since Jun 2025; v1.0 stable, 150+ orgs by Apr 2026. Reality: valuable for cross-team/vendor delegation, overkill for local synchronous systems; **security/trust (identity, delegation tracking, artifact provenance) largely unresolved**. **Layering consensus: MCP for tools, A2A for peers.** https://www.glukhov.org/ai-systems/comparisons/a2a-protocol-2026-adoption
- **Meta-harness term of art (2026)**: harness = runtime around a model (tools, context, permissions); meta-harness sits above and makes them interoperable. **Databricks Omnigent** (OSS Jun 2026, Apache 2.0, alpha) = reference implementation: *"however each harness calls its LLM internally, the interface is the same: messages and files in, text streams and tool calls out"* — a **runner** wraps any terminal harness (Claude Code, Codex, Pi) or SDK behind a uniform API; server layer adds **stateful contextual policies** (e.g., pause after $100 spend), OS-level sandboxing with network interception, live session sharing/steering, cloud execution (Modal, Daytona, Fly.io). https://www.databricks.com/blog/introducing-omnigent-meta-harness-combine-control-and-share-your-agents
- Practical control planes for CLI harnesses today: (a) headless modes (`claude -p` + Agent SDK streaming JSON; Codex CLI exec; Aider scripting); (b) PTY wrapping + uniform stream parsing (Omnigent; "Agent Orchestrator" IDE — per-agent branches/worktrees); (c) MCP/A2A endpoints where exposed. **Git worktree isolation per agent is the standard conflict-avoidance mechanism.** https://addyosmani.com/blog/code-agent-orchestra/

## 5. Prescribed Workflows: Definitions, Checkpointing, HITL

- **Encoding**: code-as-workflow dominant for engineering (Temporal/Inngest, CrewAI Flows, LangGraph, Claude Code workflow scripts saved as versioned slash commands); YAML/JSON DSL where business+eng collaborate (MS Foundry dual visual/YAML views); visual builders export to code — **visual is an authoring surface, code is source of truth**. Hybrid: markdown/frontmatter agent definitions portable across harnesses, orchestration logic in code.
- **Checkpointing**: two granularities, both needed — step-level journaling (every side effect recorded; crash → replay-skip) and graph-state snapshots. LangGraph's inside-a-node blind spot is the canonical argument for pairing with a durable engine.
- **HITL**: pause-as-first-class-state — LangGraph `interrupt()`; MS `RequestInfoEvent` + checkpoint; Temporal signals (durable wait for days at zero compute). Gate placement: plan approval, before irreversible actions, stage boundaries, low-confidence triggers. Governance trend: workflow definitions as version-controlled artifacts reviewed via PR; **policies enforced by the runtime, not prompt text**.

## Cross-Cutting Takeaways for a Meta-Harness

- Layer cleanly: durable deterministic spine (journaled code-as-workflow) → supervisor LLM for dynamic decomposition within steps → wrapped harnesses as workers behind a uniform "messages+files in, streams+tool-calls out" runner interface.
- The delegation contract matters more than topology: explicit objective/format/boundaries per task, effort-scaling rules, context isolation with summary-only returns.
- Budget for 15× tokens; token throughput is the primary performance lever; checkpoint everything; synchronous fan-out first, async steering later.
- MCP to give the orchestrator tools; A2A only across trust/vendor boundaries; headless modes + PTY wrapping for local CLI harnesses.
