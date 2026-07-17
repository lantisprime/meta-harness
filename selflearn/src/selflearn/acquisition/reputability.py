"""Deterministic source-reputability policy: tiered domain/channel lists.

Not model judgment — a configurable lookup. Unknown origins are fetchable
during gather but their claims cannot be sole support for a published entry
(enforced by the verification module's corroboration rule).
"""
from __future__ import annotations

from dataclasses import dataclass, field


def registrable_domain(url_or_name: str) -> str:
    """Key used for tier lookup and corroboration independence."""
    s = url_or_name.strip().lower()
    if "://" in s:
        s = s.split("/")[2]
    return s.lstrip("www.") if s.startswith("www.") else s


@dataclass(frozen=True)
class ReputabilityPolicy:
    official: frozenset[str] = frozenset()
    primary: frozenset[str] = frozenset()
    community: frozenset[str] = frozenset()
    # channel identities (YouTube etc.) extend the tiers alongside domains
    official_channels: frozenset[str] = frozenset()
    primary_channels: frozenset[str] = frozenset()

    def tier_for(self, url: str) -> str:
        domain = registrable_domain(url)
        if domain in self.official:
            return "official"
        if domain in self.primary:
            return "primary"
        if domain in self.community:
            return "community"
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
    community=frozenset({"stackoverflow.com", "news.ycombinator.com"}),
)
