"""
Portfolio optimizer with strategy allocation and risk management.

Manages overall portfolio allocation across strategies and positions.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

import structlog

from polykalsh.hybrid.portfolio.kelly import KellyResult, calculate_position_size

logger = structlog.get_logger()


class StrategyType(str, Enum):
    """Trading strategy types."""

    DIRECTIONAL = "directional"
    MARKET_MAKING = "market_making"
    ARBITRAGE = "arbitrage"


@dataclass
class Position:
    """An open position in the portfolio."""

    market_ticker: str
    event_ticker: str
    side: str  # "YES" or "NO"
    strategy: StrategyType
    contracts: int
    entry_price: float
    cost_basis: float  # Total USD invested
    entry_time: datetime
    current_price: float | None = None

    @property
    def current_value(self) -> float:
        """Current value of the position."""
        if self.current_price is None:
            return self.cost_basis
        return self.contracts * self.current_price

    @property
    def unrealized_pnl(self) -> float:
        """Unrealized P&L."""
        return self.current_value - self.cost_basis

    @property
    def unrealized_pnl_pct(self) -> float:
        """Unrealized P&L as percentage."""
        if self.cost_basis <= 0:
            return 0.0
        return self.unrealized_pnl / self.cost_basis


@dataclass
class PortfolioState:
    """Current state of the portfolio."""

    # Balances
    cash_balance: float
    starting_balance: float

    # Positions
    positions: list[Position] = field(default_factory=list)

    # Daily tracking
    daily_pnl: float = 0.0
    daily_trades: int = 0
    daily_ai_cost: float = 0.0

    # High water mark for drawdown
    high_water_mark: float = 0.0

    @property
    def positions_value(self) -> float:
        """Total value of all positions."""
        return sum(p.current_value for p in self.positions)

    @property
    def total_value(self) -> float:
        """Total portfolio value (cash + positions)."""
        return self.cash_balance + self.positions_value

    @property
    def total_pnl(self) -> float:
        """Total P&L since start."""
        return self.total_value - self.starting_balance

    @property
    def total_pnl_pct(self) -> float:
        """Total P&L as percentage."""
        if self.starting_balance <= 0:
            return 0.0
        return self.total_pnl / self.starting_balance

    @property
    def drawdown(self) -> float:
        """Current drawdown from high water mark."""
        if self.high_water_mark <= 0:
            return 0.0
        return (self.high_water_mark - self.total_value) / self.high_water_mark

    @property
    def open_positions_count(self) -> int:
        """Number of open positions."""
        return len(self.positions)

    def get_strategy_exposure(self, strategy: StrategyType) -> float:
        """Get total exposure for a strategy."""
        return sum(
            p.cost_basis for p in self.positions if p.strategy == strategy
        )

    def get_strategy_allocation(self, strategy: StrategyType) -> float:
        """Get current allocation percentage for a strategy."""
        if self.total_value <= 0:
            return 0.0
        return self.get_strategy_exposure(strategy) / self.total_value

    def has_position(self, market_ticker: str) -> bool:
        """Check if we have a position in a market."""
        return any(p.market_ticker == market_ticker for p in self.positions)

    def get_position(self, market_ticker: str) -> Position | None:
        """Get position for a market."""
        for p in self.positions:
            if p.market_ticker == market_ticker:
                return p
        return None


@dataclass
class SizeRequest:
    """Request for position sizing."""

    market_ticker: str
    event_ticker: str
    strategy: StrategyType
    side: str  # "YES" or "NO"
    probability_estimate: float
    confidence: float
    current_price: float  # Market price for YES


@dataclass
class SizeResult:
    """Result of position sizing calculation."""

    # Recommendation
    can_trade: bool
    recommended_usd: float
    recommended_contracts: int

    # Kelly details
    kelly: KellyResult | None = None

    # Constraints
    limiting_factor: str = ""
    rejection_reason: str = ""

    # Allocation info
    strategy_allocation_before: float = 0.0
    strategy_allocation_after: float = 0.0


class PortfolioOptimizer:
    """
    Portfolio optimizer with strategy allocation and risk management.

    Features:
    - Kelly Criterion position sizing
    - Strategy allocation limits
    - Position limits
    - Daily loss limits
    - Drawdown protection
    """

    def __init__(
        self,
        # Kelly parameters
        kelly_fraction: float = 0.75,
        min_edge: float = 0.05,
        min_confidence: float = 0.50,
        # Position limits
        max_position_pct: float = 0.10,
        max_bet_usd: float = 100.0,
        min_bet_usd: float = 5.0,
        max_concurrent_positions: int = 20,
        # Strategy allocation
        directional_allocation: float = 0.50,
        market_making_allocation: float = 0.40,
        arbitrage_allocation: float = 0.10,
        # Risk limits
        max_daily_loss_pct: float = 0.15,
        max_drawdown_pct: float = 0.50,
        daily_ai_cost_limit: float = 50.0,
    ):
        """
        Initialize portfolio optimizer.

        Args:
            kelly_fraction: Fraction of Kelly to use (0.75 = 75% Kelly)
            min_edge: Minimum edge to trade
            min_confidence: Minimum confidence to trade
            max_position_pct: Max single position as % of portfolio
            max_bet_usd: Maximum bet in USD
            min_bet_usd: Minimum bet in USD
            max_concurrent_positions: Max number of open positions
            directional_allocation: Target allocation for directional
            market_making_allocation: Target allocation for market making
            arbitrage_allocation: Target allocation for arbitrage
            max_daily_loss_pct: Max daily loss before circuit breaker
            max_drawdown_pct: Max drawdown before circuit breaker
            daily_ai_cost_limit: Max daily AI cost in USD
        """
        self.kelly_fraction = kelly_fraction
        self.min_edge = min_edge
        self.min_confidence = min_confidence
        self.max_position_pct = max_position_pct
        self.max_bet_usd = max_bet_usd
        self.min_bet_usd = min_bet_usd
        self.max_concurrent_positions = max_concurrent_positions
        self.max_daily_loss_pct = max_daily_loss_pct
        self.max_drawdown_pct = max_drawdown_pct
        self.daily_ai_cost_limit = daily_ai_cost_limit

        # Strategy allocations
        self.strategy_allocations = {
            StrategyType.DIRECTIONAL: directional_allocation,
            StrategyType.MARKET_MAKING: market_making_allocation,
            StrategyType.ARBITRAGE: arbitrage_allocation,
        }

        # Verify allocations sum to 1
        total = sum(self.strategy_allocations.values())
        if abs(total - 1.0) > 0.01:
            logger.warning("strategy_allocations_not_normalized", total=total)

    def calculate_position_size(
        self,
        request: SizeRequest,
        state: PortfolioState,
    ) -> SizeResult:
        """
        Calculate recommended position size with all constraints.

        Args:
            request: Position sizing request
            state: Current portfolio state

        Returns:
            SizeResult with recommendation
        """
        # Check circuit breakers first
        rejection = self._check_circuit_breakers(state)
        if rejection:
            return SizeResult(
                can_trade=False,
                recommended_usd=0.0,
                recommended_contracts=0,
                rejection_reason=rejection,
            )

        # Check position limits
        rejection = self._check_position_limits(request, state)
        if rejection:
            return SizeResult(
                can_trade=False,
                recommended_usd=0.0,
                recommended_contracts=0,
                rejection_reason=rejection,
            )

        # Check strategy allocation
        strategy_allocation_before = state.get_strategy_allocation(request.strategy)
        max_strategy_allocation = self.strategy_allocations[request.strategy]

        # Calculate available capital for this strategy
        available_for_strategy = self._get_available_for_strategy(
            request.strategy, state
        )

        if available_for_strategy <= 0:
            return SizeResult(
                can_trade=False,
                recommended_usd=0.0,
                recommended_contracts=0,
                rejection_reason="strategy_allocation_exceeded",
                strategy_allocation_before=strategy_allocation_before,
            )

        # Calculate Kelly sizing
        kelly = calculate_position_size(
            probability=request.probability_estimate,
            confidence=request.confidence,
            market_price=request.current_price,
            side=request.side,
            bankroll=state.total_value,
            kelly_fraction=self.kelly_fraction,
            max_position_pct=self.max_position_pct,
            max_bet_usd=min(self.max_bet_usd, available_for_strategy),
            min_bet_usd=self.min_bet_usd,
            min_edge=self.min_edge,
            min_confidence=self.min_confidence,
        )

        # Check if Kelly recommends betting
        if kelly.recommended_usd <= 0:
            return SizeResult(
                can_trade=False,
                recommended_usd=0.0,
                recommended_contracts=0,
                kelly=kelly,
                limiting_factor=kelly.limiting_factor,
                rejection_reason=kelly.limiting_factor,
                strategy_allocation_before=strategy_allocation_before,
            )

        # Cap by available cash
        recommended_usd = min(kelly.recommended_usd, state.cash_balance)
        if recommended_usd < self.min_bet_usd:
            return SizeResult(
                can_trade=False,
                recommended_usd=0.0,
                recommended_contracts=0,
                kelly=kelly,
                rejection_reason="insufficient_cash",
                strategy_allocation_before=strategy_allocation_before,
            )

        # Recalculate contracts at capped amount
        price_per_contract = (
            request.current_price
            if request.side.upper() == "YES"
            else (1 - request.current_price)
        )
        recommended_contracts = int(recommended_usd / price_per_contract) if price_per_contract > 0 else 0

        if recommended_contracts <= 0:
            return SizeResult(
                can_trade=False,
                recommended_usd=0.0,
                recommended_contracts=0,
                kelly=kelly,
                rejection_reason="zero_contracts",
                strategy_allocation_before=strategy_allocation_before,
            )

        # Calculate allocation after trade
        strategy_allocation_after = (
            state.get_strategy_exposure(request.strategy) + recommended_usd
        ) / state.total_value

        logger.info(
            "position_size_calculated",
            market=request.market_ticker,
            strategy=request.strategy.value,
            side=request.side,
            kelly_fraction=kelly.full_kelly_fraction,
            edge=kelly.edge,
            recommended_usd=recommended_usd,
            recommended_contracts=recommended_contracts,
            limiting_factor=kelly.limiting_factor,
        )

        return SizeResult(
            can_trade=True,
            recommended_usd=recommended_usd,
            recommended_contracts=recommended_contracts,
            kelly=kelly,
            limiting_factor=kelly.limiting_factor,
            strategy_allocation_before=strategy_allocation_before,
            strategy_allocation_after=strategy_allocation_after,
        )

    def _check_circuit_breakers(self, state: PortfolioState) -> str | None:
        """Check if any circuit breakers are triggered."""
        # Daily loss limit
        if state.starting_balance > 0:
            daily_loss_pct = -state.daily_pnl / state.starting_balance
            if daily_loss_pct >= self.max_daily_loss_pct:
                logger.warning(
                    "circuit_breaker_daily_loss",
                    daily_loss_pct=daily_loss_pct,
                )
                return "daily_loss_limit"

        # Drawdown limit
        if state.drawdown >= self.max_drawdown_pct:
            logger.warning(
                "circuit_breaker_drawdown",
                drawdown=state.drawdown,
            )
            return "max_drawdown"

        # AI cost limit
        if state.daily_ai_cost >= self.daily_ai_cost_limit:
            logger.warning(
                "circuit_breaker_ai_cost",
                daily_ai_cost=state.daily_ai_cost,
            )
            return "ai_cost_limit"

        return None

    def _check_position_limits(
        self, request: SizeRequest, state: PortfolioState
    ) -> str | None:
        """Check position-related limits."""
        # Max concurrent positions
        if state.open_positions_count >= self.max_concurrent_positions:
            return "max_positions"

        # Already have position in this market
        if state.has_position(request.market_ticker):
            return "existing_position"

        return None

    def _get_available_for_strategy(
        self, strategy: StrategyType, state: PortfolioState
    ) -> float:
        """Get available capital for a strategy."""
        target_allocation = self.strategy_allocations[strategy]
        current_exposure = state.get_strategy_exposure(strategy)
        max_exposure = state.total_value * target_allocation

        available = max_exposure - current_exposure
        return max(0.0, available)

    def get_portfolio_summary(self, state: PortfolioState) -> dict[str, Any]:
        """Get summary of portfolio state."""
        return {
            "cash_balance": state.cash_balance,
            "positions_value": state.positions_value,
            "total_value": state.total_value,
            "total_pnl": state.total_pnl,
            "total_pnl_pct": state.total_pnl_pct,
            "daily_pnl": state.daily_pnl,
            "drawdown": state.drawdown,
            "open_positions": state.open_positions_count,
            "strategy_allocations": {
                strategy.value: {
                    "target": self.strategy_allocations[strategy],
                    "current": state.get_strategy_allocation(strategy),
                    "exposure": state.get_strategy_exposure(strategy),
                }
                for strategy in StrategyType
            },
            "circuit_breakers": {
                "daily_loss_limit": self.max_daily_loss_pct,
                "max_drawdown": self.max_drawdown_pct,
                "ai_cost_limit": self.daily_ai_cost_limit,
            },
        }

    def update_high_water_mark(self, state: PortfolioState) -> None:
        """Update high water mark if we have a new high."""
        if state.total_value > state.high_water_mark:
            state.high_water_mark = state.total_value
