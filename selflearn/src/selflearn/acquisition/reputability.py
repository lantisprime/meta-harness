"""Deterministic source-reputability policy: tiered domain/channel lists.

Not model judgment — a configurable lookup. Unknown origins are fetchable
during gather but their claims cannot be sole support for a published entry
(enforced by the verification module's corroboration rule).
"""
from __future__ import annotations

from dataclasses import dataclass

from selflearn.contracts import registrable_domain

__all__ = ["ReputabilityPolicy", "DEFAULT_POLICY", "registrable_domain"]


@dataclass(frozen=True)
class ReputabilityPolicy:
    official: frozenset[str] = frozenset()
    primary: frozenset[str] = frozenset()
    community: frozenset[str] = frozenset()
    # channel identities (YouTube etc.) extend the tiers alongside domains
    official_channels: frozenset[str] = frozenset()
    primary_channels: frozenset[str] = frozenset()

    def tier_for(self, url: str) -> str:
        # normalize BOTH sides so an operator listing 'www.example.com'
        # still matches 'example.com' and vice versa
        domain = registrable_domain(url)
        for tier, entries in (("official", self.official),
                              ("primary", self.primary),
                              ("community", self.community)):
            if any(registrable_domain(e) == domain for e in entries):
                return tier
        return "unknown"

    def tier_for_channel(self, channel: str) -> str:
        name = channel.strip().lower()
        if name in {c.lower() for c in self.official_channels}:
            return "official"
        if name in {c.lower() for c in self.primary_channels}:
            return "primary"
        return "unknown"


DEFAULT_POLICY = ReputabilityPolicy(
    official=frozenset({"arxiv.org", "docs.python.org", "peps.python.org",
                        "developer.mozilla.org", "www.rfc-editor.org",
                        "rfc-editor.org"}),
    primary=frozenset({"github.com", "anthropic.com", "www.anthropic.com",
                       "openai.com", "www.youtube.com", "youtube.com"}),
    community=frozenset({"stackoverflow.com", "news.ycombinator.com",
                         "en.wikipedia.org", "wikipedia.org"}),
)
