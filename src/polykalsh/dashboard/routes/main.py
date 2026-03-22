"""
Main dashboard routes.

Provides the main dashboard overview and navigation.
"""

from datetime import datetime

from flask import Blueprint, current_app, render_template

main_bp = Blueprint("main", __name__)


@main_bp.route("/")
def index():
    """Main dashboard page."""
    settings = current_app.config["SETTINGS"]

    # Count active bots
    active_bots = 0
    if settings.hybrid_trading.enabled:
        active_bots += 1
    if settings.copy_trader.enabled:
        active_bots += 1

    return render_template(
        "index.html",
        title="Polykalsh Dashboard",
        current_time=datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        kalshi_enabled=settings.hybrid_trading.enabled,
        copytrader_enabled=settings.copy_trader.enabled,
        active_bots=active_bots,
    )


@main_bp.route("/health")
def health():
    """Health check endpoint."""
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}
