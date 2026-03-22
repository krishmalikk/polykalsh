"""
Kalshi Market Advisor - Interactive chat with Claude.

Provides market analysis and trade recommendations using
Kalshi API data and web research.
"""

import asyncio
import json
import structlog
from datetime import datetime
from typing import Any

from anthropic import Anthropic

from polykalsh.advisor.schemas import (
    ChatMessage,
    TradeRecommendation,
    PortfolioSummary,
    MarketSummary,
    AdvisorConfig,
)
from polykalsh.advisor.tools import ADVISOR_TOOLS, ADVISOR_SYSTEM_PROMPT
from polykalsh.clients.kalshi.client import KalshiClient
from polykalsh.clients.kalshi.schemas import CreateOrderRequest, OrderSide, OrderAction
from polykalsh.config import Settings

logger = structlog.get_logger()


class ChatAdvisor:
    """
    Interactive chat advisor for Kalshi market analysis.

    Uses Claude with tool calling to:
    - Check account balance and positions
    - Scan and research markets
    - Generate trade recommendations
    - Execute confirmed trades
    """

    def __init__(
        self,
        kalshi_client: KalshiClient,
        settings: Settings,
        config: AdvisorConfig | None = None,
    ):
        self.kalshi = kalshi_client
        self.settings = settings
        self.config = config or AdvisorConfig()

        # Initialize Anthropic client
        self.anthropic = Anthropic(
            api_key=settings.ai_providers.anthropic_api_key
        )
        self.model = settings.ai_providers.anthropic_model

        # Conversation state
        self.messages: list[dict] = []
        self.recommendations: dict[str, TradeRecommendation] = {}

        # Active client for tool execution (set during _run_async_with_client)
        self._active_client: KalshiClient | None = None

    def _create_kalshi_client(self) -> KalshiClient:
        """Create a fresh Kalshi client."""
        return KalshiClient(
            api_key_id=self.settings.kalshi.api_key_id,
            private_key_path=self.settings.kalshi.private_key_path,
            env=self.settings.kalshi.env,
            paper_mode=False,
        )

    def _run_async_with_client(self, coro):
        """Run async coroutine with a fresh Kalshi client context."""
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
            return future.result(timeout=60)

    @property
    def client(self) -> KalshiClient:
        """Get the active Kalshi client (only valid during tool execution)."""
        if self._active_client is None:
            raise RuntimeError("No active Kalshi client - must be called within _run_async_with_client")
        return self._active_client

    async def _execute_tool(self, tool_name: str, tool_input: dict) -> Any:
        """Execute a tool and return the result."""
        logger.info("executing_tool", tool=tool_name, input=tool_input)

        if tool_name == "get_balance":
            return await self._tool_get_balance()

        elif tool_name == "get_positions":
            return await self._tool_get_positions()

        elif tool_name == "get_markets":
            return await self._tool_get_markets(
                limit=tool_input.get("limit", 50),
                min_volume=tool_input.get("min_volume", 100),
                category=tool_input.get("category"),
            )

        elif tool_name == "get_market_details":
            return await self._tool_get_market_details(tool_input["ticker"])

        elif tool_name == "get_event_markets":
            return await self._tool_get_event_markets(tool_input["event_ticker"])

        elif tool_name == "web_search":
            return await self._tool_web_search(tool_input["query"])

        elif tool_name == "create_recommendation":
            return await self._tool_create_recommendation(tool_input)

        else:
            return {"error": f"Unknown tool: {tool_name}"}

    async def _tool_get_balance(self) -> dict:
        """Get account balance."""
        try:
            balance = await self.client.get_balance()
            positions, _ = await self.client.get_positions()
            open_count = sum(1 for p in positions if p.position != 0)

            return {
                "available_cash": f"${balance.available_usd:.2f}",
                "portfolio_value": f"${balance.portfolio_usd:.2f}",
                "total_value": f"${balance.total_usd:.2f}",
                "open_positions": open_count,
            }
        except Exception as e:
            logger.error("balance_fetch_failed", error=str(e))
            return {"error": f"Failed to fetch balance: {str(e)}"}

    async def _tool_get_positions(self) -> dict:
        """Get open positions."""
        try:
            positions, _ = await self.client.get_positions()
            open_positions = [p for p in positions if p.position != 0]

            if not open_positions:
                return {"positions": [], "message": "No open positions"}

            result = []
            for pos in open_positions:
                try:
                    market = await self.client.get_market(pos.ticker)
                    current_price = market.yes_bid / 100 if pos.position > 0 else market.no_bid / 100
                except Exception:
                    current_price = 0.50

                contracts = abs(pos.position)
                entry_price = (pos.total_cost / contracts / 100) if contracts > 0 else 0
                current_value = current_price * contracts
                cost_basis = pos.total_cost / 100
                pnl = current_value - cost_basis

                result.append({
                    "ticker": pos.ticker,
                    "side": "YES" if pos.position > 0 else "NO",
                    "contracts": contracts,
                    "entry_price": f"${entry_price:.2f}",
                    "current_price": f"${current_price:.2f}",
                    "cost": f"${cost_basis:.2f}",
                    "value": f"${current_value:.2f}",
                    "pnl": f"${pnl:+.2f}",
                })

            return {"positions": result}
        except Exception as e:
            logger.error("positions_fetch_failed", error=str(e))
            return {"error": f"Failed to fetch positions: {str(e)}"}

    async def _tool_get_markets(
        self,
        limit: int = 50,
        min_volume: int = 100,
        category: str | None = None,
    ) -> dict:
        """Get markets sorted by volume."""
        try:
            events = await self.client.get_top_events_by_volume(limit)

            markets = []
            for event in events[:20]:  # Limit to top 20 events
                try:
                    event_markets = await self.client.get_markets_for_event(event.event_ticker)
                    for m in event_markets:
                        if m.volume_24h >= min_volume:
                            yes_price = m.yes_bid / 100 if m.yes_bid else 0.50
                            if self.config.min_price <= yes_price <= self.config.max_price:
                                markets.append({
                                    "ticker": m.ticker,
                                    "title": m.title,
                                    "event": event.title,
                                    "yes_price": f"${yes_price:.2f}",
                                    "volume_24h": m.volume_24h,
                                    "spread": f"${m.spread:.2f}" if m.spread else "N/A",
                                })
                except Exception:
                    continue

            # Sort by volume and limit
            markets.sort(key=lambda x: x["volume_24h"], reverse=True)
            markets = markets[:limit]

            return {
                "markets": markets,
                "count": len(markets),
            }
        except Exception as e:
            logger.error("markets_fetch_failed", error=str(e))
            return {"error": f"Failed to fetch markets: {str(e)}"}

    async def _tool_get_market_details(self, ticker: str) -> dict:
        """Get detailed market info."""
        try:
            market = await self.client.get_market(ticker)
            orderbook = await self.client.get_orderbook(ticker, depth=5)
            trades, _ = await self.client.get_trades(ticker, limit=10)

            return {
                "ticker": market.ticker,
                "title": market.title,
                "event_ticker": market.event_ticker,
                "status": market.status,
                "yes_price": f"${market.yes_bid / 100:.2f}" if market.yes_bid else "N/A",
                "no_price": f"${market.no_bid / 100:.2f}" if market.no_bid else "N/A",
                "spread": f"${market.spread:.2f}" if market.spread else "N/A",
                "volume_24h": market.volume_24h,
                "open_interest": market.open_interest,
                "close_time": market.close_time.isoformat() if market.close_time else None,
                "orderbook": {
                    "yes_bids": [{"price": f"${l.price/100:.2f}", "qty": l.quantity} for l in (orderbook.yes or [])[:3]],
                    "no_bids": [{"price": f"${l.price/100:.2f}", "qty": l.quantity} for l in (orderbook.no or [])[:3]],
                },
                "recent_trades": [
                    {
                        "price": f"${t.yes_price/100:.2f}" if hasattr(t, 'yes_price') else "N/A",
                        "count": t.count if hasattr(t, 'count') else 0,
                    }
                    for t in trades[:5]
                ],
            }
        except Exception as e:
            logger.error("market_details_failed", ticker=ticker, error=str(e))
            return {"error": f"Failed to fetch market details: {str(e)}"}

    async def _tool_get_event_markets(self, event_ticker: str) -> dict:
        """Get all markets for an event."""
        try:
            markets = await self.client.get_markets_for_event(event_ticker)

            result = []
            for m in markets:
                yes_price = m.yes_bid / 100 if m.yes_bid else 0.50
                result.append({
                    "ticker": m.ticker,
                    "title": m.title,
                    "yes_price": f"${yes_price:.2f}",
                    "volume_24h": m.volume_24h,
                })

            return {"markets": result, "count": len(result)}
        except Exception as e:
            logger.error("event_markets_failed", event=event_ticker, error=str(e))
            return {"error": f"Failed to fetch event markets: {str(e)}"}

    async def _tool_web_search(self, query: str) -> dict:
        """Search the web for information using Tavily."""
        import os

        try:
            # Get Tavily API key from environment
            tavily_key = os.getenv("TAVILY_API_KEY", "")

            if tavily_key:
                from tavily import TavilyClient

                client = TavilyClient(api_key=tavily_key)
                response = client.search(query, search_depth="advanced", max_results=5)

                return {
                    "query": query,
                    "answer": response.get("answer", ""),
                    "results": [
                        {
                            "title": r.get("title", ""),
                            "url": r.get("url", ""),
                            "content": r.get("content", "")[:500],
                        }
                        for r in response.get("results", [])[:5]
                    ],
                }
            else:
                return {
                    "query": query,
                    "note": "Web search unavailable - TAVILY_API_KEY not configured.",
                }
        except Exception as e:
            logger.error("web_search_failed", query=query, error=str(e))
            return {"query": query, "error": f"Search failed: {str(e)}"}

    async def _tool_create_recommendation(self, params: dict) -> dict:
        """Create a trade recommendation."""
        try:
            ticker = params["market_ticker"]
            market = await self.client.get_market(ticker)

            side = params["side"].upper()
            prob_estimate = params["probability_estimate"]
            current_price = market.yes_bid / 100 if side == "YES" else market.no_bid / 100
            edge = prob_estimate - current_price if side == "YES" else (1 - prob_estimate) - current_price

            # Calculate contracts
            amount = params["suggested_amount"]
            contracts = max(1, int(amount / current_price))

            rec = TradeRecommendation(
                market_ticker=ticker,
                market_title=market.title,
                event_ticker=market.event_ticker,
                side=side,
                probability_estimate=prob_estimate,
                current_price=current_price,
                edge=edge,
                suggested_contracts=contracts,
                suggested_amount=contracts * current_price,
                reasoning=params["reasoning"],
                risks=params.get("risks", []),
            )

            self.recommendations[rec.id] = rec
            logger.info("recommendation_created", rec_id=rec.id, ticker=ticker)

            return {
                "success": True,
                "recommendation_id": rec.id,
                "market": market.title,
                "side": side,
                "contracts": contracts,
                "price": f"${current_price:.2f}",
                "total_cost": f"${rec.suggested_amount:.2f}",
                "edge": f"{edge*100:+.1f}%",
                "message": "Recommendation created. User must confirm to execute.",
            }
        except Exception as e:
            logger.error("create_recommendation_failed", error=str(e))
            return {"error": f"Failed to create recommendation: {str(e)}"}

    def chat(self, user_message: str) -> tuple[str, list[TradeRecommendation]]:
        """
        Process a user message and return the assistant's response.

        Returns:
            tuple of (response_text, new_recommendations)
        """
        # Add user message to history
        self.messages.append({
            "role": "user",
            "content": user_message,
        })

        # Track recommendations created in this turn
        initial_recs = set(self.recommendations.keys())

        # Call Claude with tools
        response = self.anthropic.messages.create(
            model=self.model,
            max_tokens=4096,
            system=ADVISOR_SYSTEM_PROMPT,
            tools=ADVISOR_TOOLS,
            messages=self.messages,
        )

        # Process response with tool calls
        while response.stop_reason == "tool_use":
            # Extract tool calls
            tool_calls = [block for block in response.content if block.type == "tool_use"]

            # Execute tools
            tool_results = []
            for tool_call in tool_calls:
                result = self._run_async_with_client(
                    self._execute_tool(tool_call.name, tool_call.input)
                )
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_call.id,
                    "content": json.dumps(result),
                })

            # Add assistant message with tool calls
            self.messages.append({
                "role": "assistant",
                "content": response.content,
            })

            # Add tool results
            self.messages.append({
                "role": "user",
                "content": tool_results,
            })

            # Continue conversation
            response = self.anthropic.messages.create(
                model=self.model,
                max_tokens=4096,
                system=ADVISOR_SYSTEM_PROMPT,
                tools=ADVISOR_TOOLS,
                messages=self.messages,
            )

        # Extract final text response
        text_content = ""
        for block in response.content:
            if hasattr(block, "text"):
                text_content += block.text

        # Add final response to history
        self.messages.append({
            "role": "assistant",
            "content": response.content,
        })

        # Get new recommendations
        new_recs = [
            rec for rec_id, rec in self.recommendations.items()
            if rec_id not in initial_recs
        ]

        return text_content, new_recs

    async def confirm_trade(self, recommendation_id: str) -> dict:
        """Execute a confirmed trade recommendation."""
        rec = self.recommendations.get(recommendation_id)
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
                type="market",  # Market order for simplicity
            )

            order = await self.client.place_order(order_request)

            rec.status = "executed"
            rec.executed_at = datetime.utcnow()
            rec.order_id = order.order_id

            logger.info(
                "trade_executed",
                rec_id=recommendation_id,
                order_id=order.order_id,
            )

            return {
                "success": True,
                "order_id": order.order_id,
                "message": f"Order placed: {rec.suggested_contracts} {rec.side} contracts on {rec.market_title}",
            }

        except Exception as e:
            rec.status = "failed"
            rec.error_message = str(e)
            logger.error("trade_execution_failed", rec_id=recommendation_id, error=str(e))
            return {"success": False, "error": str(e)}

    def reject_recommendation(self, recommendation_id: str) -> dict:
        """Reject a pending recommendation."""
        rec = self.recommendations.get(recommendation_id)
        if not rec:
            return {"success": False, "error": "Recommendation not found"}

        if rec.status != "pending":
            return {"success": False, "error": f"Recommendation already {rec.status}"}

        rec.status = "rejected"
        return {"success": True, "message": "Recommendation rejected"}

    def get_pending_recommendations(self) -> list[TradeRecommendation]:
        """Get all pending recommendations."""
        return [
            rec for rec in self.recommendations.values()
            if rec.status == "pending"
        ]

    def get_portfolio_summary(self) -> PortfolioSummary:
        """Get current portfolio summary."""
        result = self._run_async_with_client(self._tool_get_balance())

        if "error" in result:
            return PortfolioSummary(
                total_value=0,
                available_cash=0,
                positions_value=0,
                open_positions_count=0,
                pending_recommendations_count=len(self.get_pending_recommendations()),
            )

        # Parse dollar strings
        total = float(result["total_value"].replace("$", ""))
        available = float(result["available_cash"].replace("$", ""))
        portfolio = float(result["portfolio_value"].replace("$", ""))

        return PortfolioSummary(
            total_value=total,
            available_cash=available,
            positions_value=portfolio,
            open_positions_count=result.get("open_positions", 0),
            pending_recommendations_count=len(self.get_pending_recommendations()),
        )

    def clear_history(self):
        """Clear conversation history."""
        self.messages = []

    def clear_recommendations(self):
        """Clear all recommendations."""
        self.recommendations = {}
