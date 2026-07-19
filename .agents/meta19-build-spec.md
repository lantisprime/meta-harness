# META-19 Build Spec v2 — Promote shadow ContextEnvelope to the live prompt path

Card: `TASK-20260719-013` (Linear META-19). Frozen definition v2 (scope
amendment after codex plan review, user-approved, workplan receipts rev 43/44):
`.agents/meta19-definition-v2.json`
(`sha256:ec3503080424a678ec016f0a92324366219dcdc2509ec10b8c96aa541faa4072`).
Plan-review report: `.agents/meta19-plan-review-codex.txt` — its 9 findings are
FOLDED IN below; where this spec conflicts with the v1 text, v2 wins.
Worktree: `/private/tmp/meta-harness-meta-19`, branch
`dev/meta-19-live-context-envelope`, base `46c60fa`. Use the WORKTREE venv:
`/private/tmp/meta-harness-meta-19/.venv/bin/pytest`.

## Outcome

The typed `ContextEnvelope` stops being a shadow sidecar and becomes the single
live assembler both worker families consume. Trust rules and secret redaction
apply to the bytes actually sent; the `ContextManifest` is journaled per attempt
as the authoritative "what did the model see" record. Divergence between
manifest and sent bytes is impossible by construction because the sent bytes are
DERIVED FROM the validated envelope, not observed after the fact.

## Current state (read these first)

- `src/metaharness/context/models.py` — frozen contracts (do not weaken).
  Trust enum at :52; evaluator-receipt-never-INSTRUCTION at :168-177;
  `ContextEnvelope` validators at :228; `ContextManifest` at :339 with
  `reconstruct_messages()` / `reconstruct_tool_schemas()`.
- `src/metaharness/context/assembly.py` — `fit_messages` (legacy fitter, :111),
  `fit_messages_with_receipt` (shadow observer, :246), redaction machinery
  (:148-193), `_message_contract` (:196) which infers section/trust from
  role — this inference direction is what promotion inverts: callers now
  DECLARE sections; messages are rendered from them.
- `src/metaharness/harness/local.py` — `_build_messages` (:41) ad-hoc concat;
  `_execute` loop (:183-249) runs the shadow observer per round, falls back to
  the legacy fitter on ANY assembler exception, emits
  `context.manifest.shadow` / `context.manifest.shadow_failed`.
- `src/metaharness/harness/coding.py` — `_render_prompt` (:273) ad-hoc concat
  into one flat prompt; no manifest at all today.
- `src/metaharness/observability/run_events.py` — `emit_run_event` is bound by
  `core/executor.py:214` to the attempt event stream (payloads get `{"n": …}`
  merged in), so a worker-side emit IS per-attempt journaling. No executor
  change is needed or allowed (not an owned path).
- Callers: ONLY `local.py` and `coding.py` build prompts. Nothing else calls
  `_build_messages` / `_render_prompt` / `fit_messages_with_receipt`.

## Stage 0 — journal + core plumbing (plan-review F1, F2, F6)

These are PREREQUISITES the v1 spec missed; do them first, each with tests.

1. **`src/metaharness/workflows/journal.py`** (F1): add `"context.manifest"`
   to `CANONICAL_KINDS` as an attempt-scoped kind. `WorkflowEngine`'s sink
   (`engine.py:444`) already threads `attempt_id` from `payload["n"]` — no
   engine change. Add tests in `tests/test_run_events.py` /
   `tests/test_workflows.py`: an end-to-end run (engine → executor → worker →
   `Journal.load()`) shows one `context.manifest` event per attempt round with
   the manifest payload intact, and legacy projection does not crash on the
   new kind. Without this the event is REJECTED by `RunEvent`'s kind
   validator and silently lost (worker telemetry try/except).
2. **`src/metaharness/core/types.py`** (F2, F6, both additive):
   `Task.advice: list[str] = []` — accumulated reflections/knowledge hints,
   carried SEPARATELY from `boundaries` (which stay a pure caller-authored
   instruction contract); and `WorkerResult.error_kind: str | None = None`.
3. **`src/metaharness/core/executor.py`** (F2, F6):
   - `_attempt_task` (:97) puts advice into `variant.advice`, NOT
     `variant.boundaries`. Boundaries no longer receive reflexion/selflearn
     text — that was trust laundering (advice quotes prior worker output and
     retrieved content verbatim).
   - Retry loop: when a `WorkerResult` carries
     `error_kind == "context_contract"`, abort further attempts for that task
     (deterministic contract violation — retries are waste) and EXCLUDE the
     attempt from capability-matrix/routing evidence updates (it is not model
     capability signal). Tests in `tests/test_executor.py`.
4. **`src/metaharness/harness/runner.py`** (F6): `BaseRunner` maps
   `LiveContextViolation` to `WorkerResult.error_kind = "context_contract"`
   (other exceptions keep `error_kind=None`; behavior otherwise unchanged).

## Stage 1 — live assembler (`src/metaharness/context/live.py` + exports)

New module `src/metaharness/context/live.py`:

1. `SectionDraft` (frozen pydantic, extra=forbid): the caller-declared input —
   `section_type: ContextSectionType`, `source_kind: ContextSourceKind`,
   `stable_id`, `trust: ContextTrust`, `sensitivity: Sensitivity`,
   `content: str`, plus optional `role` hint for message rendering
   (system/user/assistant/tool + `tool_call_id`). Keep it minimal — token
   counts and hashes are computed by the assembler, never declared.
2. `LiveContextViolation(Exception)` — typed, raised BEFORE any model call.
3. `assemble_live(drafts, *, budget_tokens, model_id, harness_version, tier,
   redaction_values, tool_schemas=None, policy_version="context-live-v1",
   …version bindings kwargs) -> LiveAssembly` where `LiveAssembly` (frozen)
   carries EVERY transport surface, all redacted (F3): `messages` (chat list,
   redacted, fitted), `tool_schemas` (redacted — `_body()` must consume THESE,
   never the originals), `prompt` (flat string for CLI workers),
   `system_prompt` (redacted — adapters that transport a system prompt must
   use this, never `worker.system_prompt` raw), `envelope`, `manifest`.
   Granularity contract (F5): ONE `ContextSection` per draft (unique
   stable_ids, contiguous priorities); manifest ENTRIES per transport surface
   (`message`, `tool_schemas`, and new additive literals `flat_prompt`,
   `system_prompt` in `ContextManifestEntry.surface`); compression receipts
   remain 1:1 aligned with entries (per models.py:358); per-draft compression
   facts live in the sections themselves. `SectionDraft` supports structured
   message extras (F4): assistant `tool_calls` arrays and `tool_call_id`
   round-trip losslessly through rendering, recursive redaction, and manifest
   hashing — round-2 request bodies must be exactly reconstructable.
   Pipeline order (deterministic, no wall-clock, no randomness):
   a. **Trust enforcement (fail closed).** Instruction slots =
      {SYSTEM_INSTRUCTIONS, RESPONSE_CONTRACT, TOOL_SCHEMAS}. A draft whose
      `section_type` is an instruction slot MUST carry trust INSTRUCTION; a
      draft with trust UNTRUSTED_EVIDENCE or GENERATED_SUMMARY may never
      occupy one. EVALUATOR_RECEIPT sources never carry INSTRUCTION (already
      enforced by `ContextSourceRef`; surface it as `LiveContextViolation`
      not a bare pydantic error). Violations raise `LiveContextViolation`.
   b. **Redaction on live content.** Reuse `_redact` /
      `_BUILTIN_SECRET_PATTERNS` against each draft's content BEFORE fitting;
      the redacted text is what enters sections, messages, prompt, and
      manifest alike (single source of truth ⇒ no shadow/live divergence).
   c. **Budget fitting.** Reuse the existing deterministic head/tail digest
      (`digest_text`) semantics per section against
      `allocate_section_budgets`; protected edge sections
      (SYSTEM_INSTRUCTIONS, RESPONSE_CONTRACT) are never omitted (the
      models.py validator already forbids it). Record per-section
      `CompressionReceipt`s honestly. HARD postcondition (F7): after fitting,
      the total estimated tokens of ALL returned surfaces must be ≤
      budget_tokens; `digest_text` can GROW tiny inputs (pruning marker,
      ~6 tokens even at target 0) and zero allocations are possible
      (assembly.py:86) — define deterministic handling (omit non-protected
      zero-budget sections with an honest OMITTED receipt; if protected
      content alone cannot fit, raise `LiveContextViolation` pre-call).
   d. **Envelope + manifest.** Build `ContextSection`s from the fitted
      sections, then the `ContextEnvelope` and `ContextManifest` exactly as
      `assembly.py` does today (reuse helpers where practical —
      `_make_envelope`, hashing). Messages = rendered from sections; the
      manifest's `reconstruct_messages()` MUST equal the returned `messages`
      byte-for-byte (this is the divergence-impossible property; test it).
   e. **Flat prompt rendering.** `prompt` = the same sections joined in
      ordering-priority order with the same headings local/coding use today
      (Boundaries:/Constraints:/Inputs:/output-schema phrasing preserved so
      prompt content parity holds where no redaction/violation applies).
4. Keep `fit_messages` and `fit_messages_with_receipt` exported and unchanged
   (other callers/tests rely on them; `fit_messages` remains the tool-round
   refit primitive if needed). Export the new names from
   `context/__init__.py`.

## Stage 2 — `harness/local.py` consumes the assembler

- `_build_messages` is replaced by a function that DECLARES `SectionDraft`s:
  system prompt (+persona) → SYSTEM_INSTRUCTIONS / PROTECTED_INSTRUCTIONS /
  INSTRUCTION; caller-authored `task.boundaries` + output-schema directive →
  RESPONSE_CONTRACT / RESPONSE_CONTRACT / INSTRUCTION; objective →
  TASK_CONTRACT / GOAL / INSTRUCTION; task inputs → WORKFLOW_STATE /
  LIVE_RUN_STATE / UNTRUSTED_EVIDENCE (inputs are data, not instructions —
  deliberate trust correction; render them in the user message under the
  existing "Inputs:" heading); `task.advice` (F2 — reflections/knowledge
  hints, which quote prior worker output and retrieved content) →
  VERIFIER_FEEDBACK / LIVE_RUN_STATE / GENERATED_SUMMARY, rendered in the
  user message, NEVER in an instruction slot. Boundaries no longer contain
  advice after Stage 0, so declaring them INSTRUCTION is truthful.
- Each round of the tool loop: append assistant turns (LIVE_RUN_STATE /
  UNTRUSTED_EVIDENCE) and tool observations (IMMUTABLE_ARTIFACT /
  UNTRUSTED_EVIDENCE) as new drafts and re-run `assemble_live`; send
  `assembly.messages`.
- Emit ONE non-shadow event per round: kind `context.manifest`, payload
  `{"schema_version": 1, "shadow": False, "task_id", "round",
  "live_messages_hash": content_hash(sent messages), "manifest": …}`.
  Executor sink binding adds the attempt number. Telemetry sink failure must
  not fail the model call (keep the try/except around emit ONLY).
- **Failure semantics change (the point of the card):**
  `LiveContextViolation` and redaction/contract failures PROPAGATE (fail
  closed; no model call, no legacy fallback). The old fall-back-to-legacy
  behavior and `context.manifest.shadow*` events are removed. Genuine
  infrastructure impossibilities do not exist here — assembly is pure
  computation; any exception is a contract violation and fails the attempt
  loudly (WorkerResult.error via the BaseRunner error path, as any raise does
  today).
- Header `Authorization` keeps the real api_key (transport, not context);
  `redaction_values=[api_key]` scrubs it from CONTENT.

## Stage 3 — `harness/coding.py` consumes the assembler

- `_render_prompt` declares the same drafts (objective TASK_CONTRACT/GOAL/
  INSTRUCTION; "Constraints:" → RESPONSE_CONTRACT/RESPONSE_CONTRACT/
  INSTRUCTION; inputs → WORKFLOW_STATE/LIVE_RUN_STATE/UNTRUSTED_EVIDENCE;
  output-schema print-directive → RESPONSE_CONTRACT trust INSTRUCTION merged
  with constraints or a separate draft with a distinct stable_id) and calls
  `assemble_live` with the CLI worker's tier budget; the CLI receives
  `assembly.prompt`.
- `system_prompt` attestation must be TRUTHFUL per adapter (F3): pi and
  claude transport it via `--append-system-prompt` argv — for them, declare a
  SYSTEM_INSTRUCTIONS draft, and the argv MUST receive the assembler's
  REDACTED `system_prompt`, never `worker.system_prompt` raw. codex and
  opencode builders do not transport a system prompt at all — for them, do
  NOT declare the draft (a manifest claiming they saw it would be false).
  The adapter table gains a `sends_system_prompt` fact the worker consults.
- Emit `context.manifest` once per attempt (same payload shape; `"round": 0`).
- Same fail-closed semantics; no fallback rendering.

## Stage 4 — tests (owned test files only)

New/updated tests, each citing META-19:

1. **Trust violation, no model call**: a task/system_prompt path that declares
   untrusted content into an instruction slot raises `LiveContextViolation`;
   with an injected httpx client (local) / stub binary (coding), assert the
   endpoint was NEVER hit.
2. **Redaction on sent bytes**: seed a secret (api_key value + `sk-…` pattern)
   into task inputs; capture the actual request body via injected client and
   assert `[REDACTED]` appears and the raw secret does not; assert manifest
   `redaction_count` agrees.
3. **Divergence impossible**: `manifest.reconstruct_messages()` equals the
   messages the client actually received (hash equality on canonical JSON).
4. **Journal answers attempt N**: bind a run-event sink, run ≥2 rounds (tool
   loop), assert one `context.manifest` event per round with
   `live_messages_hash` matching each round's sent messages; assert
   `shadow` is False and no `context.manifest.shadow` event exists anymore.
5. **Content parity**: for a representative no-secret, no-violation task, the
   assembled user/system text contains the same objective/boundaries/inputs
   content the legacy builders produced (heading-level parity, not
   byte-identity across the whole list).
6. **Determinism**: same inputs → identical envelope/manifest hashes.
7. **Coding worker**: flat prompt contains the same sections; manifest
   emitted; stub-binary test proves prompt content.
8. Update the tests that assert the superseded shadow-only contract — the
   COMPLETE set in `tests/test_context.py` (F8): :244 (tool-schema shadow
   reconstruction), :262 (shadow event + byte-identity), :310 (sink-failure),
   :334 (`test_shadow_assembler_failure_falls_back_to_legacy_fit` — the
   fallback it asserts is REMOVED; supersede to fail-closed), :366 region
   (tool-round shadow manifests), plus any other `shadow` match a fresh grep
   finds. Sink-failure tolerance (telemetry cannot fail the call) KEEPS a
   test — only legacy-fallback-on-assembler-error assertions are superseded.
   Cite META-19 in each changed test's docstring.
9. `tests/adversarial/test_memory_skill_boundaries.py` mentions shadow for
   MEMORY (META-6) — that broker stays shadow; do NOT touch memory-broker
   semantics. Only context-path assertions change.
10. **Coverage the plan review demanded (F8)**: parameterize coding-worker
    prompt/manifest tests over ALL FOUR CLI adapters (pi/codex/opencode/
    claude — their build functions differ materially); add a
    `SubscriptionWorker` test (inherits `CodingAgentWorker`, subscription.py:65
    — Stage 3 changes flow into it); Stage-0 journal end-to-end and
    executor retry/exclusion tests per Stage 0. `tests/test_tools.py:125`
    consumes `fit_messages` — keep it green (fit_messages unchanged).
11. **Documented non-goal (F9)**: `ContextVersionBindings` has a dead-end
    (breaking-version validator demands `evidence_snapshot_version=None` but
    the field is non-nullable, models.py:103 vs :111). The live path never
    passes a `-breaking-` harness_version. Do NOT touch the frozen validator;
    add a code comment at the live-assembler call site noting the constraint.

## Hard boundaries (from the frozen card — violations are stop conditions)

- Owned paths ONLY (v2 set): `src/metaharness/context`,
  `src/metaharness/harness/local.py`, `src/metaharness/harness/coding.py`,
  `src/metaharness/harness/runner.py`, `src/metaharness/workflows/journal.py`,
  `src/metaharness/core/types.py`, `src/metaharness/core/executor.py`,
  `tests/test_context.py`, `tests/test_local_worker.py`,
  `tests/test_coding.py`, `tests/test_harness.py`, `tests/test_run_events.py`,
  `tests/test_executor.py`, `tests/test_workflows.py`, `tests/adversarial`.
  types.py/executor.py/runner.py/journal.py changes are LIMITED to the Stage-0
  items — no other edits in those files.
- No new authority: no evaluator/merge/promotion/deployment/credential grants;
  memory broker stays shadow; H/E/W frozen; selflearn untouched.
- Do not weaken `models.py` validators or delete META-4/5/6 assertions except
  the explicitly superseded shadow-only ones (cite META-19 at each).
- Determinism: no wall-clock, no randomness in assembly.
- Do not commit; report per stage with test counts + deviations.

## Acceptance commands (frozen)

```
/private/tmp/meta-harness-meta-19/.venv/bin/pytest -q tests/test_context.py tests/test_local_worker.py tests/test_coding.py tests/test_harness.py
/private/tmp/meta-harness-meta-19/.venv/bin/pytest -q tests/test_run_events.py tests/test_executor.py tests/test_workflows.py tests/adversarial
/private/tmp/meta-harness-meta-19/.venv/bin/pytest -q
node --test scripts/workplan.test.mjs
git diff --check
```
