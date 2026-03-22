"""
Main orchestrator for the hybrid trading bot.

Coordinates all components:
- Market discovery
- Deep research via Perplexity
- AI ensemble decisions
- Strategy signal generation
- Portfolio optimization
- Position exits
- Order execution
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import structlog
from sqlalchemy.orm import Session

from polykalsh.clients.kalshi.client import KalshiClient
from polykalsh.clients.kalshi.schemas import (
    CreateOrderRequest,
    OrderAction,
    OrderSide,
    OrderType,
)
from polykalsh.config import Settings
from polykalsh.hybrid.discovery import (
    BatchMarketFetcher,
    DiscoveredMarket,
    DiscoveryFilters,
    MarketDiscovery,
)
from polykalsh.hybrid.ensemble.aggregator import EnsembleAggregator
from polykalsh.hybrid.ensemble.agents import create_ensemble
from polykalsh.hybrid.ensemble.schemas import EnsembleResult, MarketContext
from polykalsh.hybrid.exit_manager import ExitManager, ExitSignal, PositionState
from polykalsh.hybrid.portfolio.optimizer import (
    PortfolioOptimizer,
    PortfolioState,
    Position,
    SizeRequest,
    SizeResult,
    StrategyType,
)
from polykalsh.hybrid.research.manager import ResearchManager
from polykalsh.hybrid.research.schemas import ResearchQuery, ResearchResult, ResearchType
from polykalsh.hybrid.strategies.base import Signal, SignalType, StrategyContext
from polykalsh.hybrid.strategies.directional import DirectionalStrategy
from polykalsh.hybrid.strategies.market_making import MarketMakingStrategy

logger = structlog.get_logger()


@dataclass
class TradingCycleResult:
    """Result of a single trading cycle."""

    # Discovery
    markets_discovered: int = 0
    markets_analyzed: int = 0

    # Research
    research_calls: int = 0
    research_cache_hits: int = 0
    research_cost_usd: float = 0.0

    # Ensemble
    ensemble_calls: int = 0
    ensemble_cost_usd: float = 0.0
    buy_signals: int = 0
    hold_signals: int = 0
    skip_signals: int = 0

    # Strategy
    entry_signals: int = 0
    exit_signals: int = 0

    # Execution
    orders_placed: int = 0
    orders_filled: int = 0
    positions_opened: int = 0
    positions_closed: int = 0

    # Portfolio
    starting_value: float = 0.0
    ending_value: float = 0.0
    cycle_pnl: float = 0.0

    # Timing
    started_at: datetime = field(default_factory=datetime.utcnow)
    ended_at: datetime | None = None
    duration_seconds: float = 0.0

    # Errors
    errors: list[str] = field(default_factory=list)


class HybridOrchestrator:
    """
    Main orchestrator for the hybrid trading bot.

    Manages the complete trading loop:
    1. Discover markets
    2. Research (with caching)
    3. AI ensemble decisions
    4. Strategy signals
    5. Position sizing
    6. Order execution
    7. Exit management
    """

    def __init__(
        self,
        settings: Settings,
        db_session: Session,
        kalshi_client: KalshiClient | None = None,
    ):
        """
        Initialize orchestrator.

        Args:
            settings: Application settings
            db_session: Database session
            kalshi_client: Optional pre-configured Kalshi client
        """
        self.settings = settings
        self.db = db_session

        # Configuration shortcuts
        self.hybrid_config = settings.hybrid_trading
        self.ensemble_config = settings.ensemble
        self.portfolio_config = settings.portfolio
        self.exit_config = settings.exit_rules

        # State
        self._is_running = False
        self._last_cycle: TradingCycleResult | None = None

        # Components (initialized lazily)
        self._kalshi_client = kalshi_client
        self._discovery: MarketDiscovery | None = None
        self._research_manager: ResearchManager | None = None
        self._ensemble: EnsembleAggregator | None = None
        self._portfolio_optimizer: PortfolioOptimizer | None = None
        self._exit_manager: ExitManager | None = None
        self._strategies: list[Any] | None = None
        self._batch_fetcher: BatchMarketFetcher | None = None

        # Portfolio state (for paper mode, loaded from DB for live)
        self._portfolio_state: PortfolioState | None = None

    # ═══════════════════════════════════════════════════════════════════════════
    # INITIALIZATION
    # ═══════════════════════════════════════════════════════════════════════════

    async def initialize(self) -> None:
        """Initialize all components."""
        logger.info("orchestrator_init_start")

        # Initialize Kalshi client
        if self._kalshi_client is None:
            self._kalshi_client = KalshiClient(
                api_key_id=self.settings.kalshi.api_key_id,
                private_key_path=self.settings.kalshi.private_key_path,
                env=self.settings.kalshi.env,
                paper_mode=self.hybrid_config.paper_mode,
            )
        await self._kalshi_client._ensure_client()

        # Initialize discovery
        discovery_filters = DiscoveryFilters(
            min_volume_24h=self.hybrid_config.min_volume_24h,
            min_hours_to_close=4.0,
            max_days_to_expiry=self.hybrid_config.max_expiry_days,
        )
        self._discovery = MarketDiscovery(
            client=self._kalshi_client,
            top_events=self.hybrid_config.top_events,
            markets_per_event=self.hybrid_config.markets_per_event,
            filters=discovery_filters,
        )

        # Initialize batch fetcher
        self._batch_fetcher = BatchMarketFetcher(self._kalshi_client)

        # Initialize research manager
        if self.settings.ai_providers.perplexity_api_key:
            self._research_manager = ResearchManager(
                api_key=self.settings.ai_providers.perplexity_api_key,
                db_session=self.db,
                model=self.settings.ai_providers.perplexity_model,
            )

        # Initialize ensemble
        agents = create_ensemble(
            anthropic_api_key=self.settings.ai_providers.anthropic_api_key,
            openrouter_api_key=self.settings.ai_providers.openrouter_api_key,
            use_mock=not self.settings.ai_providers.is_configured,
        )
        self._ensemble = EnsembleAggregator(
            agents=agents,
            min_consensus_confidence=self.ensemble_config.min_consensus_confidence,
            max_disagreement=self.ensemble_config.max_disagreement_spread,
            min_edge=self.ensemble_config.min_edge_to_trade,
        )

        # Initialize portfolio optimizer
        self._portfolio_optimizer = PortfolioOptimizer(
            kelly_fraction=self.portfolio_config.kelly_fraction,
            min_edge=self.ensemble_config.min_edge_to_trade,
            min_confidence=self.ensemble_config.min_consensus_confidence,
            max_position_pct=self.hybrid_config.max_position_pct,
            max_bet_usd=self.hybrid_config.max_bet_amount_usd,
            max_concurrent_positions=self.hybrid_config.max_concurrent_positions,
            directional_allocation=self.portfolio_config.directional_allocation,
            market_making_allocation=self.portfolio_config.market_making_allocation,
            arbitrage_allocation=self.portfolio_config.arbitrage_allocation,
            max_daily_loss_pct=self.portfolio_config.max_daily_loss_pct,
            max_drawdown_pct=self.portfolio_config.max_drawdown_pct,
        )

        # Initialize exit manager
        self._exit_manager = ExitManager(
            take_profit_pct=self.exit_config.trailing_take_profit_pct,
            stop_loss_pct=self.exit_config.stop_loss_pct,
            trailing_pullback_pct=self.exit_config.trailing_pullback_pct,
            max_hold_days=self.exit_config.max_hold_days,
            exit_hours_before_expiry=self.exit_config.exit_hours_before_expiry,
            confidence_decay_threshold=self.exit_config.confidence_decay_threshold,
            confidence_recheck_hours=self.exit_config.confidence_recheck_hours,
        )

        # Initialize strategies
        self._strategies = [
            DirectionalStrategy(
                min_edge=self.ensemble_config.min_edge_to_trade,
                min_confidence=self.ensemble_config.min_consensus_confidence,
            ),
            MarketMakingStrategy(),
        ]

        # Initialize portfolio state
        await self._initialize_portfolio_state()

        logger.info("orchestrator_init_complete")

    async def _initialize_portfolio_state(self) -> None:
        """Initialize portfolio state from Kalshi or paper mode."""
        if self.hybrid_config.paper_mode:
            self._portfolio_state = PortfolioState(
                cash_balance=self.hybrid_config.paper_starting_balance,
                starting_balance=self.hybrid_config.paper_starting_balance,
                high_water_mark=self.hybrid_config.paper_starting_balance,
            )
        else:
            # Get actual balance from Kalshi
            balance = await self._kalshi_client.get_balance()
            positions, _ = await self._kalshi_client.get_positions()

            # Convert Kalshi positions to our format
            portfolio_positions = []
            for pos in positions:
                if pos.contracts > 0:
                    portfolio_positions.append(
                        Position(
                            market_ticker=pos.ticker,
                            event_ticker=pos.event_ticker,
                            side="YES" if pos.position > 0 else "NO",
                            strategy=StrategyType.DIRECTIONAL,  # Assume directional
                            contracts=pos.contracts,
                            entry_price=pos.avg_price,
                            cost_basis=pos.total_cost / 100,  # Convert cents
                            entry_time=datetime.utcnow(),  # Unknown, use now
                        )
                    )

            self._portfolio_state = PortfolioState(
                cash_balance=balance.available_usd,
                starting_balance=balance.total_usd,
                positions=portfolio_positions,
                high_water_mark=balance.total_usd,
            )

    async def shutdown(self) -> None:
        """Shutdown and cleanup resources."""
        logger.info("orchestrator_shutdown")
        self._is_running = False

        if self._kalshi_client:
            await self._kalshi_client.close()

        if self._research_manager:
            await self._research_manager.close()

    # ═══════════════════════════════════════════════════════════════════════════
    # MAIN TRADING LOOP
    # ═══════════════════════════════════════════════════════════════════════════

    async def run_cycle(self) -> TradingCycleResult:
        """
        Run a single trading cycle.

        Returns:
            Cycle results with statistics
        """
        result = TradingCycleResult(
            starting_value=self._portfolio_state.total_value if self._portfolio_state else 0,
        )

        try:
            # Step 1: Update existing positions
            await self._update_positions(result)

            # Step 2: Check exits for existing positions
            exit_signals = await self._check_exits(result)

            # Step 3: Execute exits
            for signal in exit_signals:
                await self._execute_exit(signal, result)

            # Step 4: Discover new markets
            markets = await self._discover_markets(result)

            # Step 5: Analyze markets
            for market in markets[:20]:  # Limit per cycle
                try:
                    await self._analyze_and_trade(market, result)
                except Exception as e:
                    result.errors.append(f"Analysis error for {market.market_ticker}: {e}")
                    logger.warning(
                        "market_analysis_error",
                        market=market.market_ticker,
                        error=str(e),
                    )

        except Exception as e:
            result.errors.append(f"Cycle error: {e}")
            logger.error("trading_cycle_error", error=str(e))

        finally:
            result.ended_at = datetime.utcnow()
            result.duration_seconds = (result.ended_at - result.started_at).total_seconds()
            result.ending_value = self._portfolio_state.total_value if self._portfolio_state else 0
            result.cycle_pnl = result.ending_value - result.starting_value
            self._last_cycle = result

        logger.info(
            "trading_cycle_complete",
            duration=result.duration_seconds,
            markets_analyzed=result.markets_analyzed,
            orders_placed=result.orders_placed,
            cycle_pnl=result.cycle_pnl,
        )

        return result

    async def run_continuous(
        self,
        interval_minutes: int | None = None,
    ) -> None:
        """
        Run continuous trading loop.

        Args:
            interval_minutes: Override scan interval from config
        """
        interval = interval_minutes or self.hybrid_config.scan_interval_min

        logger.info("continuous_trading_start", interval_minutes=interval)
        self._is_running = True

        while self._is_running:
            try:
                await self.run_cycle()
            except Exception as e:
                logger.error("cycle_exception", error=str(e))

            # Wait for next cycle
            await asyncio.sleep(interval * 60)

    def stop(self) -> None:
        """Stop continuous trading."""
        self._is_running = False

    # ═══════════════════════════════════════════════════════════════════════════
    # INTERNAL METHODS
    # ═══════════════════════════════════════════════════════════════════════════

    async def _update_positions(self, result: TradingCycleResult) -> None:
        """Update current prices for existing positions."""
        if not self._portfolio_state or not self._portfolio_state.positions:
            return

        tickers = [p.market_ticker for p in self._portfolio_state.positions]
        prices = await self._batch_fetcher.get_current_prices(tickers)

        for position in self._portfolio_state.positions:
            if position.market_ticker in prices:
                yes_price, _ = prices[position.market_ticker]
                if position.side == "YES":
                    position.current_price = yes_price
                else:
                    position.current_price = 1 - yes_price

    async def _check_exits(self, result: TradingCycleResult) -> list[ExitSignal]:
        """Check all positions for exit conditions."""
        if not self._portfolio_state or not self._exit_manager:
            return []

        exit_signals: list[ExitSignal] = []

        for position in self._portfolio_state.positions:
            # Convert to PositionState for exit manager
            pos_state = PositionState(
                market_ticker=position.market_ticker,
                event_ticker=position.event_ticker,
                side=position.side,
                contracts=position.contracts,
                entry_price=position.entry_price,
                entry_time=position.entry_time,
                cost_basis=position.cost_basis,
                current_price=position.current_price or position.entry_price,
                current_value=position.current_value,
                high_water_mark=position.current_value,  # Track separately
            )

            # Update high water mark
            self._exit_manager.update_high_water_mark(pos_state)

            # Check for exit
            signal = self._exit_manager.evaluate(pos_state)
            if signal:
                exit_signals.append(signal)
                result.exit_signals += 1

        return exit_signals

    async def _execute_exit(
        self,
        signal: ExitSignal,
        result: TradingCycleResult,
    ) -> None:
        """Execute an exit order."""
        logger.info(
            "executing_exit",
            market=signal.market_ticker,
            reason=signal.reason.value,
            contracts=signal.contracts,
        )

        # Find the position
        position = self._portfolio_state.get_position(signal.market_ticker)
        if not position:
            return

        # Create sell order
        order = CreateOrderRequest(
            ticker=signal.market_ticker,
            side=OrderSide.YES if signal.side == "YES" else OrderSide.NO,
            action=OrderAction.SELL,
            type=OrderType.LIMIT,
            count=signal.contracts,
            yes_price=int((signal.target_price or position.current_price) * 100) if signal.side == "YES" else None,
            no_price=int((signal.target_price or (1 - position.current_price)) * 100) if signal.side == "NO" else None,
        )

        try:
            order_result = await self._kalshi_client.place_order(order)
            result.orders_placed += 1

            if order_result.filled_count > 0:
                result.orders_filled += 1
                result.positions_closed += 1

                # Update portfolio state
                fill_value = order_result.filled_count * (order_result.avg_fill_price / 100)
                self._portfolio_state.cash_balance += fill_value
                self._portfolio_state.positions.remove(position)

                logger.info(
                    "exit_executed",
                    market=signal.market_ticker,
                    contracts=order_result.filled_count,
                    pnl=fill_value - position.cost_basis,
                )

        except Exception as e:
            result.errors.append(f"Exit order error: {e}")
            logger.error("exit_order_error", market=signal.market_ticker, error=str(e))

    async def _discover_markets(
        self,
        result: TradingCycleResult,
    ) -> list[DiscoveredMarket]:
        """Discover tradeable markets."""
        existing = {p.market_ticker for p in self._portfolio_state.positions}

        markets = await self._discovery.discover(
            force_refresh=False,
            existing_positions=existing if self.hybrid_config.skip_existing_positions else None,
        )

        result.markets_discovered = len(markets)
        return markets

    async def _analyze_and_trade(
        self,
        market: DiscoveredMarket,
        result: TradingCycleResult,
    ) -> None:
        """Analyze a market and potentially trade."""
        result.markets_analyzed += 1

        # Step 1: Get research (if available)
        research: ResearchResult | None = None
        if self._research_manager:
            try:
                query = ResearchQuery(
                    event_ticker=market.event_ticker,
                    market_ticker=market.market_ticker,
                    title=market.market_title,
                    research_type=ResearchType.PROBABILITY,
                )
                research = await self._research_manager.research(query)
                result.research_calls += 1
                result.research_cost_usd += research.cost_usd
            except Exception as e:
                logger.warning(
                    "research_error",
                    market=market.market_ticker,
                    error=str(e),
                )

        # Step 2: Run ensemble
        ensemble_result = await self._run_ensemble(market, research, result)

        if not ensemble_result.should_trade:
            if ensemble_result.final_action.value in ("hold", "skip"):
                result.hold_signals += 1 if ensemble_result.final_action.value == "hold" else 0
                result.skip_signals += 1 if ensemble_result.final_action.value == "skip" else 0
            return

        result.buy_signals += 1

        # Step 3: Generate strategy signals
        signals = self._generate_signals(market, ensemble_result)

        for signal in signals:
            if signal.signal_type == SignalType.ENTRY:
                result.entry_signals += 1
                await self._execute_entry(signal, ensemble_result, result)

    async def _run_ensemble(
        self,
        market: DiscoveredMarket,
        research: ResearchResult | None,
        result: TradingCycleResult,
    ) -> EnsembleResult:
        """Run ensemble analysis."""
        # Build context
        context = MarketContext(
            event_ticker=market.event_ticker,
            market_ticker=market.market_ticker,
            event_title=market.event_title,
            market_title=market.market_title,
            yes_price=market.yes_price,
            no_price=market.no_price,
            spread=market.spread_pct,
            volume_24h=market.volume_24h,
            hours_until_close=market.hours_until_close,
        )

        # Add research if available
        if research:
            context.research_summary = research.summary
            context.research_probability = research.primary_probability
            context.research_confidence = research.avg_confidence
            context.bullish_factors = [f.description for f in research.bullish_factors[:3]]
            context.bearish_factors = [f.description for f in research.bearish_factors[:3]]

        ensemble_result = await self._ensemble.analyze(context)

        result.ensemble_calls += 1
        result.ensemble_cost_usd += ensemble_result.total_cost_usd

        return ensemble_result

    def _generate_signals(
        self,
        market: DiscoveredMarket,
        ensemble: EnsembleResult,
    ) -> list[Signal]:
        """Generate strategy signals."""
        # Build strategy context
        context = StrategyContext(
            market=market.to_market_data(),
            ensemble_action=ensemble.final_action.value.upper(),
            ensemble_probability=ensemble.weighted_probability,
            ensemble_confidence=ensemble.consensus_confidence,
            ensemble_edge=ensemble.estimated_edge,
            bullish_factors=ensemble.bull_case.split("; ") if ensemble.bull_case else [],
            bearish_factors=ensemble.bear_case.split("; ") if ensemble.bear_case else [],
            has_position=self._portfolio_state.has_position(market.market_ticker),
        )

        signals: list[Signal] = []

        for strategy in self._strategies:
            try:
                strategy_signals = strategy.evaluate(context)
                signals.extend(strategy_signals)
            except Exception as e:
                logger.warning(
                    "strategy_error",
                    strategy=strategy.name,
                    market=market.market_ticker,
                    error=str(e),
                )

        return signals

    async def _execute_entry(
        self,
        signal: Signal,
        ensemble: EnsembleResult,
        result: TradingCycleResult,
    ) -> None:
        """Execute an entry order."""
        # Calculate position size
        size_request = SizeRequest(
            market_ticker=signal.market_ticker,
            event_ticker=signal.event_ticker,
            strategy=signal.strategy,
            side=signal.side,
            probability_estimate=ensemble.weighted_probability,
            confidence=ensemble.consensus_confidence,
            current_price=signal.target_price,
        )

        size_result = self._portfolio_optimizer.calculate_position_size(
            size_request,
            self._portfolio_state,
        )

        if not size_result.can_trade:
            logger.debug(
                "size_rejected",
                market=signal.market_ticker,
                reason=size_result.rejection_reason,
            )
            return

        # Create order
        order = CreateOrderRequest(
            ticker=signal.market_ticker,
            side=OrderSide.YES if signal.side == "YES" else OrderSide.NO,
            action=OrderAction.BUY,
            type=OrderType.LIMIT,
            count=size_result.recommended_contracts,
            yes_price=int(signal.target_price * 100) if signal.side == "YES" else None,
            no_price=int((1 - signal.target_price) * 100) if signal.side == "NO" else None,
        )

        try:
            order_result = await self._kalshi_client.place_order(order)
            result.orders_placed += 1

            if order_result.filled_count > 0:
                result.orders_filled += 1
                result.positions_opened += 1

                # Update portfolio state
                fill_price = order_result.avg_fill_price / 100 if order_result.avg_fill_price else signal.target_price
                cost = order_result.filled_count * fill_price

                position = Position(
                    market_ticker=signal.market_ticker,
                    event_ticker=signal.event_ticker,
                    side=signal.side,
                    strategy=signal.strategy,
                    contracts=order_result.filled_count,
                    entry_price=fill_price,
                    cost_basis=cost,
                    entry_time=datetime.utcnow(),
                    current_price=fill_price,
                )

                self._portfolio_state.positions.append(position)
                self._portfolio_state.cash_balance -= cost

                logger.info(
                    "entry_executed",
                    market=signal.market_ticker,
                    side=signal.side,
                    contracts=order_result.filled_count,
                    cost=cost,
                )

        except Exception as e:
            result.errors.append(f"Entry order error: {e}")
            logger.error("entry_order_error", market=signal.market_ticker, error=str(e))

    # ═══════════════════════════════════════════════════════════════════════════
    # STATUS AND MONITORING
    # ═══════════════════════════════════════════════════════════════════════════

    def get_status(self) -> dict[str, Any]:
        """Get current orchestrator status."""
        return {
            "is_running": self._is_running,
            "paper_mode": self.hybrid_config.paper_mode,
            "portfolio": self._portfolio_optimizer.get_portfolio_summary(self._portfolio_state)
            if self._portfolio_state and self._portfolio_optimizer
            else None,
            "discovery": self._discovery.get_discovery_stats() if self._discovery else None,
            "research": self._research_manager.get_stats() if self._research_manager else None,
            "last_cycle": {
                "started_at": self._last_cycle.started_at.isoformat() if self._last_cycle else None,
                "duration_seconds": self._last_cycle.duration_seconds if self._last_cycle else None,
                "markets_analyzed": self._last_cycle.markets_analyzed if self._last_cycle else None,
                "orders_placed": self._last_cycle.orders_placed if self._last_cycle else None,
                "cycle_pnl": self._last_cycle.cycle_pnl if self._last_cycle else None,
                "errors": self._last_cycle.errors if self._last_cycle else None,
            }
            if self._last_cycle
            else None,
        }

    @property
    def portfolio_state(self) -> PortfolioState | None:
        """Get current portfolio state."""
        return self._portfolio_state

    @property
    def last_cycle(self) -> TradingCycleResult | None:
        """Get last cycle result."""
        return self._last_cycle
