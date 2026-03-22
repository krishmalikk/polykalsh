"""
Dynamic exit management for hybrid trading bot.

Handles all exit conditions:
- Take profit (trailing)
- Stop loss
- Trailing stop (pullback from high)
- Confidence decay
- Time limit
- Expiry approach
- Volatility-based exits
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

import structlog

logger = structlog.get_logger()


class ExitReason(str, Enum):
    """Reason for exit."""

    TAKE_PROFIT = "take_profit"
    STOP_LOSS = "stop_loss"
    TRAILING_STOP = "trailing_stop"
    CONFIDENCE_DECAY = "confidence_decay"
    TIME_LIMIT = "time_limit"
    EXPIRY_APPROACH = "expiry_approach"
    VOLATILITY_EXIT = "volatility_exit"
    MANUAL = "manual"
    MARKET_RESOLVED = "market_resolved"


@dataclass
class PositionState:
    """Current state of a position for exit evaluation."""

    # Identifiers
    market_ticker: str
    event_ticker: str

    # Position details
    side: str  # "YES" or "NO"
    contracts: int
    entry_price: float
    entry_time: datetime
    cost_basis: float

    # Current state
    current_price: float
    current_value: float

    # High water mark for trailing stop
    high_water_mark: float
    high_water_mark_time: datetime | None = None

    # Entry context
    entry_confidence: float = 0.0
    entry_probability: float = 0.0

    # Current ensemble (for confidence decay check)
    current_confidence: float | None = None
    current_probability: float | None = None
    last_confidence_check: datetime | None = None

    # Market timing
    hours_until_close: float | None = None
    market_status: str = "open"

    @property
    def unrealized_pnl(self) -> float:
        """Unrealized P&L in USD."""
        return self.current_value - self.cost_basis

    @property
    def unrealized_pnl_pct(self) -> float:
        """Unrealized P&L as percentage."""
        if self.cost_basis <= 0:
            return 0.0
        return self.unrealized_pnl / self.cost_basis

    @property
    def price_change_pct(self) -> float:
        """Price change from entry."""
        if self.entry_price <= 0:
            return 0.0
        return (self.current_price - self.entry_price) / self.entry_price

    @property
    def drawdown_from_high(self) -> float:
        """Drawdown from high water mark."""
        if self.high_water_mark <= 0:
            return 0.0
        return (self.high_water_mark - self.current_value) / self.high_water_mark

    @property
    def hold_duration_hours(self) -> float:
        """How long we've held the position."""
        delta = datetime.utcnow() - self.entry_time
        return delta.total_seconds() / 3600

    @property
    def hold_duration_days(self) -> float:
        """Hold duration in days."""
        return self.hold_duration_hours / 24


@dataclass
class ExitSignal:
    """Signal to exit a position."""

    # Position
    market_ticker: str
    side: str
    contracts: int

    # Exit details
    reason: ExitReason
    urgency: float  # 0-1, higher = more urgent
    target_price: float | None = None

    # Context
    trigger_value: float = 0.0  # The value that triggered exit
    threshold: float = 0.0  # The threshold that was crossed
    description: str = ""

    # Metadata
    created_at: datetime = field(default_factory=datetime.utcnow)
    metadata: dict[str, Any] = field(default_factory=dict)


class ExitManager:
    """
    Manages exit decisions for all positions.

    Evaluates multiple exit conditions and returns signals
    when positions should be closed.
    """

    def __init__(
        self,
        # Take profit
        take_profit_pct: float = 0.20,
        trailing_take_profit: bool = True,
        # Stop loss
        stop_loss_pct: float = 0.15,
        # Trailing stop
        trailing_stop_enabled: bool = True,
        trailing_pullback_pct: float = 0.25,
        min_profit_for_trailing: float = 0.10,
        # Time limits
        max_hold_days: int = 10,
        exit_hours_before_expiry: float = 4.0,
        # Confidence decay
        confidence_decay_enabled: bool = True,
        confidence_decay_threshold: float = 0.50,
        confidence_recheck_hours: int = 24,
        # Volatility
        volatility_exit_enabled: bool = False,
        volatility_threshold: float = 0.30,
    ):
        """
        Initialize exit manager.

        Args:
            take_profit_pct: Exit when profit exceeds this %
            trailing_take_profit: Use trailing take profit
            stop_loss_pct: Exit when loss exceeds this %
            trailing_stop_enabled: Enable trailing stop
            trailing_pullback_pct: Exit on this % pullback from high
            min_profit_for_trailing: Min profit before trailing activates
            max_hold_days: Maximum hold duration
            exit_hours_before_expiry: Exit this many hours before expiry
            confidence_decay_enabled: Enable confidence decay check
            confidence_decay_threshold: Exit if confidence drops by this %
            confidence_recheck_hours: Hours between confidence checks
            volatility_exit_enabled: Enable volatility-based exits
            volatility_threshold: Exit if price moves this % against us
        """
        # Take profit
        self.take_profit_pct = take_profit_pct
        self.trailing_take_profit = trailing_take_profit

        # Stop loss
        self.stop_loss_pct = stop_loss_pct

        # Trailing stop
        self.trailing_stop_enabled = trailing_stop_enabled
        self.trailing_pullback_pct = trailing_pullback_pct
        self.min_profit_for_trailing = min_profit_for_trailing

        # Time limits
        self.max_hold_days = max_hold_days
        self.exit_hours_before_expiry = exit_hours_before_expiry

        # Confidence decay
        self.confidence_decay_enabled = confidence_decay_enabled
        self.confidence_decay_threshold = confidence_decay_threshold
        self.confidence_recheck_hours = confidence_recheck_hours

        # Volatility
        self.volatility_exit_enabled = volatility_exit_enabled
        self.volatility_threshold = volatility_threshold

    def evaluate(self, position: PositionState) -> ExitSignal | None:
        """
        Evaluate a position for exit conditions.

        Checks all exit conditions in priority order and returns
        the first triggered exit signal.

        Args:
            position: Current position state

        Returns:
            ExitSignal if position should be closed, None otherwise
        """
        # Check in priority order (most urgent first)
        checks = [
            self._check_market_resolved,
            self._check_stop_loss,
            self._check_expiry_approach,
            self._check_trailing_stop,
            self._check_take_profit,
            self._check_time_limit,
            self._check_confidence_decay,
            self._check_volatility,
        ]

        for check in checks:
            signal = check(position)
            if signal:
                logger.info(
                    "exit_signal_generated",
                    market=position.market_ticker,
                    reason=signal.reason.value,
                    pnl_pct=position.unrealized_pnl_pct,
                    trigger_value=signal.trigger_value,
                    threshold=signal.threshold,
                )
                return signal

        return None

    def evaluate_batch(
        self, positions: list[PositionState]
    ) -> list[ExitSignal]:
        """
        Evaluate multiple positions for exits.

        Args:
            positions: List of position states

        Returns:
            List of exit signals for positions that should be closed
        """
        signals = []
        for position in positions:
            signal = self.evaluate(position)
            if signal:
                signals.append(signal)
        return signals

    def _check_market_resolved(self, pos: PositionState) -> ExitSignal | None:
        """Check if market has resolved."""
        if pos.market_status in ("settled", "closed"):
            return ExitSignal(
                market_ticker=pos.market_ticker,
                side=pos.side,
                contracts=pos.contracts,
                reason=ExitReason.MARKET_RESOLVED,
                urgency=1.0,
                description="Market has resolved",
            )
        return None

    def _check_stop_loss(self, pos: PositionState) -> ExitSignal | None:
        """Check if stop loss is triggered."""
        if pos.unrealized_pnl_pct <= -self.stop_loss_pct:
            return ExitSignal(
                market_ticker=pos.market_ticker,
                side=pos.side,
                contracts=pos.contracts,
                reason=ExitReason.STOP_LOSS,
                urgency=0.95,
                target_price=pos.current_price,
                trigger_value=pos.unrealized_pnl_pct,
                threshold=-self.stop_loss_pct,
                description=f"Stop loss triggered at {pos.unrealized_pnl_pct:.1%}",
            )
        return None

    def _check_expiry_approach(self, pos: PositionState) -> ExitSignal | None:
        """Check if market is approaching expiry."""
        if pos.hours_until_close is None:
            return None

        if pos.hours_until_close <= self.exit_hours_before_expiry:
            return ExitSignal(
                market_ticker=pos.market_ticker,
                side=pos.side,
                contracts=pos.contracts,
                reason=ExitReason.EXPIRY_APPROACH,
                urgency=0.90,
                target_price=pos.current_price,
                trigger_value=pos.hours_until_close,
                threshold=self.exit_hours_before_expiry,
                description=f"Expiry in {pos.hours_until_close:.1f} hours",
            )
        return None

    def _check_trailing_stop(self, pos: PositionState) -> ExitSignal | None:
        """Check if trailing stop is triggered."""
        if not self.trailing_stop_enabled:
            return None

        # Only activate after minimum profit achieved
        if pos.unrealized_pnl_pct < self.min_profit_for_trailing:
            return None

        # Check pullback from high
        if pos.drawdown_from_high >= self.trailing_pullback_pct:
            return ExitSignal(
                market_ticker=pos.market_ticker,
                side=pos.side,
                contracts=pos.contracts,
                reason=ExitReason.TRAILING_STOP,
                urgency=0.85,
                target_price=pos.current_price,
                trigger_value=pos.drawdown_from_high,
                threshold=self.trailing_pullback_pct,
                description=f"Trailing stop: {pos.drawdown_from_high:.1%} pullback from high",
                metadata={
                    "high_water_mark": pos.high_water_mark,
                    "current_value": pos.current_value,
                },
            )
        return None

    def _check_take_profit(self, pos: PositionState) -> ExitSignal | None:
        """Check if take profit is triggered."""
        if pos.unrealized_pnl_pct >= self.take_profit_pct:
            # If trailing is enabled, don't exit at fixed take profit
            # (trailing stop will handle exits)
            if self.trailing_take_profit and self.trailing_stop_enabled:
                return None

            return ExitSignal(
                market_ticker=pos.market_ticker,
                side=pos.side,
                contracts=pos.contracts,
                reason=ExitReason.TAKE_PROFIT,
                urgency=0.70,
                target_price=pos.current_price,
                trigger_value=pos.unrealized_pnl_pct,
                threshold=self.take_profit_pct,
                description=f"Take profit at {pos.unrealized_pnl_pct:.1%}",
            )
        return None

    def _check_time_limit(self, pos: PositionState) -> ExitSignal | None:
        """Check if position has exceeded max hold time."""
        if pos.hold_duration_days >= self.max_hold_days:
            return ExitSignal(
                market_ticker=pos.market_ticker,
                side=pos.side,
                contracts=pos.contracts,
                reason=ExitReason.TIME_LIMIT,
                urgency=0.60,
                target_price=pos.current_price,
                trigger_value=pos.hold_duration_days,
                threshold=self.max_hold_days,
                description=f"Max hold time ({self.max_hold_days} days) exceeded",
            )
        return None

    def _check_confidence_decay(self, pos: PositionState) -> ExitSignal | None:
        """Check if ensemble confidence has decayed significantly."""
        if not self.confidence_decay_enabled:
            return None

        if pos.current_confidence is None or pos.entry_confidence <= 0:
            return None

        # Check if enough time has passed since last check
        if pos.last_confidence_check is not None:
            hours_since_check = (
                datetime.utcnow() - pos.last_confidence_check
            ).total_seconds() / 3600
            if hours_since_check < self.confidence_recheck_hours:
                return None

        # Calculate decay
        decay = (pos.entry_confidence - pos.current_confidence) / pos.entry_confidence

        if decay >= self.confidence_decay_threshold:
            return ExitSignal(
                market_ticker=pos.market_ticker,
                side=pos.side,
                contracts=pos.contracts,
                reason=ExitReason.CONFIDENCE_DECAY,
                urgency=0.65,
                target_price=pos.current_price,
                trigger_value=decay,
                threshold=self.confidence_decay_threshold,
                description=f"Confidence dropped {decay:.0%} (from {pos.entry_confidence:.0%} to {pos.current_confidence:.0%})",
                metadata={
                    "entry_confidence": pos.entry_confidence,
                    "current_confidence": pos.current_confidence,
                },
            )
        return None

    def _check_volatility(self, pos: PositionState) -> ExitSignal | None:
        """Check for volatility-based exit (large adverse move)."""
        if not self.volatility_exit_enabled:
            return None

        # Check if price moved significantly against our position
        if pos.side == "YES":
            # We're long YES, bad if price dropped
            adverse_move = pos.entry_price - pos.current_price
        else:
            # We're long NO, bad if YES price increased
            adverse_move = pos.current_price - pos.entry_price

        adverse_move_pct = adverse_move / pos.entry_price if pos.entry_price > 0 else 0

        if adverse_move_pct >= self.volatility_threshold:
            return ExitSignal(
                market_ticker=pos.market_ticker,
                side=pos.side,
                contracts=pos.contracts,
                reason=ExitReason.VOLATILITY_EXIT,
                urgency=0.80,
                target_price=pos.current_price,
                trigger_value=adverse_move_pct,
                threshold=self.volatility_threshold,
                description=f"Large adverse move: {adverse_move_pct:.1%}",
            )
        return None

    def update_high_water_mark(self, position: PositionState) -> bool:
        """
        Update high water mark if current value is higher.

        Args:
            position: Position to update

        Returns:
            True if high water mark was updated
        """
        if position.current_value > position.high_water_mark:
            position.high_water_mark = position.current_value
            position.high_water_mark_time = datetime.utcnow()
            return True
        return False

    def get_exit_summary(self, position: PositionState) -> dict[str, Any]:
        """
        Get summary of exit conditions for a position.

        Useful for dashboard display.
        """
        return {
            "market_ticker": position.market_ticker,
            "side": position.side,
            "pnl_pct": position.unrealized_pnl_pct,
            "hold_days": position.hold_duration_days,
            "hours_to_close": position.hours_until_close,
            "drawdown_from_high": position.drawdown_from_high,
            "conditions": {
                "stop_loss": {
                    "threshold": -self.stop_loss_pct,
                    "current": position.unrealized_pnl_pct,
                    "triggered": position.unrealized_pnl_pct <= -self.stop_loss_pct,
                },
                "take_profit": {
                    "threshold": self.take_profit_pct,
                    "current": position.unrealized_pnl_pct,
                    "triggered": position.unrealized_pnl_pct >= self.take_profit_pct,
                },
                "trailing_stop": {
                    "threshold": self.trailing_pullback_pct,
                    "current": position.drawdown_from_high,
                    "active": position.unrealized_pnl_pct >= self.min_profit_for_trailing,
                    "triggered": (
                        position.unrealized_pnl_pct >= self.min_profit_for_trailing
                        and position.drawdown_from_high >= self.trailing_pullback_pct
                    ),
                },
                "time_limit": {
                    "threshold": self.max_hold_days,
                    "current": position.hold_duration_days,
                    "triggered": position.hold_duration_days >= self.max_hold_days,
                },
                "expiry": {
                    "threshold": self.exit_hours_before_expiry,
                    "current": position.hours_until_close,
                    "triggered": (
                        position.hours_until_close is not None
                        and position.hours_until_close <= self.exit_hours_before_expiry
                    ),
                },
            },
        }
