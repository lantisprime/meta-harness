# META-19 Fix Batch 1 — panel findings (codex REJECT + opus P3s)

Frozen head reviewed: `c324704`. Apply fixes ON TOP in the worktree
`/private/tmp/meta-harness-meta-19`. Do NOT commit. Definition v3 adds
`src/metaharness/workflows/engine.py` (FIX-1 only) to owned paths.
Every fix gets a regression test citing its FIX id.

- **FIX-1 (codex#1, P1)** `workflows/engine.py:412-415`: step boundaries
  containing dynamic `$steps.*` output references currently resolve prior
  worker output INTO `task.boundaries` → workers declare them
  RESPONSE_CONTRACT/INSTRUCTION. Route: a boundary whose TEMPLATE contains a
  `$steps.` reference goes (resolved) into `Task.advice`; boundaries with only
  `$context.*` refs or no refs stay boundaries. Limited change; test in
  tests/test_workflows.py.
- **FIX-2 (codex#2, P1)** `context/live.py`: `SectionDraft.role` is an open
  string; an UNTRUSTED draft with role="system" renders into the system
  message. Make role `Literal["system","user","assistant","tool"] | None` and
  enforce in the pre-render gate: role="system" requires trust INSTRUCTION
  AND section_type SYSTEM_INSTRUCTIONS; violations raise
  LiveContextViolation.
- **FIX-3 (codex#4, P1)** `harness/local.py`: `body.update(self.extra_body)`
  can replace attested surfaces post-assembly. Reject reserved keys
  ("messages", "tools") in extra_body at `__init__` (ValueError — config
  error, not a per-call violation). Test: constructing with
  extra_body={"messages": ...} raises.
- **FIX-4 (codex#5, P1)** `context/live.py`: `tool_call_id` bypasses
  redaction (model-generated string rendered verbatim). Redact it like
  content/tool_calls before rendering/manifest.
- **FIX-5 (codex#6, P1)** `context/live.py`: structured `tool_calls` are not
  counted by `messages_tokens` → unbudgeted bytes reach the endpoint (10KB
  args accepted under a 20-token budget). Include
  `estimate_tokens(canonical_json(tool_calls))` (and tool_call_id) for every
  rendered message in the hard-budget postcondition. Overflow raises
  LiveContextViolation (deterministic fail-closed; do NOT truncate structured
  turns).
- **FIX-6 (codex#7 P1 + opus P3-1 + opus P3-2)** `context/live.py` +
  workers: manifests attest surfaces the adapter never transmitted, and
  `budget_used_tokens` reports the chat total even for CLI transport. Add a
  required `transport: Literal["chat","cli"]` param to `assemble_live`:
  chat → emit message + tool_schemas entries only; cli → flat_prompt (+
  system_prompt when declared) entries only. `budget_used_tokens` reports the
  transmitted-surface total. Workers pass their transport. The envelope
  sections stay identical across transports.
- **FIX-7 (codex#8, P1)** `harness/runner.py`: `error_kind` drives retry
  abort + capability-evidence exclusion but is not signature-attested (a
  tampered signed result flips it and still verifies). Add signature version
  3 attesting `error_kind` (pattern: existing v1→v2 precedent in
  `_signature_payload`; v1/v2 remain verifiable; new results sign v3).
  Update the workspace-attestation gate only if it version-checks (>=2 stays
  correct).
- **FIX-8 (codex#9, P2)** `workflows/journal.py`: `context.manifest` events
  validate without `attempt_id`. Require attempt_id for the kind (same
  mechanism as attempt.*/tool.*) + negative test.
- **FIX-9 (opus P3-3)** `context/live.py`: a zero-budget OMITTED section
  inside a merged message yields a message receipt labeled HEAD_TAIL.
  Propagate an honest reason (e.g. action stays HEAD_TAIL only for actual
  digest fitting; omission-driven change gets reason "section omitted at
  zero budget").

DEFERRED (do not implement): codex#3 MCP tool-schema provenance —
pre-existing condition, follow-up card will be filed.

After fixes: rerun (a) focused suites, (b) run-events/executor/workflows/
adversarial, (c) FULL pytest, (d) HOME=$(mktemp -d) node --test
scripts/workplan.test.mjs, (e) git diff --check. Report verbatim counts,
per-fix summary, and any deviation. Worktree venv only.
