"""CodingAgentWorker: headless CLI invocation via stub scripts."""
from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from metaharness.core.types import Task, TaskType, Tier
from metaharness.harness import CLI_ADAPTERS, CodingAgentWorker, available_clis
from metaharness.identity import KeyPair


def _stub(tmp_path: Path, name: str, script: str) -> str:
    path = tmp_path / name
    path.write_text(f"#!/bin/sh\n{script}\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


def _task(**kw) -> Task:
    defaults = dict(id="t1", task_type=TaskType.CODE_EDIT, objective="write hello.py")
    defaults.update(kw)
    return Task(**defaults)


async def test_claude_adapter_parses_json_result(tmp_path):
    binary = _stub(tmp_path, "claude",
                   """cat > /dev/null; echo '{"result": "created hello.py", "total_cost_usd": 0.02}'""")
    worker = CodingAgentWorker("cc", cli="claude", keypair=KeyPair.generate(),
                               workspace=tmp_path / "ws", binary=binary)
    result = await worker.run(_task())
    assert result.error is None
    assert result.output == "created hello.py"
    assert result.cost_usd == pytest.approx(0.02)
    assert result.signature_b64  # BaseRunner signed it


async def test_prompt_arrives_on_stdin_and_cwd_is_workspace(tmp_path):
    ws = tmp_path / "ws"
    binary = _stub(tmp_path, "claude", 'cat > prompt.txt; pwd; echo "{}"')
    worker = CodingAgentWorker("cc", cli="claude", workspace=ws, binary=binary)
    task = _task(objective="fix the bug",
                 boundaries=["do not touch tests"],
                 inputs={"file": "a.py", "_hidden": "nope"})
    result = await worker.run(task)
    assert result.error is None
    prompt = (ws / "prompt.txt").read_text()
    assert "fix the bug" in prompt and "do not touch tests" in prompt
    assert "a.py" in prompt and "nope" not in prompt  # _-prefixed inputs hidden


async def test_nonzero_exit_is_loud_error(tmp_path):
    binary = _stub(tmp_path, "codex", 'echo "boom" >&2; exit 3')
    worker = CodingAgentWorker("cx", cli="codex", workspace=tmp_path / "ws",
                               binary=binary)
    result = await worker.run(_task())
    assert result.error is not None
    assert "exit 3" in result.error and "boom" in result.error


async def test_missing_binary_is_loud_error(tmp_path):
    worker = CodingAgentWorker("px", cli="pi", workspace=tmp_path / "ws",
                               binary=str(tmp_path / "definitely-not-here"))
    result = await worker.run(_task())
    assert result.error is not None and "cannot launch" in result.error


async def test_timeout_kills_process(tmp_path):
    binary = _stub(tmp_path, "opencode", "sleep 30")
    worker = CodingAgentWorker("oc", cli="opencode", workspace=tmp_path / "ws",
                               binary=binary, timeout_s=0.5)
    result = await worker.run(_task())
    assert result.error is not None and "timed out" in result.error


async def test_pi_jsonl_parsing(tmp_path):
    events = "\n".join([
        json.dumps({"type": "session", "id": "s1"}),
        json.dumps({"message": {"role": "assistant",
                                "content": [{"type": "text", "text": "done: wrote 2 files"}]}}),
    ])
    binary = _stub(tmp_path, "pi", f"cat > /dev/null; cat <<'EOF'\n{events}\nEOF")
    worker = CodingAgentWorker("pi1", cli="pi", workspace=tmp_path / "ws",
                               binary=binary)
    result = await worker.run(_task())
    assert result.error is None
    assert result.output == "done: wrote 2 files"


async def test_explicit_task_workspace_wins(tmp_path):
    ws_default = tmp_path / "default"
    ws_explicit = tmp_path / "explicit"
    binary = _stub(tmp_path, "claude", 'cat > /dev/null; pwd')
    worker = CodingAgentWorker("cc", cli="claude", workspace=ws_default,
                               binary=binary)
    result = await worker.run(_task(inputs={"_workspace": str(ws_explicit)}))
    assert result.error is None
    assert str(ws_explicit.resolve()) in str(result.output)


def test_unknown_cli_rejected():
    with pytest.raises(ValueError, match="unknown coding CLI"):
        CodingAgentWorker("w", cli="vim")


def test_adapters_cover_expected_clis():
    assert set(CLI_ADAPTERS) == {"pi", "codex", "opencode", "claude"}
    assert isinstance(available_clis(), dict)
