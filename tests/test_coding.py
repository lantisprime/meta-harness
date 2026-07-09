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


def test_display_model_placeholder_never_reaches_argv(tmp_path):
    """Regression: empty model -> display name 'codex-cli' leaked into the
    invocation as `-m codex-cli`, which Codex rejects on ChatGPT accounts.
    No override means NO model flag; the CLI uses its own default."""
    ws = tmp_path / "ws"
    for cli, flag in (("codex", "-m"), ("claude", "--model"),
                      ("pi", "--model"), ("opencode", "-m")):
        bare = CodingAgentWorker(f"w-{cli}", cli=cli)
        argv, _ = CLI_ADAPTERS[cli].build(bare, "prompt", ws)
        assert flag not in argv, f"{cli}: placeholder model leaked into argv"
        assert bare.model == f"{cli}-cli"  # display/matrix name still set

        pinned = CodingAgentWorker(f"w2-{cli}", cli=cli, model="real-model-id")
        argv2, _ = CLI_ADAPTERS[cli].build(pinned, "prompt", ws)
        assert argv2[argv2.index(flag) + 1] == "real-model-id"


def test_pi_config_models_reads_registry(tmp_path):
    from metaharness.harness.coding import pi_config_models

    registry = tmp_path / "models.json"
    registry.write_text(json.dumps({"providers": {
        "neuralwatt": {"baseUrl": "https://api.neuralwatt.com/v1",
                       "models": [{"id": "qwen3.5-397b"}, {"id": "glm-5.2"}]},
        "empty": {"models": []},
    }}))
    assert pi_config_models(registry) == [
        "neuralwatt/qwen3.5-397b", "neuralwatt/glm-5.2"]
    assert pi_config_models(tmp_path / "missing.json") == []


def test_opencode_config_models_tolerates_jsonc(tmp_path):
    from metaharness.harness.coding import opencode_config_models

    cfg = tmp_path / "opencode.jsonc"
    cfg.write_text("""{
  // comment line survives stripping
  "provider": {
    "lmstudio": {"models": {"qwen3.6-35b-a3b": {"name": "Qwen"},}},
    "openrouter": {"models": {"z-ai/glm-5.1": {}}}
  },
}""")
    models = opencode_config_models([cfg])
    assert "lmstudio/qwen3.6-35b-a3b" in models
    assert "openrouter/z-ai/glm-5.1" in models
    assert opencode_config_models([tmp_path / "nope.json"]) == []


async def test_list_cli_models_merges_config_registry(tmp_path, monkeypatch):
    import metaharness.harness.coding as coding_mod
    from metaharness.harness.coding import list_cli_models

    registry = tmp_path / "models.json"
    registry.write_text(json.dumps({"providers": {
        "neuralwatt": {"models": [{"id": "glm-5.2"}]}}}))
    monkeypatch.setitem(coding_mod._CLI_CONFIG_MODELS, "pi",
                        lambda: coding_mod.pi_config_models(registry))
    monkeypatch.setattr(coding_mod.shutil, "which", lambda b: None)  # no binary
    models = await list_cli_models("pi")
    assert models == ["neuralwatt/glm-5.2"]  # config registry alone suffices


async def test_workspace_root_stamped_on_cli_result(tmp_path):
    """v0.4 root binding: coding CLIs record the exact cwd their subprocess
    mutated — per-task dirs and explicit _workspace differ from builtin roots."""
    ws = tmp_path / "ws"
    binary = _stub(tmp_path, "claude", 'cat > /dev/null; echo \'{"result": "ok"}\'')
    worker = CodingAgentWorker("cw", cli="claude", workspace=ws, binary=binary)
    result = await worker.run(Task(objective="do"))
    assert result.workspace_root == str(ws)


async def test_execute_estimates_tokens_for_budget(tmp_path):
    """F3 (panel 2026-07-09, GLM P2 zero-cost coding calls): codex/opencode report
    cost 0.0 and no token usage, so CodeProposer charged ~nothing for the priciest
    calls. _execute now estimates tokens from char length so accounting is non-zero."""
    binary = _stub(tmp_path, "codex",
                   'cat > /dev/null; echo "created hello.py and wired it into the build"')
    worker = CodingAgentWorker("cx", cli="codex", workspace=tmp_path / "ws", binary=binary)
    result = await worker.run(_task(objective="write a reasonably long objective for tokens"))
    assert result.error is None
    assert result.tokens_in > 0 and result.tokens_out > 0
