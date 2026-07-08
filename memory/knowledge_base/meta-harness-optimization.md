# Meta-Harness: End-to-End Optimization of Model Harnesses

---
url: https://arxiv.org/abs/2603.28052 (html: https://arxiv.org/html/2603.28052v1; code: https://github.com/stanford-iris-lab/meta-harness)
fetched: 2026-07-08
summary: Stanford IRIS/MIT/KRAFTON outer loop that evolves harness code with an agentic proposer reading raw traces of all prior candidates from a filesystem; raw traces >> summaries (50.0 vs 34.9 median acc); Pareto frontier over accuracy vs context tokens; interface-validation gate; held-out test set.
---

Authors: Yoonho Lee, Roshen Nair, Qizheng Zhang, Kangwook Lee, Omar Khattab, Chelsea Finn (Stanford, MIT, KRAFTON).

## Method

- **Outer loop**: per iteration a coding-agent proposer (Claude Code + Opus in the paper)
  inspects a filesystem `D` holding every prior candidate's source, evaluation scores, and
  raw execution traces — via terminal tools (grep/cat), not prompt stuffing. Median 82 files
  read per iteration across ~20 candidates (41% harness source, 40% traces). ~20 iterations,
  2–3 proposals each.
- **Candidates**: single-file Python harnesses (~100–1000 LOC). Candidate dir = source +
  scores (accuracy, context tokens, pass rates) + traces (prompts, tool calls, outputs,
  state updates, failures) + proposer reasoning.
- **Proposal**: minimal domain skill describes filesystem layout + what files may be edited.
  Proposer does counterfactual diagnosis over traces, forms explicit causal hypotheses
  (e.g. after 6 regressions from prompt edits: "prompt/control-flow edits are high risk" →
  pivoted to purely-additive environment bootstrapping), then targeted edits or rewrites.
- **Scoring**: search set ⊂ train; test held out until final frontier evaluation.
  Multi-objective → **Pareto dominance over (accuracy, context tokens)**, no greedy incumbent.
- **Selection**: population maintained; proposer freely picks which parent to inspect
  (non-Markovian — routinely reads most of history, catches confounded edits across
  generations). All candidates passing **interface validation** get evaluated.
- **Workspace bootstrap snapshot** (TerminalBench-2 discovery): compound shell command
  gathers cwd, file listing, language/toolchain versions, package managers, memory →
  injected as an `[Environment Snapshot]` block; saves 3–5 exploration turns. 15s timeout.
- **Safety**: interface-validation gate; edit-scope instructions; decontamination string
  checks vs held-out benchmarks; code-space brittleness is inspectable (hard-coded
  mappings visible in diffs).

## Key ablation (text classification, identical budgets)

| Proposer interface | Median acc | Best acc |
|---|---|---|
| Scores only | 34.6 | 41.3 |
| Scores + LLM summaries | 34.9 | 38.7 |
| **Raw traces (full)** | **50.0** | **56.7** |

Summaries can HURT — they compress away diagnostic detail. Raw traces are the single most
important component. Meta-Harness spends ~10M tokens/iteration vs 0.002–0.026M for prior
text optimizers; long-horizon dependencies are informative.

## Results

- Online text classification: +7.7 pts over SOTA context-management system at **4× fewer
  context tokens**.
- Retrieval-augmented math: one discovered harness +4.7 pts avg on 200 IMO-level problems
  across five held-out models (harness transfers across models).

## Term disambiguation & related work (user-supplied research, 2026-07-08)

"Meta-harness" is overloaded. Clean definition: a layer above ordinary LLM/agent harnesses
that treats prompts, tools, memory, retrieval, execution policy, sandboxing, and runtimes
as controllable first-class components — composed, governed, swapped, or optimized.

| Paradigm | "meta" means | Example |
|---|---|---|
| Orchestration/governance | control plane above many agent harnesses: shared interface (messages/files in, streams/tool-calls out), contextual session-state policies (e.g. approval before git push after npm download), spend pauses, OS sandboxing (bubblewrap/Linux, Seatbelt/macOS via Omnibox) | **Omnigent** (Databricks, open-source **alpha** — not production-mature) |
| Automated harness optimization | outer loop searching/rewriting harness code around a fixed model | Stanford IRIS **Meta-Harness** (this paper) |

Related publications by the same group: "Post-Training Reliable Agent Systems via Harness
Search" (ICML 2026 AIWILD workshop, 2026-05-22); "Learning Agent-State Construction from
Long Histories" (RLC 2026 poster, 2026-06-10). Artifact repo
`stanford-iris-lab/meta-harness-tbench2-artifact`: optimized harness scoring **76.4% on
Terminal-Bench 2.0 with Claude Opus 4.6**. Related survey (not the paper): "Code as Agent
Harness" (UIUC/Meta/Stanford, 2026).

## Implications for metaharness (this repo)

- Candidate ledger on disk (one dir per candidate: params/config, scores, RAW eval traces,
  rationale, parent) — mirrors `D`; journals already give us trace discipline.
- Expose raw failure traces to the proposer; never `digest_text` them.
- Score = pass^k (existing gate math) + token cost; keep a Pareto frontier, not a champion.
- Reuse `evals/gate.py` paired go/no-go as the promotion gate on a held-out suite.
- Interface validation = pydantic bounds on the tunable surface (v1: config-space —
  enrichment stack, router threshold, context budgets, prompt directives; code-space later
  via the coding-agent workers).
