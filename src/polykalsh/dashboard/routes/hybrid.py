"""
Hybrid trading bot dashboard routes.

Provides web interface for the Kalshi hybrid AI trader.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from flask import Blueprint, current_app, render_template, jsonify, request

hybrid_bp = Blueprint("hybrid", __name__)


def _get_state_file() -> Path:
    """Get path to bot state file."""
    settings = current_app.config["SETTINGS"]
    return settings.data_dir / "hybrid_bot_state.json"


def _read_bot_state() -> dict:
    """Read bot state from file."""
    state_file = _get_state_file()
    try:
        if state_file.exists():
            with open(state_file) as f:
                return json.load(f)
    except Exception:
        pass
    return {
        "is_paused": False,
        "pause_reason": None,
        "is_running": False,
        "cycles_completed": 0,
        "cycles_failed": 0,
        "last_cycle_time": None,
        "daily_pnl": 0.0,
        "daily_trades": 0,
        "daily_ai_cost": 0.0,
    }


def _write_bot_state(state: dict) -> bool:
    """Write bot state to file."""
    state_file = _get_state_file()
    try:
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state["last_updated"] = datetime.utcnow().isoformat()
        with open(state_file, "w") as f:
            json.dump(state, f, indent=2)
        return True
    except Exception:
        return False


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
    state = _read_bot_state()
    settings = current_app.config["SETTINGS"]

    return jsonify({
        "success": True,
        "is_running": state.get("is_running", False),
        "is_paused": state.get("is_paused", False),
        "pause_reason": state.get("pause_reason"),
        "paper_mode": settings.hybrid_trading.paper_mode,
        "cycles_completed": state.get("cycles_completed", 0),
        "cycles_failed": state.get("cycles_failed", 0),
        "last_cycle_time": state.get("last_cycle_time"),
        "last_updated": state.get("last_updated"),
        "daily_stats": {
            "pnl": state.get("daily_pnl", 0.0),
            "trades": state.get("daily_trades", 0),
            "ai_cost": state.get("daily_ai_cost", 0.0),
        },
    })


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
    """Toggle trading on/off (pause/resume)."""
    state = _read_bot_state()
    is_paused = state.get("is_paused", False)

    # Toggle the state
    state["is_paused"] = not is_paused
    state["pause_reason"] = "Paused via dashboard" if not is_paused else None

    if _write_bot_state(state):
        return jsonify({
            "success": True,
            "is_paused": state["is_paused"],
            "message": f"Trading {'paused' if state['is_paused'] else 'resumed'}",
        })
    else:
        return jsonify({"success": False, "error": "Failed to update state"}), 500


@hybrid_bp.route("/api/bot/pause", methods=["POST"])
def api_bot_pause():
    """Pause the trading bot."""
    state = _read_bot_state()
    data = request.get_json() or {}

    state["is_paused"] = True
    state["pause_reason"] = data.get("reason", "Paused via dashboard")

    if _write_bot_state(state):
        return jsonify({
            "success": True,
            "paused": True,
            "reason": state["pause_reason"],
        })
    else:
        return jsonify({"success": False, "error": "Failed to update state"}), 500


@hybrid_bp.route("/api/bot/resume", methods=["POST"])
def api_bot_resume():
    """Resume the trading bot."""
    state = _read_bot_state()

    state["is_paused"] = False
    state["pause_reason"] = None

    if _write_bot_state(state):
        return jsonify({
            "success": True,
            "paused": False,
        })
    else:
        return jsonify({"success": False, "error": "Failed to update state"}), 500


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
    """Get bot status (real state + mock data for missing fields)."""
    state = _read_bot_state()
    settings = current_app.config["SETTINGS"]

    return {
        "is_running": state.get("is_running", False),
        "is_paused": state.get("is_paused", False),
        "pause_reason": state.get("pause_reason"),
        "paper_mode": settings.hybrid_trading.paper_mode,
        "last_cycle": state.get("last_cycle_time", datetime.utcnow().strftime("%H:%M:%S")),
        "cycles_today": state.get("cycles_completed", 0),
        "next_cycle_in": "5:00" if not state.get("is_paused") else "Paused",
        "errors_today": state.get("cycles_failed", 0),
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
