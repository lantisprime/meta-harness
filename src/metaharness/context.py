"""On-the-fly context optimization for worker calls.

Per memory/knowledge_base/context-engineering-agent-harnesses.md:
- explicit budgets per worker tier (ContextBudget), tighter for weaker models
  (AdaCoM: low-capability workers need MORE aggressive compression);
- prune the middle, keep the edges: the system contract and the final format
  instruction survive untouched; bulky inputs and stale tool observations are
  digested first;
- pruning is loud — a digest always says how much was dropped.

Token counts are estimated (~4 chars/token) — this is a budget governor, not
an exact accountant; the 25% generation reserve absorbs the error.
"""
from __future__ import annotations

from typing import Any

from metaharness.core.types import Tier
from metaharness.tools.registry import digest_text

# usable prompt budget per tier (tokens), before the generation reserve.
TIER_CONTEXT_BUDGET: dict[Tier, int] = {
    Tier.SMALL: 8_000,
    Tier.MID: 16_000,
    Tier.FRONTIER: 32_000,
}
GENERATION_RESERVE = 0.25


def estimate_tokens(text: str) -> int:
    return len(text) // 4 + 1


def messages_tokens(messages: list[dict[str, Any]]) -> int:
    return sum(estimate_tokens(str(m.get("content") or "")) for m in messages)


def budget_for(tier: Tier, override: int | None = None) -> int:
    budget = override or TIER_CONTEXT_BUDGET.get(tier, 8_000)
    return int(budget * (1 - GENERATION_RESERVE))


def fit_messages(messages: list[dict[str, Any]], budget_tokens: int) -> list[dict[str, Any]]:
    """Shrink a message list under budget by digesting the LARGEST prunable
    message first (tool observations, then bulky user inputs). The first
    system message and the final message are never pruned — instructions and
    the immediate question live at the edges and stay verbatim."""
    if messages_tokens(messages) <= budget_tokens:
        return messages
    result = [dict(m) for m in messages]
    prunable = [
        i for i, m in enumerate(result)
        if not (i == 0 and m.get("role") == "system") and i != len(result) - 1
    ]
    # tool observations are the safest compaction target (Anthropic), so they
    # shrink before user content of the same size
    def _priority(i: int) -> tuple[int, int]:
        role_rank = 0 if result[i].get("role") == "tool" else 1
        return (role_rank, -len(str(result[i].get("content") or "")))

    for i in sorted(prunable, key=_priority):
        if messages_tokens(result) <= budget_tokens:
            break
        content = str(result[i].get("content") or "")
        overshoot = messages_tokens(result) - budget_tokens
        target_chars = max(400, len(content) - overshoot * 4 - 200)
        if target_chars < len(content):
            result[i]["content"] = digest_text(content, target_chars)
    return result
