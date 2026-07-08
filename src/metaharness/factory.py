"""Build Runners from durable AgentConfig entries — the bridge between the
config store and the live fleet. Used at serve time (rebuild every configured
agent) and at runtime (agent-add wizard persists, then wires through here).
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from metaharness.config import AgentConfig, HarnessConfig
from metaharness.core.types import Tier
from metaharness.harness import CodingAgentWorker, MockLLMWorker, OpenAICompatWorker
from metaharness.harness.runner import Runner
from metaharness.identity.keys import KeyPair


def build_agent_runner(
    agent: AgentConfig,
    config: HarnessConfig,
    keypair: Optional[KeyPair] = None,
    salt_path: Optional[Path] = None,
) -> Runner:
    """One configured agent -> one signed Runner. Raises ValueError on a
    definition that cannot be built (unknown kind, missing endpoint) — a bad
    config entry must fail loudly at wire time, not produce a dead worker."""
    keypair = keypair or KeyPair.generate()
    tier = Tier(agent.tier)

    if agent.kind == "openai_compat":
        base_url, api_key = config.resolve_endpoint(agent, salt_path)
        if not base_url:
            raise ValueError(
                f"agent '{agent.worker_id}': no endpoint (set provider or base_url)"
            )
        return OpenAICompatWorker(
            agent.worker_id,
            base_url=base_url,
            model=agent.model,
            tier=tier,
            api_key=api_key,
            keypair=keypair,
            temperature=agent.temperature,
            max_tokens=agent.max_tokens,
            thinking=agent.thinking,
            system_prompt=agent.system_prompt,
        )

    if agent.kind == "coding_cli":
        if not agent.cli:
            raise ValueError(f"agent '{agent.worker_id}': coding_cli needs 'cli'")
        return CodingAgentWorker(
            agent.worker_id,
            cli=agent.cli,
            model=agent.model,
            tier=tier,
            keypair=keypair,
            system_prompt=agent.system_prompt,
        )

    if agent.kind == "mock":
        return MockLLMWorker(agent.worker_id, tier, keypair=keypair)

    raise ValueError(f"agent '{agent.worker_id}': unknown kind '{agent.kind}'")
