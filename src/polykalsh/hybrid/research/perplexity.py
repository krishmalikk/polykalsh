"""
Perplexity API client for deep research.

Uses Perplexity's online models for real-time research with citations.
"""

import json
import re
from datetime import datetime
from typing import Any

import httpx
import structlog

from polykalsh.hybrid.research.schemas import (
    KeyFactor,
    ProbabilityEstimate,
    ResearchQuery,
    ResearchResult,
    ResearchType,
    RiskFactor,
    Source,
    SourceCredibility,
)

logger = structlog.get_logger()


class PerplexityError(Exception):
    """Perplexity API error."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class PerplexityClient:
    """
    Async Perplexity API client.

    Uses sonar models for online research with citations.
    """

    BASE_URL = "https://api.perplexity.ai"

    # Pricing per 1M tokens (approximate)
    PRICING = {
        "llama-3.1-sonar-small-128k-online": {"input": 0.2, "output": 0.2},
        "llama-3.1-sonar-large-128k-online": {"input": 1.0, "output": 1.0},
        "llama-3.1-sonar-huge-128k-online": {"input": 5.0, "output": 5.0},
    }

    def __init__(
        self,
        api_key: str,
        model: str = "llama-3.1-sonar-large-128k-online",
        timeout: float = 60.0,
        max_retries: int = 3,
    ):
        """
        Initialize Perplexity client.

        Args:
            api_key: Perplexity API key
            model: Model to use (sonar-small, sonar-large, sonar-huge)
            timeout: Request timeout in seconds
            max_retries: Max retry attempts
        """
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "PerplexityClient":
        """Async context manager entry."""
        await self._ensure_client()
        return self

    async def __aexit__(self, *args: Any) -> None:
        """Async context manager exit."""
        await self.close()

    async def _ensure_client(self) -> None:
        """Ensure HTTP client is initialized."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout),
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
            )

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    def _estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        """Estimate cost in USD."""
        pricing = self.PRICING.get(
            self.model, {"input": 1.0, "output": 1.0}
        )
        input_cost = (input_tokens / 1_000_000) * pricing["input"]
        output_cost = (output_tokens / 1_000_000) * pricing["output"]
        return input_cost + output_cost

    async def _request(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.0,
    ) -> dict[str, Any]:
        """
        Make a chat completion request.

        Args:
            messages: Chat messages
            temperature: Sampling temperature

        Returns:
            API response

        Raises:
            PerplexityError: On API errors
        """
        await self._ensure_client()

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "return_citations": True,
            "return_related_questions": False,
        }

        last_error: Exception | None = None

        for attempt in range(self.max_retries):
            try:
                response = await self._client.post(
                    f"{self.BASE_URL}/chat/completions",
                    json=payload,
                )

                if response.status_code == 429:
                    wait_time = 2 ** attempt
                    logger.warning(
                        "perplexity_rate_limited",
                        attempt=attempt,
                        wait_time=wait_time,
                    )
                    import asyncio
                    await asyncio.sleep(wait_time)
                    continue

                if response.status_code >= 400:
                    raise PerplexityError(
                        f"API error {response.status_code}: {response.text}",
                        status_code=response.status_code,
                    )

                return response.json()

            except httpx.TimeoutException as e:
                last_error = e
                logger.warning(
                    "perplexity_timeout",
                    attempt=attempt,
                )
                import asyncio
                await asyncio.sleep(2 ** attempt)

            except httpx.RequestError as e:
                last_error = e
                logger.warning(
                    "perplexity_request_error",
                    attempt=attempt,
                    error=str(e),
                )
                import asyncio
                await asyncio.sleep(2 ** attempt)

        raise PerplexityError(f"Request failed after {self.max_retries} retries: {last_error}")

    async def research(self, query: ResearchQuery) -> ResearchResult:
        """
        Perform deep research on an event/market.

        Args:
            query: Research query with event/market details

        Returns:
            Structured research result
        """
        # Build the research prompt
        system_prompt = self._build_system_prompt()
        user_prompt = query.to_prompt()

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        logger.info(
            "perplexity_research_start",
            event_ticker=query.event_ticker,
            market_ticker=query.market_ticker,
        )

        # Make the request
        response = await self._request(messages, temperature=0.0)

        # Parse the response
        content = response["choices"][0]["message"]["content"]
        citations = response.get("citations", [])

        # Extract token usage
        usage = response.get("usage", {})
        input_tokens = usage.get("prompt_tokens", 0)
        output_tokens = usage.get("completion_tokens", 0)
        total_tokens = input_tokens + output_tokens
        cost = self._estimate_cost(input_tokens, output_tokens)

        # Parse structured data from response
        result = self._parse_research_response(
            content=content,
            citations=citations,
            query=query,
        )

        # Add metadata
        result.model_used = self.model
        result.tokens_used = total_tokens
        result.cost_usd = cost

        logger.info(
            "perplexity_research_complete",
            event_ticker=query.event_ticker,
            tokens=total_tokens,
            cost_usd=cost,
            sources=len(result.sources),
        )

        return result

    def _build_system_prompt(self) -> str:
        """Build system prompt for research."""
        return """You are an expert prediction market analyst. Your task is to research events and provide structured analysis for trading decisions.

For each research query, provide:

1. **SUMMARY**: A 2-3 sentence executive summary of the current situation.

2. **NARRATIVE**: A detailed analysis (2-3 paragraphs) covering:
   - Current state of affairs
   - Recent developments
   - Historical context if relevant

3. **PROBABILITY ESTIMATE**: Your estimate for the YES outcome.
   Format: PROBABILITY: X% (CONFIDENCE: Y%)
   Include brief reasoning.

4. **BULLISH FACTORS** (favoring YES):
   - Factor 1: [description] (Impact: positive, Confidence: X%)
   - Factor 2: ...

5. **BEARISH FACTORS** (favoring NO):
   - Factor 1: [description] (Impact: negative, Confidence: X%)
   - Factor 2: ...

6. **RISKS**:
   - Risk 1: [description] (Severity: low/medium/high/critical, Likelihood: X%)
   - Risk 2: ...

7. **KEY DATES**: List any important upcoming dates.

8. **DATA ASSESSMENT**:
   - Data Freshness: hours/days/weeks
   - Consensus: weak/moderate/strong
   - Time Sensitivity: low/medium/high

Be objective and cite specific sources. Focus on actionable insights for trading."""

    def _parse_research_response(
        self,
        content: str,
        citations: list[str],
        query: ResearchQuery,
    ) -> ResearchResult:
        """Parse unstructured response into ResearchResult."""
        # Extract sections using regex patterns
        summary = self._extract_section(content, "SUMMARY", "NARRATIVE") or ""
        narrative = self._extract_section(content, "NARRATIVE", "PROBABILITY") or ""

        # Parse probability estimate
        prob_estimates = self._parse_probability(content)

        # Parse factors
        bullish = self._parse_factors(content, "BULLISH")
        bearish = self._parse_factors(content, "BEARISH")

        # Parse risks
        risks = self._parse_risks(content)

        # Parse key dates
        key_dates = self._parse_key_dates(content)

        # Parse data assessment
        freshness = self._extract_value(content, "Data Freshness") or "unknown"
        consensus = self._extract_value(content, "Consensus") or "unknown"
        time_sensitivity = self._extract_value(content, "Time Sensitivity") or "medium"

        # Build sources from citations
        sources = [
            Source(title=f"Source {i+1}", url=url, credibility=SourceCredibility.UNKNOWN)
            for i, url in enumerate(citations)
        ]

        return ResearchResult(
            event_ticker=query.event_ticker,
            market_ticker=query.market_ticker,
            research_type=query.research_type,
            title=query.market_title or query.event_title,
            summary=summary.strip() if summary else content[:500],
            narrative=narrative.strip() if narrative else content,
            probability_estimates=prob_estimates,
            bullish_factors=bullish,
            bearish_factors=bearish,
            risk_factors=risks,
            key_dates=key_dates,
            time_sensitivity=time_sensitivity.lower(),
            sources=sources,
            source_count=len(sources),
            data_freshness=freshness.lower(),
            consensus_strength=consensus.lower(),
        )

    def _extract_section(
        self, content: str, start_marker: str, end_marker: str
    ) -> str | None:
        """Extract text between two section markers."""
        pattern = rf"\*?\*?{start_marker}\*?\*?:?\s*(.*?)(?=\*?\*?{end_marker}|\Z)"
        match = re.search(pattern, content, re.DOTALL | re.IGNORECASE)
        if match:
            return match.group(1).strip()
        return None

    def _extract_value(self, content: str, key: str) -> str | None:
        """Extract a single value after a key."""
        pattern = rf"{key}:?\s*(\w+)"
        match = re.search(pattern, content, re.IGNORECASE)
        if match:
            return match.group(1)
        return None

    def _parse_probability(self, content: str) -> list[ProbabilityEstimate]:
        """Parse probability estimates from content."""
        estimates = []

        # Look for patterns like "PROBABILITY: 65% (CONFIDENCE: 70%)"
        pattern = r"PROBABILITY:?\s*(\d+)%?\s*\(?CONFIDENCE:?\s*(\d+)%?\)?"
        match = re.search(pattern, content, re.IGNORECASE)

        if match:
            prob = int(match.group(1)) / 100
            conf = int(match.group(2)) / 100

            # Extract reasoning (text after the match until next section)
            reasoning_start = match.end()
            reasoning_text = content[reasoning_start:reasoning_start + 500]
            reasoning = reasoning_text.split("\n")[0].strip()

            estimates.append(
                ProbabilityEstimate(
                    outcome="YES",
                    probability=prob,
                    confidence=conf,
                    reasoning=reasoning or "Based on current evidence",
                )
            )
            estimates.append(
                ProbabilityEstimate(
                    outcome="NO",
                    probability=1 - prob,
                    confidence=conf,
                    reasoning="Inverse of YES probability",
                )
            )
        else:
            # Fallback: look for any percentage that might be a probability
            pct_pattern = r"(\d{1,2})%\s*(?:probability|chance|likely)"
            pct_match = re.search(pct_pattern, content, re.IGNORECASE)
            if pct_match:
                prob = int(pct_match.group(1)) / 100
                estimates.append(
                    ProbabilityEstimate(
                        outcome="YES",
                        probability=prob,
                        confidence=0.5,
                        reasoning="Extracted from analysis",
                    )
                )

        return estimates

    def _parse_factors(self, content: str, factor_type: str) -> list[KeyFactor]:
        """Parse bullish/bearish factors from content."""
        factors = []

        # Find the section
        section = self._extract_section(
            content,
            f"{factor_type} FACTORS",
            "BEARISH" if factor_type == "BULLISH" else "RISKS",
        )

        if not section:
            return factors

        # Parse bullet points
        lines = section.split("\n")
        for line in lines:
            line = line.strip()
            if not line or not (line.startswith("-") or line.startswith("•") or line[0].isdigit()):
                continue

            # Clean up the line
            text = re.sub(r"^[-•\d.)\s]+", "", line).strip()
            if not text:
                continue

            # Extract confidence if present
            conf_match = re.search(r"Confidence:?\s*(\d+)%", text, re.IGNORECASE)
            confidence = int(conf_match.group(1)) / 100 if conf_match else 0.6

            # Clean text of metadata
            clean_text = re.sub(r"\(.*?\)", "", text).strip()

            factors.append(
                KeyFactor(
                    factor=clean_text[:200],
                    impact="positive" if factor_type == "BULLISH" else "negative",
                    confidence=confidence,
                )
            )

        return factors[:5]  # Limit to 5 factors

    def _parse_risks(self, content: str) -> list[RiskFactor]:
        """Parse risk factors from content."""
        risks = []

        section = self._extract_section(content, "RISKS", "KEY DATES")
        if not section:
            return risks

        lines = section.split("\n")
        for line in lines:
            line = line.strip()
            if not line or not (line.startswith("-") or line.startswith("•") or line[0].isdigit()):
                continue

            text = re.sub(r"^[-•\d.)\s]+", "", line).strip()
            if not text:
                continue

            # Extract severity
            severity = "medium"
            for sev in ["critical", "high", "medium", "low"]:
                if sev in text.lower():
                    severity = sev
                    break

            # Extract likelihood
            likelihood = 0.5
            lik_match = re.search(r"Likelihood:?\s*(\d+)%", text, re.IGNORECASE)
            if lik_match:
                likelihood = int(lik_match.group(1)) / 100

            clean_text = re.sub(r"\(.*?\)", "", text).strip()

            risks.append(
                RiskFactor(
                    risk=clean_text[:200],
                    severity=severity,
                    likelihood=likelihood,
                )
            )

        return risks[:5]

    def _parse_key_dates(self, content: str) -> list[str]:
        """Parse key dates from content."""
        dates = []

        section = self._extract_section(content, "KEY DATES", "DATA ASSESSMENT")
        if not section:
            return dates

        lines = section.split("\n")
        for line in lines:
            line = line.strip()
            if line and (line.startswith("-") or line.startswith("•") or line[0].isdigit()):
                text = re.sub(r"^[-•\d.)\s]+", "", line).strip()
                if text:
                    dates.append(text[:100])

        return dates[:5]


async def research_event(
    api_key: str,
    event_ticker: str,
    event_title: str,
    market_ticker: str | None = None,
    market_title: str | None = None,
    current_yes_price: float | None = None,
    close_time: datetime | None = None,
    model: str = "llama-3.1-sonar-large-128k-online",
) -> ResearchResult:
    """
    Convenience function to research an event.

    Args:
        api_key: Perplexity API key
        event_ticker: Event ticker
        event_title: Event title
        market_ticker: Optional market ticker
        market_title: Optional market title
        current_yes_price: Current YES price (0-1)
        close_time: Market close time
        model: Perplexity model to use

    Returns:
        Research result
    """
    query = ResearchQuery(
        event_ticker=event_ticker,
        event_title=event_title,
        market_ticker=market_ticker,
        market_title=market_title,
        current_yes_price=current_yes_price,
        close_time=close_time,
    )

    async with PerplexityClient(api_key=api_key, model=model) as client:
        return await client.research(query)
