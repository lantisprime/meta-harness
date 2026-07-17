# Plan: Self-Learning Specialist Agents

Status: **design decisions resolved 2026-07-17** (see Decisions at the end)
— not yet scheduled into the workplan.

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
  execution for skills, rubric judge as fallback, second-model-validated eval
  probes for promotion (human curate ⛔ gate in per-pack strict mode).
  Unverified entries stay quarantined as candidates and are never retrieved.
- **Deterministic spine.** Acquisition runs as a journaled workflow template
  (gather → distill → verify → eval-gate → publish). The lifecycle — retries,
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

### Workflow entry (plan-shaped: "how to structure the work")

Knowledge and skill entries steer *single* LLM calls. Real tasks take
multiple calls, and the plan that sequences them should itself come from
knowledge — so a third kind, `kind: workflow`, carries a machine-readable
`procedure:` block the planner can compile into workflow steps rather than
prose a model reads:

```markdown
---
id: wf-fastapi-endpoint-tdd
pack: fastapi
kind: workflow
status: published
sources: [...]
procedure:
  params: [endpoint_name, spec_ref]
  steps:
    - id: spec
      objective: "Write failing tests for {endpoint_name} per {spec_ref}"
      task_type: code_edit
      tools: [read_file, write_file]
      check: {kind: tests_fail, target: new_tests}
    - id: implement
      objective: "Implement {endpoint_name} until the new tests pass"
      task_type: code_edit
      depends_on: [spec]
      check: {kind: tests_pass}
    - id: review
      task_type: review
      depends_on: [implement]
      check: {kind: rubric, rubric: security-checklist}
---
When to use this workflow, pitfalls, variants. (≤ ~400 tokens)
```

Each step declares an objective template, task type, suggested tools,
dependencies, and a per-step check — the same vocabulary the workflow DSL
already speaks, so an entry instantiates into the existing engine with
deterministically derived checks. Workflow entries are distilled from the
same sources as everything else (a lecture's method section, a vendor's
recommended process, a paper's algorithm) and from skill harvesting when a
verified multi-step run reveals a reusable shape.

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

A specialist is a **declarative spec, not a model binding**: a JSON/YAML
document naming knowledge packs, an optional specialty system-prompt
archetype, the pack-derived eval suite that qualifies a model to serve the
role, and routing constraints (minimum tier). `AgentConfig` gains
`knowledge_packs: [fastapi, security]`; which LLM actually serves the
specialist is decided by the router/config at runtime, exactly as today. At
task time, the context builder retrieves top-k published entries (semantic
scoring: embedding cosine as the primary signal, keyword as a cheap
prefilter and loudly-flagged degraded fallback) under a dedicated
per-tier context budget slice, injected fenced and untrusted-marked —
advisory context, same treatment as the ✦ companion's inputs. The router's
capability matrix keeps working unchanged; specialists simply earn better
verified outcomes in their domain, so pre-routing naturally starts favoring
them.

## Platform agnosticism (requirement)

The design must not assume any particular LLM or vendor:

- **All model steps route through the runner layer.** Distillation,
  judge-verification, eval generation, vision extraction, and embeddings
  are ordinary capability-keyed worker/endpoint calls — they work
  against anything the harness can already drive: local
  OpenAI-compatible endpoints (LM Studio, Ollama), remote providers
  (Anthropic, OpenAI, Groq, …), and coding CLIs. `knowledge/` imports no
  provider SDK and contains no provider-specific prompt features; injection
  is plain fenced text that renders identically everywhere.
- **Artifacts are plain files.** Packs are MD + JSON directories — readable,
  git-diffable, and usable by any harness, not just this one. A pack can
  optionally be exported as an integrity-addressed archive via the existing
  `portable/` packaging for distribution to other deployments.
- **Models are qualified, not assumed.** Any model becomes eligible to serve
  a specialist by passing the pack's generated eval suite (below).
  Qualification results are recorded as capability evidence keyed by
  (model, pack) — extending the matrix's per-model evidence principle — so
  swapping LLMs is: point at the new endpoint, re-run the suite, done. No
  pack content or specialist spec changes.

## Eval generation from acquired knowledge (requirement)

Every published entry must also *teach the harness how to test for it*. The
verification module's **evalgen** step runs inside the acquisition workflow
and derives eval items from each candidate entry. The library's built-in
probe-suite runner mirrors the existing suite machinery (pass^k, paired
sign test); in meta-harness the adapter feeds its results into
`evals/gate.py: compare_suites` and the capability matrix rather than
duplicating them:

- **Recall probes** (knowledge entries): fact questions whose answer keys are
  extracted from the *verified sources* — never from model memory — checked
  deterministically (equals/contains/regex) where the fact allows, rubric
  judge with the entry as ground truth otherwise.
- **Application probes** (knowledge entries): a novel scenario that requires
  the knowledge to solve, judge-scored. These measure transfer, not parroting;
  reported separately from recall so a pack that only enables regurgitation is
  visible as such.
- **Skill probes** (skill entries): the entry's `check:` block wraps directly
  into an executable eval task run by the sandboxed execution verifier — the
  strongest evidence class.

Lifecycle coupling is strict: a probe may gate publishing only after passing
**second-model validation** — a different qualified model must answer it
correctly given only the verified sources, so the generator never grades
itself (in strict mode a human additionally reviews entry + probes together).
An amended entry regenerates its items; a deprecated entry retires them.
Items live in the pack under `evals/` with a suite manifest. Default density:
1 recall + 1 application probe per entry (skills carry their executable
`check:` for free), configurable per pack.

The generated suite is what makes the rest of the system honest. It is used
for:

1. **Pack promotion** — a pack version is promoted only if injection beats
   no-injection on the suite through the existing paired go/no-go gate.
2. **Model qualification** — the platform-agnostic serving contract above.
3. **Regression** — the suite reruns after any pack update; a knowledge
   change that silently degrades the specialist fails loudly.

## The learning loop (two-speed, mirroring `correction/`)

The learning module is deliberately the *dumbest* module in the system —
counters, thresholds, and joins over externally verified events. Any model
judgment inside the learning loop is self-assessment sneaking back in, so
the only LLM appearance is cosmetic: optionally rendering a gap cluster as
readable suggestion text, advisory-only.

**Input contract.** One event type from the host: a verified task outcome
carrying (a) the verdict from the external verification hierarchy, (b) the
**retrieval attribution list** — which entries were actually injected into
that task's prompt, from the run journal — and (c) the MAST failure label
when it failed. Unverified outcomes never enter.

**Fast loop — per-task marks with asymmetric credit assignment.** Injection
is not use, so marking is asymmetric:

- *Helpful marks are cheap and noisy-tolerant*: verified PASS → every
  injected entry gets `helpful+1`. Undeserved credit washes out over
  volume, and a false helpful mark only keeps an entry retrievable.
- *Harmful marks require implication evidence*: verified FAIL marks an
  entry `harmful+1` only when something implicates it — grounded reflection
  cites it, or the failure mode lands in the domain the entry claims. A
  false harmful mark can kill good knowledge, so the bar is higher.

Marks feed a score (the `PlaybookBullet.score()` shape) that multiplies
into retrieval ranking as a prior — doubtful entries sink *before* anything
is deprecated. Ranking demotion is the soft continuous consequence;
deprecation the hard discrete one: an entry deprecates when harmful marks
persist past a threshold over a window and outweigh helpful ones —
journaled, reversible, probes retired, nothing deleted (the evidence trail
is pack provenance).

**Slow loop — gap detection over the coverage map.** Offline, the module
joins failure-store MAST clusters against each pack's coverage map and
emits typed `GapSignal`s, each implying a different acquisition prompt:

1. **Coverage gap** — failures cluster in a claimed topic where retrieval
   surfaced little; the knowledge doesn't exist → acquire the topic.
2. **Quality gap** — relevant entries were retrieved but carry poor marks;
   the knowledge isn't working → re-verify/amend those entries.
3. **Staleness** — an entry's helpful rate decays and its sources are old;
   the world moved → re-fetch sources and refresh.

**Guardrails (anti-runaway).** Gap signals never auto-launch acquisition —
they surface advisory-only (✦ posture) for human approval, which breaks the
thrash loop *acquire → publish → fail → deprecate → re-acquire*. A signal
for a topic with a recent failed acquisition attempt is suppressed with
backoff, so even suggestions can't nag in a loop.

**Where honesty comes from.** Retrieval feedback is accepted as noisy; it
only steers day-to-day ranking. The clean signal is the generated eval
suite: promotion and regression run through the paired go/no-go, and a
publish batch that degrades the suite rolls back regardless of its marks.
Known weak point, stated: implication-based harmful marking depends on
grounded-reflection quality and will miss subtly bad entries that are never
cited in a failure analysis — the backstop is staleness decay plus suite
regression, which such an entry still cannot survive.

## How a specialist learns (lifecycle)

The model's weights never change; everything the agent "knows" that it
didn't know yesterday lives in the harness. The cycle: **act → get verified
→ get marked → expose gaps → research → publish verified knowledge →
retrieve it next time.**

1. A new specialist is only its spec: archetype prompt, seeded-or-empty
   packs, routing constraints. First tasks run on base-model competence.
2. Every task is externally verified, and the verdict drives three channels
   at three timescales: marks reweight retrieval immediately; failures gain
   MAST labels and playbook lessons per run; failure clusters become gap
   signals offline — the agent discovering *what it doesn't know*.
3. Acquisition (any mode) turns goals and gaps into verified knowledge:
   scout syllabus → reputable sources → distilled entries → corroboration,
   executable checks, second-model-validated probes → eval-gated publish.
   The agent is never allowed to believe its own study notes.
4. New knowledge changes behavior through retrieval, not retraining — same
   model, smarter prompt; skill entries carry procedures proven once.
5. The exam grows with the textbook: probes accumulate with entries, so
   promotion requires beating no-injection, and later changes cannot
   silently make the agent worse (regression ratchet).
6. Routing learns too: (model, pack) qualification evidence accumulates in
   the capability matrix, and because learned state is spec + packs + suite
   (plain files), expertise survives a model swap — requalify and go.

Concretely: a FastAPI specialist ships a bug using deprecated `on_event`
handlers → execution verification fails the task → coverage gap in the
`fastapi` pack → an approved acquisition run distills the official lifespan
docs into an entry plus probes → eval-gated publish → the next lifespan
task retrieves it and passes → helpful marks rank it higher. Months later
its helpful rate decays as FastAPI moves on → staleness signal → re-fetch
and amend.

## Steering: getting the model to actually use the knowledge

Injection is not use — a model can ignore context. Usage can't be forced,
so the harness makes it **cheap to use, costly to ignore, measurable, and
self-optimizing**:

1. **Injection mechanics** (context-engineering KB): the knowledge block
   sits in a stable slot (KV-cache-friendly prefix ordering), under its own
   budget slice, top-k entries at ≤ ~400 tokens each, ordered so the most
   relevant entries land nearest the task statement — small and close
   enough that nothing is "lost in the middle".
2. **Addressability framing** (self-correction KB, arXiv 2606.05976:
   external-role framing lifts correction 23–93 pp): entries are rendered
   depersonalized, as *verified field notes from domain research* — never
   "your memories" or "you previously learned", and no trust-me framing.
   The block header is a directive: *ground your approach in the applicable
   notes below, cite entry ids where used, say so if none apply.*
3. **Structured usage report**: the delegation contract adds an
   `applied_knowledge` field — entry ids used and how, or explicitly none.
   This refines credit assignment (marks weight toward entries the worker
   *cited as used*, not merely injected) but is self-report, so it is
   **never a verification signal** — it tunes attribution, nothing else.
4. **Skills as step contracts**: a retrieved skill entry's recipe becomes
   an explicit checklist in the task contract; the worker reports
   deviations. The skill's `check:` remains externally verified, so
   "followed the recipe" is claimed but "the recipe worked" is proven.
5. **Active lookup escape hatch** (RAG-MCP/MCP-Zero pattern in the KB):
   besides the passive top-k, a `knowledge_lookup` tool in the registry
   lets the worker pull additional entries mid-task — covering the case
   where the need only becomes visible after work starts. Normal tool-cap
   rules apply.
6. **Verification pressure**: ignoring applicable knowledge surfaces as
   verified failures, harmful-mark implication, and gap signals — and the
   paired go/no-go means a pack only promotes when injection measurably
   beats no-injection. If the steering format doesn't work, promotion
   fails; nobody has to notice manually.
7. **The steering format is itself searchable**: slot position, framing
   text, k, and per-entry token caps are harness parameters — exactly what
   the existing Meta-Harness optimization outer loop searches, scored on
   pack suites through the same held-out gate. Steering gets tuned by
   evidence, not taste.

### After a verified success

When the worker's result comes back and passes external verification, the
deliverable ships (step output journaled, downstream phases consume it) and
the ledgers update deterministically: helpful marks weighted toward
entries cited in `applied_knowledge`, capability-matrix and qualification
evidence, provenance, pack usage stats. **The answer itself never becomes
knowledge** — task outputs are performances, not sources; letting them
self-publish would be self-assessment through the back door.

One deliberate exception: **skill harvesting.** When a verified success
contains a reusable procedure no existing skill entry covers, the harness
may propose a *candidate* skill entry (Voyager-style) — or, when the
success was a multi-step run with a reusable shape, a candidate *workflow*
entry. The proposal enters the acquisition pipeline exactly like external
material: quarantined, its `check:` derived from the test that verified the
original task, probes generated and second-model-validated, eval-gated
before publish. The agent's own experience is treated as just another
untrusted source.

## Knowledge-driven planning (multi-call tasks)

A task that needs multiple LLM calls needs a plan, and the plan should come
from the knowledge — while the *execution* of that plan stays with the
deterministic engine, never a free-running agent loop:

1. **Planning-time retrieval.** The goal→WorkflowSpec planner queries the
   specialist's bound packs *before* planning. A strong semantic match on a
   `workflow` entry means the planner **instantiates** it: parameters
   filled from the goal, `procedure:` steps compiled into the WorkflowSpec,
   per-step checks derived through the existing deterministic machinery. A
   weak match means the planner plans freehand with the entry's prose
   injected as planning guidance — knowledge constrains the plan even when
   it can't dictate it.
2. **Step-scoped retrieval.** Each instantiated step then gets its own
   knowledge/skill retrieval at execution time (scoped by the step's task
   type and objective), with its own `applied_knowledge` report — so
   multi-call tasks steer per call, not once at the top.
3. **Plan attribution.** The WorkflowSpec records `seeded_by:` entry ids.
   Run verdicts propagate to plan-level marks: a completed verified run is
   helpful evidence for the workflow entry; repeated failures *at the same
   step* implicate that step's definition specifically — a quality-gap
   signal proposing an amendment to the entry, not just a mark.
4. **Verification of workflow entries.** Beyond recall/application probes,
   a workflow entry can carry a **golden-run probe**: instantiate the
   procedure on a sandboxed micro-task and verify it completes — the
   strongest and most expensive evidence class, so it runs at promotion
   and qualification rather than on every publish.

This keeps the division of power intact: knowledge proposes the plan, the
deterministic engine owns the lifecycle, verification owns the verdict.

## Module architecture (decision 8)

The self-learning system is a **standalone distribution** — `selflearn/` at
the repo root with its own `pyproject.toml` and **zero `metaharness`
imports** (the `development/remote_workplan/` precedent; extractable to its
own repo later, like `youtube-distiller`). meta-harness consumes it through
one thin adapter package, `src/metaharness/knowledge/`. Inside `selflearn`
are six modules with **typed contracts** between them. Modules never import
each other's internals — they exchange frozen value objects — so each is
independently testable, replaceable, and usable by any harness (the
platform-agnosticism requirement applied to the code itself):

| Module | Package | Consumes → Produces |
|---|---|---|
| Acquisition | `selflearn/acquisition/` | `SourceRef` → `SourceDocument` — plugin-based, below |
| Distillation | `selflearn/distillation/` | `SourceDocument[]` → `CandidateEntry[]` — SchemaGuard-enforced, injection screen |
| Verification & evals | `selflearn/verification/` | `CandidateEntry` → `VerifiedEntry + ProbeSet + PublishDecision` — reputability policy, corroboration, skill `check:` execution, evalgen, second-model probe validation, eval gate |
| Store | `selflearn/store/` | packs on disk — entries, manifests, embedding index, assets, quarantine/promotion state machine, provenance |
| Retrieval | `selflearn/retrieval/` | `TaskProfile` + bound packs → budgeted, fenced injection block — semantic scoring |
| Learning | `selflearn/learning/` | verified task outcomes → helpful/harmful marks, auto-deprecation; failure clusters → gap signals / acquisition suggestions |

Contract flow: `SourceRef → SourceDocument → CandidateEntry →
(VerifiedEntry, ProbeSet, PublishDecision)`. The store is the only shared
state, and the `knowledge_acquisition` workflow template is pure
orchestration — each phase calls exactly one module, keeping the
deterministic spine intact: `scope → gather (acquisition) → distill
(distillation) → verify + eval-gate (verification) → publish (store)`.

### Ports: how a host plugs in (decision 11)

`selflearn` is host-agnostic through five small Protocols; everything else
is self-contained (stores are plain files):

- **`ModelPort`** — one `complete(request) -> result` for every LLM step
  (distill, judge, evalgen, vision descriptions). meta-harness binds its
  runner layer and tier router; another harness binds whatever it drives.
- **`EmbeddingPort`** — `embed(texts) -> vectors`, keyed by embedder id (the
  re-index-on-swap rule lives in the library).
- **`ExecutionPort`** — sandboxed command execution for skill `check:`
  blocks and executable probes. meta-harness binds the `evals/execution.py`
  sandbox; a host without one gets executable checks refused loudly, never
  skipped silently.
- **`ProvenancePort`** — append-only event sink. meta-harness binds the
  hash-chained provenance log; the standalone default is a local JSONL.
- **`IdentityPort`** — answers "are these two workers distinct?" for the
  probe-author/validator separation. meta-harness binds Ed25519 worker
  identities; the standalone default compares model ids (weaker, and
  reported as such in the publish decision).

The library ships its own minimal probe-suite runner (pass^k + paired
comparison) so eval-gated publishing works with no host at all, and a small
`selflearn` CLI (`acquire / distill / publish / retrieve / evals`) exercises
the full pipeline standalone — which doubles as the integration-test
surface. The meta-harness adapter (`src/metaharness/knowledge/`) is the only
place harness and library meet: it binds the five ports, registers the
worker-agent archetypes, wires `AgentConfig.knowledge_packs`, feeds suite
results into `evals/gate.py`'s go/no-go and the capability matrix, and
exposes the `knowledge_acquisition` workflow template.

Publishing is **eval-gated** (decision 3): the deterministic
reputability/citation policy must pass, the entry's second-model-validated
probes must pass with the entry injected, and the pack-level paired
go/no-go delta must be non-negative. A per-pack **strict mode** reinstates a
human curate ⛔ gate (post-artifact, the PR #21 UX) — the entry-by-entry pack
diff with sources remains the review surface either way.

### Acquisition plugins

Acquisition is a **plugin registry**: adding a source type never touches the
pipeline. A plugin implements one small protocol:

```python
class SourcePlugin(Protocol):
    id: str                       # "web", "pdf", "arxiv", "youtube", …
    requires: tuple[str, ...]     # optional extras / external CLIs, checked up front
    def can_handle(self, ref: SourceRef) -> bool: ...      # scheme/URL/mime match
    async def acquire(self, ref: SourceRef, ctx: AcquireContext) -> SourceDocument: ...
```

`SourceDocument` is the normalized envelope every plugin must emit: text
blocks and/or pre-chunked segments, assets (images tagged
figure/chart/equation for the vision path), and full provenance (url,
fetched_at, sha256, `locator`, producing plugin + version). Resolution is
deterministic: explicit registration order in config, first `can_handle`
match wins, and a ref no plugin claims fails the gather step loudly. The
`AcquireContext` hands plugins rate-limit budgets and the workspace jail —
plugins never carry their own filesystem or network policy.

Registration: built-in plugins ship in-tree; third-party plugins load only
from a config **allowlist** of Python entry points
(`metaharness.knowledge.sources` group). A plugin is code, and installing
one is a trust decision — provenance records which plugin (and version)
produced every document, so a bad plugin's output is traceable and revocable
as a unit. An `mcp` adapter plugin can wrap any connected MCP server that
exposes fetch/search tools, covering one-off sources without new code.

Built-in plugins:

- **`web`** (decision 2): the semantic search path. A natural-language
  research question goes to a pluggable search backend (Brave API,
  self-hosted SearXNG, or an MCP search server); pages are fetched and
  extracted (size caps, robots/rate-limit courtesy); passages are ranked
  against the question with the retrieval module's scorer. Emits
  question-ranked passages with provenance, not raw hits. Plain page-URL
  refs go through the same extraction (the `_web_fetch` upgrade).
- **`pdf`** (decision 5): prose from the text layer (`pypdf`); embedded
  figures/charts rendered via a permissively-licensed renderer (`pdfium`);
  vision-qualified workers convert figure → description, chart →
  description + data table when legible, equation → LaTeX. Vision-derived
  content carries the lower `extraction: vision` evidence class and needs
  corroboration or second-worker agreement. Assets live in the pack;
  prompts always receive the textual rendering, so serving models never
  need vision.
- **`arxiv`**: prefers the published LaTeX source tarball over the PDF —
  exact equations and captioned figures with no vision call; falls back to
  the `pdf` plugin.
- **`youtube`** (decision 7): drives the existing `yt-distill` CLI
  ([lantisprime/youtube-distiller](https://github.com/lantisprime/youtube-distiller),
  optional `[youtube]` extra). `analyze` yields `chunks.jsonl` —
  timestamped, source-linked, embedding-sized chunks plus a structured
  summary record — emitted as pre-chunked segments; `slides` yields
  OpenCV-cropped slide/diagram/code frames with ±12 s transcript context
  (`slides.json` + `frames/*.jpg`), emitted as assets for the same vision
  path as PDF figures. Everything upstream of the vision step is
  deterministic (yt-dlp captions / local faster-whisper, extractive
  scoring, classical CV — no LLM), which fits the evidence model: raw
  chunks are the citable source; entry-writing happens only in the
  distillation module. Channel identities join the reputability tiers (a
  conference or university channel can be `official`/`primary`); timestamp
  ranges become source `locator`s. The tool already rate-limits and never
  bypasses access controls; cookie-based access stays a user-supplied,
  never-stored input.
- **`local`**: drop-in directory of `.md`/`.json` files — including
  `memory/knowledge_base/` and existing `yt-distill` `distilled/` folders.

The distillation module additionally runs a deterministic injection-pattern
screen over source text and candidate entries; a match quarantines the entry
for human review regardless of eval results.

### Acquisition modes: how knowledge gets in

Gap-driven study is the *reactive* trigger, not the main one — packs are
built and grown proactively. Five entry points feed the same pipeline
(gather → distill → verify → eval-gate → publish); how knowledge arrives
never changes what it takes to publish:

1. **Goal-directed research (primary).** A human gives a pack-building goal
   ("build a FastAPI pack", "make a security-review specialist"). The
   `knowledge-scout`'s scope phase turns it into a **syllabus**: subtopics →
   research questions → a source-type strategy per subtopic (official docs,
   papers, lectures), effort-scaled to the run budget. The syllabus persists
   in the pack manifest as a **coverage map** — topics claimed vs. covered —
   which later powers dedupe (don't re-acquire covered ground) and makes
   coverage-gap detection concrete.
2. **Bulk seeding.** Point the pipeline at an existing corpus: a directory
   of `.md`/PDF files, a URL reading list, an arXiv query, a YouTube
   playlist or channel (`yt-distill` `distilled/` folders load directly via
   the `local` plugin). Refs fan out through normal plugin resolution.
3. **Citation expansion.** Distilled entries surface adjacent references —
   papers cite papers, docs link docs, lectures name tools. The scout may
   propose follow-up refs from those citations (bounded depth,
   budget-capped, reputability-filtered), so a pack deepens from what it
   just learned rather than only from failures.
4. **Watched sources.** A pack can watch sources (a docs site, an arXiv
   category, a YouTube channel): scheduled runs propose acquisition for new
   or changed material, and staleness signals trigger re-fetch of aging
   entries.
5. **Gap-driven (reactive).** Failure clusters → gap signals, per the
   learning loop.

Modes 1–2 are human-initiated; modes 3–5 only *propose* runs (advisory,
human-approved, with backoff) — nothing self-triggers acquisition.

### Agent topology — orchestrator–workers (decision 9)

Each module's LLM step is served by a **named worker-agent role**, coordinated
in the orchestrator–workers pattern — with the orchestrator being the
existing deterministic `WorkflowEngine`, never an LLM. This is the layering
the project's own research cache prescribes ("durable deterministic spine →
LLM decomposition only *within* steps → workers behind a uniform runner
interface") and it avoids the documented supervisor failure modes (context
saturation after ~8–12 round trips, single point of failure).

| Module | Agent? | Worker role(s) |
|---|---|---|
| Orchestration | No — journaled `WorkflowEngine` runs the template (retries, gates, resume) | — |
| Acquisition | Plugins are tools, not agents; one agent up front | `knowledge-scout`: turns the goal into search queries, selects refs to acquire, effort-scaled |
| Distillation | Yes — fan-out, one call per `SourceDocument` | `knowledge-distiller`: doc → candidate entries, SchemaGuard-forced |
| Verification & evals | Yes — three roles, deliberately distinct | `knowledge-judge` (rubric fallback), `probe-author` (evalgen), `probe-validator` (second-model check) |
| Store | No — deterministic code | — |
| Retrieval | No — embedder endpoint call + deterministic scoring | — |
| Learning | Deterministic (marks, MAST clustering); advisory read only | ✦ companion surfaces gap suggestions, never executes |

Why this fits the existing machinery, not just the pattern:

- **Roles, not models.** Each worker role is an `AgentConfig` archetype
  (`roles`/`capabilities`/`system_prompt` already exist); the router assigns
  the cheapest capable tier per role via the capability matrix. Platform
  agnosticism holds — a "distiller" is a role any qualified worker can serve.
- **Identity-enforced separation.** The second-model probe check stops being
  a prompt convention: the engine verifies `probe-validator`'s Ed25519
  worker identity and model differ from `probe-author`'s before accepting
  the validation — a runtime check in the trust plane, aligned with
  "policies enforced by the runtime, not prompt text".
- **Delegation contracts + context isolation.** Workers get an explicit
  contract (objective, output schema, source refs, boundaries) and return
  schema-checked artifacts, not conversation — the orchestrator's context
  never accumulates worker transcripts (contract in, summary out).
- **Bounded dynamic decomposition.** The one place the classic LLM
  orchestrator-workers shape appears is *inside* the gather phase: the
  `knowledge-scout` may decompose a research goal into parallel sub-queries
  with effort-scaling rules in its archetype prompt — bounded by the phase
  budget, journaled like any other step.
- **Per-step binding.** Pinning phases to roles uses the per-step agent
  preference mechanism being added by issue #29 — the knowledge template
  becomes its first internal consumer, with automatic routing as fallback.
- **Fan-out caveat (implementation question for M4).** Distill-per-document
  and validate-per-probe want intra-phase concurrency; whether that runs as
  engine-level parallel steps or a concurrent loop inside one journaled
  phase depends on what the engine supports today — decide at M4, journal
  per-item either way so resume never re-does completed items.

## Milestones

Each milestone is independently shippable and tested; later ones can be
re-scoped after we see the earlier ones work.

Milestones map one-to-one onto modules, so each ships as a bounded package
with its contract types and tests. Module paths live in the standalone
`selflearn` distribution; anything touching `AgentConfig`, templates, the
trust plane, or the UI is adapter-side (`src/metaharness/knowledge/`).

1. **M1 — Package skeleton + store module** (`selflearn/` distribution:
   pyproject, contract value objects, the five port Protocols, adapter
   stub; `selflearn/store/`: entry/manifest models, quarantine state
   machine, provenance, boot loading). Import `memory/knowledge_base/*.md`
   and one `yt-distill` `distilled/` lecture as seed packs to prove the
   format.
2. **M2 — Retrieval module + specialist binding** (`selflearn/retrieval/`
   semantic scorer — embedding cosine primary with keyword prefilter,
   manifest vectors keyed by embedder model with re-index on embedder swap,
   loud keyword-only degradation when no embedder is configured —
   declarative specialist spec, `AgentConfig.knowledge_packs`,
   context-budget slice, fenced injection, helpful/harmful feedback
   wiring). Value ships here even with hand-authored packs, against any
   configured worker.
3. **M3 — Acquisition + distillation modules** (`selflearn/acquisition/`:
   `SourcePlugin` protocol, registry with allowlisted entry points, and the
   built-in `web`, `pdf`, `arxiv`, `youtube`, `local` plugins;
   `selflearn/distillation/` with SchemaGuard and the injection screen; the
   standalone `selflearn` CLI; `knowledge-scout` and `knowledge-distiller`
   agent archetypes adapter-side; loud failure paths throughout).
4. **M4 — Verification module + acquisition template**
   (`selflearn/verification/`: reputability policy config, corroboration,
   skill `check:` execution, `knowledge-judge` archetype; the
   `knowledge_acquisition` template wiring all modules with per-step role
   binding; the intra-phase fan-out decision; adapter-side planner
   integration — planning-time retrieval and `workflow`-entry
   instantiation in the goal→WorkflowSpec planner; publishing runs in
   strict human-gated mode until M5 lands the eval gate).
5. **M5 — Evalgen + eval-gated publishing** (verification module's evalgen
   with `probe-author` / `probe-validator` archetypes and identity-enforced
   distinctness, eval-gated auto-publish as the default, pack-promotion
   paired go/no-go, model qualification runs recording (model, pack)
   capability evidence).
6. **M6 — Learning module** (`selflearn/learning/`: gap detection from MAST
   clusters, console card with advisory suggestions, auto-deprecation,
   suite regression on pack updates).
7. **M7 — Web UI** (pack browser, entry + probe diff view at the curate gate,
   specialist wizard step in Settings, qualification results per model).

## Decisions (resolved 2026-07-17)

1. **Retrieval — semantic scoring with embeddings** *(revised 2026-07-17)*.
   Embedding cosine over stored entry vectors is the primary retrieval
   signal; keyword matching is a cheap prefilter and the loudly-flagged
   degraded fallback when no embedding endpoint is configured. The embedder
   is part of the provider abstraction (any OpenAI-compatible embeddings
   endpoint, local or remote — qualified, never assumed). Pack manifests
   record which embedder produced each vector; swapping embedders triggers
   re-indexing, since vectors are never portable across models.
2. **Web search — native semantic search tool.** Builtin `web_search` with
   pluggable backends (Brave API, self-hosted SearXNG, MCP search servers as
   backends) returning question-ranked passages with provenance, not raw
   hits.
3. **Curation — eval-gated auto-publish.** Reputability policy + validated
   probes passing with the entry injected + non-negative pack-level paired
   delta ⇒ publish. Per-pack strict mode reinstates the human curate ⛔ gate.
4. **Granularity — tech packs, roles compose.** Packs are per-technology/
   domain; a specialist role is a spec composing several packs + an
   archetype prompt.
5. **PDF — rich extraction.** Text layer for prose; diagrams, charts, and
   equations via vision-qualified workers (lower evidence class, agreement
   check required); arXiv LaTeX-source fast path; loud failure when a
   document yields nothing usable.
6. **Probe QC — second-model check.** A probe gates publishing only after a
   different qualified model answers it correctly from the sources alone;
   deterministic answer keys wherever the fact allows. Density default:
   1 recall + 1 application per entry, configurable per pack.
7. **YouTube lectures — reuse `yt-distill`** *(added 2026-07-17)*. The
   `youtube-distiller` repo already solves acquisition and deterministic
   distillation: captions/whisper → timestamped retrieval chunks +
   extractive summary (`chunks.jsonl`), and classical-CV slide/diagram/code
   frame extraction with transcript context (`slides.json`, `frames/`).
   meta-harness consumes those artifacts through a `youtube` fetcher instead
   of reimplementing; slide images ride the decision-5 vision path. Channel
   identities extend the reputability tiers; timestamp ranges extend source
   provenance as `locator`s. Its existing `distilled/` corpus (a dozen
   agent-engineering lectures) is additional seed material for M1 alongside
   `memory/knowledge_base/`.
8. **Architecture — six modules, plugin acquisition** *(added 2026-07-17)*.
   `knowledge/` splits into acquisition / distillation / verification-and-
   evals / store / retrieval / learning, exchanging frozen value objects
   (`SourceRef → SourceDocument → CandidateEntry → VerifiedEntry + ProbeSet
   + PublishDecision`) with the store as the only shared state and the
   workflow template as pure orchestration. Acquisition is a plugin
   registry behind the `SourcePlugin` protocol — built-ins in-tree (`web`,
   `pdf`, `arxiv`, `youtube`, `local`), third-party only via an explicit
   entry-point allowlist, plus an `mcp` adapter for tool-backed sources;
   per-plugin provenance makes any plugin's output revocable as a unit.
9. **Agent topology — orchestrator–workers with a deterministic
   orchestrator** *(added 2026-07-17)*. Module LLM steps are served by
   named worker-agent roles (`knowledge-scout`, `knowledge-distiller`,
   `knowledge-judge`, `probe-author`, `probe-validator`) implemented as
   `AgentConfig` archetypes and routed by the capability matrix; the
   orchestrator stays the journaled `WorkflowEngine`. Store and retrieval
   remain pure code; the ✦ companion keeps the advisory-only learning
   surface. `probe-author` / `probe-validator` distinctness is enforced by
   worker identity + model comparison in the trust plane, not by prompt
   text. Phase→role binding rides the per-step agent preference from
   issue #29.
10. **Workflow-kind entries + knowledge-driven planning** *(added
    2026-07-17)*. A third entry kind, `kind: workflow`, carries a
    machine-readable `procedure:` block (steps with objective templates,
    task types, tools, dependencies, per-step checks) that the
    goal→WorkflowSpec planner instantiates at planning time; weak matches
    inject as planning guidance instead. Specs record `seeded_by:` for
    plan-level marks and step-specific quality gaps; golden-run probes
    verify workflow entries at promotion. Knowledge proposes the plan, the
    deterministic engine owns the lifecycle.
11. **Standalone module** *(added 2026-07-17)*. The whole self-learning
    system ships as its own distribution — `selflearn/` at the repo root
    with its own pyproject, zero `metaharness` imports, and a standalone
    CLI + built-in probe-suite runner — extractable to its own repo later,
    like `youtube-distiller`. Hosts integrate through five ports
    (`ModelPort`, `EmbeddingPort`, `ExecutionPort`, `ProvenancePort`,
    `IdentityPort`); meta-harness consumes it via one adapter package
    (`src/metaharness/knowledge/`) binding those ports to the runner layer,
    sandbox, provenance log, trust plane, capability matrix, and the
    `knowledge_acquisition` workflow template.

## Residual risks (tracked, not blocking)

- **Prompt injection through published entries.** Eval gates verify truth,
  not intent — a factually correct entry could still carry a steering
  payload. Mitigations: distill-time injection screen, fenced
  untrusted-marked advisory-only injection, strict mode for sensitive packs.
- **Vision extraction fidelity.** Chart→data and equation→LaTeX are
  model-dependent; second-worker agreement is required but not infallible —
  the `extraction: vision` evidence class stays visible on every derived
  claim.
- **Eval-gate cost.** Probe validation and paired suite runs are model
  calls; per-acquisition-run budgets are enforced by the existing `Budget`
  machinery, and the qualification suite runs on demand (model swap or pack
  promotion), not continuously.
- **Plugin trust.** Acquisition plugins are code. The entry-point
  allowlist, the jailed `AcquireContext`, and per-plugin provenance bound
  the blast radius, but a malicious third-party plugin remains a
  supply-chain risk — the same class as any installed dependency, and worth
  stating on the Settings surface that enables one.
