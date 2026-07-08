"""Model routing: capability matrix, cheapest-capable routing, escalation."""
from metaharness.routing.router import (
    DEFAULT_PRIORS,
    TIER_EST_COST,
    TIER_ORDER,
    CapabilityMatrix,
    Router,
    RoutingDecision,
)

__all__ = [
    "Router",
    "RoutingDecision",
    "CapabilityMatrix",
    "DEFAULT_PRIORS",
    "TIER_ORDER",
    "TIER_EST_COST",
]
