"""
Kalshi Hybrid Trading Bot.

Multi-model AI ensemble + deep research + portfolio optimization.
"""

from polykalsh.hybrid.discovery import (
    BatchMarketFetcher,
    DiscoveredMarket,
    DiscoveryFilters,
    MarketDiscovery,
)
from polykalsh.hybrid.exit_manager import (
    ExitManager,
    ExitReason,
    ExitSignal,
    PositionState,
)
from polykalsh.hybrid.orchestrator import (
    HybridOrchestrator,
    TradingCycleResult,
)

__all__ = [
    # Discovery
    "MarketDiscovery",
    "DiscoveryFilters",
    "DiscoveredMarket",
    "BatchMarketFetcher",
    # Exit Management
    "ExitManager",
    "ExitReason",
    "ExitSignal",
    "PositionState",
    # Orchestrator
    "HybridOrchestrator",
    "TradingCycleResult",
]
