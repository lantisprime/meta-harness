# Adversarial plan review — TASK-20260724-019 / META-28 (pre-qualification)

You are an independent, adversarial plan reviewer. Your job is to break this plan BEFORE it is frozen and built. You have READ-ONLY access. Do not edit any file. Every finding needs file:line evidence from the actual code.

## Object under review

The frozen card definition draft: `.agents/t019-definition.json` (working dir is the repo root). Read it fully — especially the `budget` (the design), `stopCondition`, `evaluatorAuthority`, and `acceptanceCommands`.

## The card in one paragraph

The workplan control root (`scripts/workplan.mjs`, tests `scripts/workplan.test.mjs`) keeps per-card append-only receipt ledgers (`card.receipts`, written only by `appendReceipt`, :1292-1314) plus a standalone `card.integrationReceipt` snapshot written only by `integrate` (:1979-1988). Nothing binds the snapshot to the ledger; receipts carry no hashes. The plan: (1) hash-chain newly appended ledger records (`prevEntryHash`/`entryHash`, canonicalization per `computeAcceptanceReceiptHash` :2040-2046); (2) have integrate's ledger record carry `evidence.integrationReceiptHash` over the snapshot, checked fail-closed in `validateIntegrationReceipt` (:2124-2193) when present; (3) a chain validator called from accept/block/resume transition validators (NOT `validateState` :605-651) with legacy tolerance (records predating the change stay unhashed forever; hashed-after-unhashed prefix rule); (4) comment scope fix at :2174-2182. Plus tests: post-drift block→resume, retainPaths=false both directions, tamper/strip/swap rejections, legacy board compatibility.

## Attack the plan. Specifically probe:

1. **Line-number and factual accuracy** of every file:line claim in the budget against the real code.
2. **Canonicalization pitfalls**: JSON round-tripping of stored records vs freshly built objects (key order handled by deepSortKeys — but undefined fields, evidence objects, non-string values, the `at` timestamp); is "sha256 of the canonicalized predecessor record exactly as stored" well-defined and stable across load/save cycles?
3. **Laundering holes**: appendReceipt hashes the predecessor as stored — can a tamperer rewrite history and let the next legitimate append re-anchor the chain? Is that inside or outside the card's stated threat model (detectability of a SWAPPED/REPLAYED integrationReceipt, not full state.json tamper resistance — that residual is explicitly out of scope per the t018 panel)? Does the plan's prefix rule (once chained, always chained) actually prevent silent un-chaining, or is there a gap (e.g. deleting the entire suffix INCLUDING all chained records back to a legacy prefix)?
4. **Legacy tolerance vs the live board**: `.workplan/state.json` at revision 131 has 16 cards, all with un-chained receipts, all `done` except two backlog cards. Will every existing transition, sync, and projection keep working? Check `workplan sync` and any other command that re-validates or rewrites state.
5. **Call-site completeness**: are accept (:2248), block (:2468), resume (:2565-2571) the right (and only) places for the chain validator? What about `submit`, `claim`, `ready`, `integrate` themselves — they append records; should they validate the chain before appending so a tampered ledger can't keep growing legitimately? Is there a Python-side reader (development/remote_workplan/gateway.py or tests) that parses receipts and could choke on new fields?
6. **The retainPaths=false test design**: is the claimed semantics right — `isPathReserving` (:568-572), resume's `validateClaimPathExclusivity` (:2579) — including WHICH error the resume hits when the path was taken? Is "succeeds and re-reserves when free" actually the code's behavior?
7. **Scope discipline**: anything in the budget that is creep, and anything MISSING that the three source P3s (read them: `.review-store/wpfix-glm-gate-review-84734f8.txt` P3-1, `.review-store/wpfix-codex-review-249a826.txt`, `.review-store/wpfix-glm-gate-review-249a826.txt` P3-A/P3-B/P3-C) require but the budget dropped.
8. **Acceptance commands**: sufficient and runnable? (worktree `/private/tmp/meta-harness-t019`, venv exists, base c30b06f6.)

## Output format (mandatory)

1. A markdown table: | SEVERITY (P0/P1/P2/P3) | LOCATION | FINDING | CONCRETE FAILURE CASE |
   - P0/P1 = the plan as written would build the wrong thing or break the live board; P2 = material gap, fix the plan; P3 = advisory.
2. Then a single line: `VERDICT: APPROVE` or `VERDICT: REVISE`.

Be adversarial, not agreeable. If the design is sound say so and dig for the edge cases anyway.
