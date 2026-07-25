# TASK-20260725-023 — receipt-ledger completeness anchor

Card: `TASK-20260725-023` / Linear `META-32`. Board revision before
qualification: **145**. Baseline: `main` at `c27ff5e`, workplan suite
**151/151 green**.

Writable slice:

- `scripts/workplan.mjs`
- `scripts/workplan.test.mjs`

`H`, `E`, and `W` remain frozen.

## Defect

The hash chain proves the integrity and order of records that remain in
`card.receipts`; it does not prove that the array is complete.
TASK-20260724-022 added `tail.to === from`, which catches a stripped tail when
the surviving status differs. It cannot catch either of these shapes:

1. A legitimate `block → resume` detour starts and ends at the same status.
   Delete both records and the older tail still satisfies `tail.to === from`.
2. Delete the whole receipts array. The next transition sees no tail and writes
   a fresh `prevEntryHash: null` genesis.

Both attacks erase lifecycle evidence before a normal transition permanently
re-anchors the surviving ledger.

## Invariant

Add one optional per-card scalar, `receiptCount`, outside the receipts array.
It is the append-side completeness anchor.

Before each append:

1. Materialize the existing receipts array using the current legacy behavior.
2. If `receiptCount` is absent, treat the card as legacy and initialize the
   expected count from the current receipts length.
3. If `receiptCount` is present, require a nonnegative integer.
4. Require the anchored count to equal the receipts array length.
5. Run the existing tail/status continuity check.
6. Construct and append the new chained receipt.
7. Store `receiptCount = previous length + 1`.

The count must be checked before a new record is created and before either
canonical file is written. A mismatch gets one distinct, greppable error.

Existing `entryHash` / `prevEntryHash` checks continue to prove content and
ordering integrity. The external count supplies the missing completeness
signal.

## Why global revision equality is forbidden

Board revisions are global. Another card may transition between two
transitions of this card, so a valid ledger may have
`tail.revisionTo < revisionFrom`. The implementation must not use equality or
assume adjacent board revisions.

## Required tests

1. Returning detour: reach `in_progress`, block, resume, delete the two trailing
   records, then submit. The surviving tail still ends at `in_progress`; the
   count mismatch must reject and preserve both canonical files byte-for-byte.
2. Full truncation: reach an anchored non-backlog state, clear `receipts`, then
   run the next legitimate transition. It must reject and preserve both files.
3. Legacy migration: remove `receiptCount` from an otherwise valid legacy
   fixture. The next append succeeds and writes the exact resulting count.
4. Anchored happy path: each normal transition increments the count exactly
   once and keeps it equal to `receipts.length`.
5. Unrelated board activity: transition another card between two transitions
   of the subject card; the next transition still succeeds.
6. Invalid anchors: negative, fractional, string, and count/length mismatch
   fail closed without canonical mutation.
7. Existing 151 tests remain unchanged and green.

## Explicit residual

A tamper that deletes or rewrites both `receiptCount` and receipt records can
present the card as legacy and migrate on the next append. That is a
rewrite-with-recompute / downgrade attack outside this card, analogous to the
existing declared residual for wholesale chain-field removal. This task closes
record deletion while the external anchor remains present; it does not add a
separate immutable board-level journal.

## Verification

```text
node --test scripts/workplan.test.mjs
git diff --check c27ff5e1475d051922e7d97d7296619b26f4d121
```

The frozen exact diff then receives the mandatory Pi / NeuralWatt GLM-5.2
read-only review. No unresolved P0 or P1 may advance.
