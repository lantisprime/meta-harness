"""Sandboxed execution checks for ``code_edit`` attempts.

The worker's narration is not evidence that its edit works. When a signed v2
result names a workspace containing a recognized test suite, ``TaskExecutor``
calls this module for post-edit verification. Marked workflow verification
steps also receive the same runner's receipt before their read-only worker runs.

Safety is fail-closed:

* discovery returns one fixed argv from an allowlist; worker text never becomes
  a shell command;
* execution requires an OS sandbox (Seatbelt on macOS, bubblewrap on Linux),
  denies network access, and permits writes only inside the workspace/scratch;
* the environment contains no inherited credentials, output is memory-bounded,
  and the entire process group is killed at the wall-clock deadline;
* if no check or no usable sandbox exists, the caller receives ``None`` and
  falls back to the evidence-fed rubric judge.

The sandbox protects the host boundary; tests still run in the worker's real
workspace so they see the exact artifact being graded.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import shlex
import shutil
import signal
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from itertools import islice
from pathlib import Path
from typing import Awaitable, Callable, Optional

from metaharness.core.types import (
    MASTMode,
    Task,
    TaskType,
    VerificationResult,
    Verdict,
    WorkerResult,
)

DEFAULT_EXECUTION_TIMEOUT_S = 120.0
MAX_CAPTURE_BYTES = 64 * 1024
MAX_MARKER_BYTES = 1024 * 1024


@dataclass(frozen=True)
class ExecutionCheck:
    label: str
    argv: tuple[str, ...]
    marker: str


SandboxBuilder = Callable[
    [tuple[str, ...], Path, Path],
    Optional[tuple[list[str], str]],
]
ExecutionVerifier = Callable[
    [Task, WorkerResult],
    Awaitable[Optional[VerificationResult]],
]


def _read_text(path: Path) -> Optional[str]:
    try:
        if not path.is_file() or path.stat().st_size > MAX_MARKER_BYTES:
            return None
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _pyproject_uses_pytest(path: Path) -> bool:
    text = _read_text(path)
    if text is None:
        return False
    try:
        data = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, ValueError):
        return False
    tool = data.get("tool")
    return isinstance(tool, dict) and isinstance(tool.get("pytest"), dict)


def _has_pytest_files(root: Path) -> bool:
    tests = root / "tests"
    if not tests.is_dir():
        return False
    try:
        return any(
            p.is_file()
            for p in islice(tests.rglob("test_*.py"), 200)
        )
    except OSError:
        return False


def _venv_has_pytest(venv: Path) -> bool:
    scripts_dir = venv / ("Scripts" if os.name == "nt" else "bin")
    if (scripts_dir / ("pytest.exe" if os.name == "nt" else "pytest")).is_file():
        return True
    candidates = [
        venv / "Lib" / "site-packages" / "pytest",
        *venv.glob("lib/python*/site-packages/pytest"),
    ]
    return any(path.is_dir() for path in candidates)


def _python_executable(root: Path) -> Optional[str]:
    venv = root / ".venv"
    candidates = [
        venv / "bin" / "python",
        venv / "Scripts" / "python.exe",
    ]
    if _venv_has_pytest(venv):
        for candidate in candidates:
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
    # Meta-harness's interpreter is a valid fallback only when its environment
    # actually provides pytest. Production installs need not include the dev
    # extra; a missing verifier dependency is infrastructure uncertainty, not a
    # code failure to bank against the worker.
    return sys.executable if importlib.util.find_spec("pytest") is not None else None


def discover_execution_check(workspace_root: str | Path) -> Optional[ExecutionCheck]:
    """Return the strongest recognized test command, never an arbitrary script.

    Python wins deterministic precedence over Node when a polyglot workspace
    exposes both. A generic ``pyproject.toml`` counts only when it configures
    pytest or accompanies actual ``tests/test_*.py`` files.
    """
    try:
        root = Path(workspace_root).expanduser().resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    if not root.is_dir():
        return None

    pytest_marker: Optional[Path] = None
    for name in ("pytest.ini", "tox.ini", "setup.cfg"):
        marker = root / name
        text = _read_text(marker)
        if text is not None and (
            name == "pytest.ini" or "[pytest]" in text or "[tool:pytest]" in text
        ):
            pytest_marker = marker
            break
    pyproject = root / "pyproject.toml"
    if pytest_marker is None and (
        _pyproject_uses_pytest(pyproject)
        or (pyproject.is_file() and _has_pytest_files(root))
    ):
        pytest_marker = pyproject
    if pytest_marker is not None:
        python = _python_executable(root)
        if python is not None:
            return ExecutionCheck(
                label="pytest",
                argv=(python, "-m", "pytest", "-q"),
                marker=pytest_marker.name,
            )

    package_json = root / "package.json"
    text = _read_text(package_json)
    if text is not None:
        try:
            package = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            package = None
        scripts = package.get("scripts") if isinstance(package, dict) else None
        test_script = scripts.get("test") if isinstance(scripts, dict) else None
        npm = shutil.which("npm")
        if (
            npm
            and isinstance(test_script, str)
            and test_script.strip()
            and "error: no test specified" not in test_script.lower()
        ):
            return ExecutionCheck(
                label="npm test",
                argv=(npm, "test", "--silent"),
                marker="package.json#scripts.test",
            )
    return None


def _seatbelt_quote(path: Path | str) -> str:
    return str(path).replace("\\", "\\\\").replace('"', '\\"')


def _seatbelt_command(
    command: tuple[str, ...], workspace: Path, scratch: Path, binary: str
) -> tuple[list[str], str]:
    executable = Path(command[0]).resolve()
    read_roots = [
        workspace,
        scratch,
        Path(sys.prefix),
        Path(sys.base_prefix),
        Path(__file__).resolve().parents[2],  # installed metaharness package root
        executable.parent.parent,
        Path("/System"),
        Path("/usr"),
        Path("/bin"),
        Path("/sbin"),
        Path("/Library"),
        Path("/opt/homebrew"),
        Path("/private/etc"),
        Path("/private/var/db"),
    ]
    seen: set[str] = set()
    read_entries: list[tuple[Path, str]] = []
    for path in read_roots:
        value = str(path)
        if value in seen or not path.exists():
            continue
        seen.add(value)
        rule = f'(subpath "{_seatbelt_quote(path)}")'
        read_entries.append((path, rule))
    write_rules = [
        '(literal "/dev/null")',
        '(literal "/dev/zero")',
        f'(subpath "{_seatbelt_quote(workspace)}")',
        f'(subpath "{_seatbelt_quote(scratch)}")',
    ]
    profile_rules = [
        "(version 1)",
        # Python/Node need a large and OS-version-sensitive set of Mach/sysctl
        # services. Start from the normal runtime policy, then subtract the two
        # capabilities that matter here with filtered deny rules.
        "(allow default)",
        "(deny network*)",
        "(deny file-write* (require-not (require-any "
        + " ".join(write_rules) + ")))",
    ]
    home = Path.home().resolve()
    home_read_rules = [
        rule for path, rule in read_entries
        if path == home or path.is_relative_to(home)
    ]
    if home_read_rules:
        # File names/metadata remain visible for path traversal, but file data
        # elsewhere in HOME (keys, credentials, unrelated repos) is unreadable.
        profile_rules.append(
            f'(deny file-read-data (require-all (subpath "{_seatbelt_quote(home)}") '
            "(require-not (require-any " + " ".join(home_read_rules) + "))))"
        )
    profile = "\n".join(profile_rules)
    return [binary, "-p", profile, "--", *command], "seatbelt"


def _bubblewrap_command(
    command: tuple[str, ...], workspace: Path, scratch: Path, binary: str
) -> tuple[list[str], str]:
    # The host is visible read-only so language runtimes and installed project
    # dependencies remain usable. Only the attested workspace and an empty,
    # credential-free scratch HOME are writable; the network namespace is empty.
    return [
        binary,
        "--die-with-parent",
        "--new-session",
        "--unshare-net",
        "--unshare-pid",
        "--unshare-ipc",
        "--unshare-uts",
        "--ro-bind", "/", "/",
        "--bind", str(workspace), str(workspace),
        "--bind", str(scratch), str(scratch),
        "--dev", "/dev",
        "--proc", "/proc",
        "--chdir", str(workspace),
        "--",
        *command,
    ], "bubblewrap"


def _system_sandbox(
    command: tuple[str, ...], workspace: Path, scratch: Path
) -> Optional[tuple[list[str], str]]:
    if sys.platform == "darwin":
        binary = shutil.which("sandbox-exec")
        return _seatbelt_command(command, workspace, scratch, binary) if binary else None
    if sys.platform.startswith("linux"):
        binary = shutil.which("bwrap")
        return _bubblewrap_command(command, workspace, scratch, binary) if binary else None
    return None


async def _read_bounded(stream: asyncio.StreamReader) -> tuple[bytes, bool]:
    captured = bytearray()
    truncated = False
    while True:
        chunk = await stream.read(8192)
        if not chunk:
            break
        remaining = MAX_CAPTURE_BYTES - len(captured)
        if remaining > 0:
            captured.extend(chunk[:remaining])
        if len(chunk) > max(remaining, 0):
            truncated = True
    return bytes(captured), truncated


def _kill_process_group(proc: asyncio.subprocess.Process) -> None:
    """Kill the runner and descendants that kept its process group.

    This is called on timeout *and* after a normal parent exit: a test can pass
    while leaving a background child holding stdout/stderr open, which must not
    keep verification alive indefinitely.
    """
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        if proc.returncode is None:
            proc.kill()


def _clean_environment(scratch: Path, command: tuple[str, ...]) -> dict[str, str]:
    executable = Path(command[0])
    path_candidates = [
        executable.parent,
        executable.resolve().parent,
        Path("/usr/local/bin"),
        Path("/opt/homebrew/bin"),
        Path("/usr/bin"),
        Path("/bin"),
        Path("/usr/sbin"),
        Path("/sbin"),
    ]
    seen: set[str] = set()
    safe_parts: list[str] = []
    for path in path_candidates:
        value = str(path)
        if value not in seen:
            seen.add(value)
            safe_parts.append(value)
    safe_path = os.pathsep.join(safe_parts)
    return {
        "PATH": safe_path,
        "HOME": str(scratch),
        "TMPDIR": str(scratch),
        "CI": "1",
        "NO_COLOR": "1",
        "TERM": "dumb",
        "PYTHONHASHSEED": "0",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
    }


def _sandbox_failed_to_start(backend: str, text: str) -> bool:
    lowered = text.lower()
    return (
        (backend == "seatbelt" and "sandbox-exec:" in lowered)
        or (backend == "bubblewrap" and ("bwrap:" in lowered or "bubblewrap:" in lowered))
    )


async def run_workspace_execution(
    workspace_root: str | Path,
    *,
    timeout_s: float = DEFAULT_EXECUTION_TIMEOUT_S,
    sandbox_builder: Optional[SandboxBuilder] = None,
) -> Optional[VerificationResult]:
    """Run the recognized workspace check and return its sandboxed receipt.

    Discovery owns the command; no worker-supplied text reaches the process
    launcher. ``None`` means no safe execution signal exists.
    """
    check = discover_execution_check(workspace_root)
    if check is None:
        return None
    try:
        workspace = Path(workspace_root).expanduser().resolve(strict=True)
    except (OSError, RuntimeError):
        return None

    builder = sandbox_builder or _system_sandbox
    with tempfile.TemporaryDirectory(prefix="metaharness-verify-") as temp_dir:
        scratch = Path(temp_dir).resolve()
        wrapped = builder(check.argv, workspace, scratch)
        if wrapped is None:
            return None
        argv, backend = wrapped
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=workspace,
                env=_clean_environment(scratch, check.argv),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
        except OSError:
            return None

        assert proc.stdout is not None and proc.stderr is not None
        stdout_task = asyncio.create_task(_read_bounded(proc.stdout))
        stderr_task = asyncio.create_task(_read_bounded(proc.stderr))
        timed_out = False
        deadline = asyncio.get_running_loop().time() + timeout_s
        # Poll returncode rather than awaiting Process.wait(): asyncio's wait
        # future can remain pending after the group leader exits when a
        # background descendant inherited stdout/stderr.
        while proc.returncode is None:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                timed_out = True
                break
            await asyncio.sleep(min(0.05, remaining))
        if timed_out:
            _kill_process_group(proc)
            await proc.wait()
        else:
            # The group leader has exited; kill any background descendants
            # before draining so inherited pipe descriptors cannot hang us.
            _kill_process_group(proc)
        try:
            stdout, stderr = await asyncio.wait_for(
                asyncio.gather(stdout_task, stderr_task), timeout=2.0
            )
        except asyncio.TimeoutError:
            stdout_task.cancel()
            stderr_task.cancel()
            await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
            stdout, stderr = (b"", True), (b"", True)
        stdout_b, stdout_truncated = stdout
        stderr_b, stderr_truncated = stderr

    text = "\n".join(
        part for part in (
            stdout_b.decode(errors="replace").strip(),
            stderr_b.decode(errors="replace").strip(),
        ) if part
    )
    if stdout_truncated or stderr_truncated:
        text += "\n[verification output truncated]"
    command = shlex.join(check.argv)
    if timed_out:
        return VerificationResult(
            verdict=Verdict.FAIL,
            score=0.0,
            detail=(f"command: {command}\nstatus: timed out\n"
                    f"{check.label} exceeded the {timeout_s:g}s execution timeout"),
            failure_mode=MASTMode.TIMEOUT,
            scorer="execution",
        )
    if proc.returncode == 0:
        tail = text[-2000:] if text else "no test output"
        return VerificationResult(
            verdict=Verdict.PASS,
            score=1.0,
            detail=(f"command: {command}\nstatus: passed\n"
                    f"sandbox: {backend} ({check.marker})\noutput:\n{tail}"),
            scorer="execution",
        )
    if _sandbox_failed_to_start(backend, text):
        return None
    tail = text[-2000:] if text else "no test output"
    return VerificationResult(
        verdict=Verdict.FAIL,
        score=0.0,
        detail=(f"command: {command}\nstatus: failed (exit {proc.returncode})\n"
                f"output:\n{tail}"),
        failure_mode=MASTMode.DISOBEY_TASK_SPEC,
        scorer="execution",
    )


async def verify_code_edit_execution(
    task: Task,
    result: WorkerResult,
    *,
    timeout_s: float = DEFAULT_EXECUTION_TIMEOUT_S,
    sandbox_builder: Optional[SandboxBuilder] = None,
) -> Optional[VerificationResult]:
    """Verify an attested code-edit result with the safe workspace runner."""
    if task.task_type != TaskType.CODE_EDIT or result.error or not result.workspace_root:
        return None
    return await run_workspace_execution(
        result.workspace_root,
        timeout_s=timeout_s,
        sandbox_builder=sandbox_builder,
    )
