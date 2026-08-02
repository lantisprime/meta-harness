# Mandatory frozen-diff review — META-35 successor campaign authoring

You are an independent read-only reviewer. Review the EXACT frozen diff

    base 24-hex: da3a98e2cde4934bcd60afea879c6f9f7812e38a
    head 40-hex: d9e628ba0368f012708b51d7121d8a19da28e1b5

in the checked-out worktree at
/Users/charltonho/Developer/worktrees/meta-harness-meta35 (currently at the
frozen head). Run `git diff da3a98e2cde4934bcd60afea879c6f9f7812e38a
d9e628ba0368f012708b51d7121d8a19da28e1b5` there. Review only that immutable
diff plus the surrounding context needed to judge it. You must NOT edit any
file. Every finding must cite file and line evidence.

## What this change is

Card TASK-20260802-029 (supersedes TASK-20260802-028): author the successor
one-time protected H campaign for the accepted META-34 real-execution
surface. The predecessor campaign meta34-real-h-20260726-v1 is permanently
void (protected input packages lost; holdout never opened, nothing burned).
This diff contains NO production implementation changes — 4 files, 720
insertions, all additive:

1. `.agents/meta35-campaign-spec.json` — NEW precommitted campaign spec,
   campaign_id meta35-real-h-20260802-v1, generated against the live
   implementations at the accepted head (main already carries e538ca80).
   Pins: qwen3.5:4b digest sha256:2a654d98e6fba55d452b7043684e9b57a947e393bbffa62485a7aac05ee4eefd,
   base_url http://127.0.0.1:11434/v1 (loopback; local Ollama binds
   localhost only), temperature 0.0, max_tokens 32, thinking false,
   top_p 1.0, top_k 1, reasoning_effort none, seeds (211, 877), budget
   100000 tokens / $0 / 7200 s, three canonical cells whose sole policy
   delta is query_max_results 1->2, 18 cases (6 views x 3 splits, all
   mandatory, approved_target only on validation-approved-target,
   selection preregistered to development-approved-target), and NEW
   protected package digests (dev cafde166..., val 405b9274...,
   holdout a4f371b6...).
2. `tests/test_real_h_campaign.py` — one additive test
   `test_committed_meta35_campaign_spec_matches_live_runtime_implementations`
   mirroring the existing meta34 drift test for the meta35 spec, plus
   supersession pins (campaign_id and all three package digests must
   differ from the void meta34 spec). No existing test modified.
3. `.agents/meta35-build-spec.md` — the card's build/design record:
   case design (contrast decoy for approved-target, restricted-sensitivity
   shadow for privacy, injection record for safety), storage contract
   (durable ~/Developer/protected/meta35 + off-host sealed archive), smoke
   evidence summary, and an explicit role note (coordinator authored data,
   spec, additive test, docs — per the META-34 role split; this review is
   the mandatory non-self-approval gate).
4. `docs/architecture.md` — bounded note: meta34 campaign void, meta35
   successor identity, explicitly asserting NO campaign outcome.

The protected packages, corpus, and generator are deliberately NOT in the
repository (committing them would expose sealed holdout content); the spec
binds them by digest. You cannot and must not look for them.

## Charter invariants to check against

- Evaluator non-self-approval; deterministic evaluators only
  (equals/contains/one_of); no model judge.
- Development-only selection (preregistered case), approved-target
  eligibility only in validation, sealed one-shot holdout.
- Frozen one-axis attribution: only the memory snapshot/retrieval policy
  may differ across cells; equal budgets/seeds.
- No promotion, activation, deployment, W-start, or authority surface may
  be added or implied; terminal statuses only
  eligible_pending_human_promotion / ineligible; human decision gates
  META-10.
- Do not claim planned capabilities are implemented; docs must not assert
  an outcome that has not occurred.

## Test evidence (coordinator-run at the frozen head)

- focused tests/test_real_h_campaign.py: 130 passed
- full suite: 1927 passed, 4 skipped, 1 xfailed
- node --test scripts/workplan.test.mjs: 155 passed, 0 failed
- git diff --check vs base: clean

## Required output format

Findings classified P0 (blocks: correctness/contract violation), P1
(blocks: significant risk), P2 (should fix), P3 (minor/wording). For each:
severity, file:line, what, why it matters, suggested disposition. If you
find none at a severity, say so. End with EXACTLY one line:

    VERDICT: APPROVE
or
    VERDICT: REQUEST_CHANGES
