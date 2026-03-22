"""
Kalshi async REST client.

Handles API requests with authentication, retries, and rate limiting.
"""

import asyncio
from datetime import datetime
from typing import Any
from urllib.parse import urlencode

import httpx
import structlog

from polykalsh.clients.kalshi.auth import KalshiAuth
from polykalsh.clients.kalshi.schemas import (
    Balance,
    BalanceResponse,
    CreateOrderRequest,
    Event,
    EventsResponse,
    Fill,
    FillsResponse,
    Market,
    MarketsResponse,
    MarketResponse,
    Order,
    OrderbookResponse,
    Orderbook,
    OrdersResponse,
    Position,
    PositionsResponse,
    Trade,
    TradesResponse,
    CreateOrderResponse,
)

logger = structlog.get_logger()


class KalshiClientError(Exception):
    """Base exception for Kalshi client errors."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class KalshiRateLimitError(KalshiClientError):
    """Rate limit exceeded."""

    pass


class KalshiAuthError(KalshiClientError):
    """Authentication failed."""

    pass


class KalshiClient:
    """
    Async Kalshi API client.

    Handles authentication, rate limiting, retries, and paper mode.
    """

    PROD_BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
    DEMO_BASE_URL = "https://demo-api.kalshi.co/trade-api/v2"

    def __init__(
        self,
        api_key_id: str,
        private_key_path: str,
        env: str = "prod",
        paper_mode: bool = True,
        max_retries: int = 3,
        timeout: float = 30.0,
    ):
        """
        Initialize Kalshi client.

        Args:
            api_key_id: Kalshi API key ID
            private_key_path: Path to RSA private key PEM file
            env: Environment ("prod" or "demo")
            paper_mode: If True, skip actual order placement
            max_retries: Max retry attempts for failed requests
            timeout: Request timeout in seconds
        """
        self.api_key_id = api_key_id
        self.private_key_path = private_key_path
        self.env = env
        self.paper_mode = paper_mode
        self.max_retries = max_retries
        self.timeout = timeout

        self.base_url = self.DEMO_BASE_URL if env == "demo" else self.PROD_BASE_URL
        self._auth: KalshiAuth | None = None
        self._client: httpx.AsyncClient | None = None

        # Rate limiting: 10 requests per second
        self._rate_limit = 10
        self._request_times: list[float] = []

        # Paper mode tracking
        self._paper_balance = 100000  # $1000 in cents
        self._paper_positions: dict[str, int] = {}
        self._paper_order_id = 0

    async def __aenter__(self) -> "KalshiClient":
        """Async context manager entry."""
        await self._ensure_client()
        return self

    async def __aexit__(self, *args: Any) -> None:
        """Async context manager exit."""
        await self.close()

    async def _ensure_client(self) -> None:
        """Ensure HTTP client and auth are initialized."""
        if self._auth is None:
            self._auth = KalshiAuth(self.api_key_id, self.private_key_path)

        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout),
                headers={"Content-Type": "application/json"},
            )

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _wait_for_rate_limit(self) -> None:
        """Wait if we're exceeding rate limit."""
        now = asyncio.get_event_loop().time()

        # Remove old timestamps (older than 1 second)
        self._request_times = [t for t in self._request_times if now - t < 1.0]

        # If at limit, wait
        if len(self._request_times) >= self._rate_limit:
            wait_time = 1.0 - (now - self._request_times[0])
            if wait_time > 0:
                await asyncio.sleep(wait_time)

        self._request_times.append(now)

    async def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Make an authenticated API request.

        Args:
            method: HTTP method
            path: API path (without base URL)
            params: Query parameters
            json: JSON body

        Returns:
            Response JSON

        Raises:
            KalshiClientError: On API errors
            KalshiRateLimitError: On rate limit
            KalshiAuthError: On auth failure
        """
        await self._ensure_client()
        await self._wait_for_rate_limit()

        # Build full URL
        url = f"{self.base_url}{path}"
        if params:
            url = f"{url}?{urlencode(params)}"

        # Get auth headers (sign path WITHOUT query params - Kalshi API requirement)
        full_path = f"/trade-api/v2{path}"
        auth_headers = self._auth.get_auth_headers(method, full_path)

        last_error: Exception | None = None

        for attempt in range(self.max_retries):
            try:
                response = await self._client.request(
                    method=method,
                    url=url,
                    headers=auth_headers,
                    json=json,
                )

                # Handle errors
                if response.status_code == 429:
                    wait_time = 2 ** attempt
                    logger.warning(
                        "rate_limited",
                        attempt=attempt,
                        wait_time=wait_time,
                    )
                    await asyncio.sleep(wait_time)
                    continue

                if response.status_code == 401:
                    raise KalshiAuthError(
                        "Authentication failed - check API key and private key",
                        status_code=401,
                    )

                if response.status_code >= 400:
                    error_text = response.text
                    raise KalshiClientError(
                        f"API error {response.status_code}: {error_text}",
                        status_code=response.status_code,
                    )

                return response.json()

            except httpx.TimeoutException as e:
                last_error = e
                logger.warning(
                    "request_timeout",
                    path=path,
                    attempt=attempt,
                )
                await asyncio.sleep(2 ** attempt)

            except httpx.RequestError as e:
                last_error = e
                logger.warning(
                    "request_error",
                    path=path,
                    attempt=attempt,
                    error=str(e),
                )
                await asyncio.sleep(2 ** attempt)

        raise KalshiClientError(f"Request failed after {self.max_retries} retries: {last_error}")

    # ═══════════════════════════════════════════════════════════════════════════
    # EVENTS
    # ═══════════════════════════════════════════════════════════════════════════

    async def get_events(
        self,
        limit: int = 100,
        cursor: str | None = None,
        status: str | None = None,
        series_ticker: str | None = None,
        with_nested_markets: bool = False,
    ) -> tuple[list[Event], str | None]:
        """
        Get list of events.

        Args:
            limit: Max events to return (1-200)
            cursor: Pagination cursor
            status: Filter by status ("open", "closed", "settled")
            series_ticker: Filter by series
            with_nested_markets: Include markets in response

        Returns:
            Tuple of (events, next_cursor)
        """
        params: dict[str, Any] = {"limit": min(limit, 200)}
        if cursor:
            params["cursor"] = cursor
        if status:
            params["status"] = status
        if series_ticker:
            params["series_ticker"] = series_ticker
        if with_nested_markets:
            params["with_nested_markets"] = "true"

        data = await self._request("GET", "/events", params=params)
        response = EventsResponse.model_validate(data)
        return response.events, response.cursor

    async def get_event(self, event_ticker: str) -> Event:
        """Get single event by ticker."""
        data = await self._request("GET", f"/events/{event_ticker}")
        return Event.model_validate(data["event"])

    async def get_all_events(
        self,
        status: str | None = "open",
        max_events: int = 500,
    ) -> list[Event]:
        """
        Get all events with pagination.

        Args:
            status: Filter by status
            max_events: Maximum events to fetch

        Returns:
            List of all events
        """
        events: list[Event] = []
        cursor: str | None = None

        while len(events) < max_events:
            batch, cursor = await self.get_events(
                limit=200,
                cursor=cursor,
                status=status,
            )
            events.extend(batch)

            if not cursor or not batch:
                break

        return events[:max_events]

    # ═══════════════════════════════════════════════════════════════════════════
    # MARKETS
    # ═══════════════════════════════════════════════════════════════════════════

    async def get_markets(
        self,
        limit: int = 100,
        cursor: str | None = None,
        event_ticker: str | None = None,
        status: str | None = None,
        tickers: list[str] | None = None,
    ) -> tuple[list[Market], str | None]:
        """
        Get list of markets.

        Args:
            limit: Max markets to return
            cursor: Pagination cursor
            event_ticker: Filter by event
            status: Filter by status
            tickers: Filter by specific tickers

        Returns:
            Tuple of (markets, next_cursor)
        """
        params: dict[str, Any] = {"limit": min(limit, 200)}
        if cursor:
            params["cursor"] = cursor
        if event_ticker:
            params["event_ticker"] = event_ticker
        if status:
            params["status"] = status
        if tickers:
            params["tickers"] = ",".join(tickers)

        data = await self._request("GET", "/markets", params=params)
        response = MarketsResponse.model_validate(data)
        return response.markets, response.cursor

    async def get_market(self, ticker: str) -> Market:
        """Get single market by ticker."""
        data = await self._request("GET", f"/markets/{ticker}")
        response = MarketResponse.model_validate(data)
        return response.market

    async def get_markets_for_event(self, event_ticker: str) -> list[Market]:
        """Get all markets for an event."""
        markets: list[Market] = []
        cursor: str | None = None

        while True:
            batch, cursor = await self.get_markets(
                limit=200,
                cursor=cursor,
                event_ticker=event_ticker,
            )
            markets.extend(batch)

            if not cursor or not batch:
                break

        return markets

    # ═══════════════════════════════════════════════════════════════════════════
    # ORDERBOOK
    # ═══════════════════════════════════════════════════════════════════════════

    async def get_orderbook(self, ticker: str, depth: int = 10) -> Orderbook:
        """
        Get market orderbook.

        Args:
            ticker: Market ticker
            depth: Number of levels to fetch

        Returns:
            Orderbook with yes/no levels
        """
        params = {"depth": depth}
        data = await self._request("GET", f"/markets/{ticker}/orderbook", params=params)
        response = OrderbookResponse.model_validate(data)
        return response.orderbook

    async def get_trades(
        self,
        ticker: str,
        limit: int = 100,
        cursor: str | None = None,
    ) -> tuple[list[Trade], str | None]:
        """
        Get recent trades for a market.

        Args:
            ticker: Market ticker
            limit: Max trades to return
            cursor: Pagination cursor

        Returns:
            Tuple of (trades, next_cursor)
        """
        params: dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor

        data = await self._request("GET", f"/markets/{ticker}/trades", params=params)
        response = TradesResponse.model_validate(data)
        return response.trades, response.cursor

    # ═══════════════════════════════════════════════════════════════════════════
    # ORDERS
    # ═══════════════════════════════════════════════════════════════════════════

    async def place_order(self, order: CreateOrderRequest) -> Order:
        """
        Place a new order.

        In paper mode, simulates the order without hitting the API.

        Args:
            order: Order request

        Returns:
            Created order
        """
        if self.paper_mode:
            return await self._paper_place_order(order)

        data = await self._request("POST", "/portfolio/orders", json=order.to_api_dict())
        response = CreateOrderResponse.model_validate(data)

        logger.info(
            "order_placed",
            ticker=order.ticker,
            side=order.side.value,
            action=order.action.value,
            count=order.count,
            order_id=response.order.order_id,
        )

        return response.order

    async def _paper_place_order(self, order: CreateOrderRequest) -> Order:
        """Simulate order in paper mode."""
        self._paper_order_id += 1
        order_id = f"PAPER-{self._paper_order_id}"

        # Simulate fill at limit price
        fill_price = order.yes_price or order.no_price or 50

        # Update paper position
        position_delta = order.count if order.action.value == "buy" else -order.count
        if order.side.value == "no":
            position_delta = -position_delta

        current_pos = self._paper_positions.get(order.ticker, 0)
        self._paper_positions[order.ticker] = current_pos + position_delta

        # Update paper balance
        cost = fill_price * order.count
        if order.action.value == "buy":
            self._paper_balance -= cost
        else:
            self._paper_balance += cost

        logger.info(
            "paper_order_placed",
            ticker=order.ticker,
            side=order.side.value,
            action=order.action.value,
            count=order.count,
            fill_price=fill_price,
            order_id=order_id,
            paper_balance=self._paper_balance,
        )

        from polykalsh.clients.kalshi.schemas import OrderStatus, OrderType

        return Order(
            order_id=order_id,
            ticker=order.ticker,
            side=order.side,
            action=order.action,
            type=order.type,
            status=OrderStatus.EXECUTED,
            count=order.count,
            remaining_count=0,
            filled_count=order.count,
            yes_price=order.yes_price,
            no_price=order.no_price,
            avg_fill_price=fill_price,
            created_time=datetime.utcnow(),
        )

    async def cancel_order(self, order_id: str) -> Order:
        """Cancel an open order."""
        if self.paper_mode:
            logger.info("paper_order_canceled", order_id=order_id)
            from polykalsh.clients.kalshi.schemas import (
                OrderStatus,
                OrderType,
                OrderSide,
                OrderAction,
            )

            return Order(
                order_id=order_id,
                ticker="PAPER",
                side=OrderSide.YES,
                action=OrderAction.BUY,
                type=OrderType.LIMIT,
                status=OrderStatus.CANCELED,
                count=0,
                remaining_count=0,
                filled_count=0,
            )

        data = await self._request("DELETE", f"/portfolio/orders/{order_id}")
        return Order.model_validate(data["order"])

    async def get_orders(
        self,
        ticker: str | None = None,
        status: str | None = None,
        limit: int = 100,
        cursor: str | None = None,
    ) -> tuple[list[Order], str | None]:
        """
        Get your orders.

        Args:
            ticker: Filter by market ticker
            status: Filter by status
            limit: Max orders to return
            cursor: Pagination cursor

        Returns:
            Tuple of (orders, next_cursor)
        """
        params: dict[str, Any] = {"limit": limit}
        if ticker:
            params["ticker"] = ticker
        if status:
            params["status"] = status
        if cursor:
            params["cursor"] = cursor

        data = await self._request("GET", "/portfolio/orders", params=params)
        response = OrdersResponse.model_validate(data)
        return response.orders, response.cursor

    # ═══════════════════════════════════════════════════════════════════════════
    # PORTFOLIO
    # ═══════════════════════════════════════════════════════════════════════════

    async def get_positions(
        self,
        ticker: str | None = None,
        event_ticker: str | None = None,
        limit: int = 100,
        cursor: str | None = None,
    ) -> tuple[list[Position], str | None]:
        """
        Get your positions.

        Args:
            ticker: Filter by market ticker
            event_ticker: Filter by event ticker
            limit: Max positions to return
            cursor: Pagination cursor

        Returns:
            Tuple of (positions, next_cursor)
        """
        if self.paper_mode:
            positions = [
                Position(
                    ticker=t,
                    event_ticker="PAPER",
                    position=p,
                    total_cost=abs(p) * 50,  # Assume 50c avg
                )
                for t, p in self._paper_positions.items()
                if p != 0
            ]
            return positions, None

        params: dict[str, Any] = {"limit": limit}
        if ticker:
            params["ticker"] = ticker
        if event_ticker:
            params["event_ticker"] = event_ticker
        if cursor:
            params["cursor"] = cursor

        data = await self._request("GET", "/portfolio/positions", params=params)
        response = PositionsResponse.model_validate(data)
        return response.market_positions, response.cursor

    async def get_balance(self) -> Balance:
        """Get your account balance."""
        if self.paper_mode:
            # Calculate paper portfolio value
            portfolio_value = sum(
                abs(p) * 50 for p in self._paper_positions.values()
            )
            return Balance(
                balance=self._paper_balance,
                portfolio_value=portfolio_value,
            )

        data = await self._request("GET", "/portfolio/balance")
        response = BalanceResponse.model_validate(data)
        return response.to_balance()

    async def get_fills(
        self,
        ticker: str | None = None,
        limit: int = 100,
        cursor: str | None = None,
    ) -> tuple[list[Fill], str | None]:
        """
        Get your fills.

        Args:
            ticker: Filter by market ticker
            limit: Max fills to return
            cursor: Pagination cursor

        Returns:
            Tuple of (fills, next_cursor)
        """
        if self.paper_mode:
            return [], None

        params: dict[str, Any] = {"limit": limit}
        if ticker:
            params["ticker"] = ticker
        if cursor:
            params["cursor"] = cursor

        data = await self._request("GET", "/portfolio/fills", params=params)
        response = FillsResponse.model_validate(data)
        return response.fills, response.cursor

    # ═══════════════════════════════════════════════════════════════════════════
    # CONVENIENCE METHODS
    # ═══════════════════════════════════════════════════════════════════════════

    async def get_top_events_by_volume(
        self,
        n: int = 50,
        status: str = "open",
    ) -> list[Event]:
        """
        Get top N events sorted by 24h volume.

        Args:
            n: Number of events to return
            status: Filter by status

        Returns:
            List of events sorted by volume_24h descending
        """
        events = await self.get_all_events(status=status, max_events=500)
        sorted_events = sorted(events, key=lambda e: e.volume_24h, reverse=True)
        return sorted_events[:n]

    async def get_top_markets_for_event(
        self,
        event_ticker: str,
        n: int = 10,
    ) -> list[Market]:
        """
        Get top N markets for an event sorted by volume.

        Args:
            event_ticker: Event ticker
            n: Number of markets to return

        Returns:
            List of markets sorted by volume_24h descending
        """
        markets = await self.get_markets_for_event(event_ticker)
        sorted_markets = sorted(markets, key=lambda m: m.volume_24h, reverse=True)
        return sorted_markets[:n]
