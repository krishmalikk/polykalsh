"""
Base strategy interface for trading strategies.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from polykalsh.hybrid.portfolio.optimizer import StrategyType


class SignalType(str, Enum):
    """Type of trading signal."""

    ENTRY = "entry"  # Open new position
    EXIT = "exit"  # Close existing position
    ADJUST = "adjust"  # Modify existing position


class SignalStrength(str, Enum):
    """Strength of signal."""

    STRONG = "strong"
    MODERATE = "moderate"
    WEAK = "weak"


@dataclass
class MarketData:
    """Market data for strategy evaluation."""

    # Identifiers
    event_ticker: str
    market_ticker: str
    event_title: str
    market_title: str

    # Prices (0-1 scale)
    yes_price: float
    no_price: float
    yes_bid: float
    yes_ask: float
    no_bid: float
    no_ask: float

    # Volume and liquidity
    volume_24h: int = 0
    open_interest: int = 0
    liquidity: int = 0

    # Timing
    close_time: datetime | None = None
    hours_until_close: float | None = None

    # Computed
    @property
    def spread(self) -> float:
        """Bid-ask spread for YES side."""
        return self.yes_ask - self.yes_bid

    @property
    def spread_pct(self) -> float:
        """Spread as percentage of mid price."""
        mid = (self.yes_ask + self.yes_bid) / 2
        if mid <= 0:
            return 1.0
        return self.spread / mid

    @property
    def mid_price(self) -> float:
        """Mid price for YES."""
        return (self.yes_ask + self.yes_bid) / 2


@dataclass
class Signal:
    """Trading signal from a strategy."""

    # Signal type
    signal_type: SignalType
    strategy: StrategyType
    strength: SignalStrength

    # Market
    market_ticker: str
    event_ticker: str

    # Trade details
    side: str  # "YES" or "NO"
    target_price: float  # Limit price
    urgency: float = 0.5  # 0-1, higher = more urgent

    # Sizing inputs (for portfolio optimizer)
    probability_estimate: float | None = None
    confidence: float | None = None
    edge: float | None = None

    # Reasoning
    reason: str = ""
    factors: list[str] = field(default_factory=list)

    # Metadata
    created_at: datetime = field(default_factory=datetime.utcnow)
    expires_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_entry(self) -> bool:
        return self.signal_type == SignalType.ENTRY

    @property
    def is_exit(self) -> bool:
        return self.signal_type == SignalType.EXIT

    @property
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return datetime.utcnow() > self.expires_at


@dataclass
class StrategyContext:
    """Context passed to strategies for evaluation."""

    # Market data
    market: MarketData

    # Research (if available)
    research_probability: float | None = None
    research_confidence: float | None = None
    bullish_factors: list[str] = field(default_factory=list)
    bearish_factors: list[str] = field(default_factory=list)

    # Ensemble decision (if available)
    ensemble_action: str | None = None  # "BUY_YES", "BUY_NO", etc.
    ensemble_probability: float | None = None
    ensemble_confidence: float | None = None
    ensemble_edge: float | None = None

    # Current position (if any)
    has_position: bool = False
    position_side: str | None = None
    position_contracts: int = 0
    position_entry_price: float = 0.0
    position_pnl_pct: float = 0.0


class BaseStrategy(ABC):
    """
    Abstract base class for trading strategies.

    Each strategy evaluates market opportunities and generates signals.
    """

    def __init__(self, strategy_type: StrategyType):
        self.strategy_type = strategy_type

    @property
    @abstractmethod
    def name(self) -> str:
        """Strategy name."""
        pass

    @abstractmethod
    def evaluate(self, context: StrategyContext) -> list[Signal]:
        """
        Evaluate market and generate signals.

        Args:
            context: Market data and research context

        Returns:
            List of trading signals (can be empty)
        """
        pass

    def should_skip_market(self, context: StrategyContext) -> tuple[bool, str]:
        """
        Check if market should be skipped.

        Override in subclasses for strategy-specific filters.

        Returns:
            Tuple of (should_skip, reason)
        """
        # Default checks
        if context.market.hours_until_close is not None:
            if context.market.hours_until_close < 1.0:
                return True, "too_close_to_expiry"

        if context.market.volume_24h < 100:
            return True, "low_volume"

        if context.market.spread_pct > 0.20:
            return True, "wide_spread"

        return False, ""

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(type={self.strategy_type.value})"
