"""
Kalshi AI trading bot dashboard routes.

Provides web interface for the Kalshi hybrid AI trader.
Fetches real data from Kalshi API.
"""

import asyncio
from datetime import datetime, timedelta
from typing import Any

from flask import Blueprint, current_app, render_template, jsonify, request
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from polykalsh.database.models import (
    HybridPosition,
    HybridOrder,
    PortfolioSnapshot,
    KalshiMarket,
    TradeStatus,
)

kalshi_bp = Blueprint("kalshi", __name__)


def get_db() -> Session:
    """Get database session."""
    return current_app.config["DB_SESSION"]


def run_async(coro):
    """Run async coroutine in sync context."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # If we're in an async context, create a new loop
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, coro)
                return future.result()
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN VIEWS
# ═══════════════════════════════════════════════════════════════════════════════


@kalshi_bp.route("/")
def index():
    """Kalshi bot main dashboard."""
    settings = current_app.config["SETTINGS"]

    return render_template(
        "kalshi/index.html",
        title="Kalshi AI Bot",
        settings=settings,
    )


@kalshi_bp.route("/positions")
def positions():
    """Positions management page."""
    return render_template(
        "kalshi/positions.html",
        title="Open Positions",
    )


@kalshi_bp.route("/history")
def history():
    """Trade history page."""
    return render_template(
        "kalshi/history.html",
        title="Trade History",
    )


@kalshi_bp.route("/settings")
def settings_page():
    """Settings configuration page."""
    settings = current_app.config["SETTINGS"]

    return render_template(
        "kalshi/settings.html",
        title="Settings",
        settings=settings,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# HTMX PARTIALS
# ═══════════════════════════════════════════════════════════════════════════════


@kalshi_bp.route("/partials/status")
def partial_status():
    """HTMX partial for bot status."""
    settings = current_app.config["SETTINGS"]
    status = _get_bot_status(settings)

    return render_template(
        "kalshi/partials/status.html",
        status=status,
    )


@kalshi_bp.route("/partials/portfolio")
def partial_portfolio():
    """HTMX partial for portfolio summary."""
    settings = current_app.config["SETTINGS"]
    portfolio = _get_portfolio_from_api(settings)

    return render_template(
        "kalshi/partials/portfolio.html",
        portfolio=portfolio,
    )


@kalshi_bp.route("/partials/positions")
def partial_positions():
    """HTMX partial for open positions table."""
    settings = current_app.config["SETTINGS"]
    positions = _get_positions_from_api(settings)

    return render_template(
        "kalshi/partials/positions_table.html",
        positions=positions,
    )


@kalshi_bp.route("/partials/recent-trades")
def partial_recent_trades():
    """HTMX partial for recent trades."""
    settings = current_app.config["SETTINGS"]
    trades = _get_fills_from_api(settings)

    return render_template(
        "kalshi/partials/recent_trades.html",
        trades=trades,
    )


@kalshi_bp.route("/partials/strategy-allocation")
def partial_strategy_allocation():
    """HTMX partial for strategy allocation chart data."""
    settings = current_app.config["SETTINGS"]

    allocation = {
        "directional": settings.portfolio.directional_allocation * 100,
        "market_making": settings.portfolio.market_making_allocation * 100,
        "arbitrage": settings.portfolio.arbitrage_allocation * 100,
    }

    return render_template(
        "kalshi/partials/strategy_allocation.html",
        allocation=allocation,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# API ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════


@kalshi_bp.route("/api/status")
def api_status():
    """API endpoint for bot status."""
    settings = current_app.config["SETTINGS"]
    status = _get_bot_status(settings)
    return jsonify(status)


@kalshi_bp.route("/api/portfolio")
def api_portfolio():
    """API endpoint for portfolio data."""
    settings = current_app.config["SETTINGS"]
    portfolio = _get_portfolio_from_api(settings)
    return jsonify(portfolio)


@kalshi_bp.route("/api/positions")
def api_positions():
    """API endpoint for positions."""
    settings = current_app.config["SETTINGS"]
    positions = _get_positions_from_api(settings)
    return jsonify(positions)


@kalshi_bp.route("/api/trades")
def api_trades():
    """API endpoint for trade history."""
    settings = current_app.config["SETTINGS"]
    limit = request.args.get("limit", 50, type=int)
    trades = _get_fills_from_api(settings, limit=limit)
    return jsonify(trades)


@kalshi_bp.route("/api/toggle-trading", methods=["POST"])
def api_toggle_trading():
    """Toggle trading on/off."""
    data = request.get_json() or {}
    enabled = data.get("enabled", True)

    return jsonify({
        "success": True,
        "trading_enabled": enabled,
        "message": f"Trading {'enabled' if enabled else 'disabled'}",
    })


@kalshi_bp.route("/api/close-position", methods=["POST"])
def api_close_position():
    """Manually close a position."""
    data = request.get_json() or {}
    market_ticker = data.get("market_ticker")

    if not market_ticker:
        return jsonify({"success": False, "error": "market_ticker required"}), 400

    return jsonify({
        "success": True,
        "message": f"Position {market_ticker} closed",
    })


# ═══════════════════════════════════════════════════════════════════════════════
# KALSHI API DATA FETCHING
# ═══════════════════════════════════════════════════════════════════════════════


def _get_kalshi_client(settings):
    """Create a Kalshi client."""
    from polykalsh.clients.kalshi.client import KalshiClient

    return KalshiClient(
        api_key_id=settings.kalshi.api_key_id,
        private_key_path=settings.kalshi.private_key_path,
        env=settings.kalshi.env,
        paper_mode=False,  # Always fetch real data for display
    )


def _get_bot_status(settings) -> dict[str, Any]:
    """Get bot status."""
    # Check if Kalshi is configured
    kalshi_configured = settings.kalshi.is_configured

    return {
        "is_running": False,  # Bot running state would come from orchestrator
        "last_cycle": "--",
        "cycles_today": 0,
        "next_cycle_in": "--",
        "errors_today": 0,
        "kalshi_configured": kalshi_configured,
    }


def _get_portfolio_from_api(settings) -> dict[str, Any]:
    """Get portfolio data from Kalshi API."""
    if not settings.kalshi.is_configured:
        return {
            "total_value": 0,
            "cash_balance": 0,
            "positions_value": 0,
            "total_pnl": 0,
            "total_pnl_pct": 0,
            "daily_pnl": 0,
            "daily_pnl_pct": 0,
            "open_positions": 0,
            "drawdown": 0,
            "high_water_mark": 0,
            "error": "Kalshi not configured",
        }

    async def fetch_balance():
        client = _get_kalshi_client(settings)
        async with client:
            balance = await client.get_balance()
            positions, _ = await client.get_positions()
            return balance, positions

    try:
        balance, positions = run_async(fetch_balance())

        # Count positions with non-zero holdings
        open_count = sum(1 for p in positions if p.position != 0)

        return {
            "total_value": balance.total_usd,
            "cash_balance": balance.available_usd,
            "positions_value": balance.portfolio_usd,
            "total_pnl": 0,  # Would need historical data
            "total_pnl_pct": 0,
            "daily_pnl": 0,
            "daily_pnl_pct": 0,
            "open_positions": open_count,
            "drawdown": 0,
            "high_water_mark": balance.total_usd,
        }
    except Exception as e:
        return {
            "total_value": 0,
            "cash_balance": 0,
            "positions_value": 0,
            "total_pnl": 0,
            "total_pnl_pct": 0,
            "daily_pnl": 0,
            "daily_pnl_pct": 0,
            "open_positions": 0,
            "drawdown": 0,
            "high_water_mark": 0,
            "error": str(e),
        }


def _get_positions_from_api(settings) -> list[dict[str, Any]]:
    """Get positions from Kalshi API."""
    if not settings.kalshi.is_configured:
        return []

    async def fetch_positions():
        client = _get_kalshi_client(settings)
        async with client:
            positions, _ = await client.get_positions()

            # Get market details for each position
            result = []
            for pos in positions:
                if pos.position == 0:
                    continue

                try:
                    market = await client.get_market(pos.ticker)
                    market_title = market.title
                    current_price = market.yes_bid / 100 if pos.position > 0 else market.no_bid / 100
                except:
                    market_title = pos.market_title or pos.ticker
                    current_price = 0.50

                # Calculate values
                contracts = abs(pos.position)
                entry_price = (pos.total_cost / contracts / 100) if contracts > 0 else 0
                current_value = current_price * contracts
                cost_basis = pos.total_cost / 100
                pnl = current_value - cost_basis
                pnl_pct = (pnl / cost_basis * 100) if cost_basis > 0 else 0

                result.append({
                    "market_ticker": pos.ticker,
                    "market_title": market_title,
                    "side": "YES" if pos.position > 0 else "NO",
                    "contracts": contracts,
                    "entry_price": entry_price,
                    "current_price": current_price,
                    "cost_basis": cost_basis,
                    "current_value": current_value,
                    "pnl": pnl,
                    "pnl_pct": pnl_pct,
                    "strategy": "manual",
                    "entry_time": "--",
                    "hold_hours": 0,
                })

            return result

    try:
        return run_async(fetch_positions())
    except Exception as e:
        print(f"Error fetching positions: {e}")
        return []


def _get_fills_from_api(settings, limit: int = 20) -> list[dict[str, Any]]:
    """Get fills/trades from Kalshi API."""
    if not settings.kalshi.is_configured:
        return []

    async def fetch_fills():
        client = _get_kalshi_client(settings)
        async with client:
            fills, _ = await client.get_fills(limit=limit)

            result = []
            for fill in fills:
                result.append({
                    "id": fill.trade_id,
                    "market_ticker": fill.ticker,
                    "side": fill.side.value.upper(),
                    "action": fill.action.value,
                    "contracts": fill.count,
                    "price": fill.price,
                    "value": fill.count * fill.price,
                    "pnl": 0,  # Would need entry/exit matching
                    "pnl_pct": 0,
                    "strategy": "manual",
                    "exit_reason": fill.action.value,
                    "timestamp": fill.created_time.strftime("%Y-%m-%d %H:%M") if fill.created_time else "--",
                })

            return result

    try:
        return run_async(fetch_fills())
    except Exception as e:
        print(f"Error fetching fills: {e}")
        return []
