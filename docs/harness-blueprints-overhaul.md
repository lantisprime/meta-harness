# Harness Blueprints Overhaul

Status: approved architecture and implementation plan  
Tracking issue: [#29](https://github.com/lantisprime/meta-harness/issues/29)  
Last updated: 2026-07-12

## 1. Outcome

MetaHarness will evolve from a one-time workflow wizard into a reusable harness platform.
Users will be able to:

- choose a built-in harness or create a custom harness;
- configure it through a human-friendly guided builder;
- save, version, fork, tune, evaluate, run, and manage it;
- assign work automatically by role/capability or explicitly pin a step to an agent;
- connect MCP capabilities without leaving and losing their place in the builder;
- see which agent is handling each step and what it is doing during a run;
- rerun or edit a completed harness without rebuilding it;
- launch the same harness from MetaHarness, Codex, Claude Code, Pi Agent, or OpenCode;
- package the harness as a secret-free portable artifact; and
- generate deployment artifacts for OCI/Docker, AWS, Google Cloud, and Azure.

The central product object is a **Harness Blueprint**. A saved-harness run names one exact
blueprint version. An ad-hoc or legacy run has no blueprint reference; its immutable embedded
workflow snapshot and SHA-256 digest are its identity.

## 2. Terminology

| User-facing term | Meaning |
| --- | --- |
| Harness | A reusable orchestration users can save, manage, and run. |
| Harness Blueprint | The versioned definition of a harness. |
| Harness Library | The screen that lists and manages built-in and user-owned harnesses. |
| Stage | A step in the harness. The internal API may continue to use `StepSpec`. |
| Agent | A worker that performs a stage. |
| Capability | A human-readable permission or ability, backed by exact built-in or MCP tools. |
| Eval Suite | Versioned cases and policies used to evaluate one or more blueprint versions. |
| Run | One immutable execution of an exact blueprint version. |
| Package | A secret-free, portable bundle containing a blueprint and deployment/launcher targets. |

“Workflow” remains an internal compatibility term and may appear in Advanced/YAML mode.
Normal product copy should use “harness,” “blueprint,” and “stage.”

## 3. Design principles

1. **Guided by default, exact when needed.** Friendly concepts lead; raw tool IDs and YAML
   remain available in Advanced mode.
2. **Immutable published versions.** Editing creates a new blueprint version. A past run is
   never changed by a later edit.
3. **Exact, replayable runs.** Every run records the exact blueprint version and embeds the
   fully resolved workflow snapshot in its journal.
4. **One executor.** MetaHarness executes the orchestration spine. Codex, Claude Code, Pi,
   and OpenCode are launchers and/or inner workers; they do not reinterpret the blueprint.
5. **Capabilities are explicit.** Tools are granted per stage and unavailable capabilities
   are surfaced before execution. Nothing is silently dropped.
6. **Human control over effects.** MCP calls, external side effects, promotion, and cloud
   deployment remain approval-gated.
7. **Secrets stay local.** Blueprints and packages reference logical bindings, never OAuth
   tokens, API keys, mailbox credentials, or local auth files.
8. **Surgical delivery.** The overhaul ships through compatible, testable slices rather than
   replacing the current engine or editor wholesale.

### 3.1 Artifact lifecycle contract

Three objects have deliberately different mutability:

- **Catalog entry** — stable ID plus mutable display metadata and archive state;
- **Draft** — mutable working copy with a server ID, revision number, optional base version,
  owner, and timestamps;
- **Published version** — immutable numbered snapshot.

Saving updates a draft. Publishing converts the current draft content into the next immutable
version. Editing a published version creates a draft based on that version. Concurrent draft
updates use an expected revision/ETag and fail with `409 Conflict` instead of last-write-wins.
Published harnesses are archived/restored, not physically deleted. Only unpublished drafts may
be hard-deleted. Historical references remain resolvable through tombstoned catalog metadata.

### 3.2 Run identity contract

- Saved run: exact `ArtifactRef` plus embedded normalized snapshot and digest.
- Ad-hoc/legacy run: `blueprint_ref: null` plus embedded normalized snapshot and digest.
- Resume and packaging always use the embedded snapshot, never catalog “latest.”
- Catalog deletion/archive cannot change or prevent replay of a historical run.

### 3.3 Readiness contract

Readiness is a side-effect-free server operation, not a client guess. It resolves inputs,
agents, capabilities, tools, MCP load state, secret bindings, eval policy, and optional target
compatibility. It returns typed issues with code, severity, stage ID, message, and repair action.
`POST /api/runs` repeats readiness atomically before creating a journal so stale browser state
cannot bypass it.

Initial issue codes include `invalid_input`, `missing_tool`, `unloaded_mcp`, `missing_worker`,
`no_eligible_worker`, `missing_secret_binding`, `unsafe_eval_tool`, and `cloud_incompatible`.

### 3.4 Secret and sensitive-input contract

Blueprint inputs are typed. Secret inputs accept only a logical `SecretBindingRef`; they cannot
have literal defaults or literal runtime values. The server resolves bindings at the last
possible moment and passes values directly to the authorized provider/tool without placing
them in context, events, errors, journals, eval cases, launchers, or packages.

Ordinary user content may itself be sensitive and is retained as run data; MetaHarness cannot
reliably infer that arbitrary prose is a credential. Product copy and API documentation must
state this boundary. Provider OAuth tokens, API keys, cloud credentials, and declared secret
inputs are accepted only through binding/configuration APIs, never ordinary context.

### 3.5 Capability eligibility contract

Logical roles and capabilities come from a versioned vocabulary. Agent profiles advertise
stable role/capability IDs through local administrator/user configuration and the registered
worker profile. Stage tool grants never make an agent capability claim true, and MCP metadata
never attests agent capability.

Eligibility is deterministic: active identity AND matching task type AND requested role AND
all required capabilities AND routing authority (tier or exact pin). Matrix ranking, cost, and
exploration operate only inside the eligible set. The readiness response explains which
predicate failed.

## 4. Product information architecture

Top-level navigation becomes:

- **Home** — status, recent harnesses, active work, and primary next action;
- **Library** — browse and manage harnesses;
- **Run** — guided create/run journey using the selected blueprint;
- **Settings** — providers, agents, MCP connections, and local bindings;
- **Console** — run history, approvals, audit evidence, and tuning;
- **Help** — concepts and operational guidance.

### 4.1 Revised landing page

The landing page must answer “what can I do now?” without requiring prior harness knowledge.

Required sections:

- primary action: **Create a harness**;
- secondary action: **Open Harness Library**;
- recent harnesses with **Run** and **Edit** actions;
- active runs and approvals needing attention;
- latest result;
- self-tuning status;
- empty-state explanation: “Create once, then run it again with new inputs.”

The existing operational alerts remain higher priority than promotional content.

### 4.2 Harness Library

The Library lists:

- built-in blueprints;
- user-created blueprints;
- forks of built-ins;
- tuned variants;
- draft and published state;
- latest version, last updated time, stage count, capabilities, eval health, and last run.

Per-harness actions:

- **Run** — opens review/readiness; it never executes immediately;
- **Edit** — opens the existing plan editor with blueprint identity attached;
- **Fork** — creates an independent user-owned draft;
- **Evaluate** — runs or reviews the blueprint’s eval suite;
- **Tune** — starts a tuning proposal against a frozen suite version;
- **Package** — exports local-agent and cloud targets;
- **Versions** — inspects and compares immutable versions;
- **Rename/archive/restore** — manages mutable catalog metadata without changing versions;
- **Delete draft** — permanently removes only an unpublished draft after confirmation.

Built-in blueprints are immutable. Editing a built-in creates a personal fork. Archiving a
blueprint never deletes published versions, lineage, eval reports, packages, historical runs,
or their embedded snapshots.

### 4.3 Create and edit journey

The existing plan editor remains the underlying editor. The overhaul extends it instead of
creating a second incompatible editor.

Guided creation flow:

1. **Purpose and inputs**
   - name and description;
   - desired outcome;
   - input fields, defaults, and validation;
   - optional frontier-agent draft.
2. **Global capabilities**
   - select friendly capability bundles;
   - connect missing MCP servers inline;
   - show side effects and approval requirements.
3. **Stages**
   - outcome/delegation contract;
   - automatic agent role/capability requirements;
   - exact named-agent pinning only after hard-pin enforcement is available;
   - capabilities and exact tools;
   - success verification and eval coverage;
   - dependencies, branching, and parallel readiness;
   - retries, escalation, budget, and approvals.
4. **Flow review**
   - a compact linear/branch/parallel visualization;
   - missing inputs, tools, agents, checks, and unsafe effects;
   - explicitly labeled cost/latency estimates with source and confidence, or “unknown.”
5. **Save and run**
   - save a new or existing draft;
   - publish the draft as a new immutable version;
   - run once without saving;
   - save changes and run.

Advanced mode exposes the normalized schema and YAML. Switching modes must preserve one
canonical in-memory draft and must not create divergent editors.

## 5. Capability model

The guided UI exposes friendly bundles while the persisted workflow continues to store exact
tool identifiers.

Initial bundles:

| Capability | Typical backing tools |
| --- | --- |
| Read workspace | `read_file`, `list_files` |
| Search workspace | `grep`, `list_files` |
| Change files | `read_file`, `write_file`, `edit_file`, `grep`, `list_files` |
| Use the web | `web_fetch` and configured web-search MCP tools |
| Browser automation | configured Playwright MCP tools |
| Calculate | `calculator` |
| Read email | configured Gmail/mail MCP read tools |
| Draft email | composition tools without send permission |
| Send email | send tools plus a required approval gate |
| Read calendar | configured calendar read tools |
| Change calendar | calendar write tools plus a required approval gate |
| Delegate work | eligible subagents/roles under an explicit delegation policy |
| No tools | reasoning-only stage |

Bundle selection previews the exact tools it grants. Advanced selection can add or remove
individual tools. Bundles are mappings, not a second authorization layer.

MCP behavior:

- “Connect a capability” is available inside the stage builder;
- after saving, the wizard attempts to load the server and returns to the exact draft/stage;
- cancel, save failure, authentication expiry, load failure, and zero-tool discovery also return
  without losing or silently changing the canonical draft;
- failed load, zero discovered tools, and configured-but-unloaded states are distinct;
- unavailable saved tools block readiness with repair actions;
- every MCP tool call remains protected by the server-side approval policy;
- OAuth token values are never serialized into blueprints, journals, packages, or API output.

## 6. Agent assignment and delegation

Default assignment is **Automatic**. The router selects the least costly eligible agent using
task type, role, capability, tier, budget, and measured success evidence.

Per-stage options:

- **Automatic** — any eligible agent; normal verified escalation is allowed;
- **Require a named agent** — exact hard pin; no silent substitution;
- optional minimum capability level shown as **Quick**, **Balanced**, or **Most capable**.

The first implementation intentionally omits “Prefer a named agent.” Its fallback semantics
are easy to misunderstand and difficult to audit.

The UI must not offer or serialize a named-agent pin until the same release enforces hard pins
in routing and readiness. Automatic role/capability requirements may ship earlier only when
their eligibility predicate is already server-enforced.

Schema additions:

```yaml
role: implementer
required_capabilities: [workspace.write, tests.run]
worker_id: codex-primary # optional exact hard pin
```

Rules:

- `worker_id` and `tier_hint` are mutually exclusive routing authorities;
- a pinned worker must still satisfy the declared role and capabilities;
- an unavailable pin blocks before starting the run;
- if a pinned worker disappears after the run begins, the stage fails honestly;
- automatic legacy stages without new constraints route exactly as they do today;
- agent tools/capabilities are visible in the wizard, but tools granted to a stage remain a
  separate least-privilege decision;
- delegation to subagents is configured as a capability and policy, not inferred from access
  to a command line.

## 7. Domain architecture

### 7.1 Artifact references

```python
class ArtifactRef(BaseModel):
    id: str       # strict slug
    version: int  # >= 1; never "latest" in a run
```

The UI may display “latest,” but it resolves that label and submits a concrete version.

### 7.2 Catalog entry, blueprint draft, and version

```python
class BlueprintCatalogEntry(BaseModel):
    id: str
    display_name: str
    archived_at: float | None = None
    latest_version: int | None = None

class InputSpec(BaseModel):
    name: str
    schema: dict
    required: bool = False
    default: Any = None
    secret: bool = False  # secret=True forbids a literal default/value

class BlueprintContent(BaseModel):
    schema_version: Literal[1] = 1
    name: str
    description: str = ""
    workflow: WorkflowSpec
    inputs: list[InputSpec] = []
    default_context: dict = {}
    eval_suites: list[ArtifactRef] = []
    source: ArtifactRef | None = None

class BlueprintDraft(BlueprintContent):
    id: str
    revision: int
    base_version: int | None = None
    owner: str
    created_at: float
    updated_at: float

class BlueprintVersion(BlueprintContent):
    id: str
    version: int
    published_at: float
```

Draft update requests carry `expected_revision`; the server assigns the next revision. Publish
requests carry the expected draft revision; the server assigns the immutable version number.
Models are strict and reject unknown schema versions or extra fields. Secret inputs accept only
`SecretBindingRef` values and are excluded from `default_context`.

### 7.3 Eval suite version

```python
class EvalSuiteVersion(BaseModel):
    schema_version: Literal[1] = 1
    id: str
    version: int
    name: str
    description: str = ""
    development_cases: list[Task]
    validation_cases: list[Task]
    holdout_cases: list[Task]
    policy: EvalPolicy
    created_at: float
```

Splits and task IDs are persisted explicitly. They are never recomputed when replaying an
evaluation.

### 7.4 Storage

Blueprints and eval suites do not live in `config.json`, which contains mutable providers,
agents, bindings, and secrets.

```text
~/.metaharness/
  blueprint-catalog/<id>.json
  blueprint-drafts/<id>.json
  blueprints/<id>/versions/<n>.json
  eval-suites/<id>/versions/<n>.json
  journals/<run-id>.jsonl
  packages/
  optimization/
```

Publishing uses per-artifact locking and atomic same-directory writes. Existing version files
are immutable. Listing may use a derived latest pointer, but execution never resolves a run
from that pointer.

### 7.5 Built-in blueprints

Existing code templates become catalog seeds behind the same read API. Each built-in has an
explicit artifact version. A golden digest test requires a version bump whenever its content
changes. Built-ins remain parameterized: their immutable definition declares a `goal` input and
deterministic rendering rules. Running the same version with two goals produces two resolved
run snapshots without republishing or mutating the built-in.

## 8. API requirements

Blueprint catalog:

```text
GET    /api/blueprints
POST   /api/blueprint-drafts
GET    /api/blueprint-drafts/{id}
PATCH  /api/blueprint-drafts/{id}             # expected_revision required
DELETE /api/blueprint-drafts/{id}             # unpublished drafts only
GET    /api/blueprints/{id}
GET    /api/blueprints/{id}/versions/{version}
POST   /api/blueprint-drafts/{id}/publish      # expected_revision required
POST   /api/blueprints/{id}/fork
PATCH  /api/blueprints/{id}/metadata           # display name only
POST   /api/blueprints/{id}/archive
POST   /api/blueprints/{id}/restore
POST   /api/blueprints/readiness
```

Eval catalog:

```text
GET    /api/eval-suites
POST   /api/eval-suites
GET    /api/eval-suites/{id}/versions/{version}
POST   /api/eval-suites/{id}/versions
POST   /api/blueprints/{id}/versions/{version}/evaluate
POST   /api/blueprints/{id}/versions/{version}/tune
```

Run intake supports exactly one source:

```json
{
  "blueprint": {"id": "software-delivery", "version": 3},
  "context": {"goal": "Add export support"},
  "wait": false
}
```

Legacy `workflow` and `workflow_yaml` requests remain supported. Requests containing multiple
sources are rejected. A blueprint request applies declared defaults, validates input schemas,
rejects unknown inputs unless the blueprint explicitly permits them, resolves secret binding
references, and runs server-side readiness before it creates a journal. Library and CLI use the
same validation/readiness implementation.

Run responses and journals include:

- exact blueprint and eval-suite references;
- embedded blueprint/workflow snapshot;
- actual worker ID, model, and tier for every attempt;
- requested role/capabilities/pin;
- tool calls and approvals;
- verification scorer and result;
- cost, tokens, latency, and failure reason.

Readiness response shape:

```json
{
  "ready": false,
  "blueprint": {"id": "software-delivery", "version": 3},
  "issues": [{
    "code": "unloaded_mcp",
    "severity": "error",
    "stage_id": "research",
    "message": "Web search is connected but its tools are not loaded.",
    "repair": {"action": "load_mcp", "server": "web-search"}
  }]
}
```

## 9. Run experience and progress

The Run screen becomes a live timeline rather than a mostly static status card.

Required states:

- waiting on dependencies;
- checking readiness;
- assigned to an agent;
- running attempt N;
- using a capability/tool;
- verifying output;
- waiting for approval;
- retrying/escalating;
- completed, skipped, or failed.

Required journal events include:

```text
run.started
step.ready
step.started
attempt.assigned
attempt.started
tool.requested
tool.completed
verification.started
verification.completed
approval.required
approval.resolved
step.completed | step.failed | step.skipped
run.completed | run.failed
```

Every event has a schema version, monotonic sequence, timestamp, run ID, optional stage ID,
optional attempt ID, and payload. `attempt.assigned` carries the actual worker ID, model, tier,
and requested role/capabilities/pin, so retries and escalation may visibly select a different
worker. Terminal events are idempotent and replay reconstructs the same timeline after refresh
or restart.

Current journal names such as `hitl.requested` and `run.finished` remain readable through a
versioned projection layer. New writers use the canonical event vocabulary; adoption maps
legacy events without rewriting historical files. The UI must not display “assigned” or
“running” until the corresponding backend event exists.

Done-screen actions:

- run again with new inputs;
- edit blueprint;
- save an ad-hoc run as a harness;
- fork and improve;
- evaluate this version;
- package/export;
- inspect in Console.

Changing inputs for a rerun never mutates blueprint defaults.

## 10. Blueprint-specific evals and tuning

Each blueprint may reference one or more exact eval-suite versions.

The frontier meta-harness agent can propose:

- cases derived from stage contracts and success checks;
- regression cases derived from failed production runs;
- deterministic assertions and output schemas;
- execution checks for code/file effects;
- judge rubrics only where deterministic checks cannot express quality;
- missing edge cases and adversarial inputs.

Human approval is required before proposed cases become a published eval-suite version.

Evaluation hierarchy:

1. deterministic value checks;
2. schema validation;
3. sandboxed execution evidence;
4. independent model judge for subjective quality.

Eval tool policy is fail-closed. Deterministic fakes, fixtures, and isolated disposable
workspaces are the default. Real email send, calendar mutation, cloud deployment, production
workspace mutation, and other external-effect tools are prohibited in eval/tuning. A tool may
participate only through a declared isolated test binding whose policy proves the target is
non-production; a generic approval gate is not sufficient isolation.

Eval health is keyed by the exact `(blueprint version, eval-suite version)` pair. The Library
summary shows the newest complete report for every suite referenced by the displayed blueprint
version, with pass/fail/never-run/stale states and timestamp. Reports for an older blueprint or
suite version never count as current health.

Tuning rules:

- freeze exact blueprint and eval-suite versions for a tuning search;
- keep development, validation, and sealed holdout cases separate;
- feed traces, cost, latency, routing, tool use, retries, and failure reasons into proposals;
- never tune directly on the sealed holdout;
- publish a tuned candidate as a new blueprint version or fork;
- require human approval before promotion;
- sandbox/mock/dry-run external side effects during evals;
- a production failure may become a proposed regression case, never an automatically active
  test with unreviewed sensitive content.

The current name-based optimization suites remain a compatibility path until they are migrated
to immutable `ArtifactRef` values. The UI must not claim exact reproducibility for legacy suites.

## 11. Portable command contract

MetaHarness gains a tool-neutral command surface:

```text
metaharness blueprint validate <file> --format json
metaharness blueprint run <file> --context-file <json|-> --workspace <dir> \
  --format jsonl --approval stop
metaharness run inspect <run-id> --format json
metaharness run approve <run-id> <stage-id>
metaharness run reject <run-id> <stage-id>
metaharness run resume <run-id> --format jsonl
metaharness blueprint package <file> --target <target> --output <dir|zip>
```

`--approval stop` is the safe non-interactive default. At a gate, execution persists state,
emits `approval.required`, and exits with a documented distinct code. An outer coding agent
must never silently approve a gate.

Suggested exit codes:

- `0` completed;
- `1` execution failed;
- `2` validation/configuration/readiness failed;
- `20` approval required.

Human logs go to stderr; machine events go to stdout as versioned JSONL.

Accepted CLI inputs are explicit: a published `harness.json`, an unpacked package directory, or
a package zip. Draft files require `blueprint validate` and an explicit `--allow-draft` for an
ad-hoc run; raw `workflow.json` uses the legacy ad-hoc path. The first emitted event contains the
run ID and snapshot digest.

Stop/approve/resume uses the normal persistent run store and per-run inter-process lock. Approval
is idempotent for the same decision, conflicts with an opposite prior decision, and resume may
advance a stage at most once. Corrupt or incomplete journals fail closed with a diagnostic and
are never repaired implicitly.

## 12. Codex, Claude Code, Pi Agent, and OpenCode

The package supports two directions:

1. **Outer launcher:** a user invokes a MetaHarness blueprint from their coding agent.
2. **Inner worker:** MetaHarness assigns a stage to a configured coding-agent worker.

The orchestration spine always remains inside MetaHarness.

Targets:

- **Codex** — generated project skill/instructions invoking the neutral command; inner worker
  continues to use `codex exec` with stdin and an explicit sandbox;
- **Claude Code** — generated skill/plugin command invoking the neutral command; inner worker
  continues to use headless print/JSON output and explicit permissions;
- **Pi Agent** — generated skill or small package/extension invoking the neutral command; inner
  worker uses print/JSON mode;
- **OpenCode** — generated command/agent wrapper invoking the neutral command; inner worker uses
  `opencode run`/structured output.

Adapters:

- pass paths, context, and workspace as separate arguments/stdin, never shell-concatenated text;
- consume the same JSONL event contract;
- do not copy credentials or authentication homes;
- detect same-host recursion and warn/block unsafe nesting;
- preserve explicit permissions and approvals;
- are optional convenience layers: the neutral command remains sufficient.

Every launcher target has a versioned golden layout and supported-version matrix. Shim tests
must prove argv/stdin separation, cwd, permission flags, JSONL parsing, approval-stop behavior,
exit-code propagation, paths with spaces/Unicode, and absence of copied credential homes. The
launcher sets `METAHARNESS_HOST` and readiness blocks an unsafe same-host inner-worker recursion;
precise vendor nesting sentinels are removed only when verified, never by broad prefix deletion.

Current upstream command surfaces were verified against official documentation for
[Claude Code](https://docs.anthropic.com/en/docs/claude-code/cli-usage),
[Pi](https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/docs/usage.md), and
[OpenCode](https://opencode.ai/docs/). Codex adapter behavior must continue to be checked
against the installed CLI and official OpenAI documentation during implementation.

## 13. Portable package format

A deployable package is an input artifact, distinct from the existing run-output zip.

```text
<harness-name>-v<version>/
  harness.json                 # canonical BlueprintVersion
  workflow.json                # normalized WorkflowSpec compatibility view
  evals/                       # exact safe snapshots when evaluate/tune is supported
  manifest.json                # digests, requirements, targets, schema versions
  README.md                    # generated run/deploy guidance
  launchers/
    codex/
    claude-code/
    pi/
    opencode/
  container/
    Dockerfile
    entrypoint
    healthcheck
  deploy/
    aws-apprunner.yaml
    gcp-cloud-run.yaml
    azure-container-apps.yaml
```

Manifest requirements:

- exact blueprint and eval-suite references;
- SHA-256 content digests;
- required logical agent roles/capabilities;
- required exact tool IDs and MCP server aliases;
- required secret binding names without values;
- workspace/storage/network expectations;
- exposed port and health endpoint;
- supported local launcher and cloud targets;
- generated-at timestamp and generator version.

The execution blueprint/workflow is always embedded, so validate/run works on a clean machine
without the original catalog. A run-only package may carry eval references as provenance only.
A package advertising Evaluate or Tune must embed the exact redacted/safe eval snapshots.

Content digests are reproducible: they cover canonical artifact bytes and exclude generated-at
metadata. Archive entry order, permissions, and timestamps are normalized (or honor
`SOURCE_DATE_EPOCH`) so identical inputs produce the same package digest.

Package validation fails if it detects an OAuth token, API key, masked secret placeholder,
absolute auth-home path, arbitrary executable supplied by blueprint data, or unsupported schema.

## 14. Cloud deployment packaging

OCI/Docker is the portable base. Provider targets translate the same container contract rather
than maintaining separate harness implementations.

Initial targets:

- **AWS App Runner** from an ECR image;
- **Google Cloud Run** service or job from an OCI image;
- **Azure Container Apps** from an ACR or compatible image;
- generic Docker/OCI for other providers and private infrastructure.

Target artifacts are separate and mode-specific: AWS App Runner service configuration, Google
Cloud Run service configuration, Google Cloud Run job configuration, and Azure Container Apps
configuration. Each declares image digest, runtime command, port/health behavior where
applicable, identity, secret-reference syntax, persistent storage requirements, approval API
channel, network policy, and deployment metadata. Tests use the provider CLI dry-run/schema
surface where available plus checked golden fixtures; absence of a reliable offline validator is
reported rather than treated as validation.

This choice matches the providers’ documented container deployment models:
[AWS App Runner](https://docs.aws.amazon.com/apprunner/latest/dg/service-source-image.html),
[Google Cloud Run](https://docs.cloud.google.com/run/docs/deploying), and
[Azure Container Apps](https://learn.microsoft.com/en-us/azure/container-apps/containerapp-up).

Packaging behavior:

- generates descriptors, commands, and preflight checks;
- never performs a deployment unless the user explicitly starts a deploy action;
- never embeds cloud credentials;
- uses provider secret managers/service identities through named bindings;
- defaults services to authenticated/private access;
- requires an explicit choice before public ingress;
- pins image digests for production descriptors;
- declares persistent journal/artifact storage where required;
- emits readiness warnings for local-only agents or MCP servers that cannot run in the target;
- distinguishes service mode (API/UI) from job mode (run one blueprint and exit);
- records the package digest and exact blueprint version in deployment metadata.

Cloud runtime constraints:

- local subscription CLIs and their desktop OAuth sessions are not assumed to exist in a cloud
  container;
- cloud-ready harnesses must bind to remote/API workers or explicitly installed non-interactive
  runners;
- filesystem capabilities require a mounted workspace/object store or a deliberately ephemeral
  workspace;
- outbound network, MCP endpoints, and side-effect permissions are declared and preflighted;
- approvals require a durable server/API channel, not a blocked container TTY.

## 15. Security and trust boundaries

- Blueprint files are data, never executable configuration.
- IDs are strict slugs; version/path lookup rejects traversal and symlink escape.
- Published versions are atomically written and immutable.
- Exact worker pins never fall back silently.
- Logical capability requirements never grant tools by themselves.
- Tools are least-privilege per stage.
- MCP annotations are never trusted as authorization.
- External-effect tools require server-side approval.
- Packages contain no secret values or local credential paths.
- Cloud descriptors use secret references and least-privilege service identities.
- Launcher adapters use argv arrays/stdin and never interpolate blueprint text into a shell.
- Run journals embed snapshots so catalog deletion or later edits cannot change history.
- Eval cases containing production data require review/redaction before publication.
- Sealed holdout data is unavailable to the tuning proposer.
- Run and deployment artifacts retain signed provenance and integrity digests.

## 16. Backward compatibility and migration

- Existing `WorkflowSpec` YAML/JSON remains valid.
- Missing blueprint, role, capability, worker, and eval fields default safely.
- Existing `/api/runs`, `/api/plans`, `/api/workflows/validate`, and workflow-type flows remain.
- Existing run journals remain adoptable; embedded workflow remains authoritative.
- Existing run-output zip remains available and gains blueprint metadata only when present.
- `workflow_type` becomes a compatibility facade over built-in blueprint seeds.
- Legacy name-based optimization remains until immutable eval versions are ready.
- Current Plan editor is extended, not replaced.
- Existing Settings ownership of providers, agents, MCP connections, and secrets remains.

Migration must not silently convert mutable templates or eval extras into an “exact” version.
They are snapshotted and published once exact semantics are introduced.

## 17. Delivery plan

### Phase 1 — Blueprint foundation

- artifact models and dedicated immutable stores;
- built-in catalog facade;
- list/get/publish/version/fork/archive APIs;
- exact blueprint reference on runs and journals;
- blueprint snapshot in run packages;
- compatibility tests for legacy workflows and journals.

Verification: model/store/API tests prove immutability, path safety, exact-version execution,
fork isolation, and replay after catalog deletion.

### Phase 2 — Library and landing page

- Library navigation and management screen;
- revised Home primary actions and recent harnesses;
- load/save/version/fork state in the existing editor;
- dirty-navigation protection;
- Done-screen save/edit/rerun actions.

Verification: Playwright covers empty state, save, edit, fork, run, version, delete/archive,
navigation reset, and run-without-saving isolation.

### Phase 3 — Human-friendly builder

- purpose/input wizard;
- capability bundles and exact-tool preview;
- verification, flow, safety, retry, budget, and gate panels;
- inline MCP connect/load/return;
- readiness report and compact flow visualization;
- frontier-agent co-design suggestions.

Verification: Playwright covers guided and Advanced round-trips, missing MCP/agent repair, and
permission/gate behavior.

### Phase 4 — Assignment and live progress

- role/capability profiles on agents;
- routing eligibility and hard-pin enforcement;
- automatic role/capability and exact named-agent controls in the builder;
- typed unavailable/no-eligible failures;
- assignment/attempt/verification events;
- truthful live timeline and per-stage agent identity.

Verification: routing and journal tests plus browser tests for automatic, pinned, unavailable,
retry, escalation, approval, failure, and refresh/resume states.

### Phase 5 — Eval catalog and blueprint tuning

- immutable eval-suite versions and explicit splits;
- frontier proposals and human publication gate;
- blueprint evaluation reports;
- frozen-suite tuning input and tuned-version promotion;
- proposed production regressions with redaction/review.

Verification: deterministic suite replay, holdout isolation, promotion gate, side-effect sandbox,
and exact report provenance.

### Phase 6 — Portable CLI and launchers

- neutral blueprint/run CLI and JSONL events;
- durable stop/approve/resume behavior;
- generated Codex, Claude Code, Pi, and OpenCode adapters;
- same-host recursion preflight;
- shim-based adapter contract tests and opt-in local smoke tests.

Verification: all launchers normalize to the same run request/event contract and never bypass
approval or leak environment secrets.

### Phase 7 — Deployment packaging

- secret scanner and integrity manifest;
- OCI/Docker runtime target;
- AWS App Runner descriptor;
- Google Cloud Run service/job descriptor;
- Azure Container Apps descriptor;
- cloud readiness and binding wizard;
- explicit deploy action as a later approval-gated operation.

Verification: golden package tests, container smoke/health test, descriptor validation, no-secret
tests, private-ingress defaults, image digest pinning, and provider dry-run/preflight checks.

Release boundaries:

- **Core reusable harness release:** Phases 1–4;
- **Eval/tuning extension:** Phase 5;
- **Local-agent portability extension:** Phase 6;
- **Cloud packaging extension:** Phase 7.

Each boundary has its own feature flag, migration note, release checklist, and zero-open-P0/P1
review rule. A stable core Library is not held back by cloud-provider work, while every extension
consumes the same artifact and readiness contracts.

## 18. Acceptance criteria

The overhaul is complete when:

1. A new user can create and save a harness without editing YAML or typing MCP commands.
2. The Library lists built-in and owned harnesses and supports run, edit, fork, versions,
   evaluate, package, and safe removal/archive.
3. Editing a published harness creates a new immutable version.
4. Every saved-harness run references a concrete blueprint version; ad-hoc/legacy runs use a
   null reference. All runs embed a resolved snapshot and digest and remain replayable after
   restart and catalog archive/deletion.
5. A run from Library stops for readiness review and never starts with one accidental click.
6. Friendly capability bundles map visibly to exact stage tool permissions.
7. Missing agents/tools/MCP servers block with a named repair action; nothing is silently
   removed or substituted.
8. Automatic assignment is capability-aware; exact pins never fall back.
9. The Run timeline names the actual agent and accurately reports attempts, tools, checks,
   approvals, escalation, completion, and failure.
10. Done supports rerun with new inputs, edit, fork, and save-as-harness without rebuilding.
11. Each published blueprint can reference exact versioned eval suites and receive an auditable
    evaluation report.
12. Tuning freezes its blueprint/eval inputs and publishes a candidate only through human
    approval.
13. The neutral CLI can validate, run, stop at approval, inspect, approve/reject, and resume.
14. Codex, Claude Code, Pi Agent, and OpenCode launchers invoke the same MetaHarness executor.
15. A package is secret-free, integrity-addressed, and contains local launcher plus selected
    cloud targets.
16. OCI/Docker, AWS App Runner, Google Cloud Run, and Azure Container Apps outputs validate and
    default to private/authenticated operation.
17. Existing workflows, APIs, journals, templates, and run-output packages continue to work.
18. Named unit, API, Playwright, package, adapter, and security suites pass; the release checklist
    records independent architecture/security/UX review artifacts with zero unresolved P0/P1.

## 19. Explicit non-goals for the first release

- a freeform node-canvas editor;
- silent autonomous cloud deployment;
- storing or brokering provider/cloud credentials in a blueprint;
- automatically approving MCP calls or other external effects;
- allowing coding agents to reinterpret the orchestration DAG;
- deleting run history when a blueprint is archived/deleted;
- tuning directly on a sealed holdout set;
- claiming immutable eval reproducibility while using legacy mutable extra-task files;
- supporting every cloud provider before the OCI contract and three initial adapters are stable.

## 20. Key decisions recorded

- Harness Blueprint is the reusable source of truth; Run is an immutable instance.
- Built-ins are immutable; customization creates a fork.
- Published versions and eval suites are exact artifacts, not mutable config entries.
- Guided mode is primary; YAML is an advanced escape hatch.
- Agent assignment defaults to automatic role/capability routing with optional hard pin.
- MCP setup is available inline and returns to the exact stage draft.
- Each blueprint owns exact eval references that feed evaluation and tuning.
- MetaHarness remains the executor across Codex, Claude Code, Pi, and OpenCode.
- Deployment is part of packaging, based on OCI with provider-specific descriptors.
- Packaging generates and validates; deployment itself requires explicit separate approval.
