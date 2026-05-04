"""Sector-relative performance: stock-selection alpha vs sector ETFs."""
import logging
import os
import sqlite3
from datetime import datetime, timedelta

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


def _get_returns(tickers: list[str], days: int = 90, db_path: str = DB_PATH) -> pd.DataFrame:
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


def compute_sector_alpha(window_days: int = 90, db_path: str = DB_PATH) -> pd.DataFrame:
    """Compute stock-selection alpha per sector over trailing window.

    Returns DataFrame with columns:
      sector, etf, portfolio_return, etf_return, alpha, winner
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    positions = conn.execute(
        "SELECT p.ticker, p.shares, t.sector "
        "FROM positions p JOIN tickers t ON p.ticker=t.symbol "
        "WHERE p.is_active=1"
    ).fetchall()
    conn.close()

    if not positions:
        return pd.DataFrame()

    sector_map: dict[str, list[str]] = {}
    for pos in positions:
        s = pos["sector"] or "Unknown"
        sector_map.setdefault(s, []).append(pos["ticker"])

    all_tickers = [pos["ticker"] for pos in positions]
    all_etfs = list(SECTOR_ETFS.values())
    returns = _get_returns(all_tickers + all_etfs, days=window_days, db_path=db_path)
    if returns.empty:
        return pd.DataFrame()

    total_return = (1 + returns).prod() - 1  # cumulative over window

    records = []
    for sector, tickers_in_sector in sector_map.items():
        etf = SECTOR_ETFS.get(sector)
        if not etf:
            continue

        sector_tickers = [t for t in tickers_in_sector if t in total_return.index]
        if not sector_tickers:
            continue

        # Equal-weight portfolio of long picks in sector
        port_return = total_return[sector_tickers].mean()
        etf_return = total_return.get(etf, 0.0)
        alpha = float(port_return - etf_return)

        records.append({
            "sector": sector,
            "etf": etf,
            "portfolio_return_pct": round(float(port_return) * 100, 2),
            "etf_return_pct": round(float(etf_return) * 100, 2),
            "alpha_pct": round(alpha * 100, 2),
            "winner": alpha > 0,
            "n_positions": len(sector_tickers),
        })

    df = pd.DataFrame(records).sort_values("alpha_pct", ascending=False)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    out_path = os.path.join(OUTPUT_DIR, "sector_alpha.csv")
    df.to_csv(out_path, index=False)
    logger.info("Sector alpha saved to %s", out_path)
    return df


def sector_alpha_summary(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"total_alpha": 0.0, "winners": 0, "losers": 0, "top_sector": None, "worst_sector": None}
    return {
        "total_alpha": round(df["alpha_pct"].sum(), 2),
        "winners": int(df["winner"].sum()),
        "losers": int((~df["winner"]).sum()),
        "top_sector": df.iloc[0]["sector"] if not df.empty else None,
        "worst_sector": df.iloc[-1]["sector"] if not df.empty else None,
    }
