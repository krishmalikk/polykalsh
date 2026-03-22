"""
Market discovery for the hybrid trading bot.

Discovers and filters tradeable markets from Kalshi.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import structlog

from polykalsh.clients.kalshi.client import KalshiClient
from polykalsh.clients.kalshi.schemas import Event, Market
from polykalsh.hybrid.strategies.base import MarketData

logger = structlog.get_logger()


@dataclass
class DiscoveryFilters:
    """Filters for market discovery."""

    # Volume filters
    min_volume_24h: int = 1000  # Minimum 24h volume in cents
    min_open_interest: int = 0

    # Time filters
    min_hours_to_close: float = 4.0
    max_days_to_expiry: int = 30

    # Price filters
    min_price: float = 0.05  # Avoid very low prices
    max_price: float = 0.95  # Avoid very high prices

    # Spread filters
    max_spread_pct: float = 0.15  # Max 15% spread

    # Status
    allowed_statuses: list[str] = field(default_factory=lambda: ["open"])


@dataclass
class DiscoveredMarket:
    """A discovered market with all relevant data."""

    # Event info
    event_ticker: str
    event_title: str

    # Market info
    market_ticker: str
    market_title: str

    # Pricing
    yes_price: float
    no_price: float
    yes_bid: float
    yes_ask: float
    no_bid: float
    no_ask: float
    spread_pct: float

    # Volume
    volume_24h: int
    open_interest: int
    liquidity: int

    # Timing
    close_time: datetime | None = None
    hours_until_close: float | None = None

    # Computed
    mid_price: float = 0.0

    @classmethod
    def from_kalshi(cls, event: Event, market: Market) -> "DiscoveredMarket":
        """Create from Kalshi event and market."""
        # Calculate spread percentage
        if market.yes_bid > 0 and market.yes_ask > 0:
            spread = (market.yes_ask - market.yes_bid) / 100
            mid = (market.yes_ask + market.yes_bid) / 200
            spread_pct = spread / mid if mid > 0 else 1.0
        else:
            spread_pct = 1.0
            mid = market.yes_price

        # Calculate hours until close
        hours_until_close = None
        if market.close_time:
            delta = market.close_time - datetime.utcnow()
            hours_until_close = max(0, delta.total_seconds() / 3600)

        return cls(
            event_ticker=event.event_ticker,
            event_title=event.title,
            market_ticker=market.ticker,
            market_title=market.title,
            yes_price=market.yes_price,
            no_price=market.no_price,
            yes_bid=market.yes_bid / 100 if market.yes_bid > 0 else 0.0,
            yes_ask=market.yes_ask / 100 if market.yes_ask > 0 else 0.0,
            no_bid=market.no_bid / 100 if market.no_bid > 0 else 0.0,
            no_ask=market.no_ask / 100 if market.no_ask > 0 else 0.0,
            spread_pct=spread_pct,
            volume_24h=market.volume_24h,
            open_interest=market.open_interest,
            liquidity=market.liquidity,
            close_time=market.close_time,
            hours_until_close=hours_until_close,
            mid_price=mid,
        )

    def to_market_data(self) -> MarketData:
        """Convert to MarketData for strategy evaluation."""
        return MarketData(
            event_ticker=self.event_ticker,
            market_ticker=self.market_ticker,
            event_title=self.event_title,
            market_title=self.market_title,
            yes_price=self.yes_price,
            no_price=self.no_price,
            yes_bid=self.yes_bid,
            yes_ask=self.yes_ask,
            no_bid=self.no_bid,
            no_ask=self.no_ask,
            volume_24h=self.volume_24h,
            open_interest=self.open_interest,
            liquidity=self.liquidity,
            close_time=self.close_time,
            hours_until_close=self.hours_until_close,
        )


class MarketDiscovery:
    """
    Discovers tradeable markets from Kalshi.

    Features:
    - Fetches top events by volume
    - Fetches top markets per event
    - Filters by volume, time, spread, etc.
    - Caches results to reduce API calls
    """

    def __init__(
        self,
        client: KalshiClient,
        top_events: int = 50,
        markets_per_event: int = 10,
        filters: DiscoveryFilters | None = None,
    ):
        """
        Initialize market discovery.

        Args:
            client: Kalshi API client
            top_events: Number of top events to fetch
            markets_per_event: Number of markets to fetch per event
            filters: Discovery filters
        """
        self.client = client
        self.top_events = top_events
        self.markets_per_event = markets_per_event
        self.filters = filters or DiscoveryFilters()

        # Cache
        self._cache: list[DiscoveredMarket] = []
        self._cache_time: datetime | None = None
        self._cache_ttl_minutes = 5

    async def discover(
        self,
        force_refresh: bool = False,
        existing_positions: set[str] | None = None,
    ) -> list[DiscoveredMarket]:
        """
        Discover tradeable markets.

        Args:
            force_refresh: Skip cache
            existing_positions: Market tickers to skip (already have positions)

        Returns:
            List of discovered markets sorted by volume
        """
        existing_positions = existing_positions or set()

        # Check cache
        if not force_refresh and self._is_cache_valid():
            logger.debug("discovery_cache_hit", markets=len(self._cache))
            return [m for m in self._cache if m.market_ticker not in existing_positions]

        logger.info("discovery_start", top_events=self.top_events)

        # Fetch top events
        events = await self.client.get_top_events_by_volume(
            n=self.top_events,
            status="open",
        )

        logger.info("discovery_events", count=len(events))

        # Fetch markets for each event
        all_markets: list[DiscoveredMarket] = []

        for event in events:
            try:
                markets = await self.client.get_top_markets_for_event(
                    event_ticker=event.event_ticker,
                    n=self.markets_per_event,
                )

                for market in markets:
                    if market.status not in self.filters.allowed_statuses:
                        continue

                    discovered = DiscoveredMarket.from_kalshi(event, market)

                    # Apply filters
                    if self._passes_filters(discovered):
                        all_markets.append(discovered)

            except Exception as e:
                logger.warning(
                    "discovery_event_error",
                    event=event.event_ticker,
                    error=str(e),
                )

        # Sort by volume
        all_markets.sort(key=lambda m: m.volume_24h, reverse=True)

        # Update cache
        self._cache = all_markets
        self._cache_time = datetime.utcnow()

        logger.info(
            "discovery_complete",
            total_markets=len(all_markets),
            skipped_existing=len(existing_positions),
        )

        return [m for m in all_markets if m.market_ticker not in existing_positions]

    def _passes_filters(self, market: DiscoveredMarket) -> bool:
        """Check if market passes all filters."""
        f = self.filters

        # Volume
        if market.volume_24h < f.min_volume_24h:
            return False

        if market.open_interest < f.min_open_interest:
            return False

        # Time
        if market.hours_until_close is not None:
            if market.hours_until_close < f.min_hours_to_close:
                return False

            max_hours = f.max_days_to_expiry * 24
            if market.hours_until_close > max_hours:
                return False

        # Price
        if market.yes_price < f.min_price or market.yes_price > f.max_price:
            return False

        # Spread
        if market.spread_pct > f.max_spread_pct:
            return False

        return True

    def _is_cache_valid(self) -> bool:
        """Check if cache is still valid."""
        if not self._cache or self._cache_time is None:
            return False

        age = (datetime.utcnow() - self._cache_time).total_seconds() / 60
        return age < self._cache_ttl_minutes

    def get_discovery_stats(self) -> dict[str, Any]:
        """Get discovery statistics."""
        return {
            "cached_markets": len(self._cache),
            "cache_age_minutes": (
                (datetime.utcnow() - self._cache_time).total_seconds() / 60
                if self._cache_time
                else None
            ),
            "filters": {
                "min_volume_24h": self.filters.min_volume_24h,
                "min_hours_to_close": self.filters.min_hours_to_close,
                "max_spread_pct": self.filters.max_spread_pct,
            },
        }


class BatchMarketFetcher:
    """
    Efficiently fetches market data for multiple tickers.

    Used for updating prices on existing positions.
    """

    def __init__(self, client: KalshiClient):
        """Initialize batch fetcher."""
        self.client = client

    async def fetch_markets(
        self,
        tickers: list[str],
        batch_size: int = 20,
    ) -> dict[str, Market]:
        """
        Fetch multiple markets by ticker.

        Args:
            tickers: List of market tickers
            batch_size: Max tickers per API call

        Returns:
            Dict mapping ticker to Market
        """
        if not tickers:
            return {}

        results: dict[str, Market] = {}

        # Batch requests
        for i in range(0, len(tickers), batch_size):
            batch = tickers[i : i + batch_size]

            try:
                markets, _ = await self.client.get_markets(tickers=batch)

                for market in markets:
                    results[market.ticker] = market

            except Exception as e:
                logger.warning(
                    "batch_fetch_error",
                    batch_start=i,
                    error=str(e),
                )

        return results

    async def get_current_prices(
        self,
        tickers: list[str],
    ) -> dict[str, tuple[float, float]]:
        """
        Get current YES/NO prices for multiple markets.

        Args:
            tickers: List of market tickers

        Returns:
            Dict mapping ticker to (yes_price, no_price)
        """
        markets = await self.fetch_markets(tickers)

        return {
            ticker: (market.yes_price, market.no_price)
            for ticker, market in markets.items()
        }
