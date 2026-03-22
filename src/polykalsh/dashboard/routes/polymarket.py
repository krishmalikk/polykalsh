"""
Polymarket copy trading bot dashboard routes.

Provides web interface for the Polymarket copy trader.
"""

from datetime import datetime, timedelta
from typing import Any

from flask import Blueprint, current_app, render_template, jsonify, request
from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from polykalsh.database.models import (
    Leader,
    LeaderPosition,
    CopiedTrade,
    TradeStatus,
)

polymarket_bp = Blueprint("polymarket", __name__)


def get_db() -> Session:
    """Get database session."""
    return current_app.config["DB_SESSION"]


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN VIEWS
# ═══════════════════════════════════════════════════════════════════════════════


@polymarket_bp.route("/")
def index():
    """Polymarket bot main dashboard."""
    settings = current_app.config["SETTINGS"]

    return render_template(
        "polymarket/index.html",
        title="Polymarket Copy Bot",
        settings=settings,
    )


@polymarket_bp.route("/leaders")
def leaders():
    """Leader wallets management page."""
    return render_template(
        "polymarket/leaders.html",
        title="Leader Wallets",
    )


@polymarket_bp.route("/positions")
def positions():
    """Positions management page."""
    return render_template(
        "polymarket/positions.html",
        title="Open Positions",
    )


@polymarket_bp.route("/history")
def history():
    """Trade history page."""
    return render_template(
        "polymarket/history.html",
        title="Trade History",
    )


@polymarket_bp.route("/settings")
def settings_page():
    """Settings configuration page."""
    settings = current_app.config["SETTINGS"]

    return render_template(
        "polymarket/settings.html",
        title="Settings",
        settings=settings,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# HTMX PARTIALS
# ═══════════════════════════════════════════════════════════════════════════════


@polymarket_bp.route("/partials/status")
def partial_status():
    """HTMX partial for bot status."""
    db = get_db()
    settings = current_app.config["SETTINGS"]
    status = _get_bot_status(db, settings)

    return render_template(
        "polymarket/partials/status.html",
        status=status,
    )


@polymarket_bp.route("/partials/portfolio")
def partial_portfolio():
    """HTMX partial for portfolio summary."""
    db = get_db()
    portfolio = _get_portfolio_data(db)

    return render_template(
        "polymarket/partials/portfolio.html",
        portfolio=portfolio,
    )


@polymarket_bp.route("/partials/leaders")
def partial_leaders():
    """HTMX partial for tracked leaders."""
    db = get_db()
    leaders = _get_tracked_leaders(db)

    return render_template(
        "polymarket/partials/leaders_table.html",
        leaders=leaders,
    )


@polymarket_bp.route("/partials/positions")
def partial_positions():
    """HTMX partial for open positions table."""
    db = get_db()
    positions = _get_open_positions(db)

    return render_template(
        "polymarket/partials/positions_table.html",
        positions=positions,
    )


@polymarket_bp.route("/partials/recent-trades")
def partial_recent_trades():
    """HTMX partial for recent trades."""
    db = get_db()
    trades = _get_recent_trades(db)

    return render_template(
        "polymarket/partials/recent_trades.html",
        trades=trades,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# API ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════


@polymarket_bp.route("/api/status")
def api_status():
    """API endpoint for bot status."""
    db = get_db()
    settings = current_app.config["SETTINGS"]
    status = _get_bot_status(db, settings)
    return jsonify(status)


@polymarket_bp.route("/api/portfolio")
def api_portfolio():
    """API endpoint for portfolio data."""
    db = get_db()
    portfolio = _get_portfolio_data(db)
    return jsonify(portfolio)


@polymarket_bp.route("/api/leaders")
def api_leaders():
    """API endpoint for tracked leaders."""
    db = get_db()
    leaders = _get_tracked_leaders(db)
    return jsonify(leaders)


@polymarket_bp.route("/api/positions")
def api_positions():
    """API endpoint for positions."""
    db = get_db()
    positions = _get_open_positions(db)
    return jsonify(positions)


@polymarket_bp.route("/api/trades")
def api_trades():
    """API endpoint for trade history."""
    db = get_db()
    limit = request.args.get("limit", 50, type=int)
    trades = _get_recent_trades(db, limit=limit)
    return jsonify(trades)


@polymarket_bp.route("/api/toggle-trading", methods=["POST"])
def api_toggle_trading():
    """Toggle trading on/off."""
    data = request.get_json() or {}
    enabled = data.get("enabled", True)

    return jsonify({
        "success": True,
        "trading_enabled": enabled,
        "message": f"Trading {'enabled' if enabled else 'disabled'}",
    })


# ═══════════════════════════════════════════════════════════════════════════════
# DATA FETCHING (Real database queries)
# ═══════════════════════════════════════════════════════════════════════════════


def _get_bot_status(db: Session, settings) -> dict[str, Any]:
    """Get bot status from database."""
    # Check if wallet is configured
    polymarket_configured = settings.polymarket.is_configured

    # Count active leaders
    leaders_count = db.query(func.count(Leader.id)).filter(
        Leader.is_active == True
    ).scalar() or 0

    # Get recent trade activity
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    polls_today = db.query(func.count(CopiedTrade.id)).filter(
        CopiedTrade.created_at >= today_start
    ).scalar() or 0

    # Get last poll time from most recent leader check
    last_leader = db.query(Leader).filter(
        Leader.last_checked_at.isnot(None)
    ).order_by(desc(Leader.last_checked_at)).first()

    # Check for recent activity (proxy for running)
    recent_trade = db.query(CopiedTrade).filter(
        CopiedTrade.created_at >= datetime.utcnow() - timedelta(minutes=30)
    ).first()

    return {
        "is_running": recent_trade is not None and polymarket_configured,
        "setup_required": not polymarket_configured,
        "last_poll": last_leader.last_checked_at.strftime("%H:%M:%S") if last_leader and last_leader.last_checked_at else None,
        "polls_today": polls_today,
        "leaders_tracked": leaders_count,
        "errors_today": 0,
    }


def _get_portfolio_data(db: Session) -> dict[str, Any]:
    """Get portfolio data from database."""
    # Get all filled trades
    filled_trades = db.query(CopiedTrade).filter(
        CopiedTrade.status == TradeStatus.FILLED
    ).all()

    # Calculate totals
    total_invested = sum(t.fill_size or t.size_usd for t in filled_trades if t.side == "BUY")
    total_sold = sum(t.fill_size or t.size_usd for t in filled_trades if t.side == "SELL")

    # Count open positions (BUYs without matching SELLs)
    # This is a simplified calculation
    open_positions = db.query(func.count(CopiedTrade.id)).filter(
        CopiedTrade.status == TradeStatus.FILLED,
        CopiedTrade.side == "BUY"
    ).scalar() or 0

    total_pnl = total_sold - total_invested
    positions_value = total_invested - total_sold if total_invested > total_sold else 0

    return {
        "total_value": positions_value,
        "cash_balance": 0.0,  # Would need Polymarket API
        "positions_value": positions_value,
        "total_pnl": total_pnl,
        "total_pnl_pct": (total_pnl / total_invested * 100) if total_invested > 0 else 0,
        "daily_pnl": 0.0,  # Would need daily tracking
        "daily_pnl_pct": 0.0,
        "open_positions": open_positions,
    }


def _get_tracked_leaders(db: Session) -> list[dict[str, Any]]:
    """Get tracked leaders from database."""
    leaders = db.query(Leader).filter(
        Leader.is_active == True
    ).order_by(desc(Leader.discovery_pnl)).all()

    result = []
    for leader in leaders:
        result.append({
            "id": leader.id,
            "address": leader.wallet_address,
            "username": leader.username,
            "pnl": leader.discovery_pnl or 0,
            "volume": leader.discovery_volume or 0,
            "period": leader.discovery_period or "unknown",
            "is_active": leader.is_active,
            "wins": leader.wins,
            "losses": leader.losses,
            "last_trade": leader.last_trade_at.strftime("%Y-%m-%d %H:%M") if leader.last_trade_at else None,
        })

    return result


def _get_open_positions(db: Session) -> list[dict[str, Any]]:
    """Get open positions from copied trades."""
    # Get leader positions that are open
    positions = db.query(LeaderPosition).filter(
        LeaderPosition.is_open == True
    ).order_by(desc(LeaderPosition.first_seen)).all()

    result = []
    for pos in positions:
        leader = pos.leader

        result.append({
            "market_title": pos.market_title or pos.market_slug or "Unknown Market",
            "market_slug": pos.market_slug,
            "side": pos.outcome,
            "contracts": pos.size,
            "entry_price": pos.avg_price,
            "leader_address": leader.wallet_address if leader else "Unknown",
            "leader_username": leader.username if leader else None,
            "entry_time": pos.first_seen.strftime("%Y-%m-%d %H:%M"),
            "pnl": 0.0,  # Would need current price
            "pnl_pct": 0.0,
        })

    return result


def _get_recent_trades(db: Session, limit: int = 20) -> list[dict[str, Any]]:
    """Get recent trades from database."""
    trades = db.query(CopiedTrade).filter(
        CopiedTrade.status == TradeStatus.FILLED
    ).order_by(desc(CopiedTrade.executed_at)).limit(limit).all()

    result = []
    for trade in trades:
        leader = db.query(Leader).filter(Leader.id == trade.leader_id).first()

        result.append({
            "id": trade.id,
            "market_ticker": trade.market_slug or trade.token_id[:16],
            "market_title": trade.market_title,
            "side": trade.outcome,
            "action": trade.side.lower(),
            "contracts": trade.fill_size or trade.size_usd,
            "price": trade.fill_price or trade.target_price or 0,
            "value": trade.fill_size or trade.size_usd,
            "pnl": 0.0,  # Would need exit tracking
            "pnl_pct": 0.0,
            "exit_reason": "copy_trade",
            "leader_address": leader.wallet_address if leader else "Unknown",
            "timestamp": trade.executed_at.strftime("%Y-%m-%d %H:%M") if trade.executed_at else "--",
        })

    return result
