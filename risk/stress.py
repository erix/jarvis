"""Stress testing: 6 historical/synthetic scenarios applied to current portfolio."""
import logging
import os
import sqlite3

import numpy as np
import pandas as pd

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "cache", "jarvis.db")
logger = logging.getLogger(__name__)

# Scenario definitions: {name: {description, shocks}}
# shocks: list of (selector, shock_return) pairs
# selector: "all" | "sector:X" | "factor:momentum_high" | "factor:momentum_low" | "top_short" | "worst_sector"
SCENARIOS = {
    "crisis_2008": {
        "description": "2008 Financial Crisis",
        "shocks": [("all", -0.60)],
        "vix_shock": 0.50,
    },
    "covid_2020": {
        "description": "2020 Covid Crash",
        "shocks": [("all", -0.35)],
        "vix_shock": 0.60,
    },
    "rate_hikes_2022": {
        "description": "2022 Rate Hike Cycle",
        "shocks": [
            ("sector:Financials", +0.20),
            ("sector:Information Technology", -0.30),
            ("sector:Communication Services", -0.20),
        ],
    },
    "sector_shock": {
        "description": "Single Worst Sector -30%",
        "shocks": [("worst_sector", -0.30)],
    },
    "momentum_reversal": {
        "description": "Momentum Factor Crash",
        "shocks": [
            ("factor:momentum_high", -0.25),   # Long momentum names get crushed
            ("factor:momentum_low", +0.25),    # Short momentum names squeeze up
        ],
    },
    "short_squeeze": {
        "description": "Short Squeeze on Top Short",
        "shocks": [("top_short", +1.00)],
    },
}


def _load_positions_with_prices() -> pd.DataFrame:
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query(
        """SELECT p.ticker, p.shares, p.entry_price, p.current_price, p.sector, p.beta,
                  p.factor_exposures,
                  s.momentum_score
           FROM positions p
           LEFT JOIN (
               SELECT ticker, momentum_score FROM scores
               WHERE date = (SELECT MAX(date) FROM scores)
           ) s ON p.ticker = s.ticker
           WHERE p.is_active = 1""",
        conn,
    )
    conn.close()
    # Fill current_price with entry_price if missing
    df["current_price"] = df["current_price"].fillna(df["entry_price"])
    return df


def _get_position_value(pos: pd.Series) -> float:
    return float(pos["shares"]) * float(pos["current_price"] or pos["entry_price"] or 0)


def _identify_worst_sector(positions_df: pd.DataFrame, weights_dict: dict[str, float]) -> str | None:
    """Return the sector with highest gross exposure in the long book."""
    if positions_df.empty:
        return None
    long_pos = positions_df[positions_df["shares"] > 0]
    if long_pos.empty:
        return None
    sector_exposure = long_pos.groupby("sector").apply(
        lambda g: (g["shares"] * g["current_price"]).sum()
    )
    return str(sector_exposure.idxmax()) if not sector_exposure.empty else None


def _identify_top_short(positions_df: pd.DataFrame) -> str | None:
    """Return the ticker with largest short position by absolute notional."""
    shorts = positions_df[positions_df["shares"] < 0].copy()
    if shorts.empty:
        return None
    shorts["notional"] = shorts["shares"].abs() * shorts["current_price"].abs()
    return str(shorts.loc[shorts["notional"].idxmax(), "ticker"])


def _apply_shocks(positions_df: pd.DataFrame, shocks: list[tuple]) -> float:
    """Apply scenario shocks to positions and compute total P&L."""
    if positions_df.empty:
        return 0.0

    worst_sector = _identify_worst_sector(positions_df, {})
    top_short = _identify_top_short(positions_df)

    # Determine per-ticker shock
    ticker_shocks: dict[str, float] = {}

    for selector, shock_return in shocks:
        if selector == "all":
            for _, row in positions_df.iterrows():
                ticker_shocks[row["ticker"]] = ticker_shocks.get(row["ticker"], 0) + shock_return

        elif selector.startswith("sector:"):
            target_sector = selector.split(":", 1)[1]
            for _, row in positions_df.iterrows():
                if row.get("sector") == target_sector:
                    ticker_shocks[row["ticker"]] = ticker_shocks.get(row["ticker"], 0) + shock_return

        elif selector == "worst_sector":
            if worst_sector:
                for _, row in positions_df.iterrows():
                    if row.get("sector") == worst_sector:
                        ticker_shocks[row["ticker"]] = ticker_shocks.get(row["ticker"], 0) + shock_return

        elif selector == "factor:momentum_high":
            # Apply shock to high-momentum names (momentum_score > 50)
            for _, row in positions_df.iterrows():
                if pd.notna(row.get("momentum_score")) and row["momentum_score"] > 50:
                    ticker_shocks[row["ticker"]] = ticker_shocks.get(row["ticker"], 0) + shock_return

        elif selector == "factor:momentum_low":
            # Apply shock to low-momentum names (momentum_score <= 50)
            for _, row in positions_df.iterrows():
                if pd.isna(row.get("momentum_score")) or row["momentum_score"] <= 50:
                    ticker_shocks[row["ticker"]] = ticker_shocks.get(row["ticker"], 0) + shock_return

        elif selector == "top_short":
            if top_short:
                ticker_shocks[top_short] = ticker_shocks.get(top_short, 0) + shock_return

    # Compute total portfolio P&L
    total_pnl = 0.0
    for _, row in positions_df.iterrows():
        ticker = row["ticker"]
        shock = ticker_shocks.get(ticker, 0.0)
        pos_value = _get_position_value(row)
        total_pnl += pos_value * shock

    return total_pnl


def run_stress_test(
    positions_df: pd.DataFrame | None = None,
    returns_df: pd.DataFrame | None = None,
    weights_dict: dict[str, float] | None = None,
    aum: float = 1_000_000.0,
) -> pd.DataFrame:
    """Run all 6 stress scenarios. Returns DataFrame with results sorted by worst P&L.

    positions_df: optional — loaded from DB if not provided
    aum: used to express P&L as % of AUM
    """
    if positions_df is None:
        positions_df = _load_positions_with_prices()

    if positions_df.empty:
        logger.warning("No active positions — stress test skipped")
        return pd.DataFrame(columns=["scenario", "description", "pnl", "pnl_pct", "worst_sector", "top_short"])

    worst_sector = _identify_worst_sector(positions_df, weights_dict or {})
    top_short = _identify_top_short(positions_df)

    results = []
    for scenario_name, scenario in SCENARIOS.items():
        pnl = _apply_shocks(positions_df, scenario["shocks"])
        pnl_pct = pnl / aum * 100 if aum else 0.0
        results.append({
            "scenario": scenario_name,
            "description": scenario["description"],
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 2),
            "worst_sector": worst_sector or "N/A",
            "top_short": top_short or "N/A",
        })
        logger.info("Stress [%s]: P&L = $%.0f (%.2f%% of AUM)", scenario_name, pnl, pnl_pct)

    results_df = pd.DataFrame(results).sort_values("pnl").reset_index(drop=True)
    return results_df
