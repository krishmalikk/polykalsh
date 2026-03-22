"""
Kalshi API response schemas.

Pydantic models for type-safe API responses.
"""

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


# ═══════════════════════════════════════════════════════════════════════════════
# ENUMS
# ═══════════════════════════════════════════════════════════════════════════════


class OrderSide(str, Enum):
    """Order side."""

    YES = "yes"
    NO = "no"


class OrderType(str, Enum):
    """Order type."""

    MARKET = "market"
    LIMIT = "limit"


class OrderStatus(str, Enum):
    """Order status."""

    RESTING = "resting"
    CANCELED = "canceled"
    EXECUTED = "executed"
    PENDING = "pending"


class OrderAction(str, Enum):
    """Order action (buy or sell)."""

    BUY = "buy"
    SELL = "sell"


# ═══════════════════════════════════════════════════════════════════════════════
# EVENTS
# ═══════════════════════════════════════════════════════════════════════════════


class Event(BaseModel):
    """Kalshi event (contains multiple markets)."""

    event_ticker: str
    series_ticker: str | None = None
    title: str
    subtitle: str | None = None
    category: str | None = None
    mutually_exclusive: bool = False

    # Volume metrics
    volume: int = Field(default=0, description="Total volume in cents")
    volume_24h: int = Field(default=0, description="24h volume in cents")
    open_interest: int = Field(default=0, description="Open interest in cents")

    # Timestamps
    strike_date: datetime | None = None


class EventsResponse(BaseModel):
    """Response from GET /events."""

    events: list[Event]
    cursor: str | None = None


# ═══════════════════════════════════════════════════════════════════════════════
# MARKETS
# ═══════════════════════════════════════════════════════════════════════════════


class Market(BaseModel):
    """Kalshi market (single yes/no contract)."""

    ticker: str
    event_ticker: str
    title: str
    subtitle: str | None = None
    status: str  # "active", "closed", "settled"
    result: str | None = None  # "yes", "no", None if unsettled

    # New API format: prices as dollar strings
    yes_bid_dollars: str = Field(default="0.0000", alias="yes_bid_dollars")
    yes_ask_dollars: str = Field(default="0.0000", alias="yes_ask_dollars")
    no_bid_dollars: str = Field(default="0.0000", alias="no_bid_dollars")
    no_ask_dollars: str = Field(default="0.0000", alias="no_ask_dollars")
    last_price_dollars: str = Field(default="0.0000", alias="last_price_dollars")

    # Volume as float strings
    volume_fp: str = Field(default="0.00", alias="volume_fp")
    volume_24h_fp: str = Field(default="0.00", alias="volume_24h_fp")
    open_interest_fp: str = Field(default="0.00", alias="open_interest_fp")
    liquidity_dollars: str = Field(default="0.0000", alias="liquidity_dollars")

    # Timestamps
    open_time: datetime | None = None
    close_time: datetime | None = None
    expiration_time: datetime | None = None

    model_config = {"populate_by_name": True}

    # Computed properties for backwards compatibility (in cents)
    @property
    def yes_bid(self) -> int:
        """Best yes bid in cents."""
        return int(float(self.yes_bid_dollars) * 100)

    @property
    def yes_ask(self) -> int:
        """Best yes ask in cents."""
        return int(float(self.yes_ask_dollars) * 100)

    @property
    def no_bid(self) -> int:
        """Best no bid in cents."""
        return int(float(self.no_bid_dollars) * 100)

    @property
    def no_ask(self) -> int:
        """Best no ask in cents."""
        return int(float(self.no_ask_dollars) * 100)

    @property
    def last_price(self) -> int | None:
        """Last trade price in cents."""
        val = float(self.last_price_dollars)
        return int(val * 100) if val > 0 else None

    @property
    def volume(self) -> int:
        """Total volume in contracts."""
        return int(float(self.volume_fp))

    @property
    def volume_24h(self) -> int:
        """24h volume in contracts."""
        return int(float(self.volume_24h_fp))

    @property
    def open_interest(self) -> int:
        """Open interest in contracts."""
        return int(float(self.open_interest_fp))

    @property
    def liquidity(self) -> int:
        """Orderbook liquidity in cents."""
        return int(float(self.liquidity_dollars) * 100)

    @property
    def yes_price(self) -> float:
        """Yes price as decimal (0.0-1.0)."""
        if self.yes_ask > 0:
            return self.yes_ask / 100
        lp = self.last_price
        return lp / 100 if lp else 0.5

    @property
    def no_price(self) -> float:
        """No price as decimal (0.0-1.0)."""
        if self.no_ask > 0:
            return self.no_ask / 100
        return 1.0 - self.yes_price

    @property
    def spread(self) -> float:
        """Bid-ask spread for yes side."""
        if self.yes_bid > 0 and self.yes_ask > 0:
            return (self.yes_ask - self.yes_bid) / 100
        return 0.0

    @property
    def mid_price(self) -> float:
        """Mid price for yes side."""
        if self.yes_bid > 0 and self.yes_ask > 0:
            return (self.yes_bid + self.yes_ask) / 200
        return self.yes_price


class MarketsResponse(BaseModel):
    """Response from GET /markets."""

    markets: list[Market]
    cursor: str | None = None


class MarketResponse(BaseModel):
    """Response from GET /markets/{ticker}."""

    market: Market


# ═══════════════════════════════════════════════════════════════════════════════
# ORDERBOOK
# ═══════════════════════════════════════════════════════════════════════════════


class OrderbookLevel(BaseModel):
    """Single level in the orderbook."""

    price: int = Field(description="Price in cents (1-99)")
    quantity: int = Field(description="Number of contracts")


class Orderbook(BaseModel):
    """Market orderbook."""

    ticker: str
    yes: list[OrderbookLevel] = Field(default_factory=list)
    no: list[OrderbookLevel] = Field(default_factory=list)

    @property
    def best_yes_bid(self) -> int | None:
        """Best bid price for yes contracts."""
        if self.yes:
            return max(level.price for level in self.yes)
        return None

    @property
    def best_yes_ask(self) -> int | None:
        """Best ask price for yes contracts (100 - best no bid)."""
        if self.no:
            return 100 - max(level.price for level in self.no)
        return None

    @property
    def yes_liquidity(self) -> int:
        """Total yes side liquidity in contracts."""
        return sum(level.quantity for level in self.yes)

    @property
    def no_liquidity(self) -> int:
        """Total no side liquidity in contracts."""
        return sum(level.quantity for level in self.no)


class OrderbookResponse(BaseModel):
    """Response from GET /markets/{ticker}/orderbook."""

    orderbook: Orderbook


# ═══════════════════════════════════════════════════════════════════════════════
# ORDERS
# ═══════════════════════════════════════════════════════════════════════════════


class Order(BaseModel):
    """Kalshi order."""

    order_id: str
    ticker: str
    client_order_id: str | None = None

    # Order details
    side: OrderSide
    action: OrderAction
    type: OrderType
    status: OrderStatus

    # Quantities
    count: int = Field(description="Total contracts ordered")
    remaining_count: int = Field(default=0, description="Unfilled contracts")
    filled_count: int = Field(default=0, description="Filled contracts")

    # Prices (in cents)
    yes_price: int | None = Field(default=None, description="Limit price for yes")
    no_price: int | None = Field(default=None, description="Limit price for no")
    avg_fill_price: int | None = Field(default=None, description="Average fill price")

    # Timestamps
    created_time: datetime | None = None
    updated_time: datetime | None = None
    expiration_time: datetime | None = None


class OrdersResponse(BaseModel):
    """Response from GET /portfolio/orders."""

    orders: list[Order]
    cursor: str | None = None


class CreateOrderRequest(BaseModel):
    """Request body for POST /portfolio/orders."""

    ticker: str
    side: OrderSide
    action: OrderAction
    type: OrderType
    count: int = Field(ge=1, description="Number of contracts")
    yes_price: int | None = Field(default=None, ge=1, le=99, description="Limit price")
    no_price: int | None = Field(default=None, ge=1, le=99)
    client_order_id: str | None = None
    expiration_ts: int | None = Field(default=None, description="Order expiry timestamp")

    def to_api_dict(self) -> dict:
        """Convert to API request format."""
        data = {
            "ticker": self.ticker,
            "side": self.side.value,
            "action": self.action.value,
            "type": self.type.value,
            "count": self.count,
        }
        if self.yes_price is not None:
            data["yes_price"] = self.yes_price
        if self.no_price is not None:
            data["no_price"] = self.no_price
        if self.client_order_id:
            data["client_order_id"] = self.client_order_id
        if self.expiration_ts:
            data["expiration_ts"] = self.expiration_ts
        return data


class CreateOrderResponse(BaseModel):
    """Response from POST /portfolio/orders."""

    order: Order


# ═══════════════════════════════════════════════════════════════════════════════
# POSITIONS
# ═══════════════════════════════════════════════════════════════════════════════


class Position(BaseModel):
    """Portfolio position."""

    ticker: str
    event_ticker: str
    market_title: str | None = None

    # Position details
    position: int = Field(description="Net position (+ for yes, - for no)")
    total_cost: int = Field(default=0, description="Total cost basis in cents")

    # Computed
    @property
    def side(self) -> Literal["yes", "no"] | None:
        """Position side."""
        if self.position > 0:
            return "yes"
        elif self.position < 0:
            return "no"
        return None

    @property
    def contracts(self) -> int:
        """Absolute number of contracts."""
        return abs(self.position)

    @property
    def avg_price(self) -> float:
        """Average entry price."""
        if self.contracts > 0 and self.total_cost > 0:
            return (self.total_cost / self.contracts) / 100
        return 0.0


class PositionsResponse(BaseModel):
    """Response from GET /portfolio/positions."""

    market_positions: list[Position] = Field(default_factory=list)
    cursor: str | None = None


# ═══════════════════════════════════════════════════════════════════════════════
# BALANCE
# ═══════════════════════════════════════════════════════════════════════════════


class Balance(BaseModel):
    """Portfolio balance."""

    balance: int = Field(description="Available balance in cents")
    portfolio_value: int = Field(default=0, description="Portfolio value in cents")

    @property
    def available_usd(self) -> float:
        """Available balance in USD."""
        return self.balance / 100

    @property
    def portfolio_usd(self) -> float:
        """Portfolio value in USD."""
        return self.portfolio_value / 100

    @property
    def total_usd(self) -> float:
        """Total account value in USD."""
        return (self.balance + self.portfolio_value) / 100


class BalanceResponse(BaseModel):
    """Response from GET /portfolio/balance."""

    balance: int
    portfolio_value: int = 0

    def to_balance(self) -> Balance:
        """Convert to Balance model."""
        return Balance(balance=self.balance, portfolio_value=self.portfolio_value)


# ═══════════════════════════════════════════════════════════════════════════════
# FILLS / TRADES
# ═══════════════════════════════════════════════════════════════════════════════


class Fill(BaseModel):
    """Order fill."""

    trade_id: str
    order_id: str
    ticker: str

    side: OrderSide
    action: OrderAction

    # Count can be string "935.00" or int
    count_fp: str = Field(alias="count_fp")

    # Prices are strings like "0.4400"
    yes_price_dollars: str = Field(alias="yes_price_dollars")
    no_price_dollars: str = Field(alias="no_price_dollars")

    created_time: datetime | None = None

    model_config = {"populate_by_name": True}

    @property
    def count(self) -> int:
        """Number of contracts as int."""
        return int(float(self.count_fp))

    @property
    def price(self) -> float:
        """Fill price as decimal (yes price)."""
        return float(self.yes_price_dollars)


class FillsResponse(BaseModel):
    """Response from GET /portfolio/fills."""

    fills: list[Fill]
    cursor: str | None = None


class Trade(BaseModel):
    """Public trade."""

    trade_id: str
    ticker: str
    count: int
    yes_price: int
    no_price: int
    created_time: datetime | None = None
    taker_side: str | None = None


class TradesResponse(BaseModel):
    """Response from GET /markets/{ticker}/trades."""

    trades: list[Trade]
    cursor: str | None = None
