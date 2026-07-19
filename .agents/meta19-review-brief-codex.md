# META-19 Frozen-Diff Review Brief

You are an INDEPENDENT correctness reviewer. Read-only. Do not edit anything.
Review the immutable diff BASE..HEAD in the worktree
/private/tmp/meta-harness-meta-19 (branch dev/meta-19-live-context-envelope).

- BASE: 46c60fad04eb92f07d33b5c02f00e2fd9f30413f
- HEAD: c3247044041368afa09c310ccf3aedfdf61d6d1b  (review
  `git diff 46c60fad04eb92f07d33b5c02f00e2fd9f30413f..c3247044041368afa09c310ccf3aedfdf61d6d1b`
  only; ignore any uncommitted state)

## What the change claims

META-19 promotes the typed ContextEnvelope from a shadow sidecar to the single
live prompt assembler for both worker families:

1. New `src/metaharness/context/live.py`: `assemble_live()` takes caller-declared
   `SectionDraft`s, enforces trust rules fail-closed BEFORE any model call
   (untrusted/generated content can never occupy SYSTEM_INSTRUCTIONS /
   RESPONSE_CONTRACT / TOOL_SCHEMAS slots; evaluator receipts never
   INSTRUCTION), redacts secrets BEFORE fitting, fits per section
   deterministically, and returns EVERY redacted transport surface (chat
   messages, tool schemas, flat prompt, system prompt) + envelope + manifest.
   Claim: sent bytes are DERIVED from the validated envelope, so
   manifest-vs-sent divergence is impossible by construction.
2. `harness/local.py` (OpenAICompatWorker) and `harness/coding.py`
   (CodingAgentWorker + SubscriptionWorker via inheritance) consume it; the
   legacy ad-hoc builders, byte-identity shadow observer, shadow events, and
   fall-back-to-legacy-on-error path are REMOVED (deliberate: this card lifts
   the shadow-era freeze). Contract violations now fail the attempt loudly.
3. `workflows/journal.py`: `context.manifest` added to CANONICAL_KINDS —
   the manifest is journaled per attempt round as the authoritative
   "what did the model see" record.
4. `core/types.py`: additive `Task.advice` + `WorkerResult.error_kind`.
   `core/executor.py`: reflexion/knowledge advice now travels in
   `task.advice` (rendered as untrusted-derived feedback), NOT laundered into
   `task.boundaries`; context-contract failures abort retries and are excluded
   from routing/capability evidence. `harness/runner.py`: maps
   `LiveContextViolation` → `error_kind="context_contract"`.
5. `context/models.py`: ONLY additive change allowed = `flat_prompt` /
   `system_prompt` literals in `ContextManifestEntry.surface`. Any other
   models.py weakening is a P0.

## Contract properties to verify (charter + frozen card)

- No new authority: no evaluator/merge/promotion/deployment/credential grants;
  memory broker stays SHADOW (any memory-broker change is P0); H/E/W frozen.
- Trust enforcement must hold for every worker call-site input: can you
  construct a task/system_prompt/inputs/advice/tool-observation value that
  lands untrusted content in an instruction slot, or reaches the model unredacted on ANY
  sent surface (message content, tool schemas, flat prompt, argv system
  prompt)? Data-vs-instruction separation for task.inputs, tool output, and advice deserves
  careful verification.
- Divergence: any path where the manifest attests something other than the
  bytes actually sent (per adapter: pi/codex/opencode/claude stdin+argv;
  local: request body). Truthful system-prompt attestation: codex/opencode
  transport NO system prompt — a manifest claiming otherwise is P0.
- Determinism: identical inputs must yield identical envelope/manifest hashes.
- Journal: one context.manifest event per attempt round, attempt-scoped,
  surviving WorkflowEngine → Journal round-trip; telemetry failure must not
  fail the model call, but assembly failure must (no silent fallback).
- Executor: context_contract aborts retries AND is excluded from
  matrix.record; normal failures unchanged. Advice no longer reaches
  boundaries — check nothing else still merges untrusted text into an
  instruction-trust surface.
- Tests: do the new tests actually prove the claims (e.g. does the
  trust-violation test really assert the endpoint was never hit; does the
  redaction test inspect the true request bytes)? Weak tests are findings.

## Known context (do not re-report)

- Builder deviations already accepted by the orchestrator: (1) coding worker
  renders task.advice as untrusted feedback; (2) tests/test_correction.py and
  tests/test_knowledge_adapter.py updated for the advice-channel move (owned-
  path census oversight, recorded); (3) assistant tool-call turns render
  content "" instead of legacy None.
- F9: ContextVersionBindings breaking-version dead-end is documented, not
  fixed (frozen validator).
- Full suite at HEAD: 1219 passed / 1 skipped / 2 xfailed (xfails =
  META5-MEM-009/011, reserved).

## Output format

Verdict: ACCEPT / ACCEPT-WITH-MOD / REJECT, then numbered findings, each:
severity P0/P1/P2/P3, CONFIRMED (you traced the failing path end to end) or
PLAUSIBLE, file:line evidence, one-line fix suggestion. No style nits. Do not
restate the diff. Every finding needs file-and-line evidence.
