---
url: multiple (arxiv, braintrust.dev, docs.anthropic.com, dspy.ai — see inline)
fetched: 2026-07-08
summary: Self-correction & learning-from-mistakes for agent systems — reflection loops, verifier-based correction, memory architectures (ACE/ExpeL/Voyager), MAST failure taxonomy, GEPA prompt optimization, runaway-loop guardrails.
---

# Self-Correction & Learning-from-Mistakes for AI Agent Systems — Research Notes

Fetched/searched: 2026-07-08. Sources marked (fetched) were read directly; others distilled from search-result summaries.

## 1. Reflection / Self-Critique Loops

- **The single most robust finding in this literature: intrinsic self-correction (model critiques its own output with no external signal) does not reliably work; correction grounded in an external signal does.** Huang et al., "LLMs Cannot Self-Correct Reasoning Yet" (ICLR 2024, https://arxiv.org/pdf/2310.01798) showed intrinsic self-correction often *degrades* performance. Follow-ups sharpen the picture:
  - "The Self-Correction Illusion: LLMs Correct Others but Not Themselves" (https://arxiv.org/html/2606.05976) — the same model that catches errors in *external* content routinely misses identical errors in its own reasoning trace. Design implication: route critique to a *different* context/persona/model than the generator, even if it's the same base model.
  - Information-theoretic explanation: generator and self-evaluator have *correlated errors*, so self-verification adds little independent signal (https://www.preprints.org/manuscript/202601.0892).
  - "Self-correction is Not An Innate Capability" (https://arxiv.org/pdf/2410.20513) — correction ability largely comes from training (e.g., RLHF/RL on correction traces), not prompting.
- **What does work:**
  - **Reflexion** (verbal RL: store natural-language reflections on failures in episodic memory, retry with them in context) — +10–20pp on coding/QA benchmarks, 91% pass@1 HumanEval in the original paper; still the canonical cheap pattern because it needs only an environment success/fail signal, no weight updates.
  - **Self-Refine** (separate generate / feedback / refine prompts) — ~20% avg absolute gains across tasks *when feedback is checkable*; gains shrink toward zero on open-ended reasoning without a grader.
  - **CRITIC** (https://arxiv.org/abs/2305.11738) — "tool-interactive critiquing": Verify → Correct → Verify loop where verification is done via external tools (code interpreter, search, toxicity API), not introspection. Consistent gains across QA/math/detox; paper explicitly demonstrates self-verification without tools is unreliable. This is the right template: **critic = tool run, not vibes.**
  - **Structured reflection for tool use** — "Failure Makes the Agent Stronger" (https://arxiv.org/pdf/2509.18847): making reflection a *structured, first-class action* (diagnose error → propose repair → retry) beats free-form "think about what went wrong."
  - **Training-time internalization**: SCoRe (multi-turn RL for self-correction, +15.6% MATH, +9.1% HumanEval), PAG (policy-as-generative-verifier, https://arxiv.org/pdf/2506.10406), Retrospective Progress-Aware Self-Refinement (https://arxiv.org/pdf/2606.14302). Relevant only if you fine-tune; for a harness orchestrating black-box models, the CRITIC/Reflexion pattern is the practical path.
- **Production vs papers**: production writeups (e.g., https://www.buildmvpfast.com/blog/ai-agent-self-improvement-recursive-accuracy-production-2026, https://zylos.ai/research/2026-03-06-ai-agent-reflection-self-evaluation-patterns) converge on: 1–2 reflection iterations capture most of the gain; diminishing/negative returns after that; always pair reflection with an objective check (test run, schema validation) before accepting the "corrected" output.

**Design rule for the harness**: never let the actor grade itself in-context. Every correction loop must be anchored to at least one of: execution result, tool output, separate verifier model, or human signal.

## 2. Verifier-Based Correction

- **Hierarchy of verification trustworthiness** (use the highest tier available per task): (1) execution/tests (compile, unit tests, type checks, schema validation, dry-runs) — treat as ground truth; (2) deterministic code scorers (exact match, citation-exists checks); (3) rubric-scored LLM judge; (4) free-form LLM judge — weakest, use only for style/tone.
- **When does a separate verifier pay off?** "When Does Verification Pay Off?" (https://arxiv.org/pdf/2512.02304): verification helps most when verifier errors are decorrelated from generator errors — i.e., different model family, or verifier armed with tools the generator lacked. Verifier ≈ generator clone re-reading the same trace adds little.
- **LLM-as-judge reliability, current state:**
  - Judges show systematic biases: length/elaboration preference, position bias, fluency-over-factuality (https://medium.com/@adnanmasood/rubric-based-evals-llm-as-a-judge-methodologies-and-empirical-validation-in-domain-context-71936b989e80).
  - **Rubrics with one criterion per LLM call** beat holistic scoring — Autorubric (https://arxiv.org/html/2603.00077v2) evaluates each criterion in a separate call to avoid halo effects; panel-of-diverse-judges outperforms any single judge.
  - **RuVerBench** (https://arxiv.org/abs/2606.29920v1) — first benchmark of judge reliability specifically for *agentic* outputs (deep research, agentic coding, 2,458 instances); long agent trajectories are notably harder to judge reliably than single responses.
  - Grading-scale matters: human–LLM alignment is highest on a 0–5 scale (https://arxiv.org/pdf/2601.03444).
  - Newer work treats judge reliability psychometrically: calibration-based bias correction with confidence intervals; item-response-theory applied to judges. Also HealthBench-style physician-authored criteria (48,562 criteria) as the gold standard for rubric construction.
- **Judge hygiene for the harness**: version-control judge prompts, calibrate scorer thresholds against human labels before gating anything, re-calibrate when the judge model changes (Braintrust guidance).

## 3. Agent Memory Architectures for Learning from Experience

- **ACE — Agentic Context Engineering** (Stanford/SambaNova, Oct 2025, https://arxiv.org/abs/2510.04618, fetched) — the strongest recent template for a self-improving harness:
  - Context = an **evolving playbook of itemized bullets** (strategy, domain fact, or known failure mode), each with usefulness metadata — not a monolithic prompt.
  - Three roles: **Generator** (executes tasks), **Reflector** (compares trajectories, distills lessons from execution feedback), **Curator** (merges lessons into the playbook via **delta updates**, never wholesale rewrites).
  - Names two failure modes of naive "let the agent rewrite its own prompt": **context collapse** (iterative rewriting erodes detail over time) and **brevity bias** (summarization destroys the domain-specific specifics that made the playbook useful). Delta/grow-and-refine updates are the fix.
  - Results: +10.6% on agent benchmarks, +8.6% finance; works **without labeled supervision** using natural execution feedback; let DeepSeek-V3.1 match a top production agent on AppWorld.
- **ExpeL** (https://arxiv.org/abs/2308.10144) — the two-track memory pattern: (a) store successful trajectories in a vector DB and retrieve top-k as few-shot exemplars; (b) distill *cross-task natural-language insights* (guidelines/constraints, derived from both successes and failures) injected into every prompt. No parameter access needed — directly applicable to black-box orchestration.
- **Voyager** (https://arxiv.org/abs/2305.16291) — skill library as *executable code*: verified skills (code that passed in the environment) stored, embedded, and retrieved for composition. Compositional, interpretable, avoids catastrophic forgetting. 2025 successors: SkillWeaver/WALT (web), OS-Copilot (OS agents); "skill engineering" now means bundles of instructions + scripts + docs + metadata loaded on demand (https://arxiv.org/html/2602.12430v4) — exactly the Claude Code Skills model.
- **Memory-layer products** (https://agentmarketcap.ai/blog/2026/04/10/agent-memory-vendor-landscape-2026-letta-zep-mem0-langmem, https://mcp.directory/blog/mem0-vs-letta-vs-zep-vs-cognee-2026):
  - **Mem0** — bolt-on fact-extraction memory layer; fast fuzzy recall; no first-class temporal indexing.
  - **Zep** — temporal knowledge graph (facts carry valid_at/invalid_at); best for "what was true when" and relational queries.
  - **Letta** (MemGPT lineage) — agent runtime where memory is core; best episodic coherence; slower per retrieval.
  - For a *learning-from-failures* subsystem, episodic + temporal properties matter most (Letta/Zep style), but ACE/ExpeL-style curated markdown playbooks are simpler and proven — files beat vector DBs at this scale.
- **Claude Code's patterns** (https://docs.anthropic.com/en/docs/claude-code/memory, https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool): layered CLAUDE.md read at session start; **auto memory / MEMORY.md** — agent-written notes accumulated across sessions with a size-limited always-loaded index pointing to detail files; API **memory tool** = agent-managed file directory persisting across conversations; **context editing** to prune stale tool results; an idle-time consolidation pass ("Dreams"-style) that merges duplicates and replaces contradicted entries. Key ideas to steal: small always-loaded index + on-demand detail files, and periodic offline consolidation as a separate job.

## 4. Failure Taxonomy & Automated Root-Cause Analysis

- **MAST** (Berkeley, "Why Do Multi-Agent LLM Systems Fail?", https://arxiv.org/abs/2503.13657, https://openreview.net/forum?id=fAjbYBmonr) — first systematic MAS failure taxonomy, from 1,600+ annotated traces across 7 frameworks. **14 failure modes, 3 categories**:
  - *System/specification design* (~44% of failures): disobeying task spec, disobeying role spec, step repetition, losing conversation history, failing to recognize termination conditions.
  - *Inter-agent misalignment* (~32%): ignoring peer input, withheld information, mismatched assumptions.
  - *Task verification & termination*: premature termination, no/incomplete verification, incorrect verification.
  - Headline insight: **most failures are harness-design problems, not model problems** — verification and termination checks are where the leverage is. Use MAST's 14 modes as the label vocabulary for the failure database.
- **TRAIL** — 148 OpenTelemetry agent traces, 841 span-level errors under a 20+ category taxonomy (planning, tool misuse, reasoning breakdowns, resource exhaustion, instruction violations). Sobering result: **even strong long-context models achieve only ~11% accuracy at localizing errors in traces** — fully-automated RCA by an LLM reading raw traces is not yet reliable; you need structured spans + taxonomy labels + narrowing heuristics.
- **Tooling**: LangSmith trajectory evals (https://docs.langchain.com/langsmith/trajectory-evals); Galileo for RCA dashboards (https://galileo.ai/blog/best-ai-agent-debugging-root-cause-analysis-tools); **AgentFixer** (https://arxiv.org/html/2603.29848) goes failure-detection → fix *recommendation*.
- Practical pattern: emit OpenTelemetry-style spans per step; classify failures against a fixed taxonomy (MAST/TRAIL vocabulary) at triage time; cluster by label before attempting fixes.

## 5. Closing the Loop: Failures → Updated Prompts/Playbooks

- **Production-incident → regression-test pipeline** (Braintrust, fetched: https://www.braintrust.dev/articles/turn-llm-production-failures-into-regression-tests) — the canonical 5-step loop:
  1. Capture the full failed trace (input, output, tool calls, retrieved context, prompt version).
  2. Classify the failure mode (hallucination / retrieval miss / tool-arg error / instruction violation / format).
  3. Promote to a versioned regression dataset record (input + expected behavior + failure label + source metadata).
  4. Write a scorer that detects that failure pattern (deterministic where possible, judge otherwise).
  5. Gate releases on the scorer in CI; deploy **the same scorer** as an online production monitor.
  - Pitfalls: overfitting to single traces instead of clustering patterns; judge-scorer drift without version control; losing prompt-version linkage; forgetting positive examples; manual triage that doesn't scale.
- **Automated prompt optimization from failures**:
  - **GEPA** (in DSPy; https://dspy.ai/getting-started/gepa-optimization/, https://github.com/gepa-ai/gepa) — reflective prompt evolution: sample trajectories, have a reflection LM diagnose failures *in natural language*, propose prompt edits, keep a Pareto frontier of candidates. Beats GRPO (RL) by up to 20% with ~35× fewer rollouts; >10% over MIPROv2. **State of the art for turning eval failures into prompt updates automatically.**
  - **OPRO** — put previous candidate prompts + scores in context, ask the model for a better one; simpler, weaker, useful baseline.
  - DSPy optimizer landscape: https://futureagi.com/blog/dspy-optimizers-explained/.
  - Prompt regression in CI with LLM-judge: https://www.traceloop.com/blog/automated-prompt-regression-testing-with-llm-as-a-judge-and-ci-cd; prompt *updates themselves* drive many production incidents (https://deepchecks.com/llm-production-challenges-prompt-update-incidents/) — every automated prompt change must pass the regression suite before adoption.
- **Synthesis for the harness**: two loops at different cadences — *fast loop* (per-task): Reflexion/ACE-Reflector writes a lesson into the playbook after each failure; *slow loop* (batch/offline): cluster labeled failures → GEPA-style optimization of the relevant prompt/routing rule → run regression suite → adopt only on non-regression. Never edit prompts in place without the gate.

## 6. Guardrails Against Runaway Loops

Converging production consensus (https://paxrel.com/blog-ai-agent-guardrails, https://medium.com/@ranju.r/why-agent-loops-fail-without-guardrails-and-how-production-systems-fix-it-12a49985176a, https://wandb.ai/site/articles/guardrails-for-ai-agents/, https://www.arthur.ai/blog/best-practices-for-building-agents-guardrails):

- **Hard iteration cap** per task — stop and report state, don't keep retrying.
- **Token/cost budget** per run and per epic, enforced from day one (~50K in / 10K out per standard task; per-epic caps with auto-suspend).
- **No-progress / plateau detection** — exit when output state hash or score hasn't improved across N iterations; heuristic: if avg(last 5 scores) − avg(earlier) ≤ −0.15 *and* recent avg < 0.5, escalate (switch to stronger model) rather than iterate. Reflection gains die after 1–2 rounds — small retry budget (2–3) is usually optimal.
- **Circuit breakers on tool calls** — per-tool retry limits with explicit failure reporting after N attempts.
- **Termination criteria defined before the loop starts**, checked by *verifiable automated means, never agent self-assessment* (echoes MAST's task-verification failure category).
- **Escalation-to-human policy = explicit decision boundaries**: enumerate what the agent may decide autonomously vs. what triggers human review (irreversible actions, high-stakes thresholds). Mandatory HITL checkpoints before irreversible steps.
- **Step-repetition detection** — detect via action-signature dedup over the trajectory, not just iteration counts.

## Cross-Cutting Design Takeaways for the Meta-Harness

- Ground every correction in an external signal (tests > deterministic scorers > rubric judges); decorrelate verifier from generator.
- Memory as curated playbook files with delta updates and offline consolidation (ACE + Claude Code auto-memory pattern), plus retrieval of past failure episodes as few-shot context (ExpeL), plus verified reusable skills (Voyager/Skills).
- Fixed failure taxonomy (MAST/TRAIL vocabulary) applied at triage; cluster before fixing.
- Two-speed learning loop: per-task lesson writing (fast) and gated batch prompt optimization à la GEPA (slow), with production failures auto-promoted to a regression suite that gates all prompt/playbook changes.
- Small retry budgets, plateau detection, verifiable termination, and human escalation boundaries baked into the loop runner — most agent failures are harness-design failures.
