"""Run packaging: the zip a finished run exports.

Root discipline under test: workspace/ contains only files changed during the
run window under each step's RECORDED root — never the whole workspace, never
a guessed path — and every cap/omission is listed in the manifest.
"""
from __future__ import annotations

import io
import json
import os
import time
import zipfile

import httpx
import pytest

from metaharness.core.types import TaskType, Tier, Verdict
from metaharness.harness import MockLLMWorker
from metaharness.identity import KeyPair
from metaharness.web import HarnessState, create_app
from metaharness.workflows import WorkflowEngine, load_workflow
from metaharness.workflows.package import build_package_bytes

YAML = """
name: pkg-test
steps:
  - id: classify
    task_type: classify
    objective: Classify severity.
    inputs: {labels: [low, high]}
    success_check: {one_of: [low, high]}
  - id: report
    task_type: summarize
    objective: Summarize.
    depends_on: [classify]
"""


def _perfect_engine(journal_dir):
    from metaharness.core import TaskExecutor
    from metaharness.harness import ScriptedWorker
    from metaharness.routing import Router

    def handler(task):
        return "high" if task.task_type == TaskType.CLASSIFY else "## Report\n\nfine"

    journal_dir.mkdir(parents=True, exist_ok=True)
    executor = TaskExecutor(Router({Tier.SMALL: ScriptedWorker("w", handler)}))
    return WorkflowEngine(executor, journal_dir=journal_dir)


async def test_package_members_and_manifest(tmp_path):
    engine = _perfect_engine(tmp_path)
    spec = load_workflow(YAML)
    state = engine.start(spec, context={})
    state = await engine.advance(state.run_id)
    assert state.status.value == "completed"

    payload = build_package_bytes(spec, state, engine.journal(state.run_id).entries())
    zf = zipfile.ZipFile(io.BytesIO(payload))
    names = set(zf.namelist())
    assert {"manifest.json", "workflow.json", "journal.jsonl",
            "steps/classify.md", "steps/report.md"} <= names

    manifest = json.loads(zf.read("manifest.json"))
    assert manifest["run_id"] == state.run_id
    assert manifest["status"] == "completed"
    assert manifest["steps"]["classify"]["verdict"] == "pass"
    assert manifest["caps"]["max_files"] > 0
    # journal round-trips as JSONL with the attempt trail included
    kinds = [json.loads(l)["kind"] for l in zf.read("journal.jsonl").decode().splitlines()]
    assert "step.attempt" in kinds and "run.finished" in kinds
    # the step outputs are the real outputs
    assert b"## Report" in zf.read("steps/report.md")


async def test_package_includes_only_run_window_workspace_files(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    stale = ws / "stale.txt"
    stale.write_text("existed before the run")
    os.utime(stale, (time.time() - 3600, time.time() - 3600))

    engine = _perfect_engine(tmp_path / "journals")
    spec = load_workflow(YAML)
    state = engine.start(spec, context={})
    state = await engine.advance(state.run_id)

    (ws / "made-during-run.txt").write_text("fresh artifact")
    # simulate a runner-recorded root (ScriptedWorker records none)
    state.completed["classify"].workspace_root = str(ws)

    payload = build_package_bytes(spec, state, engine.journal(state.run_id).entries())
    zf = zipfile.ZipFile(io.BytesIO(payload))
    names = zf.namelist()
    ws_members = [n for n in names if n.startswith("workspace/")]
    assert any("made-during-run.txt" in n for n in ws_members)
    assert not any("stale.txt" in n for n in ws_members), \
        "pre-run files must not leak into the package"
    manifest = json.loads(zf.read("manifest.json"))
    assert manifest["steps"]["classify"]["workspace_root"] == str(ws)
    assert any("made-during-run.txt" in f["path"] for f in manifest["workspace_files"])


async def test_package_caps_are_manifested_not_silent(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    engine = _perfect_engine(tmp_path / "journals")
    spec = load_workflow(YAML)
    state = engine.start(spec, context={})
    state = await engine.advance(state.run_id)
    big = ws / "big.bin"
    big.write_bytes(b"x" * 300_000)   # over the per-file cap
    state.completed["classify"].workspace_root = str(ws)

    payload = build_package_bytes(spec, state, engine.journal(state.run_id).entries())
    zf = zipfile.ZipFile(io.BytesIO(payload))
    assert not any("big.bin" in n for n in zf.namelist())
    manifest = json.loads(zf.read("manifest.json"))
    assert any("big.bin" in o["path"] and o["reason"] == "file too large"
               for o in manifest["workspace_omitted"])


@pytest.fixture
def wired_state(tmp_path) -> HarnessState:
    state = HarnessState()
    kp = KeyPair.generate()
    perfect = {t: 1.0 for t in TaskType}
    runner = MockLLMWorker("w-small", Tier.SMALL, keypair=kp, seed=1, skills=perfect)
    state.register_worker(runner, kp, tiers=["small"])
    state.wire({Tier.SMALL: runner}, journal_dir=tmp_path)
    return state


async def test_package_endpoint_over_http(wired_state):
    app = create_app(wired_state)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/runs", json={"workflow_yaml": YAML, "context": {}})
        run_id = resp.json()["run_id"]

        resp = await client.get(f"/api/runs/{run_id}/package")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/zip"
        assert run_id in resp.headers["content-disposition"]
        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        assert "manifest.json" in zf.namelist()

        assert (await client.get("/api/runs/nope/package")).status_code == 404
