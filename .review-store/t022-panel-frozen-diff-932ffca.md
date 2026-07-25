# TASK-20260724-022 — frozen-diff review panel @ `932ffca`

Frozen range: `07c50954e3ddf1b21b0bd2e52c7f5a4b131dd121..932ffca`
Diff: `scripts/workplan.mjs` +5/−2, `scripts/workplan.test.mjs` +257/−0.
Herdr private session `drv-t022-panel-77721`; both seats read-only
(`--tools read,grep,find,ls`). Transcribed by the orchestrator from pane output.

| Seat | Model / transport | Verdict | Cost |
|---|---|---|---|
| `glmrev` | `pi --provider neuralwatt --model glm-5.2` | **APPROVE** | $0.454 |
| `kimirev2` | `pi --provider kimi-coding --model k3` | **APPROVE** | ~$0.09 |

Seat-selection note: the user directed mid-run that kimi-k3 via herdr pi replaces the codex
seat. A `codex exec -m gpt-5.5` run had already completed before that instruction arrived; it
also returned APPROVE with zero findings, but it is **not counted as a panel lens** and is
retained only as supplementary. Its lens was static-only — it could not execute the suite
(`mkdtemp` `EPERM` under its read-only sandbox). `pi --provider moonshotai --model kimi-k3`
returns `401 Invalid Authentication`; `kimi-coding/k3` is the working route.

## Convergent P2 — both lenses, independently

**Returning-detour tail-strip still re-anchors silently.**

The frozen invariant `tail.to === from` is satisfied by *any* earlier record that ends at the
same status. So a strip that erases a **whole detour returning to the same status** is accepted.

GLM's repro (`workplan.mjs:1316` check; `2510` `BLOCKABLE_STATUSES`):

1. Legally reach `in_progress` (receipt A, `to=in_progress`).
2. `block` from `in_progress` (legal — `BLOCKABLE_STATUSES` includes it), then `resume`
   (receipt B, `to=in_progress`). Ledger tail sequence: `[…, in_progress(A), blocked, in_progress(B)]`.
3. Tamper: `receipts.pop()` **twice** → `[…, in_progress(A)]`. `card.status` is still `in_progress`.
4. Run `submit`: `from="in_progress"`, `tail.to="in_progress"` → check **PASSES**. The new record
   anchors `prevEntryHash` to A across the erased pair, and `validateReceiptChain` later sees a
   contiguous, valid chain.

Harm: `blockReason` / `retainPaths` / `blockedFrom` evidence is silently and permanently lost, on
both chained and legacy ledgers. GLM notes test 5's own shape at `:9096` (block-from-verifying +
resume) is the same class.

Both lenses classify this as **a limitation of the frozen invariant, faithfully implemented — not
an implementation deviation**, and out of scope under the card's stop condition (closing it needs
a stronger check, e.g. most-recent-record / no-revisit detection).

Kimi additionally proposed strengthening with `tail.revisionTo === revisionFrom`.
**Orchestrator assessment: that exact form is unsafe** and must not be adopted as written — the
global revision counter advances from *unrelated* cards, which is precisely why
`validateIntegrationReceipt` uses a `revisionTo <= expectedRevision` bound rather than equality
(see the explanatory comment at `workplan.mjs:2273-2292`). An equality rule would reject any board
where another card transitioned in between. Recorded for the follow-up card as a starting point
requiring redesign, not a drop-in fix.

## P3s

| Lens | Finding | Disposition |
|---|---|---|
| GLM | Full-truncation-to-empty remains re-anchorable: set `receipts = []` (or delete the key) → `tail` is null → check skipped → next transition writes a fresh genesis record with `prevEntryHash:null`. Same keyless-anchoring class as the `:8877` declared residual. | ACCEPT as documented residual — rule 4.1 (tolerate absence) is a frozen stop condition, so not actionable in-card. Roll into the follow-up. |
| Kimi | The genesis-absence (`:9044`) and empty-array (`:9065`) tests pass **identically with or without** the production check — they guard tolerance against future over-strictness rather than pinning new behavior. The empty-array test also permanently pins the deliberate "strip-everything → treated as genesis" hole, adjacent to but broader than the `:8877` residual. | ACCEPT — accurate; independently reproduced by the orchestrator's mutation check (that test passed against unfixed code). Worth a clarifying comment. |
| GLM | Scope is not mechanically enforced by the card's acceptance commands: `git diff --check` reports whitespace errors only, it does not reject out-of-slice file changes. | ACCEPT — valid process gap. Scope was verified manually here (`git status` showed only the two permitted files). Worth adding a scope assertion to future acceptance command sets. |

## What both lenses positively confirmed

- The check uses the `from` **argument**, never `card.status`; all 9 call sites pass the true
  pre-transition status, including `block` (`card.blockedFrom` captured at 2646 before the
  overwrite at 2649).
- Message matches the frozen wording byte-for-byte.
- Placement is strictly downstream of every pre-existing receipts check
  (accept 2416/2417 → 2463; block 2635 → 2651; resume 2738/2740/2748 → 2761), so no existing
  error message is displaced. The still-passing `:8789` assertion proves the check was not hoisted.
- Byte-preservation holds: the check throws before `receipts.push` and before any `writeFile`;
  the `1313` copy mutates only an in-memory array.
- No bricking: `validateState` does not call the check, so the live board and legacy fixtures
  still load and project. Multi-cycle block/resume/re-integrate paths remain valid.
- The `:8877` declared-residual test is untouched in both text and behavior (test diff is
  additions-only, 257/0).
- Tests 1 (`:8962`), 2 (`:9001`), and 8 (`:9173`) genuinely fail if the production check is
  deleted — independently confirmed by the orchestrator's own mutation run (3 of 4 failed).
