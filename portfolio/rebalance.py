"""Rebalance generator: compare current vs target, generate trade list."""
from portfolio.transaction_costs import estimate_cost


def generate_trades(
    current_positions: list[dict],
    target_weights: dict[str, float],
    prices: dict[str, float],
    adv: dict[str, float],
    aum: float,
    turnover_budget_pct: float = 30.0,
) -> dict:
    """Compute the trade list needed to move from current to target.

    Returns:
      {
        "trades": {ticker: {"action": str, "shares": float, "estimated_cost": float}},
        "total_cost": float,
        "turnover_pct": float,
        "scaled": bool,
      }
    """
    # Current positions: {ticker: shares}
    current_shares: dict[str, float] = {p["ticker"]: p["shares"] for p in current_positions}

    # Target shares
    target_shares: dict[str, float] = {}
    for ticker, weight in target_weights.items():
        price = prices.get(ticker, 0.0)
        if price > 0:
            target_shares[ticker] = (weight * aum) / price
        else:
            target_shares[ticker] = 0.0

    # Positions to close (in current but not in target)
    all_tickers = set(current_shares) | set(target_shares)

    raw_trades: dict[str, float] = {}
    for ticker in all_tickers:
        cur = current_shares.get(ticker, 0.0)
        tgt = target_shares.get(ticker, 0.0)
        delta = tgt - cur
        if abs(delta) > 0.1:  # ignore sub-share rounding noise
            raw_trades[ticker] = delta

    if not raw_trades:
        return {"trades": {}, "total_cost": 0.0, "turnover_pct": 0.0, "scaled": False}

    # Turnover: sum(|delta| * price) / AUM
    trade_value = sum(
        abs(shares) * prices.get(ticker, 0.0)
        for ticker, shares in raw_trades.items()
    )
    turnover_pct = (trade_value / aum) * 100 if aum > 0 else 0.0

    scaled = False
    if turnover_pct > turnover_budget_pct:
        scale = turnover_budget_pct / turnover_pct
        raw_trades = {t: s * scale for t, s in raw_trades.items()}
        turnover_pct = turnover_budget_pct
        scaled = True

    # Build output
    trades_out: dict = {}
    total_cost = 0.0
    for ticker, shares in raw_trades.items():
        if abs(shares) < 0.1:
            continue
        cur = current_shares.get(ticker, 0.0)
        price = prices.get(ticker, 0.0)
        vol = adv.get(ticker, 0.0)
        cost = estimate_cost(ticker, shares, price, vol)
        total_cost += cost
        action = _action(cur, shares)
        trades_out[ticker] = {
            "action": action,
            "shares": round(shares, 2),
            "price": round(price, 2),
            "estimated_cost": round(cost, 2),
            "dollar_value": round(abs(shares) * price, 2),
        }

    return {
        "trades": trades_out,
        "total_cost": round(total_cost, 2),
        "turnover_pct": round(turnover_pct, 2),
        "scaled": scaled,
    }


def _action(current_shares: float, delta: float) -> str:
    if current_shares == 0:
        return "short" if delta < 0 else "buy"
    after = current_shares + delta
    if current_shares > 0:
        if after <= 0:
            return "sell"
        return "buy" if delta > 0 else "sell"
    else:  # currently short
        if after >= 0:
            return "cover"
        return "short" if delta < 0 else "cover"


def whatif_report(
    trade_result: dict,
    opt_result: dict,
    exposure: dict,
    advice: dict,
) -> str:
    """Format a human-readable what-if analysis."""
    lines = []
    lines.append("=" * 60)
    lines.append("JARVIS — What-If Portfolio Analysis")
    lines.append("=" * 60)

    if advice.get("warnings"):
        lines.append("\nSchedule Warnings:")
        for w in advice["warnings"]:
            lines.append(f"  ! {w}")

    longs = opt_result.get("long_tickers", [])
    shorts = opt_result.get("short_tickers", [])
    lines.append(f"\nPositions: {len(longs)} long / {len(shorts)} short")

    weights = opt_result.get("target_weights", {})
    gross = sum(abs(w) for w in weights.values()) * 100
    net = sum(weights.values()) * 100
    lines.append(f"Gross exposure: {gross:.1f}%")
    lines.append(f"Net exposure:   {net:.1f}%")
    lines.append(f"Portfolio beta: {opt_result.get('portfolio_beta', 0):.3f}")
    lines.append(f"Expected return:{opt_result.get('expected_return', 0)*100:.1f}%")
    ev = opt_result.get("expected_volatility", 0)
    if ev:
        lines.append(f"Expected vol:   {ev*100:.1f}%")

    lines.append(f"\nLong positions ({len(longs)}):")
    for t in longs[:25]:
        w = weights.get(t, 0) * 100
        lines.append(f"  {t:<8} {w:>5.2f}%")

    lines.append(f"\nShort positions ({len(shorts)}):")
    for t in shorts[:25]:
        w = weights.get(t, 0) * 100
        lines.append(f"  {t:<8} {w:>5.2f}%")

    trades = trade_result.get("trades", {})
    total_cost = trade_result.get("total_cost", 0)
    turnover = trade_result.get("turnover_pct", 0)
    lines.append(f"\nTrades: {len(trades)}")
    lines.append(f"Est. transaction cost: ${total_cost:,.0f}")
    lines.append(f"Turnover: {turnover:.1f}%")
    if trade_result.get("scaled"):
        lines.append("  (trade sizes scaled down to fit turnover budget)")

    # Factor exposure summary
    factor_exp = exposure.get("factor_exposures", {})
    if factor_exp:
        lines.append("\nFactor Exposures (weighted avg score):")
        flags = exposure.get("overconcentration_flags", {})
        for factor, val in factor_exp.items():
            label = factor.replace("_score", "").replace("_", " ").title()
            flag = " [!]" if flags.get(factor) else ""
            lines.append(f"  {label:<22} {val:>6.1f}{flag}")

    if opt_result.get("warnings"):
        lines.append("\nOptimizer Warnings:")
        for w in opt_result["warnings"]:
            lines.append(f"  ! {w}")

    lines.append("=" * 60)
    return "\n".join(lines)
