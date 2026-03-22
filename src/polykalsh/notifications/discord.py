"""
Discord webhook notification client.

Sends trading notifications, alerts, and status updates to Discord channels.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

import httpx
import structlog

logger = structlog.get_logger()


class NotificationLevel(str, Enum):
    """Notification importance level."""

    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


# Discord embed colors
COLORS = {
    NotificationLevel.INFO: 0x3498DB,      # Blue
    NotificationLevel.SUCCESS: 0x2ECC71,   # Green
    NotificationLevel.WARNING: 0xF39C12,   # Orange
    NotificationLevel.ERROR: 0xE74C3C,     # Red
    NotificationLevel.CRITICAL: 0x9B59B6,  # Purple
}


@dataclass
class DiscordEmbed:
    """Discord embed structure."""

    title: str
    description: str = ""
    color: int = 0x3498DB
    fields: list[dict[str, Any]] = field(default_factory=list)
    footer: str | None = None
    timestamp: datetime | None = None
    thumbnail_url: str | None = None

    def add_field(
        self,
        name: str,
        value: str,
        inline: bool = True,
    ) -> "DiscordEmbed":
        """Add a field to the embed."""
        self.fields.append({
            "name": name,
            "value": value,
            "inline": inline,
        })
        return self

    def to_dict(self) -> dict[str, Any]:
        """Convert to Discord API format."""
        embed: dict[str, Any] = {
            "title": self.title,
            "color": self.color,
        }

        if self.description:
            embed["description"] = self.description

        if self.fields:
            embed["fields"] = self.fields

        if self.footer:
            embed["footer"] = {"text": self.footer}

        if self.timestamp:
            embed["timestamp"] = self.timestamp.isoformat()

        if self.thumbnail_url:
            embed["thumbnail"] = {"url": self.thumbnail_url}

        return embed


class DiscordNotifier:
    """
    Discord webhook notification client.

    Sends rich notifications with embeds to Discord channels.
    Supports rate limiting and error channel fallback.
    """

    def __init__(
        self,
        webhook_url: str,
        error_webhook_url: str | None = None,
        user_id: str | None = None,
        mention_on_critical: bool = True,
        bot_name: str = "Polykalsh Bot",
        timeout: float = 10.0,
        rate_limit_per_minute: int = 30,
    ):
        """
        Initialize Discord notifier.

        Args:
            webhook_url: Main Discord webhook URL
            error_webhook_url: Separate webhook for errors (optional)
            user_id: User ID for @mentions
            mention_on_critical: Mention user on critical alerts
            bot_name: Bot display name
            timeout: Request timeout
            rate_limit_per_minute: Max messages per minute
        """
        self.webhook_url = webhook_url
        self.error_webhook_url = error_webhook_url or webhook_url
        self.user_id = user_id
        self.mention_on_critical = mention_on_critical
        self.bot_name = bot_name
        self.timeout = timeout
        self.rate_limit_per_minute = rate_limit_per_minute

        self._client: httpx.AsyncClient | None = None
        self._request_times: list[float] = []

        # Stats
        self.messages_sent = 0
        self.errors = 0

    async def _ensure_client(self) -> httpx.AsyncClient:
        """Ensure HTTP client exists."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout),
            )
        return self._client

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _wait_for_rate_limit(self) -> None:
        """Wait if we're exceeding rate limit."""
        now = asyncio.get_event_loop().time()

        # Remove old timestamps (older than 60 seconds)
        self._request_times = [t for t in self._request_times if now - t < 60.0]

        # If at limit, wait
        if len(self._request_times) >= self.rate_limit_per_minute:
            wait_time = 60.0 - (now - self._request_times[0])
            if wait_time > 0:
                logger.warning("discord_rate_limit", wait_time=wait_time)
                await asyncio.sleep(wait_time)

        self._request_times.append(now)

    async def send(
        self,
        content: str | None = None,
        embed: DiscordEmbed | None = None,
        level: NotificationLevel = NotificationLevel.INFO,
        use_error_channel: bool = False,
    ) -> bool:
        """
        Send a notification to Discord.

        Args:
            content: Plain text content
            embed: Rich embed
            level: Notification level
            use_error_channel: Use error webhook

        Returns:
            True if sent successfully
        """
        await self._wait_for_rate_limit()
        client = await self._ensure_client()

        # Build payload
        payload: dict[str, Any] = {
            "username": self.bot_name,
        }

        # Add mention for critical
        if level == NotificationLevel.CRITICAL and self.mention_on_critical and self.user_id:
            mention = f"<@{self.user_id}>"
            if content:
                content = f"{mention} {content}"
            else:
                content = mention

        if content:
            payload["content"] = content

        if embed:
            # Set color based on level if not already set
            if embed.color == 0x3498DB:  # Default blue
                embed.color = COLORS.get(level, 0x3498DB)
            payload["embeds"] = [embed.to_dict()]

        # Choose webhook
        webhook_url = self.error_webhook_url if use_error_channel else self.webhook_url

        try:
            response = await client.post(webhook_url, json=payload)

            if response.status_code == 204:
                self.messages_sent += 1
                return True

            if response.status_code == 429:
                # Rate limited by Discord
                retry_after = response.json().get("retry_after", 5)
                logger.warning("discord_429", retry_after=retry_after)
                await asyncio.sleep(retry_after)
                return await self.send(content, embed, level, use_error_channel)

            logger.error(
                "discord_send_error",
                status=response.status_code,
                response=response.text,
            )
            self.errors += 1
            return False

        except Exception as e:
            logger.error("discord_exception", error=str(e))
            self.errors += 1
            return False

    # ═══════════════════════════════════════════════════════════════════════════
    # CONVENIENCE METHODS
    # ═══════════════════════════════════════════════════════════════════════════

    async def send_trade_entry(
        self,
        market_ticker: str,
        market_title: str,
        side: str,
        contracts: int,
        price: float,
        cost_usd: float,
        edge: float,
        confidence: float,
        strategy: str,
    ) -> bool:
        """Send trade entry notification."""
        embed = DiscordEmbed(
            title=f"Trade Entry: {side} {market_ticker}",
            description=market_title,
            color=COLORS[NotificationLevel.SUCCESS],
            timestamp=datetime.utcnow(),
        )

        embed.add_field("Side", side)
        embed.add_field("Contracts", str(contracts))
        embed.add_field("Price", f"${price:.2f}")
        embed.add_field("Cost", f"${cost_usd:.2f}")
        embed.add_field("Edge", f"{edge:.1%}")
        embed.add_field("Confidence", f"{confidence:.1%}")
        embed.add_field("Strategy", strategy, inline=False)

        return await self.send(embed=embed, level=NotificationLevel.SUCCESS)

    async def send_trade_exit(
        self,
        market_ticker: str,
        market_title: str,
        side: str,
        contracts: int,
        entry_price: float,
        exit_price: float,
        pnl_usd: float,
        pnl_pct: float,
        reason: str,
        hold_hours: float,
    ) -> bool:
        """Send trade exit notification."""
        is_profit = pnl_usd >= 0
        level = NotificationLevel.SUCCESS if is_profit else NotificationLevel.WARNING

        embed = DiscordEmbed(
            title=f"Trade Exit: {side} {market_ticker}",
            description=market_title,
            color=COLORS[level],
            timestamp=datetime.utcnow(),
        )

        embed.add_field("Side", side)
        embed.add_field("Contracts", str(contracts))
        embed.add_field("Entry", f"${entry_price:.2f}")
        embed.add_field("Exit", f"${exit_price:.2f}")
        embed.add_field("P&L", f"${pnl_usd:+.2f} ({pnl_pct:+.1%})")
        embed.add_field("Hold Time", f"{hold_hours:.1f}h")
        embed.add_field("Exit Reason", reason, inline=False)

        return await self.send(embed=embed, level=level)

    async def send_daily_summary(
        self,
        date: str,
        starting_balance: float,
        ending_balance: float,
        daily_pnl: float,
        trades_opened: int,
        trades_closed: int,
        win_rate: float,
        ai_cost: float,
        open_positions: int,
    ) -> bool:
        """Send daily summary notification."""
        is_profit = daily_pnl >= 0
        level = NotificationLevel.SUCCESS if is_profit else NotificationLevel.WARNING

        embed = DiscordEmbed(
            title=f"Daily Summary: {date}",
            description=f"P&L: ${daily_pnl:+.2f} ({daily_pnl / starting_balance * 100:+.1f}%)",
            color=COLORS[level],
            timestamp=datetime.utcnow(),
        )

        embed.add_field("Start Balance", f"${starting_balance:.2f}")
        embed.add_field("End Balance", f"${ending_balance:.2f}")
        embed.add_field("Trades Opened", str(trades_opened))
        embed.add_field("Trades Closed", str(trades_closed))
        embed.add_field("Win Rate", f"{win_rate:.1%}")
        embed.add_field("AI Cost", f"${ai_cost:.2f}")
        embed.add_field("Open Positions", str(open_positions))

        return await self.send(embed=embed, level=level)

    async def send_error(
        self,
        error_type: str,
        message: str,
        details: str | None = None,
    ) -> bool:
        """Send error notification."""
        embed = DiscordEmbed(
            title=f"Error: {error_type}",
            description=message,
            color=COLORS[NotificationLevel.ERROR],
            timestamp=datetime.utcnow(),
        )

        if details:
            embed.add_field("Details", f"```{details[:1000]}```", inline=False)

        return await self.send(
            embed=embed,
            level=NotificationLevel.ERROR,
            use_error_channel=True,
        )

    async def send_critical(
        self,
        title: str,
        message: str,
        action_required: str | None = None,
    ) -> bool:
        """Send critical alert with mention."""
        embed = DiscordEmbed(
            title=f"CRITICAL: {title}",
            description=message,
            color=COLORS[NotificationLevel.CRITICAL],
            timestamp=datetime.utcnow(),
        )

        if action_required:
            embed.add_field("Action Required", action_required, inline=False)

        return await self.send(
            embed=embed,
            level=NotificationLevel.CRITICAL,
            use_error_channel=True,
        )

    async def send_status(
        self,
        status: str,
        portfolio_value: float,
        open_positions: int,
        daily_pnl: float,
        last_cycle: str | None = None,
    ) -> bool:
        """Send status update."""
        embed = DiscordEmbed(
            title=f"Bot Status: {status}",
            color=COLORS[NotificationLevel.INFO],
            timestamp=datetime.utcnow(),
        )

        embed.add_field("Portfolio Value", f"${portfolio_value:.2f}")
        embed.add_field("Open Positions", str(open_positions))
        embed.add_field("Daily P&L", f"${daily_pnl:+.2f}")

        if last_cycle:
            embed.add_field("Last Cycle", last_cycle, inline=False)

        return await self.send(embed=embed, level=NotificationLevel.INFO)

    async def send_circuit_breaker(
        self,
        breaker_type: str,
        current_value: float,
        threshold: float,
        action_taken: str,
    ) -> bool:
        """Send circuit breaker alert."""
        embed = DiscordEmbed(
            title=f"Circuit Breaker: {breaker_type}",
            description=f"Trading paused due to {breaker_type}",
            color=COLORS[NotificationLevel.WARNING],
            timestamp=datetime.utcnow(),
        )

        embed.add_field("Current Value", f"{current_value:.2%}")
        embed.add_field("Threshold", f"{threshold:.2%}")
        embed.add_field("Action", action_taken, inline=False)

        return await self.send(
            embed=embed,
            level=NotificationLevel.WARNING,
        )

    def get_stats(self) -> dict[str, Any]:
        """Get notification statistics."""
        return {
            "messages_sent": self.messages_sent,
            "errors": self.errors,
            "error_rate": self.errors / max(1, self.messages_sent + self.errors),
        }


# Synchronous wrapper for non-async contexts
class DiscordNotifierSync:
    """Synchronous wrapper for DiscordNotifier."""

    def __init__(self, notifier: DiscordNotifier):
        self._notifier = notifier
        self._loop: asyncio.AbstractEventLoop | None = None

    def _get_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None or self._loop.is_closed():
            try:
                self._loop = asyncio.get_running_loop()
            except RuntimeError:
                self._loop = asyncio.new_event_loop()
        return self._loop

    def send(self, content: str | None = None, **kwargs: Any) -> bool:
        loop = self._get_loop()
        return loop.run_until_complete(self._notifier.send(content, **kwargs))

    def send_error(self, error_type: str, message: str, **kwargs: Any) -> bool:
        loop = self._get_loop()
        return loop.run_until_complete(
            self._notifier.send_error(error_type, message, **kwargs)
        )
