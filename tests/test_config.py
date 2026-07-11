"""Config store: obfuscation, masking, persistence, factory wiring."""
from __future__ import annotations

import json
import os

import pytest

from metaharness.config import (
    PROVIDER_CATALOG,
    AgentConfig,
    HarnessConfig,
    MCPServerConfig,
    ProviderConfig,
    deobfuscate,
    is_masked,
    mask_key,
    obfuscate,
)
from metaharness.core.types import Tier
from metaharness.factory import build_agent_runner
from metaharness.harness import (
    CodingAgentWorker,
    MockLLMWorker,
    OpenAICompatWorker,
    SubscriptionWorker,
)


@pytest.fixture()
def salt_path(tmp_path):
    return tmp_path / ".keysalt"


def test_obfuscate_roundtrip_and_prefix(salt_path):
    secret = "sk-abc123xyz789"
    stored = obfuscate(secret, salt_path)
    assert stored.startswith("enc1:") and secret not in stored
    assert deobfuscate(stored, salt_path) == secret
    # double-obfuscation is a no-op, so re-saving never corrupts a key
    assert obfuscate(stored, salt_path) == stored


def test_salt_file_created_private(salt_path):
    obfuscate("anything", salt_path)
    assert salt_path.exists()
    assert oct(salt_path.stat().st_mode & 0o777) == "0o600"


def test_legacy_plaintext_passes_through(salt_path):
    assert deobfuscate("sk-plaintext", salt_path) == "sk-plaintext"


def test_mask_key_never_reveals_middle():
    assert mask_key("sk-abc123456789xy") == "sk-…xy"
    assert mask_key("short") == "set"
    assert mask_key("") is None
    assert is_masked("sk-…xy") and not is_masked("sk-real")


def test_config_save_load_roundtrip(tmp_path, salt_path):
    cfg = HarnessConfig()
    cfg.providers["openai"] = ProviderConfig(
        id="openai", base_url="https://api.openai.com/v1", api_key="sk-secret99999"
    )
    cfg.agents.append(AgentConfig(worker_id="w1", tier="mid", provider="openai",
                                  model="gpt-5.2", system_prompt="Be terse."))
    path = tmp_path / "config.json"
    cfg.save(path, salt_path)

    assert oct(path.stat().st_mode & 0o777) == "0o600"
    on_disk = json.loads(path.read_text())
    assert "sk-secret99999" not in json.dumps(on_disk)  # never plaintext at rest

    loaded = HarnessConfig.load(path)
    assert loaded.get_api_key("openai", salt_path) == "sk-secret99999"
    agent = loaded.agent("w1")
    assert agent is not None and agent.system_prompt == "Be terse."


def test_public_dict_masks_keys(salt_path):
    cfg = HarnessConfig()
    cfg.providers["groq"] = ProviderConfig(
        id="groq", base_url="https://api.groq.com/openai/v1",
        api_key=obfuscate("gsk-verylongsecretkey", salt_path),
    )
    public = json.dumps(cfg.public_dict(salt_path))
    assert "gsk-verylongsecretkey" not in public
    assert "enc1:" not in public  # obfuscated form is also never exposed
    assert cfg.public_dict(salt_path)["providers"]["groq"]["configured"] is True


def test_mcp_secrets_are_obfuscated_masked_and_recoverable(tmp_path, salt_path):
    cfg = HarnessConfig(mcp_servers={
        "search": MCPServerConfig(
            name="search", command="npx",
            env={"BRAVE_API_KEY": "brave-secret-value"},
        ),
        "gmail": MCPServerConfig(
            name="gmail", transport="http",
            url="https://gmailmcp.googleapis.com/mcp/v1",
            oauth_token="oauth-access-token-value",
            oauth_project="workspace-project",
        ),
    })
    path = tmp_path / "config.json"
    cfg.save(path, salt_path)

    on_disk = path.read_text()
    assert "brave-secret-value" not in on_disk
    assert "oauth-access-token-value" not in on_disk
    loaded = HarnessConfig.load(path)
    assert loaded.mcp_servers["search"].plain_env(salt_path) == {
        "BRAVE_API_KEY": "brave-secret-value",
    }
    assert loaded.mcp_servers["gmail"].plain_oauth_token(salt_path) == (
        "oauth-access-token-value"
    )
    public = loaded.public_dict(salt_path)["mcp_servers"]
    assert public["search"]["env"] == {"BRAVE_API_KEY": "bra…ue"}
    assert public["gmail"]["oauth_token"] == "oau…ue"
    assert public["gmail"]["authenticated"] is True


def test_apply_provider_update_ignores_masked_echo(salt_path):
    cfg = HarnessConfig()
    cfg.apply_provider_update("deepseek", {
        "base_url": "https://api.deepseek.com/v1", "api_key": "dsk-original-key-123",
    }, salt_path)
    # the UI round-trips the masked value on an unrelated edit — key must survive
    cfg.apply_provider_update("deepseek", {
        "api_key": mask_key("dsk-original-key-123"), "default_model": "deepseek-v4",
    }, salt_path)
    assert cfg.get_api_key("deepseek", salt_path) == "dsk-original-key-123"
    assert cfg.providers["deepseek"].default_model == "deepseek-v4"


def test_upsert_and_remove_agent():
    cfg = HarnessConfig()
    cfg.upsert_agent(AgentConfig(worker_id="a", tier="small"))
    cfg.upsert_agent(AgentConfig(worker_id="a", tier="frontier"))  # replace
    assert len(cfg.agents) == 1 and cfg.agents[0].tier == "frontier"
    assert cfg.remove_agent("a") and not cfg.remove_agent("a")


def test_resolve_endpoint_provider_ref_and_direct(salt_path):
    cfg = HarnessConfig()
    cfg.providers["p"] = ProviderConfig(
        id="p", base_url="https://api.p.com/v1",
        api_key=obfuscate("key-abc", salt_path))
    via_ref = cfg.resolve_endpoint(AgentConfig(worker_id="x", provider="p"), salt_path)
    assert via_ref == ("https://api.p.com/v1", "key-abc")
    direct = cfg.resolve_endpoint(
        AgentConfig(worker_id="y", base_url="http://localhost:1234/v1"), salt_path)
    assert direct == ("http://localhost:1234/v1", "")


def test_agent_config_timeout_roundtrip(tmp_path, salt_path):
    """issue #2: timeout_s persists through save/load, set and unset."""
    cfg = HarnessConfig()
    cfg.agents.append(AgentConfig(worker_id="set", kind="coding_cli", cli="codex", timeout_s=900.0))
    cfg.agents.append(AgentConfig(worker_id="unset", kind="coding_cli", cli="codex"))
    path = tmp_path / "config.json"
    cfg.save(path, salt_path)

    loaded = HarnessConfig.load(path)
    assert loaded.agent("set").timeout_s == 900.0
    assert loaded.agent("unset").timeout_s is None


def test_agent_config_timeout_bounds():
    """issue #2 panel (Claude+codex+kimi P2): gt=0 alone accepts +Infinity and
    unbounded values that :g renders as scientific notation ('1e+15s')."""
    for bad in (float("inf"), float("nan"), 1e15, 0, -5):
        with pytest.raises(ValueError):
            AgentConfig(worker_id="w", timeout_s=bad)
    assert AgentConfig(worker_id="w", timeout_s=86400).timeout_s == 86400.0
    assert AgentConfig(worker_id="w", timeout_s=0.5).timeout_s == 0.5


def test_catalog_has_local_and_remote_entries():
    ids = {p["id"] for p in PROVIDER_CATALOG}
    assert {"anthropic", "openai", "neuralwatt", "ollama", "lmstudio", "custom"} <= ids
    assert all(p["base_url"] for p in PROVIDER_CATALOG if p["id"] != "custom")


# ------------------------------------------------------------------- factory

def test_factory_builds_openai_compat(salt_path):
    cfg = HarnessConfig()
    cfg.providers["p"] = ProviderConfig(
        id="p", base_url="https://api.p.com/v1", api_key=obfuscate("k1", salt_path))
    agent = AgentConfig(worker_id="w", kind="openai_compat", tier="mid",
                        provider="p", model="m1", system_prompt="You review code.")
    runner = build_agent_runner(agent, cfg, salt_path=salt_path)
    assert isinstance(runner, OpenAICompatWorker)
    assert runner.tier is Tier.MID
    assert runner.api_key == "k1"
    assert runner.system_prompt == "You review code."


def test_factory_builds_coding_cli_and_mock():
    cfg = HarnessConfig()
    coding = build_agent_runner(
        AgentConfig(worker_id="c", kind="coding_cli", tier="frontier", cli="codex"), cfg)
    assert isinstance(coding, CodingAgentWorker) and coding.cli == "codex"
    mock = build_agent_runner(AgentConfig(worker_id="m", kind="mock"), cfg)
    assert isinstance(mock, MockLLMWorker)


def test_factory_timeout_pass_through(salt_path):
    """issue #2: a configured timeout_s reaches the built worker; unset keeps
    each worker class's own default."""
    cfg = HarnessConfig()
    cfg.providers["p"] = ProviderConfig(
        id="p", base_url="https://api.p.com/v1", api_key=obfuscate("k1", salt_path))

    coding = build_agent_runner(
        AgentConfig(worker_id="c", kind="coding_cli", cli="codex", timeout_s=900.0), cfg)
    assert coding.timeout_s == 900.0
    coding_default = build_agent_runner(
        AgentConfig(worker_id="c2", kind="coding_cli", cli="codex"), cfg)
    assert coding_default.timeout_s is None  # None = task-type-aware default, not a literal

    sub = build_agent_runner(
        AgentConfig(worker_id="s", kind="subscription_cli", cli="codex", timeout_s=900.0), cfg)
    assert isinstance(sub, SubscriptionWorker) and sub.timeout_s == 900.0
    sub_default = build_agent_runner(
        AgentConfig(worker_id="s2", kind="subscription_cli", cli="codex"), cfg)
    assert sub_default.timeout_s is None

    compat = build_agent_runner(
        AgentConfig(worker_id="o", kind="openai_compat", provider="p", model="m1",
                    timeout_s=900.0), cfg, salt_path=salt_path)
    assert compat.timeout_s == 900.0
    compat_default = build_agent_runner(
        AgentConfig(worker_id="o2", kind="openai_compat", provider="p", model="m1"),
        cfg, salt_path=salt_path)
    assert compat_default.timeout_s == 120.0  # OpenAICompatWorker's own class default


def test_factory_rejects_bad_definitions():
    cfg = HarnessConfig()
    with pytest.raises(ValueError, match="no endpoint"):
        build_agent_runner(AgentConfig(worker_id="w", kind="openai_compat"), cfg)
    with pytest.raises(ValueError, match="needs 'cli'"):
        build_agent_runner(AgentConfig(worker_id="w", kind="coding_cli"), cfg)
    with pytest.raises(ValueError, match="unknown kind"):
        build_agent_runner(AgentConfig(worker_id="w", kind="martian"), cfg)
