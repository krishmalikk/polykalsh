"""
Kelly Criterion position sizing.

Calculates optimal bet sizes to maximize long-term portfolio growth.
"""

from dataclasses import dataclass

import structlog

logger = structlog.get_logger()


@dataclass
class KellyResult:
    """Result of Kelly Criterion calculation."""

    # Raw Kelly
    full_kelly_fraction: float  # Optimal fraction of bankroll to bet
    edge: float  # Expected edge (our_prob - market_price)
    odds: float  # Payout odds

    # Adjusted Kelly
    fractional_kelly: float  # After applying kelly_fraction
    confidence_adjusted: float  # After scaling by confidence

    # Final sizing
    recommended_fraction: float  # Final fraction to bet
    recommended_usd: float  # Dollar amount
    recommended_contracts: int  # Number of contracts

    # Constraints applied
    limiting_factor: str  # What limited the size


def calculate_kelly(
    probability: float,
    market_price: float,
    side: str = "YES",
) -> float:
    """
    Calculate raw Kelly Criterion fraction.

    For binary markets:
    - Betting YES at price p: win (1-p)/p if YES, lose 1 if NO
    - Betting NO at price p: win p/(1-p) if NO, lose 1 if YES

    Kelly formula: f* = (p * b - q) / b
    Where:
    - p = probability of winning
    - q = probability of losing (1 - p)
    - b = odds (net payout per dollar risked)

    Args:
        probability: Our estimated probability of YES outcome
        market_price: Current market price for YES
        side: "YES" or "NO" - which side we're betting

    Returns:
        Kelly fraction (can be negative if edge is negative)
    """
    if side.upper() == "YES":
        # Betting YES: we think prob > market_price
        p = probability  # Prob we win
        q = 1 - probability  # Prob we lose
        # Odds: if YES wins, we get (1 - market_price) per market_price risked
        # Net odds b = (1 - market_price) / market_price
        if market_price <= 0 or market_price >= 1:
            return 0.0
        b = (1 - market_price) / market_price
    else:
        # Betting NO: we think prob < market_price (so NO is underpriced)
        p = 1 - probability  # Prob NO wins
        q = probability  # Prob NO loses
        # Odds: if NO wins, we get market_price per (1 - market_price) risked
        # Net odds b = market_price / (1 - market_price)
        if market_price <= 0 or market_price >= 1:
            return 0.0
        b = market_price / (1 - market_price)

    if b <= 0:
        return 0.0

    # Kelly formula: f* = (p * b - q) / b
    kelly = (p * b - q) / b

    return kelly


def calculate_edge(
    probability: float,
    market_price: float,
    side: str = "YES",
) -> float:
    """
    Calculate edge (expected value per dollar).

    Args:
        probability: Our estimated probability of YES
        market_price: Current market price for YES
        side: "YES" or "NO"

    Returns:
        Edge as a fraction (e.g., 0.08 = 8% edge)
    """
    if side.upper() == "YES":
        # Edge for YES: we pay market_price, expect probability * 1
        # EV = probability * 1 - market_price
        return probability - market_price
    else:
        # Edge for NO: we pay (1 - market_price), expect (1 - probability) * 1
        # EV = (1 - probability) - (1 - market_price) = market_price - probability
        return market_price - probability


def calculate_position_size(
    probability: float,
    confidence: float,
    market_price: float,
    side: str,
    bankroll: float,
    kelly_fraction: float = 0.75,
    max_position_pct: float = 0.10,
    max_bet_usd: float = 100.0,
    min_bet_usd: float = 5.0,
    min_edge: float = 0.05,
    min_confidence: float = 0.50,
) -> KellyResult:
    """
    Calculate recommended position size with all adjustments.

    Args:
        probability: Our estimated probability of YES outcome
        confidence: Confidence in our probability estimate (0-1)
        market_price: Current market price for YES
        side: "YES" or "NO"
        bankroll: Total available capital
        kelly_fraction: Fraction of Kelly to use (e.g., 0.75 = 75% Kelly)
        max_position_pct: Maximum position as % of bankroll
        max_bet_usd: Maximum bet in USD
        min_bet_usd: Minimum bet in USD
        min_edge: Minimum edge required to bet
        min_confidence: Minimum confidence required to bet

    Returns:
        KellyResult with all calculations
    """
    # Calculate edge
    edge = calculate_edge(probability, market_price, side)

    # Calculate odds
    if side.upper() == "YES":
        odds = (1 - market_price) / market_price if market_price > 0 else 0
    else:
        odds = market_price / (1 - market_price) if market_price < 1 else 0

    # Calculate full Kelly
    full_kelly = calculate_kelly(probability, market_price, side)

    # Apply fractional Kelly
    fractional = full_kelly * kelly_fraction

    # Adjust for confidence
    # Scale down position when confidence is low
    confidence_adjusted = fractional * confidence

    # Start with confidence-adjusted Kelly
    recommended_fraction = confidence_adjusted
    limiting_factor = "kelly"

    # Check minimum edge
    if abs(edge) < min_edge:
        recommended_fraction = 0.0
        limiting_factor = "min_edge"

    # Check minimum confidence
    if confidence < min_confidence:
        recommended_fraction = 0.0
        limiting_factor = "min_confidence"

    # Check if Kelly is negative (negative edge)
    if full_kelly <= 0:
        recommended_fraction = 0.0
        limiting_factor = "negative_edge"

    # Apply max position constraint
    if recommended_fraction > max_position_pct:
        recommended_fraction = max_position_pct
        limiting_factor = "max_position_pct"

    # Calculate USD amount
    recommended_usd = recommended_fraction * bankroll

    # Apply max bet constraint
    if recommended_usd > max_bet_usd:
        recommended_usd = max_bet_usd
        recommended_fraction = recommended_usd / bankroll if bankroll > 0 else 0
        limiting_factor = "max_bet_usd"

    # Apply min bet constraint
    if 0 < recommended_usd < min_bet_usd:
        recommended_usd = 0.0
        recommended_fraction = 0.0
        limiting_factor = "min_bet_usd"

    # Calculate contracts (at the given price)
    price_per_contract = market_price if side.upper() == "YES" else (1 - market_price)
    if price_per_contract > 0:
        # Price is in 0-1, contracts cost price * 100 cents = price dollars
        recommended_contracts = int(recommended_usd / price_per_contract)
    else:
        recommended_contracts = 0

    # Ensure at least 1 contract if we're betting
    if recommended_usd >= min_bet_usd and recommended_contracts == 0:
        recommended_contracts = 1

    logger.debug(
        "kelly_calculation",
        probability=probability,
        market_price=market_price,
        side=side,
        edge=edge,
        full_kelly=full_kelly,
        fractional=fractional,
        confidence_adjusted=confidence_adjusted,
        recommended_usd=recommended_usd,
        limiting_factor=limiting_factor,
    )

    return KellyResult(
        full_kelly_fraction=full_kelly,
        edge=edge,
        odds=odds,
        fractional_kelly=fractional,
        confidence_adjusted=confidence_adjusted,
        recommended_fraction=recommended_fraction,
        recommended_usd=recommended_usd,
        recommended_contracts=recommended_contracts,
        limiting_factor=limiting_factor,
    )


def kelly_growth_rate(kelly_fraction: float, probability: float, odds: float) -> float:
    """
    Calculate expected log growth rate for a given Kelly fraction.

    G = p * log(1 + f * b) + q * log(1 - f)

    Args:
        kelly_fraction: Fraction of bankroll to bet
        probability: Win probability
        odds: Net payout odds

    Returns:
        Expected log growth rate
    """
    import math

    if kelly_fraction <= 0 or kelly_fraction >= 1:
        return 0.0

    p = probability
    q = 1 - probability
    f = kelly_fraction
    b = odds

    try:
        growth = p * math.log(1 + f * b) + q * math.log(1 - f)
        return growth
    except (ValueError, ZeroDivisionError):
        return 0.0
