from __future__ import annotations

import asyncio
import io
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

import pytest

from metaharness.blueprints.models import BlueprintVersion
from metaharness.config import HarnessConfig, MCPServerConfig
from metaharness.portable import build_portable_package
from metaharness.portable import cli as portable_cli
from metaharness.portable import runtime as portable_runtime
from metaharness.portable.launchers import launcher_descriptor
from metaharness.tools import ToolSpec, mcp_config_fingerprint


def _run(argv: list[str], *, cwd: Path | None = None, input_text: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "metaharness.cli", *argv],
        capture_output=True,
        text=True,
        cwd=cwd,
        input=input_text,
    )


def _blueprint_with_hitl() -> dict:
    return {
        "id": "runtime-demo",
        "version": 1,
        "published_at": 1.0,
        "name": "Runtime demo",
        "workflow": {
            "name": "runtime-flow",
            "steps": [
                {
                    "id": "confirm",
                    "objective": "Confirm the operation.",
                    "task_type": "general",
                    "hitl": True,
                    "hitl_timing": "before",
                    "success_check": {"equals": "go"},
                }
            ],
        },
    }


def test_subprocess_run_stops_approves_resumes_and_completes(tmp_path: Path) -> None:
    """A generated launcher argv must run, stop at HITL, resume, and complete."""
    workspace = tmp_path / "work spaces" / "团队"
    journal_dir = tmp_path / "journal dir" / "日志"
    workspace.mkdir(parents=True)
    journal_dir.mkdir(parents=True)

    harness_file = tmp_path / "harness demo.json"
    harness_file.write_text(json.dumps(_blueprint_with_hitl()), encoding="utf-8")

    context_file = tmp_path / "context with spaces.json"
    context_file.write_text(json.dumps({}), encoding="utf-8")

    run_argv = [
        "blueprint", "run", str(harness_file),
        "--context-file", str(context_file),
        "--workspace", str(workspace),
        "--journal-dir", str(journal_dir),
        "--shim",
    ]

    run_result = _run(run_argv)
    assert run_result.returncode == 20, f"stdout={run_result.stdout}\nstderr={run_result.stderr}"

    events = [json.loads(line) for line in run_result.stdout.splitlines() if line.strip().startswith("{")]
    assert events[0]["kind"] == "run.started"
    assert any(e["kind"] == "approval.required" and e["step_id"] == "confirm" for e in events)

    run_summary = _last_json_line(run_result.stderr)
    run_id = run_summary["run_id"]
    assert run_summary["status"] == "awaiting_approval"
    assert run_summary["awaiting_step"] == "confirm"

    approve_result = _run([
        "run", "approve", run_id, "confirm",
        "--journal-dir", str(journal_dir),
        "--workspace", str(workspace),
        "--shim",
    ])
    assert approve_result.returncode == 0
    assert json.loads(approve_result.stdout)["approved"] is True

    resume_result = _run([
        "run", "resume", run_id,
        "--journal-dir", str(journal_dir),
        "--workspace", str(workspace),
        "--shim",
    ])
    assert resume_result.returncode == 0, f"stdout={resume_result.stdout}\nstderr={resume_result.stderr}"
    assert _last_json_line(resume_result.stderr)["status"] == "completed"

    inspect_result = _run([
        "run", "inspect", run_id,
        "--journal-dir", str(journal_dir),
        "--workspace", str(workspace),
        "--shim",
    ])
    assert inspect_result.returncode == 0
    inspected = json.loads(inspect_result.stdout)
    assert inspected["status"] == "completed"
    assert inspected["failed_step"] is None
    assert any(e["kind"] == "step.completed" and e["step_id"] == "confirm" for e in inspected["events"])


def test_portable_exact_runs_resolve_context_into_distinct_snapshots(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    monkeypatch.setattr(portable_runtime, "CONFIG_PATH", tmp_path / "missing-config.json")
    document = _service_blueprint().model_dump(mode="json")
    document["inputs"] = [{
        "name": "goal", "schema": {"type": "string"}, "required": True,
    }]
    document["workflow"]["steps"][0].update({
        "objective": "Return this goal: $context.goal",
        "inputs": {"goal": "$context.goal"},
        "success_check": None,
    })
    harness = tmp_path / "resolved.json"
    harness.write_text(json.dumps(document), encoding="utf-8")
    workspace = tmp_path / "workspace"
    journals = tmp_path / "journals"
    workspace.mkdir()
    journals.mkdir()

    started = []
    for index, goal in enumerate(("alpha", "beta")):
        context = tmp_path / f"context-{index}.json"
        context.write_text(json.dumps({"goal": goal}), encoding="utf-8")
        result = portable_cli.run_blueprint(
            harness, context_file=str(context), workspace=workspace,
            journal_dir=journals, approval="stop", shim=True,
        )
        capsys.readouterr()
        raw = (journals / f"{result['run_id']}.jsonl").read_text().splitlines()[0]
        started.append(json.loads(raw))
    assert started[0]["snapshot_digest"] != started[1]["snapshot_digest"]
    for goal, event in zip(("alpha", "beta"), started):
        workflow = event["payload"]["workflow"]
        assert workflow["steps"][0]["inputs"]["goal"] == goal
        assert goal in workflow["steps"][0]["objective"]
        assert "$context.goal" not in json.dumps(workflow)


def test_portable_state_hydrates_configured_secret_binding(
    tmp_path: Path, monkeypatch,
) -> None:
    import metaharness.config as config_mod

    monkeypatch.setattr(config_mod, "SALT_PATH", tmp_path / ".salt")
    config_path = tmp_path / "config.json"
    config = HarnessConfig()
    config.set_secret_binding("service-token", "sk-live-runtime-value")
    config.save(config_path)
    state = portable_runtime.build_portable_state(
        journal_dir=tmp_path / "journals", workspace_root=tmp_path / "workspace",
        config_path=config_path, shim=True,
    )
    blueprint = BlueprintVersion.model_validate({
        "id": "secret-runtime", "version": 1, "published_at": 1.0,
        "name": "Secret runtime",
        "inputs": [{
            "name": "token", "schema": {"type": "string"}, "secret": True,
            "required": True, "default": {"binding": "service-token"},
        }],
        "workflow": {"name": "secret", "steps": [{
            "id": "use", "objective": "Use binding.",
            "inputs": {"token": {"binding": "service-token"}},
        }]},
    })
    readiness, workflow = asyncio.run(
        portable_runtime.prepare_portable_blueprint_run(state, blueprint, {})
    )
    assert readiness.ready and workflow is not None
    assert readiness.normalized_context == {}


def test_portable_readiness_loads_and_fingerprints_configured_mcp(
    tmp_path: Path, monkeypatch,
) -> None:
    state = portable_runtime.build_portable_state(
        journal_dir=tmp_path / "journals", workspace_root=tmp_path / "workspace",
        config_path=tmp_path / "missing.json", shim=True,
    )
    server = MCPServerConfig(
        name="search", transport="stdio", command="unused", enabled=True,
    )
    state.config.mcp_servers["search"] = server

    async def fake_load(registry, _config):
        registry.register(ToolSpec(
            name="search.query", description="search", input_schema={},
            handler=lambda: "ok", source="mcp:search",
        ))
        return {"search": {
            "ok": True, "status": "loaded", "tools": 1,
            "fingerprint": mcp_config_fingerprint(server),
        }}

    monkeypatch.setattr(portable_runtime, "load_mcp_tools", fake_load)
    blueprint = BlueprintVersion.model_validate({
        "id": "mcp-runtime", "version": 1, "published_at": 1.0,
        "name": "MCP runtime",
        "workflow": {"name": "mcp", "steps": [{
            "id": "search", "objective": "Search.", "tools": ["search.query"],
        }]},
    })
    readiness, workflow = asyncio.run(
        portable_runtime.prepare_portable_blueprint_run(state, blueprint, {})
    )
    assert readiness.ready and workflow is not None
    assert state.mcp_load_status["search"]["fingerprint"] == mcp_config_fingerprint(server)


def _service_blueprint() -> BlueprintVersion:
    return BlueprintVersion.model_validate({
        "id": "service-demo",
        "version": 1,
        "published_at": 1.0,
        "name": "Service demo",
        "workflow": {
            "name": "service-flow",
            "steps": [{
                "id": "answer",
                "objective": "Return go.",
                "success_check": {"equals": "go"},
            }],
        },
    })


def _test_environment(tmp_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["HOME"] = str(tmp_path / "isolated home")
    env["METAHARNESS_SHIM_WORKERS"] = "1"
    return env


def _request_json(url: str, payload: dict | None = None) -> dict:
    if payload is None:
        with urllib.request.urlopen(url, timeout=2) as response:
            return json.loads(response.read())
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read())


def _last_json_line(text: str) -> dict:
    for line in reversed(text.splitlines()):
        if line.strip().startswith("{"):
            return json.loads(line)
    raise AssertionError(f"no JSON object line found in: {text!r}")


def test_generated_launcher_argv_runs_unicode_package_and_workspace(tmp_path: Path) -> None:
    package = tmp_path / "packages" / "团队 harness"
    package.mkdir(parents=True)
    payload = build_portable_package(_service_blueprint(), targets=["codex"])
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        archive.extractall(package)
    workspace = tmp_path / "work spaces" / "Δ"
    workspace.mkdir(parents=True)
    descriptor = launcher_descriptor(
        "codex",
        blueprint_path="packages/团队 harness",
        workspace_path="work spaces/Δ",
    )
    argv = descriptor["process"]["argv"]
    env = _test_environment(tmp_path)
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    result = subprocess.run(
        argv,
        cwd=tmp_path,
        env=env,
        input="{}",
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert _last_json_line(result.stderr)["status"] == "completed"


def test_test_shim_environment_does_not_change_production_default(tmp_path: Path) -> None:
    blueprint = tmp_path / "harness.json"
    blueprint.write_text(
        json.dumps(_service_blueprint().model_dump(mode="json")), encoding="utf-8"
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    env = _test_environment(tmp_path)
    env.pop("METAHARNESS_SHIM_WORKERS")
    result = subprocess.run(
        [
            sys.executable, "-m", "metaharness.cli", "blueprint", "run",
            str(blueprint), "--context-file", "-", "--workspace", str(workspace),
        ],
        env=env,
        input="{}",
        text=True,
        capture_output=True,
    )
    assert result.returncode == 2
    assert "requires at least one enabled configured agent" in result.stdout


def test_package_service_healthcheck_and_integrity_failure(tmp_path: Path) -> None:
    package = tmp_path / "portable 服务.zip"
    package.write_bytes(build_portable_package(_service_blueprint()))
    workspace = tmp_path / "workspace 路径"
    journal = tmp_path / "journal 路径"
    workspace.mkdir()
    journal.mkdir()
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    env = _test_environment(tmp_path)
    process = subprocess.Popen(
        [
            sys.executable, "-m", "metaharness.cli", "serve",
            "--package", str(package),
            "--package-workspace", str(workspace),
            "--package-journal-dir", str(journal),
            "--host", "127.0.0.1", "--port", str(port),
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    base_url = f"http://127.0.0.1:{port}"
    url = f"{base_url}/health"
    try:
        health = None
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if process.poll() is not None:
                break
            try:
                with urllib.request.urlopen(url, timeout=0.2) as response:
                    health = json.loads(response.read())
                break
            except OSError:
                time.sleep(0.05)
        assert health is not None, process.stderr.read() if process.poll() is not None else ""
        assert health["status"] == "healthy"
        assert health["workspace"] == str(workspace)
        checked = subprocess.run(
            [sys.executable, "-m", "metaharness.cli", "healthcheck", "--url", url],
            capture_output=True,
            text=True,
        )
        assert checked.returncode == 0
        assert json.loads(checked.stdout)["status"] == "healthy"

        run = _request_json(f"{base_url}/api/runs", {"context": {}, "wait": True})
        assert run["status"] == "completed"
        assert run["blueprint_ref"] == {"id": "service-demo", "version": 1}
        run_id = run["run_id"]
        detail = _request_json(f"{base_url}/api/runs/{run_id}")
        assert detail["state"]["status"] == "completed"
        assert any(
            event["kind"] == "step.completed" and event["step_id"] == "answer"
            for event in detail["events"]
        )
        journal_path = journal / f"{run_id}.jsonl"
        assert journal_path.is_file()
        journal_text = journal_path.read_text(encoding="utf-8")
        assert "run.completed" in journal_text
        sidecar = json.loads((journal / "runs" / f"{run_id}.json").read_text(encoding="utf-8"))
        assert sidecar["workspace_root"] == str(workspace.resolve())
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    tampered = tmp_path / "tampered.zip"
    with zipfile.ZipFile(package) as source, zipfile.ZipFile(tampered, "w") as target:
        for info in source.infolist():
            data = source.read(info.filename)
            if info.filename == "harness.json":
                data += b" "
            target.writestr(info, data)
    rejected = subprocess.run(
        [
            sys.executable, "-m", "metaharness.cli", "serve",
            "--package", str(tampered), "--port", str(port),
        ],
        env=env,
        capture_output=True,
        text=True,
    )
    assert rejected.returncode == 2
    assert "digest mismatch" in rejected.stderr


def test_package_service_run_pauses_for_hitl_and_resumes(tmp_path: Path) -> None:
    package = tmp_path / "hitl-package.zip"
    package.write_bytes(
        build_portable_package(BlueprintVersion.model_validate(_blueprint_with_hitl()))
    )
    workspace = tmp_path / "service workspace"
    journal = tmp_path / "service journal"
    workspace.mkdir()
    journal.mkdir()
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    env = _test_environment(tmp_path)
    process = subprocess.Popen(
        [
            sys.executable, "-m", "metaharness.cli", "serve",
            "--package", str(package),
            "--package-workspace", str(workspace),
            "--package-journal-dir", str(journal),
            "--host", "127.0.0.1", "--port", str(port),
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if process.poll() is not None:
                break
            try:
                if _request_json(f"{base_url}/health")["status"] == "healthy":
                    break
            except OSError:
                time.sleep(0.05)
        else:
            raise AssertionError("package service did not become healthy")
        assert process.poll() is None, process.stderr.read()

        paused = _request_json(f"{base_url}/api/runs", {"context": {}, "wait": True})
        assert paused["status"] == "awaiting_approval"
        assert paused["awaiting"] == "confirm"
        run_id = paused["run_id"]

        detail = _request_json(f"{base_url}/api/runs/{run_id}")
        assert detail["state"]["status"] == "awaiting_approval"
        assert any(
            event["kind"] == "approval.required" and event["step_id"] == "confirm"
            for event in detail["events"]
        )

        resumed = _request_json(
            f"{base_url}/api/runs/{run_id}/approval",
            {"step_id": "confirm", "approved": True, "wait": True},
        )
        assert resumed["status"] == "completed"
        journal_path = journal / f"{run_id}.jsonl"
        assert journal_path.is_file()
        journal_text = journal_path.read_text(encoding="utf-8")
        assert "approval.resolved" in journal_text
        assert "run.completed" in journal_text
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
