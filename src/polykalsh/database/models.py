"""
SQLAlchemy ORM models for Polykalsh.

Tables:
- leaders: Tracked Polymarket wallets
- leader_positions: Positions held by leaders
- copied_trades: Our copy trades (paper and live)
- safety_guard_logs: Audit trail for safety guard activations
- kalshi_markets: Cached Kalshi market data
- kalshi_recommendations: Scored Kalshi opportunities
- discord_notifications: Notification log
- system_health: Health check tracking
"""

import enum
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base class for all models."""

    pass


class TradingMode(enum.Enum):
    """Trading mode: paper or live."""

    PAPER = "paper"
    LIVE = "live"


class TradeStatus(enum.Enum):
    """Trade execution status."""

    PENDING = "pending"
    FILLED = "filled"
    PARTIAL = "partial"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    SKIPPED = "skipped"


class HealthStatus(enum.Enum):
    """System health status."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    DOWN = "down"


# ═══════════════════════════════════════════════════════════════════════════════
# POLYMARKET COPY-TRADER TABLES
# ═══════════════════════════════════════════════════════════════════════════════


class Leader(Base):
    """Tracked Polymarket leaderboard wallets."""

    __tablename__ = "leaders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    wallet_address: Mapped[str] = mapped_column(String(42), unique=True, nullable=False)
    username: Mapped[Optional[str]] = mapped_column(String(100))
    profile_image: Mapped[Optional[str]] = mapped_column(String(500))
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)

    # Discovery metadata
    discovered_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    discovery_rank: Mapped[Optional[int]] = mapped_column(Integer)
    discovery_pnl: Mapped[Optional[float]] = mapped_column(Float)
    discovery_volume: Mapped[Optional[float]] = mapped_column(Float)
    discovery_period: Mapped[Optional[str]] = mapped_column(String(20))

    # Tracking state
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_checked_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    last_trade_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    # Performance tracking (since we started tracking)
    total_pnl_tracked: Mapped[float] = mapped_column(Float, default=0.0)
    total_trades_tracked: Mapped[int] = mapped_column(Integer, default=0)
    wins: Mapped[int] = mapped_column(Integer, default=0)
    losses: Mapped[int] = mapped_column(Integer, default=0)

    # Relationships
    positions: Mapped[list["LeaderPosition"]] = relationship(
        "LeaderPosition", back_populates="leader", cascade="all, delete-orphan"
    )
    copied_trades: Mapped[list["CopiedTrade"]] = relationship(
        "CopiedTrade", back_populates="leader"
    )

    @property
    def win_rate(self) -> Optional[float]:
        """Calculate win rate."""
        total = self.wins + self.losses
        if total == 0:
            return None
        return self.wins / total

    @property
    def display_name(self) -> str:
        """Get display name (username or truncated address)."""
        if self.username:
            return self.username
        return f"{self.wallet_address[:6]}...{self.wallet_address[-4:]}"


class LeaderPosition(Base):
    """Current positions held by tracked leaders."""

    __tablename__ = "leader_positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    leader_id: Mapped[int] = mapped_column(Integer, ForeignKey("leaders.id"), nullable=False)

    # Position details
    token_id: Mapped[str] = mapped_column(String(100), nullable=False)
    condition_id: Mapped[Optional[str]] = mapped_column(String(100))
    market_slug: Mapped[Optional[str]] = mapped_column(String(200))
    market_title: Mapped[Optional[str]] = mapped_column(String(500))
    outcome: Mapped[str] = mapped_column(String(10))  # YES or NO
    size: Mapped[float] = mapped_column(Float)
    avg_price: Mapped[float] = mapped_column(Float)

    # Timestamps
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_updated: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # State
    is_open: Mapped[bool] = mapped_column(Boolean, default=True)
    exit_price: Mapped[Optional[float]] = mapped_column(Float)
    exit_reason: Mapped[Optional[str]] = mapped_column(String(50))
    exit_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    # Relationship
    leader: Mapped["Leader"] = relationship("Leader", back_populates="positions")


class CopiedTrade(Base):
    """Our copy trades mirroring leaders."""

    __tablename__ = "copied_trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    leader_id: Mapped[int] = mapped_column(Integer, ForeignKey("leaders.id"), nullable=False)
    leader_trade_hash: Mapped[Optional[str]] = mapped_column(String(100))

    # Mode
    mode: Mapped[TradingMode] = mapped_column(Enum(TradingMode), default=TradingMode.PAPER)

    # Trade details
    token_id: Mapped[str] = mapped_column(String(100), nullable=False)
    condition_id: Mapped[Optional[str]] = mapped_column(String(100))
    market_slug: Mapped[Optional[str]] = mapped_column(String(200))
    market_title: Mapped[Optional[str]] = mapped_column(String(500))
    outcome: Mapped[str] = mapped_column(String(10))
    side: Mapped[str] = mapped_column(String(10))  # BUY or SELL
    size_usd: Mapped[float] = mapped_column(Float)
    target_price: Mapped[Optional[float]] = mapped_column(Float)

    # Execution
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    executed_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    order_id: Mapped[Optional[str]] = mapped_column(String(100))
    status: Mapped[TradeStatus] = mapped_column(Enum(TradeStatus), default=TradeStatus.PENDING)
    fill_price: Mapped[Optional[float]] = mapped_column(Float)
    fill_size: Mapped[Optional[float]] = mapped_column(Float)
    error_message: Mapped[Optional[str]] = mapped_column(Text)

    # P&L tracking
    is_closed: Mapped[bool] = mapped_column(Boolean, default=False)
    close_price: Mapped[Optional[float]] = mapped_column(Float)
    close_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    close_reason: Mapped[Optional[str]] = mapped_column(String(50))
    pnl_usd: Mapped[Optional[float]] = mapped_column(Float)

    # Safety guard that triggered skip (if any)
    guard_triggered: Mapped[Optional[str]] = mapped_column(String(50))
    guard_reason: Mapped[Optional[str]] = mapped_column(Text)

    # Confidence score at time of trade
    confidence_score: Mapped[Optional[float]] = mapped_column(Float)

    # Relationship
    leader: Mapped["Leader"] = relationship("Leader", back_populates="copied_trades")


class SafetyGuardLog(Base):
    """Audit log for safety guard activations."""

    __tablename__ = "safety_guard_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    guard_name: Mapped[str] = mapped_column(String(50), nullable=False)
    action_taken: Mapped[str] = mapped_column(String(20))  # blocked, reduced, exited
    trigger_reason: Mapped[str] = mapped_column(Text)

    # Context (JSON blob)
    context: Mapped[Optional[str]] = mapped_column(Text)

    # Related trade (if applicable)
    trade_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("copied_trades.id"))


# ═══════════════════════════════════════════════════════════════════════════════
# KALSHI ADVISOR TABLES
# ═══════════════════════════════════════════════════════════════════════════════


class KalshiMarket(Base):
    """Cached Kalshi market data."""

    __tablename__ = "kalshi_markets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(500))
    subtitle: Mapped[Optional[str]] = mapped_column(String(500))

    # Event/series context
    event_ticker: Mapped[Optional[str]] = mapped_column(String(50))
    series_ticker: Mapped[Optional[str]] = mapped_column(String(50))
    category: Mapped[Optional[str]] = mapped_column(String(50))

    # Market state
    status: Mapped[str] = mapped_column(String(20))  # open, closed, settled
    close_time: Mapped[Optional[datetime]] = mapped_column(DateTime)
    settle_time: Mapped[Optional[datetime]] = mapped_column(DateTime)
    result: Mapped[Optional[str]] = mapped_column(String(10))  # YES, NO (after settlement)

    # Market data (from last scan)
    last_scanned: Mapped[Optional[datetime]] = mapped_column(DateTime)
    yes_bid: Mapped[Optional[float]] = mapped_column(Float)
    yes_ask: Mapped[Optional[float]] = mapped_column(Float)
    no_bid: Mapped[Optional[float]] = mapped_column(Float)
    no_ask: Mapped[Optional[float]] = mapped_column(Float)
    last_price: Mapped[Optional[float]] = mapped_column(Float)
    volume: Mapped[Optional[int]] = mapped_column(Integer)
    volume_24h: Mapped[Optional[int]] = mapped_column(Integer)
    open_interest: Mapped[Optional[int]] = mapped_column(Integer)

    # Relationships
    recommendations: Mapped[list["KalshiRecommendation"]] = relationship(
        "KalshiRecommendation", back_populates="market", cascade="all, delete-orphan"
    )

    @property
    def spread(self) -> Optional[float]:
        """Calculate bid-ask spread."""
        if self.yes_ask is not None and self.yes_bid is not None:
            return self.yes_ask - self.yes_bid
        return None


class KalshiRecommendation(Base):
    """Generated recommendations for Kalshi markets."""

    __tablename__ = "kalshi_recommendations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    market_id: Mapped[int] = mapped_column(Integer, ForeignKey("kalshi_markets.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Scoring factors (0-100 scale)
    ev_edge_score: Mapped[float] = mapped_column(Float)
    liquidity_score: Mapped[float] = mapped_column(Float)
    risk_reward_score: Mapped[float] = mapped_column(Float)
    market_quality_score: Mapped[float] = mapped_column(Float)
    timing_score: Mapped[float] = mapped_column(Float)

    # Aggregate
    total_score: Mapped[float] = mapped_column(Float)
    estimated_edge: Mapped[Optional[float]] = mapped_column(Float)

    # Recommendation
    recommendation_side: Mapped[str] = mapped_column(String(10))  # YES or NO
    confidence: Mapped[str] = mapped_column(String(20))  # low, medium, high, very_high
    recommended_price: Mapped[Optional[float]] = mapped_column(Float)

    # Reasoning (stored as JSON)
    reasoning: Mapped[Optional[str]] = mapped_column(Text)
    risks: Mapped[Optional[str]] = mapped_column(Text)

    # Outcome tracking (after market settles)
    actual_resolution: Mapped[Optional[str]] = mapped_column(String(10))
    resolution_time: Mapped[Optional[datetime]] = mapped_column(DateTime)
    was_correct: Mapped[Optional[bool]] = mapped_column(Boolean)
    simulated_pnl: Mapped[Optional[float]] = mapped_column(Float)

    # Relationship
    market: Mapped["KalshiMarket"] = relationship("KalshiMarket", back_populates="recommendations")


# ═══════════════════════════════════════════════════════════════════════════════
# SHARED TABLES
# ═══════════════════════════════════════════════════════════════════════════════


class DiscordNotification(Base):
    """Log of sent Discord notifications."""

    __tablename__ = "discord_notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    notification_type: Mapped[str] = mapped_column(String(30))
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[Optional[str]] = mapped_column(Text)
    color: Mapped[Optional[int]] = mapped_column(Integer)

    success: Mapped[bool] = mapped_column(Boolean, default=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text)

    # Retry tracking
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    last_retry_at: Mapped[Optional[datetime]] = mapped_column(DateTime)


class SystemHealth(Base):
    """Health check and recovery tracking."""

    __tablename__ = "system_health"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    component: Mapped[str] = mapped_column(String(30))  # copytrader, advisor, dashboard
    status: Mapped[HealthStatus] = mapped_column(Enum(HealthStatus))

    last_successful_run: Mapped[Optional[datetime]] = mapped_column(DateTime)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[Optional[str]] = mapped_column(Text)

    # Extra details (JSON blob)
    details: Mapped[Optional[str]] = mapped_column(Text)


class DailySummary(Base):
    """Daily P&L and activity summary."""

    __tablename__ = "daily_summaries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    date: Mapped[datetime] = mapped_column(DateTime, unique=True)

    # Polymarket copy-trader
    copy_trades_count: Mapped[int] = mapped_column(Integer, default=0)
    copy_wins: Mapped[int] = mapped_column(Integer, default=0)
    copy_losses: Mapped[int] = mapped_column(Integer, default=0)
    copy_pnl_usd: Mapped[float] = mapped_column(Float, default=0.0)
    copy_volume_usd: Mapped[float] = mapped_column(Float, default=0.0)

    # Kalshi advisor
    kalshi_scans: Mapped[int] = mapped_column(Integer, default=0)
    kalshi_recommendations: Mapped[int] = mapped_column(Integer, default=0)
    kalshi_resolutions: Mapped[int] = mapped_column(Integer, default=0)
    kalshi_correct: Mapped[int] = mapped_column(Integer, default=0)

    # System
    uptime_pct: Mapped[Optional[float]] = mapped_column(Float)
    guards_triggered: Mapped[int] = mapped_column(Integer, default=0)
    errors_count: Mapped[int] = mapped_column(Integer, default=0)
