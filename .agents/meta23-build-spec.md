# META-23 bounded build specification

Role lock: the implementation seat is the BUILDER. The Codex orchestrator will
not edit product code. Do not change Linear, the canonical workplan, branches,
or commits. Work only in `/private/tmp/meta-harness-meta-23` and only in the
owned paths listed below.

## Charter and invariant

Advance product-loop stage 2: generated harness tool/context surfaces remain
inspectable and honestly typed. Preserve full-fidelity provenance, frozen
comparisons, evaluator non-self-approval, and bounded authority. This is one
bounded H-plane provenance correction; E and W remain frozen.

## Confirmed defect

- `ToolSpec.source` already records `builtin` versus `mcp:<server>`.
- `ToolRegistry.openai_schemas()` discards that source and returns only wire
  dictionaries.
- `OpenAICompatWorker` forwards those dictionaries to `assemble_live()`.
- `assemble_live()` emits one aggregate tool-schema manifest entry hard-coded
  as `TOOL_POLICY_SCHEMA / INSTRUCTION`, even when some descriptions and
  schemas came from an external MCP server.

## Required design

1. Preserve `ToolSpec.source` and the public `openai_schemas()` return shape.
   Add an immutable, deterministic per-tool schema record plus a parallel
   registry method that returns the exact wire schema with its origin class and
   exact source identity. Sorting, deduplication, dialect-safe names, and the
   raw provider schema dictionaries must remain unchanged.
2. Add a dedicated `MCP_TOOL_SCHEMA` (or equally precise external-tool-schema)
   context source kind. Model validation must reject this source kind combined
   with `ContextTrust.INSTRUCTION` in both source references and manifest
   entries. Do not add an optional manifest field that would invalidate hashes
   of historical manifests when missing.
3. Add a typed live tool-schema draft/provenance path separate from ordinary
   `SectionDraft`. Ordinary `TOOL_SCHEMAS` section drafts deliberately require
   INSTRUCTION trust and must not be weakened. The typed schema path derives
   trust from registry origin, never from MCP-controlled description, schema,
   or annotations.
4. Map builtin/local records to
   `TOOL_POLICY_SCHEMA / INSTRUCTION`. Map `mcp:<server>` records to
   `MCP_TOOL_SCHEMA / UNTRUSTED_EVIDENCE`. Include exact server/tool identity in
   the stable per-tool manifest identity. Reject duplicate stable identities.
5. Emit one `surface="tool_schemas"` manifest entry per transmitted tool. Keep
   each entry's payload as a one-element list so existing validation and
   `reconstruct_tool_schemas()` flattening remain compatible. The concatenated
   reconstruction must exactly equal `LiveAssembly.tool_schemas` and the
   provider `tools` list.
6. Keep provenance metadata out of `LiveAssembly.tool_schemas` and out of the
   provider request. Redact schema payloads before transmission and attestation,
   but keep each redacted schema attached to the correct origin. Budget only
   the aggregate transmitted schema bytes, not provenance metadata.
7. Preserve the legacy raw `tool_schemas=` entry point as explicitly
   caller-authored local policy input, or fail it closed with a typed error. Do
   not silently reinterpret the actual MCP registry path as trusted legacy
   input. Reject simultaneous raw and provenance-aware arguments.
8. Update architecture documentation narrowly: external MCP descriptions and
   schemas remain model-visible but are attested as untrusted provenance; this
   card does not sanitize, reject, or grant authority to them.

## Falsifiable tests

- Start with failing tests for the defect.
- A mixed builtin/MCP selection has the same deterministic, deduplicated,
  dialect-safe provider schemas as before.
- The mixed manifest has one entry per tool; builtin is
  `TOOL_POLICY_SCHEMA/INSTRUCTION`, MCP is
  `MCP_TOOL_SCHEMA/UNTRUSTED_EVIDENCE`, and stable identity names the origin.
- `manifest.reconstruct_tool_schemas()` equals the exact sent tools list.
- Model construction/tampering cannot pair MCP schema provenance with
  `INSTRUCTION` trust.
- Malicious MCP description text remains only in the structured tool schema,
  is marked untrusted, and is redacted when it contains configured secrets.
- Identical inputs yield identical manifest hashes and per-tool order.
- Tool-schema provenance remains identical in later tool-call rounds; tool
  observations remain separately untrusted.
- MCP stdio load/call, stale-tool reload, local-worker, and run-event tests stay
  green.

## Owned paths

- `.agents/meta23-definition.json`
- `.agents/meta23-build-spec.md`
- `.review-store/meta23-glm-5.2-review.txt` (reviewer/orchestrator only)
- `src/metaharness/tools/registry.py`
- `src/metaharness/tools/mcp.py`
- `src/metaharness/tools/__init__.py`
- `src/metaharness/context/models.py`
- `src/metaharness/context/live.py`
- `src/metaharness/context/__init__.py`
- `src/metaharness/harness/local.py`
- `tests/test_tools.py`
- `tests/test_context.py`
- `tests/adversarial/test_context_authority.py`
- `tests/adversarial/test_context_invalid_inputs.py`
- `docs/architecture.md`

Do not touch the canonical workplan files from this worktree. Do not broaden
scope into semantic sanitization, tool-selection policy, memory, evaluator,
deployment, or legacy shadow-assembler redesign. Report exact files changed,
tests run, and any concern that requires coordinator scope expansion.
