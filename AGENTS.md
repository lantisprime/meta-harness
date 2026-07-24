# Meta-Harness Repository Guidance

Before planning or implementing a non-trivial enhancement, read
`docs/PROJECT_CHARTER.md` and state which product-loop stage and invariant the
change advances.

Use the charter as the repository's enhancement decision gate:

- preserve goal specification, generated-harness recommendation, managed
  rehearsal/correction, H/E/W learning, cross-harness `SolutionTemplate` reuse,
  and reproducible package/deploy/monitor/rollback as one coherent product loop;
- support versioned hybrid frontier/open-weight and pure open-weight portfolios;
  treat hosted model selection, routing, prompts, and inference parameters as
  `H`, trainable open checkpoints as `W`, and never conflate autonomous
  improvement with evaluator or deployment self-approval;
- treat code generation as a versioned, instrumented candidate surface;
- preserve evaluator non-self-approval, full-fidelity evidence, controlled
  attribution, bounded authority, human promotion, and exact rollback; and
- do not claim planned capabilities are implemented.

If a requested change conflicts with the charter or would redefine the project
mission, surface the conflict and require an explicit charter revision rather
than allowing implementation drift.

## Development Kanban (Linear team "Meta-Harness", `META`)

Coding-agent seats (Codex, Claude Code, and pi-driven seats) coordinate development work through the Linear team `Meta-Harness` in workspace `cha-personal`. Conventions:

- Pull only cards in **Ready**. Backlog cards are not claimable; only the
  coordinator qualifies a card and moves it to Ready.
- One card in progress per agent (one-card WIP). Claim by moving the card to
  **In Progress** and assigning yourself before editing anything.
- Advance states as work progresses: In Progress → Review → Verifying → Done.
  Use **Blocked** when stuck and say why; the coordinator triages.
- Comment evidence on the card at each transition: branch, commits, test
  output, review verdicts — artifacts, not assertions.
- Respect lane labels (`codex`, `claude-code`, `coordinator`, `pi`): pick up only
  cards labeled for your seat unless the coordinator reassigns.
- Linear is the **visible development board only**, not the atomic claim
  authority. Single-winner claims, fencing, and path reservations belong to the
  Kanban control root / claim gateway (TASK-20260714-001/010). This is
  development-plane coordination; product runtime components (`Router`,
  `TaskExecutor`, `WorkflowEngine`) never touch development cards or receive
  Linear credentials.

The `pi` OWNER namespace (claimable lane for pi-CLI-driven open-weights seats, owner string `pi:<host>:<session>`) is distinct from the "Pi/NeuralWatt GLM-5.2" reviewer role described below.

## Independent Second-Opinion Review

Every non-trivial implementation must receive a frozen-diff second-opinion
review from **Pi using NeuralWatt GLM-5.2** before it advances from Review. The
implementing seat's self-review does not satisfy this gate.

- Freeze the exact review commit and base commit in the atomic card before
  dispatch. Review that immutable diff; do not review a moving worktree.
- Run Pi/GLM as a reviewer with read-only permissions. The bounded one-shot
  invocation is:

  ```bash
  pi --provider neuralwatt --model glm-5.2 \
    --permission-mode read-only \
    --tools read,grep,find,ls,bash \
    --no-session --print "<frozen-diff review brief>"
  ```

- The brief must identify the base and head commits, acceptance criteria,
  charter invariants, and test evidence. It must explicitly forbid edits and
  require file-and-line evidence for every finding.
- Require a verdict plus findings classified as P0, P1, P2, or P3. Preserve
  the review output verbatim in `.review-store/` and comment the artifact path,
  model, frozen commit, and dispositions on the Linear card.
- Any unresolved P0 or P1 blocks integration. The implementing seat must
  classify each substantive finding as `ACCEPT`, `ACCEPT-WITH-MOD`, `REJECT`,
  `DEFER`, or `NEEDS-EVIDENCE`, apply accepted fixes, rerun relevant tests, and
  request a fresh Pi/GLM review of the new frozen commit.
- If Pi, NeuralWatt, or GLM-5.2 is unavailable, leave the card in Review (or
  mark it Blocked with evidence). Never silently substitute self-review or
  claim independent approval.
- A separate coordinator remains responsible for integration, verification,
  and acceptance after the Pi/GLM gate passes.
