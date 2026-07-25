# TASK-20260724-022 — plan review (pre-build gate)

- Seat: `pi --provider neuralwatt --model glm-5.2 --thinking high --no-extensions --no-session --approve --tools read,grep,find,ls`
- Herdr private session `drv-t022-plan-7464`, pane `w1:p1`, torn down at completion.
- Target: `.agents/t022-build-spec.md` @ pre-fix revision, against `main` @ `07c5095`.
- Cost/usage at completion: ↑47k ↓48k, R1.2M, CH99.8%, $0.710, 8.4%/1.0M context.
- Transcribed by the orchestrator from the seat's pane output (herdr `pane read`).

## VERDICT: APPROVE

Reviewer states it verified every `appendReceipt` call site (1459, 1556, 1888, 1975, 2092,
2463, 2651, 2761, 2881), every `card.status =` mutation (1456, 1553, 1896, 1984, 2106, 2445,
2649, 2771, 2875), the cited guards, and every cited test.

## Challenge verdicts

- **A (use `from`, not `card.status`)** — APPROVE. The 5-before / 4-after split table is
  "correct and complete". `from` is the true pre-transition status at all 9 sites, including
  `block`, where `from = card.blockedFrom` is captured at 2646 before `card.status = "blocked"`
  at 2649.
- **B (cannot preempt an existing message)** — APPROVE. accept 2416/2417 < 2463; block 2635 <
  2651; resume 2738 + 2748 < 2761. For the other 6 sites there are no existing receipts-tail
  checks to preempt, and no existing test tampers a tail into mismatch then runs those commands
  expecting a different message (verified by grepping every
  `receipts.{push,splice,filter,pop,at(-1)}` in the test file). The do-not-hoist conclusion is
  sound: hoisting breaks 8001 and 8789 regardless of how `from` is derived.
- **C (legacy tolerance not too strong)** — APPROVE. All legal transitions preserve
  `tail.to === from`, verified by walking the linear DAG plus block/resume detours and
  multi-cycle sequences on the live board (e.g. `TASK-20260719-012`:
  ready→claimed→in_progress→review→blocked→in_progress→review→verifying→done). Both legacy
  fixtures are internally consistent.
- **D (closes the codex P2)** — APPROVE. Traced: strip `ready→claimed`, run `start`; `start`'s
  guard at 1886 passes on `card.status === "claimed"`, then `appendReceipt(card, "claimed",
  "in_progress", …)` at 1888 fires the new check — `tail.to = "ready" ≠ "claimed"` → fail.
  The only attacker escape (reset `card.status` to `"ready"`, re-run `claim`) self-negates: it
  merely regenerates the original `ready→claimed` record, destroying nothing.
- **E (misses)** — no P1/P2 miss. `cancelled` is in `VALID_STATUSES` (workplan.mjs:56) but no
  command assigns it or transitions out of it, so no `appendReceipt` ever fires with
  `from = "cancelled"`; the invariant is unreachable there. The declared-residual test at 8877
  is provably unaffected: stripping chain *fields* leaves every `from`/`to` pair intact, so
  `tail.to = "verifying" === from`, and the test still expects exit 0.

## Findings — 4 × P3, 0 × P2, 0 × P1

| # | Location | Finding | Disposition |
|---|---|---|---|
| 1 | spec §3 | Cited 7941/8641/6780 are test *body* lines; the actual regex assertions are at `workplan.test.mjs:8001`, `:8789`, `:6786`. The design claim is verified true; only the pointers were imprecise. | ACCEPT — pointers corrected |
| 2 | spec §3 | "Verified for accept and block" then generalizes to all 9 sites without citing `resume`'s equivalent precedent (2738 + 2748 precede 2761), the canonical pre-existing tail check at that site. | ACCEPT — citation added |
| 3 | spec §3 | For the 6 append-only commands there are no pre-existing receipts checks, so the new check is the *first* receipts-relevant check there, not a *last* one. Broad "cannot preempt" claim still holds. | ACCEPT-WITH-MOD — §3 reworded |
| 4 | spec §4 | "tail.to === status on 18/18" overstates: `TASK-20260714-001` and `TASK-20260724-022` carry no `receipts` key, so they satisfy rule 4.1 *vacuously*, having no tail. Rest of the live-board evidence confirmed correct (zero chained records anywhere). | ACCEPT — §4 reworded to 16 with tails + 2 vacuous |

All four are documentation-precision items. No change to the design: placement inside
`appendReceipt`, comparison against the `from` argument, and the tolerate-absence/enforce-on-
mismatch legacy rule all survive review unmodified.
