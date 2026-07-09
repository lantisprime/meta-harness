# metaharness

An intelligent **meta agent harness**: a control plane that orchestrates, steers, and learns from other agent harnesses. It plans goals into workflows, routes each step to the cheapest capable model tier, verifies every outcome against an external signal, and gets better over time — all while confirming the signed identity of every worker it delegates to.

## Design principles

- **Harness beats model.** Delegation contracts, verification, and context engineering move results more than a bigger model does.
- **Deterministic spine, intelligent steps.** The workflow lifecycle (retries, checkpoints, approval gates, budgets) is journaled deterministic code; LLM intelligence lives *inside* steps, never in control of the lifecycle.
- **Never trust self-assessment.** Every correction and termination decision anchors to an external signal: execution result > deterministic scorer > rubric judge > human. Unverified outcomes never feed the learning loop.
- **Authenticity before delegation.** No task is dispatched to a worker whose Ed25519 identity hasn't been confirmed against the registry; every action lands in a hash-chained provenance log.
- **Failures are loud.** Bad references and crashes fail runs visibly — never a silent "running".

The full design (with subsystem detail and a component diagram) is in [`docs/architecture.md`](docs/architecture.md).

## What's inside

| Subsystem | Where | What it does |
|---|---|---|
| Trust plane | `src/metaharness/identity/` | Ed25519 worker registry, scoped task tokens, hash-chained provenance log |
| Runner layer | `src/metaharness/harness/` | Uniform worker interface + enrichment stack (ToolOffload, SelfConsistency, SchemaGuard, SelfCritique) |
| Local & remote workers | `src/metaharness/harness/local.py` | OpenAI-compatible worker: local (LM Studio/Ollama) and remote (Anthropic/OpenAI/Groq/…) endpoints, per-agent system prompts, function-calling tool loop |
| Coding agents | `src/metaharness/harness/coding.py` | Pi / Codex / OpenCode / Claude Code driven headless per task in jailed workspaces — the harness can implement its own plans |
| Config store | `src/metaharness/config.py` | Providers, agents, MCP servers persisted to `~/.metaharness/config.json` (0600); keys obfuscated at rest, always masked over HTTP |
| Tools & MCP | `src/metaharness/tools/` | Workspace-jailed file tools, web fetch, calculator + MCP-server tools behind one registry; each step gets a small auto-detected subset (cap ~7) |
| Context management | `src/metaharness/context.py` | Per-tier prompt budgets, on-the-fly pruning (tool observations first, edges never), loud digests |
| Router | `src/metaharness/routing/` | Cheapest-capable pre-routing + verified-failure escalation, backed by a learned capability matrix |
| Workflow engine | `src/metaharness/workflows/` | Durable journaled runs: crash-resume, human-approval gates, YAML DSL, goal→WorkflowSpec planner with deterministically derived checks + per-step tool detection |
| Workflow types | `src/metaharness/workflows/templates.py` | Named deterministic phase templates; `software_engineering` = agentic SDLC (explore → specify ⛔ → plan ⛔ → implement → verify → review ⛔) |
| Learning | `src/metaharness/correction/` | Two-speed loop: per-task Reflexion + offline MAST failure clustering into an auto-curated playbook (delta updates, verified outcomes only) |
| Evals | `src/metaharness/evals/` | Golden sets, pass^k gating, paired go/no-go comparison; `sdlc.py` grades per-phase agentic-coding capability deterministically |
| Self-optimization | `src/metaharness/optimization/` | Meta-Harness outer loop (arXiv 2603.28052): proposer reads raw failure traces of prior candidates and searches harness configs; Pareto frontier (pass^k vs tokens); promotion only through the held-out eval gate |
| Observability | `src/metaharness/observability/` | OpenTelemetry spans across all layers; in-memory store feeds the WebUI live |
| Web UI | `src/metaharness/web/` | Home landing (single next-action card + metrics), Run wizard (Agents → Goal → Plan → Run → Done, with ✦ prompt assistant), wizard-driven Settings, live console with Harness-tuning card (start searches, approve promotions), ✦ AI advisor panels (closed action vocabulary, advisory-only), Help manual |

## Quick start

```bash
python -m venv .venv
.venv/bin/pip install -e '.[dev]'

# run the test suite
.venv/bin/python -m pytest -q

# end-to-end demo: every layer exercised in one run (mock workers, no model needed)
.venv/bin/python examples/demo.py            # add --serve to open the WebUI after
```

### Serving against real local models

Point the harness at an OpenAI-compatible server (LM Studio, Ollama) and map models to tiers:

```bash
.venv/bin/metaharness serve --local \
  --pick small=gemma-4-26b \
  --pick mid=qwen3-coder-30b \
  --pick frontier=qwen3.6-35b-a3b
```

Then open http://127.0.0.1:8321. Useful flags: `--critique` (enable SelfCritique enrichment), `--endpoint URL` (explicit server URLs), `--host/--port`.

Agents saved in the **Settings** view (wizard-driven: providers → keys → test → save; agent wizard with system-prompt archetypes) persist to `~/.metaharness/config.json` and are rebuilt at every serve — configured agents claim their tiers before `--local` discovery fills the rest. Coding CLIs found on `PATH` (pi, codex, opencode, claude) can be registered as agents that implement plans in real workspaces.

MCP tool servers: `pip install -e '.[mcp]'`, add servers in Settings (stdio command or HTTP URL), and their tools join the registry at startup — the planner hands each step only the small tool subset it needs.

### Optimizing the harness itself

The harness can search its own configuration — the Meta-Harness outer loop
([arXiv 2603.28052](https://arxiv.org/abs/2603.28052)) applied to the enrichment stack:

```bash
# offline demo: mock worker + deterministic rule proposer
.venv/bin/metaharness optimize --suite mixed --rounds 6

# real: smallest discovered local model is the target, largest is the agentic proposer
.venv/bin/metaharness optimize --local --proposer llm --suite math --max-tokens 500000
```

Each round a proposer reads the candidate ledger — params, scores, hypotheses, and the
**raw** failure traces of every prior candidate (the paper's ablation: raw traces beat
summaries by ~15 accuracy points) — and proposes a targeted config delta with a causal
hypothesis. Candidates are scored pass^k vs token cost on a search suite; a Pareto
frontier is kept, and promotion requires a strict win over the seed on a **held-out**
suite through the paired go/no-go gate. Suites are domain-general (`classify`,
`extract`, `math`, or `mixed`), and the ledger under
`~/.metaharness/optimization/<suite>/` survives restarts — a later run resumes the search.

The same loop drives the WebUI's **Harness tuning** console card: start a search from
the browser, watch candidates and plain-language findings appear live, and decide the
promotion yourself — web-started searches park gate-passing winners for **your
approval**, which then rewires the live small-tier runner immediately. Promoted params
also apply at every `serve` boot. The ✦ sparkle marks the AI companion throughout the
UI: advisory explanations and suggested next actions over fenced, untrusted-marked
context — it never executes anything itself. Beyond the Goal-step prompt assistant and
the tuning-candidate explanations, the sparkle also opens card-level reads on the
routing, failures, and playbook console cards — each pairing a verified-facts summary
with the companion's read and safe, suite-validated next actions.

## Persistence

Everything learned survives restarts, under `~/.metaharness/`:

- `playbook.json` — curated lessons retrieved into future task prompts
- `matrix.json` — capability matrix (which tier is known-safe per task type)
- `failures.json` — labeled failure store for the slow learning loop
- `journals/` — per-run event journals; interrupted runs are auto-advanced at boot

All stores are write-through and loaded at startup.

## Development notes

- `tests/test_wiring.py` is the integration sweep with text-answering workers — extend it whenever a new cross-component wire appears.
- Regression tests for loud-failure behavior live alongside the fixes that introduced them.
- Research grounding the design choices is cached in `memory/knowledge_base/` with per-file citations.
