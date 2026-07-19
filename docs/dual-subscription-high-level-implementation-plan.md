# Dual-Subscription High-Level Implementation Plan

Status: planning-only execution summary. No capability described here is
implemented merely because it appears in this document.

This plan converts the canonical
[Meta-Harness Project Charter](PROJECT_CHARTER.md) and the detailed
[context, memory, rehearsal, and self-improvement plan](context-memory-self-improving-harness-plan.md)
into a high-level delivery sequence using one Codex subscription and one Claude
Code subscription, with Pi available as an optional conformance-tested
development coding-agent seat. The detailed plan remains authoritative for
product contracts, acceptance criteria, and phase exit gates.

## Outcome

Deliver a protected, reproducible vertical slice that can:

1. build deterministic context and typed memory receipts;
2. run AutoMem-inspired memory management as a reusable cognitive skill;
3. run a native governed discovery kernel composed from CORAL,
   SwarmResearch, and EvoX mechanisms;
4. compare a deterministic scaffold, an optimized scaffold `H`, and an
   optional locally trained memory specialist `H+W_mem` under frozen external
   evaluation `E` that remains outside candidate authority;
5. package the eligible result as a versioned `SolutionTemplate` and
   `HarnessReleaseBundle` with exact fallback and rollback; and
6. park the strongest protected candidate for human promotion rather than
   self-approving or deploying it.

The first credible combined vertical slice is targeted for **8–12 weeks**.
Completing the full 11-phase roadmap is targeted for **4–6 months**, followed
by enough longitudinal evidence to reach charter-grade validation in
approximately **6–9 months total**.

## Charter decision gate

This work advances product-loop stages 2–6: generated harness recommendation,
managed rehearsal and evaluation, governed `H/E/W` evolution, reusable
`SolutionTemplate` creation, and reproducible packaging and operation.

The development Kanban, Linear integration, coding-agent seats, and engineering
multi-agent orchestrator are delivery infrastructure. They help engineers build
those stages but do not themselves satisfy a product-loop stage or exit gate.

The controlling invariants are:

- evidence before learning;
- immutable candidates and frozen first-order `H/E/W` comparisons;
- evaluator non-self-approval and protected per-case evidence;
- bounded worker, memory, training, and deployment authority;
- best protected snapshot over the latest snapshot;
- authenticated human promotion; and
- exact lineage, fallback, and rollback.

## Scope boundaries

### Included in the first vertical slice

- Phase 0 and shadow-only Phase 1 foundations.
- Typed context, memory, action, evidence, and lineage contracts.
- Deterministic context budgeting, compression, manifests, and receipts.
- Scoped persistent memory plus a deterministic `MemoryActionBroker`.
- A scaffold-only LOG/MAINTAIN and CONSULT memory baseline.
- A native discovery MVP with isolated candidate lineages, asynchronous
  supervision, scoped knowledge, explorer/optimizer roles, typed heartbeats,
  and a bounded declarative search policy.
- Protected development, validation, replay, safety, and efficiency views.
- Bounded AutoMem-style scaffold optimization as an `H` candidate lane.
- One optional local `W_mem` LoRA/adapter experiment after the `H` gate passes.
- A reusable memory-management cognitive-skill template and release package.

### Deferred beyond the first vertical slice

- Full evaluator evolution campaigns.
- Broad goal-family and cross-domain transfer claims.
- Unrestricted topology emergence or executable generated controllers.
- Automatic promotion or production deployment.
- Large weight sweeps, full-parameter training, or required cloud GPUs.
- Treating external CORAL, SwarmResearch, or EvoX implementations as the
  system of record. They remain optional conformance and benchmark references.

## Development plane versus product runtime

This plan keeps two systems separate:

- **Development plane:** the engineering workflow used to build this
  repository. It contains Linear, the development Kanban, Git worktrees and
  clones, `RemoteWorkplanGateway`, Codex, Claude Code, Pi, and the external
  engineering multi-agent orchestrator that may launch those coding agents.
- **Product runtime plane:** the Meta-Harness capabilities being implemented,
  including its `Router`, `TaskExecutor`, `WorkflowEngine`,
  `CodingAgentWorker`, discovery supervisors, evaluators, memory components,
  and release packages.

Every owner percentage, workplan claim, Linear transition, and coding-agent
seat below belongs to the development plane. Existing product runtime classes
do not claim development cards, do not hold the development gateway token, and
do not prove that a development integration exists. Conversely, the
development orchestrator, Linear adapter, and development claim store are not
candidate runtime capabilities and are excluded from a `HarnessReleaseBundle`.

Dogfooding the product runtime to help develop a later product version is a
separate controlled experiment. It requires a released baseline, a new
development coordination epoch, explicit authorization, independent evidence,
and exact fallback; it is not the bootstrap path in this plan.

## Development execution model

Implementation uses the Codex and Claude Code development CLIs authenticated by
their subscriptions, with separate Git worktrees or clones and explicit file
ownership. Pi is an optional third development seat launched by the external
engineering multi-agent orchestrator. It does not count as usable development
capacity until its launcher passes the same claim, path, fencing, evidence,
and recovery conformance suite. Development-agent and model credentials remain
local to each trusted development host; they are never copied into Linear, a
task bundle, the repository, or another agent's credential home. No
development seat writes concurrently to the same worktree or approves its own
result.

| Owner | Approximate share | Primary responsibility |
|---|---:|---|
| Codex | 45% | Core contracts, context and memory substrate, action broker, receipts, protected evaluator integration, local MLX/LoRA pipeline, package integration, and regression tests. |
| Claude Code | 40% | Campaign supervisor, lineage workspaces, discovery knowledge hub, explorer/optimizer/scheduler views, typed heartbeats, declarative search-policy lane, and adversarial review. |
| Joint and sequential gates | 15% | Interface freeze, cross-review, protected ablations, integration, release evidence, rollback rehearsal, and human promotion package. |
| Pi development seat, after conformance | No fixed share | Optional overflow, bounded implementation, and independent reproduction on development cards whose tools, host, paths, and budget are explicitly compatible. This seat does not reduce protected product gates or the baseline schedule. |

Agent review is engineering evidence, not promotion authority. Frozen tests,
protected evaluators, immutable receipts, and the authenticated human decision
remain authoritative.

## Development Kanban coordination adapted from Kraken

Kraken demonstrates a useful pull-based pattern: one canonical JSON state,
one generated Markdown board, atomic claims, distinct owner namespaces,
one-card WIP, dependency and path-conflict guards, optimistic revision checks,
evidence-based progress updates, explicit blocking, and automatic path release
at Done. This repository's engineering workflow adopts those mechanics as its
development coding-agent coordination baseline, not as a Meta-Harness runtime
feature or new product-approval authority.

The lifecycle is:

```text
backlog -> ready -> claimed -> in_progress -> review -> verifying -> done
                         \-> blocked -> in_progress
                         \-> cancelled
```

Only the development coordinator prepares and moves a card to Ready. A direct
Codex or Claude Code development seat, or an external engineering multi-agent
orchestrator, pulls an eligible Ready card through an owner-aware integration.
Stable claim owners include `codex:<host>:<session>`,
`claude:<host>:<session>`, and `dev-orchestrator:<host>:<run>`. An
engineering-orchestrator claim may delegate the card to a Pi development seat
such as `pi:<host>:<session>`, but that delegated identity is evidence, not a
second claim or a gateway credential. A failed claim, stale revision, unmet
dependency, WIP violation, or path overlap is a lost race or hard blocker; the
development seat refreshes the board and makes no owned-path edit.

Planned coordination artifacts are:

- `.workplan/state.json`: canonical versioned card state in same-host mode and
  a generated audit snapshot in distributed mode;
- `WORKPLAN.md`: human-readable board generated from canonical state;
- `scripts/workplan.mjs`: the only mutation interface and atomic lock owner in
  same-host mode;
- a development-only `RemoteWorkplanGateway` with a transactional claim store,
  fenced claim tokens, a remote MCP facade, and a Linear projection adapter;
- repository-local Codex and Claude Code workplan skills plus an engineering
  orchestrator adapter that launches and supervises a Pi development seat; and
- concurrency and transition tests covering claims, dependencies, WIP, paths,
  stale revisions, lock recovery, projection, blocking, integration, and Done
  release.

### Worktree-aware correction

Kraken's current lock explicitly coordinates only processes using one physical
checkout. Meta-Harness keeps the safety of separate code worktrees without
forking the Kanban ledger:

- one coordinator-owned primary checkout holds the canonical state, generated
  board, and lock;
- every wrapper, including one launched inside a code worktree, targets that
  explicit shared control root;
- the wrapper verifies the primary checkout and code worktree share one Git
  common directory and host; different clones or hosts use the distributed
  claim protocol below instead of the filesystem lock;
- each claimed card records its owner, owned paths, worktree path, branch, base
  commit, current head, applicable `H/E/W` plane, budget, acceptance evidence,
  and next checkpoint;
- owned paths are exclusive across all worktrees, not merely inside the local
  filesystem view;
- the owner freezes the branch at Review, retains paths through integration
  and post-integration Verification, and reaches Done only after the integrated
  commit and receipts are recorded; and
- high-contention root, charter, evaluator, release, and integration paths stay
  coordinator-owned unless an ordered shared-edit handoff is recorded.

A stale timestamp never authorizes reclamation. The coordinator must record
reassignment. Blocked cards explicitly retain or release paths, while Done and
Cancelled release them atomically. Worktrees represent candidate code state
and lineage; they are not agent memory or evidence authority.

### Linear-backed cross-host development coordination

Linear is the distributed development board and coding-agent activity surface,
not the lock and not a Meta-Harness runtime component. Its official remote MCP
server supports both Claude Code and Codex, and its agent API can expose a
development app as an issue delegate with visible sessions, plans, and
activities. Those agent APIs are still a Developer Preview, and the published
`IssueUpdateInput` exposes ordinary field updates but no expected revision or
compare-and-set precondition. The development workflow therefore does not
treat two competing Linear or MCP issue updates as an atomic claim.

Pi is not a special Linear authority. Pi intentionally has no built-in MCP, so
the external engineering multi-agent orchestrator claims through the
development gateway, launches Pi as a coding process, supervises it, and
projects its development evidence through the gateway's Linear app.

The authority split is explicit:

- Linear owns the human-authored task definition, Backlog/Ready intent,
  assignee, visible workflow, discussion, and projected progress;
- the coordinator-owned claim store owns executable revision, definition hash,
  owner/session/host identity, one-card WIP, dependency state, exclusive path
  reservations, monotonic fencing token, and transition receipts; and
- `.workplan/state.json` and `WORKPLAN.md` are deterministic, secret-free audit
  projections in distributed mode rather than competing mutation authorities.

Exactly one development coordination backend is active for an engineering
epoch. Switching between the filesystem backend and the remote gateway
requires zero active claims, a reconciled snapshot, and a coordinator-recorded
epoch increment; an outage never triggers automatic split-brain failover.

The cross-host path is:

```text
Linear Ready card
  -> atomic RemoteWorkplanGateway development claim
  -> fenced task bundle
  -> host-local clone/worktree
  -> direct Codex/Claude Code seat or engineering orchestrator -> Pi CLI
  -> evidence/checkpoint/branch receipts
  -> claim store transaction
  -> Linear development activity and board projection
```

Direct Codex and Claude Code development seats call the gateway's remote MCP
tools to list, claim, update, block, submit, and complete work. For Pi, the
external engineering orchestrator uses the same gateway contract and launches
Pi only after a successful claim. The engineering orchestrator holds the claim
ID, fencing token, and short-lived gateway credential; Pi receives the
development task bundle and claimed worktree only. A seat may create or bind
its worktree only after a successful transaction against the expected card
revision and definition hash. Direct Linear access from coding-agent
credentials is a read-only convenience at most; it never establishes
ownership.

The development Linear adapter holds one least-privilege OAuth app identity. It
is the issue delegate while a human remains the assignee, and it projects the
actual coding agent, host, session, branch or pull request, checkpoint, and
evidence as development activities. The OAuth token stays in the gateway. Each
direct seat or engineering orchestrator instead uses a short-lived host
credential for the gateway and keeps its coding-agent and model-provider
authentication on that development host.

Claims heartbeat with their fencing token. If a host, broker, or network link
is lost, the direct seat or engineering orchestrator stops owned-path mutation
after its bounded grace period and the card becomes attention-required. Expiry
can fence a stale seat, but it cannot make another seat the owner: only a
recorded coordinator requeue or reassignment can do that. A stale token cannot
update, submit, integrate, or complete a card.

Linear webhooks enter an idempotent inbox after HMAC and timestamp validation;
the unique delivery ID prevents duplicate application. The receiver
acknowledges quickly, while an outbox projects committed broker transitions to
Linear. Because Linear retries failed deliveries only a bounded number of
times, a reconciliation job detects missed, delayed, revoked, or manually
edited state. A protected definition change after claim freezes the card at
the next gateway contact until the coordinator revalidates it.

Required cross-host tests prove:

- two distinct host identities racing one Ready card produce one claim and one
  fencing generation;
- delayed or failed Linear projection cannot create a second owner;
- duplicate, reordered, invalid-signature, and missed webhook events are
  harmless or reconciled;
- stale fencing tokens, lost gateway access, OAuth revocation, and removed team
  permission fail closed;
- path conflicts hold across different clones and operating systems; and
- Review, integration, Verification, Done, blocking, and reassignment retain
  the same evidence and release semantics as same-host mode.

Linear's own hosted coding sessions and direct Codex-for-Linear cloud tasks are
optional development execution lanes, not substitutes for this protocol. They
may be useful later, but Linear-hosted coding sessions consume Linear AI
credits and neither native lane supplies the repository path-reservation and
fenced development-claim receipts required here.

#### Pi development seat through the engineering multi-agent orchestrator

**ACCEPT:** Pi is an additional coding agent used to develop Meta-Harness. The
external engineering multi-agent orchestrator owns the development claim and
supervises the Pi process; neither component is the Meta-Harness product
runtime.

The development path is:

1. the engineering orchestrator atomically claims one Ready development card
   from `RemoteWorkplanGateway` as `dev-orchestrator:<host>:<run>`;
2. it creates or binds the fenced development worktree and launches one exact,
   supported Pi version with the immutable development task bundle;
3. Pi edits only the claimed repository paths and returns process events,
   summary, branch head, diff, and test evidence to the engineering
   orchestrator;
4. the engineering orchestrator heartbeats the claim, cancels the Pi process
   on fencing or gateway loss, and rejects malformed, incomplete, stale, or
   policy-violating output; and
5. only the engineering orchestrator checkpoints, blocks, or submits through
   the development gateway, after which the Linear adapter projects the
   development activity.

Pi receives neither the Linear OAuth token nor the development gateway
credential. No Pi MCP or Linear extension is required: the engineering
orchestrator can supervise Pi through its documented headless JSON or RPC
process interface. Direct Pi Linear mutation and third-party Linear packages
are rejected for ownership, completion, and reassignment transitions.

`TASK-20260714-011` builds the engineering-orchestrator Pi seat adapter. The
repository does not currently contain evidence that this development adapter
exists. The product runtime's existing `CodingAgentWorker(cli="pi")`,
`Router`, `TaskExecutor`, and their tests are separate product capabilities;
they neither implement nor validate this development lane. A neutral process
launcher may be shared only behind separate development and runtime contracts;
the development path must not invoke the product router or workflow engine.

The first development adapter may use strict JSON one-shot mode with external
process cancellation. RPC is optional if the engineering orchestrator needs
mid-run steering or Pi's explicit settled signal. In either mode it must pin
the Pi version, disable unreviewed resource discovery, set explicit project
trust and tool allowlists, validate every terminal condition, bind process-tree
termination to fencing loss, and emit deterministic development receipts.

With no repository-local development adapter yet identified, the earlier
2–3-day estimate was unsupported. The planning estimate is **3–5 engineering
days after `TASK-20260714-010`**, assuming the engineering orchestrator already
offers a generic process-seat interface. If it does not, split and re-estimate
the orchestrator transport before moving task 011 to Ready.

Pi does not provide a built-in sandbox. An unattended Pi development seat
therefore runs inside a container, VM, micro-VM, or equivalent policy-controlled
boundary with only the claimed development worktree, minimum network access,
and explicitly approved model credential mounted. Pi extensions and packages
are disabled unless separately reviewed and pinned because they execute with
the Pi process's full permissions. The locally installed Pi `0.80.6` is the
initial development conformance target, not an unpinned moving dependency.

Pi development conformance must prove:

- direct Codex, direct Claude Code, and an engineering-orchestrator Pi seat
  racing one Ready development card yield one gateway winner and one fencing
  generation;
- Pi cannot start before the engineering orchestrator owns the claim or
  continue mutating after cancellation, gateway loss, or fencing expiry;
- malformed or partial JSONL, duplicate events, missing terminal state, crash,
  timeout, abort, and host restart cannot produce a false Done transition;
- prompts, results, receipts, logs, and task bundles expose no Linear, gateway,
  Codex, Claude, or Pi credential store; and
- development conformance imports no product runtime orchestrator, router,
  executor, evaluator, memory, or promotion authority.

Current integration basis, verified 2026-07-14:
[Linear MCP](https://linear.app/docs/mcp),
[Linear agent API](https://linear.app/developers/agents),
[Linear webhook contract](https://linear.app/developers/webhooks),
[public GraphQL schema](https://raw.githubusercontent.com/linear/linear/refs/heads/master/packages/sdk/src/schema.graphql),
[Codex in Linear](https://learn.chatgpt.com/docs/third-party/linear), and
[Claude Agent SDK subscription usage](https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan), plus Pi's official
[usage and modes](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/usage.md),
[RPC contract](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/rpc.md),
[extension API](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/extensions.md),
[package security model](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/packages.md),
[sandbox guidance](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/security.md), and
[provider authentication](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/providers.md).

### Definition of Ready

A card becomes Ready only when it has:

- a stable task ID, priority, one bounded outcome, and exact owned paths;
- a source-system revision and immutable definition hash;
- dependencies and an applicable charter/product-loop trace;
- its primary `H`, `E`, or `W` plane plus the axes that remain frozen;
- objective acceptance commands, receipts, and evaluator authority;
- a compatible owner capability and explicit budget/stop condition;
- worktree/base-commit requirements and a first checkpoint; and
- no overlap with another Ready, active, or path-retaining Blocked card.

Kanban Ready coordinates execution only. It cannot waive the charter gate,
activate memory, approve an evaluator, promote a candidate, merge a protected
release, expand authority, or deploy.

### Seed card order

The Kanban bootstrap creates these Backlog cards, then the coordinator fills
exact paths and evidence before moving them to Ready:

| Card | Priority | Preferred lane | Outcome | Dependencies |
|---|---:|---|---|---|
| `TASK-20260714-001` | P0 | Coordinator/Codex | Build and test the worktree-aware atomic Kanban control root. | None |
| `TASK-20260714-010` | P0 | Coordinator/Codex | Add the transactional remote claim gateway, Linear projection/app adapter, remote MCP facade, fencing, and cross-host race/recovery tests. | `TASK-20260714-001` |
| `TASK-20260714-011` | P1 | Coordinator/Codex | Add a Pi development seat to the external engineering multi-agent orchestrator and prove development claim, JSON/RPC, cancellation, isolation, and three-seat conformance without importing product runtime control. | `TASK-20260714-010` |
| `TASK-20260714-002` | P0 | Codex | Freeze typed context, evidence, memory, lineage, and `H/E/W` contracts plus shadow manifests. | `TASK-20260714-010` |
| `TASK-20260714-003` | P1 | Claude Code | Red-team the contract boundaries and add disjoint invalid-input, authority, and determinism fixtures. | `TASK-20260714-002` |
| `TASK-20260714-004` | P1 | Codex | Implement the typed memory substrate, deterministic scaffold, broker, and receipts. | `TASK-20260714-002`, `TASK-20260714-003` |
| `TASK-20260714-005` | P1 | Claude Code | Implement the isolated discovery supervisor, lineage recovery, knowledge hub, and role contexts. | `TASK-20260714-002`, `TASK-20260714-003` |
| `TASK-20260714-006` | P1 | Claude Code | Add typed heartbeats, population scheduling, and declarative search-policy candidates. | `TASK-20260714-005` |
| `TASK-20260714-007` | P0 | Codex | Integrate protected evaluation and run the scaffold `H` ablation. | `TASK-20260714-004`, `TASK-20260714-006` |
| `TASK-20260714-008` | P1 | Codex | Run the conditional local `W_mem` experiment under frozen `H/E/task` contracts. | `TASK-20260714-007` and passing `H` gate |
| `TASK-20260714-009` | P0 | Coordinator/joint | Integrate, cross-review, package, rehearse rollback, and prepare the human promotion bundle. | `TASK-20260714-007`; `TASK-20260714-008` only if eligible |

Preferred lanes guide card preparation; an actual owner exists only after an
atomic successful claim. A reviewer stays read-only unless it claims a separate
non-overlapping review or fixture card.

## Delivery sequence

### Milestone 0 — Baseline and contracts, weeks 1–2

Codex owns the initial contract spine. Claude Code independently reviews the
contracts, threat boundaries, invalid-input fixtures, and observable exit
criteria before runtime behavior changes.

Deliver:

- the worktree-aware atomic Kanban state, generated board, owner wrappers, and
  concurrency/transition test suite;
- the cross-host claim gateway, Linear projection, remote MCP facade, fenced
  development-seat credentials, webhook inbox/outbox, and reconciliation path;
- the non-blocking engineering-orchestrator-to-Pi development integration and
  three-seat conformance suite after the remote gateway passes its own tests;
- seeded Backlog cards with the first authorized, non-overlapping slice moved
  to Ready by the coordinator;
- versioned context, memory, evidence, lineage, and `H/E/W` identifiers;
- failing boundary fixtures and determinism tests;
- deterministic context budgets, compression receipts, and shadow manifests;
- the worktree ownership and cross-review protocol; and
- fixed development, validation, replay, and sealed-evidence boundaries.

Exit gate: shadow manifests reproduce unchanged live prompts; invalid inputs
fail closed; every context transformation is deterministic and receipted; and
simultaneous Codex/Claude claims from separate same-host worktrees and distinct
host identities each produce one winner against their selected backend; the Pi
lane remains disabled until direct Codex, direct Claude Code, and an
engineering orchestrator backed by Pi also produce one development-gateway
winner in a race; and WIP, dependency, path, revision, fencing, webhook, and
transition violations leave authoritative state unchanged.

### Milestone 1 — Parallel memory and discovery foundations, weeks 3–6

Codex implements the typed memory substrate, deterministic scaffold, and
brokered memory actions. Claude Code implements the isolated discovery
supervisor, knowledge hub, lineage recovery, and initial role contexts in its
own worktree.

Deliver:

- scoped persistent memory with source, trust, lifecycle, and rollback;
- `MemoryCognitiveSkillSnapshot`, `MemoryActionBroker`, and action receipts;
- deterministic LOG/MAINTAIN and CONSULT behavior with scaffold-only fallback;
- `CampaignSupervisor`, `LineageWorkspaceManager`, and
  `DiscoveryKnowledgeHub` MVPs;
- fresh explorer, lineage-aware optimizer, and compact scheduler contexts; and
- crash, timeout, restart, resume, and cross-scope leakage tests.

Exit gate: memory survives restart without widening scope, and an asynchronous
campaign can create isolated candidates and recover without corrupting shared
state or accessing protected evaluation internals.

### Milestone 2 — Governed discovery composition, weeks 5–8

Claude Code leads the scheduler, heartbeat, and declarative policy surfaces.
Codex integrates their receipts with the evaluator, evidence, and packaging
spine. The overlap with Milestone 1 is intentional but uses disjoint files and
frozen interfaces.

Deliver:

- CORAL-style typed reflection, consolidation, and redirection heartbeats;
- SwarmResearch-style parent, explorer/optimizer, briefing, width, and depth
  decisions with diversity floors and baseline reseeding;
- an EvoX-style population descriptor, strategy history, stagnation trigger,
  and declarative `SearchPolicyDSL` candidate lane;
- static, simulation, shadow, and exact-fallback validation for policy changes;
  and
- budget-matched discovery ablation fixtures.

Exit gate: every search decision has a versioned receipt; heartbeats and search
policies can propose candidates but cannot activate memory, edit evaluators,
train weights, promote, merge, or deploy.

### Milestone 3 — AutoMem scaffold `H` and protected evaluation, weeks 7–9

Codex leads the scaffold candidate lane and protected evaluation integration.
Claude Code reviews full trajectories, proposes bounded counter-hypotheses, and
tests delayed-memory and convergence failure cases.

Deliver:

- complete memory trajectories joined to task outcomes and action receipts;
- bounded `H` candidates covering phases, prompts, schemas, validators,
  actions, routing, and context budgets;
- development diagnosis separated from validation selection and sealed
  confirmation;
- no-external-memory, base-scaffold, and optimized-scaffold comparison cells;
  and
- per-case task benefit, activation, adherence, transfer, retention, privacy,
  safety, latency, and rollback evidence.

Exit gate: an optimized `H` candidate must improve at least one approved target
without regressing any mandatory validation or replay case. If it does not,
stop the weight lane and report the closest protected result and unresolved
gap.

### Milestone 4 — Optional local `W_mem`, weeks 9–11

This milestone begins only after the scaffold gate passes. The M5 Max with
128 GB unified memory is the default training and inference worker; external
GPU use is optional compatibility or acceleration work, not a prerequisite.

Deliver:

- an immutable memory-skill training manifest;
- deterministic removal of task-action commitments and protected answers from
  memory-specialist targets;
- one or more isolated local LoRA/adapter candidates;
- frozen-scaffold `H+W_mem` comparisons against the base and optimized `H`
  cells; and
- adapter lineage, compatibility, fallback, and exact rollback receipts.

Exit gate: any claimed `W_mem` gain is attributable under a frozen scaffold,
task model, evaluator, data partition, inference contract, and seed schedule.
Otherwise retain the stronger scaffold-only result.

### Milestone 5 — Reuse, package, and handoff, weeks 11–12

Both agents cross-review the integrated candidate. Protected evaluation and a
human remain the only promotion path.

Deliver:

- `CognitiveSkillTemplate(kind="memory_management")` with compatibility,
  evaluator, test, licensing, and negative-transfer obligations;
- a `SolutionTemplate` candidate with provenance and counterexamples;
- a reproducible `HarnessReleaseBundle` containing exact model, scaffold,
  broker, evaluator, policy, dependency, health, fallback, and rollback
  references;
- a clean-room install and rollback rehearsal; and
- a final evidence report separating implemented capability, measured result,
  limitation, and deferred work.

Exit gate: a fresh environment reproduces the selected candidate and rollback;
no candidate, agent, framework grader, or learned evaluator can promote or
deploy itself.

## Cost envelope

The estimate assumes both CLIs authenticate through subscriptions, the M5 Max
performs local inference and adapter training, subscription overage auto-top-up
is disabled, and work pauses for a limit reset instead of silently switching to
API billing. Remote hosts do not multiply subscription capacity; they share the
same account limits. Pi is an additional development coding-agent seat behind
the engineering orchestrator, not product runtime capacity or a third pool of
included model capacity; its development runs consume whichever explicitly
approved provider and budget the development coordinator assigns. Product
runtime inference and training costs are measured separately by release and
campaign evidence.

| Subscription configuration | Monthly cost | Use |
|---|---:|---|
| Codex Plus + Claude Pro | $40 | Budget option, but not schedule-reliable for sustained work on this repository. |
| Codex Pro 5x + Claude Max 5x | **$200** | Recommended baseline for the 8–12 week vertical slice. |
| Codex Pro 20x + Claude Max 20x | $400 | Maximum headroom; likely saves only one or two weeks because protected gates remain sequential. |

At the recommended tiers:

- existing subscriptions plus a self-hosted claim gateway: **$0 incremental
  project spend is possible**;
- first vertical slice: **$400–$600** of subscription renewals over 2–3 months;
- full 11-phase implementation: **$800–$1,200** over 4–6 months; and
- external GPUs and pay-as-you-go APIs: **$0 required**.

That zero-increment case assumes interactive subscription CLIs or automation
that remains inside each provider's included allowance. Codex account auth on a
trusted private runner is an advanced path and each saved `auth.json` copy must
be serialized and protected like a password. As of June 15, 2026, unattended
Claude `-p`/Agent SDK work draws from a separate per-user monthly credit rather
than the interactive Claude Code allowance; after that credit, it stops or uses
separately enabled usage credits. Linear's custom agent does not require a
billable user, but its hosted coding sessions draw from Linear AI credits and
are excluded from the zero-increment estimate.

Do not assume a Pi login has the same economics as the vendor CLI whose model
it reaches. Pi documents ChatGPT subscription authentication, but that usage
still shares the account's capacity. Pi documents Claude Pro/Max third-party
harness traffic as separately metered extra usage. The Pi development seat is
therefore disabled unless its provider route, terms, credential handling, and
hard budget are recorded; any metered Pi development traffic is outside the
zero-increment estimate.

Prices are USD before regional taxes and should be rechecked before renewal:
[OpenAI plan guidance](https://help.openai.com/en/articles/9793128-about-chatgpt-pro-plans)
and
[Anthropic Claude Code plan guidance](https://support.anthropic.com/en/articles/11145838-using-claude-code-with-your-pro-or-max-plan).

## Schedule assumptions and controls

The 8–12 week estimate assumes:

- one human can review scope, gates, and merges for approximately one to two
  hours per working day;
- both subscriptions have sufficient included capacity for sustained work;
- the Pi development seat is optional capacity only after its exact version,
  provider route, hard budget, engineering-orchestrator cancellation path, and
  sandbox pass conformance;
- agents work from small, isolated branches with one primary owner per file;
- all agents mutate claims through one selected coordinator-owned backend—the
  shared filesystem root in same-host mode or the remote gateway in distributed
  mode—and never claim by directly editing Linear;
- product runtime components never read or mutate the development board, and
  dogfooding is excluded unless separately authorized under a new coordination
  epoch;
- interface contracts freeze before parallel implementation begins;
- every milestone lands as reviewable, tested increments rather than one large
  merge; and
- no research result forces a charter revision or major architecture reset.

Two subscriptions do not halve elapsed time. Contract freeze, protected
evaluation, `H/E/W` attribution, integration, human promotion, and longitudinal
evidence remain sequential by design.

Controls:

- stop or narrow scope when subscription limits, protected regressions,
  contamination, safety failures, or attribution ambiguity invalidate a cell;
- never resolve schedule pressure by weakening evaluator isolation, receipts,
  rollback, or promotion authority;
- prefer the deterministic scaffold-only result whenever the learned
  specialist is unsupported, slower without benefit, or not attributable; and
- report the closest eligible result and unresolved gap instead of claiming
  roadmap completion.

## Full-roadmap continuation

After the first vertical slice, months 4–6 complete the remaining detailed
phases: evaluation intelligence, rehearsal-based graph optimization, bounded
adaptive orchestration, durable weakness mining, evaluator-candidate
governance, broader open-weight recipes, observability, deployment adapters,
and operational hardening.

The code-complete milestone is not the scientific finish line. Continued
next-batch, ID/OOD, replay, retention, transfer, safety, cost, and rollback
evidence extends the charter-grade validation horizon to approximately 6–9
months total.

## First action

Begin with `TASK-20260714-001` only: preserve the current planning changes,
select an approved implementation baseline, build and prove the shared
worktree-aware Kanban root, then implement `TASK-20260714-010` as a separate
adapter and cross-host conformance card. Only after both local and distributed
single-winner claims pass should the coordinator qualify the remaining Backlog
cards. `TASK-20260714-011` can then add Pi behind the engineering orchestrator
as a non-blocking development lane; it does not delay `TASK-20260714-002`, and the
engineering orchestrator may launch Pi only after the three-seat development
conformance suite passes. Only after an atomic claim succeeds should the
development coordinator create or bind the direct-seat or engineering-
orchestrator worktree, freeze the initial product contracts, and land Phase 0
plus shadow-only Phase 1. Do not use the product runtime as the development
orchestrator, add a memory database, change live prompts, start a discovery
campaign, or train `W_mem` before those gates pass.
