"""
Research manager with database caching.

Coordinates research requests and caches results to avoid redundant API calls.
"""

import asyncio
import json
from datetime import datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from polykalsh.database.models import EventResearch
from polykalsh.hybrid.research.perplexity import PerplexityClient, PerplexityError
from polykalsh.hybrid.research.schemas import (
    ResearchQuery,
    ResearchResult,
    ResearchType,
)

logger = structlog.get_logger()


class ResearchManager:
    """
    Manages research requests with caching.

    Features:
    - Database caching with configurable TTL
    - Batch research with concurrency control
    - Cost tracking
    """

    def __init__(
        self,
        api_key: str,
        db_session: Session,
        model: str = "llama-3.1-sonar-large-128k-online",
        cache_ttl_hours: float = 6.0,
        max_concurrent: int = 3,
    ):
        """
        Initialize research manager.

        Args:
            api_key: Perplexity API key
            db_session: SQLAlchemy session
            model: Perplexity model to use
            cache_ttl_hours: Cache TTL in hours
            max_concurrent: Max concurrent research requests
        """
        self.api_key = api_key
        self.db = db_session
        self.model = model
        self.cache_ttl_hours = cache_ttl_hours
        self.max_concurrent = max_concurrent
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._client: PerplexityClient | None = None

        # Cost tracking
        self.total_cost_usd = 0.0
        self.total_tokens = 0
        self.cache_hits = 0
        self.cache_misses = 0

    async def _get_client(self) -> PerplexityClient:
        """Get or create Perplexity client."""
        if self._client is None:
            self._client = PerplexityClient(
                api_key=self.api_key,
                model=self.model,
            )
            await self._client._ensure_client()
        return self._client

    async def close(self) -> None:
        """Close the client."""
        if self._client:
            await self._client.close()
            self._client = None

    def _get_cached(
        self,
        event_ticker: str,
        market_ticker: str | None = None,
    ) -> ResearchResult | None:
        """Get cached research if not stale."""
        query = select(EventResearch).where(
            EventResearch.event_ticker == event_ticker
        )
        if market_ticker:
            query = query.where(EventResearch.market_ticker == market_ticker)

        result = self.db.execute(query).scalar_one_or_none()

        if result is None:
            return None

        # Check if stale
        cutoff = datetime.utcnow() - timedelta(hours=self.cache_ttl_hours)
        if result.researched_at < cutoff:
            logger.debug(
                "cache_stale",
                event_ticker=event_ticker,
                age_hours=(datetime.utcnow() - result.researched_at).total_seconds() / 3600,
            )
            return None

        self.cache_hits += 1
        logger.debug("cache_hit", event_ticker=event_ticker)

        # Reconstruct ResearchResult from cached data
        return self._db_to_result(result)

    def _save_to_cache(self, result: ResearchResult) -> None:
        """Save research result to database cache."""
        # Check for existing record
        query = select(EventResearch).where(
            EventResearch.event_ticker == result.event_ticker
        )
        if result.market_ticker:
            query = query.where(EventResearch.market_ticker == result.market_ticker)

        existing = self.db.execute(query).scalar_one_or_none()

        if existing:
            # Update existing
            existing.title = result.title
            existing.summary = result.summary
            existing.narrative = result.narrative
            existing.probability_yes = result.primary_probability
            existing.confidence = result.avg_confidence
            existing.bullish_factors = json.dumps([f.model_dump() for f in result.bullish_factors])
            existing.bearish_factors = json.dumps([f.model_dump() for f in result.bearish_factors])
            existing.risk_factors = json.dumps([f.model_dump() for f in result.risk_factors])
            existing.key_dates = json.dumps(result.key_dates)
            existing.sources = json.dumps([s.model_dump() for s in result.sources])
            existing.source_count = result.source_count
            existing.model_used = result.model_used
            existing.tokens_used = result.tokens_used
            existing.cost_usd = result.cost_usd
            existing.data_freshness = result.data_freshness
            existing.consensus_strength = result.consensus_strength
            existing.researched_at = result.researched_at
        else:
            # Create new
            research = EventResearch(
                event_ticker=result.event_ticker,
                market_ticker=result.market_ticker,
                research_type=result.research_type.value,
                title=result.title,
                summary=result.summary,
                narrative=result.narrative,
                probability_yes=result.primary_probability,
                confidence=result.avg_confidence,
                bullish_factors=json.dumps([f.model_dump() for f in result.bullish_factors]),
                bearish_factors=json.dumps([f.model_dump() for f in result.bearish_factors]),
                risk_factors=json.dumps([f.model_dump() for f in result.risk_factors]),
                key_dates=json.dumps(result.key_dates),
                sources=json.dumps([s.model_dump() for s in result.sources]),
                source_count=result.source_count,
                model_used=result.model_used,
                tokens_used=result.tokens_used,
                cost_usd=result.cost_usd,
                data_freshness=result.data_freshness,
                consensus_strength=result.consensus_strength,
                researched_at=result.researched_at,
            )
            self.db.add(research)

        self.db.commit()
        logger.debug("cache_saved", event_ticker=result.event_ticker)

    def _db_to_result(self, db_record: EventResearch) -> ResearchResult:
        """Convert database record to ResearchResult."""
        from polykalsh.hybrid.research.schemas import (
            KeyFactor,
            ProbabilityEstimate,
            RiskFactor,
            Source,
        )

        # Parse JSON fields
        bullish = [KeyFactor(**f) for f in json.loads(db_record.bullish_factors or "[]")]
        bearish = [KeyFactor(**f) for f in json.loads(db_record.bearish_factors or "[]")]
        risks = [RiskFactor(**r) for r in json.loads(db_record.risk_factors or "[]")]
        sources = [Source(**s) for s in json.loads(db_record.sources or "[]")]
        key_dates = json.loads(db_record.key_dates or "[]")

        # Reconstruct probability estimates
        prob_estimates = []
        if db_record.probability_yes is not None:
            prob_estimates.append(
                ProbabilityEstimate(
                    outcome="YES",
                    probability=db_record.probability_yes,
                    confidence=db_record.confidence or 0.5,
                    reasoning="From cached research",
                )
            )

        return ResearchResult(
            event_ticker=db_record.event_ticker,
            market_ticker=db_record.market_ticker,
            research_type=ResearchType(db_record.research_type),
            title=db_record.title,
            summary=db_record.summary,
            narrative=db_record.narrative,
            probability_estimates=prob_estimates,
            bullish_factors=bullish,
            bearish_factors=bearish,
            risk_factors=risks,
            key_dates=key_dates,
            sources=sources,
            source_count=db_record.source_count,
            model_used=db_record.model_used or "",
            tokens_used=db_record.tokens_used or 0,
            cost_usd=db_record.cost_usd or 0.0,
            data_freshness=db_record.data_freshness or "unknown",
            consensus_strength=db_record.consensus_strength or "unknown",
            researched_at=db_record.researched_at,
        )

    async def research(
        self,
        query: ResearchQuery,
        force_refresh: bool = False,
    ) -> ResearchResult:
        """
        Research an event/market.

        Uses cache if available, otherwise calls Perplexity API.

        Args:
            query: Research query
            force_refresh: Skip cache and force new research

        Returns:
            Research result
        """
        # Check cache first (unless force refresh)
        if not force_refresh:
            cached = self._get_cached(query.event_ticker, query.market_ticker)
            if cached:
                return cached

        self.cache_misses += 1

        # Rate limit with semaphore
        async with self._semaphore:
            client = await self._get_client()

            try:
                result = await client.research(query)

                # Track costs
                self.total_cost_usd += result.cost_usd
                self.total_tokens += result.tokens_used

                # Cache the result
                self._save_to_cache(result)

                return result

            except PerplexityError as e:
                logger.error(
                    "research_failed",
                    event_ticker=query.event_ticker,
                    error=str(e),
                )
                raise

    async def research_batch(
        self,
        queries: list[ResearchQuery],
        force_refresh: bool = False,
    ) -> list[ResearchResult]:
        """
        Research multiple events/markets concurrently.

        Args:
            queries: List of research queries
            force_refresh: Skip cache for all

        Returns:
            List of research results (in same order as queries)
        """
        tasks = [
            self.research(query, force_refresh=force_refresh)
            for query in queries
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Filter out errors and log them
        final_results = []
        for query, result in zip(queries, results):
            if isinstance(result, Exception):
                logger.error(
                    "batch_research_error",
                    event_ticker=query.event_ticker,
                    error=str(result),
                )
                continue
            final_results.append(result)

        return final_results

    def get_stats(self) -> dict[str, Any]:
        """Get research statistics."""
        total_requests = self.cache_hits + self.cache_misses
        hit_rate = self.cache_hits / total_requests if total_requests > 0 else 0.0

        return {
            "total_requests": total_requests,
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "hit_rate": hit_rate,
            "total_tokens": self.total_tokens,
            "total_cost_usd": self.total_cost_usd,
        }
