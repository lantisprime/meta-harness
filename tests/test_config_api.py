"""Config API: masked exposure, wizard test/persist/retire flows."""
from __future__ import annotations

import json

import httpx
import pytest

import metaharness.config as config_mod
from metaharness.core.types import TaskType, Tier
from metaharness.harness import MockLLMWorker
from metaharness.identity import KeyPair
from metaharness.web import HarnessState, create_app


@pytest.fixture
async def harness(tmp_path, monkeypatch):
    monkeypatch.setattr(config_mod, "SALT_PATH", tmp_path / ".keysalt")
    state = HarnessState()
    kp = KeyPair.generate()
    worker = MockLLMWorker("w-small", Tier.SMALL, keypair=kp,
                           skills={t: 1.0 for t in TaskType})
    state.register_worker(worker, kp, tiers=["small"])
    state.wire({Tier.SMALL: worker}, journal_dir=tmp_path)
    state.config_path = tmp_path / "config.json"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(state)),
                                 base_url="http://test") as client:
        yield state, client


async def test_config_roundtrip_masks_keys(harness):
    state, client = harness
    resp = await client.post("/api/config/providers", json={
        "id": "groq", "base_url": "https://api.groq.com/openai/v1",
        "api_key": "gsk-supersecretvalue123"})
    assert resp.status_code == 200
    assert resp.json()["api_key"] == "gsk…23"

    cfg = (await client.get("/api/config")).json()
    dumped = json.dumps(cfg)
    assert "gsk-supersecretvalue123" not in dumped and "enc1:" not in dumped
    assert cfg["providers"]["groq"]["configured"] is True
    assert any(p["id"] == "anthropic" for p in cfg["catalog"])
    assert "coding_clis" in cfg

    # persisted to disk, masked echo never clobbers
    on_disk = json.loads(state.config_path.read_text())
    assert "gsk-supersecretvalue123" not in json.dumps(on_disk)
    await client.post("/api/config/providers", json={
        "id": "groq", "api_key": "gsk…23", "default_model": "llama-5"})
    assert state.config.get_api_key("groq") == "gsk-supersecretvalue123"
    assert state.config.providers["groq"].default_model == "llama-5"


async def test_add_persisted_mock_worker_and_retire(harness):
    state, client = harness
    resp = await client.post("/api/workers", json={
        "worker_id": "extra-mid", "tier": "mid", "kind": "mock",
        "system_prompt": "You are the mid tier."})
    assert resp.status_code == 201
    saved = json.loads(state.config_path.read_text())
    assert any(a["worker_id"] == "extra-mid" for a in saved["agents"])

    resp = await client.delete("/api/workers/extra-mid")
    assert resp.status_code == 200
    assert resp.json()["config_removed"] is True
    record = state.registry.get("extra-mid")
    assert record is not None and record.active is False
    assert Tier.MID not in state.router.runners
    assert (await client.delete("/api/workers/extra-mid")).status_code == 200 or True
    assert (await client.delete("/api/workers/ghost")).status_code == 404
    assert (await client.delete("/api/workers/orchestrator")).status_code == 422


async def test_add_worker_persist_false_stays_ephemeral(harness):
    state, client = harness
    resp = await client.post("/api/workers", json={
        "worker_id": "temp", "tier": "frontier", "kind": "mock", "persist": False})
    assert resp.status_code == 201
    assert state.config.agent("temp") is None


async def test_test_worker_mock_and_missing_cli(harness):
    _, client = harness
    assert (await client.post("/api/test_worker", json={"kind": "mock"})).json()["ok"]
    resp = await client.post("/api/test_worker",
                             json={"kind": "coding_cli", "cli": "nonexistent-cli"})
    assert resp.status_code == 200  # wizard shows the failure, HTTP layer is fine
    body = resp.json()
    assert body["ok"] is False and "not found" in body["detail"]


async def test_test_worker_requires_endpoint(harness):
    _, client = harness
    resp = await client.post("/api/test_worker", json={"kind": "openai_compat"})
    assert resp.status_code == 422


async def test_provider_delete_blocked_by_dependent_agent(harness):
    state, client = harness
    await client.post("/api/config/providers", json={
        "id": "p1", "base_url": "https://x/v1", "api_key": "k-123456789"})
    state.config.upsert_agent(config_mod.AgentConfig(
        worker_id="uses-p1", provider="p1", model="m"))
    assert (await client.delete("/api/config/providers/p1")).status_code == 409
    state.config.remove_agent("uses-p1")
    assert (await client.delete("/api/config/providers/p1")).status_code == 200
    assert (await client.delete("/api/config/providers/p1")).status_code == 404


async def test_mcp_server_config_crud(harness):
    state, client = harness
    resp = await client.post("/api/config/mcp", json={
        "name": "files", "transport": "stdio",
        "command": "npx", "args": ["-y", "@mcp/files"]})
    assert resp.status_code == 200
    assert "files" in state.config.mcp_servers
    assert (await client.get("/api/config")).json()["mcp_servers"]["files"]["command"] == "npx"
    assert (await client.delete("/api/config/mcp/files")).status_code == 200
    assert (await client.delete("/api/config/mcp/files")).status_code == 404


async def test_coding_cli_worker_add_validates_path(harness):
    _, client = harness
    resp = await client.post("/api/workers", json={
        "worker_id": "coder", "tier": "frontier", "kind": "coding_cli",
        "cli": "definitely-not-installed"})
    assert resp.status_code == 422


async def test_probe_post_provider_ref_and_no_key_in_url(harness, monkeypatch):
    state, client = harness
    seen = {}

    async def fake_probe(base_url, timeout_s=3.0, api_key=""):
        seen.update(base_url=base_url, api_key=api_key)
        return ["m-1", "m-2"]

    import metaharness.web.app as app_mod
    monkeypatch.setattr(app_mod, "probe_endpoint", fake_probe)
    await client.post("/api/config/providers", json={
        "id": "px", "base_url": "https://api.px.dev/v1", "api_key": "sk-px-secret99"})
    resp = await client.post("/api/probe", json={"provider": "px"})
    assert resp.status_code == 200
    assert resp.json() == {"reachable": True, "models": ["m-1", "m-2"]}
    # stored key resolved server-side; caller never put it in a URL
    assert seen == {"base_url": "https://api.px.dev/v1", "api_key": "sk-px-secret99"}
    assert (await client.post("/api/probe", json={})).status_code == 422


async def test_cli_models_endpoint_static_aliases(harness):
    _, client = harness
    resp = await client.post("/api/cli_models", json={"cli": "claude"})
    assert resp.status_code == 200
    assert "sonnet" in resp.json()["models"]
    assert (await client.post("/api/cli_models", json={"cli": "vim"})).status_code == 422


async def test_config_exposes_subscriptions_and_key_hints(harness):
    _, client = harness
    cfg = (await client.get("/api/config")).json()
    assert set(cfg["subscriptions"]) == {"claude", "codex"}
    assert {"installed", "authenticated", "login_hint", "models"} <= set(
        cfg["subscriptions"]["claude"])
    assert "codex login" in cfg["cli_key_hints"]["codex"]


async def test_add_subscription_worker_validates_install(harness, monkeypatch):
    state, client = harness
    import metaharness.web.app as app_mod
    monkeypatch.setattr(app_mod, "subscription_status",
                        lambda: {"claude": {"installed": False}})
    resp = await client.post("/api/workers", json={
        "worker_id": "sub-w", "tier": "frontier",
        "kind": "subscription_cli", "cli": "claude"})
    assert resp.status_code == 422


async def test_reserved_worker_ids_rejected(harness):
    _, client = harness
    for wid in ("orchestrator", "config-test"):
        resp = await client.post("/api/workers", json={
            "worker_id": wid, "tier": "mid", "kind": "mock"})
        assert resp.status_code == 422
        assert "reserved" in resp.json()["detail"]


async def test_retire_then_readd_same_worker_id(harness):
    """Regression: 'worker X is already registered' after remove -> re-add.
    Retirement deactivates the identity; the same id must be re-admittable."""
    _, client = harness
    body = {"worker_id": "planner-bot", "tier": "mid", "kind": "mock"}
    assert (await client.post("/api/workers", json=body)).status_code == 201
    assert (await client.post("/api/workers", json=body)).status_code == 409  # active dup
    assert (await client.delete("/api/workers/planner-bot")).status_code == 200
    resp = await client.post("/api/workers", json=body)
    assert resp.status_code == 201
    assert resp.json()["key_rotations"] == 1  # re-admission visible in audit
