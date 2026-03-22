"""
Hybrid trading bot dashboard routes.

Provides web interface for the Kalshi hybrid AI trader.
"""

from datetime import datetime
from typing import Any

from flask import Blueprint, current_app, render_template, jsonify, request

hybrid_bp = Blueprint("hybrid", __name__)


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN VIEWS
# ═══════════════════════════════════════════════════════════════════════════════


@hybrid_bp.route("/")
def index():
    """Hybrid bot main dashboard."""
    settings = current_app.config["SETTINGS"]

    return render_template(
        "hybrid/index.html",
        title="Hybrid Trading Bot",
        paper_mode=settings.hybrid_trading.paper_mode,
    )


@hybrid_bp.route("/positions")
def positions():
    """Positions management page."""
    return render_template(
        "hybrid/positions.html",
        title="Open Positions",
    )


@hybrid_bp.route("/history")
def history():
    """Trade history page."""
    return render_template(
        "hybrid/history.html",
        title="Trade History",
    )


@hybrid_bp.route("/settings")
def settings_page():
    """Settings configuration page."""
    settings = current_app.config["SETTINGS"]

    return render_template(
        "hybrid/settings.html",
        title="Settings",
        settings=settings,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# HTMX PARTIALS
# ═══════════════════════════════════════════════════════════════════════════════


@hybrid_bp.route("/partials/status")
def partial_status():
    """HTMX partial for bot status."""
    # TODO: Get actual status from orchestrator
    status = _get_mock_status()

    return render_template(
        "hybrid/partials/status.html",
        status=status,
    )


@hybrid_bp.route("/partials/portfolio")
def partial_portfolio():
    """HTMX partial for portfolio summary."""
    # TODO: Get actual portfolio from orchestrator
    portfolio = _get_mock_portfolio()

    return render_template(
        "hybrid/partials/portfolio.html",
        portfolio=portfolio,
    )


@hybrid_bp.route("/partials/positions")
def partial_positions():
    """HTMX partial for open positions table."""
    # TODO: Get actual positions from database
    positions = _get_mock_positions()

    return render_template(
        "hybrid/partials/positions_table.html",
        positions=positions,
    )


@hybrid_bp.route("/partials/recent-trades")
def partial_recent_trades():
    """HTMX partial for recent trades."""
    # TODO: Get actual trades from database
    trades = _get_mock_trades()

    return render_template(
        "hybrid/partials/recent_trades.html",
        trades=trades,
    )


@hybrid_bp.route("/partials/strategy-allocation")
def partial_strategy_allocation():
    """HTMX partial for strategy allocation chart data."""
    settings = current_app.config["SETTINGS"]

    allocation = {
        "directional": settings.portfolio.directional_allocation * 100,
        "market_making": settings.portfolio.market_making_allocation * 100,
        "arbitrage": settings.portfolio.arbitrage_allocation * 100,
    }

    return render_template(
        "hybrid/partials/strategy_allocation.html",
        allocation=allocation,
    )


@hybrid_bp.route("/partials/exit-conditions/<market_ticker>")
def partial_exit_conditions(market_ticker: str):
    """HTMX partial for exit conditions of a position."""
    # TODO: Get actual exit conditions from exit manager
    conditions = _get_mock_exit_conditions(market_ticker)

    return render_template(
        "hybrid/partials/exit_conditions.html",
        market_ticker=market_ticker,
        conditions=conditions,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# API ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════


@hybrid_bp.route("/api/status")
def api_status():
    """API endpoint for bot status."""
    status = _get_mock_status()
    return jsonify(status)


@hybrid_bp.route("/api/portfolio")
def api_portfolio():
    """API endpoint for portfolio data."""
    portfolio = _get_mock_portfolio()
    return jsonify(portfolio)


@hybrid_bp.route("/api/positions")
def api_positions():
    """API endpoint for positions."""
    positions = _get_mock_positions()
    return jsonify(positions)


@hybrid_bp.route("/api/trades")
def api_trades():
    """API endpoint for trade history."""
    limit = request.args.get("limit", 50, type=int)
    trades = _get_mock_trades()[:limit]
    return jsonify(trades)


@hybrid_bp.route("/api/toggle-trading", methods=["POST"])
def api_toggle_trading():
    """Toggle trading on/off."""
    # TODO: Actually toggle trading
    data = request.get_json() or {}
    enabled = data.get("enabled", True)

    return jsonify({
        "success": True,
        "trading_enabled": enabled,
        "message": f"Trading {'enabled' if enabled else 'disabled'}",
    })


@hybrid_bp.route("/api/close-position", methods=["POST"])
def api_close_position():
    """Manually close a position."""
    data = request.get_json() or {}
    market_ticker = data.get("market_ticker")

    if not market_ticker:
        return jsonify({"success": False, "error": "market_ticker required"}), 400

    # TODO: Actually close position
    return jsonify({
        "success": True,
        "message": f"Position {market_ticker} closed",
    })


# ═══════════════════════════════════════════════════════════════════════════════
# MOCK DATA (Replace with actual data from orchestrator/database)
# ═══════════════════════════════════════════════════════════════════════════════


def _get_mock_status() -> dict[str, Any]:
    """Get mock bot status."""
    return {
        "is_running": True,
        "paper_mode": True,
        "last_cycle": datetime.utcnow().strftime("%H:%M:%S"),
        "cycles_today": 48,
        "next_cycle_in": "5:32",
        "errors_today": 0,
    }


def _get_mock_portfolio() -> dict[str, Any]:
    """Get mock portfolio data."""
    return {
        "total_value": 1052.35,
        "cash_balance": 752.35,
        "positions_value": 300.00,
        "total_pnl": 52.35,
        "total_pnl_pct": 5.24,
        "daily_pnl": 12.50,
        "daily_pnl_pct": 1.20,
        "open_positions": 4,
        "drawdown": 0.02,
        "high_water_mark": 1075.00,
        "strategy_allocation": {
            "directional": {"current": 0.35, "target": 0.50},
            "market_making": {"current": 0.25, "target": 0.40},
            "arbitrage": {"current": 0.00, "target": 0.10},
        },
    }


def _get_mock_positions() -> list[dict[str, Any]]:
    """Get mock positions."""
    return [
        {
            "market_ticker": "TRUMP-2024-WIN",
            "market_title": "Will Trump win 2024 election?",
            "side": "YES",
            "contracts": 100,
            "entry_price": 0.55,
            "current_price": 0.58,
            "cost_basis": 55.00,
            "current_value": 58.00,
            "pnl": 3.00,
            "pnl_pct": 5.45,
            "strategy": "directional",
            "entry_time": "2024-01-15 10:30",
            "hold_hours": 48,
        },
        {
            "market_ticker": "FED-RATE-MAR",
            "market_title": "Fed rate cut in March?",
            "side": "NO",
            "contracts": 50,
            "entry_price": 0.30,
            "current_price": 0.28,
            "cost_basis": 15.00,
            "current_value": 14.00,
            "pnl": -1.00,
            "pnl_pct": -6.67,
            "strategy": "directional",
            "entry_time": "2024-01-14 14:15",
            "hold_hours": 72,
        },
    ]


def _get_mock_trades() -> list[dict[str, Any]]:
    """Get mock trade history."""
    return [
        {
            "id": 1,
            "market_ticker": "BTC-50K-JAN",
            "side": "YES",
            "action": "sell",
            "contracts": 75,
            "price": 0.72,
            "value": 54.00,
            "pnl": 9.00,
            "pnl_pct": 20.0,
            "strategy": "directional",
            "exit_reason": "take_profit",
            "timestamp": "2024-01-15 09:00",
        },
        {
            "id": 2,
            "market_ticker": "ETH-3K-JAN",
            "side": "NO",
            "action": "sell",
            "contracts": 50,
            "price": 0.45,
            "value": 22.50,
            "pnl": -5.00,
            "pnl_pct": -18.2,
            "strategy": "market_making",
            "exit_reason": "stop_loss",
            "timestamp": "2024-01-14 16:30",
        },
    ]


def _get_mock_exit_conditions(market_ticker: str) -> dict[str, Any]:
    """Get mock exit conditions for a position."""
    return {
        "stop_loss": {
            "threshold": -0.15,
            "current": -0.05,
            "triggered": False,
            "distance": 0.10,
        },
        "take_profit": {
            "threshold": 0.20,
            "current": 0.05,
            "triggered": False,
            "distance": 0.15,
        },
        "trailing_stop": {
            "active": False,
            "threshold": 0.25,
            "high_water_mark": 58.00,
            "current_drawdown": 0.0,
        },
        "time_limit": {
            "max_days": 10,
            "current_days": 2,
            "triggered": False,
        },
        "expiry": {
            "hours_threshold": 4.0,
            "hours_remaining": 168.0,
            "triggered": False,
        },
    }
