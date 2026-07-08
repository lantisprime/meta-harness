"""Durable harness configuration: providers, agents, MCP servers, settings.

Everything the serve command needs to rebuild the fleet lives in one file,
``~/.metaharness/config.json`` (chmod 0600). API keys are stored XOR-obfuscated
against a machine-local random salt (``~/.metaharness/.keysalt``) — an
anti-casual-exposure measure, not vault-grade crypto — and are NEVER sent over
HTTP in plaintext: every outbound representation goes through :func:`mask_key`.

Provider protocol note: every supported remote provider (Anthropic, OpenAI,
OpenRouter, Groq, DeepSeek, Mistral, …) exposes an OpenAI-compatible
chat-completions endpoint, so configured LLM agents all ride
:class:`~metaharness.harness.local.OpenAICompatWorker`; only base_url, key and
model differ. Local servers (Ollama, LM Studio) are the same thing without a key.
"""
from __future__ import annotations

import base64
import json
import os
import secrets
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field

CONFIG_DIR = Path.home() / ".metaharness"
CONFIG_PATH = CONFIG_DIR / "config.json"
SALT_PATH = CONFIG_DIR / ".keysalt"

_ENC_PREFIX = "enc1:"
_MASK_CHAR = "…"  # '…' — its presence marks a value as masked, not real


# ---------------------------------------------------------------- obfuscation

def _salt(salt_path: Path) -> bytes:
    if salt_path.exists():
        return salt_path.read_bytes()
    salt_path.parent.mkdir(parents=True, exist_ok=True)
    salt = secrets.token_bytes(64)
    salt_path.write_bytes(salt)
    os.chmod(salt_path, 0o600)
    return salt


def obfuscate(plain: str, salt_path: Optional[Path] = None) -> str:
    salt_path = salt_path or SALT_PATH
    if not plain or plain.startswith(_ENC_PREFIX):
        return plain
    salt = _salt(salt_path)
    raw = plain.encode()
    mixed = bytes(b ^ salt[i % len(salt)] for i, b in enumerate(raw))
    return _ENC_PREFIX + base64.b64encode(mixed).decode()


def deobfuscate(value: str, salt_path: Optional[Path] = None) -> str:
    salt_path = salt_path or SALT_PATH
    if not value or not value.startswith(_ENC_PREFIX):
        return value  # legacy/plaintext passes through; re-saved obfuscated
    salt = _salt(salt_path)
    raw = base64.b64decode(value[len(_ENC_PREFIX):])
    return bytes(b ^ salt[i % len(salt)] for i, b in enumerate(raw)).decode()


def mask_key(plain: str) -> Optional[str]:
    """first3…last2, or 'set' when too short to show anything safely."""
    if not plain:
        return None
    return f"{plain[:3]}{_MASK_CHAR}{plain[-2:]}" if len(plain) > 8 else "set"


def is_masked(value: str) -> bool:
    """A round-tripped masked value must never overwrite the stored key."""
    return bool(value) and _MASK_CHAR in value


# --------------------------------------------------------------------- models

class ProviderConfig(BaseModel):
    """One LLM API endpoint the harness can draw agents from."""

    id: str
    label: str = ""
    base_url: str
    api_key: str = ""          # stored obfuscated (enc1:…)
    default_model: str = ""
    keyless: bool = False      # local servers: probe instead of authenticate

    def plain_key(self, salt_path: Optional[Path] = None) -> str:
        return deobfuscate(self.api_key, salt_path)


class AgentConfig(BaseModel):
    """One durable agent definition — rebuilt into a Runner at every serve."""

    worker_id: str
    kind: str = "openai_compat"          # openai_compat | coding_cli | mock
    tier: str = "small"
    provider: str = ""                   # ProviderConfig.id ref ("" = direct)
    base_url: str = ""                   # direct endpoint when no provider ref
    model: str = ""
    system_prompt: str = ""
    task_types: list[str] = Field(default_factory=list)
    temperature: float = 0.2
    max_tokens: Optional[int] = 4000
    thinking: Optional[bool] = None
    cli: str = ""                        # coding_cli kind: codex|opencode|claude|pi
    enabled: bool = True


class MCPServerConfig(BaseModel):
    """An MCP server workflows can pull tools from (mcpServers convention)."""

    name: str
    transport: str = "stdio"             # stdio | http
    command: str = ""
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    url: str = ""
    enabled: bool = True


class HarnessConfig(BaseModel):
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    agents: list[AgentConfig] = Field(default_factory=list)
    mcp_servers: dict[str, MCPServerConfig] = Field(default_factory=dict)
    settings: dict[str, Any] = Field(default_factory=dict)

    # ------------------------------------------------------------ persistence

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "HarnessConfig":
        path = path or CONFIG_PATH
        if not path.exists():
            return cls()
        return cls.model_validate(json.loads(path.read_text()))

    def save(self, path: Optional[Path] = None, salt_path: Optional[Path] = None) -> None:
        path = path or CONFIG_PATH
        for provider in self.providers.values():
            provider.api_key = obfuscate(provider.api_key, salt_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.model_dump(), indent=2))
        os.chmod(path, 0o600)

    # -------------------------------------------------------------- accessors

    def get_api_key(self, provider_id: str, salt_path: Optional[Path] = None) -> str:
        """Plaintext key for in-process use only — never over HTTP."""
        provider = self.providers.get(provider_id)
        return provider.plain_key(salt_path) if provider else ""

    def agent(self, worker_id: str) -> Optional[AgentConfig]:
        return next((a for a in self.agents if a.worker_id == worker_id), None)

    def upsert_agent(self, agent: AgentConfig) -> None:
        self.agents = [a for a in self.agents if a.worker_id != agent.worker_id]
        self.agents.append(agent)

    def remove_agent(self, worker_id: str) -> bool:
        before = len(self.agents)
        self.agents = [a for a in self.agents if a.worker_id != worker_id]
        return len(self.agents) != before

    def resolve_endpoint(self, agent: AgentConfig,
                         salt_path: Optional[Path] = None) -> tuple[str, str]:
        """(base_url, plaintext_api_key) for an agent, via provider ref or direct."""
        if agent.provider and agent.provider in self.providers:
            provider = self.providers[agent.provider]
            return provider.base_url, provider.plain_key(salt_path)
        return agent.base_url, ""

    # ----------------------------------------------------------- API surface

    def public_dict(self, salt_path: Optional[Path] = None) -> dict[str, Any]:
        """The only representation that may cross HTTP: keys masked."""
        data = self.model_dump()
        for pid, provider in data["providers"].items():
            plain = self.providers[pid].plain_key(salt_path)
            provider["api_key"] = mask_key(plain)
            provider["configured"] = bool(plain) or self.providers[pid].keyless
        return data

    def apply_provider_update(self, pid: str, patch: dict[str, Any],
                              salt_path: Optional[Path] = None) -> ProviderConfig:
        """Merge a provider patch from the API, ignoring masked key echoes."""
        current = self.providers.get(pid)
        merged = current.model_dump() if current else {"id": pid, "base_url": ""}
        for key, value in patch.items():
            if key == "api_key":
                if value is None or is_masked(str(value)):
                    continue  # masked echo: keep the stored key
                value = obfuscate(str(value), salt_path)
            if key in ProviderConfig.model_fields:
                merged[key] = value
        provider = ProviderConfig.model_validate(merged)
        self.providers[pid] = provider
        return provider


# ------------------------------------------------------------------- catalog

# Known providers, all OpenAI-compatible. `get` is the human key-signup URL
# surfaced in the wizard; models are suggestions, not restrictions.
PROVIDER_CATALOG: list[dict[str, Any]] = [
    {"id": "anthropic", "label": "Anthropic", "base_url": "https://api.anthropic.com/v1",
     "get": "https://console.anthropic.com/settings/keys",
     "models": ["claude-fable-5", "claude-opus-4-8", "claude-sonnet-5", "claude-haiku-4-5-20251001"]},
    {"id": "openai", "label": "OpenAI", "base_url": "https://api.openai.com/v1",
     "get": "https://platform.openai.com/api-keys", "models": []},
    {"id": "openrouter", "label": "OpenRouter", "base_url": "https://openrouter.ai/api/v1",
     "get": "https://openrouter.ai/keys", "models": []},
    {"id": "groq", "label": "Groq", "base_url": "https://api.groq.com/openai/v1",
     "get": "https://console.groq.com/keys", "models": []},
    {"id": "deepseek", "label": "DeepSeek", "base_url": "https://api.deepseek.com/v1",
     "get": "https://platform.deepseek.com/api_keys", "models": []},
    {"id": "mistral", "label": "Mistral", "base_url": "https://api.mistral.ai/v1",
     "get": "https://console.mistral.ai/api-keys", "models": []},
    {"id": "neuralwatt", "label": "NeuralWatt", "base_url": "https://api.neuralwatt.com/v1",
     "get": "https://portal.neuralwatt.com",
     "models": ["qwen3.5-397b", "qwen3.5-397b-fast", "glm-5.2", "glm-5.2-fast",
                "kimi-k2.7-code", "kimi-k2.6", "kimi-k2.6-fast", "qwen3.6-35b"]},
    {"id": "ollama", "label": "Ollama (local)", "base_url": "http://localhost:11434/v1",
     "get": "", "models": [], "keyless": True},
    {"id": "lmstudio", "label": "LM Studio (local)", "base_url": "http://localhost:1234/v1",
     "get": "", "models": [], "keyless": True},
    {"id": "custom", "label": "Custom OpenAI-compatible", "base_url": "",
     "get": "", "models": []},
]
