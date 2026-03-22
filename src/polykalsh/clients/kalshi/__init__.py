"""
Kalshi API client.

Provides async REST client with RSA-PSS authentication.
"""

from polykalsh.clients.kalshi.auth import KalshiAuth
from polykalsh.clients.kalshi.client import (
    KalshiClient,
    KalshiClientError,
    KalshiAuthError,
    KalshiRateLimitError,
)
from polykalsh.clients.kalshi.schemas import (
    # Enums
    OrderSide,
    OrderType,
    OrderStatus,
    OrderAction,
    # Events
    Event,
    EventsResponse,
    # Markets
    Market,
    MarketsResponse,
    MarketResponse,
    # Orderbook
    Orderbook,
    OrderbookLevel,
    OrderbookResponse,
    # Orders
    Order,
    OrdersResponse,
    CreateOrderRequest,
    CreateOrderResponse,
    # Positions
    Position,
    PositionsResponse,
    # Balance
    Balance,
    BalanceResponse,
    # Fills/Trades
    Fill,
    FillsResponse,
    Trade,
    TradesResponse,
)

__all__ = [
    # Auth
    "KalshiAuth",
    # Client
    "KalshiClient",
    "KalshiClientError",
    "KalshiAuthError",
    "KalshiRateLimitError",
    # Enums
    "OrderSide",
    "OrderType",
    "OrderStatus",
    "OrderAction",
    # Events
    "Event",
    "EventsResponse",
    # Markets
    "Market",
    "MarketsResponse",
    "MarketResponse",
    # Orderbook
    "Orderbook",
    "OrderbookLevel",
    "OrderbookResponse",
    # Orders
    "Order",
    "OrdersResponse",
    "CreateOrderRequest",
    "CreateOrderResponse",
    # Positions
    "Position",
    "PositionsResponse",
    # Balance
    "Balance",
    "BalanceResponse",
    # Fills/Trades
    "Fill",
    "FillsResponse",
    "Trade",
    "TradesResponse",
]
