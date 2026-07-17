# selflearn — User Manual

A standalone self-learning knowledge system for LLM agents. It researches
reputable sources, distills what it finds into versioned knowledge files,
refuses to believe anything that isn't externally verified, generates evals
from what it learns, retrieves the knowledge into prompts, and gets sharper
from every verified task outcome.

This manual covers standalone use (CLI + Python API) and meta-harness
integration. Design rationale: `self-learning-specialist-agents-plan.md`.

---

## 1. Concepts in five minutes

**Knowledge pack** — a directory of knowledge for one technology or domain
(`fastapi`, `agent-memory`). Packs are plain files: Markdown entries,
`manifest.json` (retrieval metadata + coverage map), `evals/probes.jsonl`
(the pack's generated test suite), `provenance.jsonl` (every event, append
only). A specialist role *composes* packs — packs are per-technology, roles
are per-job.

**Entry** — one unit of knowledge. Three kinds:

| kind | carries | verified by |
|---|---|---|
| `knowledge` | facts ("what is true") | source corroboration + probes |
| `skill` | a procedure ("how to do X"), optionally an executable `check:` | sandboxed execution of the check |
| `workflow` | a machine-readable multi-step `procedure:` a planner can instantiate | per-step checks; golden-run at promotion |

**Entry lifecycle** — `candidate → published → deprecated` (reversible).
Only published entries are ever retrieved. Quarantined candidates (injection
screen hits) can never be published by any automatic gate.

**Probes** — every published entry ships eval items: a *recall* probe (fact
question, deterministic answer key from the sources), an *application*
probe (novel scenario), and for skills the executable check. Probes gate
publishing, qualify models, and catch regressions.

**Specialist** — a declarative YAML spec (name, packs, archetype prompt,
task types, routing constraints). Deliberately **no model field**: any model
that passes the pack suite can serve the role.

**The trust rule** — nothing the system produces about itself is trusted:
distilled entries need corroboration, probes need a *different* model to
validate them, publishing needs the gates, marks need external verdicts.

---

## 2. Installation

```bash
# from the meta-harness repo root
pip install -e './selflearn[dev,pdf]'     # pdf extra = pypdf text extraction
python -m pytest selflearn/tests -q       # 120 tests

# optional, for the youtube plugin:
pip install -e ../youtube-distiller        # provides the yt-distill CLI
```

Requires Python ≥ 3.10. Only hard dependency: PyYAML.

**Model endpoints.** Distillation, judging, probe authoring/validation, and
qualification each take any OpenAI-compatible endpoint (`/chat/completions`)
— LM Studio, Ollama, or remote providers. Semantic ranking takes any
OpenAI-compatible `/embeddings` endpoint. Nothing is vendor-specific.

---

## 3. Quick start

### Seed from material you already have (no model needed)

```bash
STORE=~/.selflearn/knowledge
selflearn seed-kb  memory/knowledge_base --pack meta-research --store $STORE --publish
selflearn seed-yt  distilled/harness-engineering-masterclass \
                   --pack masterclasses --store $STORE --publish
selflearn list --store $STORE
selflearn retrieve "which harness layer failed" --packs masterclasses --store $STORE
```

`--publish` marks seeds published with an explicit *pre-gate* basis in
provenance (bulk seeding is a human-initiated acquisition mode). Omit it to
land them as candidates for the normal gates.

### Research the web into a pack

```bash
selflearn acquire "search:how do fastapi lifespan handlers replace on_event" \
    --pack fastapi --topic lifespan --store $STORE --workdir /tmp/sl \
    --endpoint http://127.0.0.1:1234/v1 --model qwen3-coder \
    --embedding-endpoint http://127.0.0.1:1234/v1 --embedding-model nomic-embed-text
# -> gathered docs, distilled entries, verified, HELD (strict mode)
selflearn verify  --pack fastapi --store $STORE
selflearn approve kn-fastapi-lifespan-ab12cd34ef --store $STORE --approved-by you@x.com
```

### Let the eval gate publish automatically

Auto mode needs a **validator model distinct from the author model** (the
second-model check). Via the Python API:

```python
from selflearn import EvalGen, Verifier, run_acquisition
from selflearn.ports import ModelIdIdentity
evalgen = EvalGen(author_model, validator_model, ModelIdIdentity())
report = run_acquisition(refs, pack="fastapi", topic="lifespan",
                         registry=registry, ctx=ctx, distiller=distiller,
                         verifier=Verifier(), store=store,
                         evalgen=evalgen, answer_model=candidate_model)
print(report.summary())   # "... 4 auto-published (eval-gated) ..."
```

Entries that fail the gate fall back to the strict hold — never silently
dropped, never silently published.

---

## 4. CLI reference

Every command takes `--store PATH` (the knowledge root). Errors exit 2 with
a one-line reason on stderr; "no results" style outcomes exit 1.

### `selflearn gather REFS... --workdir W [--out sources.json]`
Acquire refs into provenance-stamped source documents (no model needed).

| flag | meaning |
|---|---|
| `--tier` | operator-asserted tier hint for local/unknown sources |
| `--no-network` | offline: only `file://` refs allowed |
| `--search-backend auto\|ddg\|wikipedia` | backend for `search:` refs (see §6) |
| `--brave-key` / `BRAVE_API_KEY` | optional Brave subscription |
| `--searxng URL` | self-hosted SearXNG instance |
| `--embedding-endpoint/-model` | semantic passage ranking for `search:` refs |

### `selflearn acquire REFS... --pack P --topic T --workdir W --endpoint E --model M`
Full pipeline: gather → distill → verify → hold (strict). Accepts all
`gather` flags plus `--judge-endpoint/--judge-model` (claim-support judge)
and `--api-key`.

### `selflearn distill SOURCES.json --pack P --topic T --endpoint E --model M`
Distill previously gathered documents into candidate entries.

### `selflearn verify --pack P`
Run the verification gates over a pack's candidates; prints
`[ELIGIBLE]`/`[REJECTED]` per entry with the first basis/reason.

### `selflearn approve ENTRY_ID [--approved-by WHO]`
Strict-mode publish: **re-verifies** first (refuses if the entry no longer
passes), then publishes with the approval recorded in provenance.

### `selflearn seed-kb DIR --pack P [--publish]` / `selflearn seed-yt DIR --pack P [--publish]`
Bulk-seed a research-cache directory of `.md` files / a yt-distill
`distilled/<lecture>/` folder.

### `selflearn list`
Packs with entry counts by lifecycle state, probe-suite size, coverage.

### `selflearn retrieve "QUERY" --packs P... [-k N]`
Show the exact injection block a specialist would receive. Without an
embedding endpoint this runs keyword-degraded and warns loudly.

---

## 5. Source refs and plugins

A ref is a string; the plugin registry resolves it deterministically (first
`can_handle` wins; an unclaimed ref fails the run loudly):

| ref looks like | plugin | notes |
|---|---|---|
| `file:///path/to/x.md` (or dir, `.json`, `.txt`) | `local` | dirs recurse; yt-distill `chunks.jsonl` parsed schema-tolerantly |
| `search:<natural language question>` | `web` | backend → fetch hits → extract → passages ranked against the question |
| `https://any.site/page` | `web` | plain page fetch + boilerplate-stripping extraction |
| `https://arxiv.org/abs/2603.28052` | `arxiv` | LaTeX source fast path: exact equations + figure captions, no vision needed |
| `https://x.site/paper.pdf`, `file://...pdf` | `pdf` | text layer via pypdf; scanned PDFs fail loudly (no OCR) |
| `https://www.youtube.com/watch?v=...` | `youtube` | drives the `yt-distill` CLI; or pass the distilled folder via `file://` |

All network goes through the acquisition context: rate-limited (default 1
fetch/second), size-capped (8 MiB), jailed workdir. Plugins have no network
or filesystem policy of their own.

**Third-party plugins** load only from an explicit allowlist of Python
entry points (group `selflearn.sources`). A plugin is code — installing one
is a trust decision; provenance records the producing plugin + version on
every document, so one plugin's output is revocable as a unit.

---

## 6. Search backends

| backend | cost | how to select | character |
|---|---|---|---|
| DuckDuckGo | free, no key | default | parses the public HTML results page; brittle by nature, low-volume by design; fails loudly on layout change |
| Wikipedia | free, official API | `--search-backend wikipedia` | encyclopedic topics only; the most stable free option |
| SearXNG | free, self-hosted | `--searxng https://your-instance` | full-web, no scraping fragility; one `docker run` to host |
| Brave | paid API | `--brave-key` or `BRAVE_API_KEY` | full-web, official API |

Selection order: explicit `--search-backend` > Brave key > SearXNG URL >
DuckDuckGo. In meta-harness, a connected MCP search server can also serve as
a backend through the adapter.

---

## 7. What a pack looks like on disk

```
<store>/<pack>/
  entries/kn-fastapi-lifespan-ab12cd34ef.md
  evals/probes.jsonl        # validated probes; retirement flags
  evals/baseline.json       # suite regression baseline (optional)
  manifest.json             # status, marks, vectors (keyed by embedder), coverage map
  provenance.jsonl          # every lifecycle event, append-only
```

Entry file (source of truth, git-diffable):

```markdown
---
id: kn-fastapi-lifespan-ab12cd34ef
pack: fastapi
kind: knowledge
status: published
topic: lifespan
claims:
- lifespan context manager replaces on_event
sources:
- url: https://fastapi.tiangolo.com/advanced/events/
  fetched_at: '2026-07-17T00:00:00Z'
  sha256: 9f2c…
  tier: official
  locator: ''            # page number / t=557-689s for video sources
helpful: 3.0
harmful: 0.0
---
FastAPI deprecated on_event startup/shutdown handlers in favor of the
lifespan context manager…
```

Everything is write-through; boot loading reconstructs full state from disk
and fails loudly on missing files, corrupt manifests, or status mismatches.

---

## 8. The gates: how knowledge earns publication

Order of checks for every candidate:

1. **Quarantine** — the deterministic injection screen (imperative patterns
   like "ignore previous instructions", "reveal your system prompt"; domain
   vocabulary such as discussing system prompts does NOT trip it) runs over
   source text and candidate text at distillation. A quarantined entry is
   unpublishable by any gate; only human review can clear it.
2. **Citations** — every source must carry a content hash.
3. **Corroboration** (configurable per pack via `CorroborationRule`):
   - one `official`-tier source suffices, or
   - ≥ 2 sources from *independent registrable domains*;
   - `unknown`-tier sources can never be sole support;
   - vision-extracted content needs ≥ 2 domains even with an official source.
   Tiers come from the deterministic reputability policy — domain lists plus
   channel identities for video sources — never model judgment.
4. **Skill checks** — a skill's `check:` runs in the host sandbox. No
   sandbox bound → **loud refusal**, never a silent skip.
5. **Judge** (optional) — a bound judge model verifies claims are supported
   by the source excerpts; its absence is visibly recorded in the basis.
6. **Eval gate** (auto mode only) — see §9.

**Strict mode** (default): entries passing 1–5 are *held*; a human runs
`selflearn approve`, which re-verifies then publishes with the approver in
the audit trail.

**Auto mode**: 1–5 plus the eval gate publish without a human. Failures of
the eval gate fall back to the strict hold.

---

## 9. Evals: probes, the gate, qualification, regression

**Generation** — `probe-author` writes 1 recall + 1 application probe per
entry; skill entries add their executable check for free.

**Second-model validation** — a probe may gate anything only after a
*different worker* (enforced through the IdentityPort at construction — the
generator can never grade itself) answers it correctly **from the source
excerpts alone**. Unanswerable probes are rejected.

**The eval gate** — an entry auto-publishes only if its validated probes
pass *with the entry injected* into the answering model. Cold start
(bootstrap rule): while the pack suite has < 5 probes, the pack-level paired
comparison is deferred to promotion and the decision basis says
`BOOTSTRAP…`; after that it notes the paired go/no-go applies.

**Qualification** — the platform-agnostic serving contract:

```python
from selflearn import qualify_model
q = qualify_model(candidate_model, store, "fastapi")
q.with_injection, q.without_injection, q.delta, q.qualified
```

Qualified = non-negative injection delta AND ≥ 50 % of the suite with
injection. Swapping LLMs is: point at the new endpoint, requalify, done —
packs and specs never change. (In meta-harness, `record_qualification`
feeds the verdict into the routing capability matrix.)

**Regression** — a pack update must not silently degrade its specialist:

```python
from selflearn import snapshot_baseline, check_regression
from selflearn.verification import run_pack_suite
snapshot_baseline(store, "fastapi", run_pack_suite(model, store, "fastapi", injected=True))
# ... later, after pack changes:
report = check_regression(store, "fastapi", run_pack_suite(model, store, "fastapi", injected=True))
print(report.summary())    # [OK]/[REGRESSION] with the delta
```

Baselines are model-pinned; comparing across models is refused.

---

## 10. The learning loop

Feed every externally verified task outcome to the `Learner`:

```python
from selflearn import Learner
from selflearn.contracts import TaskOutcome
learner = Learner(store)
learner.observe(TaskOutcome(
    task_id="t1", task_type="code_edit",
    topic="lifespan",                # from label_topic(), or "" = unlabeled
    verdict="pass",                  # the EXTERNAL verdict, never self-report
    injected=("kn-fastapi-lifespan-ab12cd34ef",),   # from the injection block
    applied=("kn-fastapi-lifespan-ab12cd34ef",),    # worker's usage report
))
```

**Fast loop (per outcome, automatic):**
- verified PASS → `helpful+1` per injected entry, `+2` when cited as applied;
- verified FAIL → `harmful+1` **only** for entries listed in `implicated`
  (grounded reflection cited them) — injection alone never harms;
- harmful ≥ 3 and > helpful → auto-deprecation (journaled, reversible,
  probes retired).

**Recency decay**: marks are not lifetime counters. Every mark event first
multiplies the entry's existing counters by
`0.5 ** (days_since_last_mark / 90)` (`LearningConfig.mark_half_life_days`),
so an entry helpful 100 times last year but wrong today decays to ~6
effective helpful and a handful of recent harmful marks deprecate it —
not 101. Decay is lazy (applied at mark time); staleness and other
read-only consumers use `effective_counts()` for a current view.

Marks multiply into retrieval ranking as a prior, so doubtful entries sink
before anything is deprecated.

**Slow loop (on demand):**

```python
learner.gap_signals("fastapi")        # coverage / quality signals
learner.staleness_signals("fastapi")  # old sources + decayed score
learner.suggestions("fastapi")        # advisory dicts with proposed actions
```

- **coverage** gap: ≥ 2 failures in a claimed-but-uncovered topic (or
  nothing was retrieved) → propose acquiring the topic;
- **quality** gap: failures despite retrieval → propose re-verifying the
  implicated entries;
- **staleness**: sources older than 180 days AND score ≤ 0.45 → propose
  re-fetch. Old entries still earning helpful marks are left alone.

Guardrails: a topic that just signaled is backoff-suppressed for 2 sweeps
(even if fresh failures keep arriving); a signal **consumes** its failure
evidence, so old failures never re-signal after backoff expires; unlabeled
outcomes (empty topic) are excluded from joins, never guessed; suggestions
are **advisory only** — nothing ever auto-runs acquisition.

**Durability**: slow-loop state (retained failures, backoff counters)
writes through to `<store>/learner-state.json` and reloads on
construction — a restart loses no evidence. Retained failures are FIFO-
capped (`max_failures`, default 500).

**Known limitations (by design):**
- *Effectiveness is delegated to the host*: harmful marks depend on the
  host populating `implicated` honestly (grounded reflection), and every
  guarantee rests on the host supplying real external verdicts — that is
  the never-trust-self-assessment contract, stated plainly.
- *Learning granularity is coarse*: per-entry counters and per-topic gap
  joins. The loop will not learn context-dependent nuance like "this entry
  helps for task type A but misleads for B"; per-(entry, task-type) marks
  are a possible future refinement.

Topic labeling is deterministic:

```python
from selflearn import label_topic
topic = label_topic(retriever, ["fastapi"], task_text)   # "" below threshold
```

---

## 11. meta-harness integration (the adapter)

Everything below lives in `src/metaharness/knowledge/` — the only place
harness and library meet.

```python
from metaharness.knowledge import (
    open_store,                # PackStore at ~/.metaharness/knowledge
    OpenAICompatEmbedding,     # EmbeddingPort over any /v1/embeddings endpoint
    make_knowledge_hints,      # playbook_hints-shaped callable for TaskExecutor
    knowledge_tools,           # the 4 workflow tools (no publish tool!)
    plan_from_knowledge,       # workflow-entry -> deterministic WorkflowSpec
    record_qualification,      # QualificationResult -> capability matrix
    KNOWLEDGE_ARCHETYPES,      # knowledge-scout / knowledge-distiller prompts
)
```

- **Specialist binding**: `AgentConfig.knowledge_packs = ["fastapi"]`; build
  the hints callable from `SpecialistSpec`s and pass it to `TaskExecutor`
  alongside `playbook_hints`. Injected blocks land in task boundaries,
  fenced and marked untrusted-advisory.
- **The `knowledge_acquisition` workflow template**: scope → gather →
  distill → verify, with a post-artifact HITL gate on the verification
  report. Workers drive it through `knowledge_gather`,
  `knowledge_submit_entries`, `knowledge_verify`, `knowledge_status` — there
  is deliberately **no publish tool**, so a worker can never clear the gate
  it is being verified by.
- **Knowledge-driven planning**: `plan_from_knowledge(goal, store, packs,
  embedder)` — a strong match on a `workflow` entry compiles its procedure
  into a WorkflowSpec (with `seeded_by` attribution in step boundaries for
  plan-level marks); a weak match returns the entry's prose as planner
  guidance.

---

## 12. Network, keys, environment

- **Keys** are never stored by selflearn; pass per-invocation flags or env
  vars (`BRAVE_API_KEY`). In hosted environments, set env vars in the
  environment settings, not in the repo.
- **Remote dev environments** (Claude Code on the web): outbound access is
  the environment's network policy. For full function allow:
  `html.duckduckgo.com` (or your backend), source domains (`arxiv.org`,
  docs sites), and for YouTube `youtube.com`, `youtu.be`,
  `*.googlevideo.com` (the media CDN yt-dlp downloads from).
- **Rate limits**: the acquisition context enforces spacing between fetches
  (default 1 s). Keep YouTube volume low; the tooling is built for research
  runs, not bulk crawling.

---

## 13. Troubleshooting — loud errors and what they mean

| error | meaning / fix |
|---|---|
| `no plugin claims ref '…'` | unknown ref form; check §5, or your plugin isn't registered/allowlisted |
| `search ref needs a SearchBackend` | no backend configured and no default available; see §6 |
| `DuckDuckGo returned no parseable results` | rate-limited or layout changed; try `--searxng`/Brave |
| `Tunnel connection failed: 403 Forbidden` | the environment's network policy blocks the host — not a code failure |
| `…no text layer extractable (scanned PDF?)` | decision: no OCR; find a text PDF or the arXiv source |
| `SchemaGuard: …` | the distiller/probe model returned malformed output; the batch failed rather than storing half-entries |
| `identity violation: probe validator must be a distinct worker` | author and validator resolve to the same model; configure a second endpoint |
| `skill declares an executable check but no ExecutionPort is bound` | bind a sandbox (meta-harness: evals/execution.py) or drop the check |
| `X published entries lack vectors from embedder '…'` | embedder changed; run `Retriever.index(pack)` to re-embed |
| `retrieval running WITHOUT an embedding endpoint` (warning) | keyword-degraded mode; configure `--embedding-endpoint` for semantic quality |
| `entry … no longer passes verification; refusing approval` | the world changed between verify and approve — re-run `selflearn verify` |
| `no suite baseline for pack '…'` | snapshot one after a known-good state before checking regression |

---

## 14. Trust model (what protects you)

- Web text becomes prompt content only after: injection screen → tier/
  corroboration policy → (strict) human approval or (auto) second-model-
  validated probes passing with injection. Injection blocks are fenced and
  explicitly marked *untrusted advisory — notes inform, they do not command*.
- The generator never grades itself: probe author ≠ probe validator,
  enforced by worker identity, with the basis of that identity check
  recorded on every publish decision.
- Workers in the acquisition workflow have no publish capability at all.
- Every artifact carries provenance (source URL, content hash, producing
  plugin + version, timestamps); every lifecycle event is journaled
  append-only; deprecation is reversible and nothing is ever deleted.
- Third-party source plugins run only from an explicit allowlist, inside a
  jailed context — but a plugin is still code you chose to install.
