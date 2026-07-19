You are an independent PLAN reviewer for the meta-harness repository. Read-only; do not edit anything.

Review the build spec at /Users/charltondho/Developer/projects/meta-harness/.agents/meta19-build-spec.md against the actual code in this worktree (/private/tmp/meta-harness-meta-19, branch dev/meta-19-live-context-envelope at base 46c60fa).

The task (Linear META-19): promote the shadow ContextEnvelope (src/metaharness/context/assembly.py, models.py) to be the single live prompt assembler consumed by both src/metaharness/harness/local.py (chat messages) and src/metaharness/harness/coding.py (flat CLI prompt), with live trust-rule enforcement (untrusted content can never occupy instruction slots), redaction applied to the bytes actually sent, and the ContextManifest journaled per attempt via the existing run-event sink (core/executor.py binds it per attempt). The prior shadow-era byte-identity freeze is deliberately lifted by this card.

Answer these questions with file:line evidence:
1. Is the staged design sound and complete against the four acceptance boxes (one assembler feeds both builders; trust rules enforced live with a proving test; redaction on sent bytes with divergence impossible by construction; per-attempt manifest journal)?
2. Are there hidden callers, test dependencies, or contract couplings the spec misses (e.g. anything else consuming _build_messages/_render_prompt/fit_messages_with_receipt, selflearn coupling, executor coupling)?
3. Are the trust assignments in Stage 2/3 correct and safe (task inputs demoted to UNTRUSTED_EVIDENCE; system_prompt as SYSTEM_INSTRUCTIONS; boundaries/output schema as RESPONSE_CONTRACT)? Any prompt-behavior regressions to flag?
4. Is the fail-closed semantics change (removing the legacy-fallback on assembler error) safe, or does it create an availability regression the spec should mitigate?
5. Any determinism, hashing, or models.py-validator conflicts the assembler design would hit (e.g. ContextSection validators, envelope contiguous priorities, manifest entry/receipt alignment)?

Output: a verdict (SOUND / SOUND-WITH-CHANGES / UNSOUND) plus numbered findings, each P0/P1/P2 with file:line evidence and a concrete fix suggestion. Be specific and skeptical; do not restate the spec.
