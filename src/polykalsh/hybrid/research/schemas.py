"""
Research schemas for deep research pipeline.

Defines structured outputs from Perplexity research.
"""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class ResearchType(str, Enum):
    """Type of research query."""

    EVENT_ANALYSIS = "event_analysis"
    MARKET_DEEP_DIVE = "market_deep_dive"
    NEWS_SENTIMENT = "news_sentiment"
    PROBABILITY_ESTIMATE = "probability_estimate"


class SourceCredibility(str, Enum):
    """Source credibility rating."""

    HIGH = "high"  # Official sources, major news outlets
    MEDIUM = "medium"  # Established blogs, industry publications
    LOW = "low"  # Social media, unverified sources
    UNKNOWN = "unknown"


class Source(BaseModel):
    """A source used in research."""

    title: str
    url: str | None = None
    snippet: str | None = None
    credibility: SourceCredibility = SourceCredibility.UNKNOWN
    published_date: datetime | None = None


class KeyFactor(BaseModel):
    """A key factor affecting the outcome."""

    factor: str = Field(description="Description of the factor")
    impact: str = Field(description="Positive, negative, or neutral impact")
    confidence: float = Field(
        ge=0.0, le=1.0, description="Confidence in this factor's relevance"
    )
    sources: list[str] = Field(default_factory=list, description="Source references")


class ProbabilityEstimate(BaseModel):
    """A probability estimate with reasoning."""

    outcome: str = Field(description="The outcome being estimated (e.g., 'YES', 'NO')")
    probability: float = Field(ge=0.0, le=1.0, description="Estimated probability")
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence in estimate")
    reasoning: str = Field(description="Brief reasoning for this estimate")


class RiskFactor(BaseModel):
    """A risk factor to consider."""

    risk: str = Field(description="Description of the risk")
    severity: str = Field(description="low, medium, high, critical")
    likelihood: float = Field(ge=0.0, le=1.0, description="Likelihood of occurrence")
    mitigation: str | None = Field(default=None, description="Possible mitigation")


class ResearchResult(BaseModel):
    """
    Structured result from deep research.

    Contains comprehensive analysis of an event/market.
    """

    model_config = ConfigDict(protected_namespaces=())

    # Identifiers
    event_ticker: str
    market_ticker: str | None = None
    research_type: ResearchType

    # Core analysis
    title: str = Field(description="Event/market title")
    summary: str = Field(description="Executive summary of findings")
    narrative: str = Field(description="Detailed narrative analysis")

    # Probability estimates
    probability_estimates: list[ProbabilityEstimate] = Field(default_factory=list)

    # Key factors
    bullish_factors: list[KeyFactor] = Field(
        default_factory=list, description="Factors favoring YES outcome"
    )
    bearish_factors: list[KeyFactor] = Field(
        default_factory=list, description="Factors favoring NO outcome"
    )

    # Risk assessment
    risk_factors: list[RiskFactor] = Field(default_factory=list)

    # Timeline
    key_dates: list[str] = Field(
        default_factory=list, description="Important upcoming dates"
    )
    time_sensitivity: str = Field(
        default="medium", description="low, medium, high - how time-sensitive is this"
    )

    # Sources
    sources: list[Source] = Field(default_factory=list)
    source_count: int = Field(default=0)

    # Metadata
    researched_at: datetime = Field(default_factory=datetime.utcnow)
    model_used: str = Field(default="")
    tokens_used: int = Field(default=0)
    cost_usd: float = Field(default=0.0)

    # Quality indicators
    data_freshness: str = Field(
        default="unknown", description="How recent is the data: hours, days, weeks"
    )
    consensus_strength: str = Field(
        default="unknown", description="weak, moderate, strong - agreement among sources"
    )

    @property
    def primary_probability(self) -> float | None:
        """Get the primary YES probability estimate."""
        for est in self.probability_estimates:
            if est.outcome.upper() in ("YES", "TRUE", "POSITIVE"):
                return est.probability
        return None

    @property
    def avg_confidence(self) -> float:
        """Average confidence across probability estimates."""
        if not self.probability_estimates:
            return 0.0
        return sum(e.confidence for e in self.probability_estimates) / len(
            self.probability_estimates
        )

    @property
    def risk_score(self) -> float:
        """Aggregate risk score (0-1)."""
        if not self.risk_factors:
            return 0.0

        severity_weights = {"low": 0.25, "medium": 0.5, "high": 0.75, "critical": 1.0}
        total = sum(
            severity_weights.get(r.severity, 0.5) * r.likelihood
            for r in self.risk_factors
        )
        return min(total / len(self.risk_factors), 1.0)

    def is_stale(self, max_age_hours: float = 6.0) -> bool:
        """Check if research is stale."""
        age = datetime.utcnow() - self.researched_at
        return age.total_seconds() > max_age_hours * 3600


class ResearchQuery(BaseModel):
    """Query for research."""

    event_ticker: str
    market_ticker: str | None = None
    event_title: str
    market_title: str | None = None
    market_description: str | None = None
    current_yes_price: float | None = None
    close_time: datetime | None = None
    research_type: ResearchType = ResearchType.EVENT_ANALYSIS

    def to_prompt(self) -> str:
        """Generate research prompt for Perplexity."""
        parts = [
            f"Research the following prediction market event:\n",
            f"**Event:** {self.event_title}",
        ]

        if self.market_title:
            parts.append(f"**Market:** {self.market_title}")

        if self.market_description:
            parts.append(f"**Description:** {self.market_description}")

        if self.current_yes_price is not None:
            parts.append(f"**Current YES price:** {self.current_yes_price:.0%}")

        if self.close_time:
            parts.append(f"**Closes:** {self.close_time.strftime('%Y-%m-%d %H:%M UTC')}")

        parts.append("\n**Please provide:**")
        parts.append("1. A summary of the current situation")
        parts.append("2. Key factors that could influence the outcome (bullish and bearish)")
        parts.append("3. Your probability estimate for the YES outcome with reasoning")
        parts.append("4. Key risks and uncertainties")
        parts.append("5. Important upcoming dates or events")
        parts.append("6. Assessment of data freshness and source consensus")

        return "\n".join(parts)
