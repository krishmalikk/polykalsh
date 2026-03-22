"""
Tests for Phase 10: Dashboard.

Tests Flask app, routes, and HTMX endpoints.
"""

import pytest
from unittest.mock import MagicMock, patch

from polykalsh.dashboard.app import create_app


# ═══════════════════════════════════════════════════════════════════════════════
# FIXTURES
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.fixture
def mock_settings():
    """Create mock settings."""
    settings = MagicMock()
    settings.database_url = "sqlite:///:memory:"
    settings.dashboard.password = "test-password"
    settings.hybrid_trading.enabled = True
    settings.hybrid_trading.paper_mode = True
    settings.hybrid_trading.max_position_pct = 0.10
    settings.hybrid_trading.max_bet_amount_usd = 100.0
    settings.hybrid_trading.max_concurrent_positions = 20
    settings.copy_trader.enabled = True
    settings.portfolio.kelly_fraction = 0.75
    settings.portfolio.directional_allocation = 0.50
    settings.portfolio.market_making_allocation = 0.40
    settings.portfolio.arbitrage_allocation = 0.10
    settings.portfolio.max_daily_loss_pct = 0.15
    settings.portfolio.max_drawdown_pct = 0.50
    settings.exit_rules.trailing_take_profit_pct = 0.20
    settings.exit_rules.stop_loss_pct = 0.15
    settings.exit_rules.max_hold_days = 10
    settings.exit_rules.exit_hours_before_expiry = 4.0
    settings.ensemble.min_consensus_confidence = 0.60
    settings.ensemble.max_disagreement_spread = 0.30
    settings.ensemble.min_edge_to_trade = 0.05
    return settings


@pytest.fixture
def app(mock_settings):
    """Create test Flask app."""
    with patch("polykalsh.dashboard.app.create_engine"), \
         patch("polykalsh.dashboard.app.sessionmaker"), \
         patch("polykalsh.dashboard.app.scoped_session"):
        app = create_app(mock_settings)
        app.config["TESTING"] = True
        return app


@pytest.fixture
def client(app):
    """Create test client."""
    return app.test_client()


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN ROUTES TESTS
# ═══════════════════════════════════════════════════════════════════════════════


def test_index_page(client):
    """Test main index page loads."""
    response = client.get("/")
    assert response.status_code == 200
    assert b"Dashboard" in response.data


def test_health_endpoint(client):
    """Test health check endpoint."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.get_json()
    assert data["status"] == "ok"
    assert "timestamp" in data


# ═══════════════════════════════════════════════════════════════════════════════
# HYBRID ROUTES TESTS
# ═══════════════════════════════════════════════════════════════════════════════


def test_hybrid_index(client):
    """Test hybrid bot main page."""
    response = client.get("/hybrid/")
    assert response.status_code == 200
    assert b"Hybrid Trading Bot" in response.data


def test_hybrid_positions(client):
    """Test positions page."""
    response = client.get("/hybrid/positions")
    assert response.status_code == 200
    assert b"Open Positions" in response.data


def test_hybrid_history(client):
    """Test history page."""
    response = client.get("/hybrid/history")
    assert response.status_code == 200
    assert b"Trade History" in response.data


def test_hybrid_settings(client):
    """Test settings page."""
    response = client.get("/hybrid/settings")
    assert response.status_code == 200
    assert b"Settings" in response.data


# ═══════════════════════════════════════════════════════════════════════════════
# HTMX PARTIAL TESTS
# ═══════════════════════════════════════════════════════════════════════════════


def test_partial_status(client):
    """Test status partial."""
    response = client.get("/hybrid/partials/status")
    assert response.status_code == 200
    assert b"Status" in response.data or b"Running" in response.data


def test_partial_portfolio(client):
    """Test portfolio partial."""
    response = client.get("/hybrid/partials/portfolio")
    assert response.status_code == 200
    assert b"Portfolio" in response.data or b"Total Value" in response.data


def test_partial_positions(client):
    """Test positions table partial."""
    response = client.get("/hybrid/partials/positions")
    assert response.status_code == 200
    # Should have positions or "No open positions"
    assert response.data is not None


def test_partial_recent_trades(client):
    """Test recent trades partial."""
    response = client.get("/hybrid/partials/recent-trades")
    assert response.status_code == 200


def test_partial_strategy_allocation(client):
    """Test strategy allocation partial."""
    response = client.get("/hybrid/partials/strategy-allocation")
    assert response.status_code == 200
    assert b"Directional" in response.data
    assert b"Market Making" in response.data


# ═══════════════════════════════════════════════════════════════════════════════
# API ENDPOINT TESTS
# ═══════════════════════════════════════════════════════════════════════════════


def test_api_status(client):
    """Test status API endpoint."""
    response = client.get("/hybrid/api/status")
    assert response.status_code == 200
    data = response.get_json()
    assert "is_running" in data


def test_api_portfolio(client):
    """Test portfolio API endpoint."""
    response = client.get("/hybrid/api/portfolio")
    assert response.status_code == 200
    data = response.get_json()
    assert "total_value" in data


def test_api_positions(client):
    """Test positions API endpoint."""
    response = client.get("/hybrid/api/positions")
    assert response.status_code == 200
    data = response.get_json()
    assert isinstance(data, list)


def test_api_trades(client):
    """Test trades API endpoint."""
    response = client.get("/hybrid/api/trades?limit=10")
    assert response.status_code == 200
    data = response.get_json()
    assert isinstance(data, list)


def test_api_toggle_trading(client):
    """Test toggle trading API endpoint."""
    response = client.post(
        "/hybrid/api/toggle-trading",
        json={"enabled": False},
        content_type="application/json",
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True


def test_api_close_position(client):
    """Test close position API endpoint."""
    response = client.post(
        "/hybrid/api/close-position",
        json={"market_ticker": "TEST-MKT"},
        content_type="application/json",
    )
    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True


def test_api_close_position_missing_ticker(client):
    """Test close position without ticker fails."""
    response = client.post(
        "/hybrid/api/close-position",
        json={},
        content_type="application/json",
    )
    assert response.status_code == 400


# ═══════════════════════════════════════════════════════════════════════════════
# ERROR HANDLING TESTS
# ═══════════════════════════════════════════════════════════════════════════════


def test_404_error(client):
    """Test 404 error page."""
    response = client.get("/nonexistent-page")
    assert response.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════════
# APP FACTORY TESTS
# ═══════════════════════════════════════════════════════════════════════════════


def test_app_creation(mock_settings):
    """Test app factory creates valid app."""
    with patch("polykalsh.dashboard.app.create_engine"), \
         patch("polykalsh.dashboard.app.sessionmaker"), \
         patch("polykalsh.dashboard.app.scoped_session"):
        app = create_app(mock_settings)

        assert app is not None
        assert app.config["SECRET_KEY"] == "test-password"
        assert app.config["SETTINGS"] == mock_settings


def test_context_processor(client):
    """Test context processor injects settings."""
    response = client.get("/")
    assert response.status_code == 200
    # Paper mode badge should be visible
    assert b"PAPER MODE" in response.data or b"paper" in response.data.lower()


if __name__ == "__main__":
    from unittest.mock import MagicMock, patch

    # Create mock settings
    settings = MagicMock()
    settings.database_url = "sqlite:///:memory:"
    settings.dashboard.password = "test"
    settings.hybrid_trading.enabled = True
    settings.hybrid_trading.paper_mode = True
    settings.hybrid_trading.max_position_pct = 0.10
    settings.hybrid_trading.max_bet_amount_usd = 100.0
    settings.hybrid_trading.max_concurrent_positions = 20
    settings.copy_trader.enabled = True
    settings.portfolio.kelly_fraction = 0.75
    settings.portfolio.directional_allocation = 0.50
    settings.portfolio.market_making_allocation = 0.40
    settings.portfolio.arbitrage_allocation = 0.10
    settings.portfolio.max_daily_loss_pct = 0.15
    settings.portfolio.max_drawdown_pct = 0.50
    settings.exit_rules.trailing_take_profit_pct = 0.20
    settings.exit_rules.stop_loss_pct = 0.15
    settings.exit_rules.max_hold_days = 10
    settings.exit_rules.exit_hours_before_expiry = 4.0
    settings.ensemble.min_consensus_confidence = 0.60
    settings.ensemble.max_disagreement_spread = 0.30
    settings.ensemble.min_edge_to_trade = 0.05

    # Create app with mocks
    with patch("polykalsh.dashboard.app.create_engine"), \
         patch("polykalsh.dashboard.app.sessionmaker"), \
         patch("polykalsh.dashboard.app.scoped_session"):
        app = create_app(settings)
        app.config["TESTING"] = True
        client = app.test_client()

    print("Testing Index Page...")
    response = client.get("/")
    assert response.status_code == 200
    print("PASS")

    print("\nTesting Health Endpoint...")
    response = client.get("/health")
    assert response.status_code == 200
    print("PASS")

    print("\nTesting Hybrid Index...")
    response = client.get("/hybrid/")
    assert response.status_code == 200
    print("PASS")

    print("\nTesting Hybrid Positions...")
    response = client.get("/hybrid/positions")
    assert response.status_code == 200
    print("PASS")

    print("\nTesting Hybrid History...")
    response = client.get("/hybrid/history")
    assert response.status_code == 200
    print("PASS")

    print("\nTesting Hybrid Settings...")
    response = client.get("/hybrid/settings")
    assert response.status_code == 200
    print("PASS")

    print("\nTesting Status Partial...")
    response = client.get("/hybrid/partials/status")
    assert response.status_code == 200
    print("PASS")

    print("\nTesting Portfolio Partial...")
    response = client.get("/hybrid/partials/portfolio")
    assert response.status_code == 200
    print("PASS")

    print("\nTesting Strategy Allocation Partial...")
    response = client.get("/hybrid/partials/strategy-allocation")
    assert response.status_code == 200
    print("PASS")

    print("\nTesting API Status...")
    response = client.get("/hybrid/api/status")
    assert response.status_code == 200
    print("PASS")

    print("\nTesting API Portfolio...")
    response = client.get("/hybrid/api/portfolio")
    assert response.status_code == 200
    print("PASS")

    print("\nTesting API Toggle Trading...")
    response = client.post(
        "/hybrid/api/toggle-trading",
        json={"enabled": False},
        content_type="application/json",
    )
    assert response.status_code == 200
    print("PASS")

    print("\nTesting 404 Error...")
    response = client.get("/nonexistent")
    assert response.status_code == 404
    print("PASS")

    print("\n" + "=" * 60)
    print("ALL PHASE 10 TESTS PASSED!")
    print("=" * 60)
