"""
Trading strategies for the hybrid bot.

Strategies:
- Directional (50%): AI-predicted edge trades
- Market Making (40%): Bid-ask spread capture
- Arbitrage (10%): Cross-market mispricing (future)
"""

from polykalsh.hybrid.strategies.base import (
    BaseStrategy,
    MarketData,
    Signal,
    SignalStrength,
    SignalType,
    StrategyContext,
)
from polykalsh.hybrid.strategies.directional import (
    DirectionalStrategy,
    ExitSignalGenerator,
)
from polykalsh.hybrid.strategies.market_making import (
    MarketMakingStrategy,
    InventoryManager,
)

__all__ = [
    # Base
    "BaseStrategy",
    "MarketData",
    "Signal",
    "SignalStrength",
    "SignalType",
    "StrategyContext",
    # Directional
    "DirectionalStrategy",
    "ExitSignalGenerator",
    # Market Making
    "MarketMakingStrategy",
    "InventoryManager",
]
