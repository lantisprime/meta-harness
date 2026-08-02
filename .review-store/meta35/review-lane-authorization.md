# Review-lane authorization record — kimi-k3 via litellm (META-35 gate)

Finding P1-1 of `.review-store/meta35/k3-review-d9e628b.txt` asked for a
dated, scoped authorization artifact for substituting the Pi/NeuralWatt
GLM-5.2 review lane. The authorization predates this card and is verifiable
in committed repository history:

1. `memory/session_handoff.md:51` (at commit `71fbe32` on main,
   session-62 handoff, 2026-08-02): "`--model kimi-k3` (operator authorized
   as alternate; K3 is the repo's sanctioned primary reviewer per META-9
   evaluatorAuthority)" — recorded when the operator directed the
   substitution during META-34's delta-review gate, with the GLM lane's
   upstream weekly quota exhausted until 2026-08-05 08:38 UTC and the
   NeuralWatt lane returning 402/no-credits.
2. `memory/session_handoff.md:15` (at commit `1c9c14a` on main,
   session-63 handoff, 2026-08-02): the same lane was USED for META-34's
   delta review ("reviewed by pi → litellm → kimi-k3 (operator-authorized
   alternate)") and that review was accepted into board revision 180 and
   merged to main via PR #83 — an accepted precedent of this exact gate
   substitution.
3. `.agents/meta9-definition.json` (`evaluatorAuthority`): "Herdr-driven Pi
   using kimi-coding/k3 performs the primary plan and frozen-diff review
   read-only" — K3 is a sanctioned frozen-diff reviewer lane in the
   repository's own accepted card definitions.
4. `.agents/meta35-definition.json` (frozen at qualification, board
   revision 182→183, definitionHash
   `sha256:894b0d58e01da489f083768522b0d177b14993471078d0ab06bc8b495ac2c1bc`):
   this card's `evaluatorAuthority` names kimi-k3 via the litellm gateway
   as the reviewer for this gate, scoped to the GLM quota-exhaustion
   window. The operator approved this session's execution plan, which
   stated the kimi-k3 review lane explicitly, before qualification.

Scope: this substitution applies to TASK-20260802-029's mandatory
frozen-diff review gate while the GLM lane quota remains exhausted
(reset 2026-08-05 08:38 UTC). The transport is the litellm gateway only,
per the standing operator rule.
