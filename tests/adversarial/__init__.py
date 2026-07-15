"""META-5: adversarial (red-team) coverage for src/metaharness/context.

These tests are read-only with respect to production code. They exercise:
  - executable negative tests for behavior the context contract *currently*
    enforces (invalid input, authority/provenance, canonical determinism,
    redaction, reconstruction), and
  - strict xfail tests, tagged with stable requirement IDs, for future
    memory-skill contracts that are genuinely absent from this codebase today.

See tests/fixtures/meta5/corpus.json for the machine-readable case corpus
shared by these modules.
"""
