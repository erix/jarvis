"""Rolling 60-day beta calculator vs SPY."""
from __future__ import annotations

import sqlite3
import os

import numpy as np
import pandas as pd

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "cache", "jarvis.db")

# Sector ETF proxies when a stock lacks history
SECTOR_ETF_MAP = {
    "Information Technology": "XLK",
    "Health Care": "XLV",
    "Financials": "XLF",
    "Consumer Discretionary": "XLY",
    "Consumer Staples": "XLP",
    "Energy": "XLE",
    "Industrials": "XLI",
    "Communication Services": "XLC",
    "Materials": "XLB",
    "Real Estate": "XLRE",
    "Utilities": "XLU",
}


def _load_returns(tickers: list[str], days: int = 70) -> pd.DataFrame:
    """Load daily adj_close and compute returns for requested tickers."""
    conn = sqlite3.connect(DB_PATH)
    placeholders = ",".join("?" * len(tickers))
    query = f"""
        SELECT ticker, date, adj_close
        FROM daily_prices
        WHERE ticker IN ({placeholders})
        ORDER BY ticker, date
    """
    df = pd.read_sql_query(query, conn, params=tickers)
    conn.close()

    if df.empty:
        return pd.DataFrame()

    df["date"] = pd.to_datetime(df["date"])
    pivot = df.pivot(index="date", columns="ticker", values="adj_close").sort_index()
    # Keep only last (days + 5) rows for efficiency
    pivot = pivot.tail(days + 5)
    returns = pivot.pct_change().dropna(how="all")
    return returns


def calculate_beta(ticker: str, days: int = 60, sector: str | None = None) -> float:
    """Return rolling 60-day beta vs SPY.

    Falls back to sector ETF beta when the stock has fewer than days of history.
    Returns 1.0 if all data is missing.
    """
    returns = _load_returns([ticker, "SPY"], days=days)

    if returns.empty or "SPY" not in returns.columns:
        return _sector_beta(sector) if sector else 1.0

    if ticker not in returns.columns:
        return _sector_beta(sector) if sector else 1.0

    pair = returns[[ticker, "SPY"]].dropna().tail(days)

    if len(pair) < 20:
        # Not enough history — try sector ETF
        return _sector_beta(sector) if sector else 1.0

    cov = np.cov(pair[ticker].values, pair["SPY"].values)
    var_spy = cov[1, 1]
    if var_spy == 0:
        return 1.0
    return float(cov[0, 1] / var_spy)


def _sector_beta(sector: str | None) -> float:
    """Return the beta of the sector ETF proxy vs SPY."""
    if sector is None:
        return 1.0
    etf = SECTOR_ETF_MAP.get(sector)
    if etf is None:
        return 1.0
    returns = _load_returns([etf, "SPY"], days=60)
    if returns.empty or etf not in returns.columns or "SPY" not in returns.columns:
        return 1.0
    pair = returns[[etf, "SPY"]].dropna().tail(60)
    if len(pair) < 20:
        return 1.0
    cov = np.cov(pair[etf].values, pair["SPY"].values)
    var_spy = cov[1, 1]
    if var_spy == 0:
        return 1.0
    return float(cov[0, 1] / var_spy)


def calculate_portfolio_beta(weights: dict[str, float], sectors: dict[str, str] | None = None) -> float:
    """Weighted average beta for a portfolio.

    weights: {ticker: weight}  (weights can be negative for shorts)
    sectors: {ticker: sector}  optional, used for fallback
    """
    if not weights:
        return 0.0
    tickers = list(weights.keys())
    returns = _load_returns(tickers + ["SPY"], days=65)

    portfolio_beta = 0.0
    for ticker, weight in weights.items():
        sector = (sectors or {}).get(ticker)
        if returns.empty or ticker not in returns.columns or "SPY" not in returns.columns:
            b = _sector_beta(sector) if sector else 1.0
        else:
            pair = returns[[ticker, "SPY"]].dropna().tail(60)
            if len(pair) < 20:
                b = _sector_beta(sector) if sector else 1.0
            else:
                cov = np.cov(pair[ticker].values, pair["SPY"].values)
                var_spy = cov[1, 1]
                b = float(cov[0, 1] / var_spy) if var_spy != 0 else 1.0
        portfolio_beta += weight * b

    return portfolio_beta


def get_betas(tickers: list[str], sectors: dict[str, str] | None = None, days: int = 60) -> dict[str, float]:
    """Return {ticker: beta} for all tickers in one DB round-trip."""
    returns = _load_returns(tickers + ["SPY"], days=days + 5)
    result = {}
    for ticker in tickers:
        sector = (sectors or {}).get(ticker)
        if returns.empty or ticker not in returns.columns or "SPY" not in returns.columns:
            result[ticker] = _sector_beta(sector) if sector else 1.0
            continue
        pair = returns[[ticker, "SPY"]].dropna().tail(days)
        if len(pair) < 20:
            result[ticker] = _sector_beta(sector) if sector else 1.0
            continue
        cov = np.cov(pair[ticker].values, pair["SPY"].values)
        var_spy = cov[1, 1]
        result[ticker] = float(cov[0, 1] / var_spy) if var_spy != 0 else 1.0
    return result
