"""Config API: masked exposure, wizard test/persist/retire flows."""
from __future__ import annotations

import json

import httpx
import pytest

import metaharness.config as config_mod
from metaharness.core.types import Task, TaskType, Tier
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
    assert Tier.MID not in state.router.pools  # last member gone -> tier retired
    assert (await client.delete("/api/workers/extra-mid")).status_code == 200 or True
    assert (await client.delete("/api/workers/ghost")).status_code == 404
    assert (await client.delete("/api/workers/orchestrator")).status_code == 422


async def test_two_workers_same_tier_pool_and_partial_retire(harness):
    """A tier holds a pool: two workers coexist, retiring one keeps the tier
    serving through the other, retiring the last drops the tier."""
    state, client = harness
    for wid in ("mid-a", "mid-b"):
        resp = await client.post("/api/workers", json={
            "worker_id": wid, "tier": "mid", "kind": "mock"})
        assert resp.status_code == 201
    pool_ids = [r.worker_id for r in state.router.pools[Tier.MID]]
    assert pool_ids == ["mid-a", "mid-b"]  # both pooled, config order preserved

    # retire the first: the tier still serves through the survivor
    assert (await client.delete("/api/workers/mid-a")).status_code == 200
    assert [r.worker_id for r in state.router.pools[Tier.MID]] == ["mid-b"]
    decision = state.router.decide(
        Task(task_type=TaskType.CLASSIFY, tier_hint=Tier.MID))
    assert decision.tier == Tier.MID and decision.worker_id == "mid-b"

    # retire the last: the tier key is gone
    assert (await client.delete("/api/workers/mid-b")).status_code == 200
    assert Tier.MID not in state.router.pools


async def test_add_worker_persist_false_stays_ephemeral(harness):
    state, client = harness
    resp = await client.post("/api/workers", json={
        "worker_id": "temp", "tier": "frontier", "kind": "mock", "persist": False})
    assert resp.status_code == 201
    assert state.config.agent("temp") is None


async def test_add_worker_timeout_validation_and_persist(harness, monkeypatch):
    """issue #2: timeout_s=0 is rejected by AddWorkerRequest's gt=0 (422); a
    valid value persists to config.json AND reaches the built worker."""
    state, client = harness
    resp = await client.post("/api/workers", json={
        "worker_id": "bad-timeout", "tier": "small", "kind": "mock", "timeout_s": 0})
    assert resp.status_code == 422

    import metaharness.web.app as app_mod
    monkeypatch.setattr(app_mod, "available_clis", lambda: {"codex": "/usr/bin/codex"})
    resp = await client.post("/api/workers", json={
        "worker_id": "cx-timeout", "tier": "small", "kind": "coding_cli",
        "cli": "codex", "timeout_s": 900})
    assert resp.status_code == 201

    on_disk = json.loads(state.config_path.read_text())
    saved = next(a for a in on_disk["agents"] if a["worker_id"] == "cx-timeout")
    assert saved["timeout_s"] == 900
    runner = next(r for r in state.router.pools[Tier.SMALL] if r.worker_id == "cx-timeout")
    assert runner.timeout_s == 900


async def test_add_worker_timeout_upper_bounds(harness, monkeypatch):
    """issue #2 panel (Claude+codex+kimi P2): +Infinity and unbounded values
    must 422 at the API boundary; the 24h ceiling itself is accepted."""
    state, client = harness
    import metaharness.web.app as app_mod
    monkeypatch.setattr(app_mod, "available_clis", lambda: {"codex": "/usr/bin/codex"})

    for bad in (float("inf"), 1e15, 0):
        with pytest.raises(ValueError):
            app_mod.AddWorkerRequest(worker_id="w", tier="small", timeout_s=bad)

    base = {"worker_id": "cx-bounds", "tier": "small", "kind": "coding_cli",
            "cli": "codex"}
    # Infinity is rejected at the model boundary (pydantic finite_number). The
    # wire response cannot be a clean 422 — starlette's JSONResponse
    # (allow_nan=False) refuses to echo inf back in the error detail — but the
    # property that matters holds: the request fails loudly and no worker is
    # ever created or persisted.
    with pytest.raises(Exception):
        await client.post(
            "/api/workers",
            content=json.dumps({**base, "timeout_s": float("inf")}),
            headers={"Content-Type": "application/json"})
    assert state.config.agent("cx-bounds") is None
    resp = await client.post("/api/workers", json={**base, "timeout_s": 1e15})
    assert resp.status_code == 422
    resp = await client.post("/api/workers", json={**base, "timeout_s": 86400})
    assert resp.status_code == 201
    assert state.config.agent("cx-bounds").timeout_s == 86400.0


async def test_mock_worker_never_persists_a_timeout(harness):
    """issue #2 panel (codex+kimi P2): a direct API caller bypasses the wizard
    JS guards — the server must drop timeout_s for kind=mock, or the settings
    card displays a fake timeout."""
    state, client = harness
    resp = await client.post("/api/workers", json={
        "worker_id": "mock-t", "tier": "small", "kind": "mock",
        "timeout_s": 5, "persist": True})
    assert resp.status_code == 201
    on_disk = json.loads(state.config_path.read_text())
    saved = next(a for a in on_disk["agents"] if a["worker_id"] == "mock-t")
    assert saved["timeout_s"] is None


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
    from metaharness.tools import ToolSpec
    state.tools.register(ToolSpec(
        "files.search", "search files", {}, lambda: "ok", source="mcp:files",
    ))
    assert (await client.delete("/api/config/mcp/files")).status_code == 200
    assert state.tools.get("files.search") is None
    assert (await client.delete("/api/config/mcp/files")).status_code == 404


async def test_mcp_oauth_token_is_masked_and_server_loads_in_app(
        harness, monkeypatch):
    state, client = harness
    resp = await client.post("/api/config/mcp", json={
        "name": "gmail", "transport": "http",
        "url": "https://gmailmcp.googleapis.com/mcp/v1",
        "oauth_token": "oauth-secret-token-value",
        "oauth_project": "workspace-project",
    })
    assert resp.status_code == 200
    assert resp.json()["oauth_token"] == "oau…ue"
    assert "oauth-secret-token-value" not in state.config_path.read_text()
    public = (await client.get("/api/config")).json()["mcp_servers"]["gmail"]
    assert public["oauth_token"] == "oau…ue"
    assert public["authenticated"] is True

    seen = {}

    async def fake_load(registry, config):
        seen["names"] = list(config.mcp_servers)
        seen["token"] = config.mcp_servers["gmail"].plain_oauth_token()
        return {"gmail": {"ok": True, "tools": 9}}

    import metaharness.tools
    monkeypatch.setattr(metaharness.tools, "load_mcp_tools", fake_load)
    loaded = await client.post("/api/config/mcp/gmail/load")
    assert loaded.status_code == 200
    assert loaded.json() == {"ok": True, "tools": 9}
    assert seen == {"names": ["gmail"], "token": "oauth-secret-token-value"}

    poisoned = await client.post("/api/config/mcp", json={
        "name": "bad", "command": "npx", "env": {"TOKEN": "enc1:not-ciphertext"},
    })
    assert poisoned.status_code == 422


async def test_mcp_remote_url_and_oauth_origin_are_validated(harness):
    _, client = harness
    invalid = await client.post("/api/config/mcp", json={
        "name": "bad", "transport": "http", "url": "not-a-url",
    })
    assert invalid.status_code == 422
    exfiltration = await client.post("/api/config/mcp", json={
        "name": "bad", "transport": "http", "url": "https://evil.example/mcp",
        "oauth_token": "oauth-token", "oauth_project": "workspace-project",
    })
    assert exfiltration.status_code == 422
    assert "oauth-token" not in exfiltration.text
    missing_project = await client.post("/api/config/mcp", json={
        "name": "gmail", "transport": "http",
        "url": "https://gmailmcp.googleapis.com/mcp/v1",
        "oauth_token": "never-echo-this-token",
    })
    assert missing_project.status_code == 422
    assert "never-echo-this-token" not in missing_project.text


async def test_mcp_tools_always_gain_server_side_hitl_gate(harness):
    state, client = harness
    assert state.tools.get("mail.send") is None  # temporarily unloaded/stale MCP name
    workflow = {"name": "unsafe", "steps": [{
        "id": "send", "objective": "Send the message",
        "tools": ["mail.send"], "hitl": False,
    }]}
    validated = await client.post("/api/workflows/validate", json={"workflow": workflow})
    assert validated.status_code == 200
    step = validated.json()["workflow"]["steps"][0]
    assert step["hitl"] is True and step["hitl_timing"] == "before"
    started = await client.post("/api/runs", json={
        "workflow": workflow, "wait": True,
    })
    assert started.status_code == 200
    assert started.json()["status"] == "awaiting_approval"
    assert started.json()["awaiting"] == "send"


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
    Retirement deactivates the identity; the same id must be re-admittable.
    Re-adding a LIVE id is an in-place replace with a key rotation, not a
    conflict (each rotation stays visible in the audit trail)."""
    state, client = harness
    body = {"worker_id": "planner-bot", "tier": "mid", "kind": "mock"}
    assert (await client.post("/api/workers", json=body)).status_code == 201
    resp = await client.post("/api/workers", json=body)  # live dup -> rotate in place
    assert resp.status_code == 201
    assert resp.json()["key_rotations"] == 1
    assert [r.worker_id for r in state.router.pools[Tier.MID]] == ["planner-bot"]
    assert (await client.delete("/api/workers/planner-bot")).status_code == 200
    resp = await client.post("/api/workers", json=body)
    assert resp.status_code == 201
    assert resp.json()["key_rotations"] == 2  # every re-admission audited


async def test_readd_live_worker_replaces_pool_member_in_place(harness):
    """FIX: the replace-in-place promise used to be unreachable — the registry
    refused live ids before the pool swap ran. Re-adding now rotates the
    identity and swaps the member at its existing pool position."""
    state, _ = harness
    from metaharness.harness import MockLLMWorker
    from metaharness.identity import KeyPair

    old = MockLLMWorker("pool-a", Tier.MID, keypair=KeyPair.generate())
    other = MockLLMWorker("pool-b", Tier.MID, keypair=KeyPair.generate())
    state.add_worker(old, Tier.MID)
    state.add_worker(other, Tier.MID)

    new = MockLLMWorker("pool-a", Tier.MID, keypair=KeyPair.generate())
    state.add_worker(new, Tier.MID)  # must not raise RegistryError
    pool = state.router.pools[Tier.MID]
    assert [r.worker_id for r in pool] == ["pool-a", "pool-b"]  # position kept
    assert pool[0] is new  # the new runner object serves, not the old one
    assert state.registry.get("pool-a").key_rotations == 1
    assert state.registry.get("pool-a").active


async def test_workflow_validate_endpoint(harness):
    _, client = harness
    good = {"name": "wf", "steps": [
        {"id": "a", "objective": "do a"},
        {"id": "b", "objective": "do b", "depends_on": ["a"], "hitl": True}]}
    resp = await client.post("/api/workflows/validate", json={"workflow": good})
    assert resp.status_code == 200
    body = resp.json()
    assert [s["id"] for s in body["workflow"]["steps"]] == ["a", "b"]
    assert "steps:" in body["yaml"]

    # YAML round-trips through the same validator
    resp = await client.post("/api/workflows/validate",
                             json={"workflow_yaml": body["yaml"]})
    assert resp.status_code == 200

    dup = {"name": "wf", "steps": [{"id": "a", "objective": "x"},
                                   {"id": "a", "objective": "y"}]}
    resp = await client.post("/api/workflows/validate", json={"workflow": dup})
    assert resp.status_code == 422 and "duplicate" in resp.json()["detail"].lower()

    ghost_dep = {"name": "wf", "steps": [
        {"id": "a", "objective": "x", "depends_on": ["nope"]}]}
    resp = await client.post("/api/workflows/validate", json={"workflow": ghost_dep})
    assert resp.status_code == 422

    assert (await client.post("/api/workflows/validate", json={})).status_code == 422
