"""Issue #1: sandboxed execution verification for code_edit workspaces."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time

from metaharness.core.types import Task, TaskType, Tier, Verdict, WorkerResult
from metaharness.evals.execution import (
    MAX_CAPTURE_BYTES,
    ExecutionCheck,
    _bubblewrap_command,
    _read_bounded,
    _seatbelt_command,
    discover_execution_check,
    run_workspace_execution,
    verify_code_edit_execution,
)


def _result(task: Task, workspace) -> WorkerResult:
    return WorkerResult(
        task_id=task.id,
        worker_id="w",
        tier=Tier.SMALL,
        model="m",
        output="done",
        workspace_root=str(workspace),
    )


def _direct_sandbox(command, workspace, scratch):
    """Unit-test adapter: production always uses Seatbelt/bubblewrap."""
    return list(command), "test-isolation"


def test_discovery_prefers_pytest_over_npm(tmp_path, monkeypatch):
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    (tmp_path / "package.json").write_text(json.dumps({
        "scripts": {"test": "node test.js"},
    }))
    monkeypatch.setattr("metaharness.evals.execution.shutil.which",
                        lambda name: "/usr/bin/npm" if name == "npm" else None)

    check = discover_execution_check(tmp_path)

    assert check is not None
    assert check.label == "pytest"
    assert check.argv[-3:] == ("-m", "pytest", "-q")


def test_pyproject_needs_pytest_config_or_real_test_files(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n")
    assert discover_execution_check(tmp_path) is None

    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_demo.py").write_text("def test_ok(): pass\n")
    assert discover_execution_check(tmp_path).label == "pytest"


def test_missing_pytest_runtime_is_not_a_runnable_check(tmp_path, monkeypatch):
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    monkeypatch.setattr("metaharness.evals.execution._python_executable",
                        lambda root: None)

    assert discover_execution_check(tmp_path) is None


def test_npm_discovery_rejects_default_placeholder(tmp_path, monkeypatch):
    monkeypatch.setattr("metaharness.evals.execution.shutil.which",
                        lambda name: "/usr/bin/npm" if name == "npm" else None)
    package = tmp_path / "package.json"
    package.write_text(json.dumps({
        "scripts": {"test": "echo \"Error: no test specified\" && exit 1"},
    }))
    assert discover_execution_check(tmp_path) is None

    package.write_text(json.dumps({"scripts": {"test": "node test.js"}}))
    check = discover_execution_check(tmp_path)
    assert check is not None
    assert check.label == "npm test"
    assert check.argv == ("/usr/bin/npm", "test", "--silent")


def test_os_sandbox_commands_pin_network_and_write_boundaries(tmp_path):
    workspace = tmp_path / "workspace"
    scratch = tmp_path / "scratch"
    workspace.mkdir()
    scratch.mkdir()
    command = ("/usr/bin/python3", "-V")

    seatbelt, seatbelt_name = _seatbelt_command(
        command, workspace, scratch, "/usr/bin/sandbox-exec"
    )
    profile = seatbelt[2]
    assert seatbelt_name == "seatbelt"
    assert "(deny network*)" in profile
    assert "(deny file-write*" in profile
    assert f'(subpath "{workspace}")' in profile
    assert f'(subpath "{scratch}")' in profile
    assert seatbelt[-2:] == list(command)

    bubblewrap, bubblewrap_name = _bubblewrap_command(
        command, workspace, scratch, "/usr/bin/bwrap"
    )
    assert bubblewrap_name == "bubblewrap"
    assert "--unshare-net" in bubblewrap
    assert ["--ro-bind", "/", "/"] == bubblewrap[7:10]
    assert ["--bind", str(workspace), str(workspace)] in [
        bubblewrap[i:i + 3] for i in range(len(bubblewrap) - 2)
    ]


async def test_output_reader_drains_but_caps_memory():
    reader = asyncio.StreamReader()
    reader.feed_data(b"x" * (MAX_CAPTURE_BYTES + 10_000))
    reader.feed_eof()

    captured, truncated = await _read_bounded(reader)

    assert len(captured) == MAX_CAPTURE_BYTES
    assert truncated is True


async def test_passing_runner_cannot_leave_pipe_holding_child(
    tmp_path, monkeypatch
):
    command = (
        sys.executable,
        "-c",
        "import subprocess,sys; "
        "subprocess.Popen([sys.executable,'-c','import time; time.sleep(10)'])",
    )
    monkeypatch.setattr(
        "metaharness.evals.execution.discover_execution_check",
        lambda root: ExecutionCheck(label="background probe", argv=command, marker="test"),
    )
    task = Task(task_type=TaskType.CODE_EDIT, objective="fix it")
    started = time.monotonic()

    verification = await verify_code_edit_execution(
        task,
        _result(task, tmp_path),
        timeout_s=1.0,
        sandbox_builder=_direct_sandbox,
    )

    assert verification is not None and verification.verdict is Verdict.PASS
    assert time.monotonic() - started < 1.0


async def test_pytest_exit_zero_is_execution_pass_and_env_is_scrubbed(
    tmp_path, monkeypatch
):
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    (tmp_path / "test_demo.py").write_text(
        "import os\n"
        "def test_clean_environment():\n"
        "    assert 'METAHARNESS_TEST_SECRET' not in os.environ\n"
        "    assert 'attacker-bin' not in os.environ['PATH']\n"
    )
    monkeypatch.setenv("METAHARNESS_TEST_SECRET", "do-not-forward")
    monkeypatch.setenv("PATH", "/tmp/attacker-bin:" + os.environ["PATH"])
    task = Task(task_type=TaskType.CODE_EDIT, objective="fix it")

    verification = await verify_code_edit_execution(
        task, _result(task, tmp_path), sandbox_builder=_direct_sandbox,
    )

    assert verification is not None
    assert verification.verdict is Verdict.PASS
    assert verification.scorer == "execution"
    assert "pytest" in verification.detail
    assert "1 passed" in verification.detail


async def test_workspace_execution_reuses_safe_runner_without_code_edit_task(tmp_path):
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    (tmp_path / "test_demo.py").write_text("def test_ok(): pass\n")

    verification = await run_workspace_execution(
        tmp_path, sandbox_builder=_direct_sandbox,
    )

    assert verification is not None
    assert verification.verdict is Verdict.PASS
    assert "command:" in verification.detail
    assert "1 passed" in verification.detail


async def test_pytest_nonzero_is_execution_fail_with_diagnostic(tmp_path):
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    (tmp_path / "test_demo.py").write_text(
        "def test_broken():\n    assert 1 == 2, 'real regression'\n"
    )
    task = Task(task_type=TaskType.CODE_EDIT, objective="fix it")

    verification = await verify_code_edit_execution(
        task, _result(task, tmp_path), sandbox_builder=_direct_sandbox,
    )

    assert verification is not None
    assert verification.verdict is Verdict.FAIL
    assert verification.scorer == "execution"
    assert "real regression" in verification.detail


async def test_execution_timeout_kills_suite_and_is_structured(tmp_path):
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    (tmp_path / "test_demo.py").write_text(
        "import time\n"
        "def test_slow():\n    time.sleep(10)\n"
    )
    task = Task(task_type=TaskType.CODE_EDIT, objective="fix it")

    verification = await verify_code_edit_execution(
        task,
        _result(task, tmp_path),
        timeout_s=0.1,
        sandbox_builder=_direct_sandbox,
    )

    assert verification is not None
    assert verification.verdict is Verdict.FAIL
    assert verification.failure_mode.value == "timeout"
    assert "0.1s" in verification.detail


async def test_missing_sandbox_falls_back_instead_of_false_blame(tmp_path):
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    (tmp_path / "test_demo.py").write_text("def test_ok(): pass\n")
    task = Task(task_type=TaskType.CODE_EDIT, objective="fix it")

    verification = await verify_code_edit_execution(
        task,
        _result(task, tmp_path),
        sandbox_builder=lambda command, workspace, scratch: None,
    )

    assert verification is None


async def test_non_code_task_never_executes_workspace(tmp_path):
    marker = tmp_path / "ran"
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    (tmp_path / "test_demo.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('yes')\n"
    )
    task = Task(task_type=TaskType.GENERAL, objective="summarize")

    verification = await verify_code_edit_execution(
        task, _result(task, tmp_path), sandbox_builder=_direct_sandbox,
    )

    assert verification is None
    assert not marker.exists()
