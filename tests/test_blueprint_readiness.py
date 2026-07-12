from __future__ import annotations

import io
import json
import zipfile

import httpx
import pytest
from pydantic import ValidationError

from metaharness.blueprints import (
    BlueprintContent,
    BlueprintVersion,
    InputSpec,
    LocalSecretBindingRegistry,
    SecretBindingUseError,
    get_builtin_version,
    prepare_blueprint_run,
    resolve_blueprint_workflow,
)
from metaharness.config import MCPServerConfig
from metaharness.core.types import TaskType, Tier
from metaharness.harness.workers import MockLLMWorker
from metaharness.identity.keys import KeyPair
from metaharness.tools import ToolSpec, default_registry
from metaharness.tools.mcp import mcp_config_fingerprint
from metaharness.web.app import create_app
from metaharness.web.state import HarnessState
from metaharness.workflows.dsl import WorkflowSpec


def _workflow(*, tools: list[str] | None = None, inputs=None) -> WorkflowSpec:
    return WorkflowSpec.model_validate({
        "name": "readiness-test",
        "steps": [{
            "id": "work",
            "task_type": "general",
            "objective": "Do the work.",
            "inputs": inputs or {},
            "tools": tools or [],
        }],
    })


def _version(
    *,
    version: int = 1,
    inputs: list[InputSpec] | None = None,
    default_context: dict | None = None,
    tools: list[str] | None = None,
    step_inputs=None,
) -> BlueprintVersion:
    return BlueprintVersion(
        id="ready-bp",
        version=version,
        published_at=1.0,
        name=f"Ready v{version}",
        workflow=_workflow(tools=tools, inputs=step_inputs),
        inputs=inputs or [],
        default_context=default_context or {},
    )


@pytest.fixture
def readiness_state(tmp_path) -> HarnessState:
    state = HarnessState()
    state.enable_persistence(tmp_path / "store")
    (tmp_path / "journals").mkdir()
    keypair = KeyPair.generate()
    runner = MockLLMWorker(
        "w-small",
        Tier.SMALL,
        keypair=keypair,
        seed=1,
        skills={task_type: 1.0 for task_type in TaskType},
    )
    state.register_worker(runner, keypair, tiers=["small"])
    state.wire({Tier.SMALL: runner}, journal_dir=tmp_path / "journals")
    return state


def _publish(state: HarnessState, blueprint: BlueprintVersion) -> None:
    content = BlueprintContent.model_validate(
        blueprint.model_dump(
            mode="json", exclude={"id", "version", "published_at"}
        )
    )
    draft = state.blueprint_store.create_draft(
        blueprint.id, content, owner="tester"
    )
    published = state.blueprint_store.publish(
        blueprint.id, expected_revision=draft.revision
    )
    assert published.version == blueprint.version


def test_prepare_defaults_required_schema_unknown_and_deterministic_order():
    blueprint = _version(
        inputs=[
            InputSpec(
                name="title", schema={"type": "string", "minLength": 3},
                required=True, default="input-default",
            ),
            InputSpec(name="count", schema={"type": "integer"}, required=True),
        ],
        default_context={"title": "context-default", "ambient": "preserved"},
    )
    tools = default_registry()

    ready = prepare_blueprint_run(
        blueprint, {"title": "request-value", "count": 2},
        tools=tools, mcp_servers={},
    )
    assert ready.ready
    assert ready.normalized_context == {
        "title": "request-value", "count": 2, "ambient": "preserved"
    }

    invalid = prepare_blueprint_run(
        blueprint, {"title": "x", "zzz": True}, tools=tools, mcp_servers={}
    )
    assert [issue.code for issue in invalid.issues] == [
        "invalid_input", "invalid_input", "invalid_input"
    ]
    assert [issue.input_name for issue in invalid.issues] == ["zzz", "title", "count"]


def test_prepare_reports_malformed_schema_without_raising():
    blueprint = _version(inputs=[
        InputSpec(name="bad", schema={"type": "not-a-json-schema-type"})
    ])
    result = prepare_blueprint_run(
        blueprint, {"bad": "value"}, tools=default_registry(), mcp_servers={}
    )
    assert not result.ready
    assert result.issues[0].code == "invalid_input"
    assert "invalid JSON Schema" in result.issues[0].message


def test_local_schema_refs_and_property_named_ref_are_allowed():
    blueprint = _version(inputs=[
        InputSpec(
            name="local",
            schema={
                "$defs": {"label": {"type": "string", "minLength": 2}},
                "$ref": "#/$defs/label",
            },
            required=True,
        ),
        InputSpec(
            name="object",
            schema={
                "type": "object",
                "properties": {"$ref": {"type": "string"}},
                "required": ["$ref"],
            },
            required=True,
        ),
    ])
    result = prepare_blueprint_run(
        blueprint,
        {"local": "ok", "object": {"$ref": "ordinary property value"}},
        tools=default_registry(), mcp_servers={},
    )
    assert result.ready
    assert result.normalized_context["local"] == "ok"


def test_declared_secret_cannot_be_read_through_renamed_nested_step_input():
    with pytest.raises(ValidationError, match="cannot read a declared secret"):
        BlueprintContent(
            name="bypass",
            workflow=_workflow(inputs={"renamed": {"nested": "$context.token"}}),
            inputs=[InputSpec(
                name="token", schema={"type": "string"}, secret=True
            )],
        )


def test_secret_values_fail_closed_and_never_enter_normalized_context():
    blueprint = _version(inputs=[InputSpec(
        name="token", schema={"type": "string"}, required=True, secret=True
    )])

    literal = prepare_blueprint_run(
        blueprint, {"token": "super-secret-literal"},
        tools=default_registry(), mcp_servers={},
    )
    assert literal.issues[0].code == "invalid_input"
    assert "super-secret-literal" not in literal.model_dump_json()
    assert "token" not in literal.normalized_context

    binding = prepare_blueprint_run(
        blueprint, {"token": {"binding": "service-token"}},
        tools=default_registry(), mcp_servers={},
    )
    assert binding.issues[0].code == "missing_secret_binding"
    assert binding.issues[0].repair.action == "configure_secret_binding"
    assert "token" not in binding.normalized_context


def test_configured_secret_binding_passes_readiness_without_resolving_plaintext():
    secret = "sk-test-never-journal-this-value"
    registry = LocalSecretBindingRegistry({"service-token": secret})
    blueprint = _version(
        inputs=[InputSpec(
            name="token", schema={"type": "string"}, required=True,
            secret=True, default={"binding": "service-token"},
        )],
        step_inputs={"token": {"binding": "service-token"}},
    )

    result = prepare_blueprint_run(
        blueprint, {}, tools=default_registry(), mcp_servers={},
        secret_bindings=registry,
    )

    assert result.ready
    assert result.normalized_context == {}
    assert secret not in result.model_dump_json()
    assert registry.use("service-token", lambda value: value == secret) is True
    with pytest.raises(SecretBindingUseError) as raised:
        registry.use("service-token", lambda value: (_ for _ in ()).throw(
            RuntimeError(f"provider echoed {value}")
        ))
    assert secret not in str(raised.value)


def test_local_secret_registry_rejects_credential_shaped_binding_names():
    with pytest.raises(ValueError, match="resembles credential material"):
        LocalSecretBindingRegistry({"sk-test-abcdefghijk": "actual-value"})


def test_builtin_run_rendering_is_fully_resolved_and_input_specific():
    from metaharness.workflows.engine import _snapshot_digest

    blueprint = get_builtin_version("research", 1)
    assert blueprint is not None
    first = resolve_blueprint_workflow(blueprint, {"goal": "Explain alpha"})
    second = resolve_blueprint_workflow(blueprint, {"goal": "Explain beta"})
    authored = blueprint.model_dump(mode="json")

    assert "$context.goal" not in first.model_dump_json()
    assert first.steps[0].inputs["goal"] == "Explain alpha"
    assert "Explain alpha" in first.steps[0].objective
    assert first != second
    assert _snapshot_digest(first, authored) != _snapshot_digest(second, authored)
    # Rendering a run must never mutate the immutable authored seed.
    assert blueprint.workflow.steps[0].inputs["goal"] == "$context.goal"


def test_run_rendering_rejects_secret_values_in_context():
    blueprint = _version(inputs=[InputSpec(
        name="token", schema={"type": "string"}, secret=True,
    )])
    with pytest.raises(ValueError, match="secret inputs cannot enter"):
        resolve_blueprint_workflow(blueprint, {"token": "plaintext"})


def test_tool_readiness_uses_loaded_registry_and_classifies_mcp_state():
    registry = default_registry()
    configured = MCPServerConfig(
        name="search", transport="stdio", command="unused", enabled=True
    )
    disabled = MCPServerConfig(
        name="mail", transport="stdio", command="unused", enabled=False
    )

    built_in = prepare_blueprint_run(
        _version(tools=["calculator"]), {}, tools=registry,
        mcp_servers={"search": configured, "mail": disabled},
    )
    assert built_in.ready

    result = prepare_blueprint_run(
        _version(tools=["ghost", "search.web", "mail.send"]), {},
        tools=registry, mcp_servers={"search": configured, "mail": disabled},
    )
    assert [(issue.code, issue.repair.action) for issue in result.issues] == [
        ("missing_tool", "choose_tool"),
        ("unloaded_mcp", "load_mcp"),
        ("unloaded_mcp", "enable_mcp"),
    ]
    assert [issue.mcp_state for issue in result.issues] == [
        None, "never_loaded", "disabled"
    ]

    for load_state in ("load_failed", "zero_tools"):
        unavailable = prepare_blueprint_run(
            _version(tools=["search.web"]), {}, tools=registry,
            mcp_servers={"search": configured},
            mcp_load_status={"search": {
                "status": load_state,
                "fingerprint": mcp_config_fingerprint(configured),
            }},
        )
        assert unavailable.issues[0].mcp_state == load_state

    registry.register(ToolSpec(
        name="search.other", description="loaded", input_schema={},
        handler=lambda: "ok", source="mcp:search",
    ))
    loaded_but_absent = prepare_blueprint_run(
        _version(tools=["search.web"]), {}, tools=registry,
        mcp_servers={"search": configured},
        mcp_load_status={"search": {
            "status": "loaded", "fingerprint": mcp_config_fingerprint(configured)
        }},
    )
    assert loaded_but_absent.issues[0].code == "missing_tool"

    registry.register(ToolSpec(
        name="search.web", description="web", input_schema={},
        handler=lambda: "ok", source="mcp:search",
    ))
    stale = prepare_blueprint_run(
        _version(tools=["search.web"]), {}, tools=registry,
        mcp_servers={"search": configured},
        mcp_load_status={"search": {
            "status": "loaded", "fingerprint": "0" * 64,
        }},
    )
    assert stale.issues[0].mcp_state == "stale_config"

    unconfigured_registry = default_registry()
    unconfigured_registry.register(ToolSpec(
        name="orphan.call", description="orphan", input_schema={},
        handler=lambda: "ok", source="mcp:orphan",
    ))
    orphan = prepare_blueprint_run(
        _version(tools=["orphan.call"]), {}, tools=unconfigured_registry,
        mcp_servers={}, mcp_load_status={},
    )
    assert orphan.issues[0].code == "missing_tool"


async def test_preview_run_parity_and_stale_tool_recheck_has_zero_journals(
    readiness_state, tmp_path
):
    blueprint = _version(tools=["calculator"])
    _publish(readiness_state, blueprint)
    app = create_app(readiness_state)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        preview = await client.post("/api/blueprints/readiness", json={
            "blueprint": {"id": "ready-bp", "version": 1}, "context": {}
        })
        assert preview.status_code == 200
        assert preview.json()["ready"] is True
        assert "normalized_context" not in preview.json()

        readiness_state.tools.unregister_source("builtin")
        journal_dir = tmp_path / "journals"
        before = list(journal_dir.glob("*.jsonl"))
        run = await client.post("/api/runs", json={
            "blueprint": {"id": "ready-bp", "version": 1}, "context": {}
        })
        after = list(journal_dir.glob("*.jsonl"))

    assert run.status_code == 409
    assert run.json()["detail"]["issues"][0]["code"] == "missing_tool"
    assert before == after == []
    assert readiness_state.engine.runs() == []


async def test_readiness_input_statuses_exact_versions_and_secret_free_run_package(
    readiness_state, tmp_path
):
    v1 = _version(inputs=[
        InputSpec(name="title", schema={"type": "string"}, required=True),
        InputSpec(name="token", schema={"type": "string"}, secret=True),
    ])
    _publish(readiness_state, v1)
    store = readiness_state.blueprint_store
    draft = store.create_draft_from_version(v1.ref, owner="tester")
    content_v2 = BlueprintContent.model_validate({
        **draft.model_dump(
            mode="json",
            exclude={"id", "revision", "base_version", "owner", "created_at", "updated_at"},
        ),
        "name": "Ready v2",
    })
    updated = store.update_draft(
        v1.id, content_v2, expected_revision=draft.revision
    )
    store.publish(v1.id, expected_revision=updated.revision)

    app = create_app(readiness_state)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        preview = await client.post("/api/blueprints/readiness", json={
            "blueprint": {"id": "ready-bp", "version": 1},
            "context": {"title": "v1-run"},
        })
        assert preview.status_code == 200
        assert preview.json()["blueprint_ref"]["version"] == 1
        assert preview.json()["ready"] is True

        unknown = await client.post("/api/runs", json={
            "blueprint": {"id": "ready-bp", "version": 1},
            "context": {"title": "ok", "unknown": True},
        })
        assert unknown.status_code == 422

        missing_binding = await client.post("/api/runs", json={
            "blueprint": {"id": "ready-bp", "version": 1},
            "context": {"title": "ok", "token": {"binding": "service-token"}},
        })
        assert missing_binding.status_code == 409

        successful = await client.post("/api/runs", json={
            "blueprint": {"id": "ready-bp", "version": 1},
            "context": {"title": "v1-run"}, "wait": True,
        })
        assert successful.status_code == 200, successful.text
        run = successful.json()
        assert run["blueprint_ref"]["version"] == 1
        assert "token" not in run["context"]

        detail = (await client.get(f"/api/runs/{run['run_id']}")).json()
        started = next(event for event in detail["journal"]
                       if event["kind"] == "run.started")
        assert "token" not in started["payload"]["context"]

        package = await client.get(f"/api/runs/{run['run_id']}/package")
        assert package.status_code == 200
        archive = zipfile.ZipFile(io.BytesIO(package.content))
        journal = archive.read("journal.jsonl").decode()
        started_in_package = json.loads(journal.splitlines()[0])
        assert "token" not in started_in_package["payload"]["context"]
        assert "super-secret-literal" not in package.content.decode("latin1")


async def test_exact_runs_render_distinct_workflows_and_snapshot_digests(
    readiness_state,
):
    blueprint = BlueprintVersion(
        id="rendered-run",
        version=1,
        published_at=1.0,
        name="Rendered run",
        workflow=WorkflowSpec.model_validate({
            "name": "rendered-run",
            "steps": [{
                "id": "work",
                "objective": "Complete this exact goal: $context.goal",
                "inputs": {"goal": "$context.goal"},
            }],
        }),
        inputs=[InputSpec(
            name="goal", schema={"type": "string", "minLength": 1}, required=True,
        )],
    )
    _publish(readiness_state, blueprint)
    app = create_app(readiness_state)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        runs = []
        for goal in ("Explain alpha", "Explain beta"):
            response = await client.post("/api/runs", json={
                "blueprint": {"id": "rendered-run", "version": 1},
                "context": {"goal": goal}, "wait": True,
            })
            assert response.status_code == 200, response.text
            runs.append((goal, (await client.get(
                f"/api/runs/{response.json()['run_id']}"
            )).json()))

    started = [
        next(event for event in detail["journal"] if event["kind"] == "run.started")
        for _, detail in runs
    ]
    assert started[0]["payload"]["snapshot_digest"] != started[1]["payload"]["snapshot_digest"]
    for (goal, _), event in zip(runs, started):
        workflow = event["payload"]["workflow"]
        assert workflow["steps"][0]["inputs"]["goal"] == goal
        assert goal in workflow["steps"][0]["objective"]
        assert "$context.goal" not in json.dumps(workflow)
        # Authored identity remains the same immutable v1 snapshot.
        assert event["payload"]["blueprint_snapshot"]["workflow"]["steps"][0][
            "inputs"
        ]["goal"] == "$context.goal"


async def test_configured_secret_binding_runs_without_plaintext_leak(
    readiness_state, tmp_path,
):
    sentinel = "sk-test-never-leak-runtime-value-4af9"
    readiness_state.config_path = tmp_path / "config.json"
    blueprint = _version(
        inputs=[InputSpec(
            name="token", schema={"type": "string"}, required=True,
            secret=True, default={"binding": "service-token"},
        )],
        step_inputs={"token": {"binding": "service-token"}},
    )
    _publish(readiness_state, blueprint)
    app = create_app(readiness_state)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        configured = await client.post("/api/config/secret-bindings", json={
            "name": "service-token", "value": sentinel,
        })
        assert configured.status_code == 200
        readiness = await client.post("/api/blueprints/readiness", json={
            "blueprint": {"id": "ready-bp", "version": 1}, "context": {},
        })
        assert readiness.json()["ready"] is True
        response = await client.post("/api/runs", json={
            "blueprint": {"id": "ready-bp", "version": 1},
            "context": {}, "wait": True,
        })
        assert response.status_code == 200, response.text
        run_id = response.json()["run_id"]
        detail = await client.get(f"/api/runs/{run_id}")
        package = await client.get(f"/api/runs/{run_id}/package")
        public = await client.get("/api/config")

    assert sentinel not in detail.text
    assert sentinel not in package.content.decode("latin1")
    assert sentinel not in public.text
    assert "token" not in response.json()["context"]


async def test_http_validation_never_echoes_literal_or_wrapped_secret_values(
    readiness_state,
):
    sentinel = "DO-NOT-ECHO-secret-7f31"
    base = {
        "schema_version": 1,
        "name": "Secret validation",
        "workflow": _workflow().model_dump(mode="json"),
        "inputs": [{
            "name": "token", "schema": {"type": "string"}, "secret": True,
        }],
    }
    app = create_app(readiness_state)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        cases = [
            {
                "blueprint_id": "literal-default",
                "content": {
                    **base,
                    "inputs": [{**base["inputs"][0], "default": sentinel}],
                },
            },
            {
                "blueprint_id": "wrapped-default",
                "content": {
                    **base,
                    "inputs": [{
                        **base["inputs"][0],
                        "default": {"binding": {"wrapped": sentinel}},
                    }],
                },
            },
            {
                "blueprint_id": "literal-step",
                "content": {
                    **base,
                    "workflow": _workflow(inputs={"token": sentinel}).model_dump(mode="json"),
                },
            },
            {
                "blueprint_id": "wrapped-step",
                "content": {
                    **base,
                    "workflow": _workflow(inputs={
                        "token": {"binding": {"wrapped": sentinel}}
                    }).model_dump(mode="json"),
                },
            },
        ]
        for case in cases:
            response = await client.post("/api/blueprint-drafts", json={
                **case, "owner": "tester",
            })
            assert response.status_code == 422
            assert sentinel not in response.text
            assert all(
                set(error) == {"loc", "msg", "type"}
                for error in response.json()["detail"]
            )


async def test_schema_refs_are_rejected_without_retrieval_500_or_journal(
    readiness_state, tmp_path,
):
    sentinel = "private-schema-input-91af"
    blueprint = _version(inputs=[
        InputSpec(
            name="http_ref",
            schema={"$ref": "https://127.0.0.1:1/never-fetch.json"},
            required=True,
        ),
        InputSpec(
            name="urn_ref", schema={"$ref": "urn:private:test"}, required=True,
        ),
        InputSpec(
            name="file_ref", schema={"$dynamicRef": "file:///private/schema.json"},
            required=True,
        ),
    ])
    _publish(readiness_state, blueprint)
    app = create_app(readiness_state)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        preview = await client.post("/api/blueprints/readiness", json={
            "blueprint": {"id": "ready-bp", "version": 1},
            "context": {
                "http_ref": sentinel, "urn_ref": sentinel, "file_ref": sentinel,
            },
        })
        assert preview.status_code == 200
        assert [issue["input_name"] for issue in preview.json()["issues"]] == [
            "http_ref", "urn_ref", "file_ref"
        ]
        assert all(issue["code"] == "invalid_input"
                   for issue in preview.json()["issues"])
        assert sentinel not in preview.text

        run = await client.post("/api/runs", json={
            "blueprint": {"id": "ready-bp", "version": 1},
            "context": {
                "http_ref": sentinel, "urn_ref": sentinel, "file_ref": sentinel,
            },
        })
        assert run.status_code == 422
        assert sentinel not in run.text
    assert list((tmp_path / "journals").glob("*.jsonl")) == []


async def test_mixed_input_and_environment_failures_use_conflict_status(
    readiness_state,
):
    blueprint = _version(
        inputs=[InputSpec(
            name="required", schema={"type": "string"}, required=True
        )],
        tools=["missing-tool"],
    )
    _publish(readiness_state, blueprint)
    app = create_app(readiness_state)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        run = await client.post("/api/runs", json={
            "blueprint": {"id": "ready-bp", "version": 1}, "context": {}
        })
    assert run.status_code == 409
    assert {issue["code"] for issue in run.json()["detail"]["issues"]} == {
        "invalid_input", "missing_tool"
    }


async def test_workflow_validate_surfaces_same_blueprint_readiness(
    readiness_state,
):
    blueprint = _version(inputs=[InputSpec(
        name="required", schema={"type": "string"}, required=True
    )])
    _publish(readiness_state, blueprint)
    body = {"blueprint": {"id": "ready-bp", "version": 1}, "context": {}}
    app = create_app(readiness_state)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        preview = (await client.post("/api/blueprints/readiness", json=body)).json()
        validated = await client.post("/api/workflows/validate", json=body)
    assert validated.status_code == 200
    assert validated.json()["readiness"] == preview
    assert "normalized_context" not in validated.json()["readiness"]


async def test_effective_mcp_hitl_workflow_and_authored_snapshot_share_digest(
    readiness_state,
):
    import hashlib

    from metaharness.workflows.engine import _canonical_json_bytes, _snapshot_digest

    server = MCPServerConfig(
        name="mail", transport="stdio", command="unused", enabled=True
    )
    readiness_state.config.mcp_servers["mail"] = server
    readiness_state.tools.register(ToolSpec(
        name="mail.send", description="send", input_schema={},
        handler=lambda: "ok", source="mcp:mail",
    ))
    readiness_state.mcp_load_status["mail"] = {
        "ok": True, "status": "loaded", "tools": 1,
        "fingerprint": mcp_config_fingerprint(server),
    }
    blueprint = _version(tools=["mail.send"])
    _publish(readiness_state, blueprint)

    app = create_app(readiness_state)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/runs", json={
            "blueprint": {"id": "ready-bp", "version": 1},
            "context": {}, "wait": True,
        })
        assert response.status_code == 200, response.text
        run = response.json()
        detail = (await client.get(f"/api/runs/{run['run_id']}")).json()
        package = await client.get(f"/api/runs/{run['run_id']}/package")

    started = next(event for event in detail["journal"]
                   if event["kind"] == "run.started")
    effective = WorkflowSpec.model_validate(started["payload"]["workflow"])
    authored = started["payload"]["blueprint_snapshot"]
    assert authored["workflow"]["steps"][0]["hitl"] is False
    assert effective.steps[0].hitl is True
    assert started["payload"]["snapshot_digest"] == _snapshot_digest(
        effective, authored
    )
    old_blueprint_only = hashlib.sha256(_canonical_json_bytes(authored)).hexdigest()
    assert started["payload"]["snapshot_digest"] != old_blueprint_only

    archive = zipfile.ZipFile(io.BytesIO(package.content))
    assert json.loads(archive.read("blueprint.json"))["workflow"]["steps"][0]["hitl"] is False
    assert json.loads(archive.read("workflow.json"))["steps"][0]["hitl"] is True


def test_binding_shaped_ordinary_data_is_not_treated_as_a_secret():
    blueprint = _version(step_inputs={"payload": {"binding": "ordinary-data"}})
    result = prepare_blueprint_run(
        blueprint, {}, tools=default_registry(), mcp_servers={}
    )
    assert result.ready


def test_null_input_default_is_documented_as_absent():
    blueprint = _version(inputs=[InputSpec(
        name="nullable", schema={"type": ["null", "string"]},
        required=True, default=None,
    )])
    result = prepare_blueprint_run(
        blueprint, {}, tools=default_registry(), mcp_servers={}
    )
    assert not result.ready
    assert result.issues[0].input_name == "nullable"
