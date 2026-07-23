FROZEN-DIFF INDEPENDENT REVIEW — META-17 (read-only; you MUST NOT edit, create, or delete any file)

Repo/worktree: /private/tmp/meta-harness-meta-17
Frozen diff: base e487dba..head 889572fffd8e3cd31e2bcf9b0029368ecba5557dd
(branch dev/meta-17-workflow-compilation). Review exactly this diff: 12 files, a new
selflearn compilation package (7 modules), 3 new test modules, and scoped edits to
selflearn/doctor.py and selflearn/__init__.py. Inspect with: git -C /private/tmp/meta-harness-meta-17 diff e487dba..889572ff and by reading the new files in full.

Ground truth docs in the worktree (read them first):
- .agents/meta17-build-spec.md — the frozen build contract (writable set, D1-D7 design decisions, module contracts, test plan).
- .agents/meta17-fix-brief-1.md — orchestrator review findings (FIX-1..10) the second build round claims to have resolved.

Mission being reviewed: compile learned selflearn workflow entries (ProcedureStep chains,
contracts.py) into generated Python executors. The entry remains the verified spec; the
executor is a derived, quarantined-by-default artifact activated only by a
cross-validation gate (independent model test-author + ExecutionPort sandbox pass +
strict-mode approval) and journaled receipts; a runtime executes activated executors and
emits TaskOutcome evidence with executed step_ids; doctor flags spec/executor drift.

Charter invariants to hold the diff against:
1. Evaluator non-self-approval — the compiler must never score/approve/activate its own
   output; the test-author must be identity-distinct; activation needs sandbox pass +
   strict-mode approval (ApprovalRecord.strict_mode enforced).
2. Bounded authority — generated code and the runtime must not mint permissions, widen
   scope, reach network/credentials/subprocess, or mutate the store/marks/promotion
   state; runtime emits TaskOutcome but never writes marks itself.
3. Reversible lineage — spec_hash/executor_hash binding; self-hashed receipts; atomic
   registry writes; supersede history.
4. Honest termination / fail-closed — every check failure must reject/refuse loudly;
   no exception-swallowing that deletes a guard; no silent auto-repair of authority
   bindings.
5. Evidence fidelity — awaiting approval is NOT a task failure and must not produce
   implicated TaskOutcome evidence.

Acceptance evidence already run by the orchestrator at head 889572ff: the three new test
files 66 passed; full selflearn suite 290 passed + 1 env-skip (pypdf absent); root suite
1631 passed + 1 skipped + 2 xfailed; node --test scripts/workplan.test.mjs 115 passed;
git diff --check e487dba clean.

Your task: adversarially review the frozen diff for correctness, security (code-injection,
escaping, sandbox escapes, authority laundering), evidence semantics, and contract
compliance with the two ground-truth docs. Verify the FIX-1..10 claims in the fix brief
against the actual code. Think about what the tests do NOT cover.

Output format (strict):
VERDICT: APPROVE | REJECT
FINDINGS:
- [P0|P1|P2|P3] <file:line> <finding> — <evidence/reasoning>
(one per line; P0 = release-blocking correctness/authority hole, P1 = must-fix before
integration, P2 = should-fix, P3 = note/accepted-limitation candidate)
NOTES: <anything the orchestrator should know that is not a finding>
Cite exact file:line for every finding. No edits. No approvals of your own work — you did
not write this code; if you cannot verify a claim, say NEEDS-EVIDENCE with the check
that would settle it.
