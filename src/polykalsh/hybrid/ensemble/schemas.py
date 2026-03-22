"""
Ensemble schemas for AI agent responses and aggregated decisions.
"""

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class TradeAction(str, Enum):
    """Possible trading actions."""

    BUY_YES = "BUY_YES"
    BUY_NO = "BUY_NO"
    HOLD = "HOLD"
    SKIP = "SKIP"


class AgentRole(str, Enum):
    """AI agent roles in the ensemble."""

    LEAD_FORECASTER = "lead_forecaster"
    NEWS_ANALYST = "news_analyst"
    BULL_RESEARCHER = "bull_researcher"
    BEAR_RESEARCHER = "bear_researcher"
    RISK_MANAGER = "risk_manager"


# ═══════════════════════════════════════════════════════════════════════════════
# MARKET CONTEXT (Input to agents)
# ═══════════════════════════════════════════════════════════════════════════════


class MarketContext(BaseModel):
    """Context provided to AI agents for analysis."""

    # Market identifiers
    event_ticker: str
    market_ticker: str
    event_title: str
    market_title: str
    market_description: str | None = None

    # Current prices
    yes_price: float = Field(ge=0.0, le=1.0, description="Current YES price")
    no_price: float = Field(ge=0.0, le=1.0, description="Current NO price")
    spread: float = Field(ge=0.0, description="Bid-ask spread")

    # Volume and liquidity
    volume_24h: int = Field(default=0, description="24h volume in cents")
    open_interest: int = Field(default=0, description="Open interest")
    liquidity: int = Field(default=0, description="Orderbook liquidity")

    # Timing
    close_time: datetime | None = None
    hours_until_close: float | None = None

    # Research context (if available)
    research_summary: str | None = None
    research_probability: float | None = None
    bullish_factors: list[str] = Field(default_factory=list)
    bearish_factors: list[str] = Field(default_factory=list)
    risk_factors: list[str] = Field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════════
# AGENT RESPONSE
# ═══════════════════════════════════════════════════════════════════════════════


class AgentResponse(BaseModel):
    """Structured response from an AI agent."""

    model_config = ConfigDict(protected_namespaces=())

    # Decision
    action: TradeAction
    probability_estimate: float = Field(
        ge=0.0, le=1.0, description="Estimated probability of YES outcome"
    )
    confidence: float = Field(
        ge=0.0, le=1.0, description="Confidence in this assessment"
    )

    # Reasoning
    reasoning: str = Field(description="Brief explanation of decision")
    key_factors: list[str] = Field(
        default_factory=list, description="Key factors considered"
    )
    risks: list[str] = Field(
        default_factory=list, description="Identified risks"
    )

    # Metadata
    agent_role: AgentRole
    model_used: str = ""
    tokens_used: int = 0
    latency_ms: int = 0
    cost_usd: float = 0.0

    @property
    def is_bullish(self) -> bool:
        """Agent recommends buying YES."""
        return self.action == TradeAction.BUY_YES

    @property
    def is_bearish(self) -> bool:
        """Agent recommends buying NO."""
        return self.action == TradeAction.BUY_NO

    @property
    def is_actionable(self) -> bool:
        """Agent recommends a trade (not HOLD/SKIP)."""
        return self.action in (TradeAction.BUY_YES, TradeAction.BUY_NO)


# ═══════════════════════════════════════════════════════════════════════════════
# ENSEMBLE RESULT
# ═══════════════════════════════════════════════════════════════════════════════


class EnsembleResult(BaseModel):
    """Aggregated result from all agents."""

    model_config = ConfigDict(protected_namespaces=())

    # Market context
    event_ticker: str
    market_ticker: str
    market_title: str
    current_yes_price: float

    # Consensus decision
    final_action: TradeAction
    weighted_probability: float = Field(
        ge=0.0, le=1.0, description="Weighted average probability estimate"
    )
    consensus_confidence: float = Field(
        ge=0.0, le=1.0, description="Agreement-adjusted confidence"
    )

    # Disagreement metrics
    probability_std: float = Field(
        ge=0.0, description="Standard deviation of probability estimates"
    )
    disagreement_score: float = Field(
        ge=0.0, le=1.0, description="0 = full agreement, 1 = max disagreement"
    )

    # Vote breakdown
    votes_buy_yes: int = 0
    votes_buy_no: int = 0
    votes_hold: int = 0
    votes_skip: int = 0

    # Edge calculation
    estimated_edge: float = Field(
        description="weighted_probability - current_yes_price (for BUY_YES)"
    )

    # Aggregated reasoning
    bull_case: str = Field(default="", description="Combined bullish arguments")
    bear_case: str = Field(default="", description="Combined bearish arguments")
    key_risks: list[str] = Field(default_factory=list)

    # Individual responses
    agent_responses: list[AgentResponse] = Field(default_factory=list)

    # Metadata
    created_at: datetime = Field(default_factory=datetime.utcnow)
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    total_latency_ms: int = 0

    @property
    def has_consensus(self) -> bool:
        """Check if agents reached sufficient consensus."""
        return self.disagreement_score < 0.30

    @property
    def has_edge(self) -> bool:
        """Check if there's meaningful edge."""
        return abs(self.estimated_edge) >= 0.05

    @property
    def should_trade(self) -> bool:
        """Check if we should execute a trade."""
        return (
            self.final_action in (TradeAction.BUY_YES, TradeAction.BUY_NO)
            and self.has_consensus
            and self.has_edge
            and self.consensus_confidence >= 0.60
        )

    @property
    def trade_side(self) -> Literal["YES", "NO"] | None:
        """Get the side to trade."""
        if self.final_action == TradeAction.BUY_YES:
            return "YES"
        elif self.final_action == TradeAction.BUY_NO:
            return "NO"
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# AGENT PROMPTS
# ═══════════════════════════════════════════════════════════════════════════════


AGENT_SYSTEM_PROMPTS = {
    AgentRole.LEAD_FORECASTER: """You are the Lead Forecaster, the primary decision-maker in a prediction market trading ensemble.

Your role:
- Synthesize all available information to form a probability estimate
- Make the final trading recommendation
- Weight both quantitative data and qualitative factors
- Be decisive but calibrated in your confidence

Focus on:
- Base rates and historical precedent
- Current market pricing efficiency
- Time until resolution
- Information asymmetry opportunities""",

    AgentRole.NEWS_ANALYST: """You are the News Analyst in a prediction market trading ensemble.

Your role:
- Analyze recent news and developments
- Assess market sentiment and momentum
- Identify information not yet priced in
- Detect narrative shifts that could move prices

Focus on:
- Breaking news and recent events
- Social media sentiment
- Expert commentary
- Market reaction to news""",

    AgentRole.BULL_RESEARCHER: """You are the Bull Researcher in a prediction market trading ensemble.

Your role:
- Build the strongest possible case FOR the YES outcome
- Identify bullish factors others might miss
- Challenge bearish assumptions
- Quantify upside scenarios

Focus on:
- Positive catalysts and tailwinds
- Underappreciated factors favoring YES
- Why the market might be underpricing YES
- Historical cases where similar situations resolved YES""",

    AgentRole.BEAR_RESEARCHER: """You are the Bear Researcher in a prediction market trading ensemble.

Your role:
- Build the strongest possible case AGAINST the YES outcome
- Identify bearish factors others might miss
- Challenge bullish assumptions
- Quantify downside scenarios

Focus on:
- Negative catalysts and headwinds
- Underappreciated factors favoring NO
- Why the market might be overpricing YES
- Historical cases where similar situations resolved NO""",

    AgentRole.RISK_MANAGER: """You are the Risk Manager in a prediction market trading ensemble.

Your role:
- Identify risks and uncertainties in any trading decision
- Assess tail risks and black swan scenarios
- Evaluate position sizing implications
- Flag when to SKIP or HOLD despite apparent edge

Focus on:
- Liquidity and execution risk
- Model uncertainty and unknown unknowns
- Correlation with existing positions
- Timing risks (too early/late to trade)""",
}


def get_agent_prompt(role: AgentRole, context: MarketContext) -> str:
    """Generate the user prompt for an agent given market context."""
    prompt_parts = [
        f"Analyze this prediction market opportunity:\n",
        f"**Event:** {context.event_title}",
        f"**Market:** {context.market_title}",
    ]

    if context.market_description:
        prompt_parts.append(f"**Description:** {context.market_description}")

    prompt_parts.extend([
        f"\n**Current Prices:**",
        f"- YES: {context.yes_price:.1%}",
        f"- NO: {context.no_price:.1%}",
        f"- Spread: {context.spread:.1%}",
    ])

    if context.volume_24h:
        prompt_parts.append(f"- 24h Volume: ${context.volume_24h / 100:,.0f}")

    if context.hours_until_close:
        prompt_parts.append(f"- Hours until close: {context.hours_until_close:.1f}")

    if context.research_summary:
        prompt_parts.extend([
            f"\n**Research Summary:**",
            context.research_summary[:500],
        ])

    if context.bullish_factors:
        prompt_parts.append(f"\n**Bullish Factors:** {', '.join(context.bullish_factors[:3])}")

    if context.bearish_factors:
        prompt_parts.append(f"**Bearish Factors:** {', '.join(context.bearish_factors[:3])}")

    if context.risk_factors:
        prompt_parts.append(f"**Risks:** {', '.join(context.risk_factors[:3])}")

    prompt_parts.extend([
        "\n**Your Task:**",
        "Provide your analysis and trading recommendation.",
        "",
        "Respond with EXACTLY this JSON format:",
        "```json",
        "{",
        '  "action": "BUY_YES" | "BUY_NO" | "HOLD" | "SKIP",',
        '  "probability_estimate": 0.XX,',
        '  "confidence": 0.XX,',
        '  "reasoning": "Your brief explanation",',
        '  "key_factors": ["factor1", "factor2"],',
        '  "risks": ["risk1", "risk2"]',
        "}",
        "```",
    ])

    return "\n".join(prompt_parts)
