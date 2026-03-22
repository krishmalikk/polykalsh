"""
Kalshi Market Advisor dashboard routes.

Provides chat interface for AI-powered market analysis and trade recommendations.
"""

import asyncio
import concurrent.futures
from typing import Any

from flask import Blueprint, current_app, render_template, jsonify, request

from polykalsh.advisor import ChatAdvisor, AdvisorConfig, AutoAdvisor, AutoAdvisorConfig
from polykalsh.clients.kalshi.client import KalshiClient

advisor_bp = Blueprint("advisor", __name__)

# Store advisor instances per session (in production, use proper session management)
_advisors: dict[str, ChatAdvisor] = {}
_auto_advisor: AutoAdvisor | None = None


def _create_kalshi_client(settings) -> KalshiClient:
    """Create a new Kalshi client instance."""
    return KalshiClient(
        api_key_id=settings.kalshi.api_key_id,
        private_key_path=settings.kalshi.private_key_path,
        env=settings.kalshi.env,
        paper_mode=False,
    )


def _get_advisor() -> ChatAdvisor:
    """Get or create advisor instance for current session."""
    session_id = "default"  # In production, use actual session ID
    settings = current_app.config["SETTINGS"]

    # Check if AI is configured
    if not settings.ai_providers.anthropic_api_key:
        raise ValueError("Anthropic API key not configured")

    if session_id not in _advisors:
        # Create Kalshi client (will be recreated per request as needed)
        kalshi_client = _create_kalshi_client(settings)

        # Create advisor
        _advisors[session_id] = ChatAdvisor(
            kalshi_client=kalshi_client,
            settings=settings,
            config=AdvisorConfig(),
        )
    else:
        # Refresh the Kalshi client to avoid closed session issues
        _advisors[session_id].kalshi = _create_kalshi_client(settings)

    return _advisors[session_id]


def _run_async(coro):
    """Run async coroutine in sync context."""
    # Always use a fresh event loop in a thread pool to avoid loop conflicts
    with concurrent.futures.ThreadPoolExecutor() as pool:
        future = pool.submit(asyncio.run, coro)
        return future.result(timeout=60)


def _run_async_simple(coro):
    """Simple async runner for when we know loop state."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, coro)
                return future.result()
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN VIEWS
# ═══════════════════════════════════════════════════════════════════════════════


@advisor_bp.route("/")
def chat_page():
    """Main chat interface."""
    settings = current_app.config["SETTINGS"]

    # Check configuration
    kalshi_configured = settings.kalshi.is_configured
    ai_configured = bool(settings.ai_providers.anthropic_api_key)

    return render_template(
        "advisor/chat.html",
        title="Market Advisor",
        kalshi_configured=kalshi_configured,
        ai_configured=ai_configured,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# HTMX PARTIALS
# ═══════════════════════════════════════════════════════════════════════════════


@advisor_bp.route("/partials/portfolio")
def partial_portfolio():
    """HTMX partial for portfolio sidebar."""
    try:
        advisor = _get_advisor()
        portfolio = advisor.get_portfolio_summary()
        recommendations = advisor.get_pending_recommendations()

        return render_template(
            "advisor/partials/portfolio.html",
            portfolio=portfolio,
            recommendations=recommendations,
        )
    except Exception as e:
        return render_template(
            "advisor/partials/portfolio.html",
            error=str(e),
            portfolio=None,
            recommendations=[],
        )


@advisor_bp.route("/partials/recommendations")
def partial_recommendations():
    """HTMX partial for pending recommendations."""
    try:
        advisor = _get_advisor()
        recommendations = advisor.get_pending_recommendations()

        return render_template(
            "advisor/partials/recommendations.html",
            recommendations=recommendations,
        )
    except Exception as e:
        return render_template(
            "advisor/partials/recommendations.html",
            error=str(e),
            recommendations=[],
        )


# ═══════════════════════════════════════════════════════════════════════════════
# API ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════


@advisor_bp.route("/api/chat", methods=["POST"])
def api_chat():
    """Process chat message and return AI response."""
    data = request.get_json() or {}
    message = data.get("message", "").strip()

    if not message:
        return jsonify({"success": False, "error": "Message required"}), 400

    try:
        advisor = _get_advisor()

        # Need to enter async context for Kalshi client
        async def run_chat():
            async with advisor.kalshi:
                return advisor.chat(message)

        response_text, new_recs = _run_async(run_chat())

        return jsonify({
            "success": True,
            "response": response_text,
            "recommendations": [
                {
                    "id": rec.id,
                    "market_ticker": rec.market_ticker,
                    "market_title": rec.market_title,
                    "side": rec.side,
                    "contracts": rec.suggested_contracts,
                    "price": f"${rec.current_price:.2f}",
                    "amount": f"${rec.suggested_amount:.2f}",
                    "edge": f"{rec.edge*100:+.1f}%",
                    "reasoning": rec.reasoning,
                    "risks": rec.risks,
                }
                for rec in new_recs
            ],
        })

    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"success": False, "error": f"Chat failed: {str(e)}"}), 500


@advisor_bp.route("/api/confirm/<rec_id>", methods=["POST"])
def api_confirm_trade(rec_id: str):
    """Confirm and execute a trade recommendation."""
    try:
        advisor = _get_advisor()

        async def run_confirm():
            async with advisor.kalshi:
                return await advisor.confirm_trade(rec_id)

        result = _run_async(run_confirm())
        return jsonify(result)

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@advisor_bp.route("/api/reject/<rec_id>", methods=["POST"])
def api_reject_trade(rec_id: str):
    """Reject a trade recommendation."""
    try:
        advisor = _get_advisor()
        result = advisor.reject_recommendation(rec_id)
        return jsonify(result)

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@advisor_bp.route("/api/clear", methods=["POST"])
def api_clear_chat():
    """Clear chat history."""
    try:
        advisor = _get_advisor()
        advisor.clear_history()
        advisor.clear_recommendations()
        return jsonify({"success": True, "message": "Chat cleared"})

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@advisor_bp.route("/api/portfolio")
def api_portfolio():
    """API endpoint for portfolio data."""
    try:
        advisor = _get_advisor()

        # get_portfolio_summary handles async internally via _run_async
        portfolio = advisor.get_portfolio_summary()

        return jsonify({
            "total_value": portfolio.total_value,
            "available_cash": portfolio.available_cash,
            "positions_value": portfolio.positions_value,
            "open_positions": portfolio.open_positions_count,
            "pending_recommendations": portfolio.pending_recommendations_count,
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════════════
# AUTO-ADVISOR ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════


def _get_auto_advisor() -> AutoAdvisor:
    """Get or create auto-advisor instance."""
    global _auto_advisor
    settings = current_app.config["SETTINGS"]

    if not settings.ai_providers.anthropic_api_key:
        raise ValueError("Anthropic API key not configured")

    if _auto_advisor is None:
        kalshi_client = _create_kalshi_client(settings)
        _auto_advisor = AutoAdvisor(
            kalshi_client=kalshi_client,
            settings=settings,
            config=AutoAdvisorConfig(),
        )
    else:
        # Refresh the Kalshi client to avoid closed session issues
        _auto_advisor.kalshi = _create_kalshi_client(settings)

    return _auto_advisor


@advisor_bp.route("/partials/auto-dashboard")
def partial_auto_dashboard():
    """HTMX partial for the auto-recommendations dashboard."""
    try:
        auto_advisor = _get_auto_advisor()
        cache = auto_advisor.get_recommendations_cache()

        # Get filter from query params
        filter_type = request.args.get("filter", "all")

        # Filter recommendations based on filter type
        all_recommendations = cache.recommendations
        if filter_type == "today":
            filtered = [r for r in all_recommendations if r.time_bucket == "today"]
        elif filter_type == "this_week":
            filtered = [r for r in all_recommendations if r.time_bucket in ["today", "this_week"]]
        elif filter_type == "sports":
            filtered = [r for r in all_recommendations if r.category == "Sports"]
        else:
            filtered = all_recommendations

        # Get portfolio for display
        try:
            advisor = _get_advisor()
            portfolio = advisor.get_portfolio_summary()
        except Exception:
            portfolio = None

        return render_template(
            "advisor/partials/auto_dashboard.html",
            recommendations=filtered,
            all_count=len(all_recommendations),
            filtered_count=len(filtered),
            active_filter=filter_type,
            cache=cache,
            portfolio=portfolio,
        )
    except Exception as e:
        return render_template(
            "advisor/partials/auto_dashboard.html",
            error=str(e),
            recommendations=[],
            all_count=0,
            filtered_count=0,
            active_filter="all",
            cache=None,
            portfolio=None,
        )


@advisor_bp.route("/api/auto-recommendations")
def api_auto_recommendations():
    """Get cached auto-recommendations."""
    try:
        auto_advisor = _get_auto_advisor()
        cache = auto_advisor.get_recommendations_cache()

        return jsonify({
            "success": True,
            "recommendations": [
                {
                    "id": rec.id,
                    "market_ticker": rec.market_ticker,
                    "market_title": rec.market_title,
                    "event_title": rec.event_title,
                    "side": rec.side,
                    "yes_price": rec.yes_price,
                    "no_price": rec.no_price,
                    "edge": rec.edge,
                    "confidence": rec.confidence,
                    "contracts": rec.suggested_contracts,
                    "amount": rec.suggested_amount,
                    "profit_potential": rec.profit_potential,
                    "max_loss": rec.max_loss,
                    "reasoning": rec.reasoning,
                    "bull_case": rec.bull_case,
                    "bear_case": rec.bear_case,
                    "risk_level": rec.risk_level,
                    "risk_details": rec.risk_details,
                    "hours_until_close": rec.hours_until_close,
                    "status": rec.status,
                    "category": rec.category,
                    "time_bucket": rec.time_bucket,
                }
                for rec in cache.recommendations
            ],
            "generated_at": cache.generated_at.isoformat() if cache.generated_at else None,
            "expires_at": cache.expires_at.isoformat() if cache.expires_at else None,
            "is_stale": cache.is_stale,
            "status": cache.analysis_status,
            "balance": cache.balance_at_generation,
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@advisor_bp.route("/api/auto-recommendations/refresh", methods=["POST"])
def api_refresh_recommendations():
    """Trigger manual refresh of auto-recommendations."""
    try:
        auto_advisor = _get_auto_advisor()

        async def run_analysis():
            async with auto_advisor.kalshi:
                auto_advisor._active_client = auto_advisor.kalshi
                try:
                    return await auto_advisor.analyze_markets(force=True)
                finally:
                    auto_advisor._active_client = None

        recommendations = _run_async(run_analysis())

        return jsonify({
            "success": True,
            "message": "Analysis complete",
            "count": len(recommendations),
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@advisor_bp.route("/api/auto-recommendations/confirm/<rec_id>", methods=["POST"])
def api_confirm_auto_recommendation(rec_id: str):
    """Confirm and execute an auto-recommendation."""
    try:
        auto_advisor = _get_auto_advisor()

        async def run_confirm():
            async with auto_advisor.kalshi:
                auto_advisor._active_client = auto_advisor.kalshi
                try:
                    return await auto_advisor.confirm_recommendation(rec_id)
                finally:
                    auto_advisor._active_client = None

        result = _run_async(run_confirm())
        return jsonify(result)

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@advisor_bp.route("/api/auto-recommendations/reject/<rec_id>", methods=["POST"])
def api_reject_auto_recommendation(rec_id: str):
    """Reject an auto-recommendation."""
    try:
        auto_advisor = _get_auto_advisor()
        result = auto_advisor.reject_recommendation(rec_id)
        return jsonify(result)

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ═══════════════════════════════════════════════════════════════════════════════
# BOT CONTROL ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════


@advisor_bp.route("/api/bot/status")
def api_bot_status():
    """Get the current status of the auto-advisor bot."""
    try:
        auto_advisor = _get_auto_advisor()
        status = auto_advisor.get_status()
        return jsonify({"success": True, **status})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@advisor_bp.route("/api/bot/pause", methods=["POST"])
def api_bot_pause():
    """Pause the auto-advisor bot."""
    try:
        auto_advisor = _get_auto_advisor()
        data = request.get_json() or {}
        reason = data.get("reason", "Paused by user")
        result = auto_advisor.pause(reason=reason)
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@advisor_bp.route("/api/bot/resume", methods=["POST"])
def api_bot_resume():
    """Resume the auto-advisor bot."""
    try:
        auto_advisor = _get_auto_advisor()
        result = auto_advisor.resume()
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@advisor_bp.route("/api/bot/toggle", methods=["POST"])
def api_bot_toggle():
    """Toggle the auto-advisor bot pause state."""
    try:
        auto_advisor = _get_auto_advisor()
        if auto_advisor.is_paused:
            result = auto_advisor.resume()
        else:
            result = auto_advisor.pause(reason="Paused by user")
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
