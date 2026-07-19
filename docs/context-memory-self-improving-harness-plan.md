# Context, Memory, Rehearsal, and Self-Improving Harness Plan

Status: proposed implementation plan; no product implementation is included in this change.

Authority: this plan implements the mission and enhancement constraints in
[`PROJECT_CHARTER.md`](PROJECT_CHARTER.md). If the plan and charter diverge, the
charter governs until an explicit charter revision reconciles them.

Execution summary: [`dual-subscription-high-level-implementation-plan.md`](dual-subscription-high-level-implementation-plan.md)
maps this detailed roadmap onto coordinated Codex and Claude Code workstreams,
milestones, protected gates, schedule, and subscription budget.

## Outcome

Meta-Harness should become a control plane that:

1. creates, recommends, manages, corrects, packages, and deploys a population of
   goal-specific evolving harnesses, each produced from a measurable
   goal-family specification as generated executable instrumented code;
2. deterministically builds the right bounded context for each model call;
3. maintains distinct working, episodic, semantic, and procedural memory and
   can treat governed memory management as a trainable, domain-scoped cognitive
   skill rather than passive storage;
4. rehearses typed workflow-graph and generated-code candidates against a declared distribution
   of task variations before deployment;
5. can adapt approved roles, relationships, sequencing, and information flow
   during genuinely complex runs without escaping hard constraints;
6. evaluates models and harnesses from the complete item-response matrix with
   uncertainty, item quality, contamination, and exposure controls;
7. records exactly what generated source, graph, context, evidence, model
   weights, and runtime topology influenced
   each attempt;
8. learns from verified outcomes without treating model output as trusted memory;
9. proposes bounded harness improvements—including regenerating the smallest
   justified source-code surface—against protected evaluations; and
10. evaluates every evolving harness snapshot across future batches, held-out
   transfer, replay/retention, artifact use, and efficiency rather than only its
   final development score; and
11. evolves versioned evaluators on scheduled runs from sampled sensor evidence,
    while freezing one evaluator snapshot inside each comparable assessment;
    and
12. composes versioned hybrid frontier/open-weight or pure open-weight model
    portfolios, tunes their role routing and inference parameters, evolves the
    open checkpoints from sensor-grounded evidence, and targets self-contained
    releases able to run their own bounded H/E/W improvement campaigns; and
13. distills verified outcomes and reusable cognitive-skill policies into
    parameterized `SolutionTemplate` artifacts that compatible future harnesses
    can instantiate and independently re-evaluate; and
14. packages an eligible generated harness with its code, dependencies, graph,
    prompts, tools, sensors, evaluators, model/weight references, policies,
    manifests, provenance, and rollback parent for a declared deployment
    target; and
15. requires explicit human promotion, monitors the deployed release, and
    supports exact rollback.

The implementation order is intentional: self-improvement is unsafe and hard to
evaluate until context selection, memory provenance, calibrated evaluation,
rehearsal evidence, bounded runtime adaptation, and durable trace evidence are
first-class harness primitives.

## Distilled Corpus Coverage

The source review underlying this plan covers nine reviewed folders under
`/Users/charltondho/Developer/projects/youtube-distiller/distilled`. A separate
new folder, `forget-loop-engineering-agentic-engineering-is-about-this`, was
discovered during the adaptive-engineering audit but is not included in these
totals or conclusions because it is a different source and has not yet received
the same full review.

| Source folder | Non-image artifacts checked | Visual artifacts checked | Distinct contribution to this plan |
| --- | ---: | ---: | --- |
| `harness-engineering-masterclass` | 8 | 40 frames | Complete harness primitive model; context delivery versus context management; durable state, orchestration, skills, verification, observability, and the failure-to-harness ratchet. |
| `ai-agent-memory-masterclass` | 6 | 0 | Working, episodic, semantic, and procedural memory; the context builder; contradiction, staleness, pinning, decay, compression, and forgetting. |
| `building-ai-agent-scratch-python` | 8 | 40 frames | Concrete control flow: UI to harness to model; bounded tool loop; head-and-tail compaction; JSONL durability; episodic search; deterministic planning; isolated subagents; receipt-based verification and tracing. |
| `self-improving-ai-agents` | 7 | 0 | Frozen target model; weakness mining; editable-surface ladder; protected evaluation; miner and guard tasks; held-out testing; repeated trials; per-task non-regression; human promotion and rollback. |
| `beyond-harness` | 6 | 0 | Fixed versus adaptive engineering; complicated versus complex problem spaces; runtime reorganization; horizontal coordination; constraint design; selection pressure; stability, drift, monoculture, and legibility risks. |
| `stop-evaluating-models-like-it-s-50s-alejandro` | 6 | 0 | Item Response Theory; item difficulty/discrimination; uncertainty; benchmark auditing; adaptive/private item banks; residual contamination and inference anomaly detection; differential item behavior; error-profile correlation and model complementarity. |
| `self-evolving-ai` | 6 | 0 | GaP graph-as-policy synthesis and rehearsal-time optimization, plus HASE model/harness co-evolution. Both were reconciled against their primary arXiv papers; Meta-Harness was also checked as the named prior-work baseline. |
| `self-learning-ai-swarm-intelligence-new-code-rsi` | 6 | 0 | SwarmResearch explorer/optimizer lineages and orchestrator-guided scaling; CORAL shared persistent memory and reflection/consolidation/redirection heartbeats; EvoX search-policy evolution; and SIA harness/weight updates. All claims are reconciled against the primary papers and official repositories. |
| `open-32b-w-automemory-beats-opus` | 6 | 0 | AutoMem's two-axis treatment of memory as a cognitive skill: an `H` scaffold loop over code/prompts/schema/actions and a `W` loop that trains a dedicated memory-only LoRA while the task-action model remains frozen. The comparison claims and limitations were reconciled against the paper, project results, and official code. |
| **Total** | **59** | **80 frames** | **139 files accounted for.** |

Coverage checks:

- Each `analysis.md` and the additional `self-improving-ai-agents/distilled.md`
  was reviewed.
- Each `analysis.json` schema, source metadata, full transcript, eight-item
  summary, key terms, and distilled chunk collection was checked.
- All 157 JSONL chunks were reviewed across the nine sources. Their segment
  ranges cover the full source transcripts: 15 memory, 20 build-from-scratch,
  17 harness-engineering, 20 self-improvement, and 17 adaptive-engineering
  chunks, plus 12 model-evaluation and 22 self-evolving-AI chunks. The latter
  cover all 945 transcript cues, and the swarm-intelligence/RSI source adds 18
  chunks from its 5,383-word generated-caption transcript. The AutoMem source
  adds 16 chunks from its 4,475-word original-caption transcript.
- Each `transcript.txt` matches the transcript embedded in its `analysis.json`
  (with only the terminal text-file newline differing where present). The SRT
  and VTT files were checked as alternate timed encodings of those transcripts.
- Both `slides.json` files contain 40 entries, both `slides.html` files refer to
  all 40 entries, and all 80 referenced JPGs exist and were visually reviewed.

This plan uses the overlap among the nine sources as corroboration, while also
preserving the unique requirements from each source. In particular, memory
lifecycle comes from the memory source, executable control flow comes from the
build-from-scratch source, and evaluation isolation comes from the
self-improvement source. The adaptive-engineering source adds a distinct
within-run, horizontal adaptation problem; it is not collapsed into the
cross-run self-improvement loop. None is treated as optional because it appears
in only one folder. The model-evaluation source adds an item-level measurement
layer; its adaptive-test recommendation is applied to repeated calibration and
routing, not used to skip mandatory safety regression guards.
The AutoMem source adds a further distinction between the memory substrate,
the `H` policy for operating it, and an optional open-weight `W` specialist
trained to exercise that policy. It does not make stored memory itself a model
weight or make every memory decision trustworthy.

### Primary-source reconciliation for the papers named in the video

The `self-evolving-ai` video features two different new systems—**GaP
(Graph-as-Policy)** and **HASE (Harness-Aware Self-Evolving)**—and names
**Meta-Harness** as the closest prior work. The primary
[GaP paper](https://arxiv.org/abs/2607.05369),
[HASE paper](https://arxiv.org/abs/2607.03935), and
[Meta-Harness paper](https://arxiv.org/abs/2603.28052) override the video and
repository commentary wherever they differ. GaP's official demonstrations and
tables are additionally checked against the
[official GaP project/results](https://graph-robots.github.io/gap/).

| Primary paper | Learned artifact | Model weights | Evaluation boundary | Correct use here |
| --- | --- | --- | --- | --- |
| Meta-Harness | Executable task-specific harness code selected by outer-loop search over a full filesystem history. | Fixed. | Search distribution plus final test evaluation; the proposer never sees test results. | Retain rich candidate history and code search, but repair this repo's data split and promotion semantics. |
| GaP | Typed workflow/skill graph and parameters rehearsed over a bounded task-instance distribution. | Fixed. | Static graph checks, independent checkpoints, parallel simulation, and success/throughput reward. | Add the missing pre-deployment graph-policy rehearsal layer. |
| HASE | Policy weights, task solutions, and whitelisted guidance or evaluation-harness components. | Updated with GRPO. | Rollout-local edits, deterministic phase review, validation, and mismatch/non-regression gating for editable evaluators. | Adopt candidate isolation, phase review, mismatch evidence, and a separately governed open-weight evolution plane. |

GaP keeps model weights fixed. It synthesizes a typed computation graph from a
bounded task specification, rehearses that graph over sampled variations in
simulation, localizes failures to graph nodes, edits graph structure or
parameters, stops at a plateau, and ships an immutable optimized graph to an
edge interpreter. It does **not** co-evolve model weights, edit evaluator code,
or use HASE's local-evaluator/oracle mismatch set. The video's dismissal of
GaP as merely “skills” is editorial and is rejected: typed skills, structural
validation, and specialized generation roles are central to the method and are
supported by the paper's ablations.

HASE really does co-evolve Qwen3-8B policy weights and selected harness
components, but more narrowly than the speaker implies. Edits are rollout-local
until deterministic phase-boundary filtering and validation; editable files are
whitelisted; and evaluator repair is enabled only for suitable tasks with an
immutable external evaluator and recorded proxy/oracle mismatches. In the alpha
mining experiment the financial evaluator stays fixed and the editable surface
is only `prompt.txt` plus `pool_viewer.py`. Its 8B result matches Meta-Harness's
GPT-OSS-120B text-classification result and exceeds the *reported* GPT-OSS-120B
alpha-mining baseline on selected metrics; it is not evidence that an 8B model
is universally more capable than a 120B model.

Meta-Harness remains the foundation for rich filesystem-backed candidate search
with a fixed target model. GaP adds typed graph construction and rehearsal over
sampled variations. HASE adds rollout-local guidance edits, phase candidate
pools, and—only when a genuinely trusted external validity signal exists—a
mismatch-driven evaluator-repair experiment. These are complementary layers,
not one interchangeable algorithm.

Applying GaP's pattern to software-agent harnesses is an architectural
inference, not a result demonstrated by the robotics paper. Likewise, HASE is a
four-domain research prototype rather than an exhaustive scaling or production
safety study; its own limitations call out whitelisted edit surfaces,
interface-valid phase commits, and the need for stronger static/dynamic tests.

### Primary-source reconciliation for continual harness evolution and evaluation

The follow-up arXiv review checked the optimization loop and the evaluation
loop separately. This is necessary because an evolver can make a plausible
edit without the solver loading it, following it, benefiting from it, retaining
older capability, or transferring the gain to the next task distribution.

| Primary paper | Disposition | Contribution retained here | Boundary or correction |
| --- | --- | --- | --- |
| [Agentic Harness Engineering (AHE)](https://arxiv.org/abs/2604.25850) | `ACCEPT-WITH-MOD` | File-level editable components, layered trace evidence, a prediction manifest for every edit, next-round attribution, and granular rollback. Its component ablations also justify treating tools, middleware, and long-term memory—not only prompt prose—as first-class optimization surfaces. | AHE reports useful fix prediction but weak regression prediction and describes incomplete self-modification governance. Self-attribution is evidence, never promotion authority. |
| [Self-Harness](https://arxiv.org/abs/2606.09498) | `ACCEPT-WITH-MOD` | Verifier-grounded weakness mining, diverse minimal proposals, and held-in plus held-out non-regression checks with a fixed model and evaluator. | A split repeatedly consulted by an automatic acceptance gate is validation, not a sealed final holdout. Preserve this plan's separate one-time holdout and human promotion. |
| [SEAGym](https://arxiv.org/abs/2606.17546) | `ACCEPT` | A stateful evaluation environment with sequential train batches, frozen update-validation, held-out ID/OOD transfer, replay/retention, cost records, and saved snapshots. It directly tests whether frequent updates transfer and whether a useful intermediate snapshot later collapses. | Use executable outcomes as primary authority; LLM diagnostics remain secondary. Never assume the latest snapshot is the best. |
| [SEA-Eval](https://arxiv.org/abs/2604.08988) | `ACCEPT-WITH-MOD` | Sequential task streams and joint tracking of success and token consumption expose pseudo-evolution hidden by one terminal score. | Token convergence is one signal, not a sufficient definition of safe evolution; combine it with transfer, replay, regressions, and protected evaluators. |
| [Harness Updating Is Not Harness Benefit](https://arxiv.org/abs/2605.30621) | `ACCEPT` | Split evaluation into harness-update quality, artifact activation/load, trajectory-long adherence, and realized solver benefit. It also suggests spending premium-model budget on the solver only when a stronger evolver shows measured value. | Do not infer usefulness from the quality of the generated artifact alone. Model/harness compatibility and operating point remain part of the evaluated subject. |
| [Retrospective Harness Optimization (RHO)](https://arxiv.org/abs/2606.05922) | `ACCEPT-WITH-MOD` | Label-free candidate discovery from a diverse coreset of difficult historical trajectories, resettable group replay, self-consistency diagnosis, parallel proposals, and conservative self-preference ranking. | Self-preference is noisy, cannot cover irreversible tasks, and can distill adversarial trajectory content. It may prioritize proposals but cannot authorize durable promotion. |
| [Test-Time Harness Evolution (TTHE)](https://arxiv.org/abs/2607.08124) | `ACCEPT-WITH-MOD` | A population of persistent harness branches, separated solver/proposer/judge roles, and execution-derived proxy signals for unlabeled test-time adaptation. | Same-batch adaptation and scoring are transductive and do not establish forward generalization; learned proxy judges have selection regret. Require prequential next-batch and protected snapshot evaluation before persistence or promotion. |
| [Adaptive Auto-Harness](https://arxiv.org/abs/2606.01770) | `ACCEPT-WITH-MOD` | A persistent evolution workspace plus a harness tree with regime-specific branches and solve-time routing for heterogeneous, shifting task streams. | Its evolution/adaptation losses are analytical framing rather than directly observed oracle quantities. Branches still require independent validation, retention checks, and bounded routing. |
| [LifelongAgentBench](https://arxiv.org/abs/2505.11942) | `ACCEPT-WITH-MOD` | Interdependent sequential tasks with automatic verification test whether skills and memory accumulate across tasks. Its replay results reinforce selective retrieval rather than indiscriminate history injection. | It is an evaluation pattern, not evidence that one memory or harness implementation generalizes to all domains. |
| [SIA](https://arxiv.org/abs/2605.27276) and HASE | `ACCEPT-WITH-MOD` for open-weight models | They show harness and model-weight updates can be complementary in selected domains. | Add a separate weight-evolution plane with immutable bases, versioned adapters/checkpoints, governed training data and rewards, controlled attribution, forgetting/safety/ID/OOD evaluation, compute stops, and independent promotion. Hosted closed-weight models simply disable this plane. |

These papers establish a two-plane design. The **evolution plane** mines
evidence, generates minimal or branched harness candidates, and may use
label-free proxies to allocate search compute. The **evaluation plane** owns
sequential task sources, immutable executable evaluators, frozen snapshots,
next-batch scoring, ID/OOD transfer, replay, efficiency, and promotion evidence.
The evolution plane cannot edit or overrule the evaluation plane.

### Primary-source reconciliation for open-ended multi-agent discovery and RSI

The additional `self-learning-ai-swarm-intelligence-new-code-rsi` video is a
useful synthesis, but its proposed combination of several systems is not itself
an evaluated architecture. The primary
[SwarmResearch paper](https://arxiv.org/abs/2607.02807),
[CORAL paper](https://arxiv.org/abs/2604.01658),
[EvoX paper](https://arxiv.org/abs/2602.23413), and
[SIA paper](https://arxiv.org/abs/2605.27276), together with Lilian Weng's
[Harness Engineering for Self-Improvement](https://lilianweng.github.io/posts/2026-07-04-harness/)
and the official [SwarmResearch](https://github.com/SwarmResearch/SwarmResearch),
[CORAL](https://github.com/Human-Agent-Society/CORAL),
[SkyDiscover](https://github.com/skydiscover-ai/skydiscover) (the reference
framework that exposes EvoX), and
[SIA](https://github.com/hexo-ai/sia) repositories, override transcript
captions and commentary wherever they differ.

| Source | Disposition | Contribution retained here | Boundary or correction |
| --- | --- | --- | --- |
| Weng's harness review | `ACCEPT` as synthesis | Filesystem-backed durable artifacts, explicit subagents/background jobs, context lifecycle, diversity preservation, evaluator isolation, and joint H/W research as a future direction. | It is a literature synthesis, not evidence that one integrated RSI system is safe or effective. Its warnings about fuzzy evaluators, negative-result loss, diversity collapse, reward hacking, long-term outcomes, and human oversight remain requirements. |
| SwarmResearch | `ACCEPT-WITH-MOD` | Build native parent-lineage selection, explorer versus optimizer role contexts, minimal strategic briefings, and adaptive width/depth allocation into the discovery scheduler. Fresh-context explorers and lineage-aware optimizers preserve alternative candidate states in isolated Git branches. | The main comparison uses one costly run per technique/task; larger diffs are only a rough exploration diagnostic; the observed shepherd remains near-greedy, rarely merges, and struggles with strategic experiment design. Require repeated protected outcomes, behavioral/structural diversity, baseline reseeding, and explicit merge evaluation. The source is named **SwarmResearch**, not SwarmSearch; its repository is mainly a set of skills and currently exposes no explicit license, so do not copy its skill text or code until licensing is confirmed. |
| CORAL | `ACCEPT-WITH-MOD` | Use its retrieve/propose/evaluate/update loop as the native discovery substrate: asynchronous isolated agents, an attempts/notes/skills knowledge hub, lifecycle recovery, protected evaluation access, and typed reflection/consolidation/redirection heartbeats. | Worktrees are candidate code lineage, not the memory architecture. Shared agent-written memory is untrusted and may itself cause idea convergence. Heartbeat output may propose memory, skills, or pivots but cannot activate durable knowledge, alter protected evaluators, promote candidates, or expand authority. CORAL's newer multi-island implementation is promising anti-convergence evidence, but it postdates the paper experiments and must be evaluated separately. |
| EvoX / SkyDiscover | `ACCEPT-WITH-MOD` for native meta-control; `DEFER` activation | Add a two-level native loop: evolve candidate solutions under one frozen search policy, then use population descriptors and stagnation evidence to propose a new parent/inspiration/variation policy. Preserve the population across policy changes and retain strategy performance history. | Search-policy evolution is an `H` change, not a fourth learning plane. SkyDiscover hot-swaps an LLM-generated Python `ProgramDatabase`; Meta-Harness instead uses a bounded declarative policy DSL with static validation, shadow deployment, and exact fallback. Do not activate meta-optimization until the first-order candidate loop is safe and longitudinally useful. |
| SIA | `ACCEPT-WITH-MOD` for the Phase 9 research lane | The Feedback-Agent demonstrates that scaffold and open-weight changes can occupy complementary search spaces in three selected domains. The transcript's “Hazer/Haas” identification is treated as an inference to SIA, not a source name. | SIA optimizes H and W against the same verifier and explicitly identifies coupled co-evolutionary Goodhart risk. Do not attach training to every explorer branch; use separate frozen H and W epochs or an explicit factorial interaction experiment with protected evaluation. |

These are not three peer dependencies. The native design composes their best
mechanisms at different layers:

| Native Meta-Harness component | Mechanism adopted | Source and modification |
| --- | --- | --- |
| `DiscoveryKernel` interfaces | Separate population sampling, context construction, candidate generation, evaluation, and append/checkpoint operations so search policies can change without rewriting generation or evaluation. | SkyDiscover's modular `sample -> prompt -> generate -> evaluate -> add` loop, bound to Meta-Harness context, runner, sensor, evaluator, and journal contracts. |
| `CampaignSupervisor` | Long-running asynchronous workers, health monitoring, bounded restart/resume, evaluation timeouts, graceful stop, and durable terminal state. | CORAL lifecycle machinery, with Meta-Harness journals and budgets as the source of truth. |
| `LineageWorkspaceManager` | One isolated worktree/commit per candidate lineage; branch from baseline or a prior candidate; preserve losing-but-distinct directions. | CORAL isolation plus SwarmResearch branching. A worktree stores candidate state, not conversation or durable memory. |
| `DiscoveryKnowledgeHub` | Append-only attempts, evidence-linked notes, candidate skills, synthesis, open questions, contradictions, and progressive disclosure. | CORAL attempts/notes/skills, converted into typed scoped memory candidates with provenance, trust, lifecycle, and usage receipts. |
| `AnalysisWarmStart` | Before solution mutation, diverse read-only scouts profile the baseline, test assumptions, map bottlenecks, and preserve verified findings for later roles. | SwarmResearch's speculative-decoding analysis phase plus CORAL's current warm-start support; outputs are evidence and candidate hypotheses, not correctness or mandatory ideas. |
| `AutonomousWorkerProtocol` | A long-running worker may choose what scoped evidence to retrieve, which local tests to run, when a candidate is ready for proxy evaluation, and which findings to externalize. | CORAL agent autonomy, bounded by attempt/evaluation/tool budgets, required receipts, evaluator isolation, and terminal conditions. |
| `RoleContextPolicy` | Fresh explorer conversation, direct-lineage optimizer history, and compact global scheduler context. | SwarmResearch context tiers, expanded so each role can retrieve explicitly scoped episodic, semantic, and procedural memory without ambient all-branch access. |
| `PopulationScheduler` | Choose parent, role, variation class, minimal briefing, width/depth, concurrency, budget, baseline reseeding, and stop/pivot actions. | SwarmResearch shepherd, constrained by diversity floors and receipts because the paper's shepherd tends toward greedy collapse and over-prescriptive prompts. |
| `HeartbeatEngine` | Event/evaluation/time/plateau-triggered reflection, consolidation, and redirection with noise thresholds and cooldowns. | CORAL heartbeats, changed from prompt side effects into policy-owned checkpoints whose outputs are observations or candidates. |
| `SearchPolicyEvolver` | Assess a policy over a window, summarize population state, retain strategy history, mutate parent/inspiration/variation rules on stagnation, validate, deploy, and fall back without resetting candidates. | EvoX two-level evolution, represented as a safe declarative policy rather than generated executable Python. |
| `ProtectedEvaluationBroker` | Agents submit immutable candidates and receive bounded score/feedback while the grader, sealed cases, and promotion authority remain inaccessible. | CORAL evaluator separation strengthened by Meta-Harness's proxy/trusted-evaluator split, per-case evidence, and human promotion. |

The resulting native loop is: **observe population -> compose role-scoped
context -> schedule parent/role/operator/budget -> run in an isolated lineage ->
proxy-evaluate and append evidence -> trigger reflection/consolidation/pivot ->
optionally propose a new frozen search policy -> stop -> run protected frozen
assessment -> park a pending candidate**. External CORAL or SkyDiscover runs are
useful later as conformance and benchmark comparisons, not as the architecture
or system of record.

The plan therefore adopts a **bounded open-ended discovery campaign**, not an
unrestricted swarm. Git commits/worktrees preserve immutable candidate code and
parent lineage. The context orchestrator separately composes typed context from
goal/instructions, live state, assigned lineage artifacts, scoped episodic,
semantic, and procedural memory, selected cross-lineage findings, tools,
policies, and evaluator receipts. “Fresh explorer context” means no inherited
conversation anchoring; it does not mean erasing relevant governed memory.
Optimizers receive parent-lineage history plus scoped memory, while the
scheduler receives compact population evidence rather than every raw branch
history. Every included and deliberately withheld source is recorded in the
`ContextManifest` so memory's effect on exploration, convergence, activation,
adherence, and realized benefit can be evaluated.

Branch combination is a new multi-parent candidate with a conflict-resolution
receipt, never an automatic promotion or evidence that two partial ideas compose
correctly. Lines changed, branch count, and agent count are resource or search
diagnostics; quality, novelty, diversity, and progress require executable and
domain-specific evidence under frozen evaluation.

### Primary-source reconciliation for AutoMem and trainable metamemory

The `open-32b-w-automemory-beats-opus` title overstates the generality of the
result. The primary [AutoMem paper](https://arxiv.org/abs/2607.01224),
[project page](https://autolearnmem.github.io/), and
[official repository](https://github.com/autoLearnMem/AutoMem) support a more
useful and narrower conclusion: memory management can be separated into a
versioned scaffold and a trainable model skill, and this separation produced
large gains for Qwen2.5-32B-Instruct on three procedurally generated games.

| AutoMem element | Disposition | Native Meta-Harness use | Required correction or boundary |
| --- | --- | --- | --- |
| File operations as first-class memory actions | `ACCEPT-WITH-MOD` | Expose typed search, read, create, append, upsert, revise, link, and compress operations through a governed `MemoryActionBroker`; retain every selection and mutation as a `MemoryActionReceipt`. | A raw filesystem path is not an authorization boundary. Scope, lifecycle, sensitivity, record provenance, write validation, and protected-history rules remain deterministic and external to the model. |
| Outer loop 1: scaffold optimization | `ACCEPT-WITH-MOD` as `H` | Review full trajectories and propose bounded changes to memory phases, prompts, schemas, validators, action vocabulary, context budgets, and role routing as immutable `MemoryCognitiveSkillSnapshot` children. | AutoMem repeatedly gates revisions on the same fixed evaluation seeds and reports those seeds as its final comparison. Meta-Harness uses development traces for diagnosis, separate validation for selection, next-batch/ID/OOD/replay views for eligibility, and a one-time sealed holdout; the reviewer cannot approve its own change. |
| Outer loop 2: memory-proficiency training | `ACCEPT-WITH-MOD` as `W` | Under one frozen scaffold and evaluator, select authority-qualified memory-operation examples from governed traces, deterministically strip task-action targets, train isolated LoRA/checkpoint candidates for a memory-specialist role, and retain the exact data/config/weight lineage. | Successful task return alone is not a trustworthy label for every earlier memory decision. Include negative and counterexample evidence, blind holdback, redaction/license checks, and protected evaluation; the meta-LLM's selection is a proposal, not ground truth. |
| Two-model inference | `ACCEPT-WITH-MOD` | An optional memory specialist performs post-observation LOG/MAINTAIN and pre-action CONSULT operations; a separately bound frozen task model commits the domain action. Both go through the same scoped context and memory broker, and a deterministic scaffold-only fallback remains packageable. | The specialist cannot emit task actions, mutate active policy/evaluators, widen memory scope, rewrite source evidence, promote records, or delete protected history. Sharing a base model does not collapse the two role/weight identities. |
| Reported 2–4x gain and frontier comparison | `NEEDS-EVIDENCE` outside the reported tasks | Use the result to justify a dedicated memory-skill ablation, not a global model-ranking claim. Compare no external memory, deterministic scaffold, scaffold-optimized `H`, and scaffold-plus-specialist `H+W` under equal budgets. | The paper studies Crafter, MiniHack, and NetHack; starts memory fresh each episode; optimizes a separate scaffold and specialist per environment; and reports only a v1 preprint. It does not show persistent cross-episode memory, cross-domain transfer, or general superiority over Claude Opus. The added `W` lift is smaller than the preceding scaffold lift. |

The video's Maxwell-demon analogy is retained only as a design lens: a memory
cognitive skill selectively reduces *context load* by deciding what evidence to
encode, consult, revise, compress, or omit. It is not a thermodynamic claim and
does not authorize silent forgetting. Source evidence, conflicts, tombstones,
retention constraints, and every lossy transformation remain independently
auditable and reversible where the underlying policy permits it.

This becomes a native capability at two levels. Meta-Harness may use a
`MemoryCognitiveSkill` for its own context and knowledge management, and it may
generate, evaluate, and package a goal-specific instance for each managed
harness. `SolutionTemplate.cognitive_skills[]` can retain the reusable skill:
its `H` scaffold, typed operation protocol, schemas and validators, context and
memory requirements, sensors and evaluator obligations, compatibility tests,
and either an optional compatible `W` snapshot reference or a governed
retraining recipe. It never contains unscoped user memories. An actual adapter
is reusable only with an explicitly compatible base checkpoint, tokenizer,
chat/tool protocol, domain, license, and protected transfer result; otherwise
the template instantiates the scaffold and retrains a new child `W` candidate.

The official repository is useful as an implementation reference for its
LOG/PLAN split, deterministic task-action filtering, per-run ledger, and
two-model deployment, but its GitHub metadata currently reports no license.
Meta-Harness may reproduce the paper's interfaces and evaluate a pinned
external checkout, but must not copy or redistribute repository code or
scaffolds until an explicit compatible license is present and reviewed.

### Source-to-plan traceability

| Distilled material | Repository application | Plan location |
| --- | --- | --- |
| Harness engineering: instructions and repository rules | Treat stable instructions as a protected context section with a source hash, not concatenated free text. | Phases 0-1 |
| Harness engineering: context delivery | Carry explicit workflow inputs, prior outputs, and artifact references into the envelope. | Phase 1 |
| Harness engineering: context management | Select, rank, budget, compress, and record omissions rather than merely truncating a message list. | Phases 1 and 3 |
| Harness engineering: tools and execution environment | Preserve current tool/sandbox controls; include only allowed tool schemas and bind their policy version to attempts. | Core contracts and Phase 1 |
| Harness engineering: durable state | Keep canonical journals and persisted workflow state as evidence beneath working/episodic memory. | Phase 2 |
| Harness engineering: orchestration and subagents | Keep task control deterministic and give each worker a narrow envelope; do not share an unbounded conversation. | Target architecture and Phase 1 |
| Harness engineering: skills | Represent reusable procedures as governed procedural memory and preserve progressive disclosure. | Phases 2-3 |
| Harness engineering: verification and observability | Require receipts, persist learning evidence, and use OTel as telemetry rather than the audit database. | Phases 4, 7, and 10 |
| Harness engineering: failure ratchet | Route repeated misses to the layer that failed: retrieval rule, schema, permission, regression case, memory, or procedure. | Phases 7-8 |
| Memory masterclass: working memory | Derive the current bounded working set from run/step/attempt state. | Phases 1-2 |
| Memory masterclass: episodic memory | Store immutable, timestamped, verified events with exact run evidence and compression lifecycle. | Phases 2-3 |
| Memory masterclass: semantic memory | Store scoped, versioned facts with validity, supersession, contradiction, and source strength. | Phases 2-3 |
| Memory masterclass: procedural memory | Migrate the playbook and later skills/workflows into versioned, reviewed procedures. | Phases 2-7 |
| Memory masterclass: context builder | Put scoped retrieval and assembly between durable stores and the model. | Target architecture and Phase 3 |
| Memory masterclass: forgetting and hygiene | Add decay, pinning, expiry, compression, conflict review, tombstones, and retention. | Phase 3 |
| Build from scratch: stateless model and message history | Keep adapters stateless; the harness reconstructs only the selected history/state needed now. | Target architecture and Phase 1 |
| Build from scratch: `@`-style context delivery | Prefer immutable artifact references and just-in-time fetch over copying large artifacts into every prompt. | Phases 1 and 3 |
| Build from scratch: bounded tool loop and approvals | Preserve existing bounded runner/tool behavior and record tool/approval receipts in attempt evidence. | Phases 1 and 7 |
| Build from scratch: head-and-tail compaction | Retain both ends of oversized observations and expose the full artifact by reference. | Phase 1 |
| Build from scratch: JSONL sessions and episodic search | Reuse canonical JSONL journals as event sources; add a typed indexed memory store instead of searching raw logs as the final design. | Phase 2 |
| Build from scratch: deterministic planning | Keep the workflow engine as the control plane; models propose structured data within it. | Target architecture |
| Build from scratch: isolated subagent context | Assemble role/task-specific envelopes with scoped tools and memory for each worker. | Phase 1 |
| Build from scratch: receipts and traces | Persist verifier receipts and versioned attempt evidence, then render it in the UI. | Phases 4, 7, and 10 |
| Build from scratch: UI as event consumer | Add context, memory, evaluation, and improvement inspectors without moving control policy into the UI. | Phase 10 |
| Self-improvement: frozen target model | Search harness surfaces while binding every result to one exact target model/adapter. | Version contract and Phase 8 |
| Self-improvement: real traces and weakness mining | Mine repeated verified failure evidence and diagnose its harness layer before proposing a fix. | Phase 7 |
| Self-improvement: editable-surface ladder | Search retrieval/config first, then prompts, workflow, and finally bounded code. | Phase 8 |
| Self-improvement: miner tasks and regression guards | Separate discovery cases from reviewed permanent guards. | Phase 7 |
| Self-improvement: protected evaluator | Isolate candidate workspaces and fingerprint evaluator code, fixtures, answers, manifests, and promotion state. | Phase 8 |
| Self-improvement: held-in versus held-out evaluation | Split development, validation, and one-time sealed holdout; rotate after exposure. | Phase 8 |
| Self-improvement: repeated trials and per-task safety | Retain per-case pass rates and block promotion on any case regression. | Phase 8 |
| Self-improvement: inspectable PR and human decision | Park every passing candidate for authenticated review with exact rollback; never auto-promote. | Phase 8 |
| Adaptive engineering: complicated versus complex | Keep fixed deterministic workflows as the default; permit adaptation only when observable non-stationarity or interdependence justifies it. | Phase 6 |
| Adaptive engineering: runtime reorganization | Adapt a versioned topology of approved roles, edges, sequencing, tools, and memory flow at explicit checkpoints. | Phase 6 |
| Adaptive engineering: horizontal intelligence | Evaluate coordination and information flow across agents separately from improving one agent's memory or prompt. | Phases 6-7 |
| Adaptive engineering: engineer designs constraints | Bound topology size, capability catalog, permissions, budgets, required verifiers, change rate, and stop conditions. | Phase 6 |
| Adaptive engineering: probe, sense, respond | Feed verified environment observations into adaptation decisions and record every decision as evidence. | Phases 6-7 |
| Adaptive engineering: selection pressure | Score candidate topologies on verified progress, safety, cost, latency, and diversity rather than agent self-assessment. | Phase 6 |
| Adaptive engineering: attractors, drift, and oscillation | Add hysteresis, cooldowns, topology-change budgets, stagnation detection, and safe fallback. | Phase 6 |
| Adaptive engineering: monoculture and legibility loss | Require capability diversity where useful and preserve topology snapshots, decision receipts, and causal replay. | Phases 6 and 10 |
| Model evaluation: preserve the item-response matrix | Store every verified model/harness/item/repetition response and exact version tuple; never retain only a total score. | Phase 4 |
| Model evaluation: item difficulty and discrimination | Estimate which items are hard, informative, flat/noisy, or negatively discriminating and route suspicious items to review. | Phase 4 |
| Model evaluation: ability with uncertainty | Report capability-scoped estimates and confidence/credible intervals; do not rank overlapping estimates as certain. | Phase 4 |
| Model evaluation: benchmark auditing | Use item fit and negative discrimination to propose mislabeled, stale, dependent, or ambiguous cases for human review, never auto-delete them. | Phase 4 |
| Model evaluation: adaptive/private testing | Select informative anchor plus private fingerprint items under exposure quotas for repeated calibration; keep mandatory promotion guards exhaustive. | Phases 4 and 8 |
| Model evaluation: residual anomaly and contamination detection | Track unexpected successes/failures by subject and item, compare with clean controls, and treat leakage signals as investigation evidence rather than proof. | Phase 4 |
| Model evaluation: differential item behavior | Compare item curves across provider/model/harness families to identify capability-specific or harness-specific behavior hidden by one score. | Phase 4 |
| Model evaluation: error-profile correlation | Use residual complementarity to inform robust routing or ensembles, while labeling shared-base/distillation conclusions as inference. | Phases 4 and 6 |
| Model evaluation: multidimensionality | Calibrate by task/capability domain first; defer a universal single-theta model until dimensionality and local-independence assumptions are tested. | Phase 4 |
| GaP: bounded task distribution | Represent the expected software input/config/environment variations explicitly; do not optimize against one happy-path fixture. | Phase 5 |
| GaP: graph as policy | Extend the existing workflow DAG into immutable typed node ports plus data/control edges and postcondition checkpoints. | Rehearsal contracts and Phase 5 |
| GaP: orchestration and skill agents | Let an orchestrator partition a candidate graph while bounded skill agents receive only their subgraph, approved procedure, tools, and canonical scripts. | Phases 1 and 5 |
| GaP: generation/test separation | Use a separate checkpoint author and protected verifier; a graph generator cannot define whether its own candidate succeeded. | Phase 5 |
| GaP: simulation rehearsal | Run candidate graphs across sampled variations in isolated parallel fixtures, preserve seeds and pre/post node evidence, and optimize before deployment. | Phase 5 |
| GaP: localized graph updates | Permit typed node swaps, edge changes, and bounded parameters only when a verified failure is localized; stop on plateau or oscillation. | Phase 5 |
| GaP: edge execution | Promote an immutable optimized graph to the deterministic interpreter; synthesis agents are absent from ordinary repeated execution. | Phases 5 and 8 |
| GaP: multi-objective reward | Compare success and safety first, then cost and latency/throughput; retain the complete Pareto evidence. | Phases 5 and 8 |
| HASE: rollout-local guidance edits | Reuse isolated candidate workspaces and a phase-local pool; only parseable, interface-valid candidates that improve validation may persist. | Phases 5 and 8 |
| HASE: proxy/external mismatch set | Preserve proxy-versus-trusted-evaluator disagreements as regression evidence, but keep the production evaluator protected; evaluator repair is a separate experimental safety case. | Phases 4, 7, and 8 |
| HASE: guidance versus evaluation harness | Allow bounded prompt, memory-format, retrieval, and context-policy candidates; govern correctness-defining evaluator changes under a materially stricter policy. | Phase 8 |
| HASE/SIA: model-weight co-evolution | Add a separate open-weight evolution plane using immutable base checkpoints plus versioned deltas/adapters, sensor-grounded training manifests, controlled attribution, and protected checkpoint promotion. | Phase 9 |
| AutoMem: memory as an active cognitive skill | Separate durable memory contents from the policy that decides what to encode, consult, revise, compress, or omit; expose only typed, scoped, receipted operations through a broker. | Memory contracts and Phases 2-3 |
| AutoMem: scaffold/proficiency axes | Treat prompts, schema, validators, action vocabulary, phases, routing, and budgets as `H`; treat only a compatible open memory-specialist checkpoint/adapter as `W`; keep `E` fixed and external. | Core contracts and Phases 8-9 |
| AutoMem: trajectory review and memory-only training | Diagnose delayed memory failures from complete traces, but curate authority-qualified examples and deterministically exclude task-action targets before training the specialist. | Phases 7-9 |
| AutoMem: two-model runtime | Let the optional specialist perform post-observation maintenance and pre-action consultation while a separate frozen task model alone commits the domain action; retain a deterministic scaffold-only fallback. | Target architecture and Phases 3, 9, and 10 |
| AutoMem: cognitive-skill reuse | Store the reusable memory scaffold, protocol, schema, sensors, evaluator obligations, compatibility tests, and optional compatible `W` reference or recipe in `SolutionTemplate.cognitive_skills[]`; do not package unscoped memories. | Phases 7 and 10 |
| SwarmResearch: explorer/optimizer lineages | Preserve alternative program states in isolated candidate worktrees; use fresh conversational context for structural exploration and parent history for refinement. | Context contracts and Phase 8 |
| SwarmResearch: scheduler-guided scaling | Make parent selection, explorer/optimizer role, selective context, width/depth allocation, budgets, and stop decisions explicit versioned receipts. | Evolution contracts and Phase 8 |
| CORAL: open-ended discovery framework | Build native asynchronous campaign supervision, isolated candidate worktrees, protected evaluation, health/restart/resume, and optional scoped islands with explicit migration. | Phases 6-8 |
| CORAL: persistent knowledge and heartbeats | Project attempts, notes, skills, syntheses, contradictions, and open questions through governed memory; schedule noise-aware reflection, consolidation, and plateau redirection as policy-owned events whose outputs remain candidates until reviewed. | Phases 2-3 and 7-8 |
| EvoX: meta-evolving search policy | Represent population descriptors, strategy history, parent/inspiration selection, structural-versus-local variation, and stagnation-triggered policy changes as frozen declarative `H` candidates with validation and fallback; do not generate executable controller code. | Evolution contracts and Phase 8 |
| SIA: coupled H/W risk | Preserve complementary H and W search spaces while separating epochs, holding E fixed, and testing joint effects only through declared factorial experiments. | Phases 8-9 |
| AHE: component observability | Expose every editable prompt, tool, middleware, skill, subagent, memory, workflow, and policy surface as a versioned file or typed record with a narrow rollback unit. | Core contracts and Phase 8 |
| AHE: experience and decision observability | Distill raw traces into drill-down evidence; require each edit to name failure evidence, cause, predicted fixes, regression risks, and the next-round verdict. | Phases 7-10 |
| SEAGym: longitudinal snapshot assessment | Evaluate frozen snapshots after sequential updates on update-validation, the next unseen batch, ID/OOD transfer, replay/retention, and cost; retain best-so-far history because later updates can collapse. | Phases 4 and 8 |
| SEA-Eval: evolutionary trajectory | Report success and token/tool/cost trajectories across the task stream rather than only final pass rate. | Phases 4 and 10 |
| Updating versus benefit | Measure update quality, artifact activation, adherence, and realized benefit as separate response variables. | Evaluation contracts and Phase 4 |
| RHO: label-free retrospective proposals | Use diverse historical coresets, resettable group replay, self-consistency, and self-preference only for candidate generation or prioritization. | Phases 7-8 |
| TTHE: test-time branch population | Permit proxy-driven branch search only in an isolated experimental lane; require prequential next-batch and protected frozen-snapshot evaluation before persistence. | Phases 6 and 8 |
| Adaptive Auto-Harness: harness tree | Preserve validated regime-specific branches and route by evidence instead of destructively forcing heterogeneous tasks into one global harness. | Phases 6 and 8 |
| LifelongAgentBench: interdependent streams | Add sequential tasks that require earlier verified skills or facts while measuring irrelevant-memory and context-budget failures. | Phases 3-4 |

## Current Repository Audit

| Finding | Disposition | Evidence | Required change |
| --- | --- | --- | --- |
| Token budgeting and middle-message compression exist. | `ACCEPT-WITH-MOD` | `src/metaharness/context.py` estimates tokens, preserves edge messages, and digests large middle messages. | Retain it as a compatibility utility, but put a typed context assembler in front of every model call. |
| Local model calls start from a fresh two-message prompt. | `NEEDS-EVIDENCE` for cross-step continuity | `src/metaharness/harness/local.py` constructs a system message plus the current task and only retains tool messages within that call. | Supply a context envelope from the control plane; do not make individual runner adapters own memory. |
| Workflow state is durable and explicit references can be resolved. | `ACCEPT-WITH-MOD` | `src/metaharness/workflows/engine.py` persists `RunState.context`, completed outputs, and canonical journal events. | Keep journals as canonical run evidence; derive working memory and episodic candidates from them rather than calling the journal itself AI memory. |
| Coding-agent adapters are intentionally ephemeral. | `ACCEPT` | `src/metaharness/harness/coding.py` disables CLI session persistence. | Preserve ephemeral workers. The Meta-Harness, not a vendor CLI session, owns continuity. |
| The playbook is a useful procedural-memory seed. | `ACCEPT-WITH-MOD` | `src/metaharness/correction/playbook.py` stores scored task-type bullets with helpful and harmful counts. | Migrate bullets into versioned procedural records with provenance, scope, validity, supersession, and review state. |
| Failure learning is too coarse to support safe improvement. | `REJECT` as the sole learning mechanism | `src/metaharness/correction/learning.py` clusters only by task type and MAST label and can generate static guidance after a count threshold. | Add evidence-backed causal clusters and distinguish context, retrieval, procedure, tool, workflow, sandbox, and model failures. |
| OTel traces are useful but not a durable learning corpus. | `ACCEPT-WITH-MOD` | `src/metaharness/observability/tracing.py` keeps a bounded in-memory span store unless an external collector is configured. | Persist redacted attempt, context-manifest, tool, verification, and version references needed for replay and mining. |
| Exact evaluation artifacts preserve useful per-item evidence. | `ACCEPT` | `src/metaharness/evals/models.py` versions development/validation/holdout cases; `evals/artifacts.py` stores each case and repetition with verdict and cost/latency/token metrics; holdout projections stay sealed. | Keep these immutable reports as the response-matrix source of truth and extend them by reference rather than replacing them with psychometric summaries. |
| Deterministic output verification exists, but there is no instrumented domain-artifact evaluator stack. | `ACCEPT-WITH-MOD` | `src/metaharness/evals/verifiers.py` correctly makes execution/schema/exact-value checks outrank opinions and returns `UNVERIFIED` when no checkable signal exists. Repository search finds no domain sensor manifest, equation AST/domain/assumption and proof stack, repository-analogue/patch-effect instrumentation, governed evaluator-update loop, or transferable solution-pattern contract. | Preserve the verifier hierarchy and `UNVERIFIED` semantics; add typed domain sensors, protected layered evaluators, and separately governed evaluator candidates rather than stretching `equals`/`contains` or an LLM judge into universal verification. |
| Evaluation is suite-oriented rather than evolution-oriented. | `ACCEPT-WITH-MOD` | Current reports compare candidates within an evaluation run, but there is no sequential batch schedule, frozen post-update snapshot assessment, next-batch protocol, ID/OOD transfer view, replay/forgetting metric, best-snapshot selector, or artifact activation/adherence record. | Add a longitudinal evolution-evaluation layer over the immutable case evidence; do not replace or weaken existing per-case gates. |
| Evaluation gates collapse item evidence into pass^k, task-type, and overall aggregates. | `ACCEPT-WITH-MOD` | `src/metaharness/evals/gate.py` retains per-task passes while its gate reasons and comparisons use pass-all, broad task types, wins/losses, and overall pass^k. | Add calibrated item/capability profiles, uncertainty, item-quality review, and residual analysis as advisory evidence; retain exhaustive per-case non-regression for promotion. |
| Routing capability evidence is too coarse for calibrated model selection. | `ACCEPT-WITH-MOD` | `src/metaharness/routing/router.py` stores pass/total only by `(model, task_type)` and applies Laplace smoothing toward a fixed prior. | Preserve item IDs, harness/context/tool versions, repetitions, costs, and residual/error profiles; route on capability estimates plus uncertainty and complementary failure patterns. |
| Full IRT is not trustworthy without enough heterogeneous observations and fit checks. | `NEEDS-EVIDENCE` | The repository has no calibrated item bank, anchor design, convergence diagnostics, dimensionality/local-independence checks, or clean-control population. | Start with response-matrix capture and descriptive item audits, then introduce a vetted 1PL/2PL calibration backend through an ADR. Keep estimates advisory until sample, fit, and stability thresholds are met. |
| The optimizer already freezes the target and searches knobs/code. | `ACCEPT-WITH-MOD` | `src/metaharness/optimization/loop.py`, `params.py`, and `code_gate.py` provide a proposer, candidate ledger, bounded parameter schema, code hash, path checks, interface checks, and answer-string decontamination. | Preserve those foundations but harden dataset separation, evaluator integrity, candidate isolation, and promotion. |
| The code-proposer path captures Meta-Harness's richest feedback pattern, but it is not the default tuning path. | `ACCEPT-WITH-MOD` | `optimization/code_proposer.py` gives a coding agent filesystem access to all candidate code, scores, and raw traces. However, CLI default `--proposer rule` uses fixed heuristics; `LLMProposer` prompt-packs only capped failure rows; the built-in mixed suite has only 18 search and 6 holdout items; and the default search is six rounds. The primary Meta-Harness experiments typically evaluated about 60 harnesses over 20 iterations and let the coding proposer selectively inspect the full artifact history. | Keep `CodeProposer` as the full-history search option, stop describing rule/LLM modes as paper-faithful equivalents, and measure candidate-history access, task-distribution coverage, and search budget explicitly. |
| Current harness tuning evaluates candidate wrappers; it does not perform GaP-style simulation rehearsal. | `NEEDS-EVIDENCE` for rehearsal | `optimization/loop.py` runs one candidate configuration on a fixed task list and stores attempt-level traces. `workflows/dsl.py` has a static DAG, but there is no versioned variation distribution, typed data/control port validator, checkpoint-author role, parallel sampled rehearsal, node-local pre/post state, localized graph mutation, or graph-to-edge export. | Retain the optimizer as a candidate-search backend, but add a separate graph-policy rehearsal control plane rather than relabeling fixed-suite tuning as simulation. |
| Blueprint tuning is a safe proposal bridge, not a learning or rehearsal loop. | `ACCEPT` for its stated scope | `evals/tuning.py` binds immutable blueprint/eval/report references and applies only narrow human-approved patches to a draft. It neither generates candidates nor executes variation trials. | Reuse its immutable proposal, provenance, and human-gated draft semantics at the rehearsal/promotion boundary. |
| Current optimizer promotion and holdout use do not meet the corpus safety bar. | `REJECT` for unattended production use | The optimizer defaults `auto_promote=True`; CLI optimization can write `promoted.json`; search has only search/holdout splits; final contenders are compared on the same holdout; `compare_suites` allows bounded aggregate/type regression. | Remove automatic promotion, add development/validation/sealed holdout roles, select on validation, enforce per-case non-regression, rotate exposed holdouts, and require human approval. |
| Harvested production tasks are not yet a trustworthy evaluation dataset. | `NEEDS-EVIDENCE` | `src/metaharness/optimization/harvest.py` converts journal tasks directly into alternating search/holdout extras. | Redact, deduplicate, cluster by cause, classify miner versus regression-guard candidates, and require review before any task enters a versioned evaluation suite. |
| Workflow topology is fixed before execution. | `ACCEPT` as the default; `ACCEPT-WITH-MOD` for complex domains | `src/metaharness/workflows/dsl.py` validates a static step DAG and `workflows/engine.py` executes its topological order. This is the right reliability model for predictable work. | Add an opt-in bounded-adaptive controller that can select or revise an approved runtime topology only at journaled checkpoints. |
| The workflow/blueprint model is the right graph-policy seed but lacks graph-level contracts. | `ACCEPT-WITH-MOD` | `StepSpec` already declares inputs, an output schema, dependencies, tools, capabilities, success checks, and approvals; `WorkflowSpec` validates IDs, references, and acyclicity; blueprints version the workflow immutably. Inputs are still free-form mappings and references rather than typed input/output ports and validated data/control edges, and checkpoints are embedded in the generating step's success check. | Add a backward-compatible typed graph view, explicit edge/port schemas, independently authored postcondition checkpoints, and graph validation receipts before allowing graph search. |
| HASE-style evaluator repair demonstrates a real second learning loop, but the repository has no scheduled evaluator-evolution control plane. | `ACCEPT-WITH-MOD` | The HASE paper whitelists editable components, keeps rollout edits local, requires proxy/external divergence before evaluator edits, checks mismatch and non-regression cases, and commits at phase boundaries. The current repo has only fixed verifier implementations and no sensor sampler, evaluator lineage, scheduled evolution run, shadow backtest, mutation score, or evaluator promotion state. | Keep the evaluator snapshot immutable during one assessment, but evolve later versions through a separate scheduled `EvaluatorEvolver` that samples sensor evidence, generates candidates, backtests them against anchors/mismatches/replay/adversarial mutations, and promotes only independently validated versions. The solver proposer cannot edit its active yardstick. |
| The repository has no active memory-cognitive-skill or memory-specialist plane. | `NEEDS-EVIDENCE` | Repository search finds no `MemoryCognitiveSkillSnapshot`, typed memory-action broker/receipt, LOG/CONSULT phase contract, memory-only training manifest, specialist role, or cognitive-skill template. Existing context compaction, workflow state, and playbook learning are useful inputs but do not train or safely execute a memory-management policy. | Build the typed store and deterministic broker/scaffold baseline first; then evaluate bounded `H` scaffold candidates and only later a separately attributed open `W_mem` specialist. Package the verified skill through `SolutionTemplate` and release contracts without claiming it already exists. |
| Unrestricted emergent orchestration is not yet production-safe. | `DEFER` | The adaptive-engineering source explicitly identifies loss of legibility and predictability, local attractors, drift without selection pressure, and monoculture. The repository has no runtime topology policy or corresponding eval suite. | Implement bounded adaptation first. Do not allow agents to invent capabilities, permissions, evaluator rules, or arbitrary code during a run. |

The principal gap is therefore not “add a larger prompt.” The missing control
plane is the context builder that decides what enters a model call, records why,
and connects durable state to typed memory. The principal self-improvement gap
is not “add another proposer.” It is evaluation and promotion isolation. The
newly confirmed rehearsal gap sits between those systems: the repo can score
candidate wrappers, but it cannot declare a task-variation distribution,
synthesize and statically validate a typed graph, rehearse it across sampled
variants, localize graph-node failures, and export a verified immutable graph.
The adaptive-engineering gap is a further timescale: the fixed workflow engine
has no governed way to revise roles, relationships, sequencing, or information
flow in response to a changing environment during the run. The longitudinal
evaluation gap cuts across all three timescales: the repository cannot yet
prove that an update transfers to the next batch, retains older capability,
survives ID/OOD transfer, is actually activated and followed by the solver, or
beats the best earlier frozen snapshot at an acceptable cost.
The AutoMem gap is not merely another database or retrieval backend: the repo
does not yet expose memory decisions as typed observable actions, optimize their
scaffold as `H`, train a memory-only `W` role, or package the resulting cognitive
skill with a protected task-role boundary and fallback.

### Harness-tuning disposition

The existing tuning subsystem is **not discarded**, but “self-optimization” is
too broad a label for what it currently proves.

- `ACCEPT`: immutable candidate ledger, raw attempt traces, Pareto bookkeeping,
  fixed target-model binding, `CodeProposer` filesystem access, code hashing,
  interface/path checks, and human-gated blueprint draft proposals.
- `ACCEPT-WITH-MOD`: reuse `HarnessOptimizer` behind the new rehearsal and
  protected-evaluation contracts; expose the actual proposer mode, history
  access, task coverage, variation coverage, and search budget in every report.
- `REJECT`: CLI auto-promotion, selection on repeatedly inspected holdout data,
  aggregate/type-level regression tolerance, direct harvested-task admission,
  and claims that a tiny fixed suite demonstrates general harness improvement.
- `NEEDS-EVIDENCE`: graph topology synthesis, simulation/fixture rehearsal,
  localized credit assignment, robustness across task variations, and
  transfer from rehearsal to live execution.

This makes the old optimizer one search engine within the harness—not the
self-learning harness itself.

## Target Architecture

```text
UI / API / Task + Variation Specification
           |
           +--> Graph Generator (orchestrator + bounded skill agents)
           |          |
           |          v
           |    Static graph/type/checkpoint validation
           |          |
           |          v
           |    Parallel isolated rehearsal over sampled variants
           |          |
           |          v
           |    Node-local evidence -> localized graph/parameter update
           |          |
           |          v
           |    validation -> sealed holdout -> human promotion
           |          |
           |          v
           +---- immutable optimized Workflow Graph
                              |
                              v
               Deterministic Workflow Interpreter
                              |
                              v
 Runtime Mode Gate: fixed (default) or bounded-adaptive
                              |
                              v
 Adaptive Runtime Controller -------- Constraint Envelope
           |                           + approved capabilities
           v
 Versioned Harness Topology ---------- Topology/decision receipts
           |
           v
    Context Orchestrator ---------------- Context Manifest + hashes
      |       |       |       |                    |
      |       |       |       |                    v
   current  working  episodic semantic/procedural  Durable attempt evidence
    task     state    memory       memory                    |
      |       |       |       |                             v
      +-------+-------+-------+----> bounded Context Envelope per role
                                          +
                            MemoryCognitiveSkillSnapshot
                                          +
                    task and optional memory-specialist WeightSnapshots
                                          +
                              instrumented agent program
                                          |
                                          v
                       Ephemeral memory-specialist / task runners
                                          |
                                          v
                         domain artifact/decision sensors
                                          |
                  structural + behavioral + domain evaluators
                                          |
                   counterexample + protected verification
                                          |
                                tool + evaluation receipts
                                          |
                              Item-response/evidence ledger
                                          |
            +-----------------------------+---------------------------+
            |                             |                           |
            v                             v                           v
   Memory candidate pipeline     Evaluation Intelligence      Weakness mining
   redact -> extract -> review   item bank + calibration      cluster -> diagnose
            |                    uncertainty + residuals              |
            v                             |                           v
   versioned memory store                 +--> Router        isolated candidate workspace
                                          |                           |
                                  adaptive item selection     proposal/rehearsal signals
                                                                      |
                                                sequential train batches -> frozen snapshots
                                                                      |
                                              update-validation -> next-batch prequential
                                                                      |
                                                   ID/OOD transfer -> replay/retention
                                                                      |
                                            best eligible snapshot -> sealed holdout
                                                                      |
                                                            human approve -> promote/rollback
```

A governed `MemoryActionBroker` sits on the context/memory boundary even when
the runtime uses only the deterministic scaffold. It validates and receipts
every typed read or mutation against scope, lifecycle, sensitivity, retention,
and policy. When a compatible learned specialist is enabled, it performs only
the memory-maintenance and consultation phases through this broker; a separate
task-role binding alone may commit the domain action. This same runtime can be
used by Meta-Harness internally or compiled into a managed harness release.

The candidate-generation path contains a native `DiscoveryKernel`, not a
framework-shaped sidecar. It composes the campaign supervisor, lineage
workspaces, knowledge hub, role context policy, population scheduler,
heartbeat engine, declarative search-policy evolver, and protected evaluation
broker. Optional external framework adapters sit outside this kernel and exist
only to reproduce or compare reference behavior.

Seventeen boundaries are non-negotiable:

1. Runners remain stateless execution adapters. The orchestrator owns context
   and memory policy.
2. Raw worker output and retrieved text are untrusted evidence, never executable
   instruction or durable semantic/procedural memory by default.
3. The evaluator, sealed holdout, and promotion pointer live outside the
   proposer/fixer's editable workspace.
4. Fixed deterministic execution remains the default. Bounded adaptation is an
   explicit run mode selected by policy, not a universal replacement for DAGs.
5. A generated graph is never active merely because it passed rehearsal. Static
   validation, protected validation/holdout evaluation, and human promotion
   precede activation; the runtime interpreter receives an immutable graph.
6. Simulation and proxy scores are evidence about declared fixtures, not an
   oracle or proof of real-world correctness. Fidelity limits and sim-to-live
   gaps remain visible.
7. Runtime adaptation can rearrange only approved capabilities inside a hard
   constraint envelope. It cannot mint permissions, edit evaluator state,
   promote durable memory, or change production harness code.
8. Psychometric summaries are derived evidence, not ground truth. Raw verified
   item responses stay immutable, calibration assumptions and uncertainty stay
   visible, and required safety guards are never skipped by adaptive sampling.
9. Label-free self-preference, consistency, execution-health, or LLM-judge
   scores may guide candidate generation and compute allocation, but cannot
   promote a persistent harness or redefine correctness.
10. Every durable update is assessed as a frozen snapshot on future and replay
    views. The newest snapshot never displaces the best validated snapshot by
    recency alone.
11. Agent synthesis may be stochastic, but every candidate program is frozen as
    immutable code/config/graph before evaluation. Promoted execution is
    deterministic for the same recorded inputs, environment, tool versions,
    and seeds; nondeterministic dependencies require repeated trials and are
    labeled explicitly.
12. Deterministic execution proves reproducibility, not correctness. Generated
    patches, equations, plans, classifications, and transformations must pass
    independent domain-appropriate evaluators. An evaluator may learn only
    through a separate protected proposal and promotion loop; it cannot approve
    its own definition of success.
13. A verified solution may become a scoped reusable pattern, but never a
    universal patch or instruction. Every new goal/harness must satisfy the
    pattern's preconditions and independently re-evaluate the instantiated
    solution; negative transfer is retained as first-class evidence.
14. Open-weight evolution never mutates the active checkpoint in place. Every
    base, adapter, delta, optimizer state, training-data/reward manifest, and
    recipe is immutable and versioned. Weight effects are attributed with the
    harness and evaluator held fixed before joint co-evolution claims are made.
15. A Git commit or worktree is an immutable candidate-state and lineage source,
    not the complete agent-memory architecture. The context orchestrator may
    combine it with scoped working, episodic, semantic, and procedural memory,
    selected population findings, and live evidence; source visibility and
    omissions are role-specific and receipt-backed.
16. The native open-ended discovery kernel remains a bounded experimental
    campaign. Its scheduler, search-policy evolver, knowledge view, workers,
    islands, migrations, and heartbeats cannot change protected evaluators,
    activate memory, merge/promote candidates, expand permissions, or deploy a
    result without the ordinary external gates.
17. A learned memory specialist is a scoped actuator, not memory authority or a
    second task solver. It cannot commit domain actions, activate records,
    overwrite source evidence, widen visibility, delete protected history,
    redefine evaluation, or promote itself. The deterministic broker enforces
    actions and receipts; scaffold-only fallback and exact `H`/`W` rollback
    remain available.

## The Self-Learning and Adaptive Harness

The first version of this plan described the learning behavior across several
phases but did not name it as one subsystem. It should be explicit. The
`SelfLearningHarness` is the control plane that connects four different
learning timescales:

1. **Before-run rehearsal (graph-policy learning):** generate a typed workflow
   graph, sample task/environment variations, execute candidates in an isolated
   simulator or fixture world, localize verified failures to nodes, and propose
   bounded graph/parameter updates until a plateau. Only an immutable promoted
   graph reaches the deterministic runtime interpreter.
2. **Within-run adaptation (horizontal learning):** observe changing task and
   environment state, decide whether the current topology remains suitable,
   and at bounded checkpoints propose a new arrangement of approved roles,
   relationships, sequencing, tools, and memory flow. The change is validated
   against the constraint envelope, journaled, and reversible to the last stable
   topology.
3. **Across-run learning (vertical and harness learning):** mine repeated,
   verified item/outcome evidence plus calibrated residuals; diagnose whether
   the failure belongs to context, memory, procedure, coordination, tools,
   sandbox, workflow, verifier, evaluation item, or model; then propose a
   reviewed memory, procedure, evaluation, configuration, or code change. For
   open-ended goal families, the native discovery kernel may asynchronously
   preserve candidate lineages, exchange scoped knowledge, vary explorer and
   optimizer depth/width, and adapt a declarative search policy over frozen
   windows. Candidate discovery may use label-free retrospective or proxy
   evidence, but durable promotion uses protected longitudinal evaluation and
   a human gate.
4. **Scheduled open-weight learning (model learning):** when a model exposes
   trainable weights, sample governed sensor/evaluator evidence, create
   training and reward manifests, train several immutable
   adapter/delta/checkpoint candidates, and compare them while holding harness
   and evaluator snapshots fixed. Promote only a protected best checkpoint and
   stop on goal, plateau, regression, forgetting, safety, compute, or wall-time
   limits.

Trainable memory management crosses the third and fourth timescales without
collapsing them. The memory scaffold, phases, prompts, schemas, validators,
action vocabulary, broker policy, and role routing are `H`; an open-weight
memory-specialist adapter/checkpoint is `W`; the authority that judges memory
accuracy and task benefit is frozen `E`. A scaffold-only candidate must be
measured before a specialist is trained, and the task-action model remains
frozen in a memory-specialist `W` epoch.

An orthogonal **evolution-evaluation plane** measures every frozen snapshot
across sequential update-validation, the next unseen batch, held-out ID/OOD
transfer, replay/retention, artifact activation/adherence, regressions, and
efficiency. It preserves the best eligible historical snapshot and can stop or
roll back an evolution campaign when later updates collapse.

For any generative goal, each candidate harness is an instrumented executable
program specialized to a goal family. Sensors capture the domain artifacts,
inputs, assumptions, dependencies, tool calls, intermediate decisions, state
changes, and verification obligations at the point they are produced.
Independent evaluators return typed failure evidence. The evolver may use that
evidence to revise prompts, retrieval, memory, tools, topology, or regenerate
the agent program. Equation/proof evaluation is one domain profile; software
ticket triage and repair is another.

```text
specify task distribution -> generate typed graph -> validate structure
        -> rehearse sampled variations -> localize failures -> update candidate
        -> validate/holdout/human promote immutable graph
        -> classify runtime mode -> sense verified state
        -> adapt approved runtime topology at a checkpoint
        -> synthesize/freeze instrumented agent code
        -> execute with role-specific context
        -> broker scoped LOG/MAINTAIN and CONSULT memory actions
        -> task-role model alone commits the domain action
        -> sense domain artifacts + assumptions + intermediate decisions
        -> independently evaluate -> localize failure
        -> preserve topology + context + item-response evidence
        -> update versioned evaluation profiles and uncertainty
        -> mine repeated weaknesses across runs
        -> retrieve compatible verified solution patterns as candidate priors
        -> launch bounded native discovery campaign when open-ended search is justified
        -> schedule isolated explorer/optimizer lineages with scoped knowledge
        -> heartbeat reflect/consolidate/redirect and detect population stagnation
        -> optionally shadow a validated declarative search-policy child
        -> propose durable change -> frozen snapshot
        -> freeze evaluator E_t for comparable harness assessment
        -> scheduled evaluator-evolution run samples sensor evidence
        -> generate/backtest E_t+1 candidates against protected meta-evidence
        -> promote immutable E_t+1 and re-evaluate eligible harness snapshots
        -> if open weights enabled, sample training evidence under frozen H_t/E_t
        -> for memory proficiency, strip task-action targets and train W_mem only
        -> train/evaluate W_t+1 candidates with paired/factorial attribution
        -> update-validation -> next-batch -> ID/OOD -> replay/retention
        -> compare with best eligible historical snapshot
        -> protected sealed-holdout confirmation
        -> human promote or reject -> exact rollback available
```

Runtime adaptation has three policy modes:

- `fixed`: immutable workflow topology; default for normal software tasks;
- `bounded_adaptive`: controller may change an approved topology at explicit
  checkpoints within budgets, permissions, invariants, and change-rate limits;
- `experimental_emergent`: reserved schema value only; disabled until bounded
  adaptation has convincing safety, stability, and value evidence.

This separation prevents a common category error: changing which approved
agents coordinate during one run is not the same as learning a durable rule or
rewriting the harness for future runs. Nor is scoring a wrapper on a small fixed
suite equivalent to rehearsal over an explicit variation model.

The coordinating implementation belongs in
`src/metaharness/learning/harness.py`. It consumes immutable journal/evidence
events and delegates storage, runtime topology decisions, weakness diagnosis,
candidate evaluation, and promotion to their bounded subsystems. It does not
call a model with unrestricted tools or act as a second workflow engine.

## Core Contracts

### Context contracts

`ContextSourceRef` makes every possible context origin explicit. Its source kind
is one of protected instructions/goal, live run state, immutable artifact,
candidate worktree or parent lineage, working/episodic/semantic/procedural
memory, selected population finding, tool/policy schema, or evaluator receipt.
It records scope, trust, content hash or high-water mark, retrieval/selection
reason, sensitivity, and fetch capability. Git state and durable agent memory
are distinct source kinds even when both are represented by files.

`ContextEnvelope` is the versioned input to a runner. It contains ordered,
typed sections rather than an anonymous message list:

- stable system role and repository instructions;
- task objective, inputs, boundaries, and required output schema;
- the minimum tool schemas allowed for this task;
- current workflow/attempt state;
- relevant prior step outputs, immutable artifacts, or assigned candidate and
  parent-lineage references;
- retrieved episodic, semantic, and procedural memories;
- selected cross-lineage findings or negative-result summaries when the role's
  visibility policy permits them;
- verifier feedback from the immediately preceding attempt; and
- a final response contract.

Each `ContextSection` carries:

- section type and stable ID;
- source reference and source hash;
- trust class (`instruction`, `verified_fact`, `untrusted_evidence`, or
  `generated_summary`);
- original, selected, and compressed token counts;
- budget allocation and ordering priority;
- sensitivity/redaction markers; and
- compression or omission reason.

`ContextManifest` is the durable receipt. It records policy version, model and
harness versions, memory snapshot/version, section hashes, retrieval scores,
source candidates considered, role-specific visibility decisions, deliberate
omissions, budget use, compression actions, and fetchable artifact references.
It must be possible to reconstruct the same redacted envelope from the same
immutable inputs.

Population-search roles use separate context-view policies. An explorer starts
without inherited conversation history but may retrieve scoped governed memory
and receives its baseline/worktree plus a minimal non-prescriptive brief. An
optimizer receives its direct parent lineage and detailed attempt history plus
scoped memory. A scheduler receives compact scores, approach/structure
fingerprints, uncertainty, budget, health, and selected summaries across the
population, not every raw branch transcript. Cross-lineage transfer is an
explicit `ContextSourceRef`; shared-memory visibility is never ambient.

### Rehearsal and graph-policy contracts

`TypedWorkflowGraph` is a backward-compatible, immutable graph view over a
blueprint workflow. It contains typed skill/step nodes, named input and output
ports, data and control edges, postcondition checkpoints, capability/tool
references, bounded recovery or loop edges, parent graph ID, and content hash.
The existing `WorkflowSpec` can be wrapped as graph schema v1 without changing
execution; inferred ports remain shadow-only until explicitly reviewed.

`TaskDistribution` / `RehearsalSpec` declares what the candidate is expected to
handle:

- immutable task objective, environment/fixture image, tool and sandbox policy;
- variation dimensions and distributions for inputs, repository states,
  configurations, failures, concurrency, and external responses;
- deterministic seed schedule, sample count, parallelism, and fidelity limits;
- required checkpoints, safety invariants, and terminal conditions;
- reward vector ordered by correctness/safety, then cost and
  latency/throughput; and
- development, validation, and sealed-holdout variation manifests.

`RehearsalObservation` records one node execution within one sampled variation:
graph/node and variant IDs, pre-state reference, resolved inputs, outputs,
post-state reference, checkpoint/verifier receipt, tool effects, failure class,
timing, cost, seed, and simulator/fixture version. Large state remains in
immutable artifacts and is referenced by digest.

`GraphCandidate` / `GraphUpdate` records an isolated candidate, its parent,
localized failure evidence, allowlisted operation (typed node substitution,
edge edit, or bounded parameter change), static-validation receipts,
multi-objective results, plateau/oscillation state, and promotion status.
Generation, checkpoint authorship, execution, and final verification use
separate roles and trust boundaries. The graph generator cannot redefine its
own success criterion.

### Open-ended discovery and candidate-search contracts

`SearchPolicySnapshot` is an immutable `H` artifact for one bounded discovery
campaign. It versions parent and inspiration selection, explorer/optimizer role
allocation, lineage and memory visibility, local-versus-structural variation,
width/depth and concurrency limits, branch-combination rules, scheduler prompt
constraints, health checks, budget allocation, stagnation/diversity triggers,
and stop conditions. The snapshot is frozen while its downstream progress is
assessed; a search-policy proposer cannot score, approve, or activate itself.

`SearchPopulationSnapshot` records every candidate node and parent edge,
worktree/commit and generated-code hashes, model/context/memory/evaluator
versions, approach and structural/behavioral fingerprints, current eligibility,
resource use, and best-protected rather than merely latest state. A worktree is
an ephemeral checkout of this lineage; the commit and receipts are durable.

`SearchDecisionReceipt` records why the scheduler selected a parent, role,
context view, cross-lineage finding, variation operator, width/depth allocation,
or stop/pivot action; alternatives considered; the frozen population descriptor
and policy; budget impact; and subsequent protected outcome. Reflection,
consolidation, and redirection triggers are policy-owned decision events. Their
agent output remains untrusted evidence or a memory/skill candidate until the
normal review and promotion workflow accepts it.

`DiscoveryCampaignManifest` binds one native campaign to its goal/task
distribution, initial candidates, exact H/E/W tuple, model portfolio, proxy and
protected evaluator references, context-source policy, memory and knowledge
scopes, frozen search policy, agent/concurrency and evaluation budgets,
workspace/tool/network permissions, seed and repetition schedule, heartbeat
policy, optional analysis warm-start, health/restart rules, stop conditions, and
terminal export. The native kernel keeps `PopulationStore`, `ContextBuilder`,
`CandidateGenerator`, `EvaluationBroker`, and `DiscoveryController` as narrow
interfaces; a search-policy change can alter sampling and guidance without
replacing generation, evidence capture, or evaluation. The
`CampaignSupervisor` owns the asynchronous state machine: prepare -> optionally
analyze -> schedule -> run -> proxy-evaluate -> append -> checkpoint ->
reschedule or stop. Worker crashes, timeouts, restarts, resumptions,
cancellations, and partial terminal states are journaled; no worker session is
the source of truth.

`AnalysisWarmStart` is an optional, separately budgeted phase in which diverse
read-only scouts profile the baseline, reproduce measurements, identify
bottlenecks and assumptions, search repository or approved external evidence,
and propose falsifiable directions without modifying solution candidates. Its
observations enter the knowledge hub with verifier/tool receipts and scope.
Later explorers may retrieve them, but the context manifest records whether
they did; unverified hypotheses cannot become mandatory instructions.

`AutonomousWorkerProtocol` gives a long-running worker bounded discretion over
which allowed local evidence to retrieve, which tests/profilers to run, when to
submit an immutable attempt to the proxy evaluator, and which notes or skill
candidates to externalize. The supervisor does not reduce the worker to a
one-shot mutation operator, but it retains hard tool, time, token, evaluation,
workspace, and stop envelopes and requires a receipt or explicit omission for
every terminal path.

`DiscoveryKnowledgeHub` is the typed native form of CORAL's shared filesystem.
It exposes append-only evaluated attempts, evidence-linked notes, candidate
skills/procedures, syntheses, cross-topic connections, contradictions, and open
questions through progressive disclosure. Every artifact declares creator,
source attempts, candidate lineage, scope (`private`, `lineage`, `island`,
`campaign`, or reviewed `project`), confidence/trust, validity, supersession,
and use receipts. Notes and skills written by agents remain untrusted memory
candidates. They cannot become active procedural or semantic memory merely by
being placed in a shared directory.

`RoleContextPolicy` constructs different source views without conflating
freshness with amnesia. An explorer gets a fresh conversation, selected parent
worktree, goal and constraints, scoped memory, and a compact novelty briefing
that names explored regions without prescribing an answer. An optimizer gets
the direct parent's worktree, relevant conversation/failure lineage, and scoped
memory for serial refinement. The population scheduler gets summaries,
fingerprints, resource state, and evaluation receipts across eligible lineages,
but not every raw conversation. Islands may receive different seeds, role
priors, memories, or inspiration pools; cross-island migration is an explicit
candidate/context event, not ambient shared state.

`PopulationDescriptor` extends EvoX's score/frontier/window statistics with
approach and behavioral diversity, parent-selection concentration, lineage
depth/width, score-tier and Pareto coverage, steps since meaningful improvement,
variation-operator yield, cross-agent code/knowledge transfer, memory-use
concentration, evaluator failures, cost, latency, and remaining budget. It is a
compact, immutable scheduler input and is never a replacement for per-candidate
evidence.

`SearchPolicyDSL` is the only meta-evolvable control surface. It declaratively
chooses among allowlisted parent selectors (baseline, elite, diverse,
underexplored, uncertain, score-tier, or Pareto), inspiration selectors,
explorer/optimizer roles, local/structural/counterexample/compositional variation
classes, minimal-briefing templates, width/depth/concurrency allocation,
diversity floors, baseline reseeding, memory/island visibility, and stop/pivot
rules. It cannot contain executable code, arbitrary imports, tool calls,
evaluator logic, permissions, or pointer mutations. A `SearchPolicyEvolver`
scores frozen policy windows, retains population-state-before/after and observed
progress in strategy history, proposes a bounded policy child on stagnation,
then schema-validates, simulates, shadows, and either activates it for the next
declared window or restores its parent without resetting the candidate
population.

`HeartbeatAction` defines a policy-owned reflection, consolidation, or
redirection checkpoint with local/global scope, evaluation/event/time/plateau
trigger, task-specific improvement epsilon, cooldown, prompt/context template,
resource cost, and protected status. Reflection writes an observation;
consolidation proposes typed memory/skill/synthesis records; redirection proposes
a scheduler action or policy change. Agents may propose custom actions or
cadence changes, but cannot delete protected capture, bypass cooldown/budget
checks, directly activate their outputs, or interrupt a worker without a
durable safe checkpoint and resume receipt.

`DiscoveryInteropAdapter` is secondary: it can run a pinned external CORAL or
SkyDiscover/EvoX implementation as a benchmark or conformance oracle and
translate its artifacts into the same ledger. SwarmResearch may be reproduced
from the paper's architecture or invoked as a separately installed process, but
its repository text/code is not copied while its license is unspecified. No
external runner may become the system of record or read sealed items, mutate
active state, alter protected evaluators or sensor-completeness rules, activate
memory, expand permissions, self-approve, or move promotion/deployment pointers.

Native mechanisms and optional external comparisons use one declared task
distribution, model portfolio, evaluation snapshot, context-source policy,
seed/repetition schedule, and resource envelope. Worktree-only,
scoped-role-memory, shared-memory, isolated-island, fixed width/depth,
scheduler-guided, and search-policy-evolving conditions remain separate
ablations before composition. Diff size, branch count, and self-reported novelty
cannot substitute for protected behavioral, domain, longitudinal, diversity,
cost, and safety outcomes.

Combining branches creates a new multi-parent candidate with source commits,
selected changes, conflicts, resolver identity, generated/test receipts, and
rollback parents. A clean Git merge is syntactic evidence only; the combined
candidate receives the same static, behavioral, domain, longitudinal, and
promotion evaluation as any other child.

### Instrumented self-adjusting harness contracts

`GoalFamilyProfile` fingerprints the reusable problem family before a harness is
assembled. It records domain, objective and acceptance schema, repository or
environment structure, artifact types, required capabilities, available
verifiers, risk class, similarity features, and transfer boundaries. Two goals
may share a solution pattern only when their declared preconditions and verifier
contracts are compatible.

`AgentProgramCandidate` is the executable learned artifact for one goal-family
profile. It binds immutable source code, typed graph, prompts,
context/retrieval/memory policy, tool adapters, sensor manifest, evaluator
references, build environment, dependency lock, parent candidate, and content
hash. A generator may create several candidates, but evaluation runs only
built, frozen candidates in isolated workspaces. The generator is absent from
ordinary promoted execution.

Code generation occurs on three explicit planes:

1. **Harness construction:** generate the agent roles, workflow implementation,
   prompts/configuration, tool adapters, instrumentation, domain adapters,
   tests/fixtures, and packaging glue needed for a new `GoalFamilyProfile`.
2. **Goal execution:** allow the generated harness to produce domain artifacts
   such as source patches, migrations, analyses, equations, or plans; these are
   outputs to domain evaluators, not automatically trusted harness code.
3. **Harness repair:** use localized sensor/evaluator failures to regenerate
   only the smallest justified harness source surface as a child candidate.

`GeneratedCodeReceipt` binds each generated file and candidate bundle to the
goal/program parent, generator role and `ModelPortfolioSnapshot`, applicable
open `WeightSnapshot`, prompt/context/memory
manifest, retrieved templates or solution patterns, tool calls, source and
dependency hashes, editable-surface policy, random seeds, build/static/type/test
receipts, rejected alternatives, and terminal reason. Generated code cannot
edit its active evaluator, sensor-completeness rules, permissions, secrets,
promotion state, or deployment pointer. It becomes executable only after
isolation, dependency locking, static validation, protected evaluation, and an
immutable candidate build.

`HarnessReleaseBundle` is the deployable product of an eligible generated-code
candidate. It contains the immutable program and graph, dependency lock and
build/container identity, prompts and policies, tool/capability manifest,
sensor/evaluator versions, exact `ModelPortfolioSnapshot` and its applicable
open `WeightSnapshot` members,
memory/context bootstrap policy, every required `MemoryCognitiveSkillSnapshot`,
its broker/action schemas and scaffold-only fallback, configuration schema,
SBOM/provenance, deployment health checks, target adapter, release signature,
parent release, and rollback instructions. A learned memory skill additionally
binds the compatible specialist weight, task-role separation, training recipe
or reproducibility manifest, and evaluation/rollback evidence. Deployment
produces a `DeploymentReceipt` binding the release hash to the target,
configuration, approvals, health/sensor observations, active pointer, and
rollback result; packaging never implies automatic production authority.

`SensorManifest` declares the domain-neutral observations emitted by each agent
node and tool boundary:

- goal, task, environment, repository, agent-program, context, memory, tool,
  evaluator, and parent-artifact identities;
- input/output artifact types, pre/post state, decisions, alternatives,
  assumptions, dependencies, provenance, exceptions, resource use, and terminal
  reason;
- retrieval queries, candidates considered, similarity evidence, selected
  analogues, rejected analogues, and whether retrieved information influenced
  the generated artifact;
- verification obligations, evaluator receipts, unresolved conditions, and
  counterexample or regression attempts; and
- domain-specific extensions declared by the `GoalFamilyProfile`.

For mathematical or scientific work, the domain extension captures:

- canonical equation/expression AST plus original rendering;
- declared variables, types, units, domains, quantifiers, and assumptions;
- derivation parentage and the transformation or theorem claimed at each step;
- cited source, retrieved memory, tool, prompt, model, and agent-program hashes;
- exact versus approximate arithmetic, precision, tolerances, and random seed;
- proof obligations, unresolved side conditions, and counterexample attempts;
  and
- pre/post state, exceptions, resource use, and terminal reason.

Sensors are observational and append-only. They cannot mark their own output
correct, change the evaluator, inject instructions into another agent, or omit
a mandatory proof obligation without producing a failed completeness receipt.

`ArtifactEvaluation` is the common evaluator envelope. It binds sensor inputs,
artifact and candidate hashes, evaluator and dependency versions, authority
class (`proxy`, `trusted`, or `meta`), checks executed, receipts, coverage,
unresolved conditions, and verdict. Domain profiles contribute evaluator stacks
whose receipts remain separate rather than collapsing into one model score.

The equation/proof profile applies:

1. parse, type, scope, domain, unit, and assumption consistency;
2. exact normalization or symbolic equivalence under explicitly recorded
   assumptions, with side conditions preserved;
3. numerical/property tests and adversarial counterexample search across a
   declared distribution, recorded only as falsification or supporting
   evidence—not proof;
4. dependency and derivation checks that every claimed step follows from
   accepted parents or creates an explicit unresolved obligation; and
5. formal proof-kernel or independently owned domain-verifier acceptance when
   the goal requires a correctness claim.

The software-ticket profile applies:

1. ticket classification, reproduction evidence, affected subsystem, ownership,
   dependency, security, and risk analysis;
2. repository code graph, symbol/call/reference search, blame and commit history,
   prior issues/patches, tests, documentation, and runtime/CI evidence;
3. analogue retrieval that records why an earlier issue or patch is structurally
   similar, which preconditions transfer, and which differences invalidate it;
4. immutable generated patch plus static analysis, type/lint/build checks,
   targeted and regression tests, coverage deltas, runtime traces, security
   checks, and diff/behavior inspection; and
5. repository-specific acceptance criteria and protected CI or human review for
   changes whose correctness cannot be established automatically.

`AgentRegenerationDecision` joins sensor failures to the smallest justified
editable surface: prompt/context policy, memory/retrieval, tool, evaluator
adapter wiring, graph/topology, bounded parameter, or agent source code. It
records the localized evidence, proposed change, expected repaired obligations,
regression risks, new program hash, and replay plan. Unknown causality produces
`NEEDS-EVIDENCE`; it does not authorize a broad rewrite.

`EvaluatorSnapshot` is an immutable, content-addressed evaluator version used
for one comparable assessment epoch. It binds checks, tests, probes, rubrics,
thresholds, sampling/calibration policy, tool adapters, stable anchor/kernel
dependencies, coverage declaration, parent snapshot, and promotion state. It is
not static forever; it is frozen only while producing evidence whose scores
must remain comparable.

`EvaluatorEvolutionSchedule` makes evaluator learning a first-class recurring
operation. It declares cadence and drift triggers, sensor sampling policy,
sample size and strata, candidate budget, immutable anchor/reference/replay and
adversarial-mutation corpora, blind assessment split, stop conditions, and
promotion policy. Scheduled runs may also start early when sensors show novel
failure clusters, evaluator disagreement, coverage gaps, calibration drift,
false-positive/false-negative evidence, or solution/evaluator mismatch.

`EvaluatorSampleManifest` records exactly which sensor-derived observations
entered one evolution run. Sampling is stratified across passes, failures,
uncertain/unverified cases, disagreements, regressions, novel goal families,
rare/high-risk cases, retrieved-pattern successes and negative transfers, and
cost/latency outliers. Deduplication, redaction, scope, provenance, exposure,
selection probability, and holdback status are explicit so frequent easy cases
cannot dominate evaluator evolution.

`EvaluatorCandidate` / `EvaluatorUpdate` governs the resulting learning loop on
a stricter plane than solver/harness learning. Sampled evidence may generate or
revise a check, test, probe, counterexample generator, rubric, threshold,
calibration model, coverage rule, or adapter. Candidates run in shadow mode and
are evaluated on anchor retention, historical mismatch correction, held-back
sensor samples, replay, adversarial mutation detection, calibration, coverage,
false-positive/false-negative rates, cost, and stability relative to the parent.
They cannot alter their own meta-evaluator, reference outcomes, stable kernels,
protected guards, sampling record, or promotion pointer. Promotion requires
independent evidence and human approval; without an adequate validity source
the result remains `NEEDS-EVIDENCE` rather than redefining correctness.

Harness and evaluator evolution alternate. Harness candidates are compared
under frozen `E_t`; a scheduled evaluator run may produce `E_t+1`; then every
eligible/best harness snapshot affected by the changed coverage is re-evaluated
under `E_t+1` before further promotion. A rank or verdict flip is preserved as
an evaluator-mismatch event and may quarantine both candidates. The system does
not change the solution and its yardstick in the same unversioned transaction.

`SolutionPattern` is the transferable product of repeated verified solutions,
not a copied patch or free-form memory. It records the goal-family fingerprint,
problem and failure signature, structural analogue features, preconditions,
solution mechanism, parameterized code/graph/prompt/tool changes, evaluator
receipts, counterexamples, known non-applicable cases, source repositories,
license/sensitivity policy, confidence, and lifecycle. A similar harness may
retrieve it as a candidate prior, but must instantiate and re-evaluate it in the
new repository/environment. Reuse receives credit only when its ID appears in
the attempt manifest and paired evidence shows benefit.

`SolutionTemplate` is the reusable, executable form distilled from one or more
compatible `SolutionPattern` records. It binds supported goal-family
fingerprints, typed parameters and preconditions, generated-code/graph/prompt/
tool fragments, `cognitive_skills[]`, context and memory requirements, sensor
obligations, evaluator and acceptance contracts, tests/fixtures, packaging
fragments, provenance and license policy, known counterexamples,
negative-transfer history, version lineage, and lifecycle. Cognitive skills
carry behavior, compatibility, and verification contracts—not unscoped memory
contents—and optional weight artifacts remain exact base/model/domain-bound
references. The meta-harness may instantiate a template for many harnesses, but
each instance receives a new immutable program hash and must pass
the new goal's complete rehearsal and evaluation. Template selection or prior
success never grants promotion authority.

### Model portfolio and open-weight model evolution contracts

`ModelPortfolioSnapshot` binds every model participating in one harness release
or assessment. For each role it records provider/runtime, exact hosted model ID
or open `WeightSnapshot`, endpoint/capability contract, tokenizer and chat/tool
format, inference parameters, seed support, context limit, tool permissions,
cost/latency budgets, trainability, routing and fallback rules, health probes,
credential references, and parent portfolio. Its mode is `hybrid`, `open_only`,
or `hosted_only`. Secrets and raw credentials are never stored in the snapshot.

Portfolio optimization is part of `H`: the system may change role assignment,
model selection, routing, fallback order, prompts, context, tools, batching,
sampling/decoding parameters, or budgets as immutable harness candidates.
`W` changes only an open-weight member's actual checkpoint, adapter, or delta.
A first-order comparison changes one portfolio/harness or weight surface while
holding the other versions fixed. Hosted model drift detected by health or
fingerprint probes produces a new portfolio observation/version and invalidates
silent before/after attribution.

`WeightSnapshot` identifies the exact model parameters used by an attempt. It
binds an immutable base checkpoint hash plus zero or more ordered adapter/delta
hashes, tokenizer and chat-template versions, precision/quantization, merge
state, architecture/config, license/source, parent snapshot, and lifecycle
(`experimental`, `eligible`, `best_so_far`, `pending`, `promoted`, `rejected`,
`collapsed`, or `rolled_back`). The active model is never trained in place.

A learned memory specialist is an explicit portfolio role, even when it shares
the task model's base checkpoint. `H` binds the specialist role and its
`MemoryCognitiveSkillSnapshot`; `W_mem` binds only its adapter/checkpoint. The
task-role and memory-role requests, outputs, allowed actions, costs, and health
probes remain separately observable, and a missing or unhealthy specialist
falls back to the frozen scaffold-only route rather than silently changing the
task model.

`TrainingEvidenceManifest` is constructed from sampled sensor and evaluator
records, not raw successful trajectories by default. It records examples,
labels/rewards/preferences, source attempts and weight/harness/evaluator
snapshots, provenance and licensing, redaction, deduplication, contamination and
holdout checks, quality/uncertainty, capability/domain balance, hard negatives,
counterexamples, failure clusters, replay/retention samples, selection
probability, and excluded evidence. Only outcomes grounded by an evaluator with
adequate authority may produce positive training targets.

`WeightEvolutionRun` binds the immutable base/parent, training-evidence
manifest, learning recipe (for example SFT, preference optimization, policy
optimization, or adapter search), trainer code/container, optimizer and
scheduler state, hyperparameters, random seeds, compute/data/privacy budgets,
candidate count, checkpoint cadence, stop policy, and protected assessment
manifests. The framework supplies the contract and pluggable recipes; it does
not pretend one learning algorithm is correct for every goal family.

`WeightAttributionObservation` measures weight effects without conflating them
with a changed harness or evaluator. The minimum comparison holds task,
repository/environment, harness/program/context/memory/tool versions,
`EvaluatorSnapshot`, inference settings, and seed schedule fixed while varying
only `WeightSnapshot`. A controlled factorial campaign may later compare
weight-by-harness interactions. It records paired per-case deltas, activation
and adherence, capability transfer, ID/OOD results, replay/forgetting, safety,
calibration, cost/latency, variance, and interaction effects.

`WeightEvaluationProfile` is a derived, versioned view over the complete
checkpoint-by-item response matrix; it never replaces that matrix. For a
declared item population and capability dimension it may record IRT theta
(checkpoint ability), item difficulty and discrimination, intervals, fit,
person/item residuals, and differential item-functioning results by model,
checkpoint, or harness family. Theta is not treated as one universal measure of
model quality: failed dimensionality, local-independence, sample-diversity,
identifiability, convergence, or fit checks suppress the estimate or require a
multidimensional/stratified profile.

Residuals and item information may prioritize inference-instability probes,
hard-negative collection, evaluator review, or the next calibration batch.
They do not by themselves prove a bad label, leakage, lineage, distillation, or
causality. A shortened or adaptive "tiny benchmark" must retain anchors,
capability coverage, uncertainty and exposure controls, rotate as the candidate
population changes, and remain advisory; mandatory guards and protected
promotion suites still run exhaustively. This avoids optimizing new checkpoints
against a compressed benchmark fitted only to old model families.

Weight selection follows best-over-latest semantics. Several adapters/deltas or
checkpoints may be trained and routed in parallel, but each is immutable and
must pass protected validation, next-batch, ID/OOD, replay/retention,
catastrophic-forgetting, safety, and efficiency gates. A scheduler may continue
until a declared goal threshold is independently achieved, or stop on plateau,
oscillation, regression, reward/evaluator mismatch, data or compute exhaustion,
or wall time. The closest protected checkpoint and unresolved gap remain
visible when the full goal is not achieved.

A `HarnessReleaseBundle` with the `self_contained_evolving` profile carries its
exact `ModelPortfolioSnapshot`, every packaged open `WeightSnapshot`, hosted
model capability/parameter bindings and secret references where applicable,
open-model load/train runtime, governed evidence sampler, learning recipes,
immutable sensors and current evaluator snapshot, scheduled
evaluator-evolution lane, context and memory stores, memory-action broker,
packaged cognitive-skill scaffolds and optional compatible specialist weights,
solution-template resolver, isolated code/update workspace, H/E/W coordinator,
protected assessment manifests, budgets, stop conditions, lineage, and rollback
parent.
It can run bounded improvement campaigns without continuous manual code edits.
Hybrid mode may call declared frontier/hosted dependencies; open-only mode may
run locally or offline when tools and data allow. Neither mode can edit
protected validity evidence, expand its constraint envelope, or move a
production deployment pointer on its own.

Harness (`H`), evaluator (`E`), and weight (`W`) evolution use versioned
alternating epochs. A primary attribution experiment changes one axis while the
other two remain frozen. Joint candidates are allowed only after first-order
effects exist and use a factorial or explicitly labeled interaction design.
Sensor correlation alone is not causal weight attribution.

Determinism is a deployment contract, not an assumption about LLM generation.
Candidate synthesis can be stochastic and explored as a population. After
selection, the code/graph/config is immutable; deterministic tools must replay
bit-for-bit or receipt-for-receipt under the version contract, while stochastic
model/tool calls use fixed sampling settings where supported plus repeated
trials and variance reporting. Reproducibility never substitutes for evaluator
correctness.

### Runtime adaptation contracts

`HarnessTopology` is an immutable, versioned snapshot of the active runtime
organization. It contains approved role instances, capability/tool scopes,
communication and artifact-flow edges, ordering/dependency constraints, memory
visibility, assigned verifier, and parent topology ID.

`ConstraintEnvelope` defines everything runtime adaptation cannot exceed:

- allowed role, model, tool, skill, and workflow-template catalogs;
- maximum agents, fan-out, topology changes, tokens, cost, wall time, and tool
  rounds;
- tenant/project data boundaries and memory-sharing rules;
- immutable sandbox, approval, secret, and evaluator policies;
- required verification and human checkpoints;
- minimum role/capability diversity where the task benefits from it; and
- cooldown, hysteresis, stagnation, rollback, and terminal conditions.

`AdaptationDecision` is the durable receipt for each checkpoint. It records the
verified observations, problem classification, old and proposed topology
hashes, expected benefit, selection score, constraint evaluation, decision,
controller/policy version, and resulting outcome. Model-generated rationales
are untrusted supporting evidence; deterministic policy owns authorization.

The initial controller does not create arbitrary decentralized swarms. It
selects and composes approved topology operations such as add/remove a bounded
specialist, change an allowed dependency edge, request an independent verifier,
reduce communication fan-out, or revert to the last stable snapshot.

### Evaluation intelligence contracts

`EvalObservation` preserves one verified cell in the response matrix: exact
item/suite version, split, repetition, subject identity, model role/member,
`ModelPortfolioSnapshot`, applicable `WeightSnapshot`, harness,
context/memory/topology/tool policy versions, verdict, metrics,
timestamp, and immutable source report reference. A “model” estimate is only
model-specific when the harness and inference conditions are intentionally
fixed; otherwise the evaluated subject is the complete
weight-plus-model-plus-harness tuple.

`ItemBankManifest` versions each item's capability/domain tags and role:

- `anchor`: shared across administrations to maintain a comparable scale;
- `fingerprint`: private, difficult, exposure-limited items for contamination
  investigation;
- `calibration`: informative items eligible for adaptive selection; or
- `mandatory_guard`: exhaustive safety/regression cases that adaptive selection
  may never skip.

It also records source, expected-answer review, sensitivity, exposure count and
limit, organization/run assignment history, retirement reason, and content
digest.

`ItemCalibration` stores the calibration model/version, capability dimension,
difficulty, discrimination, standard error/interval, fit diagnostics, sample
size and population manifest, dependency/local-independence findings, status,
and reviewer decision. Flat, negative, stale, dependent, or anomalous items
become review candidates; the statistics never silently change ground truth.

`SubjectCapabilityEstimate` stores a capability-scoped ability estimate and
interval, fit/person-residual statistics, item coverage, calibration reference,
and cost/latency profile. Overlapping intervals are reported as uncertainty,
not converted into a confident total order.

The rollout begins with immutable observations and descriptive item analysis.
A 1PL/2PL IRT backend requires a separate ADR selecting a vetted numerical
implementation plus convergence, identifiability, sample, fit, and reproducible
reference-fixture tests. Estimates remain advisory until those gates pass.

### Evolution-evaluation contracts

`EvolutionRunManifest` declares a complete stateful campaign rather than one
candidate comparison. It binds the fixed or explicitly editable model
portfolio, seed harness, editable
surface, optimizer/proposer/judge roles, sequential task-source version, batch
schedule, reset/replay policy, update cadence, budgets, evaluator versions,
development/update-validation/ID/OOD/replay/sealed manifests, and stopping and
promotion policy.

`HarnessSnapshot` is an immutable candidate state after an update. It records
its parent, complete file/config/context/memory/workflow hashes, branch or
regime scope, optimizer and evidence versions, change-manifest references,
creation batch, active editable surface, and whether it is experimental,
eligible, best-so-far, pending, promoted, rejected, collapsed, or rolled back.
The active production pointer is separate from the evolution lineage.

`HarnessSnapshotAssessment` evaluates one frozen snapshot without allowing the
optimizer to mutate it during assessment. It preserves exact case observations
and reports these distinct views:

- `update_validation`: immediate reusable gain after the update;
- `prequential_next_batch`: performance on the next unseen batch before that
  batch can influence another update;
- `id_transfer` and `ood_transfer`: held-out same- and shifted-distribution
  generalization;
- `replay_retention`: performance and forgetting against prior task families
  and mandatory guards;
- `efficiency`: token, tool-call, wall-time, monetary-cost, and success-per-cost
  trajectories; and
- `sealed_confirmation`: one-time confirmation for the selected snapshot only.

`HarnessArtifactUse` separates four measurements that must not be collapsed:

1. `update_quality`: whether the proposed artifact is valid, evidence-backed,
   and improves a protected evaluation when mounted correctly;
2. `activation`: whether the solver discovered or loaded the relevant prompt,
   tool, skill, memory, middleware, or branch when it should;
3. `adherence`: whether the solver followed the artifact across the trajectory
   rather than dropping it after initial activation; and
4. `realized_benefit`: the causal paired outcome difference when the artifact
   is available versus withheld or replaced by its parent.

`HarnessChangeManifest` makes an edit falsifiable. Each minimal logical change
names its failure evidence, diagnosed cause, modified files/records, predicted
fixes, at-risk tasks, expected metric direction, activation mechanism, rollback
parent, and next assessment. The following round records fix/regression
precision and recall plus a `keep`, `improve`, or `rollback_and_pivot` verdict.
Attribution may inform future search but never bypass protected gates.

Label-free signals have a typed, subordinate role. A `ProposalSignal` may come
from self-consistency, pairwise self-preference, execution health, public tests,
runtime, or trace diagnosis. It records contamination risk and evaluator
independence. Such signals can select a historical coreset, spawn or rank
branches, and allocate compute; only trusted executable assessment can make a
snapshot eligible for durable promotion.

### Trainable memory-cognitive-skill contracts

`MemoryCognitiveSkillSnapshot` is the immutable policy for actively using a
memory substrate. It records the goal-family and role scope; parent and content
hash; post-observation LOG/MAINTAIN, pre-action CONSULT, and task handoff phase
contracts; allowlisted action vocabulary; record/file schemas and validators;
query, ranking, compression, conflict, retention, and context-budget policy;
redaction and sensitivity rules; prompt/template hashes; required sensors and
evaluators; deterministic fallback; and lifecycle. It also binds the task-role
model plus an optional compatible memory-specialist `WeightSnapshot` without
conflating their identities.

The split across learning planes is explicit:

- `H` owns the memory scaffold: phases, prompts, schemas, validators, typed
  operations, deterministic lifecycle/ranking behavior, context budgets,
  model-role routing, and fallback;
- `W` owns only the immutable open checkpoint/adapter/delta used by the
  memory-specialist role; and
- `E` owns protected memory-accuracy, task-outcome, retention, transfer,
  privacy, and safety judgments and is inaccessible to either proposer.

`MemoryActionBroker` is the deterministic enforcement boundary. Its initial
vocabulary includes scoped `search`, `read`, `create_candidate`, `append`,
`upsert`, `revise_candidate`, `link`, and `compress_candidate`. Destructive
deletion or durable activation is never a direct model action: it becomes a
reviewable expiry/tombstone/promotion proposal subject to retention and
authority policy. The broker rejects unknown operations, path traversal,
cross-scope IDs, writes to immutable evidence, lifecycle bypass, unredacted
sensitive content, and any domain task action.

Every invocation emits a `MemoryActionReceipt` with skill/scaffold, task-model,
specialist-weight, context, store-high-water, and policy versions; phase and
operation; query or source record IDs; considered/selected targets; scope and
lifecycle filters; before/after content hashes; evidence/provenance links;
validation and redaction results; compression/token delta; latency/cost; effect
or rejection reason; and the later attempt/outcome references that may provide
credit. A memory action can receive benefit or harm attribution only when its
receipt proves the resulting record or retrieval influenced the attempt.

`MemorySkillTrainingManifest` specializes `TrainingEvidenceManifest` for the
memory-only `W` target. It binds the frozen scaffold and evaluator, task-model
and base-specialist weights, source trajectories and action receipts,
authority-qualified labels/selection criteria, positive and negative examples,
counterexamples, redaction/license/deduplication/contamination results,
train/validation/blind-holdback partitions, deterministic postprocessing, and
the exact recipe/configuration search. Task-action commitments and protected
answers are excluded from the supervised target and verified after
postprocessing. A meta-LLM may propose selection and configuration, but only
schema checks and protected evaluation admit a weight candidate.

At runtime, scaffold-only mode uses the task model through the same broker and
is the required baseline/fallback. In two-model mode the specialist may maintain
or consult memory, then hands the same receipted conversation state to the task
model. Only the task-role binding can commit a domain action; the task model may
request further scoped reads but cannot bypass the broker. The system records
which role produced every token and action so apparent task gains cannot be
misattributed to `W_mem`.

`SolutionTemplate.cognitive_skills[]` contains versioned
`CognitiveSkillTemplate` entries; the first supported kind is
`memory_management`. Each entry carries the reusable `H` scaffold and operation
contract, memory/context requirements, sensors, evaluator and acceptance
obligations, fixtures, compatibility rules, packaging fragments, provenance,
license, counterexamples, and negative-transfer history. It may reference a
packaged `W` snapshot only for an exact compatible base/tokenizer/chat-tool/
domain tuple, or carry a governed retraining recipe when a new child specialist
is required. Stored episodic/semantic content is not a cognitive skill and is
never copied across harnesses merely because the template is reusable.

### Memory contract

Use one versioned `MemoryRecord` envelope with type-specific payloads:

- `working`: current run/step/attempt facts derived from canonical state;
- `episodic`: immutable timestamped events and verified outcomes;
- `semantic`: current facts and preferences with explicit validity;
- `procedural`: versioned instructions, playbook rules, skills, and workflows.

`MemoryRecord` is governed content; `MemoryCognitiveSkillSnapshot` is the policy
that operates on that content. A file, vector index, Git branch, model adapter,
or template must not silently stand in for both layers.

Verified `SolutionPattern` records are governed procedural-memory artifacts with
additional goal-family, precondition, counterexample, provenance, transfer, and
paired-benefit fields. They are not flattened into generic advice or injected
outside their compatible scope.

Every durable record includes:

- memory ID, schema version, memory kind, tenant/project/repository scope, and
  optional user/blueprint/task-type scope;
- content and normalized search text;
- source run, step, attempt, event, artifact, and verifier references;
- observed time, valid-from/valid-to time, and creation/update time;
- confidence and evidence strength;
- lifecycle state (`candidate`, `active`, `superseded`, `rejected`, `expired`,
  or `tombstoned`);
- `supersedes` and conflict-set IDs;
- pin/retention and sensitivity/redaction metadata;
- creator and approver identity; and
- use, helpful, harmful, and last-retrieved counters.

The first durable implementation should use standard-library SQLite in WAL
mode with schema migrations and FTS5 lexical search. SQLite supplies atomic
updates, conflict/version indexes, and concurrent readers without adding a
service dependency. Embeddings and an external vector store are deferred until
lexical retrieval has a measured recall gap.

### Version contract

Every attempt and improvement result binds to exact versions of:

- model-portfolio snapshot, role/member bindings, hosted model IDs/capability
  probes, routing/fallback policy, inference parameters, and applicable open
  weight/adapter snapshots;
- repository/harness commit;
- context policy and compression policy;
- memory snapshot or high-water mark;
- memory-cognitive-skill scaffold, action vocabulary/schema, broker/policy,
  action receipts, task/specialist role bindings, optional specialist weight,
  memory-skill training manifest/recipe, deterministic fallback, and
  cognitive-skill-template versions;
- runtime mode, adaptation policy, constraint envelope, topology snapshot, and
  environment-observation high-water mark;
- item-bank, calibration, subject-profile, and exposure-policy versions;
- tool and sandbox policy;
- blueprint/workflow snapshot;
- evaluation suite and split manifests;
- graph-policy, task-distribution, rehearsal-runner, simulator/fixture image,
  variation-manifest, and graph-promotion versions; and
- goal-family profile, agent-program source/build/dependency hashes, sensor
  manifest and completeness policy, generated-code request/file receipts,
  editable-surface and retrieved-template/pattern manifests, domain-artifact
  evaluator stack,
  software-repository/CI adapters, symbolic engine, precision/tolerance policy,
  counterexample distribution, formal-verifier/kernel, evaluator-candidate and
  meta-evaluator, and solution-pattern/template/transfer-policy versions; and
- immutable base/adapter/delta/tokenizer/chat-template weight snapshots,
  training-evidence manifest, learning recipe, trainer/container,
  optimizer/scheduler state, hyperparameters, seeds, data/compute/privacy
  budgets, checkpoint lineage, and weight-promotion versions; and
- evolution-run, sequential task-source, batch schedule, harness snapshot and
  parent lineage, assessment-view, replay corpus, branch-router,
  `SearchPolicySnapshot`, population/decision receipts, worktree/commit lineage,
  branch-combination manifest, and change-manifest versions; and
- candidate code/config hash; and
- release-bundle, SBOM/provenance/signature, target-adapter, deployment,
  monitoring, active-pointer, approval, and rollback-receipt versions.

Without this tuple, an outcome is not replayable evidence and cannot justify a
memory promotion or harness promotion.

## Phased Implementation Plan

### Phase 0: Baselines, terminology, and invariants

- [Define the context, graph-policy/rehearsal, open-ended discovery/search,
  adaptation, evaluation, memory-cognitive-skill, memory, scope, trust, and
  version contracts] ->
  verify: Pydantic schema tests reject
  unknown fields, invalid
  graph ports/edges, undeclared variations, topology operations, constraint
  escalation, cross-kind invalid memory fields, missing evaluation provenance,
  invalid lifecycles, and ambiguous scope.
- [Create deterministic fixtures for long tool output, stale facts,
  contradictory facts, cross-project isolation, useful and harmful procedures,
  noisy/mislabeled/dependent eval items, overlapping ability intervals,
  exposure leakage, graph type mismatch, dangling edges, generator-authored
  success criteria, variation-specific failures, simulator fidelity gaps,
  runtime topology drift/oscillation, restart recovery, and candidate evaluator
  tampering, next-batch overfitting, useful-intermediate-snapshot collapse,
  ID/OOD transfer failure, replay forgetting, harness non-activation,
  trajectory adherence decay, self-judge misranking, and adversarial content in
  retrospective traces, wrong ticket classification, irrelevant historical
  patch retrieval, repository/version mismatch, patch behavior regression,
  evaluator self-approval, negative solution transfer, invalid equation syntax,
  hidden domain assumptions,
  symbolic-equivalence side conditions, numerically plausible false claims,
  missing derivation parents, sensor omission, generated-code nondeterminism,
  proof-kernel rejection, in-place weight mutation, contaminated training
  evidence, reward hacking, harness/weight confounding, catastrophic forgetting,
  checkpoint collapse, unsafe/OOD weight transfer, single-lineage anchoring,
  cross-lineage context leakage, shared-memory convergence, missing scoped
  memory for a fresh explorer, diff-size false diversity, unreceipted branch
  combination, agent-note self-activation, heartbeat suppression or unsafe
  interruption, duplicate attempts after worker restart, cross-island leakage,
  one-candidate policy switching, generated executable search code, population
  loss after policy fallback, search-policy self-scoring, and per-branch H/W
  confounding] ->
  verify: tests fail
  against the current implementation for the intended missing behavior.
- [Create adversarial memory-skill fixtures] -> verify: unknown operations,
  path traversal, cross-scope reads, immutable-evidence rewrites, premature
  outcome logging, silent lossy compression, direct activation/tombstoning,
  specialist-emitted task actions, unreceipted mutations, task-action tokens in
  `W_mem` targets, scaffold/weight confounding, repeated-development-set
  promotion, incompatible adapter/template reuse, unhealthy-specialist
  fallback, and incomplete release packaging all fail against the intended
  contracts.
- [Measure the current prompt budget, success, retry, token, and memory-recall
  baselines] -> verify: a checked-in redacted baseline report names the exact
  model, harness commit, and fixtures.
- [Define redaction and retention policy before capture] -> verify: seeded
  secrets, credentials, and disallowed paths never appear in stored context or
  memory fixtures.

Exit gate: contracts and failing behavioral tests are reviewed before runtime
wiring changes begin.

### Phase 1: Context orchestration in shadow mode

- [Refactor `src/metaharness/context.py` into a package with compatibility
  exports for `budget_for`, `fit_messages`, and `messages_tokens`] -> verify:
  existing context tests and callers remain green.
- [Define a typed context-source registry and role-specific source views] ->
  verify: shadow manifests distinguish instructions, live state, immutable
  artifacts, candidate/parent lineage, each memory kind, population findings,
  tools/policies, and evaluator receipts; identical inputs produce identical
  inclusions and explicit omission reasons.
- [Implement deterministic section budgeting and assembly] -> verify: stable
  ordering and content hashes for identical inputs; system/instructions and
  final response contract cannot be silently pruned.
- [Use tier-specific section quotas instead of one undifferentiated message
  budget] -> verify: tests cover rules, tool schemas, task, working state,
  memory, observations, and reserve allocations at every tier.
- [Add head-and-tail compression, structured summaries, and immutable artifact
  references for oversized observations] -> verify: information at both ends is
  retained or fetchable and every omitted byte has a manifest reason.
- [Emit a redacted `ContextManifest` journal event without changing live runner
  input] -> verify: shadow manifests reconstruct deterministically and do not
  alter existing task outputs.
- [Promote the assembler from shadow to active behind an explicit feature flag]
  -> verify: old and new paths run side-by-side on the baseline suite; active
  rollout requires no success regression and a measured token/attention gain.

Exit gate: every local-model attempt has a durable context receipt, while
coding-agent CLIs remain ephemeral.

### Phase 2: Typed memory substrate

- [Add SQLite schema migrations and a repository-scoped memory service] ->
  verify: atomic create/read/supersede/tombstone operations, WAL restart tests,
  concurrent reader tests, and schema upgrade/downgrade fixture coverage.
- [Expose typed store APIs rather than generic arbitrary JSON writes] -> verify:
  a worker cannot directly activate semantic or procedural memory.
- [Add immutable `MemoryCognitiveSkillSnapshot`, typed `MemoryAction`, and
  `MemoryActionReceipt` schemas plus a shadow `MemoryActionBroker`] -> verify:
  only allowlisted scoped operations validate; candidate writes preserve source
  provenance and before/after hashes; protected evidence and activation/
  tombstone authority remain unreachable; replay reproduces each accepted or
  rejected action.
- [Derive working memory from `RunState` and journal events] -> verify: resumed
  runs reconstruct the same working set without duplicating durable facts.
- [Capture redacted verified outcomes and topology transitions as episodic
  candidates] -> verify: exact source event/verifier/topology links,
  deterministic deduplication, and no capture from unverifiable self-claims.
- [Migrate playbook bullets to procedural records] -> verify: text, task scope,
  helpful/harmful counts, active/deprecated state, timestamps, and origins are
  preserved; migration is idempotent and reversible.
- [Implement semantic supersession and conflict sets] -> verify: a current fact
  replaces an old fact without erasing history, and unresolved conflicting
  evidence is surfaced instead of arbitrarily selected.
- [Expose a typed discovery-knowledge projection over evidence and memory] ->
  verify: concurrent agents append uniquely keyed attempts, evidence-linked
  notes, candidate skills, syntheses, connections, contradictions, and open
  questions at private/lineage/island/campaign scopes; filesystem placement
  alone grants neither trust nor activation.

Exit gate: memory survives restart, is scoped, versioned, reviewable, and can be
deleted or superseded without editing journal history.

### Phase 3: Retrieval, context assembly, and memory lifecycle

- [Build a deterministic query from objective, task type, blueprint/step,
  repository scope, role, topology, and current attempt state] -> verify: query
  fixtures are stable and never include secret values.
- [Apply hard scope and lifecycle filters before ranking] -> verify: no
  cross-tenant, cross-project, rejected, expired, tombstoned, or superseded
  record enters a context candidate set.
- [Rank with FTS relevance, evidence strength, validity, recency, pinning,
  helpful/harmful feedback, and source diversity] -> verify: golden ranking
  tests cover relevance, stale fact rejection, current-version preference, and
  diversity under a token cap.
- [Allocate independent episodic, semantic, and procedural quotas] -> verify:
  one verbose memory kind cannot consume the whole prompt.
- [Use progressive disclosure] -> verify: the initial envelope carries compact
  memory summaries and references; a memory-fetch tool can retrieve full
  evidence within the same scope and records the fetch in the manifest.
- [Use governed memory as a first-class context source beside repository and
  worktree lineage] -> verify: explorer fixtures have fresh conversation state
  but can retrieve scoped episodic/semantic/procedural memory; optimizer
  fixtures receive direct-lineage history plus scoped memory; scheduler fixtures
  receive compact population evidence; ambient all-branch memory and
  cross-scope retrieval hard-fail.
- [Implement the deterministic scaffold-only memory cognitive skill] -> verify:
  post-observation LOG/MAINTAIN cannot claim an outcome before a verifier or
  environment receipt exists; pre-action CONSULT uses only scoped broker
  operations; one task-role binding alone commits domain actions; all memory
  choices and omissions appear in the context and action receipts.
- [Measure active-memory behavior separately from task success] -> verify:
  relevant recall and realized benefit, stale/conflicting use, repeated writes,
  empty searches, compression loss, input tokens per step, operation latency/
  cost, and broker rejections remain per-case; lower context or more writes
  cannot masquerade as better memory without protected outcome evidence.
- [Add lifecycle jobs for episodic compression, semantic conflict review,
  procedural staleness, decay, pinning, and retention] -> verify: dry-run
  reports precede mutations and every mutation is versioned/auditable.
- [Credit memory only when its ID appeared in the attempt manifest] -> verify:
  helpful/harmful counters cannot be updated for uninjected memories.
- [Measure knowledge diffusion and convergence by scope] -> verify: every
  cross-lineage or cross-island retrieval records source, recipient, influence,
  and later outcome; fixtures distinguish helpful code transfer, helpful note
  transfer, irrelevant diffusion, and premature idea collapse.

Exit gate: held-out memory tasks show improved relevant recall and task benefit
without prompt overflow, stale-fact use, premature outcome claims, unreceipted
mutation, task-action leakage, or scope escape. The scaffold-only path is a
stable baseline and fallback before any learned specialist is enabled.

### Phase 4: Model, harness, and evolution evaluation intelligence

- [Normalize exact eval reports and legacy suite runs into immutable
  `EvalObservation` rows] -> verify: every case/repetition retains its full
  subject version tuple, item/suite reference, verdict, metrics, and source
  digest; summary regeneration never loses or rewrites raw evidence.
- [Define `EvolutionRunManifest`, `HarnessSnapshot`, assessment-view, and
  sequential task-source schemas] -> verify: invalid split reuse, mutable
  frozen snapshots, missing parent/version lineage, batch-order leakage,
  undeclared replay, and assessment-time mutation fail closed.
- [Implement the longitudinal snapshot schedule] -> verify: each update is
  followed by frozen update-validation and next-batch prequential scoring;
  selected checkpoints also receive held-out ID/OOD and replay assessment;
  exact per-case evidence survives every aggregate view.
- [Track best eligible snapshot independently of latest snapshot] -> verify: a
  synthetic campaign that improves at epoch two and collapses at epoch three
  retains epoch two, flags epoch three as collapsed, and never moves the active
  or pending pointer by recency alone.
- [Measure update quality, activation, adherence, and realized benefit
  separately] -> verify: fixtures distinguish a good artifact that was never
  loaded, an activated artifact that was later ignored, and a faithfully used
  artifact that did not improve outcomes.
- [Bind every observation to an exact `ModelPortfolioSnapshot` and applicable
  `WeightSnapshot`] -> verify: two attempts with different role models, hosted
  IDs/probes, routing, fallbacks, inference parameters, adapters, deltas,
  quantization, tokenizer, chat template, or merge state cannot collapse into
  one subject row or share an attribution result.
- [Add protected memory-cognitive-skill assessments] -> verify: no-external-
  memory, deterministic base scaffold, scaffold-optimized `H`, and frozen-
  scaffold-plus-specialist `H+W_mem` cells use equal task/budget/repetition
  envelopes; per-case results cover memory accuracy, relevant recall, stale/
  conflict handling, compression loss, scope/privacy, action efficiency,
  activation/adherence, realized task benefit, next-batch, ID/OOD, persistent-
  versus-episode memory, replay/retention, and specialist fallback. Development
  traces and repeatedly consulted selection sets cannot be reported as sealed
  final evidence.
- [Add paired and factorial attribution designs] -> verify: a weight-only
  comparison freezes harness, evaluator, task/environment, inference settings,
  and seed schedule; interaction reports explicitly identify H-by-W and E-by-W
  cells instead of assigning their joint delta to weights alone.
- [Report evolution trajectories, not only terminal scores] -> verify: success,
  regressions, forgetting, tokens, tool calls, wall time, monetary cost, and
  success-per-cost are available by batch, snapshot, task family, and
  assessment view with uncertainty.
- [Persist an evaluator-evolution sensor reservoir and sample manifests] ->
  verify: stratified samples cover pass/fail/unverified, disagreement,
  regression, novelty, rare/high-risk, pattern-transfer, and cost outliers;
  provenance, deduplication, selection probability, exposure, scope, and blind
  holdback are reproducible.
- [Define versioned evaluator snapshots and schedules] -> verify: each
  assessment binds one immutable `EvaluatorSnapshot`; cadence and early triggers
  react to drift, disagreement, coverage gaps, mismatch clusters, and
  calibration failure without changing scores mid-assessment.
- [Measure evaluator evolution itself] -> verify: parent/candidate reports
  retain anchor and hard-invariant coverage, mismatch correction, mutation
  detection, held-back predictive validity, calibration, false-positive and
  false-negative rates, stability, cost, and newly exposed blind spots.
- [Add versioned item-bank manifests with anchor, fingerprint, calibration, and
  mandatory-guard roles] -> verify: duplicate content, cross-split IDs, missing
  provenance, unauthorized role changes, and exposure-limit violations fail
  closed.
- [Implement descriptive item audits before IRT] -> verify: empirical
  difficulty, uncertainty, discrimination/error association, missingness,
  dependency, stale-label, and negative-discrimination fixtures produce
  reviewable findings without mutating cases.
- [Write and approve the calibration-backend ADR] -> verify: the chosen 1PL/2PL
  implementation passes convergence, identifiability, synthetic parameter
  recovery, numerical stability, reproducibility, and failure-path fixtures;
  failed fit produces no estimate.
- [Calibrate within explicit capability dimensions and populations] -> verify:
  sample thresholds, model/harness diversity, anchor coverage,
  local-independence/dimensionality diagnostics, fit, standard errors, and
  intervals are stored with every estimate.
- [Add item/person residual analysis] -> verify: synthetic inference faults and
  unexpected item outcomes are detected, while anomaly labels remain
  investigation evidence and cannot directly reject a model or rewrite an
  expected answer.
- [Add private fingerprint sets and exposure accounting] -> verify: seeded
  organization-specific leakage is attributable only with a clean control and
  declared threshold; item assignment, exposure, rotation, and retirement are
  auditable without disclosing sealed content.
- [Add differential item-functioning reports by provider/model/harness family]
  -> verify: the report identifies matched-capability curve differences and
  labels causal/provider-family interpretations as hypotheses.
- [Use adaptive item selection only for repeated calibration and routing probes]
  -> verify: deterministic seeds, anchor coverage, capability coverage,
  information target, exposure quotas, and maximum length are enforced;
  `mandatory_guard` cases and final promotion suites remain exhaustive.
- [Upgrade routing in shadow mode] -> verify: decisions compare
  capability-scoped estimates and uncertainty, cost/latency, and complementary
  residual profiles; the existing pass-rate router remains authoritative until
  replay demonstrates no reliability or budget regression.

Exit gate: item-level evaluation is reproducible, uncertainty-aware, resistant
to silent item corruption and uncontrolled exposure, and demonstrably useful
for routing. Psychometric estimates remain supporting evidence; exact mandatory
guards retain final authority.

### Phase 5: Rehearsal-based graph-policy optimization

- [Wrap each existing `WorkflowSpec` as immutable graph schema v1 without
  changing execution] -> verify: current blueprint/workflow serialization,
  topological order, engine behavior, and tests are byte/behavior compatible;
  inferred port types are advisory and cannot silently activate.
- [Add explicit typed input/output ports, data/control edges, checkpoint
  contracts, and bounded recovery edges] -> verify: duplicate ports, unresolved
  references, incompatible schemas, dangling/unreachable nodes, implicit
  permission expansion, unbounded cycles, and missing terminal/recovery paths
  fail static validation before execution.
- [Define versioned software `TaskDistribution` manifests] -> verify: fixtures
  vary inputs, repository/workspace states, configurations, tool failures,
  concurrency schedules, and external responses under recorded seeds; every
  unsupported or unrealistic dimension is disclosed as a fidelity limit.
- [Separate candidate generation from success-definition and verification] ->
  verify: an orchestrator partitions the workflow; bounded skill agents receive
  only their subgraph, approved procedure, filtered tools, and canonical
  scripts; a separate checkpoint author and protected verifier own
  postconditions.
- [Generate candidates only in an isolated copy-on-write graph workspace] ->
  verify: candidate nodes, edges, code, and parameters cannot mutate the active
  blueprint, evaluator, variation manifests, fixtures, or promotion pointer.
- [Compile each graph candidate into an immutable `AgentProgramCandidate`] ->
  verify: source, prompts, context policy, tools, dependencies, graph, sensors,
  evaluator references, and build environment receive one content-addressed
  version; evaluation cannot run an unbuilt or mutable candidate.
- [Implement bounded harness source generation] -> verify: a goal-family fixture
  generates role/workflow code, prompts/config, adapters, sensors, tests, and
  packaging files only inside the declared workspace; every file has a
  `GeneratedCodeReceipt`, builds under its lock, and cannot change evaluators,
  completeness rules, permissions, secrets, or promotion/deployment pointers.
- [Instrument every generative node with declared sensors] -> verify: equations,
  patches, assumptions, transformations, decisions, selected analogues,
  intermediate claims, verification obligations, derivation/dependency parents,
  tool effects, and terminal state produce append-only typed observations;
  suppressing a mandatory sensor fails the run.
- [Implement the layered domain-artifact evaluator interface] -> verify:
  software ticket/repository/patch/CI checks and mathematical
  parse/type/domain/symbolic/property/derivation/formal checks emit distinct
  receipts and cannot be converted into success by an LLM judge.
- [Add the software-ticket instrumentation profile] -> verify: fixtures capture
  ticket classification, reproduction, repository and symbol graph, prior
  issues/patches and their similarity/differences, generated diff, build/static
  checks, targeted and regression tests, coverage, runtime/CI effects, and
  repository-specific acceptance evidence.
- [Run sampled variants in parallel isolated rehearsal workers] -> verify:
  deterministic seeds reproduce deterministic variants; stochastic repetitions
  report variance; resource and side-effect isolation holds under concurrent
  runs; each node emits pre/post state and checkpoint receipts.
- [Localize failures before proposing graph changes] -> verify: a candidate
  update cites a verified node/checkpoint and uses only an allowlisted operation
  (compatible node substitution, edge change, or bounded parameter change);
  unsupported causal attribution becomes `NEEDS-EVIDENCE`, not an edit.
- [Regenerate the smallest justified agent surface from sensor evidence] ->
  verify: a localized prompt failure does not rewrite tools/code, a tool failure
  does not rewrite the proof criterion, and a code regeneration creates a new
  immutable child candidate that must replay the complete relevant suite.
- [Optimize against an ordered multi-objective policy] -> verify: correctness
  and safety are hard constraints; cost and latency/throughput form a preserved
  Pareto frontier; aggregate gains cannot hide a variant or mandatory-guard
  regression.
- [Add plateau, oscillation, and budget stops] -> verify: repeated graph states,
  alternating edits, no material validation gain, sample/compute limits, and
  wall-time limits stop cleanly with a replayable report.
- [Promote only an immutable selected graph] -> verify: development rehearsals
  drive proposals, validation selects, sealed holdout confirms once, authenticated
  human approval changes the active graph pointer, and the deterministic
  interpreter executes the exact promoted hash without synthesis agents.
- [Run the existing `HarnessOptimizer` as one bounded candidate-search backend,
  not as the rehearsal control plane] -> verify: legacy wrapper/config searches
  produce `GraphCandidate` evidence through an adapter while graph validation,
  variation sampling, isolation, and promotion remain independently enforced.

Exit gate: a software workflow graph improves on at least one declared
variation suite without any mandatory-guard regression, simulator evidence is
reproducible and fidelity-labeled, and the promoted graph runs deterministically
without access to the generator.

### Phase 6: Bounded adaptive orchestration

- [Add a deterministic complexity/mode gate] -> verify: ordinary predictable
  software tasks remain `fixed`; only fixtures with changing state, coupled
  actors, or invalidated assumptions can request `bounded_adaptive`, and policy
  may still refuse it.
- [Keep open-ended discovery campaigns separate from production runtime modes]
  -> verify: a CORAL-style asynchronous campaign can create experimental
  candidate worktrees, context/memory views, and decision receipts, but cannot
  reconfigure a live harness, change an active pointer, or be selected by the
  fixed-versus-bounded-adaptive runtime gate.
- [Implement immutable `HarnessTopology`, `ConstraintEnvelope`, and
  `AdaptationDecision` records] -> verify: schema and hash tests, parent-chain
  replay, exact rollback, and rejection of unknown capabilities or elevated
  permissions.
- [Add a validated harness-tree registry for heterogeneous regimes] -> verify:
  branches have immutable parentage, declared scope/fingerprints, independent
  snapshot assessments, and exact rollback; one branch cannot overwrite
  another or the global default.
- [Route among eligible branches in shadow mode] -> verify: the router cites
  verified task/regime evidence and uncertainty, logs abstention/fallback, and
  cannot select an experimental, collapsed, unassessed, or higher-authority
  branch.
- [Add explicit adaptation checkpoints to the workflow engine] -> verify:
  topology cannot change during a tool action or verification transaction, and
  every accepted/rejected proposal becomes a canonical journal event.
- [Begin with an allowlisted topology-operation vocabulary] -> verify: add or
  remove bounded specialist, change an approved dependency edge, request an
  independent verifier, reduce fan-out, or revert; arbitrary code/workflow DSL
  injection hard-fails.
- [Use verified environment/task observations as sensor input] -> verify: stale,
  unverified, or out-of-scope observations cannot authorize reconfiguration.
- [Score topology proposals against verified progress, safety, cost, latency,
  and capability diversity] -> verify: agent confidence alone has zero authority
  and every score component is inspectable.
- [Add stability controls] -> verify: topology-change budget, hysteresis,
  cooldown, oscillation detection, stagnation detection, and last-known-good
  fallback work under adversarial simulations.
- [Build non-stationary scenario evaluations] -> verify: bounded adaptation
  improves at least one approved changing-environment case without regressing
  fixed-mode cases, containment, auditability, or budget compliance.
- [Run the controller in shadow mode before it can change topology] -> verify:
  proposed decisions and counterfactual scores are captured while the existing
  static DAG remains authoritative.

Exit gate: bounded adaptation demonstrates measured value on complex scenarios,
never expands authority, and can replay or revert every topology transition.
Unrestricted emergent mode remains disabled.

### Phase 7: Durable learning evidence and weakness mining

- [Persist a redacted attempt evidence record that joins context manifest,
  graph/rehearsal observations, topology/adaptation decisions, sensor and
  equation/artifact evaluations, runner/tool receipts, verifier result, MAST
  label, versions, and outcome] -> verify: the
  record replays the decision inputs without depending on the in-memory OTel
  collector.
- [Replace count-only failure grouping with causal weakness clusters] ->
  verify: clusters include failure layer, task family,
  context/graph/variation/topology/tool/policy versions, shared evidence fingerprint,
  recurrence, and counterexamples.
- [Diagnose the failing layer before proposing a change] -> verify: fixtures
  distinguish instruction, context selection, memory retrieval/staleness,
  procedure, graph construction/rehearsal fidelity, coordination/topology,
  adaptation policy, tool schema, sandbox, workflow, verifier, and model
  limitations.
- [Diagnose generated-artifact failures from typed sensor receipts] -> verify:
  the miner distinguishes ticket/reproduction/retrieval/patch/build/test/runtime
  failures as well as malformed equations, inconsistent assumptions, invalid
  transformations, counterexamples, incomplete derivations, formal proof
  failure, sensor incompleteness, agent-program bugs, and genuine model
  limitations.
- [Create one falsifiable `HarnessChangeManifest` per minimal logical edit] ->
  verify: every proposal binds failure evidence, root cause, files/records,
  predicted fixes, at-risk cases, activation path, expected metric change, and
  rollback parent; the next assessment records observed task-level deltas and
  attribution precision/recall.
- [Mine activation and adherence failures separately from artifact-quality
  failures] -> verify: the system proposes retrieval/mount/routing fixes for
  artifacts that were useful when forced, rather than repeatedly rewriting the
  useful artifact itself.
- [Generate two reviewed outputs from a proven cluster: miner cases and
  regression-guard candidates] -> verify: no harvested production task enters
  an evaluation suite without redaction, deduplication, source approval, and an
  expected-result review.
- [Make semantic/procedural learning a proposal workflow] -> verify: raw model
  suggestions remain `candidate`; approval, rejection, supersession, and
  rollback are explicit events.
- [Add policy-owned reflection, consolidation, and redirection checkpoints] ->
  verify: interval/time/evaluation/plateau triggers, task noise epsilon,
  cooldown, resource cost, and minimum protected actions are versioned; a safe
  checkpoint and resume receipt surround any worker interruption; reflection
  emits an observation, consolidation emits typed memory/skill candidates, and
  redirection emits a search decision; agents may propose schedule changes but
  cannot suppress mandatory capture, activate their own output, or change
  evaluator/promotion state.
- [Distill repeatedly successful topology adaptations into reviewed workflow or
  procedural candidates] -> verify: a runtime topology never becomes a durable
  default from one run or without protected evaluation and approval.
- [Distill repeated verified solutions into scoped `SolutionPattern`
  candidates] -> verify: each pattern contains a goal-family fingerprint,
  preconditions, mechanism, parameterized change, evaluator receipts,
  counterexamples, non-applicable cases, provenance, and lifecycle; raw patches
  or task text never become universal instructions.
- [Distill repeatedly beneficial active-memory behavior into
  `CognitiveSkillTemplate(kind="memory_management")` candidates] -> verify:
  each candidate separates reusable scaffold/protocol/schema/validator behavior
  from stored memory content, names required sensors and evaluator obligations,
  binds H/W compatibility and licensing, retains harmful/counterexample cases,
  and stays pending until transfer evidence exists.
- [Compile compatible patterns into versioned `SolutionTemplate` candidates] ->
  verify: typed parameters, preconditions, code/graph/prompt/tool fragments,
  `cognitive_skills[]`, sensors, evaluator obligations, tests, packaging,
  provenance, licenses, counterexamples, and negative-transfer history are
  complete; unsupported goal-family, base-model, tokenizer/tool-protocol,
  memory-scope, or specialist-weight combinations fail before instantiation.
- [Retrieve patterns and instantiate solution templates for similar goals in
  shadow mode] -> verify:
  similarity cites structural evidence, repository/environment differences are
  explicit, non-matching preconditions reject transfer, each harness gets a new
  immutable candidate hash, and reuse earns credit only when injected and
  independently beneficial on the new goal.

Exit gate: every proposed memory or harness change cites repeated verified
evidence and the diagnosed harness layer.

### Phase 8: Safe self-improvement loop

- [Define versioned development, validation, and sealed holdout manifests] ->
  verify: the proposer/fixer can read development failures, selection uses
  validation, and sealed holdout content is inaccessible until the one-time
  final gate.
- [Move candidate edits into an isolated copy-on-write workspace with an
  allowlisted editable-surface manifest] -> verify: any change outside the
  declared files/fields fails even if candidate tests pass.
- [Add an AutoMem-style memory-scaffold `H` lane after the deterministic
  baseline exists] -> verify: full trajectory and `MemoryActionReceipt` review
  may propose only bounded phase/prompt/schema/validator/action/budget/routing
  changes; every child predicts delayed failure fixes and at-risk cases; the
  task and specialist weights plus evaluator remain frozen; development traces
  drive diagnosis, validation selects, next-batch/ID/OOD/replay establish
  eligibility, and the one-time sealed holdout is never fed back to the
  scaffold reviewer.
- [Fingerprint all evaluator code, seeded fixtures, expected results, suite
  manifests, and promotion pointers before and after proposal/evaluation] ->
  verify: answer-key edits, test deletion, symlink/hardlink tricks, and indirect
  fixture mutation hard-fail the candidate.
- [Store proxy-versus-trusted-evaluator mismatches as immutable counterexample
  evidence] -> verify: the mismatch corpus records both evaluator versions and
  inputs, never exposes sealed answers to the proposer, and becomes a mandatory
  non-regression guard rather than authorization to edit production scoring.
- [Run scheduled evaluator-evolution campaigns from sensor samples] -> verify:
  configured cadence and drift triggers create an immutable sample manifest,
  generate bounded candidate tests/checks/probes/rubrics/thresholds/adapters,
  and backtest them without modifying the currently frozen evaluator snapshot.
- [Keep evaluator updates in a protected shadow-candidate lane until eligible]
  -> verify: the candidate cannot edit reference outcomes, stable anchors or
  kernels, its meta-evaluator, mandatory guards, sample manifest, or promotion
  state and remains `NEEDS-EVIDENCE` without an adequate validity source.
- [Promote evaluator candidates under stricter gates than solver candidates] ->
  verify: immutable mismatch/replay/adversarial corpora, comparison with the
  prior evaluator, protected external outcomes, per-case non-regression, and
  authenticated human approval are required before the evaluator version can
  change.
- [Alternate harness and evaluator evolution epochs] -> verify: harnesses are
  compared under frozen `E_t`; promoted `E_t+1` triggers re-evaluation of every
  affected eligible/best harness snapshot; rank/verdict flips are preserved and
  quarantine promotion rather than being overwritten.
- [Keep rollout/candidate edits phase-local until review] -> verify: parseable
  and interface-valid candidates may enter a phase pool, but only validation
  evidence can nominate one pending candidate and no phase boundary changes the
  active harness without authenticated approval.
- [Add a label-free retrospective proposal lane] -> verify: a diverse coreset
  of difficult historical traces may be selected and replayed only in
  resettable, isolated environments; self-consistency, self-preference,
  execution-health, or public-test signals can rank proposals but are recorded
  as proxy evidence and never satisfy a protected promotion gate.
- [Reject contaminated or irreversible retrospective learning inputs] ->
  verify: untrusted task content cannot become an instruction, tool, skill,
  memory, or harness edit without redaction and review; one-shot/irreversible
  tasks are excluded from group replay and use separately designed evidence.
- [Support bounded candidate populations and regime branches] -> verify:
  proposers may explore several isolated descendants, but a judge sees no
  sealed labels, branch lineage is immutable, and each persistent descendant
  must pass its own protected snapshot assessments.
- [Build the native asynchronous campaign supervisor and lineage workspace]
  -> verify: bounded workers can start, checkpoint, proxy-evaluate, stop,
  restart, and resume independently; every attempt lands in a unique immutable
  child commit and ledger row; a crash, timeout, or cancellation leaves neither
  shared-code corruption nor an ambiguous terminal state.
- [Separate discovery-kernel interfaces and support an analysis warm-start] ->
  verify: population sampling, context assembly, generation, evaluation, and
  append/checkpoint use independent contracts; read-only scouts can profile the
  baseline and store verified findings without creating solution candidates;
  later context manifests prove which findings influenced which workers.
- [Preserve bounded worker autonomy inside the supervisor] -> verify: a worker
  can choose scoped retrieval, local tests, proxy-evaluation timing, and
  knowledge externalization across several tool turns, while hard workspace,
  tool, token, time, evaluation, and stop envelopes remain externally enforced.
- [Build the typed discovery knowledge hub] -> verify: attempts, notes, skills,
  syntheses, connections, contradictions, and open questions retain creator,
  source evidence, lineage, scope, trust, lifecycle, and use receipts; an
  agent-written file cannot become active memory or procedure by location
  alone.
- [Implement explorer, optimizer, and scheduler context views] -> verify: a
  fresh explorer conversation can still retrieve explicitly scoped memory; an
  optimizer receives only direct-lineage history and selected memory; the
  scheduler receives compact population evidence; withheld sources and every
  cross-lineage briefing are manifest-visible.
- [Add population-level parent/role/width/depth scheduling] -> verify: every
  spawn records parent, explorer/optimizer role, variation class, minimal
  briefing, budget, alternatives, and expected information gain; diversity
  floors, baseline reseeding, and underexplored-lineage quotas prevent the
  scheduler from silently collapsing onto the current leader.
- [Add typed heartbeat checkpoints] -> verify: reflection, consolidation, and
  redirection fire from versioned event/evaluation/time/plateau triggers with
  task noise epsilon and cooldown; safe interruption preserves worker state;
  emitted notes, skills, pivots, and cadence changes remain candidates subject
  to budget, scope, validation, and approval.
- [Implement a declarative EvoX-style search-policy evolver] -> verify: a
  population descriptor and strategy-history row cover each frozen policy
  window; stagnation may generate only allowlisted DSL changes to parent,
  inspiration, role, variation, memory visibility, budget, and stop rules;
  schema/static/simulation/shadow failures restore the parent policy while
  preserving the candidate population.
- [Evaluate scoped islands and explicit migration as an anti-convergence
  mechanism] -> verify: islands can receive different seeds, roles, memory, or
  inspiration pools without cross-scope leakage; a migrated candidate or
  finding has a selection and context-use receipt; isolated-island performance
  is compared with globally shared memory before activation.
- [Use external frameworks only for conformance and benchmark comparisons] ->
  verify: pinned CORAL and SkyDiscover/EvoX runs translate into the common
  ledger under the same task/model/evaluator/budget/repetition envelope;
  SwarmResearch is reproduced from its paper or run externally without copying
  unlicensed source; no framework grader can satisfy protected promotion.
- [Compare and compose mechanisms through budget-matched ablations] -> verify:
  repeated cells add CORAL-style knowledge/heartbeats, SwarmResearch-style
  role/scheduling, and EvoX-style policy evolution incrementally; worktree-only,
  scoped memory, global shared memory, and islands remain separable; the report
  retains per-case outcomes, variance, fingerprints, convergence, cross-agent
  transfer, cost, latency, and failures rather than ranking by LOC or branch
  count. A composed policy receives a new version and no automatic merge.
- [Keep actual model weights fixed during a harness epoch and order editable
  surfaces from least to most dangerous: retrieval/config weights, portfolio
  role/routing/fallback/inference parameters, approved topology/adaptation
  policy, prompt/instructions, workflow, then harness code] -> verify: each
  proposal names exactly one surface and its safety class; open checkpoints
  change only in a declared `W` epoch.
- [Allow agent-program regeneration only as a versioned code candidate] ->
  verify: regenerated code is isolated, dependency-locked, statically checked,
  instrumented, content-addressed, replayed against its parent, and unable to
  modify sensor completeness rules, protected evaluators, or promotion state.
- [Require deterministic replay where the dependency contract permits it] ->
  verify: identical frozen code, inputs, environment, tool versions, and seeds
  reproduce receipts; declared stochastic components receive repeated trials
  and variance rather than a false determinism claim.
- [Evaluate repeated stochastic trials and retain complete per-case results] ->
  verify: pass rates, variance, tokens, cost, latency, and failures are reported
  per task rather than only as an aggregate.
- [Require longitudinal eligibility before sealed confirmation] -> verify: a
  candidate must pass frozen update-validation, prequential next-batch,
  held-out ID/OOD transfer as applicable, replay/retention, mandatory guards,
  and efficiency budgets before it can become the one selected sealed-holdout
  contender.
- [Select the best eligible historical snapshot, not the latest] -> verify:
  later non-monotonic updates remain inspectable but cannot displace an earlier
  snapshot with stronger protected evidence.
- [Replace aggregate tolerance with strict case-level promotion] -> verify: a
  candidate is eligible only when no validation or holdout case regresses and
  at least one approved target case improves; aggregate gains cannot cancel a
  loss.
- [Use validation to choose among candidates and sealed holdout only to confirm
  the selected candidate] -> verify: rejected candidates and their outcomes are
  not fed back to the fixer as holdout hints.
- [Disable automatic promotion in the optimizer, CLI, and Web UI] -> verify: a
  passing candidate becomes a pending proposal with diff, evidence, and rollback
  target; only an authenticated human action updates the active pointer.
- [Rotate and retire holdout versions after exposure or repeated selection] ->
  verify: the ledger records exposure and refuses reuse as sealed evidence.
- [Make rollback a first-class promotion event] -> verify: one command/API call
  restores the exact prior config/code/context/memory policy tuple.

Exit gate: an adversarial test can neither change the yardstick nor cause an
unreviewed promotion, and a single case regression blocks promotion.

### Phase 9: Open-weight model evolution

- [Keep population search and weight training in separate attribution epochs]
  -> verify: no explorer, native discovery worker, or external comparison run
  can launch branch-local training;
  an `H` campaign freezes all weights, a `W` campaign freezes the selected
  harness/search policy and evaluator, and any declared H-by-W experiment uses
  complete factorial cells rather than crediting a coupled run to either axis.
- [Gate the plane on explicit open-weight capability and policy] -> verify:
  hosted-only portfolios report the `W` plane unavailable; hybrid portfolios
  expose only declared open members as trainable and hold frontier members
  fixed; unsupported architecture, license, data, privacy, trainer, or compute
  configurations fail before sampling or training.
- [Implement immutable `WeightSnapshot` storage and lineage] -> verify: base,
  adapter/delta order, tokenizer/template, precision/quantization, merge state,
  source/license, parent, and hashes reproduce loading; the active checkpoint is
  read-only and exact rollback restores the prior tuple.
- [Build governed `TrainingEvidenceManifest` samples from sensors] -> verify:
  source attempts and H/E/W versions, authority-qualified targets,
  redaction/licensing, deduplication, contamination, uncertainty, hard
  negatives, counterexamples, balance, replay, and blind holdback are complete;
  unverified self-claims cannot become positive reward or labels.
- [Specialize a `MemorySkillTrainingManifest` for `W_mem`] -> verify: the
  selected scaffold, task model, base specialist, evaluator, source
  trajectories, memory-action receipts, selection decisions, positive/negative
  examples, redaction/license/deduplication/contamination, partitions, and
  deterministic postprocessing are immutable; all task-action commitments and
  protected answers are absent from final targets, and successful return alone
  cannot label an earlier memory action as correct.
- [Add pluggable versioned learning recipes] -> verify: SFT, preference/policy
  optimization, or adapter-search backends declare trainer/container,
  hyperparameters, optimizer/scheduler, seeds, budgets, checkpoints, and failure
  behavior; no backend may mutate the base or evaluator.
- [Train multiple isolated weight candidates] -> verify: partial/crashed runs
  cannot alter active state; each checkpoint has immutable lineage, resource and
  data receipts, and a reproducible or explicitly stochastic training record.
- [Train and deploy memory-specialist candidates as a distinct portfolio role]
  -> verify: the selected `H` scaffold, `E`, task model, inference settings, and
  seed schedule remain frozen; each LoRA/checkpoint can emit only brokered
  memory actions during LOG/MAINTAIN and CONSULT; the unmodified task model
  alone commits domain actions; role-token/action provenance, latency/cost, and
  scaffold-only fallback are complete.
- [Attribute weight effects with controlled comparisons] -> verify: first-order
  tests freeze H/E/task/environment/inference/seed variables; factorial runs
  quantify H-by-W and E-by-W interactions and reject correlation-only causal
  claims.
- [Evaluate every candidate on longitudinal and safety views] -> verify:
  update-validation, next-batch, ID/OOD, replay/retention, catastrophic
  forgetting, calibration, reward/evaluator mismatch, mandatory safety guards,
  cost/latency, and variance remain per-case and cannot be hidden by aggregate
  reward.
- [Preserve and analyze the full checkpoint-by-item response matrix] -> verify:
  each response and repetition retains its exact H/E/W and inference tuple;
  versioned IRT profiles report theta, item difficulty/discrimination,
  intervals, fit, residuals, and model-family differential behavior only when
  population, diversity, dimensionality, local-independence, identifiability,
  convergence, and sample gates pass.
- [Use psychometric signals for diagnosis and sampling, not promotion] ->
  verify: anomalous residuals, informative items, or compact adaptive probes may
  schedule investigation and training-data review, but cannot relabel items,
  assert leakage/lineage, skip mandatory guards, or replace the exhaustive
  protected suite; forward checks detect tiny-benchmark validity loss on new
  checkpoint/model families.
- [Select best protected weight snapshot and stop safely] -> verify: latest
  checkpoint cannot displace best-so-far; goal threshold, plateau, oscillation,
  regression, safety, compute/data, and wall-time stops preserve the closest
  eligible checkpoint plus the unresolved gap.
- [Alternate H/E/W evolution epochs] -> verify: the coordinator changes one
  primary axis at a time, records the other frozen snapshots, re-evaluates
  affected best candidates after E changes, and labels any joint experiment as
  an interaction design.
- [Assemble the self-contained evolving-harness release profile] -> verify: the
  bundle contains an exact hybrid or open-only model portfolio, every packaged
  open loadable/trainable weight snapshot, governed sampler and recipes,
  sensors, evaluator/evolution schedule, context/memory, memory-action broker,
  cognitive-skill scaffolds, optional compatible memory-specialist weights and
  memory-only training recipes,
  solution-template resolver, isolated update workspace, H/E/W coordinator,
  protected assessments, budgets, stops, lineage, and rollback; it completes a
  bounded improvement cycle without manual code editing, exercises declared
  frontier dependencies in hybrid mode and offline/local execution in open-only
  mode, and cannot self-promote or change protected validity evidence.
- [Require pending promotion and exact rollback] -> verify: a passing weight
  candidate cannot update the active model pointer without authenticated
  approval; one action restores the exact prior base/adapter/harness/evaluator
  tuple.

Exit gate: an open-weight campaign demonstrates an attributable improvement on
at least one declared goal family without safety, retention, ID/OOD, or
mandatory-case regression, and every checkpoint/data/reward/trainer decision is
replayable and reversible. Failure to reach the full goal is reported honestly
with the closest protected checkpoint and remaining gap.

### Phase 10: Context, memory, rehearsal, evaluation, adaptation, and improvement observability

- [Add an attempt-level context inspector] -> verify: authorized users can see
  section budgets, source/memory IDs, scores, compression, omissions, hashes,
  and redactions without exposing secrets.
- [Add a memory browser] -> verify: users can filter by kind/scope/state, inspect
  provenance, compare conflicts, pin, approve, supersede, reject, tombstone, and
  audit use feedback.
- [Add a memory-cognitive-skill inspector] -> verify: it separates stored
  content from the active `H` scaffold and optional `W_mem`; displays phase,
  action, scope, before/after, selection/omission, compression, broker rejection,
  role handoff, task benefit, cost, compatibility, training provenance,
  best-versus-latest, fallback, pending promotion, and rollback without exposing
  sensitive records or protected evaluation content.
- [Add an improvement review console] -> verify: it displays the source cluster,
  diagnosed layer, editable surface, candidate diff, integrity checks,
  change-manifest predictions versus outcomes, activation/adherence, per-case
  results, pending approval, and rollback target.
- [Add a rehearsal/graph-policy inspector] -> verify: it displays task
  distribution and fidelity limits, sampled variants/seeds, graph lineage,
  static/type checks, node-local pre/post evidence, localized updates, Pareto
  objectives, plateau reason, selected graph, and promotion receipt.
- [Add a runtime topology inspector] -> verify: it displays the active and prior
  topology, constraint envelope, verified sensor inputs, accepted/rejected
  decisions, stability controls, and one-action fallback to the last stable
  snapshot.
- [Add an evaluation intelligence inspector] -> verify: it displays response
  coverage, item roles/exposure, difficulty/discrimination/fit, intervals,
  residual anomalies, differential behavior, calibration population/version,
  and why an adaptive item or route was selected.
- [Add an evolution campaign inspector] -> verify: it displays sequential task
  batches, frozen snapshot lineage, assessment views, best-versus-latest,
  prequential and ID/OOD transfer, replay forgetting, efficiency trajectories,
  branch routing, proxy/trusted-evaluator separation, collapse/plateau reason,
  and promotion eligibility.
- [Add an evaluator-evolution inspector] -> verify: it displays schedules and
  drift triggers, sensor sample strata/provenance/holdbacks, parent/candidate
  lineage, anchor/mutation/mismatch/replay/calibration/coverage results,
  H/E epoch boundaries, rank flips, pending promotion, and rollback.
- [Add an open-weight evolution inspector] -> verify: it displays immutable
  model-portfolio modes, role routing/fallbacks/inference parameters, immutable
  base/adapter/delta lineage, training-evidence provenance and exclusions,
  learning recipe and budgets, checkpoint curves, controlled H/E/W attribution,
  the full checkpoint-by-item matrix, advisory IRT intervals/fit/residuals/DIF,
  tiny-benchmark coverage and drift, forgetting/safety/ID/OOD results,
  best-versus-latest, stops, pending promotion, and rollback without exposing
  sensitive training data or sealed items.
- [Build reproducible `HarnessReleaseBundle` artifacts] -> verify: eligible
  generated source, graph, dependencies, policies, sensors, evaluators,
  model/weight references, cognitive-skill templates, memory scaffolds,
  action-broker/schema, compatible task/specialist bindings, scaffold-only
  fallback, optional memory-only recipe/evidence references, SBOM/provenance,
  health checks, target adapter, and rollback parent resolve to one signed
  release hash; rebuilding from the same immutable inputs produces the same
  artifact or a declared reproducibility exception.
- [Add explicit deployment adapters and monitoring receipts] -> verify: staging
  and production targets require authenticated promotion, cannot expand the
  bundle's capability envelope, continuously emit release-bound health/goal
  sensors, and can atomically restore the exact prior release; package creation
  alone never changes an active deployment pointer.
- [Emit OTel spans for assembly, retrieval, lifecycle, mining, evaluation, and
  promotion while retaining durable domain records] -> verify: telemetry can be
  discarded without losing audit/replay state.

Exit gate: a reviewer can answer what the model saw, what it remembered, why a
candidate was proposed, what changed, how it was tested, and who promoted it.

## Proposed File Boundaries

Keep changes surgical and preserve existing public imports during migration.

```text
src/metaharness/context/
  __init__.py          # compatibility exports plus new public contracts
  models.py            # envelope, section, manifest
  policy.py            # tier/section budgets and ordering
  assembler.py         # deterministic selection and assembly
  compression.py       # head/tail, summaries, artifact references

src/metaharness/memory/
  models.py            # typed record payloads and lifecycle
  migrations.py        # SQLite schema versions
  store.py             # transactions and scoped queries
  retrieval.py         # filtering, FTS ranking, quotas
  actions.py           # typed operations, broker enforcement, action receipts
  skills.py            # cognitive-skill scaffold, phases, compatibility, fallback
  runtime.py           # LOG/MAINTAIN, CONSULT, and task-role handoff
  capture.py           # journal/evidence to episodic candidates
  lifecycle.py         # supersession, conflict, decay, consolidation
  policy.py            # redaction, retention, promotion rules

src/metaharness/rehearsal/
  models.py            # typed graph, distribution, candidate, observation
  generator.py         # orchestrator plus bounded skill-agent subgraphs
  validator.py         # ports, edges, checkpoints, capabilities, reachability
  runner.py            # seeded parallel isolated variation execution
  localization.py      # checkpoint evidence to node/root-cause candidates
  optimizer.py         # allowlisted updates, Pareto, plateau/oscillation stops
  interpreter.py       # deterministic execution of immutable promoted graphs

src/metaharness/instrumentation/
  models.py            # goal family, agent program, sensors, observations
  compiler.py          # frozen graph/config/prompt/tool code to program bundle
  sensors.py           # append-only domain artifact, decision, tool, state sensors
  completeness.py      # mandatory-observation and proof-obligation receipts
  replay.py            # deterministic/seeded program and receipt comparison

src/metaharness/generation/
  models.py            # code request, file receipt, candidate and release inputs
  planner.py           # goal profile to bounded generated source surfaces
  generator.py         # role/workflow/prompt/adapter/sensor/test/package code
  workspace.py         # isolated allowlisted source-generation workspace
  build.py             # dependency lock, static/type/test and artifact build
  repair.py            # localized evidence to minimal code regeneration

src/metaharness/evaluators/
  models.py            # artifact evaluation and authority/coverage receipts
  software.py          # ticket, repo analogue, patch, build/test/runtime/CI
  equations.py         # parse, type, scope, domain, unit, assumptions
  symbolic.py          # exact normalization/equivalence plus side conditions
  properties.py        # numerical/property tests and counterexample search
  derivations.py       # dependency graph and step-obligation validation
  formal.py            # protected proof-kernel/domain-verifier adapters
  updates.py           # shadow evaluator candidates and protected promotion

src/metaharness/training/
  models.py            # model portfolio, weight/evidence/run/attribution models
  sampling.py          # sensor/evaluator evidence to governed training data
  memory_skill.py      # memory-only trace curation, filtering, and two-role eval
  recipes.py           # versioned SFT/preference/policy/adapter backends
  runner.py            # isolated budgeted training and checkpoint capture
  attribution.py       # paired and factorial H/E/W comparisons
  selection.py         # best-over-latest, plateau/safety/forgetting stops
  promotion.py         # pending approve and exact weight rollback

src/metaharness/adaptation/
  models.py            # topology, constraint envelope, decision receipt
  mode.py              # fixed versus bounded-adaptive classification
  sensors.py           # verified environment/task observations
  policy.py            # allowed operations, budgets, invariants, stability
  controller.py        # checkpoint proposal, validation, apply/revert
  scoring.py           # progress, safety, cost, latency, diversity

src/metaharness/discovery/
  models.py            # campaign, population, policy, heartbeat, decision receipts
  interfaces.py        # store, context, generator, evaluator, controller protocols
  kernel.py            # native bounded observe/schedule/run/evaluate/checkpoint loop
  supervisor.py        # async worker lifecycle, health, restart/resume, budgets
  warmstart.py         # read-only baseline profiling and evidence-grounded hypotheses
  worker.py            # bounded long-running retrieve/test/submit/externalize loop
  workspace.py         # isolated worktree lineage and multi-parent children
  knowledge.py         # attempts/notes/skills/synthesis/open-question hub views
  roles.py             # explorer, optimizer, scheduler context-source policies
  scheduler.py         # parent/role/operator/width/depth/budget decisions
  heartbeat.py         # reflect/consolidate/redirect triggers and safe delivery
  population.py        # descriptors, fingerprints, frontier and diversity state
  policy.py            # bounded declarative search-policy DSL and validation
  evolution.py         # frozen policy windows, stagnation, strategy history/fallback
  evaluation.py        # proxy broker and protected-assessment handoff
  islands.py           # scoped populations and explicit migration experiments
  comparison.py        # budget-matched repetitions, ablations, diversity/progress
  containment.py       # evaluator, memory, permission, pointer, and export guards
  interop/
    coral.py           # optional pinned CORAL conformance/benchmark translator
    skydiscover.py     # optional pinned SkyDiscover/EvoX benchmark translator
    swarmresearch.py   # external/paper reproduction; no unlicensed source reuse

src/metaharness/evals/
  intelligence.py      # coordinates observations, calibration, and selection
  observations.py      # normalized immutable item-response rows
  evolution.py         # sequential batches and frozen snapshot assessments
  retention.py         # replay, forgetting, ID/OOD, prequential views
  artifact_use.py      # update quality, activation, adherence, benefit
  item_bank.py         # roles, anchors, fingerprints, exposure policy
  calibration.py       # versioned item/subject estimates and fit gates
  residuals.py         # item/person anomalies and differential behavior
  selection.py         # bounded adaptive calibration-item selection
  profiles.py          # model-plus-harness capability/error profiles

src/metaharness/learning/
  harness.py           # coordinates rehearsal, within-run, across-run loops
  evidence.py          # durable joined attempt/topology/outcome records
  weaknesses.py        # causal clustering over verified evidence
  diagnosis.py         # failing harness-layer classification
  changes.py           # falsifiable change manifests and attribution verdicts
  lineage.py           # immutable snapshot/branch tree and best-so-far state
  patterns.py          # scoped transferable solution patterns and lifecycle
  cognitive_skills.py  # reusable skill templates and H/W compatibility
  templates.py         # reusable parameterized solution templates and versions
  transfer.py          # goal similarity, preconditions, reuse/negative transfer
  proposals.py         # memory/procedure/eval/config/code candidates

src/metaharness/optimization/
  datasets.py          # dev/validation/sealed manifests and exposure state
  integrity.py         # fingerprints and evaluator protection
  workspace.py         # isolated candidate edit surface
  retrospective.py     # label-free coreset/replay/proxy proposal lane
  promotion.py         # pending, approve, rollback events

src/metaharness/release/
  models.py            # release bundle, target, deployment and rollback receipts
  package.py           # reproducible artifact, manifest, SBOM and signature
  adapters.py          # bounded staging/production deployment interfaces
  monitor.py           # release-bound health and goal sensors
  promotion.py         # authenticated deploy pointer and exact rollback
```

Expected integration points are `core/executor.py`, `workflows/dsl.py`,
`workflows/engine.py`, `workflows/journal.py`, `blueprints/models.py`,
`harness/local.py`, `correction/learning.py`,
`correction/playbook.py`, `optimization/loop.py`, `optimization/harvest.py`,
`evals/artifacts.py`, `evals/evaluator.py`, `evals/gate.py`,
`routing/router.py`, `web/state.py`, `web/app.py`, and the CLI. Runner-specific
memory or evaluation implementations are explicitly out of scope.

## Verification Matrix

| Invariant | Minimum evidence before completion |
| --- | --- |
| Deterministic context | Identical immutable inputs produce identical section order and hashes. |
| Bounded attention | Every tier stays within budget; no required section is silently lost. |
| Durable replay | Restart reconstructs working state, manifest references, and memory high-water mark. |
| Memory correctness | Relevant episode is recalled; stale semantic fact is excluded; conflict is surfaced; current procedure wins. |
| Scope isolation | Cross-user/project/repository retrieval tests return no data. |
| Trust isolation | Retrieved text and worker output cannot become instruction or active semantic/procedural memory without policy/review. |
| Secret hygiene | Seeded secret values are absent from durable context, memory, traces, and eval artifacts. |
| Useful learning | Only memories actually injected receive outcome credit; harmful records can be deprecated and rolled back. |
| Memory content/policy separation | `MemoryRecord`, `MemoryCognitiveSkillSnapshot`, optional specialist weight, and `CognitiveSkillTemplate` have separate IDs, scopes, lifecycles, and rollback; none silently substitutes for another. |
| Memory-action containment | Every search/read/write/revise/compress proposal passes a typed broker and yields an immutable receipt; cross-scope access, source-evidence mutation, direct activation/deletion, hidden loss, and task actions fail closed. |
| Memory phase and role integrity | Outcomes are logged only after environment/verifier evidence; consultation precedes action; the specialist cannot commit the domain action; an unhealthy or incompatible specialist falls back to the exact scaffold-only route. |
| Context-source plurality | Git/worktree lineage, live state, artifacts, episodic/semantic/procedural memory, population findings, tools/policies, and evaluator receipts are independently selectable and visible in the manifest; no source silently stands in for another. |
| Role/source isolation | Fresh explorers omit inherited conversation but may receive scoped memory; optimizers receive only direct lineage plus scoped memory; schedulers receive compact population evidence; ambient cross-branch or cross-scope retrieval fails closed. |
| Discovery-interface separation | Population sampling or search-policy changes cannot replace context receipts, candidate freezing, sensor capture, evaluation authority, or append/checkpoint semantics. |
| Analysis/solution separation | Warm-start scouts cannot mutate solution candidates; every profiled claim has a tool/verifier receipt and later influence is visible in recipient context manifests. |
| Bounded worker autonomy | Workers may choose scoped retrieval, local experiments, submission timing, and knowledge capture, but external workspace/tool/time/token/evaluation/stop envelopes remain enforceable and replayable. |
| Native discovery replay | One campaign manifest plus its ordered population, scheduler, context/memory, heartbeat, worker-health, tool, and proxy-evaluation receipts reconstructs every candidate lineage and terminal decision. |
| Asynchronous campaign containment | Concurrent workers have isolated worktrees and bounded resources; crashes, timeouts, restarts, resume, cancellation, and partial completion cannot corrupt shared state or leave an unjournaled outcome. |
| Discovery-knowledge governance | Attempts are append-only evidence; notes, skills, syntheses, contradictions, and open questions retain provenance, scope, trust, lifecycle, and use receipts and cannot self-activate as memory or procedure. |
| Heartbeat safety | Reflection, consolidation, and redirection triggers are versioned, noise-aware, cooldown-bound, checkpoint-safe, and unable to suppress protected capture, change authority, or activate their own output. |
| Search-policy containment | Meta-evolution changes only an allowlisted declarative DSL; generated executable controller code, evaluator edits, permission expansion, and pointer mutation fail static validation, and failed policies restore their parent without losing candidates. |
| External conformance parity | Optional pinned CORAL and SkyDiscover/EvoX runs and a paper-faithful SwarmResearch reproduction translate into the native immutable schema under the same task, model, evaluator, budget, and repetition contracts without becoming authoritative. |
| Search diversity evidence | Behavioral and structural fingerprints, independent protected outcomes, convergence indicators, and repeated-run variance support diversity claims; LOC, branch count, or agent count alone do not. |
| Search-policy attribution | Fixed, scheduler-guided, and EvoX-adapted policies are frozen within comparable cells; every parent/role/context/budget/pivot decision traces to a policy and later protected outcome. |
| Branch-combination provenance | Every multi-parent child records source commits, selected changes, conflicts, resolver, build/test/evaluation receipts, and rollback parents; merge cleanliness is never promotion evidence. |
| Response-matrix preservation | Every derived score/profile traces to immutable per-item, per-repetition evidence and an exact model-portfolio-plus-harness version tuple. |
| Calibration validity | Synthetic recovery, fit, convergence, dimensionality/local-independence, sample-diversity, and interval tests gate every published calibration. |
| Item-bank hygiene | Noisy, negatively discriminating, stale, dependent, or anomalous items become review findings; statistics never silently rewrite or delete ground truth. |
| Evaluation uncertainty | Overlapping capability intervals are not reported as a certain ranking or used as sole promotion authority. |
| Frozen snapshot assessment | Assessment cannot mutate the snapshot; every result binds exact lineage, batch, evaluator, and assessment-view versions. |
| Forward generalization | An update is scored on the next unseen batch before that batch can influence a later update. |
| Transfer and retention | Eligible snapshots pass applicable ID/OOD transfer, replay/forgetting, mandatory-guard, and efficiency gates. |
| Best over latest | A later collapsed snapshot cannot replace the strongest eligible historical snapshot or active pointer. |
| Harness-use decomposition | Update quality, activation, trajectory adherence, and realized paired benefit are separately observable. |
| Proxy subordination | Self-preference, consistency, execution health, public tests, and LLM judges may rank proposals but never authorize promotion or edit trusted evaluation. |
| Change falsifiability | Every logical edit predicts fixes and regressions, receives a next-assessment verdict, and has an exact rollback parent. |
| Branch containment | Regime branches have independent lineage and assessment; routing cannot select experimental, collapsed, or higher-authority branches. |
| Exposure containment | Anchor/fingerprint/calibration assignments obey private-bank exposure limits and never reveal sealed content. |
| Mandatory guard completeness | Adaptive selection cannot omit any required safety or regression case from a final promotion gate. |
| Graph structural safety | Typed ports and data/control edges resolve; permissions are bounded; dangling, unreachable, incompatible, or unbounded-cycle graphs fail before rehearsal. |
| Generator/verifier separation | A graph generator or candidate edit cannot change checkpoints, evaluator code, variation manifests, fixtures, expected results, or promotion state. |
| Rehearsal reproducibility | Exact graph, fixture/simulator version, variation manifest, seed, and policy recreate deterministic observations; stochastic results retain repetitions and variance. |
| Synthesized-program immutability | Evaluated agent source, graph, prompts, tools, dependencies, sensor manifest, and build environment resolve to one immutable hash. |
| Generated-code provenance | Every generated file traces to its goal/parent, generator portfolio/member and applicable open weight, context/memory, retrieved patterns, tools, editable surface, dependencies, build/test receipts, and rejected alternatives. |
| Heterogeneous model portfolio | Every role binds an exact hosted model/capability probe or open weight snapshot plus routing, fallbacks, inference parameters, budgets and trainability; hybrid and open-only modes are replayable and hosted drift cannot silently share attribution. |
| Sensor completeness | Every mandatory equation, assumption, transformation, tool effect, proof obligation, and terminal state emits an append-only observation or a failed completeness receipt. |
| Domain-artifact evidence separation | Software ticket/repository/patch/CI and mathematical structural/symbolic/property/derivation/formal results remain separately inspectable. |
| Proof authority | Numerical agreement, symbolic simplification, deterministic execution, or an LLM judgment cannot substitute for the required trusted proof kernel or domain verifier. |
| Deterministic deployment | Frozen deterministic components replay under exact inputs/environment/tool versions/seeds; stochastic dependencies are declared and evaluated with repetitions and variance. |
| Sensor-driven regeneration | Every prompt, tool, graph, or code regeneration cites localized sensor/evaluator evidence, creates a new child hash, and cannot edit the yardstick. |
| Evaluator non-self-approval | Sensor evidence may create an evaluator candidate, but only an immutable external/meta-evaluation corpus plus human promotion can change the trusted evaluator version. |
| Software repair evidence | A ticket solution binds reproduction, repository analogue provenance, immutable diff, static/build/test/coverage/runtime/CI results, and repository acceptance criteria. |
| Scoped solution transfer | A reusable pattern declares goal-family fingerprints, preconditions, counterexamples, non-applicable cases, provenance, and paired benefit in each new harness; similarity alone cannot promote it. |
| Cross-harness solution templates | Every template binds typed compatibility, reusable code/graph/prompt/tool fragments, sensor/evaluator/test/package obligations, provenance and negative-transfer history; every harness instance receives a new hash and full re-evaluation. |
| Weight snapshot immutability | Base, ordered adapters/deltas, tokenizer/template, precision/quantization, merge state, license/source, and parent lineage resolve to one immutable loadable hash; training never mutates active weights. |
| Training-evidence governance | Every example/reward traces to sensor and authority-qualified evaluator evidence with H/E/W versions, redaction/license, dedupe/contamination, uncertainty, hard negatives, replay, and blind holdback. |
| Weight attribution | A weight-only claim freezes harness, evaluator, task/environment, inference settings, and seeds; joint effects use an explicit factorial design and report interactions. |
| Memory-skill H/W attribution | Base scaffold, optimized scaffold, and scaffold-plus-`W_mem` cells are separately frozen and budget-matched; task-action targets are absent from memory training, role outputs remain attributable, and repeatedly consulted selection data is not final holdout evidence. |
| Cognitive-skill transfer | A reusable memory skill carries scaffold/protocol/schema/sensors/evaluator obligations and negative-transfer history, never unscoped memories; packaged weights require exact base/tokenizer/tool/domain/license compatibility and protected transfer evaluation. |
| Open-weight response matrix | Every checkpoint retains exact per-item, per-repetition outcomes; summaries and IRT profiles are reproducible derived views and never discard the source cells. |
| Psychometric validity limits | Theta/item/residual/DIF outputs require population, sample-diversity, dimensionality, local-independence, fit, convergence, identifiability, and interval evidence; tiny benchmarks remain advisory and are forward-tested against new model families. |
| Weight retention and safety | Every eligible checkpoint passes next-batch, ID/OOD, replay/forgetting, calibration, reward-mismatch, safety, cost, and per-case regression gates. |
| Weight best over latest | A later or higher-reward checkpoint cannot replace a stronger protected historical snapshot; stops preserve the closest eligible checkpoint and unresolved goal gap. |
| H/E/W epoch separation | Harness, evaluator, and weight evolution snapshots and transitions are versioned; one primary axis changes per attribution epoch and evaluator changes trigger re-evaluation. |
| H/W population separation | Discovery branches cannot train their own weights; weight effects are measured with frozen harness/search policy or complete declared factorial H-by-W cells under one protected evaluator. |
| Reproducible release and deployment | A release hash binds generated code, graph, dependencies, policies, sensors, evaluators, weights/model, target constraints, provenance and rollback; packaging cannot change the deployed pointer and monitoring remains release-bound. |
| Self-contained evolution | The release can execute a bounded H/E/W improvement campaign with its packaged open weights, sampler, recipes, sensors, evaluator lane, memory, update workspace and protected assessments, but cannot self-authorize correctness, authority expansion, or deployment. |
| Variation coverage and fidelity | Every promoted graph reports covered dimensions, mandatory variants, unsupported dimensions, and measured sim-to-live/fixture-to-production gaps. |
| Failure localization | Every graph update cites node-local pre/post evidence and a verifier receipt; unsupported attribution produces no mutation. |
| Candidate isolation | Rehearsal candidates never alter the active graph or shared workspace before protected evaluation and human promotion. |
| Deterministic graph deployment | Ordinary execution uses the exact immutable promoted graph and does not invoke generation/optimization agents. |
| Adaptive containment | Runtime changes use only approved topology operations and never expand permissions, tools, memory scope, or evaluator access. |
| Adaptive stability | Change budgets, hysteresis, cooldowns, oscillation/stagnation detection, and last-known-good fallback hold under adversarial simulations. |
| Adaptive legibility | Every topology and accepted/rejected transition has immutable hashes, verified inputs, policy decisions, and replayable parentage. |
| Evaluation integrity | Any evaluator, fixture, answer-key, suite-manifest, or promotion-pointer mutation hard-fails. |
| No regression masking | One regressed case blocks promotion even when aggregate score improves. |
| Human control | All surfaces park passing candidates; authenticated approval is required to promote. |
| Rollback | Prior version tuple is restored exactly and is visible in the audit log. |
| End-to-end compatibility | Existing build/run/save/edit/rerun workflows and current tests remain green through staged migration. |

Each implementation phase should run its focused tests, then the full test
suite with `.venv/bin/pytest -q`, followed by `git diff --check` and inspection
of the actual diff. The mandatory independent second opinion is a read-only,
frozen-diff review by Pi using NeuralWatt `glm-5.2`, launched through the
session-scoped tmux `pi-drive.sh` workflow described in `AGENTS.md`. Preserve
its verbatim output under `.review-store/`, attach the evidence and finding
dispositions to the Linear card, and block integration while any P0/P1 remains.

## Migration and Rollout

1. Add contracts and shadow manifests without changing live prompts.
2. Introduce the memory database empty; do not bulk-promote historical text.
3. Backfill only redacted verified episodes as `candidate` records.
4. Migrate playbook bullets to procedural records with a reversible migration.
5. Enable retrieval for selected task types in shadow mode, compare selected
   memory and outcomes, add the typed action broker and deterministic
   scaffold-only memory skill, then activate by explicit policy version only
   after action receipts, scope/lifecycle enforcement, phase ordering, task-role
   separation, behavior metrics, and fallback are green.
6. Normalize new and historical exact eval reports into immutable response
   observations without changing existing scores or gates.
7. Add evolution-run and frozen-snapshot schemas, then replay historical
   optimizer output as a shadow sequential campaign without changing promotion.
8. Publish update-validation, prequential next-batch, ID/OOD transfer,
   replay/forgetting, artifact-use, and efficiency reports in shadow mode.
9. Publish descriptive item-quality and uncertainty reports in shadow mode;
   review suspicious items manually.
10. Approve the calibration-backend ADR and keep calibrated item/subject profiles
   advisory until sample, fit, and stability gates pass.
11. Shadow adaptive calibration-item selection and uncertainty-aware routing;
   mandatory regression guards and the current router remain authoritative.
12. Wrap existing `WorkflowSpec`/blueprint versions as graph schema v1 with no
    execution change; infer ports only in shadow reports.
13. Generate and compile shadow `AgentProgramCandidate` bundles, retain
    per-file `GeneratedCodeReceipt` provenance, and emit sensor-completeness
    receipts without changing current runner outputs or verdicts.
14. Add protected domain-artifact evaluator adapters incrementally, preserving
    each check and authority level as a separate receipt.
15. Run an end-to-end software-ticket shadow profile over reviewed historical
    issues: retrieve analogous repository evidence, generate a frozen patch,
    capture diff/build/test/runtime/CI sensors, and compare with known outcomes.
16. Add equation/proof adapters incrementally: structural and domain checks
    first, then symbolic/property/derivation checks, with formal-kernel adapters
    only for explicitly supported domains.
17. Add a small reviewed software-variation suite with pinned fixture images,
    seeds, fidelity limits, independent checkpoints, and mandatory guards.
18. Run graph generation, agent-code regeneration, and rehearsal in shadow
    mode; candidate graphs/programs remain
    inert and the current workflow stays authoritative.
19. Build the native campaign supervisor, isolated lineage-workspace manager,
    narrow population/context/generator/evaluator/controller interfaces,
    analysis warm-start, bounded autonomous worker protocol, population ledger,
    proxy-evaluation broker, health/restart/resume controls, and terminal export
    in single-agent shadow mode; no framework dependency or active-state change
    is required.
20. Add CORAL-derived typed attempts/notes/skills knowledge, safe heartbeats,
    and optional scoped islands; then add SwarmResearch-derived explorer,
    optimizer, scheduler context views and parent/role/width/depth decisions.
    Prove each mechanism separately before enabling the combined native loop.
21. Add the declarative EvoX-derived policy DSL, population descriptors,
    frozen strategy windows, stagnation-triggered policy candidates, validation,
    shadow deployment, and fallback. Run budget-matched repeated ablations of
    worktree-only, scoped/global/island memory, fixed/scheduler-guided/adaptive
    search, and the full native composition. Use pinned CORAL and
    SkyDiscover/EvoX plus a paper-faithful SwarmResearch reproduction only as
    optional conformance benchmarks; do not copy unlicensed source.
22. Allow protected validation and one-time sealed holdout only after static
    graph/program checks, sensor completeness, evaluator isolation,
    reproducibility, and node-evidence checks pass.
23. Permit a human-approved immutable graph/program to execute through the
    deterministic interpreter, with immediate pointer rollback.
24. Run adaptive-mode classification, harness-tree routing, and topology
    decisions in shadow mode while the static workflow remains authoritative.
25. Enable bounded adaptation only for approved non-stationary scenarios after
   containment, stability, replay, and fallback tests pass.
26. Introduce weakness mining and change manifests as reporting only; require
    reviewed regression cases before the optimizer consumes it.
27. Create scoped `SolutionPattern` evidence and compile compatible
    `SolutionTemplate` candidates from repeated verified outcomes, including
    `CognitiveSkillTemplate(kind="memory_management")` entries that separate
    reusable behavior from stored content; test typed instantiation, model/tool/
    domain compatibility, precondition rejection, reuse benefit, provenance,
    licensing, and negative transfer across held-out goal families before
    activation.
28. Run evaluator adaptation in shadow mode; compare candidate checks and
    tests against immutable mismatch, external-outcome, adversarial, and replay
    corpora without changing the trusted evaluator pointer.
29. Enable scheduled sensor-driven evaluator-evolution runs after sampling,
    anchor/mutation/holdback, alternating-epoch, and promotion tests pass;
    candidates remain versioned and pending until independently approved.
30. Add versioned hybrid and open-only `ModelPortfolioSnapshot` support plus the
    open-weight substrate with immutable base/adapter/delta snapshots, governed
    training-evidence manifests, pluggable recipes, budgeted isolated training,
    and controlled H-versus-W attribution in shadow mode. Represent an optional
    memory specialist as a distinct role/weight binding with a memory-only
    manifest, task-action filtering, two-role receipts, and scaffold fallback.
31. Run the first open-weight campaign as pending-only; require next-batch,
    ID/OOD, replay/forgetting, safety, reward-mismatch, best-over-latest, and
    exact rollback evidence before any weight pointer can change; preserve its
    full checkpoint-by-item response matrix and keep IRT/tiny-benchmark outputs
    advisory until validity and forward-family tests pass. Before any coupled
    memory `H+W` claim, run a separate `W_mem` cell with the optimized scaffold,
    evaluator, task model, inference settings, and seeds frozen and prove that
    task-action targets never entered specialist training.
32. Add the label-free retrospective lane as proposal-only; compare its rankings
   with trusted evaluation and keep it unable to change promotion state.
33. Put all optimizer surfaces into pending-promotion mode before expanding the
   editable surface.
34. Build release bundles and exercise deployment adapters in staging/shadow
    mode; verify reproducible packaging of cognitive-skill scaffolds, action
    broker/schema, compatible task/specialist weights, recipe/evidence
    references, scaffold fallback, capability-envelope preservation,
    release-bound monitoring, authenticated promotion, and exact rollback before
    any production pointer can change.
35. Activate self-improvement only after evaluator/sensor/weight-integrity
    adversarial tests, deterministic-or-variance replay, longitudinal
    transfer/retention,
    evaluator non-self-approval, H/E/W attribution, scoped pattern transfer,
    per-case gating, sealed-holdout separation, best-snapshot selection, and
    rollback are green.

## Non-Goals

- mutating active weights in place, training closed-weight models without an
  authorized provider interface, or launching an open-weight campaign without
  the Phase 9 data, reward, attribution, safety, compute, checkpoint, promotion,
  and rollback contracts;
- autonomous merge, deployment, or production promotion;
- storing raw conversations or tool output indiscriminately;
- treating a long context window as a memory architecture;
- interpreting the Maxwell-demon analogy as permission to erase source
  evidence, conflicts, protected history, or retention obligations; it describes
  selective context-load management, not autonomous truth or deletion;
- treating AutoMem's task-specific Qwen2.5-32B results as general evidence that
  a 32B model “beats Opus,” that one memory scaffold transfers across domains,
  or that persistent cross-episode memory has been solved;
- training a memory specialist on raw successful trajectories, retaining task-
  action commitments in its targets, letting it commit domain actions or bypass
  the broker, or using its own return/review as promotion authority;
- copying or redistributing AutoMem repository code while it has no explicit
  license, or packaging a specialist adapter for a different base/tokenizer/
  chat-tool/domain tuple without compatibility and protected transfer evidence;
- treating Git branches or worktrees as the complete agent-memory architecture,
  or interpreting a fresh explorer conversation as a ban on scoped governed
  memory;
- making a vendor CLI session the source of truth;
- adding embeddings or an external vector database before lexical retrieval is
  measured and insufficient;
- sharing memory across users/projects without an explicit scope and policy;
- replacing predictable fixed workflows with adaptive orchestration by default;
- claiming that GaP's robotics results validate software-harness improvement;
- calling a fixed task-list optimizer “simulation rehearsal” without an explicit
  versioned variation distribution and environment/fixture model;
- treating a simulator, local proxy, LLM judge, leaderboard, or “oracle” label
  as infallible ground truth outside its declared validity contract;
- treating deterministic generated code or reproducible execution as evidence
  that a generated equation, transformation, proof, or factual claim is true;
- treating finite numerical tests or symbolic simplification without preserved
  assumptions and side conditions as a mathematical proof;
- using same-batch or retrospective self-judgment as evidence of forward
  generalization or as authority for persistent promotion;
- assuming the latest harness snapshot is better than the best protected,
  frozen historical snapshot;
- conflating a valid harness update with solver activation, adherence, or
  realized benefit;
- deploying a generated or rehearsed graph without static validation, protected
  evaluation, immutable promotion, and deterministic execution;
- allowing unbounded swarms, runtime code generation, permission expansion, or
  agent-controlled evaluators under the label of emergence;
- copying a whole discovery framework as the Meta-Harness architecture instead
  of implementing the governed native contracts, or making an external runner
  its system of record;
- giving every explorer the same global shared memory, allowing unscoped
  cross-island diffusion, or treating agent-authored notes/skills as active
  knowledge without evidence and lifecycle review;
- evolving arbitrary executable scheduler/controller code, allowing heartbeats
  to mutate authority or suppress protected capture, or switching policy from
  one lucky candidate instead of a frozen evidence window;
- trusting a CORAL, SwarmResearch, SkyDiscover/EvoX, or native discovery proxy
  score as promotion authority; ranking quality by LOC/branch count; or
  automatically merging partial branches without a new candidate and
  protected evaluation;
- vendoring or copying a discovery framework before exact-version,
  dependency/security, and license review; in particular, reusing
  SwarmResearch repository code while its license is unspecified;
- launching weight training from every explorer branch or claiming coupled H/W
  search is attributable without frozen-axis or complete factorial evidence;
- reducing heterogeneous model/harness ability to one universal theta without
  dimensionality evidence;
- treating residual correlation as proof of leakage, distillation, ancestry, or
  causation;
- automatically deleting/relabeling eval items from a statistical flag;
- using adaptive sampling to skip mandatory safety or regression guards;
- letting a solver/harness/weight proposer edit its active or sealed evaluator;
  evaluator evolution runs through the separately scheduled, sampled,
  versioned, meta-evaluated, non-self-approving promotion plane; or
- meta-optimizing the optimizer before the first-order loop is demonstrably
  safe.

## First Implementation Slice

The first implementation PR should contain Phase 0 and the shadow-only portion
of Phase 1:

1. typed `ContextEnvelope`, `ContextSection`, and `ContextManifest` contracts;
2. deterministic section budgeting and compression receipts;
3. a shadow manifest emitted beside existing prompts;
4. invalid-input and determinism tests; and
5. no memory database, retrieval, optimizer behavior, or live prompt change.

This is the smallest slice that creates trustworthy evidence for every later
memory and self-improvement decision while keeping the runtime blast radius
low. Phase 0 now also defines the graph/rehearsal and evolution-evaluation
schemas plus failing contract fixtures, including the memory-cognitive-skill
boundary, but the first PR does not build a memory store/action broker, train or
deploy a memory specialist, build a simulator, native discovery kernel or
optional interoperability adapter, activate graph/search-policy evolution, or
run an evolution campaign; those executable loops remain in later phases after
context, memory, and evaluation evidence are trustworthy.
