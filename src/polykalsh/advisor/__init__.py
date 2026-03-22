"""
Kalshi Market Advisor module.

Interactive chat-based advisor for Kalshi prediction market trading.
Also includes auto-advisor for automatic recommendation generation.
"""

from polykalsh.advisor.chat import ChatAdvisor
from polykalsh.advisor.auto_advisor import AutoAdvisor
from polykalsh.advisor.schemas import (
    ChatMessage,
    TradeRecommendation,
    PortfolioSummary,
    MarketSummary,
    AdvisorConfig,
    AutoAdvisorConfig,
    AutoRecommendation,
    RecommendationsCache,
)
from polykalsh.advisor.tools import ADVISOR_TOOLS, ADVISOR_SYSTEM_PROMPT, AUTO_ANALYSIS_SYSTEM_PROMPT

__all__ = [
    "ChatAdvisor",
    "AutoAdvisor",
    "ChatMessage",
    "TradeRecommendation",
    "PortfolioSummary",
    "MarketSummary",
    "AdvisorConfig",
    "AutoAdvisorConfig",
    "AutoRecommendation",
    "RecommendationsCache",
    "ADVISOR_TOOLS",
    "ADVISOR_SYSTEM_PROMPT",
    "AUTO_ANALYSIS_SYSTEM_PROMPT",
]
