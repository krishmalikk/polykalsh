"""
Advisor schemas for chat messages and trade recommendations.
"""

from datetime import datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    """A single message in the conversation."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    role: Literal["user", "assistant", "system"]
    content: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    # Optional tool call info
    tool_calls: list[dict] | None = None
    tool_results: list[dict] | None = None


class TradeRecommendation(BaseModel):
    """A trade recommendation from the advisor."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    market_ticker: str
    market_title: str
    event_ticker: str | None = None

    # Trade details
    side: Literal["YES", "NO"]
    action: Literal["BUY", "SELL"] = "BUY"

    # Analysis
    probability_estimate: float = Field(ge=0.0, le=1.0, description="AI's probability estimate")
    current_price: float = Field(ge=0.0, le=1.0, description="Current market price")
    edge: float = Field(description="Estimated edge (probability - price)")

    # Position sizing
    suggested_contracts: int = Field(ge=1, description="Number of contracts")
    suggested_amount: float = Field(ge=0.0, description="Total cost in USD")

    # Reasoning
    reasoning: str
    risks: list[str] = Field(default_factory=list)

    # Status
    status: Literal["pending", "confirmed", "rejected", "executed", "failed"] = "pending"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    executed_at: datetime | None = None
    order_id: str | None = None
    error_message: str | None = None


class PortfolioSummary(BaseModel):
    """Summary of the user's portfolio."""

    total_value: float
    available_cash: float
    positions_value: float
    open_positions_count: int
    pending_recommendations_count: int = 0


class MarketSummary(BaseModel):
    """Summary of a market for display."""

    ticker: str
    title: str
    event_ticker: str | None = None
    yes_price: float
    no_price: float
    spread: float
    volume_24h: int
    open_interest: int | None = None


class AdvisorConfig(BaseModel):
    """Configuration for the advisor."""

    # Position sizing rules
    max_position_pct: float = 0.05  # Max 5% of balance per trade
    max_deployment_pct: float = 0.40  # Max 40% of cash deployed
    min_edge: float = 0.05  # Minimum 5 cent edge

    # Market filters
    min_volume_24h: int = 100
    min_price: float = 0.10
    max_price: float = 0.90

    # Search limits
    markets_to_scan: int = 50
    recommendations_per_request: int = 5


class AutoAdvisorConfig(BaseModel):
    """Configuration for the auto-advisor."""

    # Refresh settings
    refresh_interval_seconds: int = 300  # 5 minutes
    max_recommendations: int = 5

    # Position sizing rules
    max_position_pct: float = 0.05  # Max 5% of balance per trade
    max_deployment_pct: float = 0.40  # Max 40% of cash deployed
    min_edge: float = 0.05  # Minimum 5 cent edge

    # Relaxed market filters (to avoid "no open markets")
    min_volume_24h: int = 500  # Lower than hybrid (1000)
    min_price: float = 0.08  # Wider range than default
    max_price: float = 0.92
    min_hours_to_close: float = 2.0  # Lower than hybrid (4.0)
    max_days_to_expiry: int = 60  # Higher than hybrid (30)
    max_spread_pct: float = 0.20  # More permissive than hybrid (0.15)

    # Time-based prioritization
    prioritize_short_term: bool = True  # Prefer markets closing sooner


class AutoRecommendation(BaseModel):
    """Auto-generated recommendation with full analysis."""

    id: str = Field(default_factory=lambda: str(uuid4()))

    # Market info
    market_ticker: str
    market_title: str
    event_ticker: str
    event_title: str

    # Current pricing
    yes_price: float
    no_price: float
    spread: float
    volume_24h: int

    # AI Analysis
    side: Literal["YES", "NO"]
    probability_estimate: float = Field(ge=0.0, le=1.0)
    edge: float
    confidence: Literal["low", "medium", "high", "very_high"]

    # Position sizing
    suggested_contracts: int
    suggested_amount: float
    profit_potential: float  # If correct: (1.0 - price) * contracts
    max_loss: float  # price * contracts

    # Reasoning
    reasoning: str
    bull_case: str
    bear_case: str

    # Risk Assessment
    risk_level: Literal["low", "medium", "high"]
    risks: list[str] = Field(default_factory=list)
    risk_details: str

    # Timing
    hours_until_close: float | None = None
    urgency: Literal["low", "medium", "high"] = "low"

    # Categorization for filtering
    category: str = "Other"  # e.g., "Sports", "Politics", "Economics", etc.
    time_bucket: Literal["today", "this_week", "this_month", "long_term"] = "long_term"

    # Status
    status: Literal["pending", "confirmed", "rejected", "expired"] = "pending"
    created_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime | None = None
    order_id: str | None = None
    error_message: str | None = None


class RecommendationsCache(BaseModel):
    """Cache metadata for auto-recommendations."""

    recommendations: list[AutoRecommendation] = Field(default_factory=list)
    generated_at: datetime | None = None
    expires_at: datetime | None = None
    balance_at_generation: float = 0.0
    is_stale: bool = False
    analysis_status: Literal["idle", "analyzing", "complete", "error"] = "idle"
    error_message: str | None = None
