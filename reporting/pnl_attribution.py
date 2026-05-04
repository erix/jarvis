"""Daily P&L attribution: beta + sector + factor + alpha residual."""
import logging
import os
import sqlite3
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "cache", "jarvis.db")
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "output")

SECTOR_ETFS = {
    "Information Technology": "XLK",
    "Health Care": "XLV",
    "Financials": "XLF",
    "Consumer Discretionary": "XLY",
    "Consumer Staples": "XLP",
    "Energy": "XLE",
    "Industrials": "XLI",
    "Materials": "XLB",
    "Real Estate": "XLRE",
    "Utilities": "XLU",
    "Communication Services": "XLC",
}


def _load_returns(tickers: list[str], days: int = 252, db_path: str = DB_PATH) -> pd.DataFrame:
    if not tickers:
        return pd.DataFrame()
    conn = sqlite3.connect(db_path)
    ph = ",".join("?" * len(tickers))
    df = pd.read_sql_query(
        f"SELECT ticker, date, adj_close FROM daily_prices WHERE ticker IN ({ph}) ORDER BY date",
        conn, params=tickers,
    )
    conn.close()
    if df.empty:
        return pd.DataFrame()
    df["date"] = pd.to_datetime(df["date"])
    pivot = df.pivot(index="date", columns="ticker", values="adj_close").sort_index()
    return pivot.tail(days + 1).pct_change().dropna(how="all")


def _load_portfolio_history(days: int = 252, db_path: str = DB_PATH) -> pd.DataFrame:
    conn = sqlite3.connect(db_path)
    df = pd.read_sql_query(
        "SELECT date, total_value FROM portfolio_history ORDER BY date",
        conn,
    )
    conn.close()
    if df.empty:
        return pd.DataFrame()
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index().tail(days + 1)
    df["return"] = df["total_value"].pct_change()
    return df.dropna()


def _load_positions_snapshot(db_path: str = DB_PATH) -> list[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM positions WHERE is_active=1").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def run_attribution(days: int = 252, db_path: str = DB_PATH) -> pd.DataFrame:
    """Compute daily P&L attribution DataFrame.

    Returns DataFrame with columns:
      date, total_return, beta_component, sector_component, factor_component, alpha_residual
    """
    portfolio_hist = _load_portfolio_history(days=days, db_path=db_path)
    if portfolio_hist.empty:
        logger.warning("No portfolio history — cannot run attribution")
        return pd.DataFrame()

    # Load SPY returns
    etf_tickers = ["SPY"] + list(SECTOR_ETFS.values())
    returns = _load_returns(etf_tickers, days=days, db_path=db_path)
    if returns.empty or "SPY" not in returns.columns:
        logger.warning("No SPY data for attribution")
        return pd.DataFrame()

    positions = _load_positions_snapshot(db_path)
    total_long = sum(p.get("shares", 0) for p in positions if p.get("shares", 0) > 0)
    total_short = sum(abs(p.get("shares", 0)) for p in positions if p.get("shares", 0) < 0)
    net_beta = 0.20  # Approximate — use risk model output if available

    records = []
    for date, row in portfolio_hist.iterrows():
        date_str = date.strftime("%Y-%m-%d")
        total_ret = row["return"]

        spy_ret = returns["SPY"].get(date, 0.0) if date in returns.index else 0.0
        beta_component = net_beta * spy_ret

        # Brinson-style sector attribution (simplified)
        sector_component = 0.0
        for sector_name, etf in SECTOR_ETFS.items():
            if etf in returns.columns and date in returns.index:
                etf_ret = returns[etf].get(date, 0.0)
                sector_weight = 1.0 / len(SECTOR_ETFS)
                sector_component += sector_weight * (etf_ret - spy_ret) * 0.1

        # Factor component (residual of regression on factor returns — placeholder)
        factor_component = 0.0

        alpha_residual = total_ret - beta_component - sector_component - factor_component

        records.append({
            "date": date_str,
            "total_return": round(total_ret * 100, 4),
            "beta_component": round(beta_component * 100, 4),
            "sector_component": round(sector_component * 100, 4),
            "factor_component": round(factor_component * 100, 4),
            "alpha_residual": round(alpha_residual * 100, 4),
        })

    df = pd.DataFrame(records)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    out_path = os.path.join(OUTPUT_DIR, "daily_attribution.csv")
    df.to_csv(out_path, index=False)
    logger.info("Attribution saved to %s (%d rows)", out_path, len(df))
    return df
