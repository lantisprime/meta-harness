"""Shared assignment eligibility, hard pins, and recursion protection."""
from __future__ import annotations

import pytest
import httpx
import subprocess
import sys
import yaml
from pydantic import ValidationError

from metaharness.blueprints import BlueprintContent, BlueprintVersion, prepare_blueprint_run
from metaharness.core.types import Task, TaskType, Tier
from metaharness.core.executor import TaskExecutor
from metaharness.harness import CodingAgentWorker, MockLLMWorker, ScriptedWorker
from metaharness.harness.runner import WorkerTimeout
from metaharness.identity import KeyPair
from metaharness.routing.eligibility import child_host_environment
from metaharness.routing import Router
from metaharness.tools import default_registry
from metaharness.web.state import HarnessState
from metaharness.web.app import create_app
from metaharness.workflows.dsl import StepSpec, WorkflowSpec


def _state_with_profiles(tmp_path) -> HarnessState:
    state = HarnessState()
    runners = []
    profiles = [
        ("researcher", "research", ["web-search"], ["reasoning"]),
        ("coder", "implementation", ["filesystem"], ["code_edit"]),
    ]
    for worker_id, role, capabilities, task_types in profiles:
        key = KeyPair.generate()
        runner = MockLLMWorker(worker_id, Tier.MID, keypair=key)
        state.register_worker(
            runner, key, tiers=["mid"], roles=[role],
            capabilities=capabilities, task_types=task_types,
        )
        runners.append(runner)
    state.wire({Tier.MID: runners}, journal_dir=tmp_path / "journals")
    return state


def _blueprint(**assignment) -> BlueprintVersion:
    workflow = WorkflowSpec.model_validate({
        "name": "assigned",
        "steps": [{
            "id": "work",
            "objective": "Do assigned work.",
            "task_type": "reasoning",
            **assignment,
        }],
    })
    return BlueprintVersion(
        id="assigned", version=1, published_at=1.0,
        name="Assigned", workflow=workflow,
    )


def test_worker_pin_and_tier_hint_are_rejected_as_ambiguous():
    with pytest.raises(ValidationError, match="cannot be combined"):
        StepSpec(id="x", objective="x", worker_id="w", tier_hint="mid")
    with pytest.raises(ValidationError, match="cannot be combined"):
        Task(worker_id="w", tier_hint=Tier.MID)


def test_blank_assignment_values_normalize_before_ambiguity_and_roundtrip():
    step = StepSpec(
        id="x", objective="x", worker_id="  ", role="\t",
        required_capabilities=[" ", "search", "search"], tier_hint="mid",
    )
    assert step.worker_id is None and step.role is None
    assert step.required_capabilities == ["search"]
    task = step.to_task({})
    assert task.worker_id is None and task.role is None
    assert task.required_capabilities == ["search"]
    plain = StepSpec(id="plain", objective="plain")
    dumped = plain.model_dump(mode="json")
    assert "worker_id" not in dumped
    assert "role" not in dumped
    assert "required_capabilities" not in dumped


def test_routing_imports_work_in_a_fresh_interpreter():
    code = (
        "from metaharness.routing import Router; "
        "from metaharness.routing.eligibility import worker_eligibility; "
        "from metaharness.routing.router import Router as DirectRouter; "
        "from metaharness.core import TaskExecutor"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=15
    )
    assert completed.returncode == 0, completed.stderr


def test_readiness_and_router_use_same_role_capability_predicate(tmp_path):
    state = _state_with_profiles(tmp_path)
    blueprint = _blueprint(role="research", required_capabilities=["web-search"])
    result = prepare_blueprint_run(
        blueprint, {}, tools=default_registry(), mcp_servers={}, router=state.router,
    )
    assert result.ready
    task = blueprint.workflow.steps[0].to_task({})
    assert state.router.decide(task).worker_id == "researcher"
    assert [m.worker_id for m in state.router.eligible_members(Tier.MID, task)] == [
        "researcher"
    ]


def test_pin_mismatch_is_structured_and_router_never_substitutes(tmp_path):
    state = _state_with_profiles(tmp_path)
    blueprint = _blueprint(
        worker_id="coder", role="research", required_capabilities=["web-search"]
    )
    result = prepare_blueprint_run(
        blueprint, {}, tools=default_registry(), mcp_servers={}, router=state.router,
    )
    assert not result.ready
    assert result.issues[0].code == "pin_mismatch"
    assert result.issues[0].worker_id == "coder"
    with pytest.raises(ValueError, match="pinned worker 'coder' is not eligible"):
        state.router.decide(blueprint.workflow.steps[0].to_task({}))


def test_retired_or_missing_pin_is_not_ready(tmp_path):
    state = _state_with_profiles(tmp_path)
    blueprint = _blueprint(worker_id="researcher")
    state.registry.deactivate("researcher")
    retired = prepare_blueprint_run(
        blueprint, {}, tools=default_registry(), mcp_servers={}, router=state.router,
    )
    assert not retired.ready
    assert retired.issues[0].code == "missing_worker"

    missing = prepare_blueprint_run(
        _blueprint(worker_id="ghost"), {}, tools=default_registry(),
        mcp_servers={}, router=state.router,
    )
    assert not missing.ready
    assert missing.issues[0].code == "missing_worker"


def test_no_eligible_worker_is_structured(tmp_path):
    state = _state_with_profiles(tmp_path)
    result = prepare_blueprint_run(
        _blueprint(role="legal", required_capabilities=["court-records"]), {},
        tools=default_registry(), mcp_servers={}, router=state.router,
    )
    assert not result.ready
    assert result.issues[0].code == "no_eligible_worker"


def test_tier_hint_remains_a_floor_not_an_exact_assignment(tmp_path):
    state = HarnessState()
    key = KeyPair.generate()
    runner = MockLLMWorker("frontier", Tier.FRONTIER, keypair=key)
    state.register_worker(runner, key, tiers=["frontier"])
    state.wire({Tier.FRONTIER: runner}, journal_dir=tmp_path / "journals")
    blueprint = _blueprint(tier_hint="mid")
    result = prepare_blueprint_run(
        blueprint, {}, tools=default_registry(), mcp_servers={}, router=state.router,
    )
    assert result.ready
    assert state.router.decide(blueprint.workflow.steps[0].to_task({})).worker_id == "frontier"


def test_readding_worker_at_new_tier_removes_all_stale_pool_entries(tmp_path):
    state = HarnessState()
    first_key = KeyPair.generate()
    first = MockLLMWorker("movable", Tier.SMALL, keypair=first_key)
    state.register_worker(first, first_key, tiers=["small"])
    state.wire({Tier.SMALL: first}, journal_dir=tmp_path / "journals")

    replacement_key = KeyPair.generate()
    replacement = MockLLMWorker("movable", Tier.FRONTIER, keypair=replacement_key)
    state.add_worker(replacement, Tier.FRONTIER)
    assert state.router.pool(Tier.SMALL) == []
    assert [member.worker_id for member in state.router.pool(Tier.FRONTIER)] == [
        "movable"
    ]
    task = _blueprint(worker_id="movable").workflow.steps[0].to_task({})
    assert state.router.decide(task).tier is Tier.FRONTIER
    result = prepare_blueprint_run(
        _blueprint(worker_id="movable"), {}, tools=default_registry(),
        mcp_servers={}, router=state.router,
    )
    assert result.ready


@pytest.mark.asyncio
async def test_hard_pin_retries_exact_worker_without_frontier_substitution():
    calls: list[str] = []

    def pinned_handler(task):
        calls.append("pinned")
        return "wrong" if len(calls) == 1 else "right"

    frontier_calls: list[str] = []
    pinned = ScriptedWorker("pinned", pinned_handler, tier=Tier.MID)
    frontier = ScriptedWorker(
        "other", lambda task: frontier_calls.append("other") or "right",
        tier=Tier.FRONTIER,
    )
    executor = TaskExecutor(Router({Tier.MID: pinned, Tier.FRONTIER: frontier}))
    outcome = await executor.execute(Task(
        worker_id="pinned", objective="answer", max_attempts=2,
        success_check={"one_of": ["right"]},
    ))
    assert outcome.final_output == "right"
    assert [attempt.result.worker_id for attempt in outcome.attempts] == [
        "pinned", "pinned"
    ]
    assert outcome.escalations == 0
    assert frontier_calls == []


@pytest.mark.asyncio
async def test_hard_pin_timeout_retries_exact_worker_without_escalation():
    calls = 0

    def pinned_handler(task):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise WorkerTimeout("first timeout", timeout_s=1)
        return "right"

    pinned = ScriptedWorker("pinned", pinned_handler, tier=Tier.MID)
    frontier = ScriptedWorker("other", lambda task: "right", tier=Tier.FRONTIER)
    executor = TaskExecutor(Router({Tier.MID: pinned, Tier.FRONTIER: frontier}))
    outcome = await executor.execute(Task(
        worker_id="pinned", objective="answer", max_attempts=2,
        success_check={"one_of": ["right"]},
    ))
    assert outcome.final_output == "right"
    assert [attempt.result.worker_id for attempt in outcome.attempts] == [
        "pinned", "pinned"
    ]
    assert outcome.escalations == 0


def test_recursion_chain_blocks_direct_and_a_to_b_to_a_cycles(monkeypatch, tmp_path):
    monkeypatch.setenv("METAHARNESS_HOST", "codex")
    monkeypatch.delenv("METAHARNESS_HOST_CHAIN", raising=False)
    first = child_host_environment("pi")
    assert first == {
        "METAHARNESS_HOST": "pi",
        "METAHARNESS_HOST_CHAIN": "codex,pi",
    }
    with pytest.raises(RuntimeError, match="already active"):
        child_host_environment("codex")

    monkeypatch.setenv("METAHARNESS_HOST", "claude-code")
    with pytest.raises(RuntimeError, match="host 'claude' is already active"):
        child_host_environment("claude")

    monkeypatch.setenv("METAHARNESS_HOST", "codex")
    state = HarnessState()
    key = KeyPair.generate()
    runner = MockLLMWorker("nested-codex", Tier.MID, keypair=key)
    # Host metadata is explicit because this mock stands in for a Codex CLI.
    state.register_worker(runner, key, tiers=["mid"], host="codex")
    state.wire({Tier.MID: runner}, journal_dir=tmp_path / "journals")
    result = prepare_blueprint_run(
        _blueprint(worker_id="nested-codex"), {}, tools=default_registry(),
        mcp_servers={}, router=state.router,
    )
    assert not result.ready
    assert result.issues[0].code == "unsafe_recursion"


@pytest.mark.asyncio
async def test_coding_worker_recursion_guard_runs_before_subprocess(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("METAHARNESS_HOST", "codex")
    spawned = False

    async def should_not_spawn(*args, **kwargs):
        nonlocal spawned
        spawned = True
        raise AssertionError("subprocess spawn should be unreachable")

    monkeypatch.setattr("asyncio.create_subprocess_exec", should_not_spawn)
    worker = CodingAgentWorker(
        "nested", cli="codex", workspace=tmp_path, binary="codex"
    )
    with pytest.raises(RuntimeError, match="unsafe recursive harness spawn"):
        await worker._execute(Task(objective="do work"))
    assert spawned is False


@pytest.mark.asyncio
async def test_retired_pin_run_intake_creates_zero_run_or_journal(tmp_path):
    state = _state_with_profiles(tmp_path)
    state.enable_persistence(tmp_path / "store")
    blueprint = _blueprint(worker_id="researcher")
    content = BlueprintContent.model_validate(
        blueprint.model_dump(mode="json", exclude={"id", "version", "published_at"})
    )
    draft = state.blueprint_store.create_draft("assigned", content, owner="tester")
    state.blueprint_store.publish("assigned", expected_revision=draft.revision)
    state.registry.deactivate("researcher")

    app = create_app(state)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        preview = await client.post(
            "/api/blueprints/readiness",
            json={"blueprint": {"id": "assigned", "version": 1}, "context": {}},
        )
        started = await client.post(
            "/api/runs",
            json={"blueprint": {"id": "assigned", "version": 1}, "context": {}},
        )
    assert preview.status_code == 200
    assert preview.json()["issues"][0]["code"] == "missing_worker"
    assert started.status_code == 409
    assert state.engine.runs() == []
    assert list((tmp_path / "journals").glob("*.jsonl")) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("source_field", ["workflow", "workflow_yaml"])
async def test_ad_hoc_assignment_validate_and_run_fail_before_journal(
    source_field, tmp_path
):
    state = _state_with_profiles(tmp_path)
    workflow = _blueprint(worker_id="ghost").workflow.model_dump(mode="json")
    source = workflow if source_field == "workflow" else yaml.safe_dump(workflow)
    body = {source_field: source, "context": {}}
    app = create_app(state)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        validated = await client.post("/api/workflows/validate", json=body)
        started = await client.post("/api/runs", json=body)
    assert validated.status_code == 409
    assert validated.json()["detail"]["issues"][0]["code"] == "missing_worker"
    assert started.status_code == 409
    assert started.json()["detail"]["issues"][0]["code"] == "missing_worker"
    assert state.engine.runs() == []
    assert list((tmp_path / "journals").glob("*.jsonl")) == []
