"""
AI ensemble for prediction market analysis.

5 agents with weighted voting:
- Lead Forecaster (30%) - Primary decision maker
- News Analyst (20%) - Recent news and sentiment
- Bull Researcher (20%) - Case for YES
- Bear Researcher (15%) - Case for NO
- Risk Manager (15%) - Risk identification
"""

from polykalsh.hybrid.ensemble.schemas import (
    AgentResponse,
    AgentRole,
    EnsembleResult,
    MarketContext,
    TradeAction,
    AGENT_SYSTEM_PROMPTS,
    get_agent_prompt,
)
from polykalsh.hybrid.ensemble.base import BaseAgent, AgentError
from polykalsh.hybrid.ensemble.agents import (
    AnthropicAgent,
    OpenRouterAgent,
    MockAgent,
    create_ensemble,
    close_ensemble,
)
from polykalsh.hybrid.ensemble.aggregator import EnsembleAggregator

__all__ = [
    # Schemas
    "AgentResponse",
    "AgentRole",
    "EnsembleResult",
    "MarketContext",
    "TradeAction",
    "AGENT_SYSTEM_PROMPTS",
    "get_agent_prompt",
    # Base
    "BaseAgent",
    "AgentError",
    # Agents
    "AnthropicAgent",
    "OpenRouterAgent",
    "MockAgent",
    "create_ensemble",
    "close_ensemble",
    # Aggregator
    "EnsembleAggregator",
]
