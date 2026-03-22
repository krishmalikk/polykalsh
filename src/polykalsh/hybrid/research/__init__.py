"""
Deep research pipeline.

Uses Perplexity API for real-time research with citations.
"""

from polykalsh.hybrid.research.schemas import (
    KeyFactor,
    ProbabilityEstimate,
    ResearchQuery,
    ResearchResult,
    ResearchType,
    RiskFactor,
    Source,
    SourceCredibility,
)
from polykalsh.hybrid.research.perplexity import (
    PerplexityClient,
    PerplexityError,
    research_event,
)
from polykalsh.hybrid.research.manager import ResearchManager

__all__ = [
    # Schemas
    "KeyFactor",
    "ProbabilityEstimate",
    "ResearchQuery",
    "ResearchResult",
    "ResearchType",
    "RiskFactor",
    "Source",
    "SourceCredibility",
    # Client
    "PerplexityClient",
    "PerplexityError",
    "research_event",
    # Manager
    "ResearchManager",
]
