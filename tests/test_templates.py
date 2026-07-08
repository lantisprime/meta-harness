"""Workflow templates (deterministic spine) + SDLC capability suite."""
from __future__ import annotations

import httpx
import pytest

from metaharness.core.types import TaskType, Tier
from metaharness.evals import run_suite, sdlc_capability_suite, summarize_by_phase
from metaharness.harness import MockLLMWorker, ScriptedWorker
from metaharness.identity import KeyPair
from metaharness.web import HarnessState, create_app
from metaharness.workflows import get_template, list_templates
from metaharness.workflows.engine import RunStatus


def test_templates_registry_and_shapes():
    listed = list_templates()
    ids = {t["id"] for t in listed}
    assert {"software_engineering", "research"} <= ids
    assert get_template("nope") is None


def test_software_engineering_instantiates_deterministically():
    template = get_template("software_engineering")
    spec = template.instantiate("add a --json flag to the exporter")
    ids = [s.id for s in spec.steps]
    assert ids == ["explore", "specify", "plan", "implement", "verify", "review"]
    by_id = {s.id: s for s in spec.steps}
    # human gates exactly where the SDLC baseline puts them
    assert [s.id for s in spec.steps if s.hitl] == ["specify", "plan", "review"]
    # phases chain outputs through $steps refs
    assert by_id["implement"].inputs["plan"] == "$steps.plan.output"
    assert by_id["implement"].task_type is TaskType.CODE_EDIT
    assert by_id["implement"].tier_hint is Tier.FRONTIER
    assert "write_file" in by_id["implement"].tools
    assert by_id["explore"].tools == ["list_files", "grep", "read_file"]
    assert by_id["verify"].output_schema is not None
    # the goal is embedded in every phase contract
    assert all("--json flag" in s.objective for s in spec.steps)
    # same input -> same spine, twice (deterministic, no LLM)
    assert template.instantiate("add a --json flag to the exporter").model_dump() \
        == spec.model_dump()


def test_workflow_name_never_cuts_mid_word():
    """Humanize pass: long goals are shortened at a word boundary, so the UI
    never shows fragments like '…accepts contact de'."""
    template = get_template("software_engineering")
    spec = template.instantiate("build a web form that accepts contact details")
    short = spec.name.split(":", 1)[1]
    assert short == "build a web form that accepts contact"
    # short goals pass through untouched
    assert template.instantiate("tiny goal").name.endswith(":tiny goal")


async def test_template_run_end_to_end_with_gates(tmp_path):
    """The SE template runs through the real engine: parks at the spec gate,
    then the plan gate, then the review gate, then completes."""
    state = HarnessState()
    kp = KeyPair.generate()
    worker = ScriptedWorker(
        "w", lambda t: {"all_met": True, "criteria": []}
        if t.output_schema else f"work product for {t.id}",
        tier=Tier.FRONTIER, keypair=kp)
    state.register_worker(worker, kp, tiers=["frontier"])
    state.wire({Tier.FRONTIER: worker}, journal_dir=tmp_path)

    spec = get_template("software_engineering").instantiate("tiny goal")
    run = state.engine.start(spec, context={"goal": "tiny goal"})
    for expected_gate in ("specify", "plan", "review"):
        run = await state.engine.advance(run.run_id)
        assert run.status is RunStatus.AWAITING_APPROVAL
        assert run.awaiting == expected_gate
        state.engine.approve(run.run_id, expected_gate)
    run = await state.engine.advance(run.run_id)
    assert run.status is RunStatus.COMPLETED
    assert set(run.completed) == {"explore", "specify", "plan",
                                  "implement", "verify", "review"}


async def test_api_plan_from_workflow_type(tmp_path):
    state = HarnessState()
    kp = KeyPair.generate()
    worker = MockLLMWorker("w", Tier.FRONTIER, keypair=kp)
    state.register_worker(worker, kp, tiers=["frontier"])
    state.wire({Tier.FRONTIER: worker}, journal_dir=tmp_path)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(state)),
                                 base_url="http://test") as client:
        types = (await client.get("/api/workflow-types")).json()
        assert any(t["id"] == "software_engineering" for t in types)

        resp = await client.post("/api/plans", json={
            "goal": "build the widget", "workflow_type": "software_engineering"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["plan_source"] == "template:software_engineering"
        assert [s["id"] for s in data["workflow"]["steps"]][0] == "explore"

        bad = await client.post("/api/plans", json={
            "goal": "x", "workflow_type": "martian_dance"})
        assert bad.status_code == 422


# ------------------------------------------------------------- SDLC suite

def test_sdlc_suite_is_deterministically_checkable():
    tasks = sdlc_capability_suite()
    assert len(tasks) >= 10
    assert all(t.success_check for t in tasks)          # zero judges
    assert all(t.id.startswith("sdlc-") for t in tasks)
    phases = {t.id.split("-")[1] for t in tasks}
    assert {"localize", "spec", "plan", "edit", "verify", "gate"} <= phases


async def test_sdlc_suite_runs_and_summarizes_by_phase():
    answers = {
        "sdlc-localize-1": "parser.py", "sdlc-localize-2": "cli.py",
        "sdlc-spec-1": "2", "sdlc-spec-2": "ask",
        "sdlc-plan-1": "report.py",
        "sdlc-edit-1": "MAX_RETRIES = 5",
        "sdlc-edit-2": "def parse_documents(paths, strict=False):",
        "sdlc-verify-1": "12", "sdlc-verify-2": "no",
        "sdlc-gate-1": "no", "sdlc-gate-2": "code",
    }
    perfect = ScriptedWorker("gold", lambda t: answers[t.id],
                             keypair=KeyPair.generate())
    suite = await run_suite(perfect, sdlc_capability_suite(), k=3)
    assert suite.overall_pass_hat_k() == 1.0

    by_phase = summarize_by_phase(suite)
    assert by_phase["edit"]["pass_hat_k"] == 1.0
    assert set(by_phase) == {"localize", "spec", "plan", "edit", "verify", "gate"}

    # a gamer that asserts success without evidence fails verify + gate phases
    gamer = ScriptedWorker(
        "gamer", lambda t: {"sdlc-verify-2": "yes", "sdlc-gate-1": "yes",
                            "sdlc-gate-2": "test"}.get(t.id, answers[t.id]),
        keypair=KeyPair.generate())
    suite2 = await run_suite(gamer, sdlc_capability_suite(), k=3)
    by_phase2 = summarize_by_phase(suite2)
    assert by_phase2["verify"]["pass_hat_k"] < 1.0
    assert by_phase2["gate"]["pass_hat_k"] == 0.0
    assert by_phase2["edit"]["pass_hat_k"] == 1.0
