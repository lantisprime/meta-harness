# META-8 Independent Frozen-Diff Review (AGENTS.md gate)

You are the independent frozen-diff reviewer (read-only). You must not
edit, stage, commit, or run any mutating command. Every finding requires
file-and-line evidence and a P0/P1/P2/P3 severity. Finish with an explicit
verdict: APPROVE, APPROVE-WITH-MOD, or REJECT.

## Frozen commits

- Base: e487dba361ccb382eb9ab4bc29754367289ab39d (main)
- Head (review THIS immutable commit): 3290a35dbdb93501bf7cb8646610314526faeacf
- Branch: dev/meta-8-scheduling-heartbeats-policy, worktree /private/tmp/meta-harness-meta-8
- Scope: exactly 10 files — 5 new modules src/metaharness/discovery/
  {population,policy,scheduler,heartbeat,evolution}.py, re-exports added to
  discovery/__init__.py, and 4 new test modules (test_discovery_
  {scheduling,policy,heartbeats}.py, adversarial/test_discovery_policy_
  boundaries.py). Review `git diff e487dba...3290a35` (= `git show 3290a35`).

## What this implements (card TASK-20260714-006 / Linear META-8)

H-only extension of the META-7 discovery kernel: frozen self-hashed
population descriptors/fingerprints; a deterministic PopulationScheduler
emitting SearchDecisionReceipts (parent/role/variation/briefing/budget/
alternatives/expected gain) under diversity floors and baseline reseeding;
typed HeartbeatEngine checkpoints (reflection/consolidation/redirection on
event/evaluation/time/plateau triggers with epsilon + cooldown) whose
outputs are only untrusted CANDIDATE artifacts or queued proposals; a
declarative SearchPolicyDSL + SearchPolicySnapshot with strict
SCHEMA->STATIC->SIMULATION->SHADOW validation; and a SearchPolicyEvolver
with frozen windows, strategy history, four-receipt-gated activation, and
parent fallback that preserves the candidate population.

## Charter invariants that must hold (docs/PROJECT_CHARTER.md)

Bounded authority (no heartbeat/scheduler/policy/evolver path may mint or
carry promotion, deploy, evaluator-write, memory-activation,
weight-training, or permission-expansion authority, suppress protected
capture, or activate its own output); evaluator non-self-approval (a
policy proposer cannot score/approve/activate itself); fail-closed
validation (any stage failure restores the parent policy, candidate
population untouched); honest termination/limitation docstrings; E and W
frozen; determinism (no wall clock, no randomness on any decision path);
the DSL must be structurally unable to carry executable code, imports,
tool calls, evaluator logic, permissions, or pointer mutations.

## Acceptance evidence at this exact commit

Focused META-8 suite 113 passed; discovery+context regression 409 passed;
full repository suite 1810 passed, 2 xfailed (the intentional META5
strict xfails); node workplan suite 115/115; git diff --check clean
against base. Built by sequenced pi seats (MiniMax-M3 and GLM-5.2) under
coordinator supervision with per-slice independent verification.

## Deliverable

Verdict + ordered findings (P0..P3, file:line, concrete failure scenario
each, CONFIRMED/PLAUSIBLE). State explicitly if there are no P0/P1
findings. Pay particular attention to: cross-module contract mismatches
between the two builder providers' modules (scheduler/heartbeat vs
policy/evolution/population); simulation-stage arithmetic; hash-binding
gaps (a receipt that binds fewer fields than it claims); and any decision
path where identical inputs could yield different bytes.
