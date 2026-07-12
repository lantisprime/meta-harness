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
        assert "blueprint.json" not in zf.namelist()
        manifest = json.loads(zf.read("manifest.json"))
        assert manifest["blueprint_ref"] is None

        assert (await client.get("/api/runs/nope/package")).status_code == 404


async def test_package_includes_blueprint_json_and_exact_ref(tmp_path):
    """A saved-harness run packages the embedded blueprint snapshot and exact
    reference without catalog lookup."""
    from metaharness.blueprints.models import ArtifactRef

    engine = _perfect_engine(tmp_path)
    spec = load_workflow(YAML)
    bp_ref = ArtifactRef(id="pkg-bp", version=1)
    snapshot = {
        "schema_version": 1,
        "name": "Packaged BP",
        "description": "",
        "workflow": {"name": "pkg-wf", "steps": [{"id": "s", "objective": "o"}]},
        "inputs": [],
        "default_context": {},
        "eval_suites": [],
        "id": "pkg-bp",
        "version": 1,
        "published_at": 1.0,
    }
    state = engine.start(
        spec,
        context={},
        blueprint_ref=bp_ref.model_dump(mode="json"),
        blueprint_snapshot=snapshot,
    )
    state = await engine.advance(state.run_id)

    payload = build_package_bytes(spec, state, engine.journal(state.run_id).entries())
    zf = zipfile.ZipFile(io.BytesIO(payload))
    assert "blueprint.json" in zf.namelist()
    assert json.loads(zf.read("blueprint.json")) == snapshot
    manifest = json.loads(zf.read("manifest.json"))
    assert manifest["blueprint_ref"] == {"id": "pkg-bp", "version": 1}
    assert manifest["snapshot_digest"] == state.snapshot_digest


def _valid_bp_snapshot(bp_id: str = "pkg-bp", version: int = 1, name: str = "Packaged BP"):
    return {
        "schema_version": 1,
        "name": name,
        "description": "",
        "workflow": {"name": "pkg-wf", "steps": [{"id": "s", "objective": "o"}]},
        "inputs": [],
        "default_context": {},
        "eval_suites": [],
        "id": bp_id,
        "version": version,
        "published_at": 1.0,
    }


async def test_package_rejects_tampered_snapshot_digest(tmp_path):
    """Packaging fails closed when the run's snapshot digest does not match
    the embedded snapshot."""
    engine = _perfect_engine(tmp_path)
    spec = load_workflow(YAML)
    state = engine.start(spec, context={})
    state = await engine.advance(state.run_id)
    state.snapshot_digest = "0" * 64

    with pytest.raises(ValueError, match="snapshot digest mismatch"):
        build_package_bytes(spec, state, engine.journal(state.run_id).entries())


async def test_package_rejects_one_sided_blueprint_provenance(tmp_path):
    """Packaging refuses a ref without a snapshot or vice-versa."""
    engine = _perfect_engine(tmp_path)
    spec = load_workflow(YAML)
    state = engine.start(spec, context={})
    state = await engine.advance(state.run_id)

    state.blueprint_ref = {"id": "pkg-bp", "version": 1}
    with pytest.raises(ValueError, match="must both be present or both absent"):
        build_package_bytes(spec, state, engine.journal(state.run_id).entries())

    state.blueprint_ref = None
    state.blueprint_snapshot = _valid_bp_snapshot()
    with pytest.raises(ValueError, match="must both be present or both absent"):
        build_package_bytes(spec, state, engine.journal(state.run_id).entries())


async def test_package_rejects_mismatched_or_mutated_blueprint_ref(tmp_path):
    """Packaging validates that ref and snapshot identities match, so an
    in-memory ref mutation cannot伪造 provenance."""
    engine = _perfect_engine(tmp_path)
    spec = load_workflow(YAML)
    snapshot = _valid_bp_snapshot(bp_id="real-bp", version=1, name="Real BP")
    state = engine.start(
        spec,
        context={},
        blueprint_ref={"id": "real-bp", "version": 1},
        blueprint_snapshot=snapshot,
    )
    state = await engine.advance(state.run_id)

    # mismatched id
    state.blueprint_ref = {"id": "forged-bp", "version": 1}
    with pytest.raises(ValueError, match="does not match ref"):
        build_package_bytes(spec, state, engine.journal(state.run_id).entries())

    # mismatched version (mutated ref)
    state.blueprint_ref = {"id": "real-bp", "version": 2}
    with pytest.raises(ValueError, match="does not match ref"):
        build_package_bytes(spec, state, engine.journal(state.run_id).entries())


async def test_package_step_members_are_traversal_safe(tmp_path):
    """Step ids containing path separators, '..' or control chars are refused
    at packaging time rather than escaping the steps/ directory."""
    from metaharness.workflows.package import _safe_step_member

    for bad in ["../etc", "a/b", "a\\b", "..", "a\x00b", "a\x1fb"]:
        with pytest.raises(ValueError, match="unsafe step id"):
            _safe_step_member(bad, "output")
    name, _ = _safe_step_member("safe-id_2.txt", "output")
    assert name == "steps/safe-id_2.txt.md"


async def test_package_skips_external_symlinks_and_out_of_root_paths(tmp_path):
    """Workspace symlinks and paths resolving outside the recorded root are
    omitted from the package, never followed."""
    import os
    import time

    ws = tmp_path / "ws"
    ws.mkdir()
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("secret")
    (ws / "link-to-outside").symlink_to(outside)
    (ws / "normal.txt").write_text("ok")

    engine = _perfect_engine(tmp_path / "journals")
    spec = load_workflow(YAML)
    state = engine.start(spec, context={})
    state = await engine.advance(state.run_id)
    state.completed["classify"].workspace_root = str(ws)

    payload = build_package_bytes(spec, state, engine.journal(state.run_id).entries())
    zf = zipfile.ZipFile(io.BytesIO(payload))
    names = zf.namelist()
    assert any("normal.txt" in n for n in names)
    assert not any("link-to-outside" in n or "outside-secret" in n for n in names)
    manifest = json.loads(zf.read("manifest.json"))
    assert any("symlink refused" in o["reason"] for o in manifest["workspace_omitted"])


async def test_package_allows_workspace_beneath_symlinked_ancestor(tmp_path):
    """A workspace root reached through a symlinked ancestor (like macOS
    /var -> /private/var) is allowed; only symlinks strictly inside the root
    are refused."""
    real_ws = tmp_path / "real_ws"
    real_ws.mkdir()
    link_ws = tmp_path / "link_ws"
    link_ws.symlink_to(real_ws)
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    (real_ws / "real.txt").write_text("ok")
    (real_ws / "internal-link").symlink_to(outside)

    engine = _perfect_engine(tmp_path / "journals")
    spec = load_workflow(YAML)
    state = engine.start(spec, context={})
    state = await engine.advance(state.run_id)
    # record the symlinked path as the workspace root
    state.completed["classify"].workspace_root = str(link_ws)

    payload = build_package_bytes(spec, state, engine.journal(state.run_id).entries())
    zf = zipfile.ZipFile(io.BytesIO(payload))
    names = zf.namelist()
    assert any("real.txt" in n for n in names)
    assert not any("internal-link" in n or "outside.txt" in n for n in names)
    manifest = json.loads(zf.read("manifest.json"))
    assert any("symlink refused" in o["reason"] for o in manifest["workspace_omitted"])
