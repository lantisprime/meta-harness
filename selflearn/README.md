# selflearn

Standalone self-learning knowledge system for LLM agents: acquire knowledge
from sources (web, PDFs, arXiv, YouTube lectures), verify it externally,
gate it with generated evals, retrieve it into prompts, and learn from
verified task outcomes.

**Host-agnostic by construction**: zero imports from any harness. Hosts
integrate through five small Protocols (`ModelPort`, `EmbeddingPort`,
`ExecutionPort`, `ProvenancePort`, `IdentityPort` — see
`src/selflearn/ports.py`); every artifact is a plain file (Markdown entries
with YAML frontmatter, JSON manifests, JSONL provenance).

Design document: `../docs/self-learning-specialist-agents-plan.md` in the
meta-harness repository, including the executable plan simulation
(`../development/selflearn_simulation.py`).

## Status

| Milestone | State |
|---|---|
| M1 — package skeleton, contracts, five ports, store module, seed importers | shipped |
| M2 — retrieval (semantic scorer, injection block), specialist spec, fast-loop marks, harness adapter | shipped |
| M3 — acquisition plugin registry (local/web/arxiv/pdf/youtube), distillation with SchemaGuard + injection screen, CLI | shipped |
| M4 — verification (corroboration/citations/skill-exec/judge), strict-mode pipeline + approve, acquisition template + knowledge tools, knowledge-driven planning | shipped |
| M5 — evalgen, second-model probe validation, eval-gated auto-publish (bootstrap rule), suite runner, model qualification | shipped |
| M6 — learning module | pending |

## Quick start (M1 scope)

```bash
pip install -e './selflearn[dev]'
python -m pytest selflearn/tests -q
```

```python
from pathlib import Path
from selflearn import PackStore
from selflearn.store import seed_knowledge_base, seed_ytdistill

store = PackStore(Path("~/.selflearn/knowledge").expanduser())
seed_knowledge_base(store, Path("memory/knowledge_base"), pack="meta-research",
                    publish=True)
seed_ytdistill(store, Path("distilled/ai-agent-memory-masterclass"),
               pack="agent-memory", publish=True)
```

Seeded entries are candidates by default; `publish=True` records an explicit
pre-gate seed basis in provenance (bulk seeding is a human-initiated
acquisition mode). Once the verification module lands, seeded packs should
be re-verified through the normal gate.
