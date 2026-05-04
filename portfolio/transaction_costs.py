"""Transaction cost model: commission, bid-ask spread, and market impact."""


def estimate_cost(
    ticker: str,
    shares: float,
    current_price: float,
    avg_daily_volume: float,
    impact_coeff: float = 0.10,
) -> float:
    """Return total estimated transaction cost in dollars.

    Uses IBKR-like commission schedule plus market impact via square-root law.
    """
    abs_shares = abs(shares)
    if abs_shares == 0 or current_price <= 0:
        return 0.0

    commission = 0.005 * abs_shares

    # Spread cost: assume 1 cent spread (bid-ask ~0.5 bps for liquid names)
    spread_cost = 0.005 * abs_shares  # half-spread ≈ $0.005/share

    # Market impact: Almgren-style square-root model
    if avg_daily_volume and avg_daily_volume > 0:
        participation = abs_shares / avg_daily_volume
        market_impact = impact_coeff * (participation ** 0.6) * current_price * abs_shares
    else:
        # Fall back to 5 bps if ADV unknown
        market_impact = 0.0005 * current_price * abs_shares

    return commission + spread_cost + market_impact


def estimate_portfolio_cost(
    trades: dict,
    prices: dict,
    adv: dict,
) -> float:
    """Sum transaction costs across all trades.

    trades: {ticker: shares}
    prices: {ticker: current_price}
    adv:    {ticker: avg_daily_volume}
    """
    total = 0.0
    for ticker, shares in trades.items():
        price = prices.get(ticker, 0.0)
        vol = adv.get(ticker, 0.0)
        total += estimate_cost(ticker, shares, price, vol)
    return total
