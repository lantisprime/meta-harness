---
url: multiple (lmsys.org, arxiv, huggingface, dspy.ai — see inline)
fetched: 2026-07-08
summary: Model routing/cascades (RouteLLM, FrugalGPT, AutoMix, commercial routers) and techniques lifting small models toward frontier — test-time compute, harness/scaffolding effects (22–36pp SWE-bench swings), PAL tool offloading, GEPA/DSPy prompt compilation, RAG/exemplar retrieval, production tiering guidance.
---

# Model Routing & Small-Model Enrichment — Research Notes (fetched 2026-07-08)

## 1. Model Routing & Cascades

- **RouteLLM (LMSYS/Berkeley, ICLR 2025)** — learned routers on Chatbot Arena preference data; picks weak vs strong model per query before generation. Matrix factorization best of 4 architectures. Holding 95% of GPT-4 quality: cost cut **>85% MT-Bench, 45% MMLU, 35% GSM8K**; with judge-data augmentation, 95% quality at **14% strong-model calls**. Routers **generalize to unseen model pairs** (route on *query difficulty*, not model identity). Matched Martian/Unify while >40% cheaper. https://www.lmsys.org/blog/2024-07-01-routellm/ ; https://arxiv.org/abs/2406.18665 ; https://github.com/lm-sys/routellm
- **FrugalGPT (Stanford)** — sequential cascade: cheap model → trained DistilBERT quality scorer → escalate below threshold. Matched GPT-4 with **50–98% cost reduction**, or +4–5% accuracy at equal cost (different models err on different queries). Three levers: prompt adaptation, LLM approximation (cache/distill), cascade. https://arxiv.org/abs/2305.05176
- **AutoMix** — cascade with few-shot self-verification (entailment check vs context) + POMDP router because self-verification is noisy. Up to **89% better incremental-benefit-per-cost**; beats FrugalGPT. https://arxiv.org/abs/2310.12963
- **Confidence-based escalation caveats**: raw self-reported confidence poorly calibrated; use trained quality estimators, confidence tokens (Self-REF), or self-consistency agreement. "Cascaded Selective Evaluation" (ICLR 2025): Mistral-7B first-tier judge, escalate on insufficient confidence — preserves human-agreement guarantees cheaply.
- **Cascade vs pre-route**: cascades pay latency + double-generation on escalated queries, no upfront classifier; pre-routing single-shot but needs training data. **Hybrid "route, then verify, then escalate" is the production pattern.** https://tianpan.co/blog/2025-11-03-llm-routing-model-cascades
- **Commercial**: Not Diamond (+25% accuracy over any single model claim, powers OpenRouter auto-router with `cost_quality_tradeoff` knob); Martian (interpretability-based; vendor-reported 92% cost cut cases). https://www.notdiamond.ai/ ; https://openrouter.ai/docs/guides/routing/routers/auto-router ; https://github.com/Not-Diamond/awesome-ai-model-routing

## 2. Test-Time Compute: Lifting Small Models

- **DeepMind compute-optimal scaling (Snell, arXiv 2408.03314)** — adaptive per-prompt allocation (PRM-guided search vs iterative revision, by difficulty) is **>4× more efficient** than best-of-N. FLOPs-matched: small model + test-time compute **outperforms 14× larger model** — but **only where the small model has non-trivial base success rate**. Core routing insight: test-time compute substitutes for parameters only within the competence envelope.
- **HuggingFace replication**: **Llama-3.2 1B + beam search (N=32, 8B PRM) matches Llama-3.1 8B** on MATH-500; **3B + compute-optimal search beats 70B (22×)**. Beam N=4 ≈ best-of-N N=16; DVTS wins at large N. Hidden dependency: a good PRM. https://huggingfaceh4-blogpost-scaling-test-time-compute.hf.space/ ; https://github.com/huggingface/search-and-learn
- **"Can 1B Surpass 405B?"** (arXiv 2502.06703) — yes on MATH-500/AIME with right PRM+strategy.
- **s1 (arXiv 2501.19393)** — SFT on 1,000 curated traces + "budget forcing" (append "Wait" to extend thinking): s1-32B exceeds o1-preview up to 27% on MATH/AIME24.
- **Classic parallel sampling**: self-consistency +17.9pp GSM8K (cheapest reliable lift when answers are checkable/parseable); best-of-N with generative verifier (GenRM-CoT): Gemini 1.0 Pro GSM8K 73% → 93.4% with 9B verifier; ToT: GPT-4 Game-of-24 4% → 74%; Self-Refine ~20% avg (weak models give weak feedback — pair small generator with strong/programmatic critic); **execution feedback is the strongest verifier**: CodeT (generate solutions + tests, dual execution agreement) >10pp pass@1 gains. https://arxiv.org/abs/2203.11171 ; https://arxiv.org/html/2408.15240 ; https://arxiv.org/abs/2305.10601 ; https://arxiv.org/abs/2303.17651
- **Ladder**: (1) single sample → (2) self-consistency k=5–10 → (3) best-of-N + verifier (execution tests ≫ PRM ≫ LLM judge) → (4) beam/DVTS search for hard multi-step → (5) escalate model tier when base success ~0.

## 3. Harness / Scaffolding Effects

- **Same LLM: 42% → 78%** on coding benchmark changing only scaffolding (36pp swing); SWE-bench Pro: scaffold accounts for **22+ point swing while frontier model swaps ≈ 1 point**; three agent systems on same Claude Opus 4.5 spanned 50.2%–55.4%. Harness = prompt construction + tool set + output parsing + context/memory mgmt + retry policy. **Biggest single lever is the harness, not the model.** https://particula.tech/blog/agent-scaffolding-beats-model-upgrades-swe-bench ; https://www.digitalapplied.com/blog/swe-bench-verified-june-2026-benchmark-vs-scaffolding-analysis
- **Task decomposition**: each step down a model tier saves 70–90% (Amazon Science, no benchmarks given; warns re coordination overhead). Decomposition pushes subtasks into the small model's competence envelope (the Snell precondition), enables per-subtask verification. TDAG for dynamic decomposition. https://www.amazon.science/blog/how-task-decomposition-and-smaller-llms-can-make-ai-more-affordable
- **Tool offloading (PAL)**: LLM writes Python, interpreter executes reasoning. Smaller model + PAL beats larger + CoT (up to 15% absolute); PAL + majority@40 GSM8K 72.0 → 80.4. **Never make a small model do arithmetic, date math, unit conversion, string manipulation, counting — route to code execution.** https://arxiv.org/abs/2211.10435
- **Constrained decoding**: guarantees syntactic validity; "enables relatively small models to perform comparably to much larger" on structured tasks. **Constraint tax for sub-3B**: schema compliance consumes capacity; token masking distorts distribution — validity ≠ semantic correctness. Prefer flat schemas; two-pass (free-form reason → constrained extract). Frameworks: Outlines, Guidance, XGrammar. https://arxiv.org/html/2501.10868v1 ; https://arxiv.org/pdf/2605.26128

## 4. Prompt Optimization for Weak Models

- **DSPy / MIPROv2** — compiles pipeline prompts (instructions + few-shot demos, Bayesian-optimized) against a metric; standard pattern: **strong teacher optimizing prompts for a small student** = automated distillation of a frontier playbook into small-model prompts. https://dspy.ai/api/optimizers/MIPROv2/
- **GEPA (ICLR 2026 oral)** — reflective prompt evolution using NL reflection on failure traces; Pareto frontier of candidates. **Beats GRPO (RL fine-tuning) by +6% avg / up to +20% with 35× fewer rollouts on Qwen3-8B; >10% over MIPROv2.** For small models, optimized prompting can outperform weight training at a fraction of cost. `dspy.GEPA`; https://github.com/gepa-ai/gepa ; https://arxiv.org/abs/2507.19457
- **Harness recipe**: collect traces where small model fails and frontier succeeds → GEPA/MIPROv2-compile the small model's prompt against those, frontier model as reflection LM.

## 5. Knowledge / Context Enrichment

- **RAG disproportionately helps small models**: Atlas (11B retrieval-augmented) beat a 540B closed-book model on NaturalQuestions — retrieval substitutes for parametric knowledge.
- **Dynamic few-shot exemplar retrieval**: semantically-similar demonstrations significantly beat fixed/random sets (survey: https://arxiv.org/pdf/2401.11624). Harness: maintain exemplar store of solved tasks, retrieve top-k similar per request.
- **Context engineering** (survey arXiv 2507.13334): small models are more sensitive to irrelevant/distracting context — aggressive filtering/reranking matters more at the small end.

## 6. Practical Tiering Guidance

- **Safe for haiku-class / 8B-class** (single-pass, verifiable, narrow): classification, intent detection, routing decisions themselves, sentiment, NER, structured extraction, reformatting, template generation, short-context QA over provided text, basic summarization, translation. Small tier ≈ 3–5× lower latency, 10–20× lower per-token cost; ~80% of enterprise workloads don't need frontier; cutting default tier = 10–50× cost reduction.
- **Needs frontier**: long-horizon multi-step agentic work, ambiguous-evidence reasoning, tool coordination across many steps, long-context synthesis, novel debugging — **errors compound: 95%-per-step over 20 steps ≈ 36% success**.
- **How production decides**: (1) pre-route with cheap complexity classifier; (2) cascade (small → verify → escalate) when quality signal is checkable; (3) escalation triggers: failed tests/schema validation, low self-consistency agreement, trained quality estimator — NOT raw verbalized confidence; (4) route on query features + observed competence, not model brand.

## Synthesis for the Meta-Harness

- Invest in the harness before model upgrades (22–36pp swings at fixed model).
- Small-model enrichment stack, ROI order: (1) tool offloading + execution verification, (2) GEPA/DSPy-compiled prompts with frontier teacher, (3) dynamic exemplar retrieval + RAG, (4) self-consistency/best-of-N with verifier, (5) constrained decoding (flat schemas), (6) beam/DVTS only for hard reasoning with a PRM.
- Routing: hybrid pre-route + cascade; escalate on verifiable failure signals; expect ~75–85% cost cut at ~95% frontier quality; test-time compute only rescues tasks with non-trivial base success — decomposition pushes subtasks into that envelope.
