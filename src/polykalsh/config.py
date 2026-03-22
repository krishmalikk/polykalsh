"""
Configuration management using Pydantic Settings.

Loads configuration from environment variables and .env file.
Supports runtime overrides via the dashboard.
"""

from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _find_env_file_early() -> str:
    """Find the .env file path (called at module load time)."""
    locations = [
        Path.cwd() / ".env",
        Path(__file__).parent.parent.parent / ".env",
        Path.home() / "Documents/projects/polykalsh/.env",
    ]
    for loc in locations:
        if loc.exists():
            return str(loc)
    return ".env"


# Shared env file path for all config classes
_ENV_FILE = _find_env_file_early()


class PolymarketConfig(BaseSettings):
    """Polymarket wallet and API configuration."""

    model_config = SettingsConfigDict(env_prefix="POLYMARKET_", env_file=_ENV_FILE, extra="ignore")

    private_key: str = Field(default="", description="Wallet private key (hex)")
    funder_address: str = Field(default="", description="Funder/proxy address")
    signature_type: Literal[0, 1] = Field(
        default=1, description="0=EOA/MetaMask, 1=Proxy/Email"
    )

    @field_validator("signature_type", mode="before")
    @classmethod
    def coerce_signature_type(cls, v):
        """Coerce string to int for signature type."""
        if isinstance(v, str):
            return int(v)
        return v

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

    model_config = SettingsConfigDict(env_prefix="KALSHI_", env_file=_ENV_FILE, extra="ignore")

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

    model_config = SettingsConfigDict(env_prefix="DISCORD_", env_file=_ENV_FILE, extra="ignore")

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

    model_config = SettingsConfigDict(env_prefix="DASHBOARD_", env_file=_ENV_FILE, extra="ignore")

    port: int = Field(default=8502)
    password: str = Field(default="", description="Dashboard password (optional)")


class CopyTraderConfig(BaseSettings):
    """Copy-trader behavior configuration."""

    model_config = SettingsConfigDict(env_prefix="COPY_", env_file=_ENV_FILE, extra="ignore")

    enabled: bool = Field(default=True, description="Master enable/disable")
    paper_mode: bool = Field(default=True, description="Paper trading mode")
    paper_starting_balance: float = Field(default=500.0)
    max_trade_usd: float = Field(default=50.0, description="Max per trade")
    max_total_exposure_usd: float = Field(default=500.0, description="Max total")
    max_positions: int = Field(default=10)
    poll_interval_sec: int = Field(default=30)


class SafetyGuardsConfig(BaseSettings):
    """Safety guards configuration."""

    model_config = SettingsConfigDict(env_prefix="GUARD_", env_file=_ENV_FILE, extra="ignore")

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

    model_config = SettingsConfigDict(env_prefix="LEADER_", env_file=_ENV_FILE, extra="ignore")

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

    model_config = SettingsConfigDict(env_prefix="ADVISOR_", env_file=_ENV_FILE, extra="ignore")

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


# ═══════════════════════════════════════════════════════════════════════════════
# KALSHI HYBRID TRADING BOT CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════


class AIProvidersConfig(BaseSettings):
    """AI provider API keys and settings."""

    model_config = SettingsConfigDict(env_prefix="AI_", env_file=_ENV_FILE, extra="ignore")

    # Anthropic (Lead Forecaster, Risk Manager)
    anthropic_api_key: str = Field(default="", description="Anthropic API key")
    anthropic_model: str = Field(default="claude-sonnet-4-20250514")

    # OpenRouter (News Analyst, Bull/Bear Researchers)
    openrouter_api_key: str = Field(default="", description="OpenRouter API key")
    openrouter_model: str = Field(default="anthropic/claude-sonnet-4-20250514")

    # Perplexity (Deep Research)
    perplexity_api_key: str = Field(default="", description="Perplexity API key")
    perplexity_model: str = Field(default="llama-3.1-sonar-large-128k-online")

    # Rate limiting
    max_concurrent_requests: int = Field(default=5)
    request_timeout_sec: int = Field(default=60)

    @property
    def is_configured(self) -> bool:
        """Check if at least one AI provider is configured."""
        return bool(self.anthropic_api_key or self.openrouter_api_key)


class HybridTradingConfig(BaseSettings):
    """Kalshi hybrid trading bot configuration."""

    model_config = SettingsConfigDict(env_prefix="HYBRID_", env_file=_ENV_FILE, extra="ignore")

    # Master controls
    enabled: bool = Field(default=True)
    paper_mode: bool = Field(default=True, description="Paper trading mode")
    paper_starting_balance: float = Field(default=1000.0)

    # Discovery settings
    top_events: int = Field(default=50, description="Top N events by 24h volume")
    markets_per_event: int = Field(default=10, description="Top M markets per event")
    max_expiry_days: int = Field(default=30, description="Max days until expiry")
    min_volume_24h: int = Field(default=1000, description="Minimum 24h volume")
    skip_existing_positions: bool = Field(default=True)

    # Position limits
    max_position_pct: float = Field(default=0.10, description="Max 10% of portfolio per position")
    max_concurrent_positions: int = Field(default=20)
    max_bet_amount_usd: float = Field(default=100.0)

    # Trading loop
    scan_interval_min: int = Field(default=15)
    exit_check_interval_min: int = Field(default=5)


class EnsembleConfig(BaseSettings):
    """AI ensemble configuration."""

    model_config = SettingsConfigDict(env_prefix="ENSEMBLE_", env_file=_ENV_FILE, extra="ignore")

    # Agent weights (must sum to 1.0)
    lead_forecaster_weight: float = Field(default=0.30)
    news_analyst_weight: float = Field(default=0.20)
    bull_researcher_weight: float = Field(default=0.20)
    bear_researcher_weight: float = Field(default=0.15)
    risk_manager_weight: float = Field(default=0.15)

    # Consensus thresholds
    min_consensus_confidence: float = Field(
        default=0.60, description="Minimum agreement to trade"
    )
    max_disagreement_spread: float = Field(
        default=0.30, description="Max probability estimate spread (std dev)"
    )

    # Trading thresholds
    min_edge_to_trade: float = Field(default=0.05, description="Minimum 5% edge")
    temperature: float = Field(default=0.0, description="LLM temperature")

    # Batch processing
    parallel_batch_size: int = Field(default=5)


class PortfolioConfig(BaseSettings):
    """Portfolio optimization configuration."""

    model_config = SettingsConfigDict(env_prefix="PORTFOLIO_", env_file=_ENV_FILE, extra="ignore")

    # Kelly Criterion
    kelly_fraction: float = Field(default=0.75, description="Fractional Kelly (0.75 = 75%)")

    # Strategy allocation (must sum to 1.0)
    directional_allocation: float = Field(default=0.50)
    market_making_allocation: float = Field(default=0.40)
    arbitrage_allocation: float = Field(default=0.10)

    # Hedging
    enable_hedging: bool = Field(default=False)
    hedge_ratio: float = Field(default=0.25, description="Hedge size as % of main position")
    min_confidence_for_hedging: float = Field(default=0.60)
    max_hedge_amount_usd: float = Field(default=50.0)

    # Risk limits
    max_daily_loss_pct: float = Field(default=0.15, description="Max 15% daily loss")
    max_drawdown_pct: float = Field(default=0.50, description="Max 50% drawdown")
    daily_ai_cost_limit_usd: float = Field(default=50.0)


class ExitConfig(BaseSettings):
    """Exit management configuration."""

    model_config = SettingsConfigDict(env_prefix="EXIT_", env_file=_ENV_FILE, extra="ignore")

    # Take profit / Stop loss
    trailing_take_profit_pct: float = Field(default=0.20, description="20% profit")
    trailing_pullback_pct: float = Field(default=0.25, description="Exit on 25% pullback from high")
    stop_loss_pct: float = Field(default=0.15, description="15% stop loss")

    # Time-based
    max_hold_days: int = Field(default=10)
    exit_hours_before_expiry: float = Field(default=4.0)

    # Confidence decay
    enable_confidence_decay: bool = Field(default=True)
    confidence_decay_threshold: float = Field(
        default=0.50, description="Exit if confidence drops 50% from entry"
    )
    confidence_recheck_hours: int = Field(default=24)

    # Volatility adjustment
    volatility_stop_multiplier: float = Field(default=1.5)


class Settings(BaseSettings):
    """Main application settings."""

    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Sub-configurations - Polymarket Copy-Trader
    polymarket: PolymarketConfig = Field(default_factory=PolymarketConfig)
    copy_trader: CopyTraderConfig = Field(default_factory=CopyTraderConfig)
    safety_guards: SafetyGuardsConfig = Field(default_factory=SafetyGuardsConfig)
    leaders: LeaderConfig = Field(default_factory=LeaderConfig)

    # Sub-configurations - Kalshi
    kalshi: KalshiConfig = Field(default_factory=KalshiConfig)
    advisor: AdvisorConfig = Field(default_factory=AdvisorConfig)

    # Sub-configurations - Kalshi Hybrid Trading Bot (NEW)
    ai_providers: AIProvidersConfig = Field(default_factory=AIProvidersConfig)
    hybrid_trading: HybridTradingConfig = Field(default_factory=HybridTradingConfig)
    ensemble: EnsembleConfig = Field(default_factory=EnsembleConfig)
    portfolio: PortfolioConfig = Field(default_factory=PortfolioConfig)
    exit_rules: ExitConfig = Field(default_factory=ExitConfig)

    # Sub-configurations - Shared
    discord: DiscordConfig = Field(default_factory=DiscordConfig)
    dashboard: DashboardConfig = Field(default_factory=DashboardConfig)

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
