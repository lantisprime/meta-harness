# selflearn

Standalone self-learning knowledge system for LLM agents: **acquire**
knowledge from sources (web search, pages, PDFs, arXiv, YouTube lectures),
**verify** it externally, **gate** it with generated evals, **retrieve** it
into prompts, and **learn** from verified task outcomes.

**Host-agnostic by construction**: zero imports from any harness, any
OpenAI-compatible model endpoint works, and every artifact is a plain file
(Markdown entries with YAML frontmatter, JSON manifests, JSONL provenance).
Hosts integrate through five small Protocols (`ModelPort`, `EmbeddingPort`,
`ExecutionPort`, `ProvenancePort`, `IdentityPort`).

📖 **[Full user manual](../docs/selflearn-manual.md)** — concepts, CLI
reference, plugin/backend guide, gates, learning loop, harness integration,
troubleshooting.

Design document: [`../docs/self-learning-specialist-agents-plan.md`](../docs/self-learning-specialist-agents-plan.md)
(11 recorded decisions + executable plan simulation in
`../development/selflearn_simulation.py`).

## Status — all six milestones shipped (2026-07-17)

| Milestone | State |
|---|---|
| M1 — package skeleton, contracts, five ports, store module, seed importers | shipped |
| M2 — retrieval (semantic scorer, injection block), specialist spec, fast-loop marks, harness adapter | shipped |
| M3 — acquisition plugin registry (local/web/arxiv/pdf/youtube), distillation with SchemaGuard + injection screen, CLI | shipped |
| M4 — verification (corroboration/citations/skill-exec/judge), strict-mode pipeline + approve, acquisition template + knowledge tools, knowledge-driven planning | shipped |
| M5 — evalgen, second-model probe validation, eval-gated auto-publish (bootstrap rule), suite runner, model qualification | shipped |
| M6 — gap detection over the coverage map, topic labeling, staleness, backoff, advisory suggestions, suite regression | shipped |

Remaining plan milestone M7 (meta-harness Web UI surfaces) lives host-side.

## 60-second tour

```bash
pip install -e './selflearn[dev,pdf]'
python -m pytest selflearn/tests -q

# New here? The wizard walks every workflow interactively, showing the
# equivalent plain command before running it:
selflearn wizard

# Bulk-seed existing material (no model needed):
selflearn seed-yt distilled/some-lecture --pack lectures --store ~/.selflearn --publish
selflearn list --store ~/.selflearn

# Full research pipeline (needs an OpenAI-compatible chat endpoint):
selflearn acquire "search:how do fastapi lifespan handlers work" \
    --pack fastapi --topic lifespan --store ~/.selflearn --workdir /tmp/sl \
    --endpoint http://127.0.0.1:1234/v1 --model qwen3-coder \
    --embedding-endpoint http://127.0.0.1:1234/v1 --embedding-model nomic-embed-text

# Strict mode holds verified entries; a human publishes:
selflearn verify --pack fastapi --store ~/.selflearn
selflearn approve <entry-id> --store ~/.selflearn --approved-by you@example.com

# Test what a specialist would be handed:
selflearn retrieve "lifespan startup shutdown" --packs fastapi --store ~/.selflearn

# Not sure what to do next? A prioritized, executable to-do list:
selflearn next --store ~/.selflearn

# Store won't load / looks inconsistent? Diagnose, then repair:
selflearn doctor --store ~/.selflearn
selflearn doctor --store ~/.selflearn --fix

# See the store as a map (packs, topics, entries, source domains, task
# types) — read-only projection; also feeds the harness UI's Knowledge tab:
selflearn graph --store ~/.selflearn --format mermaid
```

Auto-publish (eval-gated, no human in the loop) activates when a distinct
validator model is configured — see the manual's *Gates* chapter. Search is
keyless by default (DuckDuckGo); Wikipedia, self-hosted SearXNG, and Brave
are supported backends. An end-to-end offline demo lives in
`examples/offline_course_demo.py`.
