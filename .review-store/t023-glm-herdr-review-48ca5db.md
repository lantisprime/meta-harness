# TASK-20260725-023 — Herdr-driven Pi/NeuralWatt GLM-5.2 frozen-diff review

- Session: `drv-t023-glm-0726a`
- Pane: `w1:p2`
- Seat: `pi --provider neuralwatt --model glm-5.2 --thinking high --no-extensions --no-session --approve --tools read,grep,find,ls`
- Frozen base: `c27ff5e1475d051922e7d97d7296619b26f4d121`
- Frozen head: `48ca5db92cee45f3700a4c15841c6075bb95cfdc`
- Completion telemetry: `↑61k ↓33k R702k CH99.8% $0.490 8.8%/1.0M`
- Startup verification: no Extensions section and no permission-status line.
- Repository permissions: read-only tool allowlist; no edit/write tool.

## Verbatim reviewer output

```text
I have completed a thorough read-only review of the
frozen head 48ca5db (base c27ff5e), inspecting
scripts/workplan.mjs and scripts/workplan.test.mjs
directly with file-and-line evidence. Below is the
review.

────────────────────────────────────────────────────

Verdict: APPROVE

No findings at any severity. All seven acceptance
criteria and the charter invariant (stage‑6
lifecycle-receipt integrity; H/E/W untouched) are
satisfied. The declared residual is neither exceeded
nor misstated.

### P0 — none

### P1 — none

### P2 — none

### P3 — none

### Acceptance-criteria verification (file:line
evidence)

1. Returning in_progress→blocked→in_progress detour
deletion rejected before either canonical file is
written.
- Mechanism: appendReceipt count-vs-length check,
  scripts/workplan.mjs:1328-1332 (receipt ledger
  count N does not match receipts length M).
- The anchoring derives anchoredReceiptCount via
  Object.hasOwn(card,"receiptCount") ?
  card.receiptCount : card.receipts.length
  (workplan.mjs:1314-1316), so deleting the detour
  pair (leaving receiptCount unchanged) makes 5 ≠ 3.
- submit invokes appendReceipt at workplan.mjs:1993,
  strictly before the WORKPLAN.md write (:2004-2009)
  and the state.json write (:2012-2017).
- Proof test scripts/workplan.test.mjs:9227-9340:
  reproduces the detour strip, asserts exit≠0,
  asserts /receipt ledger count 5 does not match
  receipts length 3/, and asserts byte-identity of
  BOTH state.json (:9316-9317) and WORKPLAN.md
  (:9318-9320) — fail-before-write.

2. Clearing the whole receipts array on an anchored
card rejected; no false genesis.
- Same count check :1328-1332: receiptCount=2 ≠
  length 0.
- start invokes appendReceipt at :1906 before its
  canonical writes.
- Proof test workplan.test.mjs:9289-9341: empty
  array + retained receiptCount → exit≠0, /receipt
  ledger count 2 does not match receipts length 0/,
  both files byte-preserved.
- False-genesis is correctly impossible on an
  anchored card: genesis only proceeds when
  receiptCount is absent (Object.hasOwn gate :1314);
  that absent-count+empty-array case is the declared
  residual and is pinned as intended at
  workplan.test.mjs:9079-9126.

3. Legitimate transitions remain valid under
unrelated-card board advancement; no tail.revisionTo
=== revisionFrom rule.
- appendReceipt (:1304-1349) contains only tail.to
  !== from (:1319-1321); it has no tail.revisionTo
  === revisionFrom comparison anywhere.
- Pre-existing tests workplan.test.mjs:5362 ("accept
  succeeds when an unrelated card advanced the board
  after integrate") and :5592 ("block from verifying
  succeeds when an unrelated card advanced the
  board") exercise appendReceipt with revisionFrom >
  tail.revisionTo and assert success — so the
  forbidden rule would have broken them.
- Design intent confirms the rule is rejected
  (memory/session_handoff.md, session-56 entry: "Do
  not adopt kimi's proposed tail.revisionTo ===
  revisionFrom fix as written — the global revision
  counter advances from unrelated cards").

4. Legacy cards without receiptCount migrate
explicitly on next append.
- Migration trigger:
  Object.hasOwn(card,"receiptCount") ?
  card.receiptCount : card.receipts.length
  (:1314-1316); migration action: card.receiptCount
  = anchoredReceiptCount + 1 (:1348).
- Proof test workplan.test.mjs:9343-9367: delete
  receiptCount, start succeeds, receipts.length===3
  and receiptCount===3 after.

5. Negative / fractional / string / count-vs-length
fail closed.
- Integer/non-negative guard :1322-1327
  (!Number.isInteger(anchoredReceiptCount) ||
  anchoredReceiptCount < 0 → "must be a nonnegative
  integer"); Number.isInteger rejects strings,
  booleans, NaN, fractions without coercion.
- Count-vs-length guard :1328-1332.
- Proof test workplan.test.mjs:9370-9448:
  negative(-1), fractional(1.5), string("2") all →
  /receipt ledger count must be a nonnegative
  integer/; length-mismatch(3 vs 2) → /receipt
  ledger count 3 does not match receipts length 2/;
  all exit≠0 and byte-preserve.

6. Existing tail-mismatch diagnostics retain
precedence.
- Error ordering in appendReceipt: tail check
  :1319-1321 runs before the integer/nonneg check
  :1322-1327 and the count-vs-length check
  :1328-1332.
- Proof test workplan.test.mjs:9195-9225: a card
  with both a tail mismatch (popped claimed receipt;
  tail ready) and a count mismatch (receiptCount=2,
  length=1) fails with the existing tail message
  /ledger tail ready does not match transition from
  claimed/, confirming precedence, with
  byte-preservation.

7. Change confined to the two declared files.
- Production change is entirely inside appendReceipt
   (workplan.mjs:1304-1349), specifically the
  anchoring at :1312-1348; the validateReceiptChain
  residual comment (:1411-1426) and receiptCount
  single-choke-point (receiptCount is read/written
  only at :1314, :1315, :1348) confirm no hoisting
  to validateState or command entry.
- No exported symbol or signature change (the export
  block at end of file is unaltered in surface), so
  no caller ripple.
- Build spec bounds the slice:
  .agents/t022-build-spec.md "Writable slice:
  scripts/workplan.mjs, scripts/workplan.test.mjs.
  Nothing else"; the frozen stop condition forbid
  "any edit outside the two reserved paths."
- Repo-wide grep for
  receiptCount|appendReceipt|validateReceiptChain
  outside scripts/**: only non-code artifacts
  (.agents/* docs, .workplan/state.json card traces,
  .review-store/* prior reviews,
  memory/session_handoff.md) — none are code edited
  by this builder's commit, and
  memory/session_handoff.md's most-recent entry
  documents the precursor card (META‑31, "5 net
  production lines") plus the filing of
  TASK‑20260725‑023, i.e. it is durable uncommitted
  control-root state, not part of this diff.

### Residual boundary (reviewed per brief
instruction)

The declared residual — "simultaneous deletion or
rewrite of both receiptCount and the receipt records
so the card presents as legacy" — is accurately
stated (validateReceiptChain comment :1411-1426;
genesis-on-empty-nonanchored pinned at
workplan.test.mjs:9079-9126) and the implementation
does not exceed it: the anchoring catches
deletion-without-count-fix (the realistic
append-side tampering) and the hash-chain
(validateReceiptChain at accept/block/resume) still
independently validates any surviving chained
records. ReceiptCount-only deletion (keeping chained
records) is self-healing on next append (:1348) and
does not disable the chain binding. No exceedance,
no misstatement → no finding, per the brief's
flag-only-if rule.

### Methodology / transparency notes (not findings)

- I operated read-only with read/grep/find/ls only;
  the env provided no bash, so I could not execute
  git diff c27ff5e..48ca5db, git diff --check, or
  node --test. Scope (criterion 7) is therefore
  verified by direct file inspection + build-spec
  constraints + orchestrator-supplied stat, not by
  an independent git diff. The logic, ordering, and
  call-site claims above are all traced in the
  frozen-head source itself.
- I cannot introspect my own runtime model identity;
  confirming that this gate was actually satisfied
  by Pi/NeuralWatt/GLM‑5.2 (per AGENTS.md) rests
  with the outer orchestrator. The technical review
  above is independent of the implementing Codex
  seat.

No unresolved P0 or P1. The change may advance from
Review per the Pi/NeuralWatt GLM‑5.2 gate; the
coordinator retains integration, verification, and
acceptance authority.
```

## Orchestrator dispositions

- Verdict: `APPROVE`.
- P0: none.
- P1: none.
- P2: none.
- P3: none.
- Methodology note: accepted as transparent limitation; orchestrator-owned test
  and diff evidence remains the executable verification record.
- Model/transport identity: confirmed externally by the Herdr launch command and
  pane status as Pi provider `neuralwatt`, model `glm-5.2`, thinking `high`.
