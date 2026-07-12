from __future__ import annotations

import json

import pytest

from metaharness.portable.launchers import (
    all_launcher_layouts,
    launcher_descriptor,
    launcher_layout,
)


@pytest.mark.parametrize("target", ["codex", "claude-code", "pi", "opencode"])
def test_launcher_uses_neutral_argv_stdin_jsonl_and_exit_contract(target):
    value = launcher_descriptor(
        target,
        blueprint_path="packages/团队 harness.json",
        workspace_path="work spaces/Δ",
    )
    process = value["process"]
    assert process["argv"] == [
        "metaharness", "blueprint", "run", "packages/团队 harness.json",
        "--context-file", "-", "--workspace", "work spaces/Δ",
        "--format", "jsonl", "--approval", "stop",
    ]
    assert process["stdin"] == "context-json"
    assert process["stdout"] == "metaharness-jsonl-v1"
    assert process["stderr"] == "human-diagnostics"
    assert process["environment"] == {"METAHARNESS_HOST": target}
    assert process["exit_codes"] == {
        "completed": 0, "execution_failed": 1,
        "invalid_or_not_ready": 2, "approval_required": 20,
    }
    assert process["propagate_exit_code"] is True
    assert value["security"] == {
        "copies_vendor_auth": False, "shell": False, "approval_policy": "stop",
    }


def test_context_file_is_an_argv_element_and_does_not_claim_stdin():
    value = launcher_descriptor("pi", context_file="inputs/context with spaces.json")
    assert value["process"]["argv"][5] == "inputs/context with spaces.json"
    assert value["process"]["stdin"] == "inherit"


def test_launcher_layout_is_versioned_deterministic_and_generator_owned():
    first = all_launcher_layouts()
    assert first == all_launcher_layouts()
    assert len(first) == 8
    descriptor = json.loads(first["launchers/codex/launcher.json"])
    assert descriptor["schema_version"] == 1
    assert b"credentials" not in b"".join(first.values()).lower()
    assert b".claude" not in b"".join(first.values()).lower()
    assert launcher_layout("codex") == {
        key: first[key] for key in sorted(first) if key.startswith("launchers/codex/")
    }


def test_launcher_rejects_unknown_target_and_nul_paths():
    with pytest.raises(ValueError, match="unsupported"):
        launcher_descriptor("shell")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="path"):
        launcher_descriptor("codex", workspace_path="bad\x00path")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("blueprint_path", "/tmp/harness.json"),
        ("blueprint_path", "../harness.json"),
        ("blueprint_path", "dir\\harness.json"),
        ("context_file", ".claude/credentials.json"),
        ("context_file", "inputs/../context.json"),
        ("workspace_path", "/Users/alice/.codex"),
        ("workspace_path", "/Users/alice/.config/opencode"),
        ("workspace_path", "/Users/alice/.local/share/opencode/auth.json"),
        ("blueprint_path", "--help"),
        ("context_file", "--context.json"),
        ("workspace_path", "--workspace"),
    ],
)
def test_launcher_rejects_traversal_absolute_package_paths_and_auth_homes(field, value):
    with pytest.raises(ValueError):
        launcher_descriptor("codex", **{field: value})
