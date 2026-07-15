"""Typed context-contract and shadow-manifest boundary tests."""
from __future__ import annotations

import copy
import json

import httpx
import pytest
from pydantic import ValidationError

from metaharness.context import (
    CompressionAction,
    ContextScope,
    ContextSection,
    ContextSectionType,
    ContextManifest,
    ContextSourceKind,
    ContextSourceRef,
    ContextTrust,
    ContextVersionBindings,
    Sensitivity,
    allocate_section_budgets,
    fit_messages,
    fit_messages_with_receipt,
)
from metaharness.core.types import Task
from metaharness.harness import OpenAICompatWorker
from metaharness.observability.run_events import bind_run_event_sink, reset_run_event_sink


def _source(**changes):
    values = {
        "source_id": "repo-instructions",
        "kind": ContextSourceKind.PROTECTED_INSTRUCTIONS,
        "scope": ContextScope(project_id="meta-harness"),
        "trust": ContextTrust.INSTRUCTION,
        "content_hash": "sha256:" + "1" * 64,
        "selection_reason": "required repository contract",
        "sensitivity": Sensitivity.PUBLIC,
        "fetchable": False,
    }
    values.update(changes)
    return ContextSourceRef(**values)


def _section(**changes):
    source = changes.pop("source", _source())
    values = {
        "section_type": ContextSectionType.SYSTEM_INSTRUCTIONS,
        "stable_id": "system-contract",
        "source": source,
        "source_hash": source.content_hash,
        "trust": source.trust,
        "content": "You are a bounded worker.",
        "original_tokens": 7,
        "selected_tokens": 7,
        "compressed_tokens": 7,
        "budget_tokens": 100,
        "ordering_priority": 0,
        "sensitivity": source.sensitivity,
        "compression_action": CompressionAction.NONE,
    }
    values.update(changes)
    return ContextSection(**values)


@pytest.mark.parametrize(
    "changes",
    [
        {"unknown": "field"},
        {"content_hash": None, "high_water_mark": None},
        {"content_hash": "sha256:" + "1" * 64, "high_water_mark": "event:9"},
        {"fetchable": True, "artifact_ref": None},
        {"fetchable": False, "artifact_ref": "artifact:secret"},
    ],
)
def test_source_contract_fails_closed_on_unknown_or_ambiguous_inputs(changes):
    with pytest.raises(ValidationError):
        _source(**changes)


def test_scope_requires_project_and_rejects_ambiguous_run_task_pairing():
    with pytest.raises(ValidationError):
        ContextScope()
    with pytest.raises(ValidationError):
        ContextScope(project_id="meta-harness", task_id="task-1")


def test_section_cannot_lie_about_source_hash_trust_or_protected_omission():
    with pytest.raises(ValidationError):
        _section(source_hash="sha256:" + "2" * 64)
    with pytest.raises(ValidationError):
        _section(trust=ContextTrust.VERIFIED_FACT)
    with pytest.raises(ValidationError):
        _section(
            content="",
            selected_tokens=0,
            compressed_tokens=0,
            compression_action=CompressionAction.OMITTED,
            omission_reason="budget",
        )


def test_version_bindings_reject_self_parent_lineage_and_unknown_axes():
    values = {
        "model_portfolio_version": "portfolio:1",
        "harness_version": "h:1",
        "evaluator_version": "e:1",
        "weight_snapshot_version": None,
        "memory_snapshot_version": "memory:4",
        "evidence_snapshot_version": "evidence:9",
        "candidate_version": "candidate:2",
        "parent_candidate_version": "candidate:1",
    }
    assert ContextVersionBindings(**values).weight_snapshot_version is None
    with pytest.raises(ValidationError):
        ContextVersionBindings(**{**values, "parent_candidate_version": "candidate:2"})
    with pytest.raises(ValidationError):
        ContextVersionBindings(**{**values, "deployment_version": "prod"})


def test_tier_section_budgets_are_deterministic_complete_and_tier_specific():
    sections = [
        ContextSectionType.SYSTEM_INSTRUCTIONS,
        ContextSectionType.PRIOR_OUTPUTS,
        ContextSectionType.MEMORY,
        ContextSectionType.RESPONSE_CONTRACT,
    ]
    small = allocate_section_budgets(sections, 1001, tier="small")
    frontier = allocate_section_budgets(sections, 1001, tier="frontier")
    assert small == allocate_section_budgets(sections, 1001, tier="small")
    assert sum(small) == sum(frontier) == 1001
    assert small != frontier
    assert small[0] > frontier[0]
    with pytest.raises(ValueError):
        allocate_section_budgets(sections, -1, tier="small")


def test_fit_receipt_is_deterministic_reconstructable_and_compatible():
    messages = [
        {"role": "system", "content": "contract " * 40},
        {"role": "user", "content": "head-marker " + "middle " * 4000 + " tail-marker"},
        {"role": "tool", "content": "tool-head " + "observation " * 4000 + " tool-tail"},
        {"role": "user", "content": "final response contract"},
    ]
    original = copy.deepcopy(messages)

    first = fit_messages_with_receipt(
        messages,
        budget_tokens=2600,
        model_id="local-model",
        harness_version="git:abc123",
        policy_version="context-v1",
    )
    second = fit_messages_with_receipt(
        copy.deepcopy(messages),
        budget_tokens=2600,
        model_id="local-model",
        harness_version="git:abc123",
        policy_version="context-v1",
    )

    assert messages == original
    assert first.messages == fit_messages(messages, 2600)
    assert first.model_dump_json() == second.model_dump_json()
    assert first.manifest.reconstruct_messages() == first.redacted_messages
    assert (
        first.manifest.reconstruct_redacted_envelope().content_hash
        == json.loads(first.manifest.redacted_envelope_json)["content_hash"]
    )
    assert first.envelope.content_hash == second.envelope.content_hash
    assert first.manifest.manifest_hash == second.manifest.manifest_hash
    assert first.messages[0] == messages[0]
    assert first.messages[-1] == messages[-1]
    compressed = [r for r in first.manifest.compression_receipts if r.action != "none"]
    assert compressed
    assert all(r.before_hash != r.after_hash for r in compressed)
    assert "tool-head" in first.messages[2]["content"]
    assert "tool-tail" in first.messages[2]["content"]


def test_receipt_honestly_records_legacy_head_tail_growth_near_floor():
    receipt = fit_messages_with_receipt(
        [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "x" * 410},
            {"role": "user", "content": "final"},
        ],
        budget_tokens=100,
        model_id="m",
        harness_version="h",
    )
    transformations = [
        item for item in receipt.manifest.compression_receipts
        if item.action == CompressionAction.HEAD_TAIL
    ]
    assert transformations
    assert any(item.final_tokens > item.original_tokens for item in transformations)


def test_manifest_redacts_sensitive_values_without_changing_live_messages():
    secret = "sk-live-super-secret"
    messages = [
        {"role": "system", "content": "system contract"},
        {"role": "user", "content": f"token={secret}"},
    ]
    receipt = fit_messages_with_receipt(
        messages,
        budget_tokens=10_000,
        model_id="m",
        harness_version="h",
        redaction_values=[secret],
    )

    assert receipt.messages == messages
    assert secret in receipt.messages[1]["content"]
    assert secret not in receipt.manifest.model_dump_json()
    assert secret not in receipt.manifest.reconstruct_redacted_envelope().model_dump_json()
    assert receipt.manifest.reconstruct_messages()[1]["content"] == "token=[REDACTED]"
    assert receipt.manifest.redaction_count == 1


def test_redaction_prefers_longest_secret_and_redacts_secret_keys():
    short = "credential-short"
    long = "credential-short-production-999"
    receipt = fit_messages_with_receipt(
        [
            {"role": "system", "content": "system"},
            {"role": "user", "content": f"value={long}"},
        ],
        budget_tokens=10_000,
        model_id="m",
        harness_version="h",
        redaction_values=[short, long],
        tool_schemas=[{"type": "function", "function": {long: "must not persist"}}],
    )
    persisted = receipt.manifest.model_dump_json()
    assert short not in persisted
    assert long not in persisted
    assert "production-999" not in persisted
    assert "[REDACTED_KEY_" in persisted


def test_shadow_manifest_records_tool_schemas_outside_live_message_reconstruction():
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "task"},
    ]
    schemas = [{"type": "function", "function": {"name": "read", "parameters": {}}}]
    receipt = fit_messages_with_receipt(
        messages,
        budget_tokens=1_000,
        model_id="m",
        harness_version="h",
        tool_schemas=schemas,
    )
    assert receipt.manifest.reconstruct_messages() == messages
    assert receipt.manifest.reconstruct_tool_schemas() == schemas
    assert any(section.section_type == ContextSectionType.TOOL_SCHEMAS for section in receipt.envelope.sections)


async def test_local_worker_emits_shadow_manifest_and_preserves_request_bytes():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "done"}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 1},
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    worker = OpenAICompatWorker(
        "local",
        base_url="http://fake/v1",
        model="m",
        client=client,
    )
    task = Task(id="task-1", objective="do the exact task", boundaries=["stay bounded"])
    events = []
    token = bind_run_event_sink(lambda kind, payload: events.append((kind, payload)))
    try:
        result = await worker.run(task)
    finally:
        reset_run_event_sink(token)
        await client.aclose()

    assert result.output == "done"
    expected_messages = [
        {
            "role": "system",
            "content": "You are a worker agent executing one well-scoped task.\n\n"
            "Boundaries:\n- stay bounded",
        },
        {"role": "user", "content": "do the exact task"},
    ]
    assert seen["body"]["messages"] == expected_messages
    shadow = [payload for kind, payload in events if kind == "context.manifest.shadow"]
    assert len(shadow) == 1
    assert shadow[0]["shadow"] is True
    assert shadow[0]["live_messages_hash"]
    assert shadow[0]["manifest"]["model_id"] == "m"
    persisted = ContextManifest.model_validate(shadow[0]["manifest"])
    assert persisted.reconstruct_messages() == expected_messages


async def test_shadow_sink_failure_cannot_change_worker_execution():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "done"}}]})

    def broken_sink(kind, payload):
        if kind == "context.manifest.shadow":
            raise RuntimeError("journal unavailable")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    worker = OpenAICompatWorker("local", base_url="http://fake/v1", model="m", client=client)
    token = bind_run_event_sink(broken_sink)
    try:
        result = await worker.run(Task(objective="still run"))
    finally:
        reset_run_event_sink(token)
        await client.aclose()

    assert result.output == "done"
    assert seen["body"]["messages"][-1]["content"] == "still run"


async def test_shadow_assembler_failure_falls_back_to_legacy_fit(monkeypatch):
    seen = {}
    events = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "done"}}]})

    def fail_assembly(*args, **kwargs):
        raise ValueError("invalid shadow contract")

    monkeypatch.setattr("metaharness.context.fit_messages_with_receipt", fail_assembly)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    worker = OpenAICompatWorker("local", base_url="http://fake/v1", model="m", client=client)
    token = bind_run_event_sink(lambda kind, payload: events.append((kind, payload)))
    try:
        result = await worker.run(Task(objective="legacy fallback"))
    finally:
        reset_run_event_sink(token)
        await client.aclose()

    assert result.output == "done"
    assert seen["body"]["messages"][-1]["content"] == "legacy fallback"
    failures = [payload for kind, payload in events if kind == "context.manifest.shadow_failed"]
    assert failures == [{
        "schema_version": 1,
        "shadow": True,
        "task_id": result.task_id,
        "error_type": "ValueError",
    }]


async def test_second_tool_round_keeps_tool_observation_untrusted():
    class Registry:
        workspace_root = ""

        def openai_schemas(self, names):
            return [{"type": "function", "function": {"name": "probe", "parameters": {}}}]

        async def call(self, name, arguments, focus=""):
            return "untrusted tool observation"

    class Client:
        def __init__(self):
            self.calls = 0

        async def post(self, url, json=None, headers=None):
            self.calls += 1
            if self.calls == 1:
                message = {
                    "content": None,
                    "tool_calls": [{
                        "id": "call-1",
                        "function": {"name": "probe", "arguments": "{}"},
                    }],
                }
            else:
                message = {"content": "done"}
            return httpx.Response(
                200,
                json={"choices": [{"message": message}]},
                request=httpx.Request("POST", url),
            )

    events = []
    worker = OpenAICompatWorker(
        "local",
        base_url="http://fake/v1",
        model="m",
        client=Client(),
        tool_registry=Registry(),
    )
    token = bind_run_event_sink(lambda kind, payload: events.append((kind, payload)))
    try:
        result = await worker.run(Task(objective="use probe", tools=["probe"]))
    finally:
        reset_run_event_sink(token)

    assert result.output == "done"
    manifests = [
        ContextManifest.model_validate(payload["manifest"])
        for kind, payload in events
        if kind == "context.manifest.shadow"
    ]
    assert len(manifests) == 2
    second_messages = manifests[1].reconstruct_messages()
    tool_index = next(index for index, message in enumerate(second_messages) if message["role"] == "tool")
    tool_entry = [entry for entry in manifests[1].entries if entry.surface == "message"][tool_index]
    assert tool_entry.trust == ContextTrust.UNTRUSTED_EVIDENCE
    assert tool_entry.source_kind == ContextSourceKind.IMMUTABLE_ARTIFACT
