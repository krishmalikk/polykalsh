"""
Tests for Phase 9: Shared Infrastructure.

Tests Discord notifications and hybrid worker scheduling.
"""

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from polykalsh.notifications.discord import (
    DiscordEmbed,
    DiscordNotifier,
    NotificationLevel,
    COLORS,
)
from polykalsh.workers.hybrid_worker import HybridWorker


# ═══════════════════════════════════════════════════════════════════════════════
# DISCORD EMBED TESTS
# ═══════════════════════════════════════════════════════════════════════════════


def test_discord_embed_creation():
    """Test creating a Discord embed."""
    embed = DiscordEmbed(
        title="Test Title",
        description="Test Description",
        color=0xFF0000,
    )

    assert embed.title == "Test Title"
    assert embed.description == "Test Description"
    assert embed.color == 0xFF0000


def test_discord_embed_add_fields():
    """Test adding fields to an embed."""
    embed = DiscordEmbed(title="Test")
    embed.add_field("Field 1", "Value 1")
    embed.add_field("Field 2", "Value 2", inline=False)

    assert len(embed.fields) == 2
    assert embed.fields[0]["name"] == "Field 1"
    assert embed.fields[0]["inline"] is True
    assert embed.fields[1]["inline"] is False


def test_discord_embed_to_dict():
    """Test converting embed to Discord API format."""
    embed = DiscordEmbed(
        title="Test",
        description="Desc",
        color=0x00FF00,
        footer="Footer text",
        timestamp=datetime(2024, 1, 15, 12, 0, 0),
    )
    embed.add_field("F1", "V1")

    result = embed.to_dict()

    assert result["title"] == "Test"
    assert result["description"] == "Desc"
    assert result["color"] == 0x00FF00
    assert result["footer"]["text"] == "Footer text"
    assert "2024-01-15" in result["timestamp"]
    assert len(result["fields"]) == 1


def test_discord_embed_chaining():
    """Test method chaining on embed."""
    embed = (
        DiscordEmbed(title="Test")
        .add_field("A", "1")
        .add_field("B", "2")
        .add_field("C", "3")
    )

    assert len(embed.fields) == 3


# ═══════════════════════════════════════════════════════════════════════════════
# DISCORD NOTIFIER TESTS
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_discord_notifier_send():
    """Test sending a basic notification."""
    notifier = DiscordNotifier(
        webhook_url="https://discord.com/api/webhooks/test",
    )

    # Mock the HTTP client
    mock_response = MagicMock()
    mock_response.status_code = 204

    with patch.object(notifier, "_ensure_client") as mock_client:
        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_response)
        mock_client.return_value = mock_http

        result = await notifier.send(content="Test message")

        assert result is True
        assert notifier.messages_sent == 1
        mock_http.post.assert_called_once()


@pytest.mark.asyncio
async def test_discord_notifier_send_embed():
    """Test sending a notification with embed."""
    notifier = DiscordNotifier(
        webhook_url="https://discord.com/api/webhooks/test",
    )

    mock_response = MagicMock()
    mock_response.status_code = 204

    with patch.object(notifier, "_ensure_client") as mock_client:
        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_response)
        mock_client.return_value = mock_http

        embed = DiscordEmbed(title="Test Embed", description="Test")
        result = await notifier.send(embed=embed)

        assert result is True

        # Check the payload included embed
        call_kwargs = mock_http.post.call_args[1]
        assert "embeds" in call_kwargs["json"]


@pytest.mark.asyncio
async def test_discord_notifier_critical_mention():
    """Test that critical notifications mention the user."""
    notifier = DiscordNotifier(
        webhook_url="https://discord.com/api/webhooks/test",
        user_id="123456789",
        mention_on_critical=True,
    )

    mock_response = MagicMock()
    mock_response.status_code = 204

    with patch.object(notifier, "_ensure_client") as mock_client:
        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_response)
        mock_client.return_value = mock_http

        await notifier.send(
            content="Critical alert!",
            level=NotificationLevel.CRITICAL,
        )

        call_kwargs = mock_http.post.call_args[1]
        assert "<@123456789>" in call_kwargs["json"]["content"]


@pytest.mark.asyncio
async def test_discord_notifier_trade_entry():
    """Test sending trade entry notification."""
    notifier = DiscordNotifier(
        webhook_url="https://discord.com/api/webhooks/test",
    )

    mock_response = MagicMock()
    mock_response.status_code = 204

    with patch.object(notifier, "_ensure_client") as mock_client:
        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_response)
        mock_client.return_value = mock_http

        result = await notifier.send_trade_entry(
            market_ticker="TEST-MKT",
            market_title="Test Market",
            side="YES",
            contracts=100,
            price=0.55,
            cost_usd=55.0,
            edge=0.08,
            confidence=0.75,
            strategy="directional",
        )

        assert result is True

        # Check embed was created with trade info
        call_kwargs = mock_http.post.call_args[1]
        embed = call_kwargs["json"]["embeds"][0]
        assert "Trade Entry" in embed["title"]


@pytest.mark.asyncio
async def test_discord_notifier_error_channel():
    """Test using separate error channel."""
    notifier = DiscordNotifier(
        webhook_url="https://discord.com/api/webhooks/main",
        error_webhook_url="https://discord.com/api/webhooks/errors",
    )

    mock_response = MagicMock()
    mock_response.status_code = 204

    with patch.object(notifier, "_ensure_client") as mock_client:
        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_response)
        mock_client.return_value = mock_http

        await notifier.send_error(
            error_type="Test Error",
            message="Something went wrong",
        )

        call_args = mock_http.post.call_args[0]
        assert "errors" in call_args[0]  # URL should be error webhook


def test_notification_colors():
    """Test notification level colors."""
    assert COLORS[NotificationLevel.INFO] == 0x3498DB  # Blue
    assert COLORS[NotificationLevel.SUCCESS] == 0x2ECC71  # Green
    assert COLORS[NotificationLevel.WARNING] == 0xF39C12  # Orange
    assert COLORS[NotificationLevel.ERROR] == 0xE74C3C  # Red
    assert COLORS[NotificationLevel.CRITICAL] == 0x9B59B6  # Purple


# ═══════════════════════════════════════════════════════════════════════════════
# HYBRID WORKER TESTS
# ═══════════════════════════════════════════════════════════════════════════════


def test_worker_initialization():
    """Test worker initialization."""
    mock_settings = MagicMock()
    mock_settings.hybrid_trading.paper_mode = True
    mock_settings.hybrid_trading.scan_interval_min = 15
    mock_settings.hybrid_trading.exit_check_interval_min = 5
    mock_settings.discord.is_configured = False

    worker = HybridWorker(settings=mock_settings)

    assert worker.settings == mock_settings
    assert worker.cycles_completed == 0
    assert worker.cycles_failed == 0
    assert worker.daily_pnl == 0.0


def test_worker_status():
    """Test worker status reporting."""
    mock_settings = MagicMock()
    mock_settings.hybrid_trading.paper_mode = True

    worker = HybridWorker(settings=mock_settings)
    worker.cycles_completed = 10
    worker.cycles_failed = 1
    worker.daily_pnl = 25.50
    worker.daily_trades = 5
    worker.last_cycle_time = datetime(2024, 1, 15, 12, 0, 0)

    status = worker.get_status()

    assert status["cycles_completed"] == 10
    assert status["cycles_failed"] == 1
    assert status["daily_stats"]["pnl"] == 25.50
    assert status["daily_stats"]["trades"] == 5


@pytest.mark.asyncio
async def test_worker_daily_reset():
    """Test daily counter reset."""
    mock_settings = MagicMock()
    mock_settings.hybrid_trading.paper_mode = True

    worker = HybridWorker(settings=mock_settings)
    worker.daily_pnl = 100.0
    worker.daily_trades = 10
    worker.daily_ai_cost = 5.0

    # Mock orchestrator
    worker._orchestrator = MagicMock()
    worker._orchestrator._portfolio_optimizer = None

    await worker._daily_reset()

    assert worker.daily_pnl == 0.0
    assert worker.daily_trades == 0
    assert worker.daily_ai_cost == 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# INTEGRATION TESTS
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_full_notification_flow():
    """Test a full notification flow with all message types."""
    notifier = DiscordNotifier(
        webhook_url="https://discord.com/api/webhooks/test",
        user_id="123456789",
    )

    mock_response = MagicMock()
    mock_response.status_code = 204

    with patch.object(notifier, "_ensure_client") as mock_client:
        mock_http = AsyncMock()
        mock_http.post = AsyncMock(return_value=mock_response)
        mock_client.return_value = mock_http

        # Send various notification types
        await notifier.send_status(
            status="Running",
            portfolio_value=1000.0,
            open_positions=3,
            daily_pnl=50.0,
        )

        await notifier.send_trade_entry(
            market_ticker="MKT-1",
            market_title="Test Market",
            side="YES",
            contracts=50,
            price=0.60,
            cost_usd=30.0,
            edge=0.10,
            confidence=0.80,
            strategy="directional",
        )

        await notifier.send_trade_exit(
            market_ticker="MKT-1",
            market_title="Test Market",
            side="YES",
            contracts=50,
            entry_price=0.60,
            exit_price=0.70,
            pnl_usd=5.0,
            pnl_pct=0.167,
            reason="take_profit",
            hold_hours=24.0,
        )

        await notifier.send_daily_summary(
            date="2024-01-15",
            starting_balance=1000.0,
            ending_balance=1050.0,
            daily_pnl=50.0,
            trades_opened=5,
            trades_closed=3,
            win_rate=0.67,
            ai_cost=2.50,
            open_positions=2,
        )

        await notifier.send_circuit_breaker(
            breaker_type="Daily Loss Limit",
            current_value=0.16,
            threshold=0.15,
            action_taken="Trading paused for remainder of day",
        )

        assert notifier.messages_sent == 5
        assert mock_http.post.call_count == 5


if __name__ == "__main__":
    # Run basic tests
    print("Testing Discord Embed Creation...")
    test_discord_embed_creation()
    print("PASS")

    print("\nTesting Discord Embed Fields...")
    test_discord_embed_add_fields()
    print("PASS")

    print("\nTesting Discord Embed to Dict...")
    test_discord_embed_to_dict()
    print("PASS")

    print("\nTesting Discord Embed Chaining...")
    test_discord_embed_chaining()
    print("PASS")

    print("\nTesting Notification Colors...")
    test_notification_colors()
    print("PASS")

    print("\nTesting Worker Initialization...")
    test_worker_initialization()
    print("PASS")

    print("\nTesting Worker Status...")
    test_worker_status()
    print("PASS")

    print("\nTesting Discord Notifier Send...")
    asyncio.run(test_discord_notifier_send())
    print("PASS")

    print("\nTesting Discord Notifier Send Embed...")
    asyncio.run(test_discord_notifier_send_embed())
    print("PASS")

    print("\nTesting Discord Notifier Critical Mention...")
    asyncio.run(test_discord_notifier_critical_mention())
    print("PASS")

    print("\nTesting Discord Notifier Trade Entry...")
    asyncio.run(test_discord_notifier_trade_entry())
    print("PASS")

    print("\nTesting Discord Notifier Error Channel...")
    asyncio.run(test_discord_notifier_error_channel())
    print("PASS")

    print("\nTesting Worker Daily Reset...")
    asyncio.run(test_worker_daily_reset())
    print("PASS")

    print("\nTesting Full Notification Flow...")
    asyncio.run(test_full_notification_flow())
    print("PASS")

    print("\n" + "=" * 60)
    print("ALL PHASE 9 TESTS PASSED!")
    print("=" * 60)
