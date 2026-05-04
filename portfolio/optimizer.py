"""Conviction-tilt optimizer — simpler fallback to MVO."""
import os
import sqlite3

import numpy as np
import pandas as pd

from portfolio.beta import get_betas

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "cache", "jarvis.db")


def _load_adv(tickers: list[str], days: int = 20) -> dict[str, float]:
    """Return 20-day average daily volume for each ticker."""
    conn = sqlite3.connect(DB_PATH)
    placeholders = ",".join("?" * len(tickers))
    rows = conn.execute(
        f"""
        SELECT ticker, AVG(volume) as adv
        FROM (
            SELECT ticker, volume, date,
                   ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY date DESC) as rn
            FROM daily_prices WHERE ticker IN ({placeholders})
        ) WHERE rn <= {days}
        GROUP BY ticker
        """,
        tickers,
    ).fetchall()
    conn.close()
    return {r[0]: r[1] or 0.0 for r in rows}


def _load_latest_prices(tickers: list[str]) -> dict[str, float]:
    conn = sqlite3.connect(DB_PATH)
    placeholders = ",".join("?" * len(tickers))
    rows = conn.execute(
        f"""
        SELECT ticker, adj_close FROM daily_prices dp
        WHERE ticker IN ({placeholders})
          AND date = (SELECT MAX(date) FROM daily_prices WHERE ticker=dp.ticker)
        """,
        tickers,
    ).fetchall()
    conn.close()
    return {r[0]: r[1] for r in rows if r[1] and r[1] > 0}


def _score_to_conviction(score: float) -> float:
    """Map composite score [0,100] to conviction adjustment [-0.5, +0.5]."""
    return (score - 50.0) / 100.0


def conviction_tilt(
    scores_df: pd.DataFrame,
    cfg: dict,
    current_positions: list[dict] | None = None,
) -> dict:
    """Build a long/short portfolio using conviction-weighted equal-risk tilts.

    Returns:
      {
        "target_weights": {ticker: weight},
        "expected_return": float,
        "expected_volatility": float (0 if not computed),
        "sector_neutrality_score": float,
        "long_tickers": list,
        "short_tickers": list,
        "warnings": list,
      }
    """
    num_longs = cfg.get("num_longs", 20)
    num_shorts = cfg.get("num_shorts", 20)
    max_pos_pct = cfg.get("max_position_pct", 5.0) / 100.0
    gross_exp = cfg.get("gross_exposure", 165.0) / 100.0
    max_beta = cfg.get("max_portfolio_beta", 0.20)
    aum = cfg.get("aum", 10_000_000)

    warnings = []

    # Sort long/short candidates
    long_pool = scores_df[scores_df["is_long_candidate"] == 1].copy()
    short_pool = scores_df[scores_df["is_short_candidate"] == 1].copy()

    long_pool = long_pool.sort_values("composite_score", ascending=False).head(num_longs * 3)
    short_pool = short_pool.sort_values("composite_score", ascending=True).head(num_shorts * 3)

    all_cands = pd.concat([long_pool, short_pool]).drop_duplicates("ticker")
    tickers = all_cands["ticker"].tolist()

    prices = _load_latest_prices(tickers)
    adv = _load_adv(tickers)
    sectors = dict(zip(all_cands["ticker"], all_cands.get("sector", pd.Series())))

    # Base weight per side
    long_base = (gross_exp * 0.55) / num_longs  # ~55% gross on long side
    short_base = (gross_exp * 0.45) / num_shorts

    target_weights: dict[str, float] = {}
    selected_longs: list[str] = []
    selected_shorts: list[str] = []

    # --- Longs ---
    for _, row in long_pool.iterrows():
        if len(selected_longs) >= num_longs:
            break
        ticker = row["ticker"]
        price = prices.get(ticker, 0.0)
        if price <= 0:
            continue
        # Liquidity constraint: position <= 1% of 20-day ADV
        adv_val = adv.get(ticker, 0.0)
        max_shares_liquidity = adv_val * 0.01 if adv_val > 0 else float("inf")
        conviction = _score_to_conviction(row.get("composite_score", 50))
        weight = min(long_base + conviction * long_base * 0.5, max_pos_pct)
        dollar_val = weight * aum
        shares_needed = dollar_val / price if price > 0 else 0
        if adv_val > 0 and shares_needed > max_shares_liquidity:
            weight = (max_shares_liquidity * price) / aum
            warnings.append(f"{ticker}: size capped by liquidity constraint")
        weight = max(0, min(weight, max_pos_pct))
        target_weights[ticker] = weight
        selected_longs.append(ticker)

    # --- Shorts ---
    for _, row in short_pool.iterrows():
        if len(selected_shorts) >= num_shorts:
            break
        ticker = row["ticker"]
        if ticker in target_weights:
            continue
        price = prices.get(ticker, 0.0)
        if price <= 0:
            continue
        adv_val = adv.get(ticker, 0.0)
        max_shares_liquidity = adv_val * 0.01 if adv_val > 0 else float("inf")
        conviction = _score_to_conviction(row.get("composite_score", 50))
        # conviction for shorts is negative (low score → more negative)
        weight = -min(short_base + abs(conviction) * short_base * 0.5, max_pos_pct)
        dollar_val = abs(weight) * aum
        shares_needed = dollar_val / price if price > 0 else 0
        if adv_val > 0 and shares_needed > max_shares_liquidity:
            weight = -(max_shares_liquidity * price) / aum
            warnings.append(f"{ticker}: short size capped by liquidity constraint")
        weight = max(-max_pos_pct, min(weight, 0))
        target_weights[ticker] = weight
        selected_shorts.append(ticker)

    if not target_weights:
        return {
            "target_weights": {},
            "expected_return": 0.0,
            "expected_volatility": 0.0,
            "sector_neutrality_score": 0.0,
            "long_tickers": [],
            "short_tickers": [],
            "warnings": ["No candidates with valid prices found"],
        }

    # Beta adjustment: reduce highest-beta longs if portfolio beta > max
    betas = get_betas(list(target_weights.keys()), sectors)
    port_beta = sum(w * betas.get(t, 1.0) for t, w in target_weights.items())

    iteration = 0
    while abs(port_beta) > max_beta and iteration < 10:
        iteration += 1
        # Find highest absolute beta-contribution longs
        long_contributions = {
            t: target_weights[t] * betas.get(t, 1.0)
            for t in selected_longs
            if t in target_weights
        }
        if not long_contributions:
            break
        worst = max(long_contributions, key=lambda t: abs(long_contributions[t]))
        target_weights[worst] *= 0.85
        port_beta = sum(w * betas.get(t, 1.0) for t, w in target_weights.items())

    if abs(port_beta) > max_beta:
        warnings.append(f"Portfolio beta {port_beta:.3f} exceeds limit {max_beta} after adjustment")

    # Expected return: score → annual return mapping
    expected_return = 0.0
    for ticker, weight in target_weights.items():
        score_row = scores_df[scores_df["ticker"] == ticker]
        if not score_row.empty:
            score = score_row.iloc[0].get("composite_score", 50)
            ann_ret = (score - 50) / 50 * 0.15  # map [0,100] → [-15%, +15%]
            expected_return += weight * ann_ret

    # Sector neutrality: std dev of sector exposures (lower = more neutral)
    sector_exp: dict[str, float] = {}
    for ticker, weight in target_weights.items():
        sec = sectors.get(ticker, "Unknown")
        sector_exp[sec] = sector_exp.get(sec, 0.0) + weight
    sector_neutrality_score = float(np.std(list(sector_exp.values()))) if sector_exp else 0.0

    return {
        "target_weights": target_weights,
        "expected_return": expected_return,
        "expected_volatility": 0.0,  # not computed for conviction-tilt
        "sector_neutrality_score": sector_neutrality_score,
        "long_tickers": selected_longs,
        "short_tickers": selected_shorts,
        "warnings": warnings,
        "betas": betas,
        "portfolio_beta": port_beta,
        "prices": prices,
        "adv": adv,
    }
