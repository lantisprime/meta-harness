# META-34 resume — GLM-5.2 chunk-review dispositions (draft, pending full-diff verdict)

Coordinator dispositions per CLAUDE.md categories, each verified against the working tree
(head 9770d11) before classification. Fix batch = ACCEPT + ACCEPT-WITH-MOD items.

## P1 (both block the campaign)

| # | Finding (chunk) | Verification | Disposition |
|---|---|---|---|
| 1 | Holdout ledger consumed before verdict re-derivation; late `CampaignContractError`/gate exception burns the holdout with no terminal artifact and no retry (C2) | Confirmed by inspection of `_run_ordered_campaign` step ordering | **ACCEPT** — wrap post-consume steps; on failure persist an `aborted` terminal artifact binding the ledger key + exception, so the burn is recorded and auditable; add citing test |
| 2 | Committed spec `task_model_portfolio_ref` still names retired 35B; `w_refs[0]`/`task_model_portfolio_digest` carry the old digest; no validator ties portfolio ref/digest to `model.*` (C1) | Confirmed: spec lines 519-520 (`sha256:6e73b30f…`, `portfolio-qwen3-5-35b-a3b-coding-nvfp4`) | **ACCEPT** — regenerate portfolio ref/digest + `w_refs` for qwen3.5:4b; add `CampaignSpec` validator enforcing ref↔digest↔model consistency; extend drift test to cover these fields |

## P2

| # | Finding (chunk) | Verification | Disposition |
|---|---|---|---|
| 3 | `case_set_digest` hashes only `split + campaign_id` (C2) | Confirmed line 1743 | **ACCEPT** — bind case IDs, input digests, mandatory flags |
| 4 | Sub-digests (`blueprint_digest`, `task_model_portfolio_digest`, …) trusted verbatim when supplied (C1) | Confirmed `_populate_frozen_axes` only-when-empty pattern | **ACCEPT** — validate supplied sub-digests against derivations where derivable |
| 5 | `load_spec` doesn't compare `spec_digest` to recomputed (C2 #6) | **Partially wrong**: `_populate_frozen_axes` line 717 raises on mismatch during `model_validate`, so a tampered spec DOES fail to load | **REJECT (with note)** — binding exists via validator; add an explicit citing test to make it load-bearing |
| 6 | Seed probe uses synthetic `_body()` call, not live `runner.run` path (C2) | Confirmed in `_enforce_runner_model_contract` | **ACCEPT-WITH-MOD** — keep probe; additionally capture the literal outbound body via a worker-level request recorder and verify seed per attempt in `_phase_execution` |
| 7 | Secret screen covers `worker_result_dump` only; `selected_record_contents` etc. unscreened (C2) | Confirmed in `_phase_execution` | **ACCEPT** — run `_assert_secret_safe_worker_result`-equivalent over the full evidence row |
| 8 | Vacuous "no model call" reload test — spy factory never wired (C4 #1) | Confirmed in test body | **ACCEPT** — pass the counting factory into the verify path or spy the transport |
| 9 | LOG receipt scope/snapshot binding missing (wrapper + reload) vs docstring claim (C3) | Confirmed `_coerce_receipt` + `_verify_evidence_rows` LOG branch | **ACCEPT** — add scope/snapshot binding to LOG path both sides + citing test |

## P3 (accepted hardening, small)

- `next(...)` without default in eligible branch → `next(..., None)` (C3). **ACCEPT**
- Read-spies also patch `read_bytes`/`open` (C4 #7). **ACCEPT**
- `consult_receipt_before_content_hashes` row copy cross-check (C3). **ACCEPT**
- Cell-0 frozen-axis negative tests (C4 #6). **ACCEPT**
- Stale "loopback" prose in build-spec/architecture (C1 P3-1). **ACCEPT** (doc-only)
- `CampaignEvidenceError` export (C1 P3-3). **ACCEPT**
- Double-consume O_EXCL campaign-level test (C5 P3-4). **ACCEPT**
- Factory `config` param discarded (C3). **ACCEPT-WITH-MOD** — use it (assert equality with derived config) rather than remove
- Digest prefix inconsistency (C1 P3-4). **DEFER** — cosmetic; changing breaks committed digests for no security gain
- Test-name overpromises (C4 #2, #3, #8, #9). **ACCEPT** — rename or strengthen bodies
- Secret-in-assertion-value semantics (C4 #10). **ACCEPT** — add distinct case
- Per-cell full seed schedule + per-cell budget symmetry assertions (C4 #5). **ACCEPT**
- Served-model digest verification vs re-tagged model (C5 P3-2). **ACCEPT-WITH-MOD** — resolve digest from the live Ollama API (`/api/tags`) at campaign start and compare to pin
- Transitive-dependency attestation gap (C5 P3-3). **DEFER** — recursive closure over all transitive callees is a design change; record as known bound of the attestation model, revisit post-campaign
- Model attestation from self-declared config vs HTTP body (C3 observation). **NEEDS-EVIDENCE** — awaiting full-diff reviewer on the run-path derivation; if confirmed, fold into #6's request-recorder fix

## Pending
- Full-diff reviewer verdict + any cross-file findings (running, herdr w3:p1).
- C2 findings #7+ (file truncated in my summary pass — full text in findings-C2.txt, all items carried into the fix brief).
