# META-6 Build Spec — Typed Memory Substrate (TASK-20260714-004)

You are a BUILDER seat. The orchestrator will not edit code. Work ONLY in
`/private/tmp/meta-harness-meta-6` (branch `dev/meta-6-memory-substrate`, base
`24dadae`). Do NOT commit — leave changes in the working tree; the orchestrator
commits after review. Do NOT edit this spec file.

Run tests with the WORKTREE venv: `/private/tmp/meta-harness-meta-6/.venv/bin/pytest`.

## Mission

Implement the Phase-2 typed memory substrate per
`docs/context-memory-self-improving-harness-plan.md` (§Phase 2, lines ~1636-1668;
broker ~1419-1435; snapshot ~1399-1407; record contract ~1467-1504). The
authoritative acceptance contract is `tests/adversarial/test_memory_skill_boundaries.py`:
13 of its 15 strict-xfail contracts must flip to enforced green tests.
META5-MEM-009 and META5-MEM-011 stay strict-xfail (reserved for later cards).

## Hard boundaries (stop conditions — violating any is a P0)

- Live prompts, fitted messages, and request bytes stay BYTE-IDENTICAL. Nothing
  in `assembly.py`'s live fitting path may change output for existing inputs;
  new fields appear only in shadow/receipt artifacts.
- No authority grants: specialists never get domain-action commit, visibility
  widening, evaluator, promotion, deployment, or self-approval authority.
- Destructive deletion / durable activation are NEVER direct operations — only
  reviewable tombstone/expiry proposals.
- Every mutation path emits a receipt; no unreceipted mutation exists.
- Do not weaken or delete any existing META-4/META-5 test assertion.
- Do not implement `metaharness.memory.training` or `metaharness.memory.promotion`.
- No imports from `metaharness.harness`/runtime workers into the memory package;
  memory may import `metaharness.context` models/helpers only (+ stdlib, pydantic).
- Touch ONLY: `src/metaharness/memory/`, `src/metaharness/context/`,
  `tests/test_memory.py`, `tests/test_memory_broker.py`, `tests/test_context.py`
  (additive only), `tests/adversarial/`, `tests/fixtures/meta5/`, `tests/fixtures/meta6/`.

## House style (match exactly)

- Contracts subclass `FrozenModel` (`src/metaharness/context/models.py:23-24`):
  pydantic `ConfigDict(extra="forbid", frozen=True)`.
- Hashing: reuse `canonical_json` / `content_hash` from
  `src/metaharness/context/models.py:13-20`; sha256 fields match
  `SHA256_PATTERN` (`models.py:11`).
- Self-verifying immutable records follow `ContextManifest.validate_manifest_hash`
  (`models.py:271-295`): recompute `content_hash(model_dump(mode="json",
  exclude={<hash field>}))`, raise on mismatch.
- Tests: flat `def test_*` functions, behavioral names, `pytest.raises(ValidationError)`
  for contract violations, parametrize with change-dicts (see `tests/test_context.py`).
  Reuse factories from `tests/adversarial/_meta5_support.py` (`make_scope`,
  `make_source`, `make_section`, `cases_for`).
- Comment density: sparse; only constraints code can't express.

## STAGE 1 — Context boundary validators (context package only)

Close META5-MEM-002, 003, 006, 010, 012, 014, 015 with ADDITIVE validators in
`src/metaharness/context/models.py` (+ `assembly.py` only if receipt emission
needs the new field). Read each xfail test first — it is the exact contract.

- MEM-002 (`test_memory_skill_boundaries.py:88`): `ContextSourceRef` rejects
  path-traversal in `source_id` and `artifact_ref` (any `..` path segment,
  backslash variants). Must NOT reject legit ids (`artifact:evidence-42`,
  `event-seq:1`, `lineage-42`).
- MEM-003 (`:108`): `ContextEnvelope` rejects sections whose source scopes
  carry different `project_id`s (cross-scope bleed).
- MEM-006 (`:180`): `CompressionReceipt` gains `fidelity_loss_estimate: float`
  in [0,1]. Default computed in a model validator when absent (e.g. clamped
  `1 - final_tokens/original_tokens` for lossy actions); existing constructions
  must stay valid. Live fitting output unchanged.
- MEM-010 (`:256`): `ContextEnvelope` rejects two sections whose sources share
  a `source_id` where one is content_hash-pinned and the other high-water-mark
  live (confounded lineage). Minimal contract: reject the mix for the same
  source_id; a future reconciliation marker is out of scope — name it in the
  error message.
- MEM-012 (`:326`): `ContextVersionBindings` cross-axis check. DESIGN NOTE: a
  single-instance validator cannot see "reuse"; implement the documented
  convention that a `harness_version` self-declaring a breaking bump (segment
  `breaking` in the version string) must not bind non-None
  `memory_snapshot_version`/`evidence_snapshot_version`. Docstring must state
  the convention explicitly. The xfail test is the exact accept/reject pair.
- MEM-014 (`:372`): `ContextManifest` validator parses `redacted_envelope_json`,
  collects every fetchable source's `artifact_ref`, requires each to appear in
  `artifact_refs`.
- MEM-015 (`:440`): trust ceiling on `ContextSourceRef`:
  `ContextSourceKind.EVALUATOR_RECEIPT` may never carry
  `ContextTrust.INSTRUCTION`. Only that pair — do not restrict other kinds.

Then in `tests/adversarial/test_memory_skill_boundaries.py`: remove the xfail
decorators for these 7 (tests become enforced; keep bodies/raises), update
`tests/fixtures/meta5/corpus.json` case `status` absent→enforced for their
cases, and update `test_all_absent_cases_have_a_stable_requirement_id_and_a_test_below`
(final absent set after all stages = {META5-MEM-009, META5-MEM-011}).

Verify: `.venv/bin/pytest -q tests/adversarial tests/test_context.py` then full
`.venv/bin/pytest -q` — all green, no xpass, no new failures.

## STAGE 2 — Memory package core (`src/metaharness/memory/`)

New package closing META5-MEM-001, 004, 005, 007, 008, 013. Exact names are
fixed by the xfail tests:

- `metaharness.memory` exports: `MemoryRecord`, `ActivationState`
  (ACTIVE/DORMANT/TOMBSTONED), `EpisodicMemoryStore`, `SemanticMemoryStore`,
  `WorkingMemoryStore`, `ProceduralMemoryStore`, `ImmutableRecordError`,
  `UnreceiptedMutationError`.
- `MemoryRecord(kind=..., content=...)`: frozen pydantic; kinds
  working/episodic/semantic/procedural (align with `ContextSourceKind` values);
  fields per plan ~1485-1498: id, schema_version, kind, scope (reuse
  `ContextScope`), content + normalized search text, source refs, observed/valid
  times (injectable clock — NO wall-clock defaults that break determinism),
  confidence, lifecycle state (candidate/active/superseded/rejected/expired/
  tombstoned), supersedes, sensitivity, creator identity, usage counters.
  `record.tombstone(reason=...)` returns a NEW record, TOMBSTONED, content
  preserved (MEM-007). `activation_state` starts ACTIVE.
- Stores: `commit(kind=, content=) -> MemoryRecord`; `overwrite(id, content=)`
  raises `ImmutableRecordError` (MEM-001: append-only; supersede instead);
  `mutate(id, content=, receipt=)` with `receipt=None` raises
  `UnreceiptedMutationError` (MEM-004); receipted mutation appends a superseding
  record + immutable `MemoryMutationReceipt` (self-verifying hash), never
  rewrites in place.
- Durability: stdlib `sqlite3`, WAL mode, numbered schema migrations, FTS5
  lexical index over normalized text. Records survive close/reopen. In-memory
  mode for unit tests where durability isn't under test.
- `metaharness.memory.audit` (MEM-005): `bind_sink(fn)`,
  `CommitOrderedMemoryStore` — the durable commit completes BEFORE the audit
  event is emitted; event payload carries `commit_state == "committed"`.
- `metaharness.memory.skills` (MEM-008): `SpecialistTaskAction(specialist_id,
  action, scope)`; `.authorize(allowed_actions=set)` raises
  `UnauthorizedTaskActionError` when action not allowlisted. No execution
  authority — the type only validates and records.
- `metaharness.memory.health` (MEM-013): `MemorySkillCircuitBreaker(
  failure_threshold=)`, `.record_failure()`, `.is_healthy()`,
  `.require_healthy()` raises `CircuitOpenError` once open.

Flip those 6 xfails to enforced; update corpus statuses. New focused suite
`tests/test_memory.py`: stores + lifecycle + receipted mutation + audit
ordering + authorization + breaker + SQLite restart survival + scope isolation
(records from project A never returned for project B queries).

## STAGE 3 — Shadow broker + scaffold baseline (`src/metaharness/memory/`)

- `MemoryCognitiveSkillSnapshot`: immutable policy — goal-family/role scope,
  parent + self-verifying content hash, LOG/MAINTAIN + CONSULT phase contracts,
  allowlisted action vocabulary, query/ranking/compression/retention/context-
  budget policy knobs, redaction/sensitivity rules, deterministic fallback
  declaration, lifecycle state.
- `MemoryAction`: typed frozen action — operation enum exactly
  {search, read, create_candidate, append, upsert, revise_candidate, link,
  compress_candidate}, phase (log/maintain/consult), scope, payload.
- `MemoryActionBroker`: SHADOW-only deterministic enforcement boundary bound to
  one snapshot + stores. Validates and either executes scoped read/candidate-
  write operations or rejects: unknown operation, out-of-vocabulary action,
  path traversal, cross-scope record IDs, writes to immutable evidence,
  lifecycle bypass (no direct activate/delete/tombstone-of-others — those
  become typed PROPOSALS), redaction violations, and any domain task action.
  EVERY invocation — accepted or rejected — emits a `MemoryActionReceipt`.
- `MemoryActionReceipt`: immutable, self-verifying hash; fields per plan
  ~1428-1435: snapshot/skill + context + store high-water + policy versions,
  phase, operation, query or source record ids, considered/selected targets,
  scope + lifecycle filters, before/after content hashes, validation +
  redaction results, token/latency accounting fields, effect-or-rejection
  reason.
- Scaffold-only LOG/CONSULT baseline: pure deterministic H functions (no model
  call, no prompt change): post-observation LOG validates typed records into
  candidate lifecycle via the broker; pre-action CONSULT issues scoped broker
  reads with deterministic ranking (stable sort keys, no randomness, no
  wall-clock). Identical inputs ⇒ byte-identical receipts (given injected
  clock/ids).
- `tests/test_memory_broker.py`: vocabulary enforcement, rejection receipts,
  scope guards, receipt immutability + hash self-check + tamper detection,
  determinism (run twice, compare receipts), lifecycle-bypass rejection,
  domain-action rejection, scaffold LOG/CONSULT round-trip.

## Per-stage verification (before reporting done)

1. `.venv/bin/pytest -q tests/test_memory.py tests/test_memory_broker.py` (once they exist)
2. `.venv/bin/pytest -q tests/adversarial/test_memory_skill_boundaries.py`
3. `.venv/bin/pytest -q tests/adversarial tests/test_context.py tests/test_harness.py tests/test_local_worker.py`
4. `.venv/bin/pytest -q` (full — zero regressions)
5. `git diff --check`

Report per stage: files touched, test counts (exact), any deviation from this
spec with evidence-backed reasoning. If you disagree with a contract, push back
with evidence — do not silently deviate.
