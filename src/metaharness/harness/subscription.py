"""Subscription-CLI workers: LLM access through logged-in coding CLIs.

Claude Code (`claude -p`) rides an Anthropic subscription; Codex CLI
(`codex exec`) rides a ChatGPT/OpenAI one. Both answer arbitrary text tasks
headless, so a signed-in CLI is a legitimate LLM provider — no API key stored,
no per-token bill. The CLI's own auth store is the credential; the harness
never sees it.

Mechanically these reuse the CodingAgentWorker adapters (same one-shot
invocation, same parsers) with two differences: a shared scratch workspace
(text tasks produce no artifacts worth keeping) and, for codex, a read-only
sandbox — a subscription LLM worker must answer, not edit.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Optional

from metaharness.core.types import Tier
from metaharness.harness.coding import WORKSPACES_DIR, CodingAgentWorker
from metaharness.identity.keys import KeyPair

# model aliases the CLIs accept; "" = the CLI's configured default
SUBSCRIPTION_CLIS: dict[str, dict[str, Any]] = {
    "claude": {
        "label": "Claude Code (Anthropic subscription)",
        "binary": "claude",
        "auth_paths": ["~/.claude.json", "~/.claude"],
        "login_hint": "run `claude` once and sign in, or `claude login`",
        "models": ["sonnet", "opus", "haiku", "claude-fable-5", "claude-opus-4-8"],
    },
    "codex": {
        "label": "Codex CLI (OpenAI subscription)",
        "binary": "codex",
        "auth_paths": ["~/.codex/auth.json"],
        "login_hint": "run `codex login` and sign in with ChatGPT",
        "models": ["gpt-5.2-codex", "gpt-5.2", "o5"],
    },
}


def subscription_status() -> dict[str, dict[str, Any]]:
    """Install + sign-in state per subscription CLI, for the Settings view.
    Auth detection is a file heuristic (same approach as structure-discovery-
    lab): it can say 'signed in' wrongly after a logout, but the wizard's live
    Test button is the real check."""
    status = {}
    for name, spec in SUBSCRIPTION_CLIS.items():
        path = shutil.which(spec["binary"])
        authenticated = any(
            Path(p).expanduser().exists() for p in spec["auth_paths"]
        ) if path else False
        status[name] = {
            "label": spec["label"],
            "installed": path is not None,
            "path": path,
            "authenticated": authenticated,
            "login_hint": spec["login_hint"],
            "models": spec["models"],
        }
    return status


class SubscriptionWorker(CodingAgentWorker):
    """A tier worker whose completions come from a signed-in CLI."""

    BASE_TIMEOUT_S = 300.0  # answer-only calls need less room than CodingAgentWorker's edits

    def __init__(
        self,
        worker_id: str,
        cli: str,
        model: str = "",
        tier: Tier = Tier.FRONTIER,
        keypair: Optional[KeyPair] = None,
        system_prompt: str = "",
        timeout_s: Optional[float] = None,  # None = task-type-aware default (issue #2)
        **kwargs: Any,
    ) -> None:
        if cli not in SUBSCRIPTION_CLIS:
            raise ValueError(
                f"unknown subscription CLI {cli!r} (known: {sorted(SUBSCRIPTION_CLIS)})")
        timeout_kwargs = {} if timeout_s is None else {"timeout_s": timeout_s}
        super().__init__(
            worker_id,
            cli=cli,
            model=model,
            tier=tier,
            keypair=keypair,
            system_prompt=system_prompt,
            workspace=WORKSPACES_DIR / "subscription-scratch",
            **timeout_kwargs,
            **kwargs,
        )
        # answer, don't edit: codex gets a read-only sandbox (claude's builder
        # adds no write flags, and the scratch cwd contains nothing anyway)
        self.sandbox = "read-only"
