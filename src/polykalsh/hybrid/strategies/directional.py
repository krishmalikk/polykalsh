"""
Directional trading strategy.

Uses AI ensemble predictions to identify edge opportunities.
Allocates 50% of portfolio to directional trades.
"""

from datetime import datetime, timedelta

import structlog

from polykalsh.hybrid.portfolio.optimizer import StrategyType
from polykalsh.hybrid.strategies.base import (
    BaseStrategy,
    Signal,
    SignalStrength,
    SignalType,
    StrategyContext,
)

logger = structlog.get_logger()


class DirectionalStrategy(BaseStrategy):
    """
    Directional strategy based on AI ensemble predictions.

    Enters positions when:
    - Ensemble recommends BUY_YES or BUY_NO
    - Edge exceeds minimum threshold
    - Confidence exceeds minimum threshold
    - Sufficient liquidity and reasonable spread
    """

    def __init__(
        self,
        min_edge: float = 0.05,
        min_confidence: float = 0.60,
        min_volume_24h: int = 1000,
        max_spread_pct: float = 0.10,
        min_hours_to_close: float = 4.0,
        signal_expiry_minutes: int = 15,
    ):
        """
        Initialize directional strategy.

        Args:
            min_edge: Minimum edge to generate signal (default 5%)
            min_confidence: Minimum ensemble confidence (default 60%)
            min_volume_24h: Minimum 24h volume in cents
            max_spread_pct: Maximum spread as % of mid price
            min_hours_to_close: Minimum hours until market close
            signal_expiry_minutes: Signal validity in minutes
        """
        super().__init__(StrategyType.DIRECTIONAL)
        self.min_edge = min_edge
        self.min_confidence = min_confidence
        self.min_volume_24h = min_volume_24h
        self.max_spread_pct = max_spread_pct
        self.min_hours_to_close = min_hours_to_close
        self.signal_expiry_minutes = signal_expiry_minutes

    @property
    def name(self) -> str:
        return "Directional"

    def evaluate(self, context: StrategyContext) -> list[Signal]:
        """
        Evaluate market for directional trading opportunity.

        Returns entry signal if ensemble recommends a trade with sufficient edge.
        """
        signals: list[Signal] = []

        # Check if we should skip this market
        should_skip, reason = self.should_skip_market(context)
        if should_skip:
            logger.debug(
                "directional_skip",
                market=context.market.market_ticker,
                reason=reason,
            )
            return signals

        # Need ensemble decision
        if context.ensemble_action is None:
            return signals

        # Check for actionable ensemble decision
        if context.ensemble_action not in ("BUY_YES", "BUY_NO"):
            return signals

        # Check confidence
        confidence = context.ensemble_confidence or 0.0
        if confidence < self.min_confidence:
            logger.debug(
                "directional_low_confidence",
                market=context.market.market_ticker,
                confidence=confidence,
            )
            return signals

        # Check edge
        edge = context.ensemble_edge or 0.0
        if abs(edge) < self.min_edge:
            logger.debug(
                "directional_low_edge",
                market=context.market.market_ticker,
                edge=edge,
            )
            return signals

        # Already have position
        if context.has_position:
            # Could generate adjustment signals here
            return signals

        # Determine side and price
        if context.ensemble_action == "BUY_YES":
            side = "YES"
            # Target price slightly below ask for better fill
            target_price = context.market.yes_ask - 0.01
            target_price = max(0.01, min(0.99, target_price))
        else:
            side = "NO"
            target_price = context.market.no_ask - 0.01
            target_price = max(0.01, min(0.99, target_price))

        # Determine signal strength
        strength = self._calculate_strength(edge, confidence)

        # Calculate urgency based on time to close and edge size
        urgency = self._calculate_urgency(context, edge)

        # Build signal
        signal = Signal(
            signal_type=SignalType.ENTRY,
            strategy=self.strategy_type,
            strength=strength,
            market_ticker=context.market.market_ticker,
            event_ticker=context.market.event_ticker,
            side=side,
            target_price=target_price,
            urgency=urgency,
            probability_estimate=context.ensemble_probability,
            confidence=confidence,
            edge=edge,
            reason=f"Ensemble recommends {context.ensemble_action} with {edge:.1%} edge",
            factors=self._collect_factors(context),
            expires_at=datetime.utcnow() + timedelta(minutes=self.signal_expiry_minutes),
            metadata={
                "ensemble_action": context.ensemble_action,
                "volume_24h": context.market.volume_24h,
                "spread_pct": context.market.spread_pct,
            },
        )

        logger.info(
            "directional_signal",
            market=context.market.market_ticker,
            side=side,
            edge=edge,
            confidence=confidence,
            strength=strength.value,
        )

        signals.append(signal)
        return signals

    def should_skip_market(self, context: StrategyContext) -> tuple[bool, str]:
        """Check if market should be skipped for directional trading."""
        # Time check
        if context.market.hours_until_close is not None:
            if context.market.hours_until_close < self.min_hours_to_close:
                return True, "too_close_to_expiry"

        # Volume check
        if context.market.volume_24h < self.min_volume_24h:
            return True, "low_volume"

        # Spread check
        if context.market.spread_pct > self.max_spread_pct:
            return True, "wide_spread"

        # Price extremes (avoid 95%+ or 5%- markets)
        if context.market.yes_price > 0.95 or context.market.yes_price < 0.05:
            return True, "extreme_price"

        return False, ""

    def _calculate_strength(self, edge: float, confidence: float) -> SignalStrength:
        """Calculate signal strength based on edge and confidence."""
        score = edge * confidence

        if score >= 0.08:  # 8%+ edge * confidence
            return SignalStrength.STRONG
        elif score >= 0.04:  # 4-8%
            return SignalStrength.MODERATE
        else:
            return SignalStrength.WEAK

    def _calculate_urgency(self, context: StrategyContext, edge: float) -> float:
        """
        Calculate urgency (0-1).

        Higher urgency for:
        - Larger edge (might disappear)
        - Less time to close
        - Higher volume (more efficient market)
        """
        urgency = 0.5

        # Edge component
        if edge > 0.15:
            urgency += 0.2
        elif edge > 0.10:
            urgency += 0.1

        # Time component
        if context.market.hours_until_close is not None:
            if context.market.hours_until_close < 24:
                urgency += 0.15
            elif context.market.hours_until_close < 72:
                urgency += 0.05

        # Volume component (higher volume = more urgent to capture edge)
        if context.market.volume_24h > 100000:
            urgency += 0.1

        return min(1.0, urgency)

    def _collect_factors(self, context: StrategyContext) -> list[str]:
        """Collect key factors for the signal."""
        factors = []

        if context.bullish_factors:
            factors.append(f"Bullish: {context.bullish_factors[0]}")

        if context.bearish_factors:
            factors.append(f"Bearish: {context.bearish_factors[0]}")

        if context.market.hours_until_close is not None:
            factors.append(f"Closes in {context.market.hours_until_close:.0f}h")

        return factors[:5]


class ExitSignalGenerator:
    """
    Generates exit signals for directional positions.

    Separate from main strategy to allow different evaluation logic.
    """

    def __init__(
        self,
        take_profit_pct: float = 0.25,
        stop_loss_pct: float = 0.15,
        confidence_decay_threshold: float = 0.50,
        min_hours_before_expiry: float = 2.0,
    ):
        """
        Initialize exit signal generator.

        Args:
            take_profit_pct: Exit when profit exceeds this %
            stop_loss_pct: Exit when loss exceeds this %
            confidence_decay_threshold: Exit if confidence drops by this %
            min_hours_before_expiry: Exit this many hours before expiry
        """
        self.take_profit_pct = take_profit_pct
        self.stop_loss_pct = stop_loss_pct
        self.confidence_decay_threshold = confidence_decay_threshold
        self.min_hours_before_expiry = min_hours_before_expiry

    def evaluate(
        self,
        context: StrategyContext,
        entry_confidence: float | None = None,
    ) -> Signal | None:
        """
        Check if position should be exited.

        Args:
            context: Strategy context with position info
            entry_confidence: Confidence at entry time

        Returns:
            Exit signal or None
        """
        if not context.has_position:
            return None

        # Check take profit
        if context.position_pnl_pct >= self.take_profit_pct:
            return self._create_exit_signal(
                context,
                reason=f"Take profit at {context.position_pnl_pct:.1%}",
                strength=SignalStrength.STRONG,
            )

        # Check stop loss
        if context.position_pnl_pct <= -self.stop_loss_pct:
            return self._create_exit_signal(
                context,
                reason=f"Stop loss at {context.position_pnl_pct:.1%}",
                strength=SignalStrength.STRONG,
            )

        # Check time to expiry
        if context.market.hours_until_close is not None:
            if context.market.hours_until_close <= self.min_hours_before_expiry:
                return self._create_exit_signal(
                    context,
                    reason=f"Expiry approaching ({context.market.hours_until_close:.1f}h)",
                    strength=SignalStrength.MODERATE,
                )

        # Check confidence decay
        if entry_confidence is not None and context.ensemble_confidence is not None:
            decay = (entry_confidence - context.ensemble_confidence) / entry_confidence
            if decay >= self.confidence_decay_threshold:
                return self._create_exit_signal(
                    context,
                    reason=f"Confidence dropped {decay:.0%}",
                    strength=SignalStrength.MODERATE,
                )

        return None

    def _create_exit_signal(
        self,
        context: StrategyContext,
        reason: str,
        strength: SignalStrength,
    ) -> Signal:
        """Create an exit signal."""
        # Exit on opposite side
        if context.position_side == "YES":
            side = "YES"  # Sell YES
            target_price = context.market.yes_bid + 0.01
        else:
            side = "NO"  # Sell NO
            target_price = context.market.no_bid + 0.01

        return Signal(
            signal_type=SignalType.EXIT,
            strategy=StrategyType.DIRECTIONAL,
            strength=strength,
            market_ticker=context.market.market_ticker,
            event_ticker=context.market.event_ticker,
            side=side,
            target_price=target_price,
            urgency=0.8 if strength == SignalStrength.STRONG else 0.6,
            reason=reason,
            metadata={
                "pnl_pct": context.position_pnl_pct,
                "position_contracts": context.position_contracts,
            },
        )
