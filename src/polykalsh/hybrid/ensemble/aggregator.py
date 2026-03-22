"""
Ensemble aggregator for weighted consensus.

Combines agent responses into a final trading decision.
"""

import asyncio
from datetime import datetime
from typing import Any

import numpy as np
import structlog

from polykalsh.hybrid.ensemble.base import BaseAgent
from polykalsh.hybrid.ensemble.schemas import (
    AgentResponse,
    AgentRole,
    EnsembleResult,
    MarketContext,
    TradeAction,
)

logger = structlog.get_logger()


class EnsembleAggregator:
    """
    Aggregates responses from multiple AI agents into consensus.

    Features:
    - Weighted probability averaging
    - Disagreement detection
    - Action voting
    - Combined reasoning
    """

    def __init__(
        self,
        agents: list[BaseAgent],
        min_consensus_confidence: float = 0.60,
        max_disagreement: float = 0.30,
        min_edge: float = 0.05,
        parallel_batch_size: int = 5,
    ):
        """
        Initialize aggregator.

        Args:
            agents: List of AI agents
            min_consensus_confidence: Minimum confidence to recommend trade
            max_disagreement: Maximum acceptable std dev in probability estimates
            min_edge: Minimum edge (|estimated_prob - market_price|) to trade
            parallel_batch_size: Max concurrent agent calls
        """
        self.agents = agents
        self.min_consensus_confidence = min_consensus_confidence
        self.max_disagreement = max_disagreement
        self.min_edge = min_edge
        self.parallel_batch_size = parallel_batch_size

        # Verify weights sum to 1
        total_weight = sum(a.weight for a in agents)
        if abs(total_weight - 1.0) > 0.01:
            logger.warning(
                "agent_weights_not_normalized",
                total_weight=total_weight,
            )

    async def analyze(self, context: MarketContext) -> EnsembleResult:
        """
        Run all agents and aggregate responses.

        Args:
            context: Market context for analysis

        Returns:
            Aggregated ensemble result
        """
        logger.info(
            "ensemble_start",
            market=context.market_ticker,
            agents=len(self.agents),
        )

        # Run agents in parallel (with batch size limit)
        responses = await self._run_agents(context)

        # Aggregate responses
        result = self._aggregate(responses, context)

        logger.info(
            "ensemble_complete",
            market=context.market_ticker,
            action=result.final_action.value,
            probability=result.weighted_probability,
            confidence=result.consensus_confidence,
            disagreement=result.disagreement_score,
            should_trade=result.should_trade,
        )

        return result

    async def _run_agents(self, context: MarketContext) -> list[AgentResponse]:
        """Run all agents and collect responses."""
        semaphore = asyncio.Semaphore(self.parallel_batch_size)

        async def run_with_semaphore(agent: BaseAgent) -> AgentResponse:
            async with semaphore:
                return await agent.analyze(context)

        tasks = [run_with_semaphore(agent) for agent in self.agents]
        responses = await asyncio.gather(*tasks, return_exceptions=True)

        # Filter out exceptions
        valid_responses: list[AgentResponse] = []
        for agent, response in zip(self.agents, responses):
            if isinstance(response, Exception):
                logger.error(
                    "agent_exception",
                    role=agent.role.value,
                    error=str(response),
                )
                # Create a SKIP response for failed agents
                valid_responses.append(
                    AgentResponse(
                        action=TradeAction.SKIP,
                        probability_estimate=0.5,
                        confidence=0.0,
                        reasoning=f"Agent error: {response}",
                        agent_role=agent.role,
                    )
                )
            else:
                valid_responses.append(response)

        return valid_responses

    def _aggregate(
        self,
        responses: list[AgentResponse],
        context: MarketContext,
    ) -> EnsembleResult:
        """Aggregate agent responses into consensus."""
        # Extract values
        probabilities = [r.probability_estimate for r in responses]
        confidences = [r.confidence for r in responses]
        weights = [self._get_agent_weight(r.agent_role) for r in responses]

        # Weighted probability
        weighted_prob = np.average(probabilities, weights=weights)

        # Probability standard deviation (disagreement)
        prob_std = float(np.std(probabilities))
        disagreement_score = min(prob_std / self.max_disagreement, 1.0)

        # Weighted confidence, penalized by disagreement
        raw_confidence = np.average(confidences, weights=weights)
        consensus_confidence = raw_confidence * (1 - disagreement_score * 0.5)

        # Vote counting
        votes = {
            TradeAction.BUY_YES: 0,
            TradeAction.BUY_NO: 0,
            TradeAction.HOLD: 0,
            TradeAction.SKIP: 0,
        }
        weighted_votes: dict[TradeAction, float] = {a: 0.0 for a in TradeAction}

        for response in responses:
            votes[response.action] += 1
            weight = self._get_agent_weight(response.agent_role)
            weighted_votes[response.action] += weight

        # Determine final action
        final_action = self._determine_action(
            weighted_prob=weighted_prob,
            consensus_confidence=consensus_confidence,
            disagreement_score=disagreement_score,
            weighted_votes=weighted_votes,
            current_price=context.yes_price,
        )

        # Calculate edge
        if final_action == TradeAction.BUY_YES:
            estimated_edge = weighted_prob - context.yes_price
        elif final_action == TradeAction.BUY_NO:
            estimated_edge = context.yes_price - weighted_prob
        else:
            estimated_edge = abs(weighted_prob - context.yes_price)

        # Aggregate reasoning
        bull_case = self._build_bull_case(responses)
        bear_case = self._build_bear_case(responses)
        key_risks = self._aggregate_risks(responses)

        # Calculate totals
        total_tokens = sum(r.tokens_used for r in responses)
        total_cost = sum(r.cost_usd for r in responses)
        total_latency = max(r.latency_ms for r in responses)  # Parallel, so max

        return EnsembleResult(
            event_ticker=context.event_ticker,
            market_ticker=context.market_ticker,
            market_title=context.market_title,
            current_yes_price=context.yes_price,
            final_action=final_action,
            weighted_probability=float(weighted_prob),
            consensus_confidence=float(consensus_confidence),
            probability_std=prob_std,
            disagreement_score=disagreement_score,
            votes_buy_yes=votes[TradeAction.BUY_YES],
            votes_buy_no=votes[TradeAction.BUY_NO],
            votes_hold=votes[TradeAction.HOLD],
            votes_skip=votes[TradeAction.SKIP],
            estimated_edge=estimated_edge,
            bull_case=bull_case,
            bear_case=bear_case,
            key_risks=key_risks,
            agent_responses=responses,
            total_tokens=total_tokens,
            total_cost_usd=total_cost,
            total_latency_ms=total_latency,
        )

    def _get_agent_weight(self, role: AgentRole) -> float:
        """Get weight for an agent role."""
        for agent in self.agents:
            if agent.role == role:
                return agent.weight
        return 0.0

    def _determine_action(
        self,
        weighted_prob: float,
        consensus_confidence: float,
        disagreement_score: float,
        weighted_votes: dict[TradeAction, float],
        current_price: float,
    ) -> TradeAction:
        """Determine final trading action."""
        # Too much disagreement -> SKIP
        if disagreement_score > 1.0:
            return TradeAction.SKIP

        # Not enough confidence -> HOLD
        if consensus_confidence < self.min_consensus_confidence:
            return TradeAction.HOLD

        # Check edge
        edge_yes = weighted_prob - current_price
        edge_no = current_price - weighted_prob

        # No meaningful edge -> HOLD
        if abs(edge_yes) < self.min_edge and abs(edge_no) < self.min_edge:
            return TradeAction.HOLD

        # Weighted vote majority
        if weighted_votes[TradeAction.BUY_YES] > weighted_votes[TradeAction.BUY_NO]:
            if edge_yes >= self.min_edge:
                return TradeAction.BUY_YES
        elif weighted_votes[TradeAction.BUY_NO] > weighted_votes[TradeAction.BUY_YES]:
            if edge_no >= self.min_edge:
                return TradeAction.BUY_NO

        # Edge-based decision if votes are close
        if edge_yes >= self.min_edge and edge_yes > edge_no:
            return TradeAction.BUY_YES
        elif edge_no >= self.min_edge and edge_no > edge_yes:
            return TradeAction.BUY_NO

        return TradeAction.HOLD

    def _build_bull_case(self, responses: list[AgentResponse]) -> str:
        """Build combined bullish reasoning."""
        parts = []

        # Get bull researcher's reasoning
        for r in responses:
            if r.agent_role == AgentRole.BULL_RESEARCHER and r.is_bullish:
                parts.append(r.reasoning)
                break

        # Add key factors from bullish agents
        factors = []
        for r in responses:
            if r.is_bullish:
                factors.extend(r.key_factors[:2])

        if factors:
            parts.append("Key factors: " + "; ".join(list(set(factors))[:4]))

        return " ".join(parts)[:500] if parts else ""

    def _build_bear_case(self, responses: list[AgentResponse]) -> str:
        """Build combined bearish reasoning."""
        parts = []

        # Get bear researcher's reasoning
        for r in responses:
            if r.agent_role == AgentRole.BEAR_RESEARCHER and r.is_bearish:
                parts.append(r.reasoning)
                break

        # Add key factors from bearish agents
        factors = []
        for r in responses:
            if r.is_bearish:
                factors.extend(r.key_factors[:2])

        if factors:
            parts.append("Key factors: " + "; ".join(list(set(factors))[:4]))

        return " ".join(parts)[:500] if parts else ""

    def _aggregate_risks(self, responses: list[AgentResponse]) -> list[str]:
        """Aggregate risks from all agents."""
        all_risks: list[str] = []

        # Prioritize risk manager's risks
        for r in responses:
            if r.agent_role == AgentRole.RISK_MANAGER:
                all_risks.extend(r.risks)
                break

        # Add unique risks from other agents
        seen = set(all_risks)
        for r in responses:
            for risk in r.risks:
                if risk not in seen:
                    all_risks.append(risk)
                    seen.add(risk)

        return all_risks[:5]
