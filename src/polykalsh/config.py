"""
Configuration management using Pydantic Settings.

Loads configuration from environment variables and .env file.
Supports runtime overrides via the dashboard.
"""

from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class PolymarketConfig(BaseSettings):
    """Polymarket wallet and API configuration."""

    model_config = SettingsConfigDict(env_prefix="POLYMARKET_")

    private_key: str = Field(default="", description="Wallet private key (hex)")
    funder_address: str = Field(default="", description="Funder/proxy address")
    signature_type: Literal[0, 1] = Field(
        default=1, description="0=EOA/MetaMask, 1=Proxy/Email"
    )

    @field_validator("private_key")
    @classmethod
    def normalize_private_key(cls, v: str) -> str:
        """Ensure private key has 0x prefix."""
        if v and not v.startswith("0x"):
            return f"0x{v}"
        return v

    @property
    def is_configured(self) -> bool:
        """Check if Polymarket credentials are set."""
        return bool(self.private_key and self.funder_address)


class KalshiConfig(BaseSettings):
    """Kalshi API configuration (read-only)."""

    model_config = SettingsConfigDict(env_prefix="KALSHI_")

    api_key_id: str = Field(default="", description="Kalshi API key ID")
    private_key_path: str = Field(
        default="./kalshi_private_key.pem", description="Path to RSA private key"
    )
    env: Literal["prod", "demo"] = Field(default="prod")

    @property
    def host(self) -> str:
        """Get API host based on environment."""
        if self.env == "demo":
            return "https://demo-api.kalshi.co"
        return "https://api.elections.kalshi.com/trade-api/v2"

    @property
    def is_configured(self) -> bool:
        """Check if Kalshi credentials are set."""
        return bool(self.api_key_id) and Path(self.private_key_path).exists()


class DiscordConfig(BaseSettings):
    """Discord webhook configuration."""

    model_config = SettingsConfigDict(env_prefix="DISCORD_")

    webhook_url: str = Field(default="", description="Main webhook URL")
    webhook_errors: str = Field(default="", description="Error webhook URL (optional)")
    user_id: str = Field(default="", description="Your user ID for @mentions")
    mention_on_critical: bool = Field(default=True)

    @property
    def is_configured(self) -> bool:
        """Check if Discord is configured."""
        return bool(self.webhook_url)


class DashboardConfig(BaseSettings):
    """Dashboard configuration."""

    model_config = SettingsConfigDict(env_prefix="DASHBOARD_")

    port: int = Field(default=8502)
    password: str = Field(default="", description="Dashboard password (optional)")


class CopyTraderConfig(BaseSettings):
    """Copy-trader behavior configuration."""

    model_config = SettingsConfigDict(env_prefix="COPY_")

    enabled: bool = Field(default=True, description="Master enable/disable")
    paper_mode: bool = Field(default=True, description="Paper trading mode")
    paper_starting_balance: float = Field(default=500.0)
    max_trade_usd: float = Field(default=50.0, description="Max per trade")
    max_total_exposure_usd: float = Field(default=500.0, description="Max total")
    max_positions: int = Field(default=10)
    poll_interval_sec: int = Field(default=30)


class SafetyGuardsConfig(BaseSettings):
    """Safety guards configuration."""

    model_config = SettingsConfigDict(env_prefix="GUARD_")

    # Entry guards
    min_liquidity_usd: float = Field(default=1000.0)
    max_spread_pct: float = Field(default=0.10)
    min_hours_to_close: float = Field(default=24.0)
    max_price_drift_pct: float = Field(default=0.05)

    # Exit guards
    stop_loss_pct: float = Field(default=0.25)
    take_profit_pct: float = Field(default=0.50)
    exit_on_leader: bool = Field(default=True)
    exit_hours_before_close: float = Field(default=2.0)

    # Circuit breakers
    daily_loss_limit_usd: float = Field(default=100.0)
    max_consecutive_losses: int = Field(default=5)
    cooldown_after_loss_sec: int = Field(default=300)


class LeaderConfig(BaseSettings):
    """Leader discovery configuration."""

    model_config = SettingsConfigDict(env_prefix="LEADER_")

    auto_discover: bool = Field(default=True)
    max_tracked: int = Field(default=10)
    min_pnl_usd: float = Field(default=1000.0)
    min_volume_usd: float = Field(default=5000.0)
    time_periods: str = Field(default="WEEK,MONTH")

    @property
    def time_periods_list(self) -> list[str]:
        """Parse time periods as list."""
        return [p.strip().upper() for p in self.time_periods.split(",")]


class AdvisorConfig(BaseSettings):
    """Kalshi advisor configuration."""

    model_config = SettingsConfigDict(env_prefix="ADVISOR_")

    enabled: bool = Field(default=True)
    scan_interval_min: int = Field(default=30)
    min_score: float = Field(default=70.0)
    min_edge_pct: float = Field(default=0.05)
    categories: str = Field(default="", description="Comma-separated categories")

    @property
    def categories_list(self) -> list[str]:
        """Parse categories as list."""
        if not self.categories:
            return []
        return [c.strip() for c in self.categories.split(",")]


class Settings(BaseSettings):
    """Main application settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Sub-configurations
    polymarket: PolymarketConfig = Field(default_factory=PolymarketConfig)
    kalshi: KalshiConfig = Field(default_factory=KalshiConfig)
    discord: DiscordConfig = Field(default_factory=DiscordConfig)
    dashboard: DashboardConfig = Field(default_factory=DashboardConfig)
    copy_trader: CopyTraderConfig = Field(default_factory=CopyTraderConfig)
    safety_guards: SafetyGuardsConfig = Field(default_factory=SafetyGuardsConfig)
    leaders: LeaderConfig = Field(default_factory=LeaderConfig)
    advisor: AdvisorConfig = Field(default_factory=AdvisorConfig)

    # Database
    database_path: str = Field(
        default="./data/polykalsh.db", alias="DATABASE_PATH"
    )

    # Logging
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    log_file_path: str = Field(default="./data/polykalsh.log", alias="LOG_FILE_PATH")

    @property
    def database_url(self) -> str:
        """Get SQLAlchemy database URL."""
        return f"sqlite:///{self.database_path}"


# Global settings instance (lazy loaded)
_settings: Settings | None = None


def get_settings() -> Settings:
    """Get or create settings instance."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reload_settings() -> Settings:
    """Force reload settings from environment."""
    global _settings
    _settings = Settings()
    return _settings
