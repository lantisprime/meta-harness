# TASK-20260724-022 — ledger transition-completeness (frozen build spec)

Card: `TASK-20260724-022` / Linear META-31. Board revision at freeze: **138**.
Baseline: `main` @ `07c5095`, node suite **143/143 green** (`node --test scripts/workplan.test.mjs`).
Writable slice: `scripts/workplan.mjs`, `scripts/workplan.test.mjs`. Nothing else.

## 1. The defect

Deferred codex P2 from the TASK-20260724-019 panel
(`.review-store/t019-codex-review-b141afd.txt:31`), verbatim:

> | P2 | scripts/workplan.mjs:1340; scripts/workplan.mjs:1867 | Append-side transitions do
> not validate the existing ledger or status-to-tail continuity, so a stripped tail is
> permanently re-anchored. | Remove `ready→claimed` from a claimed card and run `start`; the
> new `claimed→in_progress` receipt anchors to `backlog→ready`, and all later chain
> validation passes despite the missing transition. |

Root cause: hash-chaining proves the **integrity of records that are present**. It cannot
prove **completeness**. Stripping the tail leaves a structurally valid, shorter chain; the
next legitimate append re-anchors `prevEntryHash` across the gap and every later validation
passes. `validateReceiptChain`'s own docstring disclaims this
(`scripts/workplan.mjs:1340-1343`).

The narrow precedent already in tree is the verifying-only positional rule
(`scripts/workplan.mjs:2347-2360`). This card generalizes that idea to every transition.

## 2. The invariant

At every append, the ledger tail must be the record that *ended* at the status this
transition is *leaving from*.

**Enforce: `tail.to === from`** — where `from` is the argument already passed to
`appendReceipt`, NOT `card.status`.

### Why `from`, not `card.status` (non-negotiable)

`card.status` is mutated **before** `appendReceipt` at 5 of 9 call sites and **after** at the
other 4. Verified:

| status already NEW at append | status still OLD at append |
|---|---|
| `claim` 1456→1459 · `claim-next` 1553→1556 · `accept` 2445→2463 · `block` 2649→2651 · `ready` 2875→2881 | `start` 1888→1896 · `submit` 1975→1984 · `integrate` 2092→2106 · `resume` 2761→2771 |

Any implementation reading `card.status` inside `appendReceipt` is wrong at half the call
sites. The `from` argument is the pre-transition status at all 9 sites, including `block`
(which passes `card.blockedFrom`, assigned from `card.status` before the overwrite,
`scripts/workplan.mjs:2646-2653`).

## 3. Placement

Inside **`appendReceipt`** (`scripts/workplan.mjs:1304-1331`), before the record is built.

It is the single true choke point: all 9 append sites across 8 mutating commands funnel
through it, and `card.receipts` is already in memory (no new I/O).

**Placement is load-bearing for test compatibility, not just convenience.** The three commands
that already carry receipts-relevant checks run them *before* reaching `appendReceipt`:

- `accept` — `validateIntegrationReceipt` (2416) + `validateReceiptChain` (2417) precede 2463
- `block` — `validateReceiptChain` (2635) precedes 2651
- `resume` — `latestReceipt.to !== "blocked"` guard (2738) + `validateBlockedReceipt` (2740)
  + `validateReceiptChain` (2748) all precede 2761

For the other 6 commands (`ready`, `claim`, `claim-next`, `start`, `submit`, `integrate`) there
are **no** pre-existing receipts-relevant checks, so the new check is the *first* such check at
those sites — there is nothing there for it to preempt. Net: the new check can never displace an
existing error message at any of the 9 sites.

**Do NOT hoist this as a blanket pre-check at command entry.** Doing so breaks at minimum these
three assertions (line numbers are the `assert` lines themselves):
- `scripts/workplan.test.mjs:8001` — expects `/integrationReceipt binding record missing in ledger/`
- `scripts/workplan.test.mjs:8789` — expects `/ledger tail is not the verifying record|integrationReceipt binding hash mismatch/`
- `scripts/workplan.test.mjs:6786` — expects `/is not in blocked status/`

All three construct a tail/status mismatch deliberately and assert on the *existing*
narrower message.

## 4. Legacy tolerance — the exact rule

Tolerate **absence**, never **mismatch**.

1. `card.receipts` absent or empty → **allow** (genesis; also the seeded bootstrap card
   `TASK-20260714-001`, which is `done` with zero receipts on the live board).
2. Tail present → **enforce `tail.to === from` regardless of whether the tail is chained**
   (i.e. regardless of the `entryHash` key). Legacy records are still records; a legacy
   ledger is exactly where this attack is cheapest.
3. On violation, `fail()` with a distinct, greppable message. Proposed:
   `ledger tail ${tail.to} does not match transition from ${from}`.

### Evidence this rule does not brick anything

Live board audit, all 18 cards: **16 cards have a ledger tail, and `tail.to === status` on all
16. The remaining 2 (`TASK-20260714-001`, seeded `done`; `TASK-20260724-022`, `backlog`) carry
no `receipts` key at all and satisfy rule 4.1 vacuously — there is no tail to compare. 0 cards
carry any chained record.** Both hand-authored legacy fixtures are internally consistent —
`scripts/workplan.test.mjs:8123-8132` (tail `ready`, status `ready`) and
`legacyInProgressRoot` `:8317-8342` (tail `in_progress`, status `in_progress`).

Rejected alternative: "enforce only when the tail is chained." Strictly weaker — it would
leave every legacy board (i.e. *the entire live board today*) undefended, which is the
population the attack actually targets.

## 5. Out of scope — do not re-litigate

- **Declared residual**: wholesale removal of all chain keys / rewrite-with-recompute
  (`scripts/workplan.mjs:1368-1376`), pinned by `scripts/workplan.test.mjs:8877`. That test
  strips *fields*, never a *record* — tail still matches status — so it must keep passing
  untouched.
- **codex P1-A mixed-era caveat** — accepted as documented in `.agents/t019-definition.json:13`.
- **The gateway.** The card trace names it, but scout verification found **zero coupling**:
  `development/remote_workplan/gateway.py` never references `workplan.mjs`,
  `.workplan/state.json`, or `card.receipts`; `grep -n "gateway" scripts/workplan.mjs`
  returns nothing. It is a code-disjoint reimplementation over its own SQLite ledger. Treat
  the trace's mention as precautionary. Do not edit it.

## 6. Required tests (each must cite the behavior it pins)

1. **The codex repro, exactly**: legitimately reach `claimed`, strip the `ready→claimed`
   record, run `start` → must fail with the new message. This is the card's reason to exist.
2. Same strip shape on a **fully legacy** (un-chained) ledger → must fail.
3. Genesis: card with no `receipts` key → `ready` succeeds (regression on
   `scripts/workplan.test.mjs:8195`).
4. Empty `receipts` array + non-backlog status → allow (rule 4.1), pinned explicitly since no
   existing test covers it.
5. Happy path per transition: `ready`/`claim`/`start`/`submit`/`integrate`/`block`/`resume`
   still append normally with a consistent tail.
6. `block` uses `blockedFrom` as `from` — a card blocked from `review` must pass the check.
7. `resume` lands in `in_progress` from `blocked` — tail `to:"blocked"` must satisfy `from`.
8. Byte-preservation on rejection: state.json unchanged after a rejected transition
   (mirror the existing `preserves bytes` pattern).

## 7. Verification commands

```
node --test scripts/workplan.test.mjs      # expect 143 + new tests, 0 fail
```
Full pytest suite is unaffected (no Python touched) but run it once for the record:
```
uv run pytest -q
```

## 8. Constraints

- Builder does **not** commit, push, or touch `.workplan/state.json`.
- No edits outside the two writable paths.
- Do not weaken or reword any existing assertion to make a new check fit; if an existing test
  genuinely conflicts, STOP and report it rather than editing it.
