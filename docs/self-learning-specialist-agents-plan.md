# Plan: Self-Learning Specialist Agents

Status: **draft for discussion** — not yet scheduled into the workplan.

## The idea

Specialist agents that get better at a domain over time by researching reputable
web sources, distilling what they learn into versioned Markdown/JSON knowledge
and skill files, and having those files retrieved into their prompts on future
tasks. A "security-review specialist" or "FastAPI specialist" is then an agent
config **plus** a growing, curated knowledge pack — not just a system prompt.

This formalizes something the project already does by hand:
`memory/knowledge_base/` is a human-curated cache of distilled web research with
per-file citations, and the ACE playbook (`correction/playbook.py`) already
persists verified lessons and injects the top-k into task prompts. The plan
turns that manual practice into a first-class, self-driving subsystem while
keeping every existing design principle intact.

## Design principles applied (non-negotiable)

- **Never trust self-assessment.** An agent's own summary of a web page is not
  knowledge. Every candidate entry must pass an external verification signal
  before it can influence future prompts: source-corroboration for facts,
  execution for skills, rubric judge as fallback, human gate for promotion.
  Unverified entries stay quarantined as candidates and are never retrieved.
- **Deterministic spine.** Acquisition runs as a journaled workflow template
  (gather → distill → verify → curate ⛔ → publish). The lifecycle — retries,
  quarantine, promotion, rollback — is deterministic code. LLM intelligence
  lives inside the distill/verify steps only.
- **Failures are loud.** A fetch that 404s, a PDF that won't parse, or an entry
  that fails schema validation fails the step visibly — never a silently thin
  knowledge pack.
- **Provenance everywhere.** Every entry records source URL, fetch timestamp,
  content hash, and the run that produced it; publish events land in the
  existing hash-chained provenance log.

## Core concepts

### Knowledge entry (declarative: "what is true")

Canonical form is Markdown with YAML frontmatter — human-readable and
git-diffable, matching the `memory/knowledge_base/` convention — plus a
machine-facing JSON manifest per pack for retrieval.

```markdown
---
id: kn-fastapi-lifespan-2026-07
pack: fastapi
kind: knowledge
status: published          # candidate | published | deprecated
sources:
  - url: https://fastapi.tiangolo.com/advanced/events/
    fetched_at: 2026-07-17T00:00:00Z
    sha256: 9f2c…
    tier: official          # official | primary | community
corroboration: 2            # independent sources agreeing
task_types: [code_edit, review]
helpful: 3                  # retrieval feedback, ACE-style
harmful: 0
---
FastAPI deprecated `on_event` startup/shutdown handlers in favor of the
`lifespan` context manager; mixing both silently drops … (≤ ~400 tokens)
```

### Skill entry (procedural: "how to do X")

Same envelope, `kind: skill`, body is an imperative recipe (steps, commands,
pitfalls). Skills whose domain permits it carry a `check:` block — a
deterministic command or assertion that the existing sandboxed execution
verifier (`evals/execution.py` machinery) can run to prove the skill works
before promotion. Skills that can't be executed are verified like facts
(corroboration/judge) and marked `check: none` so their lower evidence class
is visible.

### Knowledge pack

A directory per specialty under `~/.metaharness/knowledge/<pack>/`:
entries (`*.md`), `manifest.json` (ids, embeddings/keywords, status, scores),
and `provenance.jsonl`. Write-through and loaded at boot like the other
stores. Packs are the unit a specialist binds to; one pack can serve several
agents.

### Source reputability policy

A configurable, deterministic policy — not model judgment:

- **Tiered domain lists** in config: `official` (vendor docs, standards
  bodies, peer-reviewed/arXiv), `primary` (maintainer blogs, project repos),
  `community` (Stack Overflow, high-signal blogs). Unknown domains are
  fetchable during *gather* but their claims cannot be sole support for a
  published entry.
- **Corroboration rule**: facts need ≥2 independent sources, or 1 `official`
  source. Configurable per pack.
- **Citations mandatory**: an entry with a claim not traceable to a listed
  source fails curation deterministically.

### Specialist agent

An `AgentConfig` extension: `knowledge_packs: [fastapi, security]` plus an
optional specialty system-prompt archetype. At task time, the context builder
retrieves top-k published entries (keyword scoring like
`Playbook.bullets_for`, embeddings later) under a dedicated per-tier context
budget slice, injected fenced and untrusted-marked — advisory context, same
treatment as the ✦ companion's inputs. The router's capability matrix keeps
working unchanged; specialists simply earn better verified outcomes in their
domain, so pre-routing naturally starts favoring them.

## The learning loop (two-speed, mirroring `correction/`)

1. **Fast (per-task)**: after a verified task outcome, retrieved entries get
   `helpful`/`harmful` marks — exactly the ACE delta-update scheme the
   playbook uses. Persistently harmful entries auto-deprecate; deprecation is
   journaled and reversible.
2. **Slow (offline)**: a *knowledge-acquisition* workflow run, triggered
   manually or by a gap signal (repeated verified failures clustered by MAST
   in a domain a pack claims to cover). It proposes new/amended entries and
   pushes them through verify → curate ⛔ → publish.
3. **Gap detection**: when the failure store shows a cluster whose plays
   don't help, the harness suggests (never auto-runs) an acquisition run for
   that topic on the console — same advisory-only posture as the tuning card.

## Ingestion pipeline

New `src/metaharness/knowledge/` subsystem:

| Module | Responsibility |
|---|---|
| `ingest.py` | Fetchers: web page (upgrade `_web_fetch`: HTML→text extraction, size caps, robots/rate-limit courtesy), PDF (`pypdf` extra), local `.md`/`.json` drop-in directory |
| `distill.py` | LLM step: source text → candidate entries against the schema; SchemaGuard-enforced |
| `verify.py` | Corroboration checker (deterministic), skill `check:` execution via the sandboxed runner, rubric-judge fallback |
| `store.py` | Pack CRUD, manifest, quarantine/promotion state machine, provenance |
| `retrieve.py` | Scoring + budgeted selection for context injection |

Plus a `knowledge_acquisition` workflow template in
`workflows/templates.py`: `scope → gather → distill → verify → curate ⛔
(hitl_timing: after) → publish`. Curation shows the human the diff of the
pack, entry-by-entry, with sources — the same post-artifact gate UX shipped
for Software Engineering in PR #21.

A `web_search` tool is a prerequisite for real research (current `web_fetch`
needs an exact URL). The MCP Brave Search preset already exists in Settings —
the template can require it, keeping search API keys out of core.

## Milestones

Each milestone is independently shippable and tested; later ones can be
re-scoped after we see the earlier ones work.

1. **M1 — Knowledge store + schemas** (`knowledge/store.py`, entry/manifest
   models, quarantine state machine, provenance, boot loading). Import
   `memory/knowledge_base/*.md` as the seed pack to prove the format.
2. **M2 — Retrieval + specialist binding** (`retrieve.py`, `AgentConfig.
   knowledge_packs`, context-budget slice, fenced injection, helpful/harmful
   feedback wiring). Value ships here even with hand-authored packs.
3. **M3 — Ingestion** (`ingest.py` web/PDF/md fetchers, `distill.py`,
   SchemaGuard on output, loud failure paths).
4. **M4 — Verification + acquisition template** (`verify.py`, reputability
   policy config, `knowledge_acquisition` template with the curate gate,
   MCP web-search integration).
5. **M5 — Learning-loop closure** (gap detection from MAST clusters, console
   card with advisory suggestions, auto-deprecation, eval: paired go/no-go —
   does pack injection beat no-injection on a held-out suite before a pack
   version is promoted to default-on?).
6. **M6 — Web UI** (pack browser, entry diff view at the curate gate,
   specialist wizard step in Settings).

## Risks / open questions (for discussion)

1. **Retrieval quality**: start with keyword scoring (proven in
   `Playbook.bullets_for`) or bring embeddings in from day one? Keyword-first
   keeps M2 dependency-free; embeddings likely needed once packs exceed ~100
   entries.
2. **Search provider**: standardize on the MCP Brave preset, or add a
   built-in `web_search` tool with a pluggable backend?
3. **Prompt-injection surface**: web text flows through distillation into
   future prompts. Fencing + untrusted-marking + human curation gate is the
   proposed mitigation; is curate-gate-always-on acceptable friction, or do
   we want an auto-publish tier for `official`-only corroborated entries?
4. **Pack scope granularity**: per-technology (fastapi), per-role
   (security-reviewer), or both with pack composition?
5. **PDF fidelity**: text-layer extraction only (cheap, `pypdf`) vs OCR
   fallback (heavy). Proposal: text-layer only, loud failure otherwise.
