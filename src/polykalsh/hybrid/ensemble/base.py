"""
Base agent interface for AI ensemble.
"""

import json
import re
import time
from abc import ABC, abstractmethod
from typing import Any

import structlog

from polykalsh.hybrid.ensemble.schemas import (
    AgentResponse,
    AgentRole,
    MarketContext,
    TradeAction,
    AGENT_SYSTEM_PROMPTS,
    get_agent_prompt,
)

logger = structlog.get_logger()


class AgentError(Exception):
    """Base exception for agent errors."""

    pass


class BaseAgent(ABC):
    """
    Abstract base class for AI agents.

    Each agent analyzes market context and returns a structured response.
    """

    def __init__(
        self,
        role: AgentRole,
        weight: float,
        temperature: float = 0.0,
        max_retries: int = 2,
    ):
        """
        Initialize agent.

        Args:
            role: Agent's role in the ensemble
            weight: Voting weight (0-1)
            temperature: LLM temperature
            max_retries: Max retry attempts on failure
        """
        self.role = role
        self.weight = weight
        self.temperature = temperature
        self.max_retries = max_retries
        self.system_prompt = AGENT_SYSTEM_PROMPTS[role]

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Return the model name/identifier."""
        pass

    @abstractmethod
    async def _call_llm(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> tuple[str, int, float]:
        """
        Call the LLM and return response.

        Args:
            system_prompt: System message
            user_prompt: User message

        Returns:
            Tuple of (response_text, tokens_used, cost_usd)
        """
        pass

    async def analyze(self, context: MarketContext) -> AgentResponse:
        """
        Analyze market context and return trading recommendation.

        Args:
            context: Market context with prices, research, etc.

        Returns:
            Structured agent response
        """
        user_prompt = get_agent_prompt(self.role, context)
        start_time = time.time()

        last_error: Exception | None = None

        for attempt in range(self.max_retries + 1):
            try:
                response_text, tokens, cost = await self._call_llm(
                    self.system_prompt,
                    user_prompt,
                )

                latency_ms = int((time.time() - start_time) * 1000)

                # Parse the response
                parsed = self._parse_response(response_text)

                return AgentResponse(
                    action=parsed["action"],
                    probability_estimate=parsed["probability_estimate"],
                    confidence=parsed["confidence"],
                    reasoning=parsed["reasoning"],
                    key_factors=parsed.get("key_factors", []),
                    risks=parsed.get("risks", []),
                    agent_role=self.role,
                    model_used=self.model_name,
                    tokens_used=tokens,
                    latency_ms=latency_ms,
                    cost_usd=cost,
                )

            except json.JSONDecodeError as e:
                last_error = e
                logger.warning(
                    "agent_parse_error",
                    role=self.role.value,
                    attempt=attempt,
                    error=str(e),
                )

            except Exception as e:
                last_error = e
                logger.warning(
                    "agent_error",
                    role=self.role.value,
                    attempt=attempt,
                    error=str(e),
                )

        # Return a SKIP response on failure
        logger.error(
            "agent_failed",
            role=self.role.value,
            error=str(last_error),
        )

        return AgentResponse(
            action=TradeAction.SKIP,
            probability_estimate=0.5,
            confidence=0.0,
            reasoning=f"Agent failed: {last_error}",
            key_factors=[],
            risks=["Agent failure"],
            agent_role=self.role,
            model_used=self.model_name,
            tokens_used=0,
            latency_ms=int((time.time() - start_time) * 1000),
            cost_usd=0.0,
        )

    def _parse_response(self, response_text: str) -> dict[str, Any]:
        """
        Parse agent response into structured format.

        Handles various response formats including markdown code blocks.
        """
        # Try to extract JSON from markdown code block
        json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", response_text, re.DOTALL)

        if json_match:
            json_str = json_match.group(1)
        else:
            # Try to find raw JSON object
            json_match = re.search(r"\{[^{}]*\}", response_text, re.DOTALL)
            if json_match:
                json_str = json_match.group(0)
            else:
                raise json.JSONDecodeError("No JSON found in response", response_text, 0)

        # Parse JSON
        data = json.loads(json_str)

        # Validate and normalize
        action_str = data.get("action", "SKIP").upper()
        action = TradeAction(action_str) if action_str in [a.value for a in TradeAction] else TradeAction.SKIP

        probability = float(data.get("probability_estimate", 0.5))
        probability = max(0.0, min(1.0, probability))

        confidence = float(data.get("confidence", 0.5))
        confidence = max(0.0, min(1.0, confidence))

        return {
            "action": action,
            "probability_estimate": probability,
            "confidence": confidence,
            "reasoning": str(data.get("reasoning", ""))[:500],
            "key_factors": data.get("key_factors", [])[:5],
            "risks": data.get("risks", [])[:5],
        }

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(role={self.role.value}, weight={self.weight})"
