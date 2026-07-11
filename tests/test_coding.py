"""CodingAgentWorker: headless CLI invocation via stub scripts."""
from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from metaharness.core.types import MASTMode, Task, TaskType, Tier
from metaharness.evals.verifiers import verify_output
from metaharness.harness import CLI_ADAPTERS, CodingAgentWorker, SubscriptionWorker, available_clis
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
    # issue #2: structured timeout signal, and the exact EFFECTIVE timeout
    # (:g format — "0.5s" must not render as "0s")
    assert result.timed_out is True
    assert "0.5s" in result.error
    verification = verify_output(_task(), result)
    assert verification.failure_mode == MASTMode.TIMEOUT


async def test_execute_applies_scaled_timeout_when_unset(tmp_path, monkeypatch):
    """issue #2 panel (kimi P2): reverting _execute to `timeout=self.timeout_s`
    stayed green because every timeout test set timeout_s explicitly. Leave it
    UNSET and prove _execute enforced the task-type-SCALED value: base 0.2s ->
    a code_edit task must report the 3x effective timeout, 0.6s."""
    binary = _stub(tmp_path, "opencode", "sleep 30")
    monkeypatch.setattr(CodingAgentWorker, "BASE_TIMEOUT_S", 0.2)
    worker = CodingAgentWorker("oc", cli="opencode", workspace=tmp_path / "ws",
                               binary=binary)  # timeout_s deliberately unset
    result = await worker.run(_task(task_type=TaskType.CODE_EDIT))
    assert result.timed_out is True
    assert "0.6s" in result.error  # the SCALED value, not the 0.2 base


def test_effective_timeout_s_unset_scales_by_task_type(tmp_path):
    """issue #2: unset config -> code_edit gets 3x the base; other task types
    keep the flat base."""
    worker = CodingAgentWorker("cc", cli="codex", workspace=tmp_path / "ws")
    assert worker.effective_timeout_s(_task(task_type=TaskType.CODE_EDIT)) == 1800.0
    assert worker.effective_timeout_s(_task(task_type=TaskType.GENERAL)) == 600.0


def test_effective_timeout_s_explicit_override_wins_flat(tmp_path):
    """An explicit config override applies to every task type, no scaling."""
    worker = CodingAgentWorker("cc", cli="codex", workspace=tmp_path / "ws", timeout_s=50.0)
    assert worker.effective_timeout_s(_task(task_type=TaskType.CODE_EDIT)) == 50.0
    assert worker.effective_timeout_s(_task(task_type=TaskType.GENERAL)) == 50.0


def test_subscription_worker_effective_timeout_unset_scales_by_task_type(tmp_path):
    """SubscriptionWorker's own 300s base also scales for code_edit."""
    worker = SubscriptionWorker("sub", cli="codex")
    assert worker.effective_timeout_s(_task(task_type=TaskType.CODE_EDIT)) == 900.0
    assert worker.effective_timeout_s(_task(task_type=TaskType.GENERAL)) == 300.0


def test_subscription_cli_adapters_enforce_read_only_tools(tmp_path):
    workspace = tmp_path / "workspace"

    claude = SubscriptionWorker("sub-claude", cli="claude")
    claude_argv, _ = CLI_ADAPTERS["claude"].build(claude, "inspect", workspace)
    assert "--safe-mode" in claude_argv
    assert "--no-session-persistence" in claude_argv
    assert claude_argv[claude_argv.index("--permission-mode") + 1] == "plan"
    assert claude_argv[claude_argv.index("--tools") + 1] == "Read,Glob,Grep"

    codex = SubscriptionWorker("sub-codex", cli="codex")
    codex_argv, _ = CLI_ADAPTERS["codex"].build(codex, "inspect", workspace)
    assert codex_argv[codex_argv.index("--sandbox") + 1] == "read-only"
    assert "--ephemeral" in codex_argv


async def test_subscription_phases_share_active_workspace_read_only(tmp_path):
    """Issue #18: read-only phases inspect the run workspace, while only the
    code-edit worker mutates it; provenance and packaging agree on one root."""
    import io
    import zipfile

    from metaharness.tools import default_registry
    from metaharness.web import HarnessState
    from metaharness.workflows import RunStatus, get_template
    from metaharness.workflows.package import build_package_bytes

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    artifact = workspace / "artifact.txt"
    artifact.write_text("seed\n")

    subscription_binary = _stub(tmp_path, "subscription-claude", r'''
prompt=$(cat)
content=$(tr '\n' ' ' < artifact.txt)
case "$prompt" in
  *all_met*) printf '{"result":"{\\"all_met\\":true,\\"criteria\\":[{\\"criterion\\":\\"artifact\\",\\"met\\":true,\\"evidence\\":\\"%s\\"}]}"}\n' "$content" ;;
  *) printf '{"result":"saw artifact.txt: %s"}\n' "$content" ;;
esac
''')
    coding_binary = _stub(tmp_path, "coding-claude", r'''
prompt=$(cat)
case "$prompt" in
  *"Implement the approved plan"*)
    printf 'implemented\n' >> artifact.txt
    result='implemented artifact.txt' ;;
  *) result='technical plan for artifact.txt' ;;
esac
printf '{"result":"%s"}\n' "$result"
''')

    sub_key = KeyPair.generate()
    code_key = KeyPair.generate()
    subscription = SubscriptionWorker(
        "subscription", cli="claude", tier=Tier.MID,
        keypair=sub_key, binary=subscription_binary,
    )
    coding = CodingAgentWorker(
        "coding", cli="claude", tier=Tier.FRONTIER,
        keypair=code_key, workspace=workspace, binary=coding_binary,
    )
    state = HarnessState()
    state.tools = default_registry(workspace)
    state.register_worker(subscription, sub_key, tiers=["mid"])
    state.register_worker(coding, code_key, tiers=["frontier"])
    journal_dir = tmp_path / "journals"
    journal_dir.mkdir()
    state.wire(
        {Tier.MID: subscription, Tier.FRONTIER: coding},
        journal_dir=journal_dir, judge=False,
    )

    spec = get_template("software_engineering").instantiate("update artifact.txt")
    run = state.engine.start(spec, context={"goal": "update artifact.txt"})
    run = await state.engine.advance(run.run_id)
    assert run.status is RunStatus.AWAITING_APPROVAL
    assert run.awaiting == "specify"
    assert "seed" in run.completed["explore"].output
    assert "seed" in run.completed["specify"].output
    assert artifact.read_text() == "seed\n"

    state.engine.approve(run.run_id, "specify")
    run = await state.engine.advance(run.run_id)
    assert run.awaiting == "plan"
    assert artifact.read_text() == "seed\n"

    state.engine.approve(run.run_id, "plan")
    run = await state.engine.advance(run.run_id)
    assert run.awaiting == "review"
    assert artifact.read_text() == "seed\nimplemented\n"
    evidence = run.completed["verify"].output["criteria"][0]["evidence"]
    assert "implemented" in evidence
    assert "implemented" in run.completed["review"].output
    assert {record.workspace_root for record in run.completed.values()} == {str(workspace)}

    state.engine.approve(run.run_id, "review")
    run = await state.engine.advance(run.run_id)
    assert run.status is RunStatus.COMPLETED

    package = build_package_bytes(spec, run, state.engine.journal(run.run_id).entries())
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert {step["workspace_root"] for step in manifest["steps"].values()} == {
            str(workspace)
        }
        workspace_files = [name for name in archive.namelist()
                           if name.startswith("workspace/")]
        assert len(workspace_files) == 1
        assert workspace_files[0].endswith("/artifact.txt")


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
