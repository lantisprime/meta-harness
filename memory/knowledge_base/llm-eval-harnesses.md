---
url: multiple (inspect eval framework docs, promptfoo.dev, anthropic.com/research, hamel.dev — see inline)
fetched: 2026-07-08
summary: Eval harnesses for model-swap go/no-go — framework landscape (promptfoo/DeepEval/Inspect/Braintrust/LangSmith/Phoenix), trajectory & pass^k evals, LLM-as-judge best practices, model-swap regression methodology (capability matrix, paired stats, shadow/canary), building datasets from production traces.
---

# Evaluation Harnesses for LLM Apps & Agents — Research Notes

Use case: standing eval suite for model-swap go/no-go decisions per task type. All sources fetched 2026-07-08.

## 1. Eval Frameworks — Landscape

**Pattern (2025-2026):** teams run *two layers* — lightweight OSS framework for CI gating (promptfoo / DeepEval / Inspect) plus a platform for traces, annotation, regression dashboards (Braintrust / LangSmith / Phoenix).

- **promptfoo** — OSS core. Weak on trajectories (prompt/output-level). Best-in-class CI: CLI + declarative YAML, GitHub Actions, `--fail-on-error`, JUnit XML, caching. Native multi-model matrix — closest fit to "swap model, rerun suite".
- **DeepEval** — OSS (SaaS = Confident AI). Strong agent metrics: Task Completion (LLM-judge over full trace via `@observe`), Tool Correctness, Argument Correctness. "Pytest for LLMs"; 50+ metrics incl. G-Eval. Goldens decoupled from runs, designed for re-running across model versions.
- **Braintrust** — SaaS only. End-to-end agent tracing; automated release enforcement; experiment diffing per PR; eval → production monitoring lifecycle.
- **LangSmith** — SaaS (self-host enterprise). `agentevals`/`openevals` OSS packages: trajectory match (exact/unordered/subset/superset) or LLM-judge over trajectory; pairwise experiments; pytest/Vitest integration.
- **OpenAI Evals** — platform being **deprecated** (read-only Oct 2026, shutdown Nov 2026). Avoid as foundation.
- **Inspect (UK AI Safety Institute eval framework)** — fully OSS Python. Strongest for isolated-environment agent tasks: built-in ReAct/multi-agent/agent bridge, tools (bash, python, web_browser, computer). One model interface over OpenAI/Anthropic/Google/Bedrock/vLLM/Ollama — swap model via CLI flag. Isolation backends: Docker, K8s, Modal; `inspect_evals` ships GAIA and SWE-Bench.
- **W&B Weave** — SaaS-centric; scorers + comparison dashboards; evals not central to release workflow.
- **Phoenix (Arize)** — OSS, self-hostable, OTel/OpenInference-native; portable trace data is the differentiator.

**Fit:** promptfoo or DeepEval for the CI gate; Inspect AI for sandboxed agent tasks; Phoenix for OSS trace storage feeding the dataset pipeline.

Sources: https://www.promptfoo.dev/docs/integrations/ci-cd/ ; inspect eval framework docs (inspect.aisi.org.uk) ; github.com/UKGovernmentBEIS/inspect_ai ; https://deepeval.com/docs/metrics-task-completion ; https://github.com/langchain-ai/agentevals ; https://docs.langchain.com/langsmith/trajectory-evals ; https://arize.com/llm-evaluation-platforms-top-frameworks/ ; https://developers.openai.com/blog/openai-for-developers-2025

## 2. Agent-Specific Evals

- **Trajectory evaluation**: (a) trajectory match vs reference (exact/unordered/subset/superset on tool-call sequences); (b) LLM-judge over trajectory when multiple valid paths. Score separately: tool *selection*, *argument* correctness, dependency/order (TRAJECT-Bench). Rubric standard: judge scores each (state, action) pair 1–5; trajectory score = geometric mean.
- **Lucky passes**: ~10.7% of passing SWE-agent trajectories reach correct patches through weak/wrong processes (AgentLens, https://arxiv.org/pdf/2605.12925) — score outcome AND process.
- **End-to-end success**: τ-bench pattern — judge by **comparing final environment/DB state to annotated goal state**, not grading text. Agents confidently claim completion while silently failing (https://arxiv.org/pdf/2606.09863) — verify state, never trust self-report.
- **Sandboxed env evals**: Terminal-Bench (Docker per task, verification suite, oracle; Harbor harness); SWE-bench (patch authorship) vs Terminal-Bench (interactive shell) — complementary. τ-bench/τ²-bench: simulated LLM user + agent + domain policy.
- **pass@k vs pass^k — critical for gating**: pass@k = at least one of k succeeds (potential); **pass^k = ALL k succeed (reliability; τ-bench metric; ≈ p^k)**. GPT-4o-class agents <50% pass@1, pass^8 <25% on τ-bench retail — single-run evals wildly overstate deployability. **Run each task k≥4–8 times; gate on pass^k per task type.**

Sources: https://arxiv.org/abs/2406.12045 (τ-bench) ; https://arxiv.org/html/2510.04550v1 (TRAJECT-Bench) ; https://benchmarkingagents.com/terminal-bench/ ; https://github.com/langchain-ai/agentevals

## 3. LLM-as-Judge Best Practices

- **Rubric design**: write your own eval prompts, never generic ones. **Binary pass/fail beats Likert** (more consistent, clearer criteria, smaller samples); max 3 categories. Constrain judge to specific factual criteria; require structured reasoning + verdict; split multi-criteria rubrics into separate single-criterion judges.
- **Pairwise vs pointwise**: pairwise >80% human agreement (matches human-human) — best for model selection/A-B, i.e. model-swap decisions. Pointwise better for production monitoring and CI gates. For model-swap: pairwise old-vs-new on identical inputs as tie-breaker atop pointwise rubric gates.
- **Judge model choice**: start with most capable model to establish human alignment; downgrade only after measuring alignment holds. Judge model choice is the biggest driver of positional bias (IJCNLP 2025).
- **Biases & mitigations**: position bias (>10% shift — run both orders, count only consistent verdicts); verbosity bias (rubric criteria that ignore length); **self-preference bias (NeurIPS 2024) — for model-swap evals, judge with neither incumbent nor candidate; use a third model or ensemble**.
- **Calibration**: error analysis on real traces → human-label 100+ examples (single domain-expert "benevolent dictator") → iterate judge prompt; measure **TPR/TNR, not accuracy**, on held-out split; recalibrate on a cadence and after judge-model upgrades.

Sources: https://www.evidentlyai.com/llm-guide/llm-as-a-judge ; https://hamel.dev/blog/posts/evals-faq/ ; https://arxiv.org/pdf/2410.21819 ; https://mbrenndoerfer.com/writing/position-bias-in-llm-judges

## 4. Model-Swap Regression Methodology

- **Golden datasets**: reviewed, versioned, decoupled from any single run; re-run across model versions producing fresh outputs. ROI datapoint: 600-case golden set (~80 hrs) enabled frontier→fine-tuned-small swap with measured zero regression and 73% cost reduction. CI shape: any PR touching prompt/model/retrieval triggers eval vs goldens; regression past threshold blocks merge; track per-evaluator deltas vs last passing baseline.
- **Capability matrix**: **per-task-type delta is the release decision, not aggregate** — new model can win on average while regressing badly on one task type. Rows = task types, columns = metrics (quality, pass^k, cost, latency), one go/no-go cell per row.
- **Statistics (Anthropic "Adding Error Bars to Evals", arXiv 2411.00640)**: always report SEM; **paired-differences test** (same questions across models → difficulty variance cancels; models correlate 0.3–0.7); cluster standard errors on the randomization unit (naive SEs can be 3× too small); resample k answers per question (gives pass^k for free); power analysis first. **Below a few hundred datapoints, CLT error bars dramatically underestimate uncertainty (ICML 2025)** — use bootstrap/Bayesian: https://github.com/sambowyer/bayes_evals. Per-task-type cells of 20–50 examples are in this regime — Bayesian intervals per cell.
- **Canary/shadow for agents**: four-stage gate **shadow → canary → percentage → full**. Shadow = mirror traffic, 0% user-visible, auto-compare via judge. Agent complication: **shadow mode with side-effecting tools requires isolated / dry-run tool execution** — plan a "shadow tool" path that doesn't touch real state.
- **Tri-objective gating**: quality floor per task type (no regression beyond CI-noise band) + cost/latency ceilings (measure tokens, don't estimate). Payoff: mixed per-task routing decisions, not one global verdict.

Sources: https://www.anthropic.com/research/statistical-approach-to-model-evals ; https://arxiv.org/pdf/2411.00640 ; https://arxiv.org/pdf/2503.01747 ; https://futureagi.com/blog/llm-eval-shadow-traffic-canary-2026/ ; https://qaskills.sh/blog/golden-dataset-llm-evaluation-guide

## 5. Datasets from Production Traces & the Development Loop

- **Sampling**: depth beats breadth — 100 diverse traces analyzed carefully > thousands superficial. Start 20–50 random; then targeted (outliers, user-feedback signals, metric-sorted worst, stratified by task type, embedding clustering).
- **Labeling (Hamel)**: **error analysis is the highest-value eval activity**. Gather traces → open coding → axial coding into failure taxonomy → iterate to saturation (~20 fresh traces yield no new modes; review ≥100 to start). Binary labels; single domain-expert. Build automated evaluators **only for observed, persistent failure modes**; cost hierarchy: assertions/regex → LLM-judge (100+ labels, weekly maintenance).
- **Freshness**: re-run error analysis (100+ traces) after new features, prompt changes, **model switches**, incidents; otherwise every 2–4 weeks. Production monitoring findings become CI regression cases. CI set = small, curated: core features + regression tests for past bugs + edge cases.
- **EDD synthesis**: seed suite with known constraints + task-type success criteria (state-verification where possible), then grow exclusively from production error analysis. The standing suite is an *output* of the trace-mining loop, not a static artifact. Reference architecture: https://arxiv.org/html/2411.13768v3.

Sources: https://hamel.dev/blog/posts/evals-faq/ ; https://eugeneyan.com/writing/eval-process/ ; https://developers.openai.com/cookbook/examples/partners/eval_driven_system_design/receipt_inspection ; https://newsletter.pragmaticengineer.com/p/evals

## Design Takeaways for the Meta-Harness Eval Subsystem

1. **Gate on pass^k per task type**, k≥4–8; single-run pass rates overstate reliability 2×+.
2. **Verify final state, not text**; score trajectory separately from outcome (lucky passes, false success).
3. **Per-task-type capability matrix** with paired-difference stats; Bayesian intervals below ~200 examples/cell.
4. **Third-party judge** (neither incumbent nor candidate), binary rubrics, both-order pairwise, TPR/TNR-calibrated against 100+ human labels.
5. **Tri-objective gate**: quality floor + cost/latency ceilings → per-task-type routing decisions.
6. **Offline suite + shadow stage** with sandboxed tool execution before canary.
7. Grow golden set from production error analysis (2–4 week cadence); every incident becomes a CI regression case.
8. Base frameworks: promptfoo (CI + multi-model matrix), DeepEval (agent metrics), Inspect AI (sandboxed tasks, model-agnostic). Avoid OpenAI Evals platform.
