"""Harness self-optimization: the Meta-Harness outer loop (arXiv 2603.28052)
applied to this harness's own enrichment stack. See loop.py for the shape."""
from metaharness.optimization.ledger import Candidate, CandidateLedger, CandidateScores
from metaharness.optimization.loop import HarnessOptimizer, OptimizationReport
from metaharness.optimization.params import HarnessParams, PromptDirectives
from metaharness.optimization.proposer import (
    LLMProposer,
    Proposal,
    ProposalError,
    RuleProposer,
    proposer_context,
)
from metaharness.optimization.suites import SUITE_NAMES, search_and_holdout

__all__ = [
    "Candidate",
    "CandidateLedger",
    "CandidateScores",
    "HarnessOptimizer",
    "HarnessParams",
    "LLMProposer",
    "OptimizationReport",
    "Proposal",
    "ProposalError",
    "PromptDirectives",
    "RuleProposer",
    "SUITE_NAMES",
    "proposer_context",
    "search_and_holdout",
]
