# TASK-20260724-022 — independent behavioral verification @ `932ffca`

- Seat: `pi --provider minimax --model MiniMax-M3 --thinking high --no-extensions --no-session --approve`
- Herdr private session `drv-t022-verify-99488`, cwd `/tmp/verify-t022` (disposable roots only —
  the live board was never touched). Cost ~$0.24, 7.9%/1.0M context.
- Method: the verifier built its own harness and drove the **real** workplan CLI against throwaway
  git repos. Repository test results were explicitly not accepted as evidence.

## Result on the card's own claims: fully verified

> "P1–P5, P7 all pass; P3c (bonus) also passes."

| Probe | What it proves | Result |
|---|---|---|
| P1 | The codex P2 repro: strip `ready→claimed`, run `start` → rejected with the new message, and `state.json` byte-identical after the failed run | PASS |
| P2 | Same strip on a fully legacy un-chained ledger → same rejection (legacy is defended, not exempt) | PASS |
| P3 | Absence tolerated: no `receipts` key → `ready` succeeds; empty array + non-backlog status → transitions | PASS |
| P3c | Bonus: empty array on a `claimed` card also succeeds | PASS |
| P4 | Full happy path ready→claim→start→submit→integrate→accept, plus block and resume | PASS (anti-bricking) |
| P5 | Multi-cycle: to verifying → block → resume → re-integrate | PASS |
| P7 | Emitted message matches the frozen wording with real statuses interpolated | PASS |

## The residuals it found — P6 family and P8

The verifier's headline `VERIFY: FAILURES FOUND` refers to **bypasses of the invariant**, not to
failures of the card's claims. Two distinct classes:

**Class 1 — bypass at append, caught downstream (defense-in-depth working).** The new check is
append-side only; `validateReceiptChain` remains the accept/block/resume-side net.

| Variant | Append | Later outcome |
|---|---|---|
| 6.1 strip first receipt (`backlog→ready`) | bypassed | accept fails: `receipt chain prevEntryHash mismatch` |
| 6.2 strip two leading receipts | bypassed | chain validator catches later |
| 6.3 duplicate the tail | bypassed | chain validator catches later |
| 6.5 reset `card.status` to match a stripped tail | bypassed (claim exit 0) | accept fails: `receipt chain entryHash recompute mismatch` |

6.5 also empirically confirms the design choice under review: manipulating `card.status`
independently of the ledger has **no effect** on the new check, because it compares the `from`
argument.

**Class 2 — P8, the returning-detour strip: uncaught by every layer.** Verbatim:

```
Step 2: block from in_progress, then resume.
  ... claimed -> in_progress / in_progress -> blocked / blocked -> in_progress
Step 3: DELETE the last two records.   Before strip, count: 5   After strip, count: 3
  Ledger: backlog->ready / ready->claimed / claimed->in_progress   Status (unchanged): in_progress
Step 4: Run submit.
  EXIT: 0    STDOUT: {"revision":7,"submitted":true}
Running: wp integrate P8-CARD
  EXIT: 0    STDOUT: {"revision":8,"integrated":true}
```

Because the detour starts and ends at the same status, the surviving tail satisfies
`tail.to === from`, and after erasure the chain is *internally consistent* — so unlike the
Class-1 variants, `validateReceiptChain` does not catch it at accept either. The block/resume
evidence (`blockReason`, `retainPaths`, `blockedFrom`) is silently and permanently lost.

This reproduces exactly what both panel lenses predicted independently, now with live evidence.

## Disposition

P8 is a limitation of the **frozen invariant**, faithfully implemented — not an implementation
defect. Closing it needs a strictly stronger rule (per-card sequence continuity or no-revisit
detection), which the card's stop condition explicitly forbids adding in-card. Net effect of this
card is still a large reduction in attack surface: before it, *any* tail strip re-anchored
silently; now only a same-status returning detour does.

Routed to a follow-up card rather than reopening this one — the same pattern by which this card
itself was created from the T019 panel's deferred P2.

Note on kimi's proposed fix (`tail.revisionTo === revisionFrom`): unsafe as written. The global
revision counter advances from unrelated cards, which is why `validateIntegrationReceipt` uses a
`<=` bound rather than equality (`workplan.mjs:2273-2292`). The follow-up must not adopt it
verbatim.
