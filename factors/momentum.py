"""Factor 1: Momentum — 6 sub-factors, sector-relative percentile rank."""
import pandas as pd
import numpy as np
from ._base import get_db, apply_sector_ranks

# Sector → benchmark ETF mapping
SECTOR_ETF = {
    "Information Technology": "XLK",
    "Financials": "XLF",
    "Health Care": "XLV",
    "Energy": "XLE",
    "Industrials": "XLI",
    "Communication Services": "XLC",
    "Consumer Discretionary": "XLY",
    "Consumer Staples": "XLP",
    "Materials": "XLB",
    "Real Estate": "XLRE",
    "Utilities": "XLU",
}


def _price_return(prices: pd.DataFrame, ticker: str, start_offset: int, end_offset: int) -> float:
    """Return from start_offset to end_offset trading days ago (negative = lookback)."""
    s = prices[prices["ticker"] == ticker].sort_values("date")
    if len(s) < max(abs(start_offset), abs(end_offset)) + 5:
        return np.nan
    close = s["adj_close"].values
    n = len(close)
    p_end = close[n - end_offset - 1] if end_offset > 0 else close[-1]
    p_start = close[n - start_offset - 1]
    if p_start <= 0 or np.isnan(p_start) or np.isnan(p_end):
        return np.nan
    return (p_end / p_start) - 1


def calculate_all(universe: pd.DataFrame) -> pd.DataFrame:
    """
    Compute momentum scores for all tickers in universe.
    universe: DataFrame with columns [ticker, sector]
    Returns DataFrame with ticker + 6 sub-factor raw values + momentum_score (0-100).
    """
    conn = get_db()
    prices = pd.read_sql_query(
        "SELECT ticker, date, adj_close FROM daily_prices ORDER BY ticker, date",
        conn,
    )
    conn.close()

    prices = prices[prices["adj_close"].notna() & (prices["adj_close"] > 0)]

    # Trading day approximations
    D21 = 21   # ~1 month
    D63 = 63   # ~3 months
    D126 = 126  # ~6 months
    D252 = 252  # ~12 months

    records = []
    for _, row in universe.iterrows():
        tkr = row["ticker"]
        sector = row["sector"]
        s = prices[prices["ticker"] == tkr].sort_values("date")
        close = s["adj_close"].values
        n = len(close)

        def ret(start_back, end_back):
            if n <= start_back:
                return np.nan
            p0 = close[max(0, n - start_back - 1)]
            p1 = close[max(0, n - end_back - 1)] if end_back > 0 else close[-1]
            if p0 <= 0 or np.isnan(p0) or np.isnan(p1):
                return np.nan
            return (p1 / p0) - 1

        # Sub-factor 1: 12-1 month return (skip most recent month)
        ret_12_1 = ret(D252, D21)
        # Sub-factor 2: 6-month return
        ret_6m = ret(D126, 0)
        # Sub-factor 3: 3-month return
        ret_3m = ret(D63, 0)
        # Sub-factor 4: Acceleration (recent 3m minus prior 3m)
        ret_3m_prior = ret(D126, D63)
        accel = (ret_3m - ret_3m_prior) if not (np.isnan(ret_3m) or np.isnan(ret_3m_prior)) else np.nan

        # Sub-factor 5: 52-week-high proximity
        if n >= D252:
            high_52w = close[n - D252:].max()
            prox_52w = close[-1] / high_52w if high_52w > 0 else np.nan
        else:
            prox_52w = np.nan

        # Sub-factor 6: Relative strength vs sector ETF
        etf = SECTOR_ETF.get(sector)
        rel_strength = np.nan
        if etf:
            etf_s = prices[prices["ticker"] == etf].sort_values("date")
            etf_close = etf_s["adj_close"].values
            ne = len(etf_close)
            if ne >= D126 and n >= D126:
                stock_ret6 = ret_6m
                etf_ret6 = (etf_close[-1] / etf_close[max(0, ne - D126 - 1)]) - 1
                if not (np.isnan(stock_ret6) or np.isnan(etf_ret6)):
                    rel_strength = stock_ret6 - etf_ret6

        records.append({
            "ticker": tkr,
            "sector": sector,
            "ret_12_1": ret_12_1,
            "ret_6m": ret_6m,
            "ret_3m": ret_3m,
            "accel": accel,
            "prox_52w": prox_52w,
            "rel_strength": rel_strength,
        })

    df = pd.DataFrame(records)

    for col in ["ret_12_1", "ret_6m", "ret_3m", "accel", "prox_52w", "rel_strength"]:
        df[f"rank_{col}"] = apply_sector_ranks(df, col)

    rank_cols = [c for c in df.columns if c.startswith("rank_")]
    df["momentum_score"] = df[rank_cols].mean(axis=1)

    return df[["ticker", "sector", "momentum_score"] + rank_cols]
