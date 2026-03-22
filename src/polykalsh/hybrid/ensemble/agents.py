"""
AI agent implementations for the ensemble.

Agents:
- Lead Forecaster (Anthropic Claude) - 30% weight
- News Analyst (OpenRouter) - 20% weight
- Bull Researcher (OpenRouter) - 20% weight
- Bear Researcher (OpenRouter) - 15% weight
- Risk Manager (Anthropic Claude) - 15% weight
"""

from typing import Any

import httpx
import structlog

from polykalsh.hybrid.ensemble.base import BaseAgent, AgentError
from polykalsh.hybrid.ensemble.schemas import AgentRole

logger = structlog.get_logger()


# ═══════════════════════════════════════════════════════════════════════════════
# ANTHROPIC AGENT (Claude)
# ═══════════════════════════════════════════════════════════════════════════════


class AnthropicAgent(BaseAgent):
    """
    Agent using Anthropic's Claude API.

    Used for Lead Forecaster and Risk Manager roles.
    """

    # Pricing per 1M tokens (Claude 3.5 Sonnet)
    INPUT_COST_PER_M = 3.0
    OUTPUT_COST_PER_M = 15.0

    def __init__(
        self,
        role: AgentRole,
        weight: float,
        api_key: str,
        model: str = "claude-sonnet-4-20250514",
        temperature: float = 0.0,
        max_tokens: int = 1024,
        timeout: float = 60.0,
    ):
        super().__init__(role=role, weight=weight, temperature=temperature)
        self.api_key = api_key
        self.model = model
        self.max_tokens = max_tokens
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    @property
    def model_name(self) -> str:
        return f"anthropic/{self.model}"

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout),
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
            )
        return self._client

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _call_llm(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> tuple[str, int, float]:
        client = await self._ensure_client()

        payload = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "system": system_prompt,
            "messages": [
                {"role": "user", "content": user_prompt},
            ],
        }

        response = await client.post(
            "https://api.anthropic.com/v1/messages",
            json=payload,
        )

        if response.status_code != 200:
            raise AgentError(f"Anthropic API error {response.status_code}: {response.text}")

        data = response.json()

        # Extract response text
        content = data.get("content", [])
        response_text = ""
        for block in content:
            if block.get("type") == "text":
                response_text += block.get("text", "")

        # Calculate tokens and cost
        usage = data.get("usage", {})
        input_tokens = usage.get("input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)
        total_tokens = input_tokens + output_tokens

        cost = (input_tokens / 1_000_000 * self.INPUT_COST_PER_M +
                output_tokens / 1_000_000 * self.OUTPUT_COST_PER_M)

        return response_text, total_tokens, cost


# ═══════════════════════════════════════════════════════════════════════════════
# OPENROUTER AGENT
# ═══════════════════════════════════════════════════════════════════════════════


class OpenRouterAgent(BaseAgent):
    """
    Agent using OpenRouter API (OpenAI-compatible).

    Used for News Analyst, Bull Researcher, and Bear Researcher roles.
    """

    # Default pricing (varies by model)
    INPUT_COST_PER_M = 3.0
    OUTPUT_COST_PER_M = 15.0

    def __init__(
        self,
        role: AgentRole,
        weight: float,
        api_key: str,
        model: str = "anthropic/claude-sonnet-4-20250514",
        temperature: float = 0.0,
        max_tokens: int = 1024,
        timeout: float = 60.0,
    ):
        super().__init__(role=role, weight=weight, temperature=temperature)
        self.api_key = api_key
        self.model = model
        self.max_tokens = max_tokens
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    @property
    def model_name(self) -> str:
        return f"openrouter/{self.model}"

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout),
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://github.com/polykalsh",
                    "X-Title": "Polykalsh Trading Bot",
                },
            )
        return self._client

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _call_llm(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> tuple[str, int, float]:
        client = await self._ensure_client()

        payload = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }

        response = await client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            json=payload,
        )

        if response.status_code != 200:
            raise AgentError(f"OpenRouter API error {response.status_code}: {response.text}")

        data = response.json()

        # Extract response text (OpenAI format)
        choices = data.get("choices", [])
        if not choices:
            raise AgentError("No response from OpenRouter")

        response_text = choices[0].get("message", {}).get("content", "")

        # Calculate tokens and cost
        usage = data.get("usage", {})
        input_tokens = usage.get("prompt_tokens", 0)
        output_tokens = usage.get("completion_tokens", 0)
        total_tokens = input_tokens + output_tokens

        cost = (input_tokens / 1_000_000 * self.INPUT_COST_PER_M +
                output_tokens / 1_000_000 * self.OUTPUT_COST_PER_M)

        return response_text, total_tokens, cost


# ═══════════════════════════════════════════════════════════════════════════════
# MOCK AGENT (for testing)
# ═══════════════════════════════════════════════════════════════════════════════


class MockAgent(BaseAgent):
    """
    Mock agent for testing without API calls.

    Returns deterministic responses based on role.
    """

    def __init__(
        self,
        role: AgentRole,
        weight: float,
        mock_probability: float = 0.55,
        mock_confidence: float = 0.70,
        mock_action: str = "BUY_YES",
    ):
        super().__init__(role=role, weight=weight)
        self.mock_probability = mock_probability
        self.mock_confidence = mock_confidence
        self.mock_action = mock_action

    @property
    def model_name(self) -> str:
        return "mock/test"

    async def _call_llm(
        self,
        system_prompt: str,
        user_prompt: str,
    ) -> tuple[str, int, float]:
        import json

        # Vary response slightly by role
        prob = self.mock_probability
        conf = self.mock_confidence
        action = self.mock_action

        if self.role == AgentRole.BULL_RESEARCHER:
            prob = min(prob + 0.10, 0.95)
            action = "BUY_YES"
        elif self.role == AgentRole.BEAR_RESEARCHER:
            prob = max(prob - 0.10, 0.05)
            action = "BUY_NO"
        elif self.role == AgentRole.RISK_MANAGER:
            conf = max(conf - 0.15, 0.3)

        response = {
            "action": action,
            "probability_estimate": prob,
            "confidence": conf,
            "reasoning": f"Mock {self.role.value} analysis",
            "key_factors": [f"Mock factor from {self.role.value}"],
            "risks": [f"Mock risk from {self.role.value}"],
        }

        return json.dumps(response), 100, 0.001


# ═══════════════════════════════════════════════════════════════════════════════
# AGENT FACTORY
# ═══════════════════════════════════════════════════════════════════════════════


def create_ensemble(
    anthropic_api_key: str | None = None,
    openrouter_api_key: str | None = None,
    anthropic_model: str = "claude-sonnet-4-20250514",
    openrouter_model: str = "anthropic/claude-sonnet-4-20250514",
    temperature: float = 0.0,
    use_mock: bool = False,
) -> list[BaseAgent]:
    """
    Create the full ensemble of 5 agents.

    Args:
        anthropic_api_key: Anthropic API key (for Lead Forecaster, Risk Manager)
        openrouter_api_key: OpenRouter API key (for News, Bull, Bear)
        anthropic_model: Model to use for Anthropic agents
        openrouter_model: Model to use for OpenRouter agents
        temperature: LLM temperature
        use_mock: Use mock agents for testing

    Returns:
        List of 5 configured agents
    """
    if use_mock:
        return [
            MockAgent(AgentRole.LEAD_FORECASTER, weight=0.30),
            MockAgent(AgentRole.NEWS_ANALYST, weight=0.20),
            MockAgent(AgentRole.BULL_RESEARCHER, weight=0.20),
            MockAgent(AgentRole.BEAR_RESEARCHER, weight=0.15),
            MockAgent(AgentRole.RISK_MANAGER, weight=0.15),
        ]

    agents: list[BaseAgent] = []

    # Lead Forecaster (Anthropic) - 30%
    if anthropic_api_key:
        agents.append(
            AnthropicAgent(
                role=AgentRole.LEAD_FORECASTER,
                weight=0.30,
                api_key=anthropic_api_key,
                model=anthropic_model,
                temperature=temperature,
            )
        )

    # News Analyst (OpenRouter) - 20%
    if openrouter_api_key:
        agents.append(
            OpenRouterAgent(
                role=AgentRole.NEWS_ANALYST,
                weight=0.20,
                api_key=openrouter_api_key,
                model=openrouter_model,
                temperature=temperature,
            )
        )

    # Bull Researcher (OpenRouter) - 20%
    if openrouter_api_key:
        agents.append(
            OpenRouterAgent(
                role=AgentRole.BULL_RESEARCHER,
                weight=0.20,
                api_key=openrouter_api_key,
                model=openrouter_model,
                temperature=temperature,
            )
        )

    # Bear Researcher (OpenRouter) - 15%
    if openrouter_api_key:
        agents.append(
            OpenRouterAgent(
                role=AgentRole.BEAR_RESEARCHER,
                weight=0.15,
                api_key=openrouter_api_key,
                model=openrouter_model,
                temperature=temperature,
            )
        )

    # Risk Manager (Anthropic) - 15%
    if anthropic_api_key:
        agents.append(
            AnthropicAgent(
                role=AgentRole.RISK_MANAGER,
                weight=0.15,
                api_key=anthropic_api_key,
                model=anthropic_model,
                temperature=temperature,
            )
        )

    if not agents:
        raise ValueError("At least one API key (anthropic or openrouter) must be provided")

    # Normalize weights if not all agents present
    total_weight = sum(a.weight for a in agents)
    if total_weight != 1.0:
        for agent in agents:
            agent.weight = agent.weight / total_weight

    return agents


async def close_ensemble(agents: list[BaseAgent]) -> None:
    """Close all agent clients."""
    for agent in agents:
        if hasattr(agent, "close"):
            await agent.close()
