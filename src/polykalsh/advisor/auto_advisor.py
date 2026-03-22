"""
Auto-Advisor - Automatic market recommendations using AI analysis.

Generates trade recommendations without user interaction,
refreshing every 5 minutes with cached results.
"""

import asyncio
import json
import re
import structlog
from datetime import datetime, timedelta, timezone
from typing import Any

from anthropic import Anthropic

from polykalsh.advisor.schemas import (
    AutoAdvisorConfig,
    AutoRecommendation,
    RecommendationsCache,
    PortfolioSummary,
)
from polykalsh.clients.kalshi.client import KalshiClient
from polykalsh.clients.kalshi.schemas import CreateOrderRequest, OrderSide, OrderAction
from polykalsh.config import Settings

logger = structlog.get_logger()


AUTO_ANALYSIS_SYSTEM_PROMPT = """You are analyzing Kalshi prediction markets to find the best trading opportunities.

## Your Task
Given a list of markets with current prices and volume, identify the 3-5 BEST opportunities where you have edge.

## For Each Opportunity, Provide:

1. **market_ticker**: The exact ticker from the input
2. **side**: "YES" or "NO"
3. **probability_estimate**: Your honest probability (0.0-1.0)
4. **confidence**: "low", "medium", "high", or "very_high" based on:
   - Data availability
   - Clear resolution criteria
   - Time until resolution
5. **reasoning**: 2-3 sentences explaining your edge
6. **bull_case**: Why this could pay off (1-2 sentences)
7. **bear_case**: Why this could fail (1-2 sentences)
8. **risk_level**: "low", "medium", or "high"
9. **risk_details**: Specific risks for this trade (1-2 sentences)
10. **urgency**: "low", "medium", or "high" (based on timing, price momentum)

## Rules
- Only recommend markets where your edge > 5%
- Be honest about uncertainty - "medium confidence" is fine
- Prioritize markets with clear, verifiable resolution criteria
- Consider market liquidity and timing
- Diversify across different event types when possible

## Output Format
Return ONLY a JSON array of recommendations. No other text before or after.
Example:
[
  {
    "market_ticker": "KXMARKET-EXAMPLE",
    "side": "YES",
    "probability_estimate": 0.65,
    "confidence": "medium",
    "reasoning": "Based on recent polling data and historical trends...",
    "bull_case": "If the trend continues, this should resolve YES.",
    "bear_case": "Unexpected events could shift the outcome.",
    "risk_level": "medium",
    "risk_details": "Polling accuracy has been historically variable.",
    "urgency": "low"
  }
]
"""


class AutoAdvisor:
    """
    Automatic market advisor that generates recommendations.

    Features:
    - AI-powered market analysis using Claude
    - In-memory caching with 5-minute TTL
    - Relaxed filters to find more markets
    - Position sizing based on user balance
    """

    def __init__(
        self,
        kalshi_client: KalshiClient,
        settings: Settings,
        config: AutoAdvisorConfig | None = None,
    ):
        self.kalshi = kalshi_client
        self.settings = settings
        self.config = config or AutoAdvisorConfig()

        # Bot control state
        self._is_paused = False
        self._pause_reason: str | None = None

        # Initialize Anthropic client
        self.anthropic = Anthropic(
            api_key=settings.ai_providers.anthropic_api_key
        )
        self.model = settings.ai_providers.anthropic_model

        # Cache state
        self._cache = RecommendationsCache()
        self._is_analyzing = False

    def _create_kalshi_client(self) -> KalshiClient:
        """Create a fresh Kalshi client."""
        return KalshiClient(
            api_key_id=self.settings.kalshi.api_key_id,
            private_key_path=self.settings.kalshi.private_key_path,
            env=self.settings.kalshi.env,
            paper_mode=False,
        )

    def _run_async(self, coro):
        """Run async coroutine in a thread."""
        import concurrent.futures

        advisor = self

        async def run():
            client = advisor._create_kalshi_client()
            async with client:
                advisor._active_client = client
                try:
                    return await coro
                finally:
                    advisor._active_client = None

        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(asyncio.run, run())
            return future.result(timeout=120)  # 2 minute timeout for analysis

    def get_recommendations_cache(self) -> RecommendationsCache:
        """Get the current recommendations cache."""
        # Check if cache is stale
        if self._cache.generated_at:
            age = (datetime.now(timezone.utc) - self._cache.generated_at).total_seconds()
            self._cache.is_stale = age > self.config.refresh_interval_seconds

        return self._cache

    # ═══════════════════════════════════════════════════════════════════════════════
    # BOT CONTROL
    # ═══════════════════════════════════════════════════════════════════════════════

    def pause(self, reason: str | None = None) -> dict:
        """Pause the auto-advisor."""
        self._is_paused = True
        self._pause_reason = reason
        logger.info("auto_advisor_paused", reason=reason)
        return {"success": True, "paused": True, "reason": reason}

    def resume(self) -> dict:
        """Resume the auto-advisor."""
        self._is_paused = False
        self._pause_reason = None
        logger.info("auto_advisor_resumed")
        return {"success": True, "paused": False}

    def get_status(self) -> dict:
        """Get the current status of the auto-advisor."""
        cache = self.get_recommendations_cache()
        return {
            "is_paused": self._is_paused,
            "pause_reason": self._pause_reason,
            "is_analyzing": self._is_analyzing,
            "recommendations_count": len(cache.recommendations),
            "last_refresh": cache.generated_at.isoformat() if cache.generated_at else None,
            "is_stale": cache.is_stale,
            "analysis_status": cache.analysis_status,
        }

    @property
    def is_paused(self) -> bool:
        """Check if the advisor is paused."""
        return self._is_paused

    async def analyze_markets(self, force: bool = False) -> list[AutoRecommendation]:
        """
        Analyze markets and generate recommendations.

        Args:
            force: Force refresh even if cache is valid

        Returns:
            List of auto-recommendations
        """
        # Check if paused
        if self._is_paused and not force:
            logger.info("auto_advisor_skipped_paused")
            return self._cache.recommendations

        # Check if already analyzing
        if self._is_analyzing:
            logger.warning("auto_advisor_already_analyzing")
            return self._cache.recommendations

        # Check cache validity
        if not force and self._cache.generated_at:
            age = (datetime.now(timezone.utc) - self._cache.generated_at).total_seconds()
            if age < self.config.refresh_interval_seconds:
                logger.debug("auto_advisor_cache_hit", age_seconds=age)
                return self._cache.recommendations

        self._is_analyzing = True
        self._cache.analysis_status = "analyzing"

        try:
            logger.info("auto_advisor_analysis_start")

            # Get balance and positions
            balance = await self._active_client.get_balance()
            available_cash = balance.available_usd

            logger.info("auto_advisor_balance", available=available_cash)

            # Fetch markets with relaxed filters
            markets = await self._fetch_analyzable_markets()

            if not markets:
                logger.warning("auto_advisor_no_markets")
                self._cache = RecommendationsCache(
                    recommendations=[],
                    generated_at=datetime.now(timezone.utc),
                    expires_at=datetime.now(timezone.utc) + timedelta(seconds=self.config.refresh_interval_seconds),
                    balance_at_generation=available_cash,
                    analysis_status="complete",
                    error_message="No markets available for analysis",
                )
                return []

            logger.info("auto_advisor_markets_found", count=len(markets))

            # Build prompt for Claude
            prompt = self._build_analysis_prompt(markets, available_cash)

            # Call Claude for analysis
            response = self.anthropic.messages.create(
                model=self.model,
                max_tokens=4096,
                system=AUTO_ANALYSIS_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )

            # Parse response
            text_content = ""
            for block in response.content:
                if hasattr(block, "text"):
                    text_content += block.text

            recommendations = await self._parse_recommendations(
                text_content, markets, available_cash
            )

            logger.info("auto_advisor_analysis_complete", recommendations=len(recommendations))

            # Update cache
            self._cache = RecommendationsCache(
                recommendations=recommendations,
                generated_at=datetime.now(timezone.utc),
                expires_at=datetime.now(timezone.utc) + timedelta(seconds=self.config.refresh_interval_seconds),
                balance_at_generation=available_cash,
                analysis_status="complete",
            )

            return recommendations

        except Exception as e:
            logger.error("auto_advisor_analysis_failed", error=str(e))
            self._cache.analysis_status = "error"
            self._cache.error_message = str(e)
            return []

        finally:
            self._is_analyzing = False

    async def _fetch_analyzable_markets(self) -> list[dict]:
        """
        Fetch markets with relaxed filters for analysis.

        Uses multiple fallback strategies to ensure we get markets.
        """
        markets = []
        all_markets_raw = []  # For fallback

        try:
            # Strategy 1: Get top events by volume
            events = await self._active_client.get_top_events_by_volume(n=50, status="open")
            logger.debug("auto_advisor_events_fetched", count=len(events))

            total_markets_seen = 0
            for event in events[:30]:  # Limit to top 30 events
                try:
                    event_markets = await self._active_client.get_markets_for_event(
                        event.event_ticker
                    )

                    logger.debug("auto_advisor_event_markets",
                                ticker=event.event_ticker,
                                market_count=len(event_markets) if event_markets else 0)

                    if not event_markets:
                        continue

                    for m in event_markets:
                        total_markets_seen += 1
                        # Skip non-active markets (API uses "active" not "open")
                        if m.status != "active":
                            continue

                        yes_price = m.yes_bid / 100 if m.yes_bid > 0 else 0.50
                        no_price = m.no_bid / 100 if m.no_bid > 0 else 1 - yes_price

                        # Calculate hours until close
                        hours_until_close = None
                        if m.close_time:
                            delta = m.close_time - datetime.now(timezone.utc)
                            hours_until_close = max(0, delta.total_seconds() / 3600)

                        # Calculate spread
                        spread = 0.0
                        if m.yes_bid > 0 and m.yes_ask > 0:
                            spread = (m.yes_ask - m.yes_bid) / 100

                        # Determine time bucket
                        time_bucket = "long_term"
                        if hours_until_close is not None:
                            if hours_until_close <= 24:
                                time_bucket = "today"
                            elif hours_until_close <= 168:  # 7 days
                                time_bucket = "this_week"
                            elif hours_until_close <= 720:  # 30 days
                                time_bucket = "this_month"

                        market_data = {
                            "event_ticker": event.event_ticker,
                            "event_title": event.title,
                            "market_ticker": m.ticker,
                            "market_title": m.title,
                            "yes_price": yes_price,
                            "no_price": no_price,
                            "spread": spread,
                            "volume_24h": m.volume_24h,
                            "open_interest": m.open_interest,
                            "hours_until_close": hours_until_close,
                            "category": getattr(event, 'category', None) or "Other",
                            "time_bucket": time_bucket,
                        }

                        # Store all markets for fallback (just need a price)
                        if yes_price > 0.01 and yes_price < 0.99:
                            all_markets_raw.append(market_data)

                        # Apply minimal filters - require price in tradeable range
                        # Accept markets with volume OR just with valid pricing
                        if 0.05 <= yes_price <= 0.95:
                            markets.append(market_data)

                except Exception as e:
                    logger.warning("auto_advisor_event_fetch_error", event_ticker=event.event_ticker, error=str(e))
                    continue

            logger.info("auto_advisor_total_markets_seen", total=total_markets_seen, filtered=len(markets), raw=len(all_markets_raw))

            # Separate markets into time buckets for prioritization
            short_term_markets = []  # < 168 hours (1 week)
            medium_term_markets = []  # 168-720 hours (1 week - 1 month)
            long_term_markets = []  # > 720 hours

            for m in markets:
                tb = m.get("time_bucket", "long_term")
                if tb in ["today", "this_week"]:
                    short_term_markets.append(m)
                elif tb == "this_month":
                    medium_term_markets.append(m)
                else:
                    long_term_markets.append(m)

            # Sort each bucket by volume (best opportunities first)
            short_term_markets.sort(key=lambda x: x["volume_24h"], reverse=True)
            medium_term_markets.sort(key=lambda x: x["volume_24h"], reverse=True)
            long_term_markets.sort(key=lambda x: x["volume_24h"], reverse=True)

            logger.info("auto_advisor_time_buckets",
                short_term=len(short_term_markets),
                medium_term=len(medium_term_markets),
                long_term=len(long_term_markets))

            # Build final list prioritizing short-term if configured
            if self.config.prioritize_short_term:
                final_markets = []
                final_markets.extend(short_term_markets)
                final_markets.extend(medium_term_markets)
                final_markets.extend(long_term_markets)
            else:
                # Just sort by volume if not prioritizing short-term
                final_markets = markets
                final_markets.sort(key=lambda x: x["volume_24h"], reverse=True)

            # If we got markets, return them
            if final_markets:
                logger.info("auto_advisor_filtered_markets", count=len(final_markets))
                return final_markets[:50]

            # Fallback: return any markets we found, sorted by volume
            if all_markets_raw:
                all_markets_raw.sort(key=lambda x: x["volume_24h"], reverse=True)
                logger.info("auto_advisor_fallback_markets", count=len(all_markets_raw))
                return all_markets_raw[:50]

            logger.warning("auto_advisor_no_markets_found", total_seen=total_markets_seen)
            return []

        except Exception as e:
            logger.error("auto_advisor_market_fetch_failed", error=str(e))
            return []

    def _build_analysis_prompt(self, markets: list[dict], available_cash: float) -> str:
        """Build the analysis prompt for Claude."""
        markets_text = ""
        for i, m in enumerate(markets[:25], 1):  # Limit to 25 markets for analysis
            hours_str = f"{m['hours_until_close']:.1f}h" if m['hours_until_close'] else "N/A"
            markets_text += f"""
{i}. {m['market_title']}
   Ticker: {m['market_ticker']}
   Event: {m['event_title']}
   YES Price: ${m['yes_price']:.2f} | NO Price: ${m['no_price']:.2f}
   Spread: ${m['spread']:.2f} | Volume 24h: {m['volume_24h']:,}
   Closes in: {hours_str}
"""

        return f"""Analyze these Kalshi prediction markets and identify the 3-5 best trading opportunities.

AVAILABLE BALANCE: ${available_cash:.2f}

MARKETS TO ANALYZE:
{markets_text}

Remember:
- Only recommend if your edge is at least 5%
- Position size should not exceed 5% of balance (${available_cash * 0.05:.2f})
- Total deployment should not exceed 40% of balance (${available_cash * 0.40:.2f})
- Return ONLY a JSON array of recommendations"""

    async def _parse_recommendations(
        self,
        response_text: str,
        markets: list[dict],
        available_cash: float,
    ) -> list[AutoRecommendation]:
        """Parse Claude's response into AutoRecommendation objects."""
        recommendations = []

        try:
            # Extract JSON from response (handle potential markdown code blocks)
            json_text = response_text.strip()
            if json_text.startswith("```"):
                # Remove markdown code block
                json_text = re.sub(r'^```(?:json)?\n?', '', json_text)
                json_text = re.sub(r'\n?```$', '', json_text)

            parsed = json.loads(json_text)

            if not isinstance(parsed, list):
                logger.error("auto_advisor_parse_error", error="Response is not a list")
                return []

            # Create market lookup
            market_lookup = {m["market_ticker"]: m for m in markets}

            for item in parsed:
                try:
                    ticker = item.get("market_ticker")
                    if ticker not in market_lookup:
                        logger.warning("auto_advisor_unknown_ticker", ticker=ticker)
                        continue

                    market = market_lookup[ticker]
                    side = item.get("side", "YES").upper()
                    prob_estimate = float(item.get("probability_estimate", 0.5))

                    # Calculate prices
                    yes_price = market["yes_price"]
                    no_price = market["no_price"]
                    current_price = yes_price if side == "YES" else no_price

                    # Calculate edge
                    if side == "YES":
                        edge = prob_estimate - yes_price
                    else:
                        edge = (1 - prob_estimate) - no_price

                    # Skip if edge is too low
                    if edge < self.config.min_edge:
                        continue

                    # Position sizing
                    max_position = available_cash * self.config.max_position_pct
                    kelly_fraction = min(0.25, edge / (1 - current_price) if current_price < 1 else 0.25)
                    suggested_amount = min(max_position, available_cash * kelly_fraction)
                    suggested_amount = max(1.0, round(suggested_amount, 2))

                    contracts = max(1, int(suggested_amount / current_price))
                    actual_cost = contracts * current_price

                    # Calculate profit potential and max loss
                    profit_potential = (1.0 - current_price) * contracts
                    max_loss = current_price * contracts

                    rec = AutoRecommendation(
                        market_ticker=ticker,
                        market_title=market["market_title"],
                        event_ticker=market["event_ticker"],
                        event_title=market["event_title"],
                        yes_price=yes_price,
                        no_price=no_price,
                        spread=market["spread"],
                        volume_24h=market["volume_24h"],
                        side=side,
                        probability_estimate=prob_estimate,
                        edge=edge,
                        confidence=item.get("confidence", "medium"),
                        suggested_contracts=contracts,
                        suggested_amount=actual_cost,
                        profit_potential=profit_potential,
                        max_loss=max_loss,
                        reasoning=item.get("reasoning", ""),
                        bull_case=item.get("bull_case", ""),
                        bear_case=item.get("bear_case", ""),
                        risk_level=item.get("risk_level", "medium"),
                        risk_details=item.get("risk_details", ""),
                        risks=[item.get("risk_details", "")] if item.get("risk_details") else [],
                        hours_until_close=market.get("hours_until_close"),
                        urgency=item.get("urgency", "low"),
                        expires_at=datetime.now(timezone.utc) + timedelta(seconds=self.config.refresh_interval_seconds),
                        category=market.get("category", "Other"),
                        time_bucket=market.get("time_bucket", "long_term"),
                    )

                    recommendations.append(rec)

                except Exception as e:
                    logger.warning("auto_advisor_parse_item_error", error=str(e))
                    continue

        except json.JSONDecodeError as e:
            logger.error("auto_advisor_json_parse_error", error=str(e), text=response_text[:200])
            return []

        return recommendations[:self.config.max_recommendations]

    async def confirm_recommendation(self, recommendation_id: str) -> dict:
        """Execute a confirmed auto-recommendation."""
        rec = None
        for r in self._cache.recommendations:
            if r.id == recommendation_id:
                rec = r
                break

        if not rec:
            return {"success": False, "error": "Recommendation not found"}

        if rec.status != "pending":
            return {"success": False, "error": f"Recommendation already {rec.status}"}

        try:
            # Create order
            order_request = CreateOrderRequest(
                ticker=rec.market_ticker,
                side=OrderSide.YES if rec.side == "YES" else OrderSide.NO,
                action=OrderAction.BUY,
                count=rec.suggested_contracts,
                type="market",
            )

            order = await self._active_client.place_order(order_request)

            rec.status = "confirmed"
            rec.order_id = order.order_id

            logger.info(
                "auto_recommendation_executed",
                rec_id=recommendation_id,
                order_id=order.order_id,
            )

            return {
                "success": True,
                "order_id": order.order_id,
                "message": f"Order placed: {rec.suggested_contracts} {rec.side} contracts on {rec.market_title}",
            }

        except Exception as e:
            rec.status = "rejected"
            rec.error_message = str(e)
            logger.error("auto_recommendation_execution_failed", rec_id=recommendation_id, error=str(e))
            return {"success": False, "error": str(e)}

    def reject_recommendation(self, recommendation_id: str) -> dict:
        """Reject a pending auto-recommendation."""
        for rec in self._cache.recommendations:
            if rec.id == recommendation_id:
                if rec.status != "pending":
                    return {"success": False, "error": f"Recommendation already {rec.status}"}
                rec.status = "rejected"
                return {"success": True, "message": "Recommendation rejected"}

        return {"success": False, "error": "Recommendation not found"}

    def get_portfolio_summary(self) -> PortfolioSummary:
        """Get current portfolio summary."""
        try:
            result = self._run_async(self._get_portfolio_async())
            return result
        except Exception as e:
            logger.error("auto_advisor_portfolio_error", error=str(e))
            return PortfolioSummary(
                total_value=0,
                available_cash=0,
                positions_value=0,
                open_positions_count=0,
                pending_recommendations_count=len([
                    r for r in self._cache.recommendations if r.status == "pending"
                ]),
            )

    async def _get_portfolio_async(self) -> PortfolioSummary:
        """Get portfolio summary async."""
        balance = await self._active_client.get_balance()
        positions, _ = await self._active_client.get_positions()
        open_count = sum(1 for p in positions if p.position != 0)

        return PortfolioSummary(
            total_value=balance.total_usd,
            available_cash=balance.available_usd,
            positions_value=balance.portfolio_usd,
            open_positions_count=open_count,
            pending_recommendations_count=len([
                r for r in self._cache.recommendations if r.status == "pending"
            ]),
        )
