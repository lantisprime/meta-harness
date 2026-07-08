---
topic: Context engineering & dynamic tool injection for LLM agent harnesses
fetched: 2026-07-08
summary: Practices for dynamic context assembly, compression, tool-subset injection,
  and cross-agent context isolation (arXiv 2024-2026 + Anthropic/Manus/LangChain blogs),
  targeted at a Python meta-harness orchestrating small (4-32k ctx) worker LLMs.
---

# Context Engineering for Agent Harnesses (2024-2026)

## Practices ranked by evidence strength

Tier 1 (convergent):
1. Inject a small per-task TOOL SUBSET, never the full catalog. Function-calling accuracy
   degrades with tool count; MCP-scale catalogs (140k-token action spaces) cap frontier
   models at ~58% (MCPVerse 2508.16260); MCP integration averaged -9.5% vs standalone
   (2508.12566). RAG over tool descriptions: >50% token cut, ~3x selection accuracy
   (RAG-MCP 2505.03275); MCP-Zero (2506.01056) 98% token cut via tool *requests* +
   hierarchical routing. Adaptive shortlist depth ~7 beats fixed-k (BoR 2605.24660:
   93.1% vs 87.1%). Small models degrade far more than frontier with many tools.
2. Keep a KV-CACHE-STABLE PREFIX; volatile content is suffix (Manus). No timestamps in
   system prompt, deterministic serialization (sorted keys), append-only history,
   never add/remove tool definitions mid-episode (select at step boundary, then FREEZE).
3. ISOLATE worker context: structured contract in (instruction, context, tools, model —
   AORCHESTRA 2602.03786), distilled <=2k-token summary out (Anthropic/LangChain).
   Workers never inherit orchestrator history, sibling transcripts, or full tool registry.
4. Compress by PRUNING TOOL OUTPUTS FIRST (replace consumed observations with
   re-fetchable refs — safest compaction, Anthropic), hierarchical summarization second
   (FoldAct 2512.22733, CompactionRL 2607.05378, ACON 2606.06708); task-conditioned
   pruning beats generic truncation (Squeez 2604.04979).

Tier 2:
5. EXPLICIT PER-SECTION BUDGETS (ContextBudget 2604.01664: budgeted sequential decision,
   1.6x at tight budgets). AdaCoM (2605.30785): weaker agents need MORE aggressive
   compression, stronger need fidelity -> tie compression ratio to capability tier.
   MemFlow (2605.03312): small-model agents run on ~2.2k tokens of tiered memory.
6. Constraints at EDGES, bulk data middle (lost-in-the-middle 2307.03172), but
   2510.14842 finds no consistent position effect for instructions in modern models —
   keep layout deterministic, don't over-engineer ordering.
7. Playbooks as itemized delta-updated bullets, deterministic merge (ACE 2510.04618);
   retrieve a per-task SLICE, not the whole playbook; promote recurring bullets to
   skills (2601.21557); evolving structured task context (COMPASS 2510.08790).

Tier 3:
8. JIT retrieval over pre-loading: identifiers + fetch tools (progressive disclosure);
   defer-loaded tool schemas + tool-search ~85% token cut (Anthropic advanced-tool-use);
   code-execution-with-MCP keeps intermediate results out of context.
- Tool descriptions: uniform style/length — selection swayed by wording (2505.18135).

## Design sketch adopted for metaharness
ToolInjector: capability/boundary hard filter -> semantic rerank vs objective ->
adaptive depth (score cliff, cap ~7) -> add request_more_tools escape hatch -> freeze
for episode, deterministic schema render.
ContextAssembler budget envelope (reserve 25-40% for generation): role+rules ~10% |
tool schemas ~15-25% | playbook slice ~10% (by-id order) | task block ~10% |
inputs/prior outputs/exemplars = remainder (distilled only; artifacts as refs) |
output_schema restated LAST ~5%.
Compression policy: per-tier fold thresholds; consumed observations -> digest + ref;
step-boundary progress notes (decisions/constraints verbatim); worker returns
{schema_result, distilled_summary<=2k}; summary is all that crosses back.

## Sources
- 2505.03275 RAG-MCP; 2506.01056 MCP-Zero; 2605.24660 BoR adaptive tool depth;
  2410.14594 Toolshed; 2511.01854 Tool-to-Agent Retrieval; 2507.21428 MemTool;
  2508.16260 MCPVerse; 2508.12566 Help or Hurdle; 2505.18135 unreliable tool prefs
- 2604.01664 ContextBudget; 2605.30785 AdaCoM; 2512.22733 FoldAct; 2607.05378
  CompactionRL; 2606.06708 ACON; 2604.04979 Squeez; 2605.03312 MemFlow
- 2307.03172 Lost in the Middle; 2403.04797 Found in the Middle; 2507.22887 demo
  position; 2510.14842 no-consistent-instruction-position
- 2510.04618 ACE; 2601.21557 skill evolution; 2510.08790 COMPASS; 2602.03786
  AORCHESTRA; 2604.08224 externalization review
- Blogs: anthropic.com/engineering/effective-context-engineering-for-ai-agents,
  /advanced-tool-use, /code-execution-with-mcp; manus.im context-engineering lessons;
  langchain.com/blog/context-engineering-for-agents
