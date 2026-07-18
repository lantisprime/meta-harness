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
from urllib.parse import urlparse

from pydantic import BaseModel, Field, model_validator

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
    roles: list[str] = Field(default_factory=list)
    # selflearn specialist binding: knowledge packs retrieved into this
    # agent's task prompts (docs/self-learning-specialist-agents-plan.md).
    knowledge_packs: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    temperature: float = 0.2
    max_tokens: Optional[int] = 4000
    thinking: Optional[bool] = None
    cli: str = ""                        # coding_cli kind: codex|opencode|claude|pi
    enabled: bool = True
    # None = kind default (task-type-aware for coding CLIs, issue #2). Bounded:
    # gt=0 alone accepts +Infinity, and :g renders >=1e6 as scientific notation
    # (issue #2 panel, Claude+codex+kimi P2) — 24h is the sane ceiling.
    timeout_s: Optional[float] = Field(default=None, gt=0, le=86400,
                                       allow_inf_nan=False)


class MCPServerConfig(BaseModel):
    """An MCP server workflows can pull tools from (mcpServers convention)."""

    name: str
    transport: str = "stdio"             # stdio | http
    command: str = ""
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    url: str = ""
    oauth_token: str = ""                 # stored obfuscated (enc1:…)
    oauth_project: str = ""               # Google quota/billing project id
    enabled: bool = True

    @model_validator(mode="after")
    def validate_remote_auth(self) -> "MCPServerConfig":
        allowed_oauth_urls = {
            "https://gmailmcp.googleapis.com/mcp/v1",
            "https://calendarmcp.googleapis.com/mcp/v1",
        }
        if self.transport == "http":
            parsed = urlparse(self.url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError("HTTP MCP servers need a valid http(s) URL")
        if self.oauth_token:
            if self.url not in allowed_oauth_urls:
                raise ValueError("OAuth tokens are only supported for pinned Google Workspace MCP endpoints")
            if not self.oauth_project:
                raise ValueError("Google Workspace OAuth needs a project id")
        if self.oauth_project and self.url not in allowed_oauth_urls:
            raise ValueError("OAuth project ids are only sent to pinned Google Workspace MCP endpoints")
        return self

    def plain_env(self, salt_path: Optional[Path] = None) -> dict[str, str]:
        return {key: deobfuscate(value, salt_path) for key, value in self.env.items()}

    def plain_oauth_token(self, salt_path: Optional[Path] = None) -> str:
        return deobfuscate(self.oauth_token, salt_path)


class HarnessConfig(BaseModel):
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    agents: list[AgentConfig] = Field(default_factory=list)
    mcp_servers: dict[str, MCPServerConfig] = Field(default_factory=dict)
    # Logical binding -> obfuscated local value. Public surfaces replace this
    # whole mapping with configured-name status; values are write-only over HTTP.
    secret_bindings: dict[str, str] = Field(default_factory=dict)
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
        for server in self.mcp_servers.values():
            server.env = {
                key: obfuscate(value, salt_path) for key, value in server.env.items()
            }
            server.oauth_token = obfuscate(server.oauth_token, salt_path)
        self.secret_bindings = {
            name: obfuscate(value, salt_path)
            for name, value in self.secret_bindings.items()
        }
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

    def set_secret_binding(
        self, name: str, plaintext: str, salt_path: Optional[Path] = None
    ) -> None:
        """Store one validated logical binding without retaining plaintext."""
        from metaharness.blueprints.secrets import validate_secret_binding_name

        binding = validate_secret_binding_name(name)
        if not plaintext:
            raise ValueError("secret binding value cannot be empty")
        if plaintext.startswith(_ENC_PREFIX):
            raise ValueError("secret binding values must be plaintext writes")
        self.secret_bindings[binding] = obfuscate(plaintext, salt_path)

    def hydrate_secret_bindings(self, registry: Any,
                                salt_path: Optional[Path] = None) -> None:
        """Load configured values into a callback-only runtime registry."""
        for name, value in self.secret_bindings.items():
            registry.configure(name, deobfuscate(value, salt_path))

    # ----------------------------------------------------------- API surface

    def public_dict(self, salt_path: Optional[Path] = None) -> dict[str, Any]:
        """The only representation that may cross HTTP: keys masked."""
        data = self.model_dump()
        for pid, provider in data["providers"].items():
            plain = self.providers[pid].plain_key(salt_path)
            provider["api_key"] = mask_key(plain)
            provider["configured"] = bool(plain) or self.providers[pid].keyless
        for name, server in data["mcp_servers"].items():
            model = self.mcp_servers[name]
            server["env"] = {
                key: mask_key(value) for key, value in model.plain_env(salt_path).items()
            }
            token = model.plain_oauth_token(salt_path)
            server["oauth_token"] = mask_key(token)
            server["authenticated"] = bool(token)
        data["secret_bindings"] = {
            name: {"configured": bool(deobfuscate(value, salt_path))}
            for name, value in sorted(self.secret_bindings.items())
        }
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
    {"id": "minimax", "label": "MiniMax", "base_url": "https://api.minimax.io/v1",
     "get": "https://platform.minimax.io",
     "models": ["MiniMax-M3", "MiniMax-M2.7", "MiniMax-M2.7-highspeed",
                "MiniMax-M2.5", "MiniMax-M2.5-highspeed", "MiniMax-M2.1"]},
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
