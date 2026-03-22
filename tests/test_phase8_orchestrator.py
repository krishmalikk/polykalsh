"""
Tests for Phase 8: Orchestrator and Discovery.

Tests the full trading loop in paper mode with mocked components.
"""

import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from polykalsh.clients.kalshi.schemas import (
    Balance,
    Event,
    Market,
    Order,
    OrderAction,
    OrderSide,
    OrderStatus,
    OrderType,
)
from polykalsh.hybrid.discovery import (
    DiscoveredMarket,
    DiscoveryFilters,
    MarketDiscovery,
)
from polykalsh.hybrid.orchestrator import HybridOrchestrator, TradingCycleResult
from polykalsh.hybrid.portfolio.optimizer import PortfolioState, Position, StrategyType


# ═══════════════════════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════════════════════


def create_mock_event(ticker: str = "TEST-EVENT", volume: int = 50000) -> Event:
    """Create a mock Kalshi event."""
    return Event(
        event_ticker=ticker,
        title=f"Test Event {ticker}",
        volume=volume,
        volume_24h=volume,
    )


def create_mock_market(
    ticker: str = "TEST-MARKET",
    event_ticker: str = "TEST-EVENT",
    yes_bid: int = 48,  # Tighter spread: 4 cents = ~8%
    yes_ask: int = 52,
    volume: int = 10000,
) -> Market:
    """Create a mock Kalshi market."""
    return Market(
        ticker=ticker,
        event_ticker=event_ticker,
        title=f"Test Market {ticker}",
        status="open",
        yes_bid=yes_bid,
        yes_ask=yes_ask,
        no_bid=100 - yes_ask,
        no_ask=100 - yes_bid,
        volume_24h=volume,
        open_interest=5000,
        liquidity=10000,
        close_time=datetime.utcnow() + timedelta(days=7),
    )


def create_mock_order(
    order_id: str = "ORDER-1",
    ticker: str = "TEST-MARKET",
    filled: bool = True,
) -> Order:
    """Create a mock order."""
    return Order(
        order_id=order_id,
        ticker=ticker,
        side=OrderSide.YES,
        action=OrderAction.BUY,
        type=OrderType.LIMIT,
        status=OrderStatus.EXECUTED if filled else OrderStatus.RESTING,
        count=10,
        remaining_count=0 if filled else 10,
        filled_count=10 if filled else 0,
        yes_price=50,
        avg_fill_price=50 if filled else None,
        created_time=datetime.utcnow(),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# DISCOVERY TESTS
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_discovery_filters():
    """Test discovery filter logic."""
    filters = DiscoveryFilters(
        min_volume_24h=1000,
        min_hours_to_close=4.0,
        max_days_to_expiry=30,
        min_price=0.05,
        max_price=0.95,
        max_spread_pct=0.15,  # 15% spread filter
    )

    # Create mock client
    mock_client = MagicMock()
    mock_client.get_top_events_by_volume = AsyncMock(
        return_value=[create_mock_event("EVENT-1", 50000)]
    )
    mock_client.get_top_markets_for_event = AsyncMock(
        return_value=[
            create_mock_market("MKT-1", "EVENT-1", 48, 52, 10000),  # Should pass
            create_mock_market("MKT-2", "EVENT-1", 48, 52, 500),  # Low volume
            create_mock_market("MKT-3", "EVENT-1", 2, 98, 10000),  # Extreme price
        ]
    )

    discovery = MarketDiscovery(
        client=mock_client,
        top_events=10,
        markets_per_event=10,
        filters=filters,
    )

    markets = await discovery.discover()

    # Only MKT-1 should pass filters
    assert len(markets) == 1
    assert markets[0].market_ticker == "MKT-1"


@pytest.mark.asyncio
async def test_discovery_caching():
    """Test that discovery caches results."""
    mock_client = MagicMock()
    mock_client.get_top_events_by_volume = AsyncMock(
        return_value=[create_mock_event()]
    )
    mock_client.get_top_markets_for_event = AsyncMock(
        return_value=[create_mock_market()]
    )

    discovery = MarketDiscovery(client=mock_client, top_events=10)

    # First call should hit API
    await discovery.discover()
    assert mock_client.get_top_events_by_volume.call_count == 1

    # Second call should use cache
    await discovery.discover()
    assert mock_client.get_top_events_by_volume.call_count == 1

    # Force refresh should hit API
    await discovery.discover(force_refresh=True)
    assert mock_client.get_top_events_by_volume.call_count == 2


@pytest.mark.asyncio
async def test_discovery_existing_positions():
    """Test that discovery skips existing positions."""
    mock_client = MagicMock()
    mock_client.get_top_events_by_volume = AsyncMock(
        return_value=[create_mock_event()]
    )
    mock_client.get_top_markets_for_event = AsyncMock(
        return_value=[
            create_mock_market("MKT-1"),
            create_mock_market("MKT-2"),
        ]
    )

    discovery = MarketDiscovery(client=mock_client, top_events=10)

    # Without existing positions
    markets = await discovery.discover()
    assert len(markets) == 2

    # With existing position in MKT-1
    markets = await discovery.discover(
        force_refresh=True,
        existing_positions={"MKT-1"},
    )
    assert len(markets) == 1
    assert markets[0].market_ticker == "MKT-2"


@pytest.mark.asyncio
async def test_discovered_market_conversion():
    """Test converting discovered market to MarketData."""
    event = create_mock_event()
    market = create_mock_market()

    discovered = DiscoveredMarket.from_kalshi(event, market)

    # Convert to MarketData
    market_data = discovered.to_market_data()

    assert market_data.market_ticker == market.ticker
    assert market_data.event_ticker == event.event_ticker
    assert market_data.yes_bid == 0.48  # 48 cents -> 0.48
    assert market_data.yes_ask == 0.52
    assert market_data.volume_24h == 10000


# ═══════════════════════════════════════════════════════════════════════════════
# PORTFOLIO STATE TESTS
# ═══════════════════════════════════════════════════════════════════════════════


def test_portfolio_state_calculations():
    """Test portfolio state calculations."""
    state = PortfolioState(
        cash_balance=800.0,
        starting_balance=1000.0,
        positions=[
            Position(
                market_ticker="MKT-1",
                event_ticker="EVENT-1",
                side="YES",
                strategy=StrategyType.DIRECTIONAL,
                contracts=100,
                entry_price=0.50,
                cost_basis=50.0,
                entry_time=datetime.utcnow(),
                current_price=0.60,
            ),
            Position(
                market_ticker="MKT-2",
                event_ticker="EVENT-2",
                side="NO",
                strategy=StrategyType.MARKET_MAKING,
                contracts=200,
                entry_price=0.40,
                cost_basis=80.0,
                entry_time=datetime.utcnow(),
                current_price=0.50,
            ),
        ],
        high_water_mark=1000.0,
    )

    # Position values: 100*0.60 = 60, 200*0.50 = 100
    assert state.positions_value == 160.0

    # Total value: 800 + 160 = 960
    assert state.total_value == 960.0

    # PnL: 960 - 1000 = -40
    assert state.total_pnl == -40.0

    # Drawdown: (1000 - 960) / 1000 = 0.04
    assert state.drawdown == 0.04

    # Has position
    assert state.has_position("MKT-1")
    assert not state.has_position("MKT-3")


# ═══════════════════════════════════════════════════════════════════════════════
# TRADING CYCLE RESULT TESTS
# ═══════════════════════════════════════════════════════════════════════════════


def test_trading_cycle_result():
    """Test trading cycle result dataclass."""
    result = TradingCycleResult(
        markets_discovered=50,
        markets_analyzed=20,
        ensemble_calls=20,
        ensemble_cost_usd=0.50,
        entry_signals=3,
        orders_placed=3,
        orders_filled=2,
        positions_opened=2,
        starting_value=1000.0,
        ending_value=1005.0,
    )

    result.ended_at = result.started_at + timedelta(seconds=30)
    result.duration_seconds = 30.0
    result.cycle_pnl = result.ending_value - result.starting_value

    assert result.cycle_pnl == 5.0
    assert result.duration_seconds == 30.0


# ═══════════════════════════════════════════════════════════════════════════════
# INTEGRATION TEST (MOCKED)
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_orchestrator_cycle_mocked():
    """Test a full trading cycle with mocked components."""
    # Create mock settings
    mock_settings = MagicMock()
    mock_settings.kalshi.api_key_id = "test-key"
    mock_settings.kalshi.private_key_path = "./test.pem"
    mock_settings.kalshi.env = "demo"

    mock_settings.hybrid_trading.enabled = True
    mock_settings.hybrid_trading.paper_mode = True
    mock_settings.hybrid_trading.paper_starting_balance = 1000.0
    mock_settings.hybrid_trading.top_events = 10
    mock_settings.hybrid_trading.markets_per_event = 5
    mock_settings.hybrid_trading.max_expiry_days = 30
    mock_settings.hybrid_trading.min_volume_24h = 1000
    mock_settings.hybrid_trading.skip_existing_positions = True
    mock_settings.hybrid_trading.max_position_pct = 0.10
    mock_settings.hybrid_trading.max_concurrent_positions = 20
    mock_settings.hybrid_trading.max_bet_amount_usd = 100.0
    mock_settings.hybrid_trading.scan_interval_min = 15

    mock_settings.ai_providers.anthropic_api_key = ""
    mock_settings.ai_providers.openrouter_api_key = ""
    mock_settings.ai_providers.perplexity_api_key = ""
    mock_settings.ai_providers.perplexity_model = "test"
    mock_settings.ai_providers.is_configured = False

    mock_settings.ensemble.min_consensus_confidence = 0.60
    mock_settings.ensemble.max_disagreement_spread = 0.30
    mock_settings.ensemble.min_edge_to_trade = 0.05

    mock_settings.portfolio.kelly_fraction = 0.75
    mock_settings.portfolio.directional_allocation = 0.50
    mock_settings.portfolio.market_making_allocation = 0.40
    mock_settings.portfolio.arbitrage_allocation = 0.10
    mock_settings.portfolio.max_daily_loss_pct = 0.15
    mock_settings.portfolio.max_drawdown_pct = 0.50

    mock_settings.exit_rules.trailing_take_profit_pct = 0.20
    mock_settings.exit_rules.stop_loss_pct = 0.15
    mock_settings.exit_rules.trailing_pullback_pct = 0.25
    mock_settings.exit_rules.max_hold_days = 10
    mock_settings.exit_rules.exit_hours_before_expiry = 4.0
    mock_settings.exit_rules.confidence_decay_threshold = 0.50
    mock_settings.exit_rules.confidence_recheck_hours = 24

    # Create mock DB session
    mock_db = MagicMock()

    # Create mock Kalshi client
    mock_kalshi = MagicMock()
    mock_kalshi._ensure_client = AsyncMock()
    mock_kalshi.get_balance = AsyncMock(
        return_value=Balance(balance=100000, portfolio_value=0)
    )
    mock_kalshi.get_positions = AsyncMock(return_value=([], None))
    mock_kalshi.get_top_events_by_volume = AsyncMock(
        return_value=[create_mock_event()]
    )
    mock_kalshi.get_top_markets_for_event = AsyncMock(
        return_value=[create_mock_market()]
    )
    mock_kalshi.place_order = AsyncMock(return_value=create_mock_order())
    mock_kalshi.close = AsyncMock()

    # Create orchestrator
    orchestrator = HybridOrchestrator(
        settings=mock_settings,
        db_session=mock_db,
        kalshi_client=mock_kalshi,
    )

    # Initialize
    await orchestrator.initialize()

    # Verify portfolio state
    assert orchestrator.portfolio_state is not None
    assert orchestrator.portfolio_state.cash_balance == 1000.0

    # Run one cycle
    result = await orchestrator.run_cycle()

    # Verify cycle completed
    assert result.ended_at is not None
    assert result.duration_seconds > 0
    assert result.markets_discovered >= 0

    # Verify status
    status = orchestrator.get_status()
    assert status["paper_mode"] is True
    assert status["last_cycle"] is not None

    # Cleanup
    await orchestrator.shutdown()

    print("\n" + "=" * 60)
    print("ORCHESTRATOR TEST RESULTS")
    print("=" * 60)
    print(f"Markets discovered: {result.markets_discovered}")
    print(f"Markets analyzed: {result.markets_analyzed}")
    print(f"Entry signals: {result.entry_signals}")
    print(f"Orders placed: {result.orders_placed}")
    print(f"Duration: {result.duration_seconds:.2f}s")
    print(f"Errors: {result.errors}")
    print("=" * 60)


if __name__ == "__main__":
    # Run basic tests
    print("Testing Discovery Filters...")
    asyncio.run(test_discovery_filters())
    print("PASS")

    print("\nTesting Discovery Caching...")
    asyncio.run(test_discovery_caching())
    print("PASS")

    print("\nTesting Existing Positions Skip...")
    asyncio.run(test_discovery_existing_positions())
    print("PASS")

    print("\nTesting Market Conversion...")
    asyncio.run(test_discovered_market_conversion())
    print("PASS")

    print("\nTesting Portfolio State...")
    test_portfolio_state_calculations()
    print("PASS")

    print("\nTesting Trading Cycle Result...")
    test_trading_cycle_result()
    print("PASS")

    print("\nTesting Full Orchestrator Cycle (Mocked)...")
    asyncio.run(test_orchestrator_cycle_mocked())
    print("PASS")

    print("\n" + "=" * 60)
    print("ALL PHASE 8 TESTS PASSED!")
    print("=" * 60)
