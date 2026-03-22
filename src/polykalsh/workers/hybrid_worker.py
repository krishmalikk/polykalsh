"""
Hybrid trading bot worker.

Manages scheduled jobs for the Kalshi hybrid trading bot:
- Trading cycle execution
- Exit monitoring
- Daily summary generation
- Portfolio snapshots
"""

import asyncio
import signal
import sys
from datetime import datetime, time
from typing import Any

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from polykalsh.config import Settings, get_settings
from polykalsh.hybrid.orchestrator import HybridOrchestrator, TradingCycleResult
from polykalsh.notifications.discord import DiscordNotifier, NotificationLevel

logger = structlog.get_logger()


class HybridWorker:
    """
    Worker for the hybrid trading bot.

    Manages scheduled jobs using APScheduler:
    - Trading cycles (every N minutes)
    - Exit checks (more frequent than trading)
    - Daily summaries (once per day)
    - Portfolio snapshots (periodic)
    """

    def __init__(
        self,
        settings: Settings | None = None,
    ):
        """
        Initialize hybrid worker.

        Args:
            settings: Application settings (uses default if not provided)
        """
        self.settings = settings or get_settings()
        self._scheduler: AsyncIOScheduler | None = None
        self._orchestrator: HybridOrchestrator | None = None
        self._notifier: DiscordNotifier | None = None
        self._db_session: Session | None = None
        self._is_running = False
        self._is_paused = False
        self._pause_reason: str | None = None

        # State file for inter-process communication
        self._state_file = self.settings.data_dir / "hybrid_bot_state.json"

        # Stats
        self.cycles_completed = 0
        self.cycles_failed = 0
        self.last_cycle_time: datetime | None = None
        self.daily_pnl = 0.0
        self.daily_trades = 0
        self.daily_ai_cost = 0.0

        # Load initial state
        self._load_state()

    # ═══════════════════════════════════════════════════════════════════════════════
    # STATE MANAGEMENT (for inter-process communication)
    # ═══════════════════════════════════════════════════════════════════════════════

    def _load_state(self) -> None:
        """Load state from file."""
        import json
        try:
            if self._state_file.exists():
                with open(self._state_file) as f:
                    state = json.load(f)
                    self._is_paused = state.get("is_paused", False)
                    self._pause_reason = state.get("pause_reason")
        except Exception as e:
            logger.warning("state_load_error", error=str(e))

    def _save_state(self) -> None:
        """Save state to file."""
        import json
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self._state_file, "w") as f:
                json.dump({
                    "is_paused": self._is_paused,
                    "pause_reason": self._pause_reason,
                    "is_running": self._is_running,
                    "last_updated": datetime.utcnow().isoformat(),
                    "cycles_completed": self.cycles_completed,
                    "cycles_failed": self.cycles_failed,
                    "last_cycle_time": self.last_cycle_time.isoformat() if self.last_cycle_time else None,
                    "daily_pnl": self.daily_pnl,
                    "daily_trades": self.daily_trades,
                    "daily_ai_cost": self.daily_ai_cost,
                }, f, indent=2)
        except Exception as e:
            logger.warning("state_save_error", error=str(e))

    def _check_pause_state(self) -> bool:
        """Check if paused (reload from file to catch dashboard changes)."""
        self._load_state()
        return self._is_paused

    def pause(self, reason: str | None = None) -> dict:
        """Pause the trading bot."""
        self._is_paused = True
        self._pause_reason = reason
        self._save_state()
        logger.info("hybrid_bot_paused", reason=reason)
        return {"success": True, "paused": True, "reason": reason}

    def resume(self) -> dict:
        """Resume the trading bot."""
        self._is_paused = False
        self._pause_reason = None
        self._save_state()
        logger.info("hybrid_bot_resumed")
        return {"success": True, "paused": False}

    @property
    def is_paused(self) -> bool:
        """Check if paused."""
        return self._is_paused

    async def start(self) -> None:
        """Start the worker and all scheduled jobs."""
        logger.info("hybrid_worker_starting")

        # Initialize components
        await self._initialize()

        # Set up signal handlers
        self._setup_signal_handlers()

        # Start scheduler
        self._scheduler.start()
        self._is_running = True

        logger.info(
            "hybrid_worker_started",
            paper_mode=self.settings.hybrid_trading.paper_mode,
            scan_interval=self.settings.hybrid_trading.scan_interval_min,
        )

        # Send startup notification
        if self._notifier:
            await self._notifier.send_status(
                status="Started",
                portfolio_value=self._orchestrator.portfolio_state.total_value
                if self._orchestrator.portfolio_state
                else 0,
                open_positions=self._orchestrator.portfolio_state.open_positions_count
                if self._orchestrator.portfolio_state
                else 0,
                daily_pnl=0.0,
            )

        # Keep running
        try:
            while self._is_running:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass

        await self.stop()

    async def stop(self) -> None:
        """Stop the worker gracefully."""
        logger.info("hybrid_worker_stopping")
        self._is_running = False

        # Stop scheduler
        if self._scheduler and self._scheduler.running:
            self._scheduler.shutdown(wait=True)

        # Send shutdown notification
        if self._notifier:
            await self._notifier.send_status(
                status="Stopped",
                portfolio_value=self._orchestrator.portfolio_state.total_value
                if self._orchestrator and self._orchestrator.portfolio_state
                else 0,
                open_positions=self._orchestrator.portfolio_state.open_positions_count
                if self._orchestrator and self._orchestrator.portfolio_state
                else 0,
                daily_pnl=self.daily_pnl,
            )

        # Cleanup
        if self._orchestrator:
            await self._orchestrator.shutdown()

        if self._notifier:
            await self._notifier.close()

        if self._db_session:
            self._db_session.close()

        logger.info("hybrid_worker_stopped")

    async def _initialize(self) -> None:
        """Initialize all components."""
        # Database
        engine = create_engine(self.settings.database_url)
        SessionLocal = sessionmaker(bind=engine)
        self._db_session = SessionLocal()

        # Orchestrator
        self._orchestrator = HybridOrchestrator(
            settings=self.settings,
            db_session=self._db_session,
        )
        await self._orchestrator.initialize()

        # Discord notifier
        if self.settings.discord.is_configured:
            self._notifier = DiscordNotifier(
                webhook_url=self.settings.discord.webhook_url,
                error_webhook_url=self.settings.discord.webhook_errors or None,
                user_id=self.settings.discord.user_id or None,
                mention_on_critical=self.settings.discord.mention_on_critical,
            )

        # Scheduler
        self._scheduler = AsyncIOScheduler()
        self._setup_jobs()

    def _setup_jobs(self) -> None:
        """Set up scheduled jobs."""
        # Trading cycle - runs every N minutes
        self._scheduler.add_job(
            self._run_trading_cycle,
            trigger=IntervalTrigger(
                minutes=self.settings.hybrid_trading.scan_interval_min
            ),
            id="trading_cycle",
            name="Trading Cycle",
            max_instances=1,
            coalesce=True,
        )

        # Exit check - runs more frequently (every 5 minutes)
        self._scheduler.add_job(
            self._check_exits,
            trigger=IntervalTrigger(
                minutes=self.settings.hybrid_trading.exit_check_interval_min
            ),
            id="exit_check",
            name="Exit Check",
            max_instances=1,
            coalesce=True,
        )

        # Daily summary - runs at 11:55 PM UTC
        self._scheduler.add_job(
            self._send_daily_summary,
            trigger=CronTrigger(hour=23, minute=55),
            id="daily_summary",
            name="Daily Summary",
            max_instances=1,
        )

        # Daily reset - runs at midnight UTC
        self._scheduler.add_job(
            self._daily_reset,
            trigger=CronTrigger(hour=0, minute=0),
            id="daily_reset",
            name="Daily Reset",
            max_instances=1,
        )

        # Portfolio snapshot - runs every hour
        self._scheduler.add_job(
            self._take_portfolio_snapshot,
            trigger=IntervalTrigger(hours=1),
            id="portfolio_snapshot",
            name="Portfolio Snapshot",
            max_instances=1,
        )

        logger.info(
            "jobs_scheduled",
            jobs=[job.id for job in self._scheduler.get_jobs()],
        )

    def _setup_signal_handlers(self) -> None:
        """Set up signal handlers for graceful shutdown."""
        def signal_handler(sig: int, frame: Any) -> None:
            logger.info("signal_received", signal=sig)
            self._is_running = False

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

    # ═══════════════════════════════════════════════════════════════════════════
    # SCHEDULED JOBS
    # ═══════════════════════════════════════════════════════════════════════════

    async def _run_trading_cycle(self) -> None:
        """Run a single trading cycle."""
        # Check if paused (reload from state file)
        if self._check_pause_state():
            logger.info("trading_cycle_skipped_paused")
            return

        logger.info("trading_cycle_start")

        try:
            result = await self._orchestrator.run_cycle()
            self.cycles_completed += 1
            self.last_cycle_time = datetime.utcnow()

            # Update daily stats
            self.daily_pnl += result.cycle_pnl
            self.daily_trades += result.positions_opened + result.positions_closed
            self.daily_ai_cost += result.ensemble_cost_usd + result.research_cost_usd

            # Log results
            logger.info(
                "trading_cycle_complete",
                markets_analyzed=result.markets_analyzed,
                orders_placed=result.orders_placed,
                cycle_pnl=result.cycle_pnl,
                duration=result.duration_seconds,
            )

            # Save state for dashboard
            self._save_state()

            # Send notifications for trades
            await self._notify_trades(result)

            # Check for errors
            if result.errors:
                for error in result.errors[:3]:  # Limit to 3
                    if self._notifier:
                        await self._notifier.send_error(
                            error_type="Cycle Error",
                            message=error[:500],
                        )

        except Exception as e:
            self.cycles_failed += 1
            logger.error("trading_cycle_error", error=str(e))

            if self._notifier:
                await self._notifier.send_error(
                    error_type="Trading Cycle Failed",
                    message=str(e),
                )

    async def _check_exits(self) -> None:
        """Check positions for exit conditions."""
        if not self._orchestrator or not self._orchestrator.portfolio_state:
            return

        if not self._orchestrator.portfolio_state.positions:
            return

        logger.debug("exit_check_start")

        try:
            # Update prices
            await self._orchestrator._update_positions(TradingCycleResult())

            # Check exits
            result = TradingCycleResult()
            exit_signals = await self._orchestrator._check_exits(result)

            for signal in exit_signals:
                await self._orchestrator._execute_exit(signal, result)

                # Notify
                if self._notifier and result.positions_closed > 0:
                    position = self._orchestrator.portfolio_state.get_position(
                        signal.market_ticker
                    )
                    if position:
                        await self._notifier.send_trade_exit(
                            market_ticker=signal.market_ticker,
                            market_title="",
                            side=signal.side,
                            contracts=signal.contracts,
                            entry_price=position.entry_price,
                            exit_price=signal.target_price or position.current_price or 0,
                            pnl_usd=position.unrealized_pnl,
                            pnl_pct=position.unrealized_pnl_pct,
                            reason=signal.reason.value,
                            hold_hours=position.entry_time.timestamp()
                            if position.entry_time
                            else 0,
                        )

            if result.positions_closed > 0:
                logger.info(
                    "exits_executed",
                    count=result.positions_closed,
                )

        except Exception as e:
            logger.error("exit_check_error", error=str(e))

    async def _send_daily_summary(self) -> None:
        """Send daily summary notification."""
        if not self._notifier or not self._orchestrator:
            return

        state = self._orchestrator.portfolio_state
        if not state:
            return

        try:
            await self._notifier.send_daily_summary(
                date=datetime.utcnow().strftime("%Y-%m-%d"),
                starting_balance=state.starting_balance,
                ending_balance=state.total_value,
                daily_pnl=self.daily_pnl,
                trades_opened=self.daily_trades,
                trades_closed=self.daily_trades,  # Simplified
                win_rate=0.0,  # Would need trade history
                ai_cost=self.daily_ai_cost,
                open_positions=state.open_positions_count,
            )

            logger.info("daily_summary_sent")

        except Exception as e:
            logger.error("daily_summary_error", error=str(e))

    async def _daily_reset(self) -> None:
        """Reset daily counters."""
        logger.info(
            "daily_reset",
            previous_pnl=self.daily_pnl,
            previous_trades=self.daily_trades,
        )

        self.daily_pnl = 0.0
        self.daily_trades = 0
        self.daily_ai_cost = 0.0

        # Update portfolio high water mark
        if self._orchestrator and self._orchestrator._portfolio_optimizer:
            self._orchestrator._portfolio_optimizer.update_high_water_mark(
                self._orchestrator.portfolio_state
            )

    async def _take_portfolio_snapshot(self) -> None:
        """Take a portfolio snapshot for historical tracking."""
        if not self._orchestrator or not self._orchestrator.portfolio_state:
            return

        state = self._orchestrator.portfolio_state

        logger.info(
            "portfolio_snapshot",
            total_value=state.total_value,
            cash=state.cash_balance,
            positions=state.open_positions_count,
            pnl_pct=state.total_pnl_pct,
        )

        # TODO: Save to database for historical tracking

    async def _notify_trades(self, result: TradingCycleResult) -> None:
        """Send notifications for trades executed in a cycle."""
        if not self._notifier:
            return

        # This would need more context from the orchestrator
        # to send detailed trade notifications
        if result.positions_opened > 0:
            await self._notifier.send(
                content=f"Opened {result.positions_opened} new position(s)",
                level=NotificationLevel.SUCCESS,
            )

    # ═══════════════════════════════════════════════════════════════════════════
    # STATUS AND MONITORING
    # ═══════════════════════════════════════════════════════════════════════════

    def get_status(self) -> dict[str, Any]:
        """Get worker status."""
        return {
            "is_running": self._is_running,
            "is_paused": self._is_paused,
            "pause_reason": self._pause_reason,
            "paper_mode": self.settings.hybrid_trading.paper_mode,
            "cycles_completed": self.cycles_completed,
            "cycles_failed": self.cycles_failed,
            "last_cycle_time": self.last_cycle_time.isoformat()
            if self.last_cycle_time
            else None,
            "daily_stats": {
                "pnl": self.daily_pnl,
                "trades": self.daily_trades,
                "ai_cost": self.daily_ai_cost,
            },
            "jobs": [
                {
                    "id": job.id,
                    "name": job.name,
                    "next_run": job.next_run_time.isoformat()
                    if job.next_run_time
                    else None,
                }
                for job in (self._scheduler.get_jobs() if self._scheduler else [])
            ],
            "orchestrator": self._orchestrator.get_status()
            if self._orchestrator
            else None,
            "notifications": self._notifier.get_stats() if self._notifier else None,
        }


async def run_worker() -> None:
    """Run the hybrid worker."""
    worker = HybridWorker()
    await worker.start()


def main() -> None:
    """Entry point for the worker."""
    try:
        asyncio.run(run_worker())
    except KeyboardInterrupt:
        logger.info("worker_interrupted")
        sys.exit(0)


if __name__ == "__main__":
    main()
