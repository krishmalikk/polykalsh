"""
Market making strategy.

Captures bid-ask spread by providing liquidity.
Allocates 40% of portfolio to market making.
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


class MarketMakingStrategy(BaseStrategy):
    """
    Market making strategy that captures bid-ask spreads.

    Provides liquidity by placing limit orders inside the spread.
    Works best in:
    - Markets with wide spreads
    - Stable markets (not trending strongly)
    - Markets with moderate volume
    """

    def __init__(
        self,
        min_spread_pct: float = 0.04,
        max_spread_pct: float = 0.20,
        target_edge_pct: float = 0.02,
        min_volume_24h: int = 500,
        max_inventory_imbalance: float = 0.30,
        min_hours_to_close: float = 12.0,
        signal_expiry_minutes: int = 5,
    ):
        """
        Initialize market making strategy.

        Args:
            min_spread_pct: Minimum spread to trade (need enough to profit)
            max_spread_pct: Maximum spread (too wide = illiquid/risky)
            target_edge_pct: Target edge per trade
            min_volume_24h: Minimum 24h volume
            max_inventory_imbalance: Max imbalance in YES/NO inventory
            min_hours_to_close: Minimum hours until close
            signal_expiry_minutes: Signal validity (shorter for MM)
        """
        super().__init__(StrategyType.MARKET_MAKING)
        self.min_spread_pct = min_spread_pct
        self.max_spread_pct = max_spread_pct
        self.target_edge_pct = target_edge_pct
        self.min_volume_24h = min_volume_24h
        self.max_inventory_imbalance = max_inventory_imbalance
        self.min_hours_to_close = min_hours_to_close
        self.signal_expiry_minutes = signal_expiry_minutes

    @property
    def name(self) -> str:
        return "MarketMaking"

    def evaluate(self, context: StrategyContext) -> list[Signal]:
        """
        Evaluate market for market making opportunity.

        Returns signals for both sides if spread is favorable.
        """
        signals: list[Signal] = []

        # Check if we should skip this market
        should_skip, reason = self.should_skip_market(context)
        if should_skip:
            logger.debug(
                "mm_skip",
                market=context.market.market_ticker,
                reason=reason,
            )
            return signals

        # Check spread is in target range
        spread_pct = context.market.spread_pct
        if spread_pct < self.min_spread_pct:
            return signals
        if spread_pct > self.max_spread_pct:
            return signals

        # Calculate fair value (use ensemble if available, else mid price)
        if context.ensemble_probability is not None:
            fair_value = context.ensemble_probability
        else:
            fair_value = context.market.mid_price

        # Calculate our bid/ask prices
        # We want to improve on the current best bid/ask while maintaining edge
        half_spread = self.target_edge_pct

        our_bid = fair_value - half_spread  # We buy YES here
        our_ask = fair_value + half_spread  # We sell YES here (buy NO)

        # Ensure our prices improve on current market
        our_bid = min(our_bid, context.market.yes_bid + 0.01)
        our_ask = max(our_ask, context.market.yes_ask - 0.01)

        # Clamp to valid range
        our_bid = max(0.01, min(0.99, our_bid))
        our_ask = max(0.01, min(0.99, our_ask))

        # Check we still have edge after adjustments
        if our_ask - our_bid < self.target_edge_pct:
            return signals

        # Determine which sides to quote based on inventory
        quote_yes_bid = True
        quote_no_bid = True

        if context.has_position:
            # Adjust based on current inventory to avoid imbalance
            if context.position_side == "YES":
                # We're long YES, prefer to sell YES (buy NO)
                quote_yes_bid = False  # Don't buy more YES
            else:
                # We're long NO, prefer to sell NO (buy YES)
                quote_no_bid = False  # Don't buy more NO

        # Generate signals
        if quote_yes_bid:
            # Signal to buy YES (our bid)
            yes_signal = self._create_mm_signal(
                context=context,
                side="YES",
                price=our_bid,
                fair_value=fair_value,
                is_passive=True,
            )
            if yes_signal:
                signals.append(yes_signal)

        if quote_no_bid:
            # Signal to buy NO (equivalent to selling YES at our ask)
            no_price = 1 - our_ask  # Convert YES ask to NO bid
            no_signal = self._create_mm_signal(
                context=context,
                side="NO",
                price=no_price,
                fair_value=fair_value,
                is_passive=True,
            )
            if no_signal:
                signals.append(no_signal)

        if signals:
            logger.info(
                "mm_signals",
                market=context.market.market_ticker,
                spread_pct=spread_pct,
                fair_value=fair_value,
                num_signals=len(signals),
            )

        return signals

    def should_skip_market(self, context: StrategyContext) -> tuple[bool, str]:
        """Check if market is suitable for market making."""
        # Time check - need enough time for orders to fill
        if context.market.hours_until_close is not None:
            if context.market.hours_until_close < self.min_hours_to_close:
                return True, "too_close_to_expiry"

        # Volume check - need some activity
        if context.market.volume_24h < self.min_volume_24h:
            return True, "low_volume"

        # Avoid strongly trending markets (use ensemble if available)
        if context.ensemble_probability is not None:
            # If ensemble is very confident, market is trending
            if context.ensemble_confidence and context.ensemble_confidence > 0.85:
                return True, "trending_market"

        # Avoid extreme prices
        if context.market.yes_price > 0.90 or context.market.yes_price < 0.10:
            return True, "extreme_price"

        return False, ""

    def _create_mm_signal(
        self,
        context: StrategyContext,
        side: str,
        price: float,
        fair_value: float,
        is_passive: bool,
    ) -> Signal | None:
        """Create a market making signal."""
        # Calculate edge
        if side == "YES":
            edge = fair_value - price  # Buying below fair value
        else:
            edge = price - (1 - fair_value)  # Buying NO below its fair value

        if edge < 0.005:  # Less than 0.5% edge, not worth it
            return None

        return Signal(
            signal_type=SignalType.ENTRY,
            strategy=self.strategy_type,
            strength=SignalStrength.MODERATE,
            market_ticker=context.market.market_ticker,
            event_ticker=context.market.event_ticker,
            side=side,
            target_price=price,
            urgency=0.3,  # MM is patient
            probability_estimate=fair_value if side == "YES" else (1 - fair_value),
            confidence=0.60,  # MM doesn't need high directional confidence
            edge=edge,
            reason=f"Market making: {side} at {price:.2f} (fair={fair_value:.2f})",
            factors=[
                f"Spread: {context.market.spread_pct:.1%}",
                f"Volume: ${context.market.volume_24h / 100:,.0f}",
            ],
            expires_at=datetime.utcnow() + timedelta(minutes=self.signal_expiry_minutes),
            metadata={
                "is_passive": is_passive,
                "fair_value": fair_value,
                "current_spread": context.market.spread_pct,
            },
        )


class InventoryManager:
    """
    Manages market making inventory to avoid directional risk.

    Tracks positions and generates rebalancing signals.
    """

    def __init__(
        self,
        max_inventory_usd: float = 200.0,
        max_imbalance_pct: float = 0.30,
        rebalance_threshold: float = 0.20,
    ):
        """
        Initialize inventory manager.

        Args:
            max_inventory_usd: Maximum total inventory
            max_imbalance_pct: Max imbalance between YES/NO
            rebalance_threshold: Trigger rebalance at this imbalance
        """
        self.max_inventory_usd = max_inventory_usd
        self.max_imbalance_pct = max_imbalance_pct
        self.rebalance_threshold = rebalance_threshold

    def check_inventory(
        self,
        yes_inventory_usd: float,
        no_inventory_usd: float,
    ) -> dict:
        """
        Check inventory status.

        Returns:
            Dict with inventory metrics and recommendations
        """
        total = yes_inventory_usd + no_inventory_usd

        if total == 0:
            imbalance = 0.0
        else:
            imbalance = abs(yes_inventory_usd - no_inventory_usd) / total

        needs_rebalance = imbalance > self.rebalance_threshold
        at_capacity = total >= self.max_inventory_usd

        # Determine which side to reduce
        reduce_side = None
        if needs_rebalance:
            if yes_inventory_usd > no_inventory_usd:
                reduce_side = "YES"
            else:
                reduce_side = "NO"

        return {
            "yes_inventory_usd": yes_inventory_usd,
            "no_inventory_usd": no_inventory_usd,
            "total_inventory_usd": total,
            "imbalance": imbalance,
            "needs_rebalance": needs_rebalance,
            "at_capacity": at_capacity,
            "reduce_side": reduce_side,
            "can_buy_yes": not at_capacity and (
                not needs_rebalance or reduce_side != "YES"
            ),
            "can_buy_no": not at_capacity and (
                not needs_rebalance or reduce_side != "NO"
            ),
        }
